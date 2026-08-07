import os
import time
from agents.worker import build_worker_agent, run_worker_turn
from controller.executor import run_tests, failure_signature
from controller.state import load_state, save_state, IterationRecord
from utils.git_utils import ensure_work_branch, get_diff, commit_iteration


def build_instruction(goal: str, last_output_tail: str, force_new_strategy: bool) -> str:
    parts = [f"Goal: {goal}"]
    if last_output_tail:
        parts.append(f"Latest test output:\n{last_output_tail}")
    if force_new_strategy:
        parts.append(
            "The previous fix did not resolve this failure. Do not repeat "
            "the same approach — analyze why it failed and try something "
            "meaningfully different."
        )
    return "\n\n".join(parts)


def run(config) -> bool:
    """Run the controller loop. Returns True if the goal was met, False
    if the loop stopped due to a limit without success."""
    if config.is_remote:
        print(f"[controller] Cloning remote repository {config.repo_path} to {config.local_repo_path}...")
        from utils.git_remote import clone_repo
        import shutil
        if os.path.exists(config.local_repo_path):
            try:
                shutil.rmtree(config.local_repo_path)
            except Exception:
                pass
        github_token = os.environ.get("GITHUB_TOKEN")
        clone_repo(config.repo_path, config.local_repo_path, github_token)
        print("[controller] Cloning completed.")

    state_path = os.path.join(config.local_repo_path, config.state_file)
    state = load_state(state_path, config.goal)

    ensure_work_branch(config.local_repo_path, "auto-agent-work")
    agent = build_worker_agent(config.local_repo_path, config.model_name, config.llm_provider)

    start_time = time.time()
    last_output_tail = ""
    force_new_strategy = False

    for i in range(1, config.max_iterations + 1):
        if time.time() - start_time > config.max_seconds:
            print(f"[controller] Stopping: max_seconds ({config.max_seconds}) exceeded.")
            return False

        print(f"\n[controller] Iteration {i}/{config.max_iterations}")

        instruction = build_instruction(config.goal, last_output_tail, force_new_strategy)
        worker_summary = run_worker_turn(agent, instruction)
        print(f"[worker] {worker_summary[:300]}")

        result = run_tests(config.local_repo_path, config.test_cmd)
        print(f"[tests] passed={result.passed} returncode={result.returncode}")

        if config.require_approval:
            diff = get_diff(config.local_repo_path)
            print(f"\n[diff]\n{diff[:2000]}\n")
            approve = input("Commit this change? [y/N] ").strip().lower()
            if approve != "y":
                print("[controller] Change rejected by user; stopping.")
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
        )
        state.add_iteration(record)

        if result.passed:
            state.note_success()
            save_state(state_path, state)
            print(f"\n[controller] Goal met after {i} iteration(s).")
            if config.is_remote:
                print(f"[controller] Pushing changes to remote...")
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

    print(f"\n[controller] Stopping: max_iterations ({config.max_iterations}) reached without success.")
    return False
