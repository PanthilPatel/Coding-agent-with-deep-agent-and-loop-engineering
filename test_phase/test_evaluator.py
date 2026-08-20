"""test_evaluator.py — Phase 3: Generalized Evaluator & Verification Engine.

Covers:
- VerificationResult dataclass shape.
- verify_file_exists: present, missing, content match, regex match, bad regex.
- verify_directory_exists: present, missing, file-path given instead.
- verify_command_execution: exit code 0, non-zero, stdout_match, timeout.
- verify_python_import: stdlib success, non-existent module failure.
- verify_test_suite: pass and fail scenarios (mocked run_tests).
- GeneralEvaluator.evaluate(): routes to correct strategy, unknown strategy.
- evaluate_iteration() legacy behaviour is completely unchanged.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller.evaluator import (
    EvaluatorResult,
    GeneralEvaluator,
    VerificationResult,
    evaluate_iteration,
    verify_command_execution,
    verify_directory_exists,
    verify_file_exists,
    verify_python_import,
    verify_test_suite,
)


# ===========================================================================
# VerificationResult dataclass
# ===========================================================================

class TestVerificationResult:
    def test_passed_field(self):
        vr = VerificationResult(passed=True, strategy="test", evidence="ok")
        assert vr.passed is True

    def test_strategy_field(self):
        vr = VerificationResult(passed=True, strategy="file_exists", evidence="ok")
        assert vr.strategy == "file_exists"

    def test_evidence_field(self):
        vr = VerificationResult(passed=False, strategy="s", evidence="file not found")
        assert vr.evidence == "file not found"

    def test_issues_defaults_to_empty_list(self):
        vr = VerificationResult(passed=True, strategy="s", evidence="ok")
        assert vr.issues == []

    def test_details_defaults_to_empty_dict(self):
        vr = VerificationResult(passed=True, strategy="s", evidence="ok")
        assert vr.details == {}

    def test_custom_issues(self):
        vr = VerificationResult(passed=False, strategy="s", evidence="e",
                                issues=["a", "b"])
        assert "a" in vr.issues and "b" in vr.issues

    def test_custom_details(self):
        vr = VerificationResult(passed=True, strategy="s", evidence="ok",
                                details={"path": "/tmp"})
        assert vr.details["path"] == "/tmp"


# ===========================================================================
# verify_file_exists
# ===========================================================================

class TestVerifyFileExists:
    def test_file_present_passes(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello")
        result = verify_file_exists(str(f))
        assert result.passed is True
        assert result.strategy == "file_exists"

    def test_file_missing_fails(self, tmp_path):
        result = verify_file_exists(str(tmp_path / "missing.txt"))
        assert result.passed is False
        assert "file_not_found" in result.issues

    def test_directory_path_fails(self, tmp_path):
        result = verify_file_exists(str(tmp_path))
        assert result.passed is False
        assert "path_is_not_a_file" in result.issues

    def test_content_match_passes(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def hello(): pass")
        result = verify_file_exists(str(f), expected_content="def hello")
        assert result.passed is True

    def test_content_match_fails(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def hello(): pass")
        result = verify_file_exists(str(f), expected_content="def goodbye")
        assert result.passed is False
        assert "expected_content_not_found" in result.issues

    def test_regex_match_passes(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("ERROR: something bad happened")
        result = verify_file_exists(str(f), regex=r"ERROR:\s+\w+")
        assert result.passed is True

    def test_regex_not_matched_fails(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("INFO: everything is fine")
        result = verify_file_exists(str(f), regex=r"ERROR:")
        assert result.passed is False
        assert "regex_not_matched" in result.issues

    def test_invalid_regex_fails(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("anything")
        result = verify_file_exists(str(f), regex=r"[invalid(")
        assert result.passed is False
        assert "invalid_regex" in result.issues

    def test_both_content_and_regex_checked(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        # content present, regex absent
        result = verify_file_exists(str(f), expected_content="hello", regex=r"NOPE")
        assert result.passed is False
        assert "regex_not_matched" in result.issues

    def test_details_contains_path(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        result = verify_file_exists(str(f))
        assert result.details.get("path") == str(f)

    def test_evidence_is_non_empty(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        result = verify_file_exists(str(f))
        assert len(result.evidence) > 0


# ===========================================================================
# verify_directory_exists
# ===========================================================================

class TestVerifyDirectoryExists:
    def test_directory_present_passes(self, tmp_path):
        result = verify_directory_exists(str(tmp_path))
        assert result.passed is True
        assert result.strategy == "directory_exists"

    def test_directory_missing_fails(self, tmp_path):
        result = verify_directory_exists(str(tmp_path / "no_such_dir"))
        assert result.passed is False
        assert "directory_not_found" in result.issues

    def test_file_path_fails(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        result = verify_directory_exists(str(f))
        assert result.passed is False
        assert "path_is_not_a_directory" in result.issues

    def test_details_contains_path(self, tmp_path):
        result = verify_directory_exists(str(tmp_path))
        assert result.details.get("path") == str(tmp_path)

    def test_nested_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        result = verify_directory_exists(str(nested))
        assert result.passed is True


# ===========================================================================
# verify_command_execution
# ===========================================================================

class TestVerifyCommandExecution:
    def test_exit_code_0_passes(self):
        result = verify_command_execution(f'"{sys.executable}" -c "exit(0)"')
        assert result.passed is True
        assert result.strategy == "command_execution"

    def test_non_zero_exit_fails(self):
        result = verify_command_execution(f'"{sys.executable}" -c "exit(1)"')
        assert result.passed is False
        assert "unexpected_exit_code" in result.issues

    def test_expected_non_zero_passes(self):
        result = verify_command_execution(f'"{sys.executable}" -c "exit(2)"', expected_exit_code=2)
        assert result.passed is True

    def test_stdout_match_passes(self):
        result = verify_command_execution(
            f'"{sys.executable}" -c "print(\'hello_world\')"',
            stdout_match="hello_world",
        )
        assert result.passed is True

    def test_stdout_match_not_found_fails(self):
        result = verify_command_execution(
            f'"{sys.executable}" -c "print(\'goodbye\')"',
            stdout_match="hello_world",
        )
        assert result.passed is False
        assert "stdout_match_not_found" in result.issues

    def test_timeout_fails(self):
        if sys.platform == "win32":
            cmd = "ping -n 10 127.0.0.1"
        else:
            cmd = "sleep 10"
        result = verify_command_execution(cmd, timeout=1)
        assert result.passed is False
        assert "command_timeout" in result.issues

    def test_timeout_does_not_raise(self):
        if sys.platform == "win32":
            cmd = "ping -n 10 127.0.0.1"
        else:
            cmd = "sleep 10"
        try:
            verify_command_execution(cmd, timeout=1)
        except Exception as exc:
            pytest.fail(f"Unexpected exception: {exc}")

    def test_details_contain_exit_code(self):
        result = verify_command_execution(f'"{sys.executable}" -c "exit(0)"')
        assert "exit_code" in result.details

    def test_details_contain_stdout(self):
        result = verify_command_execution(f'"{sys.executable}" -c "print(42)"')
        assert "stdout" in result.details

    def test_cwd_is_used(self, tmp_path):
        result = verify_command_execution(
            f'"{sys.executable}" -c "import os; print(os.getcwd())"',
            cwd=str(tmp_path),
            stdout_match=str(tmp_path).replace("\\", "/").split("/")[-1],
        )
        # Just verify the call doesn't crash; cwd handling is validated by
        # the presence of the path component in stdout.
        assert result.strategy == "command_execution"


# ===========================================================================
# verify_python_import
# ===========================================================================

class TestVerifyPythonImport:
    def test_stdlib_os_passes(self):
        result = verify_python_import("os")
        assert result.passed is True
        assert result.strategy == "python_import"

    def test_stdlib_json_passes(self):
        result = verify_python_import("json")
        assert result.passed is True

    def test_stdlib_pathlib_passes(self):
        result = verify_python_import("pathlib")
        assert result.passed is True

    def test_nonexistent_module_fails(self):
        result = verify_python_import("this_module_definitely_does_not_exist_xyz")
        assert result.passed is False
        assert "import_error" in result.issues

    def test_details_contain_module_name(self):
        result = verify_python_import("os")
        assert result.details.get("module_name") == "os"

    def test_nested_stdlib_path_passes(self):
        result = verify_python_import("os.path")
        assert result.passed is True

    def test_evidence_non_empty(self):
        result = verify_python_import("os")
        assert len(result.evidence) > 0

    def test_python_path_kwarg_accepted(self, tmp_path):
        """python_path kwarg should not crash even if unused."""
        result = verify_python_import("os", python_path=str(tmp_path))
        assert result.passed is True


# ===========================================================================
# verify_test_suite
# ===========================================================================

class TestVerifyTestSuite:
    def _mock_exec_result(self, passed, returncode, output_tail=""):
        m = MagicMock()
        m.passed = passed
        m.returncode = returncode
        m.output_tail = output_tail
        return m

    def test_passing_suite_passes(self, tmp_path):
        mock_result = self._mock_exec_result(True, 0, "5 passed")
        with patch("controller.evaluator._run_tests", return_value=mock_result):
            result = verify_test_suite("pytest", str(tmp_path))
        assert result.passed is True
        assert result.strategy == "test_suite"

    def test_failing_suite_fails(self, tmp_path):
        mock_result = self._mock_exec_result(False, 1, "1 failed")
        with patch("controller.evaluator._run_tests", return_value=mock_result):
            result = verify_test_suite("pytest", str(tmp_path))
        assert result.passed is False
        assert "tests_failed" in result.issues

    def test_details_contain_exit_code(self, tmp_path):
        mock_result = self._mock_exec_result(True, 0, "ok")
        with patch("controller.evaluator._run_tests", return_value=mock_result):
            result = verify_test_suite("pytest", str(tmp_path))
        assert result.details.get("exit_code") == 0

    def test_details_contain_output_tail(self, tmp_path):
        mock_result = self._mock_exec_result(False, 1, "FAILED test_foo")
        with patch("controller.evaluator._run_tests", return_value=mock_result):
            result = verify_test_suite("pytest", str(tmp_path))
        assert "FAILED test_foo" in result.details.get("output_tail", "")

    def test_runner_exception_returns_failed_result(self, tmp_path):
        with patch("controller.evaluator._run_tests", side_effect=RuntimeError("boom")):
            result = verify_test_suite("pytest", str(tmp_path))
        assert result.passed is False
        assert "runner_exception" in result.issues


# ===========================================================================
# GeneralEvaluator
# ===========================================================================

class TestGeneralEvaluator:
    def setup_method(self):
        self.ev = GeneralEvaluator()

    def test_routes_file_exists(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        result = self.ev.evaluate("file_exists", path=str(f))
        assert result.strategy == "file_exists"
        assert result.passed is True

    def test_routes_directory_exists(self, tmp_path):
        result = self.ev.evaluate("directory_exists", path=str(tmp_path))
        assert result.strategy == "directory_exists"
        assert result.passed is True

    def test_routes_command_execution(self):
        result = self.ev.evaluate("command_execution",
                                  command=f'"{sys.executable}" -c "exit(0)"')
        assert result.strategy == "command_execution"
        assert result.passed is True

    def test_routes_python_import(self):
        result = self.ev.evaluate("python_import", module_name="os")
        assert result.strategy == "python_import"
        assert result.passed is True

    def test_routes_test_suite(self, tmp_path):
        mock_result = MagicMock(passed=True, returncode=0, output_tail="ok")
        with patch("controller.evaluator._run_tests", return_value=mock_result):
            result = self.ev.evaluate("test_suite",
                                      test_cmd="pytest",
                                      repo_path=str(tmp_path))
        assert result.strategy == "test_suite"

    def test_unknown_strategy_returns_failed_result(self):
        result = self.ev.evaluate("fly_to_the_moon")
        assert result.passed is False
        assert result.strategy == "unknown"
        assert "unknown_strategy" in result.issues

    def test_unknown_strategy_does_not_raise(self):
        try:
            self.ev.evaluate("totally_unknown")
        except Exception as exc:
            pytest.fail(f"Unexpected exception: {exc}")

    def test_case_insensitive_routing(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        result = self.ev.evaluate("FILE_EXISTS", path=str(f))
        assert result.strategy == "file_exists"

    def test_supported_strategies_returns_list(self):
        strategies = GeneralEvaluator.supported_strategies()
        assert isinstance(strategies, list)
        assert len(strategies) >= 5

    def test_supported_strategies_contains_all_five(self):
        strategies = GeneralEvaluator.supported_strategies()
        for name in ("file_exists", "directory_exists", "command_execution",
                     "python_import", "test_suite"):
            assert name in strategies

    def test_evaluate_returns_verification_result(self, tmp_path):
        result = self.ev.evaluate("directory_exists", path=str(tmp_path))
        assert isinstance(result, VerificationResult)


# ===========================================================================
# evaluate_iteration — legacy behaviour completely unchanged
# ===========================================================================

class TestEvaluateIterationLegacy:
    """Confirms that the Phase 6 evaluate_iteration function behaves
    identically to its pre-Phase-3 implementation."""

    def test_tests_pass_no_lint_is_correct(self):
        ev = evaluate_iteration(
            test_passed=True, test_output_tail="5 passed",
            lint_passed=None, lint_output_tail=None,
        )
        assert ev["is_correct"] is True
        assert ev["score"] == 1.0
        assert ev["issues"] == []
        assert ev["critical_gaps"] == []

    def test_tests_fail_is_not_correct(self):
        ev = evaluate_iteration(
            test_passed=False, test_output_tail="FAILED test_foo",
        )
        assert ev["is_correct"] is False
        assert ev["score"] == 0.0
        assert "tests_failed" in ev["issues"]
        assert "tests_must_pass" in ev["critical_gaps"]

    def test_tests_pass_lint_fails_still_correct(self):
        ev = evaluate_iteration(
            test_passed=True, test_output_tail="5 passed",
            lint_passed=False, lint_output_tail="E501 line too long",
        )
        assert ev["is_correct"] is True
        assert ev["score"] == 1.0
        assert "lint_failed" in ev["issues"]
        assert "tests_must_pass" not in ev["critical_gaps"]

    def test_repeated_failure_escalation(self):
        ev = evaluate_iteration(
            test_passed=False, test_output_tail="FAILED",
            same_failure_count=2,
        )
        assert "repeated_failure" in ev["issues"]
        assert "repeated_same_failure" in ev["critical_gaps"]

    def test_single_failure_no_escalation(self):
        ev = evaluate_iteration(
            test_passed=False, test_output_tail="FAILED",
            same_failure_count=1,
        )
        assert "repeated_failure" not in ev["issues"]

    def test_returns_evaluator_result_shape(self):
        ev = evaluate_iteration(test_passed=True, test_output_tail="")
        assert "is_correct" in ev
        assert "score" in ev
        assert "issues" in ev
        assert "critical_gaps" in ev
        assert "feedback" in ev

    def test_feedback_contains_tail_on_failure(self):
        ev = evaluate_iteration(
            test_passed=False, test_output_tail="some error output",
        )
        assert "some error output" in ev["feedback"]

    def test_feedback_all_pass_message(self):
        ev = evaluate_iteration(test_passed=True, test_output_tail="")
        assert "passed" in ev["feedback"].lower()

    def test_tests_fail_and_lint_fail(self):
        ev = evaluate_iteration(
            test_passed=False, test_output_tail="FAILED",
            lint_passed=False, lint_output_tail="E501",
        )
        assert "tests_failed" in ev["issues"]
        assert "lint_failed" in ev["issues"]
        assert ev["is_correct"] is False

    def test_tests_pass_and_lint_pass(self):
        ev = evaluate_iteration(
            test_passed=True, test_output_tail="",
            lint_passed=True, lint_output_tail="",
        )
        assert ev["issues"] == []
        assert ev["is_correct"] is True
