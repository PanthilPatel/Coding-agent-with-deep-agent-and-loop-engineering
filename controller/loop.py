"""Controller loop — the main orchestration module.

The ``run()`` function drives the entire agent lifecycle:
  1. Setup: clone (if remote), checkout branch, build worker agent, select skill.
  2. Iterate: instruct worker → run tests / verification → optional lint → commit → evaluate.
  3. Terminate: on success, time-out, iteration limit, user rejection, or error.

Logging convention (all output goes to stdout):
  [AGENT]      — agent thinking, turn summaries, LLM events
  [PLAN]       — prompt / plan instructions sent to worker
  [STEP]       — iteration step milestones
  [TOOL]       — tool execution events
  [RESULT]     — tool and verification return results
  [PERMISSION] — permission gate prompts and decisions
  [VERIFY]     — evaluation and verification check results
  [RECOVERY]   — error recovery and strategy replanning triggers
  [DONE]       — successful completion and summary
  [ERROR]      — errors and exceptions
"""

import asyncio
import os
import time
from agents.worker import build_worker_agent, run_worker_turn
from controller.executor import run_tests, run_lint, failure_signature
from controller.state import (
    load_state,
    save_state,
    IterationRecord,
    TerminationReason,
)
from controller.evaluator import evaluate_iteration, GeneralEvaluator, VerificationResult
from controller.router import decide_next_step, RouterState
from controller.permissions import PermissionHarness
from utils.git_utils import ensure_work_branch, get_diff, commit_iteration
from skills import select_skill
from tools import build_tool_registry


def build_instruction(
    goal: str,
    last_output_tail: str,
    force_new_strategy: bool,
    skill_content: str = "",
    error_feedback: str = "",
) -> str:
    """Compose the instruction string sent to the worker agent each iteration.

    Args:
        goal:             The user-specified goal for this run.
        last_output_tail: The tail of the most recent test output (empty on
                          the first iteration).
        force_new_strategy: When True, appends a directive telling the agent
                            to abandon its previous approach.
        skill_content:    Optional procedure text from the selected SKILL.md.
        error_feedback:   Optional error recovery feedback from previous failures.

    Returns:
        A newline-separated instruction string ready to pass to
        ``run_worker_turn()``.
    """
    parts = [f"Goal: {goal}"]
    if skill_content:
        parts.append(f"Approach guide (follow this procedure):\n{skill_content}")
    if error_feedback:
        parts.append(f"Error recovery note:\n{error_feedback}")
    if last_output_tail:
        parts.append(f"Latest verification output:\n{last_output_tail}")
    if force_new_strategy:
        parts.append(
            "The previous fix did not resolve this failure. Do not repeat "
            "the same approach — analyze why it failed and try something "
            "meaningfully different."
        )
    return "\n\n".join(parts)


def print_execution_summary(state, success: bool, iterations_run: int) -> None:
    """Print a human-readable summary of the run to stdout.

    Called once at the end of every code path that terminates the loop
    (success, timeout, max-iterations, user rejection, tool error).

    Args:
        state:          The final ``RunState`` (used for termination_reason
                        and the per-iteration records).
        success:        True if the goal was met, False otherwise.
        iterations_run: How many iterations completed before termination.
    """
    print("\n" + "="*60)
    print("                     EXECUTION SUMMARY")
    print("="*60)
    print(f"Overall Status:      {'SUCCESS (Code is now error-free)' if success else 'FAILED (Tests are still failing)'}")
    print(f"Iterations Run:      {iterations_run}")
    if state.termination_reason:
        print(f"Termination Reason:  {state.termination_reason}")
    print("\nChanges Performed by Iteration:")
    for record in state.iterations:
        # handle both dict and object formats
        it_num = record.get("iteration") if isinstance(record, dict) else getattr(record, "iteration", "?")
        summary = record.get("worker_summary") if isinstance(record, dict) else getattr(record, "worker_summary", "No summary")
        passed = record.get("test_passed") if isinstance(record, dict) else getattr(record, "test_passed", False)
        status_str = "PASSED" if passed else "FAILED"
        print(f"\n  [STEP] Iteration {it_num} - Verification tests {status_str}")
        for line in summary.split("\n"):
            if line.strip():
                print(f"    {line.strip()}")
    print("="*60 + "\n")


def run(config) -> bool:
    """Run the controller loop. Returns True if the goal was met, False
    if the loop stopped due to a limit without success."""

    state_path = None  # initialise early so the except block can always call save_state
    registry = None    # MCP registry — always closed in the finally block below

    # ------------------------------------------------------------------
    # SETUP — cloning, branch checkout, agent build, MCP initialization
    # Wrapped in try/except so a setup failure records unrecoverable_error
    # rather than crashing with no state.json.
    # ------------------------------------------------------------------
    try:
        if config.is_remote:
            print(f"[AGENT] Cloning remote repository {config.repo_path} to {config.local_repo_path}...")
            from utils.git_remote import clone_repo
            import shutil
            import stat
            if os.path.exists(config.local_repo_path):
                try:
                    # Remove read-only attributes on Windows before deleting
                    for root, dirs, files in os.walk(config.local_repo_path):
                        for momo in dirs:
                            os.chmod(os.path.join(root, momo), stat.S_IWRITE)
                        for momo in files:
                            os.chmod(os.path.join(root, momo), stat.S_IWRITE)
                    shutil.rmtree(config.local_repo_path)
                except Exception as e:
                    print(f"[ERROR] Warning: failed to clean up directory: {e}")
            github_token = os.environ.get("GITHUB_TOKEN")
            clone_repo(config.repo_path, config.local_repo_path, github_token)
            print("[AGENT] Cloning completed.")

        state_path = os.path.join(config.local_repo_path, config.state_file)
        state = load_state(state_path, config.goal)

        # Ensure a clean slate for new runs: reset iterations and state trackers
        # if this is a new run/goal rather than resuming an identical state
        if state.goal != config.goal or state.termination_reason is not None:
            from controller.state import RunState
            state = RunState(goal=config.goal)

        ensure_work_branch(config.local_repo_path, "auto-agent-work")

        # --- MCP initialization (Phase 4) ---
        # Bridge the async registry into the synchronous run() function.
        # Runs only when config.mcp_config_path is set; degrades gracefully on
        # any failure so a bad MCP config never blocks the agent run.
        mcp_tools = []
        if getattr(config, "mcp_config_path", None):
            try:
                from mcp_agent import MCPRegistry
                registry = MCPRegistry(config.mcp_config_path)
                asyncio.run(registry.initialize())
                if registry.tools:
                    # Wrap every MCP tool with PermissionHarness.execute_guarded
                    # at "confirm" tier (no per-tool tier config exists yet).
                    harness = PermissionHarness(interactive=False)
                    guarded_tools = []
                    for _tool in registry.tools:
                        # Build a guarded callable that captures the tool and
                        # runs it through the permission harness at confirm tier.
                        def _make_guarded(tool, h):
                            def _guarded_call(input_str=""):
                                return h.execute_guarded(
                                    tool.run,
                                    "confirm",
                                    input_str,
                                    tool_name=tool.name,
                                )
                            _guarded_call.__name__ = tool.name
                            _guarded_call.__doc__ = getattr(tool, "description", "")
                            return _guarded_call
                        guarded_tools.append(_make_guarded(_tool, harness))
                    mcp_tools = guarded_tools
                    print(f"[TOOL] [MCP] {len(mcp_tools)} tool(s) registered with confirm-tier permission gate.")
                else:
                    print("[TOOL] [MCP] No tools discovered — continuing with native toolset.")
            except Exception as e:
                print(f"[ERROR] [MCP] Initialization failed: {e} — continuing without MCP tools.")
                if registry is not None:
                    try:
                        asyncio.run(registry.close())
                    except Exception:
                        pass
                registry = None

        # Build worker with optional MCP tools appended to the native toolset
        native_tools = build_tool_registry(
            repo_path=config.local_repo_path,
            test_cmd=config.test_cmd,
            require_approval=config.require_approval,
        )
        extra_tools = native_tools + mcp_tools if mcp_tools else native_tools
        agent = build_worker_agent(
            config.local_repo_path,
            config.model_name,
            config.llm_provider,
            extra_tools=extra_tools,
        )

        # --- Skill selection (once per run, before the iteration loop) ---
        skills_dir = config.skills_dir if config.skills_dir else "skills"
        skill = select_skill(config.goal, skills_dir=skills_dir)
        skill_name = skill.name if skill else None
        skill_content = skill.content if skill else ""
        if skill_name:
            print(f"[AGENT] [SKILL] {skill_name}")
        else:
            print("[AGENT] [SKILL] No matching skill found — proceeding with base instructions.")
        state.set_skill(skill_name)
        save_state(state_path, state)

    except Exception as e:
        print(f"\n[ERROR] SETUP ERROR: {e}")
        if state_path:
            try:
                state = load_state(state_path, config.goal)
                state.set_termination_reason(TerminationReason.UNRECOVERABLE_ERROR)
                save_state(state_path, state)
            except Exception:
                pass  # best-effort; don't mask the original error
        # Close MCP registry even on setup failure
        if registry is not None:
            try:
                asyncio.run(registry.close())
            except Exception:
                pass
        return False

    start_time = time.time()
    last_output_tail = ""
    force_new_strategy = False
    error_feedback = ""
    iterations_run = 0

    for i in range(1, config.max_iterations + 1):
        iterations_run = i
        if time.time() - start_time > config.max_seconds:
            print(f"[ERROR] Stopping: max_seconds ({config.max_seconds}) exceeded.")
            state.set_termination_reason(TerminationReason.TIMEOUT)
            save_state(state_path, state)
            print_execution_summary(state, False, iterations_run)
            if registry is not None:
                try:
                    asyncio.run(registry.close())
                except Exception:
                    pass
            return False

        print(f"\n[STEP] Iteration {i}/{config.max_iterations}")

        # ------------------------------------------------------------------
        # PER-ITERATION — worker turn, test/verification run, optional lint, commit
        # ------------------------------------------------------------------
        try:
            instruction = build_instruction(
                config.goal,
                last_output_tail,
                force_new_strategy,
                skill_content,
                error_feedback=error_feedback,
            )

            # Record the plan (instruction sent to the worker) in state
            state.set_plan(instruction)
            save_state(state_path, state)
            print(f"[PLAN] Instruction composed for iteration {i}")

            worker_summary = run_worker_turn(agent, instruction)
            print(f"[AGENT] {worker_summary[:300]}")

            # Check if explicit verification strategy is set on config
            verification_strategy = getattr(config, "verification_strategy", None)
            lint_result = None

            if isinstance(verification_strategy, str) and verification_strategy.strip():
                # Generalized verification engine
                eval_kwargs = getattr(config, "verification_kwargs", {})
                gen_evaluator = GeneralEvaluator()
                v_result = gen_evaluator.evaluate(verification_strategy, **eval_kwargs)
                passed = v_result.passed
                output_tail = v_result.evidence
                print(f"[VERIFY] Strategy '{verification_strategy}' result: passed={passed} ({v_result.evidence})")

                evaluator_dict = {
                    "is_correct": v_result.passed,
                    "score": 1.0 if v_result.passed else 0.0,
                    "issues": v_result.issues,
                    "critical_gaps": v_result.issues,
                    "feedback": v_result.evidence,
                }
                state.set_evaluator_result(evaluator_dict)
            else:
                # Standard / legacy test execution
                result = run_tests(config.local_repo_path, config.test_cmd)
                passed = result.passed
                output_tail = result.output_tail
                print(f"[VERIFY] Tests passed={result.passed} returncode={result.returncode}")

                # Optional lint step — only runs when lint_cmd is configured
                if config.lint_cmd:
                    lint_result = run_lint(config.local_repo_path, config.lint_cmd)
                    print(f"[VERIFY] Lint passed={lint_result.passed} returncode={lint_result.returncode}")

                evaluator_dict = evaluate_iteration(
                    test_passed=result.passed,
                    test_output_tail=result.output_tail,
                    lint_passed=lint_result.passed if lint_result else None,
                    lint_output_tail=lint_result.output_tail if lint_result else None,
                    same_failure_count=state.same_failure_count,
                )
                state.set_evaluator_result(evaluator_dict)

            if config.require_approval:
                diff = get_diff(config.local_repo_path)
                print(f"\n[PERMISSION] Code diff inspection:\n{diff[:2000]}\n")
                approve = input("Commit this change? [y/N] ").strip().lower()
                if approve != "y":
                    print("[PERMISSION] Change rejected by user; stopping.")
                    state.set_termination_reason(TerminationReason.USER_REJECTED)
                    save_state(state_path, state)
                    print_execution_summary(state, False, iterations_run)
                    if registry is not None:
                        try:
                            asyncio.run(registry.close())
                        except Exception:
                            pass
                    return False

            commit_hash = commit_iteration(
                config.local_repo_path, f"agent iteration {i}: {worker_summary[:72]}"
            )
            if commit_hash:
                print(f"[RESULT] [git] committed {commit_hash[:8]}")

            record = IterationRecord(
                iteration=i,
                timestamp=time.time(),
                instruction_summary=instruction[:200],
                worker_summary=worker_summary[:500],
                test_passed=passed,
                test_output_tail=output_tail[:1000],
                lint_passed=lint_result.passed if lint_result else None,
                lint_output_tail=lint_result.output_tail[:500] if lint_result else None,
            )
            state.add_iteration(record)

            # Determine continuation decision using controller.router
            elapsed_seconds = time.time() - start_time
            router_decision = decide_next_step(
                evaluator_result=evaluator_dict,
                current_iteration=i,
                max_iterations=config.max_iterations,
                elapsed_seconds=elapsed_seconds,
                max_seconds=config.max_seconds,
                same_failure_count=state.same_failure_count,
            )

            # Check if router triggered replanning or error recovery
            if router_decision.state == RouterState.REPLAN:
                print(f"[RECOVERY] Router triggered REPLAN: {router_decision.reason}")
                error_feedback = f"Repeated failure occurred. Suggested action: {router_decision.suggested_action}"
            elif router_decision.state == RouterState.RECOVER:
                print(f"[RECOVERY] Router triggered RECOVER: {router_decision.reason}")
                error_feedback = f"Previous iteration failed. Suggested action: {router_decision.suggested_action}"
            else:
                error_feedback = ""

            if not router_decision.should_continue or router_decision.state == RouterState.COMPLETE:
                reason = router_decision.termination_reason or TerminationReason.SUCCESS
                if evaluator_dict["is_correct"] or router_decision.state == RouterState.COMPLETE:
                    state.note_success()
                    state.set_termination_reason(TerminationReason.SUCCESS)
                    save_state(state_path, state)
                    print(f"\n[DONE] Goal met after {i} iteration(s).")
                    print_execution_summary(state, True, iterations_run)
                    if config.is_remote:
                        print("[AGENT] Pushing changes to remote...")
                        from utils.git_remote import push_to_remote
                        try:
                            push_to_remote(config.local_repo_path, "auto-agent-work")
                            print("[AGENT] Successfully pushed 'auto-agent-work' branch to remote.")
                        except Exception as e:
                            print(f"[ERROR] Failed to push changes to remote: {e}")
                    if registry is not None:
                        try:
                            asyncio.run(registry.close())
                        except Exception:
                            pass
                    return True
                else:
                    state.set_termination_reason(reason)
                    save_state(state_path, state)
                    print(f"[ERROR] Router stopping: {reason}")
                    print_execution_summary(state, False, iterations_run)
                    if registry is not None:
                        try:
                            asyncio.run(registry.close())
                        except Exception:
                            pass
                    return False

            if not getattr(config, "verification_strategy", None):
                signature = failure_signature(result)
                force_new_strategy = state.note_failure(signature)
            else:
                force_new_strategy = False

            last_output_tail = output_tail
            save_state(state_path, state)

        except Exception as e:
            print(f"\n[ERROR] ITERATION ERROR (iteration {i}): {e}")
            state.set_termination_reason(TerminationReason.TOOL_ERROR)
            save_state(state_path, state)
            print_execution_summary(state, False, iterations_run)
            if registry is not None:
                try:
                    asyncio.run(registry.close())
                except Exception:
                    pass
            return False

    print(f"\n[ERROR] Stopping: max_iterations ({config.max_iterations}) reached without success.")
    state.set_termination_reason(TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT)
    save_state(state_path, state)
    print_execution_summary(state, False, iterations_run)
    # Always close MCP registry connections before returning.
    if registry is not None:
        try:
            asyncio.run(registry.close())
        except Exception:
            pass
    return False

