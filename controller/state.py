import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


class TerminationReason:
    """Plain string constants for recording why a run stopped.

    Using a plain class (not Enum) keeps ``asdict`` serialisation simple —
    the value is stored directly as a string in state.json, with no extra
    wrapping.
    """
    SUCCESS = "success"
    MAX_ITERATIONS_SAFETY_LIMIT = "max_iterations_safety_limit"
    TIMEOUT = "timeout"
    USER_REJECTED = "user_rejected"
    TOOL_ERROR = "tool_error"
    VERIFICATION_FAILED = "verification_failed"
    UNRECOVERABLE_ERROR = "unrecoverable_error"
    NO_CHANGES_MADE = "no_changes_made"


@dataclass
class IterationRecord:
    iteration: int
    timestamp: float
    instruction_summary: str
    worker_summary: str
    test_passed: bool
    test_output_tail: str
    # Phase 1 additions — all optional/defaulted so old state.json files load cleanly
    lint_passed: Optional[bool] = None
    lint_output_tail: Optional[str] = None


@dataclass
class RunState:
    goal: str
    started_at: float = field(default_factory=time.time)
    iterations: list = field(default_factory=list)
    last_failure_signature: Optional[str] = None
    same_failure_count: int = 0
    # Phase 1 additions — all optional/defaulted so old state.json files load cleanly
    plan: Optional[str] = None
    evaluator_result: Optional[dict] = None
    termination_reason: Optional[str] = None
    # Phase 3 addition
    selected_skill: Optional[str] = None
    # Phase 8 additions
    tool_calls_count: Optional[dict] = None
    audit_log: Optional[list] = None
    # Token usage tracking — accumulated across all worker turns in this run
    token_usage: Optional[dict] = None

    # ------------------------------------------------------------------
    # Existing methods — unchanged
    # ------------------------------------------------------------------

    def add_iteration(self, record: IterationRecord) -> None:
        self.iterations.append(asdict(record))

    def note_failure(self, signature: str) -> bool:
        """Update repeated-failure tracking. Returns True if this is the
        same failure as last time (signal to force a strategy change)."""
        if signature == self.last_failure_signature:
            self.same_failure_count += 1
        else:
            self.same_failure_count = 1
            self.last_failure_signature = signature
        return self.same_failure_count >= 2

    def note_success(self) -> None:
        self.last_failure_signature = None
        self.same_failure_count = 0

    # ------------------------------------------------------------------
    # Phase 1 setters
    # ------------------------------------------------------------------

    def set_plan(self, plan: str) -> None:
        """Record the instruction/goal context sent to the worker for the
        current iteration so it is inspectable in state.json."""
        self.plan = plan

    def set_evaluator_result(self, result: dict) -> None:
        """Store a structured snapshot of objective verification signals."""
        self.evaluator_result = result

    def set_termination_reason(self, reason: str) -> None:
        """Record why the run stopped (one of the TerminationReason constants)."""
        self.termination_reason = reason

    def set_skill(self, skill_name: Optional[str]) -> None:
        """Record the name of the skill selected for this run."""
        self.selected_skill = skill_name

    def set_tool_calls_count(self, counts: Optional[dict]) -> None:
        """Record the count of guarded tool calls executed."""
        self.tool_calls_count = counts

    def set_audit_log(self, audit_log: Optional[list]) -> None:
        """Record the audit log of guarded tool calls executed."""
        self.audit_log = audit_log

    def accumulate_token_usage(self, usage: dict) -> None:
        """Add per-turn token counts to the running run-level totals.

        Safe to call with an all-zero dict (no-op when total_tokens == 0).
        """
        if not usage or not any(usage.values()):
            return
        if self.token_usage is None:
            self.token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.token_usage[key] = self.token_usage.get(key, 0) + usage.get(key, 0)


# ------------------------------------------------------------------
# Persistence helpers
# ------------------------------------------------------------------

def build_evaluator_result(
    test_passed: bool,
    test_output_tail: str,
    lint_passed: Optional[bool],
    lint_output_tail: Optional[str],
) -> dict:
    """Build a structured evaluator-result dict from objective pass/fail signals.

    This is the Phase 1 lightweight proxy — derived purely from the existing
    ExecResult data; no LLM scoring involved.
    """
    issues = []
    critical_gaps = []

    if not test_passed:
        issues.append("tests_failed")
        critical_gaps.append("tests_must_pass")

    if lint_passed is False:
        issues.append("lint_failed")
        # lint failure is informational in Phase 1 — not a critical gap that
        # blocks success; the test result is the sole gate.

    if test_passed and not issues:
        feedback = "All verification checks passed."
    elif test_passed and issues:
        feedback = "Tests passed but lint reported issues."
    else:
        tail_preview = (test_output_tail or "")[:200]
        feedback = f"Tests failed. Output tail: {tail_preview}"

    return {
        "is_correct": test_passed,
        "score": 1.0 if test_passed else 0.0,
        "issues": issues,
        "critical_gaps": critical_gaps,
        "feedback": feedback,
    }


def load_state(path: str, goal: str) -> RunState:
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
        state = RunState(goal=data.get("goal", goal))
        state.started_at = data.get("started_at", time.time())
        state.iterations = data.get("iterations", [])
        state.last_failure_signature = data.get("last_failure_signature")
        state.same_failure_count = data.get("same_failure_count", 0)
        # Phase 1 new fields — safe .get() with None default so old JSON files
        # load without error
        state.plan = data.get("plan", None)
        state.evaluator_result = data.get("evaluator_result", None)
        state.termination_reason = data.get("termination_reason", None)
        # Phase 3 new field — safe .get() so old JSON files load cleanly
        state.selected_skill = data.get("selected_skill", None)
        # Phase 8 new fields — safe .get() so old JSON files load cleanly
        state.tool_calls_count = data.get("tool_calls_count", None)
        state.audit_log = data.get("audit_log", None)
        # Token usage tracking — safe .get() so old JSON files load cleanly
        state.token_usage = data.get("token_usage", None)
        return state
    return RunState(goal=goal)


def save_state(path: str, state: RunState) -> None:
    with open(path, "w") as f:
        json.dump(asdict(state), f, indent=2)
