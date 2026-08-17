"""Router module for Phase 5 & 6.

Weighs evaluator output against run safety limits (iterations, time, repeated failure thresholds)
to determine whether to continue the execution loop, replan, recover, or terminate.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, TypedDict, Dict, Any, Union
from controller.state import TerminationReason
from controller.evaluator import VerificationResult


class RouterState(str, Enum):
    """Execution states the router can direct the loop to."""
    CONTINUE = "CONTINUE"
    REPLAN = "REPLAN"
    RECOVER = "RECOVER"
    WAIT_FOR_PERMISSION = "WAIT_FOR_PERMISSION"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass
class RoutingDecision:
    """Structured decision returned by decide_next_step.

    Maintains backwards compatibility by behaving like a dict for keys:
    ``"continue"``, ``"should_continue"``, ``"termination_reason"``,
    ``"state"``, ``"reason"``, ``"suggested_action"``.
    """
    state: RouterState
    should_continue: bool
    reason: str
    suggested_action: Optional[str] = None
    termination_reason: Optional[str] = None

    @property
    def continue_loop(self) -> bool:
        return self.should_continue

    def __getitem__(self, key: str) -> Any:
        if key in ("continue", "should_continue", "continue_loop"):
            return self.should_continue
        if key == "termination_reason":
            return self.termination_reason
        if key == "state":
            return self.state
        if key == "reason":
            return self.reason
        if key == "suggested_action":
            return self.suggested_action
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "continue": self.should_continue,
            "should_continue": self.should_continue,
            "termination_reason": self.termination_reason,
            "state": self.state.value if isinstance(self.state, RouterState) else str(self.state),
            "reason": self.reason,
            "suggested_action": self.suggested_action,
        }


def decide_next_step(
    evaluator_result: Union[Dict[str, Any], VerificationResult],
    current_iteration: int,
    max_iterations: int,
    elapsed_seconds: float,
    max_seconds: float,
    same_failure_count: int = 0,
    max_same_failures: Optional[int] = None,
) -> RoutingDecision:
    """Decide whether to continue the loop, recover/replan, or stop.

    Checks termination & routing conditions in priority order:
      1. Success (``passed`` / ``is_correct`` is True) -> COMPLETE
      2. Time-out (``elapsed_seconds > max_seconds``) -> FAILED (TIMEOUT)
      3. Repeated-failure threshold -> REPLAN or FAILED (VERIFICATION_FAILED)
      4. Iteration limit (``current_iteration >= max_iterations``) -> FAILED (MAX_ITERATIONS)
      5. Error recovery (repeated failure count == 1 or explicit issues) -> RECOVER
      6. Continue (none of the above triggered) -> CONTINUE

    Args:
        evaluator_result:  The dict returned by ``evaluate_iteration()`` or a
                           ``VerificationResult`` instance.
        current_iteration: The 1-based index of the iteration just completed.
        max_iterations:    Hard cap on iteration count (from ``Config``).
        elapsed_seconds:   Wall-clock seconds elapsed since the run started.
        max_seconds:       Hard cap on wall-clock time (from ``Config``).
        same_failure_count: How many consecutive iterations produced the
                            same failure signature.
        max_same_failures: When set, stop with ``VERIFICATION_FAILED`` if
                           ``same_failure_count`` reaches this value.

    Returns:
        A ``RoutingDecision`` instance containing the state, continuation flag,
        termination reason, and human-readable reason/action.
    """
    # Extract pass/fail indicator from either dict or VerificationResult
    if isinstance(evaluator_result, VerificationResult):
        is_success = evaluator_result.passed
        issues = evaluator_result.issues
    elif isinstance(evaluator_result, dict):
        is_success = evaluator_result.get("is_correct", False) or evaluator_result.get("passed", False)
        issues = evaluator_result.get("issues", [])
    else:
        is_success = False
        issues = []

    if is_success:
        return RoutingDecision(
            state=RouterState.COMPLETE,
            should_continue=False,
            reason="Goal achieved successfully with all verification checks passed.",
            termination_reason=TerminationReason.SUCCESS,
        )

    if elapsed_seconds > max_seconds:
        return RoutingDecision(
            state=RouterState.FAILED,
            should_continue=False,
            reason=f"Execution timed out ({elapsed_seconds:.1f}s > {max_seconds}s limit).",
            termination_reason=TerminationReason.TIMEOUT,
        )

    if max_same_failures is not None and same_failure_count >= max_same_failures:
        return RoutingDecision(
            state=RouterState.FAILED,
            should_continue=False,
            reason=f"Exceeded maximum allowed repeated failures ({same_failure_count} >= {max_same_failures}).",
            termination_reason=TerminationReason.VERIFICATION_FAILED,
        )

    if current_iteration >= max_iterations:
        return RoutingDecision(
            state=RouterState.FAILED,
            should_continue=False,
            reason=f"Reached maximum iteration limit ({current_iteration}/{max_iterations}).",
            termination_reason=TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT,
        )

    # If repeating the exact same failure (>=2 times), trigger REPLAN
    if same_failure_count >= 2:
        return RoutingDecision(
            state=RouterState.REPLAN,
            should_continue=True,
            reason=f"Repeated identical failure {same_failure_count} times.",
            suggested_action="Force strategy change and discard previous failing approach.",
            termination_reason=None,
        )

    # If single failure or specific issues present, trigger RECOVER
    if same_failure_count == 1 or issues:
        return RoutingDecision(
            state=RouterState.RECOVER,
            should_continue=True,
            reason="Verification failed; recovery step required.",
            suggested_action="Analyze error feedback and apply targeted fix.",
            termination_reason=None,
        )

    return RoutingDecision(
        state=RouterState.CONTINUE,
        should_continue=True,
        reason="Normal execution cycle continuing.",
        termination_reason=None,
    )
