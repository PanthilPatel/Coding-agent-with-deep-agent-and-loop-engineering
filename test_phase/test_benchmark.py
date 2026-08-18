"""Phase 8 tests: Automated E2E Benchmark Harness & Reporting.

Covers:
- BenchmarkRunner sandbox isolation (real copy, not symlink) and cleanup.
- run_benchmark with mocked loop.run outcomes and state.json metric extraction.
- Non-interactive auto-deny permission gate (no input() hang).
- Hard wall-clock timeout enforcement independent of config.max_seconds.
- BenchmarkReporter JSON and Markdown table generation.
- CLI argument parsing and main() benchmark workflow integration.
"""

import json
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch
import pytest

from benchmarks.runner import BenchmarkResult, BenchmarkRunner
from benchmarks.reporter import BenchmarkReporter
from main import parse_args, main


# ---------------------------------------------------------------------------
# 1. BenchmarkResult Dataclass & docstring checks
# ---------------------------------------------------------------------------

class TestBenchmarkResult:
    def test_dataclass_fields_and_defaults(self):
        res = BenchmarkResult(
            repo_name="01_test",
            goal="fix bugs",
            passed=True,
            iterations=3,
            duration_seconds=12.5,
        )
        assert res.repo_name == "01_test"
        assert res.goal == "fix bugs"
        assert res.passed is True
        assert res.iterations == 3
        assert res.duration_seconds == 12.5
        assert res.tool_calls_count == {}
        assert res.error_message is None
        assert res.subtasks_completed == 0
        assert res.total_subtasks == 0

    def test_docstring_documents_guarded_tool_calls_limitation(self):
        doc = BenchmarkResult.__doc__ or ""
        assert "guarded" in doc.lower()
        assert "permissionharness" in doc.lower() or "limitation" in doc.lower()


# ---------------------------------------------------------------------------
# 2. BenchmarkRunner Discovery & Sandbox Isolation Tests
# ---------------------------------------------------------------------------

class TestBenchmarkRunnerSandboxAndDiscovery:
    @pytest.fixture
    def bench_dir(self, tmp_path):
        base = tmp_path / "benchmarks"
        base.mkdir()
        (base / "01_inventory").mkdir()
        (base / "02_calculator").mkdir()
        (base / "03_string_ops").mkdir()
        (base / "__pycache__").mkdir()
        (base / ".git").mkdir()
        (base / "regular_file.txt").write_text("not a dir")
        return str(base)

    def test_discover_repositories_all(self, bench_dir):
        runner = BenchmarkRunner()
        repos = runner.discover_repositories(bench_dir)
        names = [os.path.basename(r) for r in repos]
        assert names == ["01_inventory", "02_calculator", "03_string_ops"]

    def test_discover_repositories_with_filter(self, bench_dir):
        runner = BenchmarkRunner()
        repos = runner.discover_repositories(bench_dir, filter_names=["01", "calculator"])
        names = [os.path.basename(r) for r in repos]
        assert names == ["01_inventory", "02_calculator"]

    def test_discover_repositories_empty_or_missing(self, tmp_path):
        runner = BenchmarkRunner()
        assert runner.discover_repositories(str(tmp_path / "nonexistent")) == []

    def test_sandbox_is_real_deep_copy_not_symlink(self, tmp_path):
        source_repo = tmp_path / "source_repo"
        source_repo.mkdir()
        test_file = source_repo / "module.py"
        test_file.write_text("original_content = 1\n")

        runner = BenchmarkRunner()
        captured_config = {}

        def mock_loop_run(config):
            captured_config["repo_path"] = config.repo_path
            # Verify file exists in temp directory
            temp_file = os.path.join(config.repo_path, "module.py")
            assert os.path.exists(temp_file)
            assert not os.path.islink(temp_file)
            assert not os.path.islink(config.repo_path)

            # Mutate the file in the temp sandbox
            with open(temp_file, "w") as f:
                f.write("mutated_content = 999\n")

            # Write a state.json
            state_data = {
                "goal": config.goal,
                "iterations": [{"iteration": 1, "test_passed": True}],
                "termination_reason": "success",
            }
            with open(os.path.join(config.repo_path, config.state_file), "w") as f:
                json.dump(state_data, f)
            return True

        with patch("controller.loop.run", side_effect=mock_loop_run):
            result = runner.run_benchmark(str(source_repo))

        assert result.passed is True
        # Verify source repo was NOT modified
        assert test_file.read_text() == "original_content = 1\n"
        # Verify temp sandbox was cleaned up
        assert not os.path.exists(captured_config["repo_path"])


# ---------------------------------------------------------------------------
# 3. BenchmarkRunner Execution & Metric Extraction Tests
# ---------------------------------------------------------------------------

class TestBenchmarkRunnerExecution:
    @pytest.fixture
    def dummy_repo(self, tmp_path):
        repo = tmp_path / "calc_repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a, b): return a + b\n")
        return str(repo)

    def test_run_benchmark_success_with_state_extraction(self, dummy_repo):
        runner = BenchmarkRunner()

        def mock_loop_run(config):
            state_data = {
                "goal": config.goal,
                "iterations": [
                    {"iteration": 1, "test_passed": False},
                    {"iteration": 2, "test_passed": True},
                ],
                "termination_reason": "success",
                "tool_calls_count": {
                    "execute_command": 2,
                    "delete_file": 1,
                },
            }
            with open(os.path.join(config.repo_path, config.state_file), "w") as f:
                json.dump(state_data, f)
            return True

        with patch("controller.loop.run", side_effect=mock_loop_run):
            result = runner.run_benchmark(dummy_repo)

        assert result.repo_name == "calc_repo"
        assert result.passed is True
        assert result.iterations == 2
        assert result.tool_calls_count == {"execute_command": 2, "delete_file": 1}
        assert result.error_message is None
        assert result.subtasks_completed == 1
        assert result.total_subtasks == 1

    def test_run_benchmark_failure_with_state_extraction(self, dummy_repo):
        runner = BenchmarkRunner()

        def mock_loop_run(config):
            state_data = {
                "goal": config.goal,
                "iterations": [
                    {"iteration": 1, "test_passed": False},
                    {"iteration": 2, "test_passed": False},
                ],
                "termination_reason": "max_iterations_safety_limit",
                "audit_log": [
                    {"tool_name": "execute_command", "decision": "allowed"},
                    {"tool_name": "execute_command", "decision": "allowed"},
                ],
            }
            with open(os.path.join(config.repo_path, config.state_file), "w") as f:
                json.dump(state_data, f)
            return False

        with patch("controller.loop.run", side_effect=mock_loop_run):
            result = runner.run_benchmark(dummy_repo)

        assert result.passed is False
        assert result.iterations == 2
        assert result.tool_calls_count == {"execute_command": 2}
        assert "max_iterations_safety_limit" in result.error_message

    def test_run_benchmark_loop_exception_handled(self, dummy_repo):
        runner = BenchmarkRunner()

        with patch("controller.loop.run", side_effect=RuntimeError("LLM API connection failed")):
            result = runner.run_benchmark(dummy_repo)

        assert result.passed is False
        assert "LLM API connection failed" in result.error_message

    def test_run_suite_aggregates_multiple_repos(self, tmp_path):
        base = tmp_path / "suite"
        base.mkdir()
        (base / "repo_a").mkdir()
        (base / "repo_b").mkdir()

        runner = BenchmarkRunner()
        with patch("controller.loop.run", return_value=True):
            results = runner.run_suite(str(base))

        assert len(results) == 2
        assert {r.repo_name for r in results} == {"repo_a", "repo_b"}
        assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# 4. Non-Interactive Permission Gate & Timeout Tests
# ---------------------------------------------------------------------------

class TestBenchmarkSafetyAndTimeout:
    @pytest.fixture
    def dummy_repo(self, tmp_path):
        repo = tmp_path / "guarded_repo"
        repo.mkdir()
        (repo / "test.py").write_text("assert True\n")
        return str(repo)

    def test_non_interactive_mode_auto_denies_without_input_hang(self, dummy_repo):
        runner = BenchmarkRunner()

        input_called = False

        def mock_input(prompt=""):
            nonlocal input_called
            input_called = True
            raise AssertionError("input() should NEVER be called in non-interactive benchmark harness!")

        def mock_loop_run(config):
            # Assert config forces require_approval=False
            assert config.require_approval is False
            from controller.permissions import PermissionHarness
            harness = PermissionHarness(interactive=False, prompter=mock_input)
            res = harness.execute_guarded(lambda: "done", "confirm", tool_name="guarded_op")
            assert res.get("status") == "permission_denied"

            state_data = {
                "goal": config.goal,
                "iterations": [{"iteration": 1, "test_passed": False}],
                "termination_reason": "user_rejected",
            }
            with open(os.path.join(config.repo_path, config.state_file), "w") as f:
                json.dump(state_data, f)
            return False

        with patch("controller.loop.run", side_effect=mock_loop_run):
            start_t = time.time()
            result = runner.run_benchmark(dummy_repo)
            elapsed = time.time() - start_t

        assert elapsed < 5.0  # Runs instantaneously, never hangs
        assert input_called is False
        assert result.passed is False

    def test_hard_timeout_wall_clock_enforcement(self, dummy_repo):
        runner = BenchmarkRunner()

        def hanging_loop_run(config):
            # Simulate a deadlocked or very slow agent call (e.g. 5 seconds)
            time.sleep(2.0)
            return True

        with patch("controller.loop.run", side_effect=hanging_loop_run):
            start_t = time.time()
            # Enforce 0.2s hard timeout
            result = runner.run_benchmark(dummy_repo, timeout=1)
            elapsed = time.time() - start_t

        assert result.passed is False
        assert "timed out" in result.error_message.lower()
        # Verify elapsed time is well below the full sleep duration
        assert elapsed < 1.8


# ---------------------------------------------------------------------------
# 5. BenchmarkReporter Tests
# ---------------------------------------------------------------------------

class TestBenchmarkReporter:
    @pytest.fixture
    def sample_results(self):
        return [
            BenchmarkResult(
                repo_name="01_inventory",
                goal="fix tests",
                passed=True,
                iterations=2,
                duration_seconds=15.4,
                tool_calls_count={"execute_command": 3},
                subtasks_completed=1,
                total_subtasks=1,
            ),
            BenchmarkResult(
                repo_name="02_calculator",
                goal="fix tests",
                passed=False,
                iterations=5,
                duration_seconds=30.2,
                tool_calls_count={"delete_file": 1},
                error_message="Terminated: max_iterations_safety_limit",
                subtasks_completed=0,
                total_subtasks=1,
            ),
        ]

    def test_generate_json(self, tmp_path, sample_results):
        reporter = BenchmarkReporter()
        output_file = tmp_path / "results" / "report.json"
        reporter.generate_json(sample_results, str(output_file))

        assert output_file.exists()
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "summary" in data
        assert data["summary"]["total_runs"] == 2
        assert data["summary"]["passed"] == 1
        assert data["summary"]["failed"] == 1
        assert data["summary"]["pass_rate_percent"] == 50.0
        assert data["summary"]["total_iterations"] == 7
        assert len(data["results"]) == 2
        assert data["results"][0]["repo_name"] == "01_inventory"

    def test_generate_markdown(self, tmp_path, sample_results):
        reporter = BenchmarkReporter()
        output_file = tmp_path / "results" / "report.md"
        md_text = reporter.generate_markdown(sample_results, str(output_file))

        assert output_file.exists()
        assert "# Autonomous Coding Agent — Benchmark Report" in md_text
        assert "- **Total Runs:** 2" in md_text
        assert "- **Pass Rate:** 50.0%" in md_text
        assert "- **Total Iterations:** 7" in md_text
        assert "| `01_inventory` | ✅ PASSED | 2 |" in md_text
        assert "| `02_calculator` | ❌ FAILED | 5 |" in md_text
        assert "Guarded Tool Calls" in md_text
        assert "execute_command: 3" in md_text
        assert "PermissionHarness" in md_text

    def test_print_summary(self, sample_results, capsys):
        reporter = BenchmarkReporter()
        reporter.print_summary(sample_results)
        captured = capsys.readouterr().out
        assert "BENCHMARK SUITE SUMMARY" in captured
        assert "01_inventory" in captured
        assert "02_calculator" in captured


# ---------------------------------------------------------------------------
# 6. CLI Argument Parsing & Benchmark Mode Integration
# ---------------------------------------------------------------------------

class TestCLIBenchmarkArgs:
    def test_parse_args_benchmark_flags(self):
        args = parse_args([
            "--benchmark",
            "--benchmark-dir", "custom/bench/dir",
            "--filter", "01,06",
            "--benchmark-timeout", "120",
            "--output-dir", "custom_reports",
        ])
        assert args.benchmark is True
        assert args.benchmark_dir == "custom/bench/dir"
        assert args.filter == "01,06"
        assert args.benchmark_timeout == 120
        assert args.output_dir == "custom_reports"

    def test_main_benchmark_execution_flow(self, tmp_path):
        sample_result = BenchmarkResult(
            repo_name="01_test",
            goal="fix test",
            passed=True,
            iterations=1,
            duration_seconds=5.0,
        )

        out_dir = tmp_path / "test_benchmark_results"

        with patch("sys.argv", [
            "main.py",
            "--benchmark",
            "--benchmark-dir", str(tmp_path),
            "--filter", "01",
            "--output-dir", str(out_dir),
        ]), patch.object(BenchmarkRunner, "run_suite", return_value=[sample_result]) as mock_suite, \
           pytest.raises(SystemExit) as excinfo:
            main()

        assert excinfo.value.code == 0
        mock_suite.assert_called_once()
        assert (out_dir / "benchmark_report.json").exists()
        assert (out_dir / "benchmark_report.md").exists()
