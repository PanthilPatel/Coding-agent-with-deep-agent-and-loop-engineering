"""Router module for Phase 6.

Weighs evaluator output against run safety limits (iterations, time, repeated failure thresholds)
to determine whether to continue the execution loop or terminate.
"""

from typing import Optional, TypedDict, Dict, Any
from controller.state import TerminationReason


class RouterDecision(TypedDict):
    """Documented shape of the router's return value.

    Note: ``decide_next_step`` returns a plain ``dict`` (not a strict
    ``RouterDecision`` instance) for compatibility with callers that check
    for the ``"continue"`` key directly.  This TypedDict serves as a type
    annotation reference.
    """
    continue_loop: bool
    termination_reason: Optional[str]


def decide_next_step(
    evaluator_result: Dict[str, Any],
    current_iteration: int,
    max_iterations: int,
    elapsed_seconds: float,
    max_seconds: float,
    same_failure_count: int = 0,
    max_same_failures: Optional[int] = None,
) -> Dict[str, Any]:
    """Decide whether to continue the loop or stop.

    Checks termination conditions in priority order:
      1. Success (``is_correct`` is True in evaluator result)
      2. Time-out (``elapsed_seconds > max_seconds``)
      3. Repeated-failure threshold (``same_failure_count >= max_same_failures``
         when ``max_same_failures`` is set)
      4. Iteration limit (``current_iteration >= max_iterations``)
      5. Continue (none of the above triggered)

    Args:
        evaluator_result:  The dict returned by ``evaluate_iteration()``.
        current_iteration: The 1-based index of the iteration just completed.
        max_iterations:    Hard cap on iteration count (from ``Config``).
        elapsed_seconds:   Wall-clock seconds elapsed since the run started.
        max_seconds:       Hard cap on wall-clock time (from ``Config``).
        same_failure_count: How many consecutive iterations produced the
                            same failure signature.
        max_same_failures: When set, stop with ``VERIFICATION_FAILED`` if
                           ``same_failure_count`` reaches this value.

    Returns:
        A dict with keys:
          - ``"continue"`` (bool): True if the loop should proceed.
          - ``"termination_reason"`` (str | None): One of the
            ``TerminationReason`` constants, or None when continuing.
    """
    is_correct = evaluator_result.get("is_correct", False)

    if is_correct:
        return {
            "continue": False,
            "termination_reason": TerminationReason.SUCCESS,
        }

    if elapsed_seconds > max_seconds:
        return {
            "continue": False,
            "termination_reason": TerminationReason.TIMEOUT,
        }

    if max_same_failures is not None and same_failure_count >= max_same_failures:
        return {
            "continue": False,
            "termination_reason": TerminationReason.VERIFICATION_FAILED,
        }

    if current_iteration >= max_iterations:
        return {
            "continue": False,
            "termination_reason": TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT,
        }

    return {
        "continue": True,
        "termination_reason": None,
    }
