"""Evaluator module for Phase 6.

Evaluates objective verification signals (test pass/fail, lint pass/fail,
repeated failure counts) to produce a structured EvaluatorResult dict.
"""

from typing import Optional, List, TypedDict, Dict, Any


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
