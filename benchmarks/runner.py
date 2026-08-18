"""Automated E2E Benchmark Runner.

Orchestrates automated evaluation of coding agents across benchmark repositories
in isolated sandbox environments, tracking objective metrics (pass/fail, iterations,
duration, guarded tool calls).
"""

import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import Config
import controller.loop


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run on a target repository.

    Note on tool_calls_count limitation:
        `tool_calls_count` only covers *guarded* tool calls (those routed through
        `PermissionHarness.execute_guarded` — such as `execute_command`, `create_directory`,
        `move_file`, `delete_file`, `list_directory`, and MCP tools), not backend-native
        tools like `read_file`, `write_file`, `ls`, or `grep`.
    """
    repo_name: str
    goal: str
    passed: bool
    iterations: int
    duration_seconds: float
    tool_calls_count: Dict[str, int] = field(default_factory=dict)
    error_message: Optional[str] = None
    subtasks_completed: int = 0
    total_subtasks: int = 0


class BenchmarkRunner:
    """Discovers, isolates, and executes benchmarks against the agent loop."""

    DEFAULT_GOAL = "Fix all bugs in source files so pytest passes. Do not modify test files."

    def discover_repositories(
        self,
        benchmark_dir: str,
        filter_names: Optional[List[str]] = None,
    ) -> List[str]:
        """Discover candidate benchmark repository directories.

        Args:
            benchmark_dir: Base directory containing benchmark repo folders.
            filter_names: Optional list of strings/substrings to filter repos by name.

        Returns:
            Sorted list of absolute paths to discovered directories.
        """
        abs_bench_dir = os.path.abspath(benchmark_dir)
        if not os.path.isdir(abs_bench_dir):
            return []

        discovered = []
        ignored_names = {"__pycache__", ".pytest_cache", ".git", "tmp", "scratch"}

        for entry in os.listdir(abs_bench_dir):
            if entry in ignored_names or entry.startswith("."):
                continue
            entry_path = os.path.join(abs_bench_dir, entry)
            if os.path.isdir(entry_path):
                if filter_names:
                    match = any(
                        filt.lower() in entry.lower() or entry.lower() in filt.lower()
                        for filt in filter_names
                    )
                    if not match:
                        continue
                discovered.append(entry_path)

        discovered.sort()
        return discovered

    def run_benchmark(
        self,
        repo_path: str,
        goal: Optional[str] = None,
        timeout: int = 300,
        test_cmd: str = "pytest",
        model_name: str = "qwen2.5-coder:7b",
        llm_provider: Optional[str] = None,
        max_iterations: int = 5,
        lint_cmd: Optional[str] = None,
        skills_dir: Optional[str] = None,
        mcp_config_path: Optional[str] = None,
        require_approval: bool = False,
    ) -> BenchmarkResult:
        """Run a single benchmark in an isolated sandbox with wall-clock timeout enforcement.

        Args:
            repo_path: Path to the source template repository.
            goal: Goal description (defaults to DEFAULT_GOAL).
            timeout: Hard wall-clock timeout in seconds for the entire benchmark run.
            test_cmd: Test runner command (e.g. 'pytest').
            model_name: Model identifier string.
            llm_provider: LLM provider string or None.
            max_iterations: Maximum iterations allowed for the controller loop.
            lint_cmd: Optional lint command string.
            skills_dir: Path to skills directory.
            mcp_config_path: Path to MCP configuration JSON.
            require_approval: Confirmation gate flag (forced False for non-interactive benchmark).

        Returns:
            BenchmarkResult containing execution outcome and metrics.
        """
        abs_repo_path = os.path.abspath(repo_path)
        repo_name = os.path.basename(abs_repo_path)
        effective_goal = goal if goal else self.DEFAULT_GOAL

        # Create temporary isolated working directory via tempfile.mkdtemp
        temp_dir = tempfile.mkdtemp(prefix=f"bench_{repo_name}_")
        try:
            temp_repo_copy = os.path.join(temp_dir, repo_name)
            # Real deep copy (not symlinks) so benchmark mutations cannot harm template repos
            shutil.copytree(abs_repo_path, temp_repo_copy, symlinks=False)

            # Programmatically construct Config
            config = Config(
                repo_path=temp_repo_copy,
                goal=effective_goal,
                test_cmd=test_cmd,
                max_iterations=max_iterations,
                max_seconds=timeout,
                require_approval=False,  # Force non-interactive auto-deny permission mode
                model_name=model_name,
                state_file="state.json",
                llm_provider=llm_provider,
                lint_cmd=lint_cmd,
                skills_dir=skills_dir,
                mcp_config_path=mcp_config_path,
            )

            run_state_container = {
                "success": False,
                "exception": None,
                "completed": False,
            }

            def _target_worker():
                try:
                    # Real entrypoint is controller.loop.run(config)
                    run_state_container["success"] = controller.loop.run(config)
                except Exception as exc:
                    run_state_container["exception"] = exc
                finally:
                    run_state_container["completed"] = True

            start_time = time.time()
            worker_thread = threading.Thread(target=_target_worker, daemon=True)
            worker_thread.start()
            worker_thread.join(timeout=timeout)
            duration = time.time() - start_time

            # Determine wall-clock timeout vs normal completion vs exception
            if not run_state_container["completed"]:
                passed = False
                error_message = f"Benchmark timed out after {timeout} seconds"
            elif run_state_container["exception"] is not None:
                passed = False
                error_message = str(run_state_container["exception"])
            else:
                passed = bool(run_state_container["success"])
                error_message = None

            # Read back state.json from the isolated sandbox directory
            state_file_path = os.path.join(temp_repo_copy, config.state_file)
            iterations_count = 0
            tool_calls: Dict[str, int] = {}
            termination_reason = None

            if os.path.exists(state_file_path):
                try:
                    with open(state_file_path, "r", encoding="utf-8") as f:
                        state_data = json.load(f)
                    
                    iterations_list = state_data.get("iterations", [])
                    iterations_count = len(iterations_list)
                    termination_reason = state_data.get("termination_reason")

                    if "tool_calls_count" in state_data and isinstance(state_data["tool_calls_count"], dict):
                        tool_calls = dict(state_data["tool_calls_count"])
                    elif "audit_log" in state_data and isinstance(state_data["audit_log"], list):
                        for entry in state_data["audit_log"]:
                            t_name = entry.get("tool_name", "unknown")
                            tool_calls[t_name] = tool_calls.get(t_name, 0) + 1

                    if termination_reason == "success":
                        passed = True
                        error_message = None
                    elif termination_reason is not None and not passed:
                        if error_message is None:
                            error_message = f"Terminated: {termination_reason}"
                except Exception as e:
                    if error_message is None:
                        error_message = f"Failed to parse state.json: {e}"

            if not passed and error_message is None:
                error_message = termination_reason or "Benchmark run did not reach success"

            total_subtasks = 1
            subtasks_completed = 1 if passed else 0

            return BenchmarkResult(
                repo_name=repo_name,
                goal=effective_goal,
                passed=passed,
                iterations=iterations_count,
                duration_seconds=round(duration, 2),
                tool_calls_count=tool_calls,
                error_message=error_message,
                subtasks_completed=subtasks_completed,
                total_subtasks=total_subtasks,
            )
        finally:
            import stat
            for root, dirs, files in os.walk(temp_dir):
                for momo in dirs:
                    try:
                        os.chmod(os.path.join(root, momo), stat.S_IWRITE)
                    except Exception:
                        pass
                for momo in files:
                    try:
                        os.chmod(os.path.join(root, momo), stat.S_IWRITE)
                    except Exception:
                        pass
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def run_suite(
        self,
        benchmark_dir: str,
        filter_names: Optional[List[str]] = None,
        goal: Optional[str] = None,
        timeout: int = 300,
        **kwargs,
    ) -> List[BenchmarkResult]:
        """Run all discovered benchmark test cases matching optional filter.

        Args:
            benchmark_dir: Base directory containing benchmark repos.
            filter_names: Optional filter list for repo names.
            goal: Optional custom goal to apply to each run.
            timeout: Hard timeout in seconds per benchmark.
            **kwargs: Extra parameters forwarded to run_benchmark.

        Returns:
            List of BenchmarkResult objects for each executed repo.
        """
        repos = self.discover_repositories(benchmark_dir, filter_names)
        results: List[BenchmarkResult] = []

        print(f"\n[BENCHMARK] Discovered {len(repos)} benchmark repository target(s).")
        for idx, repo_path in enumerate(repos, 1):
            r_name = os.path.basename(repo_path)
            print(f"\n[BENCHMARK] [{idx}/{len(repos)}] Starting run for '{r_name}'...")
            res = self.run_benchmark(
                repo_path=repo_path,
                goal=goal,
                timeout=timeout,
                **kwargs,
            )
            status_str = "PASSED" if res.passed else f"FAILED ({res.error_message})"
            print(f"[BENCHMARK] [{idx}/{len(repos)}] '{r_name}' finished: {status_str} ({res.duration_seconds}s, {res.iterations} iterations)")
            results.append(res)

        return results
