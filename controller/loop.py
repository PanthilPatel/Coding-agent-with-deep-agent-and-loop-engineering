import os
import time
from agents.worker import build_worker_agent, run_worker_turn
from controller.executor import run_tests, run_lint, failure_signature
from controller.state import (
    load_state,
    save_state,
    IterationRecord,
    TerminationReason,
    build_evaluator_result,
)
from utils.git_utils import ensure_work_branch, get_diff, commit_iteration
from skills import select_skill


def build_instruction(
    goal: str,
    last_output_tail: str,
    force_new_strategy: bool,
    skill_content: str = "",
) -> str:
    parts = [f"Goal: {goal}"]
    if skill_content:
        parts.append(f"Approach guide (follow this procedure):\n{skill_content}")
    if last_output_tail:
        parts.append(f"Latest test output:\n{last_output_tail}")
    if force_new_strategy:
        parts.append(
            "The previous fix did not resolve this failure. Do not repeat "
            "the same approach — analyze why it failed and try something "
            "meaningfully different."
        )
    return "\n\n".join(parts)


def print_execution_summary(state, success: bool, iterations_run: int) -> None:
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
        print(f"\n  [Iteration {it_num}] - Verification tests {status_str}")
        for line in summary.split("\n"):
            if line.strip():
                print(f"    {line.strip()}")
    print("="*60 + "\n")


def run(config) -> bool:
    """Run the controller loop. Returns True if the goal was met, False
    if the loop stopped due to a limit without success."""

    state_path = None  # initialise early so the except block can always call save_state

    # ------------------------------------------------------------------
    # SETUP — cloning, branch checkout, agent build
    # Wrapped in try/except so a setup failure records unrecoverable_error
    # rather than crashing with no state.json.
    # ------------------------------------------------------------------
    try:
        if config.is_remote:
            print(f"[controller] Cloning remote repository {config.repo_path} to {config.local_repo_path}...")
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
                    print(f"[controller] Warning: failed to clean up directory: {e}")
            github_token = os.environ.get("GITHUB_TOKEN")
            clone_repo(config.repo_path, config.local_repo_path, github_token)
            print("[controller] Cloning completed.")

        state_path = os.path.join(config.local_repo_path, config.state_file)
        state = load_state(state_path, config.goal)

        ensure_work_branch(config.local_repo_path, "auto-agent-work")
        agent = build_worker_agent(config.local_repo_path, config.model_name, config.llm_provider)

        # --- Skill selection (once per run, before the iteration loop) ---
        skills_dir = config.skills_dir if config.skills_dir else "skills"
        skill = select_skill(config.goal, skills_dir=skills_dir)
        skill_name = skill.name if skill else None
        skill_content = skill.content if skill else ""
        if skill_name:
            print(f"[SKILL] {skill_name}")
        else:
            print("[SKILL] No matching skill found — proceeding with base instructions.")
        state.set_skill(skill_name)
        save_state(state_path, state)

    except Exception as e:
        print(f"\n[controller] SETUP ERROR: {e}")
        if state_path:
            try:
                state = load_state(state_path, config.goal)
                state.set_termination_reason(TerminationReason.UNRECOVERABLE_ERROR)
                save_state(state_path, state)
            except Exception:
                pass  # best-effort; don't mask the original error
        return False

    start_time = time.time()
    last_output_tail = ""
    force_new_strategy = False
    iterations_run = 0

    for i in range(1, config.max_iterations + 1):
        iterations_run = i
        if time.time() - start_time > config.max_seconds:
            print(f"[controller] Stopping: max_seconds ({config.max_seconds}) exceeded.")
            state.set_termination_reason(TerminationReason.TIMEOUT)
            save_state(state_path, state)
            print_execution_summary(state, False, iterations_run)
            return False

        print(f"\n[controller] Iteration {i}/{config.max_iterations}")

        # ------------------------------------------------------------------
        # PER-ITERATION — worker turn, test run, optional lint, commit
        # Wrapped in try/except so an unexpected exception records tool_error.
        # ------------------------------------------------------------------
        try:
            instruction = build_instruction(
                config.goal, last_output_tail, force_new_strategy, skill_content
            )

            # Record the plan (instruction sent to the worker) in state
            state.set_plan(instruction)
            save_state(state_path, state)

            worker_summary = run_worker_turn(agent, instruction)
            print(f"[worker] {worker_summary[:300]}")

            result = run_tests(config.local_repo_path, config.test_cmd)
            print(f"[tests] passed={result.passed} returncode={result.returncode}")

            # Optional lint step — only runs when lint_cmd is configured
            lint_result = None
            if config.lint_cmd:
                lint_result = run_lint(config.local_repo_path, config.lint_cmd)
                print(f"[lint]  passed={lint_result.passed} returncode={lint_result.returncode}")

            # Build structured evaluator result from objective signals
            evaluator = build_evaluator_result(
                test_passed=result.passed,
                test_output_tail=result.output_tail,
                lint_passed=lint_result.passed if lint_result else None,
                lint_output_tail=lint_result.output_tail if lint_result else None,
            )
            state.set_evaluator_result(evaluator)

            if config.require_approval:
                diff = get_diff(config.local_repo_path)
                print(f"\n[diff]\n{diff[:2000]}\n")
                approve = input("Commit this change? [y/N] ").strip().lower()
                if approve != "y":
                    print("[controller] Change rejected by user; stopping.")
                    state.set_termination_reason(TerminationReason.USER_REJECTED)
                    save_state(state_path, state)
                    print_execution_summary(state, False, iterations_run)
                    return False

            commit_hash = commit_iteration(
                config.local_repo_path, f"agent iteration {i}: {worker_summary[:72]}"
            )
            if commit_hash:
                print(f"[git] committed {commit_hash[:8]}")

            record = IterationRecord(
                iteration=i,
                timestamp=time.time(),
                instruction_summary=instruction[:200],
                worker_summary=worker_summary[:500],
                test_passed=result.passed,
                test_output_tail=result.output_tail[:1000],
                lint_passed=lint_result.passed if lint_result else None,
                lint_output_tail=lint_result.output_tail[:500] if lint_result else None,
            )
            state.add_iteration(record)

            if result.passed:
                state.note_success()
                state.set_termination_reason(TerminationReason.SUCCESS)
                save_state(state_path, state)
                print(f"\n[controller] Goal met after {i} iteration(s).")
                print_execution_summary(state, True, iterations_run)
                if config.is_remote:
                    print("[controller] Pushing changes to remote...")
                    from utils.git_remote import push_to_remote
                    try:
                        push_to_remote(config.local_repo_path, "auto-agent-work")
                        print("[controller] Successfully pushed 'auto-agent-work' branch to remote.")
                    except Exception as e:
                        print(f"[controller] Failed to push changes to remote: {e}")
                return True

            signature = failure_signature(result)
            force_new_strategy = state.note_failure(signature)
            last_output_tail = result.output_tail
            save_state(state_path, state)

        except Exception as e:
            print(f"\n[controller] ITERATION ERROR (iteration {i}): {e}")
            state.set_termination_reason(TerminationReason.TOOL_ERROR)
            save_state(state_path, state)
            print_execution_summary(state, False, iterations_run)
            return False

    print(f"\n[controller] Stopping: max_iterations ({config.max_iterations}) reached without success.")
    state.set_termination_reason(TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT)
    save_state(state_path, state)
    print_execution_summary(state, False, iterations_run)
    return False
