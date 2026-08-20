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
from controller.checkpoint import CheckpointManager
from utils.git_utils import ensure_work_branch, get_diff, commit_iteration
from skills import select_skill
from tools import build_tool_registry


def build_instruction(
    goal: str,
    last_output_tail: str,
    force_new_strategy: bool,
    skill_content: str = "",
    error_feedback: str = "",
    prior_attempts: list = None,
    last_returncode: int = None,
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
    
    if prior_attempts:
        parts.append("Prior attempts this run:\n" + "\n".join(f"- {attempt}" for attempt in prior_attempts))
        
    if skill_content:
        parts.append(f"Approach guide (follow this procedure):\n{skill_content}")
    if error_feedback:
        parts.append(f"Error recovery note:\n{error_feedback}")
    if last_output_tail:
        if last_returncode is not None and last_returncode != 0:
            parts.append(
                f"[PREVIOUS RUN FAILED - RETURN CODE {last_returncode}]\n"
                f"Pytest failure traceback:\n{last_output_tail}\n\n"
                "Instructions:\n"
                "1. Carefully inspect the failing assertions and traceback above.\n"
                "2. Edit ONLY the source code files to resolve the failure.\n"
                "3. NEVER edit test files."
            )
        else:
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
    # Token usage totals (only shown when real data was captured)
    token_usage = getattr(state, "token_usage", None)
    if token_usage:
        print(
            f"Token Usage (total): "
            f"Prompt: {token_usage.get('prompt_tokens', 0)} | "
            f"Completion: {token_usage.get('completion_tokens', 0)} | "
            f"Total: {token_usage.get('total_tokens', 0)}"
        )
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

        harness = PermissionHarness(interactive=False)

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
            harness=harness,
        )
        extra_tools = native_tools + mcp_tools if mcp_tools else native_tools
        agent = build_worker_agent(
            config.local_repo_path,
            config.model_name,
            config.llm_provider,
            extra_tools=extra_tools,
        )

        # --- Verification Strategy Inference ---
        # Automatically infer verification strategy if not already explicitly set
        if not getattr(config, "verification_strategy", None):
            from orchestrator.planner import GoalPlanner
            planner = GoalPlanner()
            inferred_strategy, inferred_kwargs = planner._infer_verification(config.goal)
            if inferred_strategy:
                # Adjust relative file/directory paths to be relative to local_repo_path if not absolute
                eval_kwargs = dict(inferred_kwargs)
                if "path" in eval_kwargs and not os.path.isabs(eval_kwargs["path"]):
                    eval_kwargs["path"] = os.path.join(config.local_repo_path, eval_kwargs["path"])
                if inferred_strategy == "test_suite":
                    eval_kwargs.setdefault("test_cmd", config.test_cmd)
                    eval_kwargs.setdefault("repo_path", config.local_repo_path)
                setattr(config, "verification_strategy", inferred_strategy)
                setattr(config, "verification_kwargs", eval_kwargs)
                print(f"[VERIFY] Inferred verification strategy: '{inferred_strategy}' with kwargs {eval_kwargs}")

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
        state.set_audit_log(list(harness.audit_log))
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

    checkpoint_mgr = CheckpointManager(config.local_repo_path)
    initial_checkpoint = checkpoint_mgr.create_checkpoint(f"initial_state_{config.goal[:30]}")

    def _save_state():
        if harness is not None:
            state.set_audit_log(list(harness.audit_log))
            counts = {}
            for entry in harness.audit_log:
                name = entry.get("tool_name", "unknown")
                counts[name] = counts.get(name, 0) + 1
            state.set_tool_calls_count(counts)
        save_state(state_path, state)

    start_time = time.time()
    last_output_tail = ""
    last_returncode = None
    previous_worker_summary = None
    previous_diff: str | None = None  # tracks the committed diff from the prior iteration
    force_new_strategy = False
    error_feedback = ""
    iterations_run = 0
    consecutive_failures = 0

    for i in range(1, config.max_iterations + 1):
        iterations_run = i
        if time.time() - start_time > config.max_seconds:
            print(f"[ERROR] Stopping: max_seconds ({config.max_seconds}) exceeded.")
            state.set_termination_reason(TerminationReason.TIMEOUT)
            _save_state()
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
            prior_attempts = []
            if state.iterations:
                for idx, record_dict in enumerate(state.iterations):
                    it_num = record_dict.get("iteration", "?")
                    w_summ = record_dict.get("worker_summary", "").strip()
                    passed = record_dict.get("test_passed", False)
                    tail = record_dict.get("test_output_tail", "")
                    
                    if passed:
                        status_str = "tests passed"
                    else:
                        sig = tail.strip().split('\n')[-1][:100] if tail.strip() else "tests failed"
                        status_str = f"tests failed ({sig})"
                        
                    is_recent = (len(state.iterations) - idx) <= 2
                    if is_recent:
                        prior_attempts.append(f"Iteration {it_num}: {w_summ} -> {status_str}")
                    else:
                        one_line_summ = w_summ.split('\n')[0][:80]
                        if len(one_line_summ) < len(w_summ):
                            one_line_summ += "..."
                        prior_attempts.append(f"Iteration {it_num}: {one_line_summ} -> {status_str}")

            # ------------------------------------------------------------------
            # PER-ITERATION CHECKPOINT — taken at the very start so that REPLAN
            # can roll back only the current iteration's changes, not all prior
            # progress from earlier (successful) iterations.
            # ------------------------------------------------------------------
            iteration_checkpoint = checkpoint_mgr.create_checkpoint(f"pre_iter_{i}")

            instruction = build_instruction(
                config.goal,
                last_output_tail,
                force_new_strategy,
                skill_content,
                error_feedback=error_feedback,
                prior_attempts=prior_attempts,
                last_returncode=last_returncode,
            )

            # Record the plan (instruction sent to the worker) in state
            state.set_plan(instruction)
            _save_state()
            print(f"[PLAN] Instruction composed for iteration {i}")

            worker_summary = run_worker_turn(agent, instruction)

            # Read token usage from the side-channel written by run_worker_turn
            from agents.worker import _last_turn_token_usage
            state.accumulate_token_usage(_last_turn_token_usage)

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
                last_returncode = 0 if v_result.passed else 1
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
                target_test_path = getattr(config, "target_test_path", None)
                result = run_tests(
                    config.local_repo_path,
                    config.test_cmd,
                    target_test_path=target_test_path,
                )
                passed = result.passed
                output_tail = result.output_tail
                last_returncode = result.returncode
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
                    _save_state()
                    print_execution_summary(state, False, iterations_run)
                    if registry is not None:
                        try:
                            asyncio.run(registry.close())
                        except Exception:
                            pass
                    return False

            # Capture diff BEFORE commit so it reflects what the agent changed this iteration.
            # get_diff() returns the staged+unstaged diff against HEAD; after commit it returns
            # empty, so we must call it first.
            current_diff = get_diff(config.local_repo_path)

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
                worker_summary=worker_summary,
                previous_worker_summary=previous_worker_summary,
                current_diff=current_diff,
                previous_diff=previous_diff,
            )
            previous_worker_summary = worker_summary
            previous_diff = current_diff

            if evaluator_dict.get("is_correct"):
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            # Check if router triggered replanning or error recovery
            if router_decision.state == RouterState.REPLAN:
                print(f"[RECOVERY] Router triggered REPLAN: {router_decision.reason}")
                error_feedback = f"Repeated failure occurred. Suggested action: {router_decision.suggested_action}"
                force_new_strategy = True
                consecutive_failures = max(consecutive_failures, 2)

                # Severity-split rollback:
                # - returncode==2 means collection/syntax error → repo may be broken,
                #   fall back to full reset to initial_checkpoint.
                # - returncode==1 (ordinary assertion failure) → only discard the
                #   current iteration's changes so prior partial progress is preserved.
                if last_returncode == 2:
                    target_cp = initial_checkpoint
                    print("[RECOVERY] Syntax/collection error (rc=2): rolling back to initial checkpoint")
                else:
                    target_cp = iteration_checkpoint
                    print(f"[RECOVERY] Rolling back to start of iteration {i} checkpoint (preserving prior progress)")
                if target_cp:
                    checkpoint_mgr.rollback_to_checkpoint(target_cp)
            elif router_decision.state == RouterState.RECOVER:
                print(f"[RECOVERY] Router triggered RECOVER: {router_decision.reason}")
                error_feedback = f"Previous iteration failed. Suggested action: {router_decision.suggested_action}"
                force_new_strategy = False
            else:
                error_feedback = ""
                force_new_strategy = False
                
            if consecutive_failures >= 2 and not getattr(agent, "escalated_to_nvidia", False):
                nvidia_api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
                if nvidia_api_key:
                    nvidia_model = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
                    print(f"[AGENT] Escalating to NVIDIA NIM ({nvidia_model}) after 2 consecutive failed iterations.")
                    agent = build_worker_agent(
                        config.local_repo_path,
                        nvidia_model,
                        "nvidia",
                        extra_tools=extra_tools,
                    )
                    setattr(agent, "escalated_to_nvidia", True)
                else:
                    print("[AGENT] NVIDIA_API_KEY not found in environment — skipping escalation, continuing with local model.")
                    setattr(agent, "escalated_to_nvidia", True)

            if not router_decision.should_continue or router_decision.state == RouterState.COMPLETE:
                reason = router_decision.termination_reason or TerminationReason.SUCCESS
                if evaluator_dict["is_correct"] or router_decision.state == RouterState.COMPLETE:
                    state.note_success()
                    state.set_termination_reason(TerminationReason.SUCCESS)
                    _save_state()
                    # Discard checkpoint reference since subtask completed cleanly
                    if initial_checkpoint:
                        checkpoint_mgr.discard_checkpoint(initial_checkpoint)
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
                    _save_state()
                    # Discard/Rollback since loop fails permanently
                    if initial_checkpoint:
                        checkpoint_mgr.rollback_to_checkpoint(initial_checkpoint)
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
                state_force = state.note_failure(signature)
                force_new_strategy = force_new_strategy or state_force

            last_output_tail = output_tail
            _save_state()

        except Exception as e:
            print(f"\n[ERROR] ITERATION ERROR (iteration {i}): {e}")
            state.set_termination_reason(TerminationReason.TOOL_ERROR)
            _save_state()
            print_execution_summary(state, False, iterations_run)
            if registry is not None:
                try:
                    asyncio.run(registry.close())
                except Exception:
                    pass
            return False

    print(f"\n[ERROR] Stopping: max_iterations ({config.max_iterations}) reached without success.")
    state.set_termination_reason(TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT)
    _save_state()
    print_execution_summary(state, False, iterations_run)
    # Always close MCP registry connections before returning.
    if registry is not None:
        try:
            asyncio.run(registry.close())
        except Exception:
            pass
    return False


