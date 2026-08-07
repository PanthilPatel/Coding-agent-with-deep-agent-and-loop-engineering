import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class IterationRecord:
    iteration: int
    timestamp: float
    instruction_summary: str
    worker_summary: str
    test_passed: bool
    test_output_tail: str


@dataclass
class RunState:
    goal: str
    started_at: float = field(default_factory=time.time)
    iterations: list = field(default_factory=list)
    last_failure_signature: Optional[str] = None
    same_failure_count: int = 0

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


def load_state(path: str, goal: str) -> RunState:
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
        state = RunState(goal=data.get("goal", goal))
        state.started_at = data.get("started_at", time.time())
        state.iterations = data.get("iterations", [])
        state.last_failure_signature = data.get("last_failure_signature")
        state.same_failure_count = data.get("same_failure_count", 0)
        return state
    return RunState(goal=goal)


def save_state(path: str, state: RunState) -> None:
    with open(path, "w") as f:
        json.dump(asdict(state), f, indent=2)
