"""controller/evaluator.py — Phase 6 (legacy) + Phase 3 (General Evaluator).

Phase 6 legacy (unchanged)
--------------------------
``EvaluatorResult`` TypedDict and ``evaluate_iteration()`` function remain
exactly as they were — they are called directly by ``controller/loop.py`` and
read by ``controller/router.py``.  Do NOT modify their signatures or behaviour.

Phase 3 additions (additive, no replacements)
---------------------------------------------
``VerificationResult``   — structured result dataclass for all new strategies.
``verify_file_exists``   — check that a file exists, with optional content/regex.
``verify_directory_exists`` — check that a directory exists.
``verify_command_execution`` — run a command and validate exit code / stdout.
``verify_python_import`` — attempt ``importlib.import_module`` on a module.
``verify_test_suite``    — thin wrapper around the existing ``run_tests`` logic.
``GeneralEvaluator``     — dispatches to the above strategies by explicit name.

Dispatch contract
-----------------
``GeneralEvaluator.evaluate()`` accepts an explicit strategy spec from the
caller — it does NOT interpret goal text itself.  Goal-to-strategy mapping is
the orchestrator's responsibility (a later phase).

Supported strategy names (case-insensitive):
    "file_exists"           -> verify_file_exists
    "directory_exists"      -> verify_directory_exists
    "command_execution"     -> verify_command_execution
    "python_import"         -> verify_python_import
    "test_suite"            -> verify_test_suite
"""

import importlib
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict

from controller.executor import run_tests as _run_tests


# ===========================================================================
# Phase 6 legacy — preserved exactly
# ===========================================================================

class EvaluatorResult(TypedDict):
    is_correct: bool
    score: float
    issues: List[str]
    critical_gaps: List[str]
    feedback: str


def evaluate_iteration(
    test_passed: bool,
    test_output_tail: str,
    lint_passed: Optional[bool] = None,
    lint_output_tail: Optional[str] = None,
    same_failure_count: int = 0,
) -> EvaluatorResult:
    """Evaluate raw verification signals into a structured evaluator result.

    Policy decisions (reflected exactly in the code below):

    1. **Tests gate correctness.** If tests fail: ``is_correct=False``,
       ``score=0.0``, ``issues`` contains ``"tests_failed"``, and
       ``critical_gaps`` contains ``"tests_must_pass"``.

    2. **Repeated failures escalate.** When ``same_failure_count >= 2``:
       ``"repeated_failure"`` is added to ``issues`` and
       ``"repeated_same_failure"`` to ``critical_gaps``.

    3. **Lint is informational only.** If tests pass but lint fails,
       ``is_correct`` stays ``True``, ``score`` stays ``1.0``, and
       ``"lint_failed"`` is added to ``issues``.  Lint alone never creates
       a critical gap or blocks correctness.
    """
    issues: List[str] = []
    critical_gaps: List[str] = []

    if not test_passed:
        issues.append("tests_failed")
        critical_gaps.append("tests_must_pass")
        if same_failure_count >= 2:
            issues.append("repeated_failure")
            critical_gaps.append("repeated_same_failure")

    if lint_passed is False:
        issues.append("lint_failed")

    # Score calculation & feedback composition
    if test_passed:
        is_correct = True
        score = 1.0
        if lint_passed is False:
            feedback = "Tests passed but lint reported issues."
        else:
            feedback = "All verification checks passed."
    else:
        is_correct = False
        score = 0.0
        tail_preview = (test_output_tail or "")[:200]
        if same_failure_count >= 2:
            feedback = f"Repeated failure ({same_failure_count} times). Output tail: {tail_preview}"
        else:
            feedback = f"Tests failed. Output tail: {tail_preview}"

    return {
        "is_correct": is_correct,
        "score": score,
        "issues": issues,
        "critical_gaps": critical_gaps,
        "feedback": feedback,
    }


# ===========================================================================
# Phase 3 additions
# ===========================================================================

# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """Structured outcome for every Phase 3 verification strategy.

    Attributes:
        passed:   True when the verification check succeeded.
        strategy: The strategy name that produced this result
                  (e.g. ``"file_exists"``).
        evidence: A human-readable summary of what was checked and found.
        issues:   List of specific problem strings (empty on success).
        details:  Arbitrary extra data produced by the strategy (paths,
                  stdout, exit codes, etc.) for callers that need it.
    """
    passed: bool
    strategy: str
    evidence: str
    issues: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Verification strategies
# ---------------------------------------------------------------------------

def verify_file_exists(
    path: str,
    expected_content: Optional[str] = None,
    regex: Optional[str] = None,
) -> VerificationResult:
    """Verify that *path* exists as a file, with optional content checks.

    Args:
        path:             Absolute or relative path to the file.
        expected_content: If provided, checks that this string appears
                          as a substring of the file contents.
        regex:            If provided, checks that the pattern matches
                          somewhere in the file contents (re.search).

    Returns:
        A ``VerificationResult`` with ``strategy="file_exists"``.
    """
    strategy = "file_exists"

    if not os.path.exists(path):
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"File not found: {path}",
            issues=["file_not_found"],
            details={"path": path},
        )

    if not os.path.isfile(path):
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"Path exists but is not a file: {path}",
            issues=["path_is_not_a_file"],
            details={"path": path},
        )

    issues: List[str] = []
    details: Dict[str, Any] = {"path": path}

    # Read content only if a content check is requested.
    if expected_content is not None or regex is not None:
        try:
            content = open(path, encoding="utf-8", errors="replace").read()
            details["content_length"] = len(content)
        except OSError as exc:
            return VerificationResult(
                passed=False,
                strategy=strategy,
                evidence=f"Could not read file: {exc}",
                issues=["read_error"],
                details={"path": path, "error": str(exc)},
            )

        if expected_content is not None and expected_content not in content:
            issues.append("expected_content_not_found")
            details["expected_content"] = expected_content

        if regex is not None:
            try:
                if not re.search(regex, content):
                    issues.append("regex_not_matched")
                    details["regex"] = regex
            except re.error as exc:
                issues.append("invalid_regex")
                details["regex_error"] = str(exc)

    if issues:
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"File exists but content checks failed: {issues}",
            issues=issues,
            details=details,
        )

    return VerificationResult(
        passed=True,
        strategy=strategy,
        evidence=f"File exists and all content checks passed: {path}",
        details=details,
    )


def verify_directory_exists(path: str) -> VerificationResult:
    """Verify that *path* exists as a directory.

    Args:
        path: Absolute or relative path to the directory.

    Returns:
        A ``VerificationResult`` with ``strategy="directory_exists"``.
    """
    strategy = "directory_exists"

    if not os.path.exists(path):
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"Directory not found: {path}",
            issues=["directory_not_found"],
            details={"path": path},
        )

    if not os.path.isdir(path):
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"Path exists but is not a directory: {path}",
            issues=["path_is_not_a_directory"],
            details={"path": path},
        )

    return VerificationResult(
        passed=True,
        strategy=strategy,
        evidence=f"Directory exists: {path}",
        details={"path": path},
    )


def verify_command_execution(
    command: str,
    expected_exit_code: int = 0,
    stdout_match: Optional[str] = None,
    cwd: Optional[str] = None,
    timeout: int = 30,
) -> VerificationResult:
    """Verify that *command* exits with *expected_exit_code* and optional stdout.

    Args:
        command:            Shell command string to run.
        expected_exit_code: Expected process exit code (default 0).
        stdout_match:       If provided, checks that this string appears
                            as a substring of stdout.
        cwd:                Working directory for the subprocess.
        timeout:            Seconds before the command is killed (default 30).

    Returns:
        A ``VerificationResult`` with ``strategy="command_execution"``.
    """
    strategy = "command_execution"
    resolved_cwd = cwd or os.getcwd()
    details: Dict[str, Any] = {
        "command": command,
        "expected_exit_code": expected_exit_code,
        "cwd": resolved_cwd,
    }

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=resolved_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"Command timed out after {timeout}s: {command}",
            issues=["command_timeout"],
            details={**details, "timeout": timeout},
        )
    except Exception as exc:  # noqa: BLE001
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"Command raised exception: {exc}",
            issues=["command_exception"],
            details={**details, "error": str(exc)},
        )

    details["exit_code"] = proc.returncode
    details["stdout"] = proc.stdout
    details["stderr"] = proc.stderr

    issues: List[str] = []

    if proc.returncode != expected_exit_code:
        issues.append("unexpected_exit_code")

    if stdout_match is not None and stdout_match not in proc.stdout:
        issues.append("stdout_match_not_found")
        details["stdout_match"] = stdout_match

    if issues:
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=(
                f"Command verification failed (issues={issues}). "
                f"exit_code={proc.returncode}, expected={expected_exit_code}"
            ),
            issues=issues,
            details=details,
        )

    return VerificationResult(
        passed=True,
        strategy=strategy,
        evidence=(
            f"Command exited with code {proc.returncode} as expected. "
            f"Command: {command}"
        ),
        details=details,
    )


def verify_python_import(
    module_name: str,
    python_path: Optional[str] = None,
) -> VerificationResult:
    """Verify that *module_name* can be imported by the running Python.

    Args:
        module_name:  Fully-qualified module name (e.g. ``"os.path"``).
        python_path:  Optional directory to prepend to ``sys.path`` before
                      attempting the import.  Restored afterward.

    Returns:
        A ``VerificationResult`` with ``strategy="python_import"``.
    """
    strategy = "python_import"
    details: Dict[str, Any] = {"module_name": module_name}

    # Temporarily extend sys.path if requested.
    _added = False
    if python_path and python_path not in sys.path:
        sys.path.insert(0, python_path)
        _added = True
        details["python_path_added"] = python_path

    try:
        importlib.import_module(module_name)
        result = VerificationResult(
            passed=True,
            strategy=strategy,
            evidence=f"Module '{module_name}' imported successfully.",
            details=details,
        )
    except ImportError as exc:
        result = VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"Module '{module_name}' could not be imported: {exc}",
            issues=["import_error"],
            details={**details, "error": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        result = VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"Unexpected error importing '{module_name}': {exc}",
            issues=["import_exception"],
            details={**details, "error": str(exc)},
        )
    finally:
        if _added:
            sys.path.remove(python_path)

    return result


def verify_test_suite(
    test_cmd: str,
    repo_path: str,
) -> VerificationResult:
    """Verify the project's test suite by running *test_cmd* in *repo_path*.

    Wraps the existing ``controller.executor.run_tests`` logic so the new
    evaluator pipeline reuses the same timeout/output handling as the loop.

    Args:
        test_cmd:  The command used to run tests (e.g. ``"pytest"``).
        repo_path: Absolute path to the repository root.

    Returns:
        A ``VerificationResult`` with ``strategy="test_suite"``.
    """
    strategy = "test_suite"
    details: Dict[str, Any] = {"test_cmd": test_cmd, "repo_path": repo_path}

    try:
        exec_result = _run_tests(repo_path, test_cmd)
    except Exception as exc:  # noqa: BLE001
        return VerificationResult(
            passed=False,
            strategy=strategy,
            evidence=f"Test runner raised exception: {exc}",
            issues=["runner_exception"],
            details={**details, "error": str(exc)},
        )

    details["exit_code"] = exec_result.returncode
    details["output_tail"] = exec_result.output_tail

    if exec_result.passed:
        return VerificationResult(
            passed=True,
            strategy=strategy,
            evidence=f"Test suite passed (exit code {exec_result.returncode}).",
            details=details,
        )

    return VerificationResult(
        passed=False,
        strategy=strategy,
        evidence=f"Test suite FAILED (exit code {exec_result.returncode}).",
        issues=["tests_failed"],
        details=details,
    )


# ---------------------------------------------------------------------------
# GeneralEvaluator
# ---------------------------------------------------------------------------

# Supported strategy names mapped to their callables.
_STRATEGY_REGISTRY: Dict[str, Any] = {
    "file_exists":        verify_file_exists,
    "directory_exists":   verify_directory_exists,
    "command_execution":  verify_command_execution,
    "python_import":      verify_python_import,
    "test_suite":         verify_test_suite,
}


class GeneralEvaluator:
    """Dispatches verification calls to the correct strategy.

    The caller must provide the strategy name explicitly in ``evaluate()``.
    This class does NOT interpret goal text to infer a strategy — that mapping
    is the orchestrator's job and belongs to a later phase.

    Usage::

        ev = GeneralEvaluator()
        result = ev.evaluate("file_exists", path="/tmp/output.py")
        assert result.passed
    """

    def evaluate(
        self,
        strategy: str,
        **kwargs: Any,
    ) -> VerificationResult:
        """Run the named verification strategy with the provided keyword args.

        Args:
            strategy: One of the supported strategy names (case-insensitive):
                      ``"file_exists"``, ``"directory_exists"``,
                      ``"command_execution"``, ``"python_import"``,
                      ``"test_suite"``.
            **kwargs: Arguments forwarded verbatim to the chosen strategy
                      function.

        Returns:
            A ``VerificationResult``.  On unknown strategy, returns a failed
            result with ``strategy="unknown"`` rather than raising.
        """
        key = strategy.lower().strip()
        func = _STRATEGY_REGISTRY.get(key)

        if func is None:
            known = sorted(_STRATEGY_REGISTRY)
            return VerificationResult(
                passed=False,
                strategy="unknown",
                evidence=f"Unknown strategy '{strategy}'. Known: {known}",
                issues=["unknown_strategy"],
                details={"requested": strategy, "known_strategies": known},
            )

        return func(**kwargs)

    @staticmethod
    def supported_strategies() -> List[str]:
        """Return the sorted list of registered strategy names."""
        return sorted(_STRATEGY_REGISTRY)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Phase 6 legacy
    "EvaluatorResult",
    "evaluate_iteration",
    # Phase 3 additions
    "VerificationResult",
    "verify_file_exists",
    "verify_directory_exists",
    "verify_command_execution",
    "verify_python_import",
    "verify_test_suite",
    "GeneralEvaluator",
]
