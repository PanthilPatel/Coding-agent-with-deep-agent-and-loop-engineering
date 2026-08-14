"""Phase 1 tests: new state fields, backward-compat load, TerminationReason,
evaluator_result builder, and loop.run() termination scenarios (all external
calls mocked — no real LLM, no real git, no real subprocess)."""

import json
import os
import time
import pytest
from unittest.mock import patch, MagicMock, call

from controller.state import (
    IterationRecord,
    RunState,
    TerminationReason,
    build_evaluator_result,
    load_state,
    save_state,
)

class TestRunStateNewFields:
    def test_plan_defaults_to_none(self):
        state = RunState(goal="test")
        assert state.plan is None

    def test_evaluator_result_defaults_to_none(self):
        state = RunState(goal="test")
        assert state.evaluator_result is None

    def test_termination_reason_defaults_to_none(self):
        state = RunState(goal="test")
        assert state.termination_reason is None

    def test_set_plan(self):
        state = RunState(goal="test")
        state.set_plan("Goal: make all tests pass")
        assert state.plan == "Goal: make all tests pass"

    def test_set_evaluator_result(self):
        state = RunState(goal="test")
        ev = {"is_correct": True, "score": 1.0, "issues": [], "critical_gaps": [], "feedback": "ok"}
        state.set_evaluator_result(ev)
        assert state.evaluator_result == ev

    def test_set_termination_reason(self):
        state = RunState(goal="test")
        state.set_termination_reason(TerminationReason.SUCCESS)
        assert state.termination_reason == "success"

class TestIterationRecordNewFields:
    def test_lint_passed_defaults_to_none(self):
        rec = IterationRecord(
            iteration=1,
            timestamp=time.time(),
            instruction_summary="inst",
            worker_summary="work",
            test_passed=True,
            test_output_tail="",
        )
        assert rec.lint_passed is None

    def test_lint_output_tail_defaults_to_none(self):
        rec = IterationRecord(
            iteration=1,
            timestamp=time.time(),
            instruction_summary="inst",
            worker_summary="work",
            test_passed=True,
            test_output_tail="",
        )
        assert rec.lint_output_tail is None

    def test_lint_fields_round_trip(self):
        rec = IterationRecord(
            iteration=2,
            timestamp=time.time(),
            instruction_summary="inst",
            worker_summary="work",
            test_passed=False,
            test_output_tail="fail",
            lint_passed=False,
            lint_output_tail="E501 line too long",
        )
        assert rec.lint_passed is False
        assert rec.lint_output_tail == "E501 line too long"


class TestLoadStateBackwardCompat:
    def test_old_format_loads_new_fields_as_none(self, tmp_path):
        """An old state.json without the Phase 1 keys must load with those
        fields defaulting to None (not raise KeyError/AttributeError)."""
        old_data = {
            "goal": "fix tests",
            "started_at": 1000.0,
            "iterations": [],
            "last_failure_signature": None,
            "same_failure_count": 0,
          
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(old_data))

        state = load_state(str(state_file), "fix tests")

        assert state.plan is None
        assert state.evaluator_result is None
        assert state.termination_reason is None
        assert state.goal == "fix tests"
        assert state.same_failure_count == 0

    def test_new_format_round_trips_correctly(self, tmp_path):
        """Save a RunState with new fields set, reload it, and verify every
        field comes back with the correct value."""
        state_file = tmp_path / "state.json"
        state = RunState(goal="round-trip goal")
        state.set_plan("Goal: round-trip goal")
        state.set_evaluator_result({"is_correct": True, "score": 1.0, "issues": [], "critical_gaps": [], "feedback": "ok"})
        state.set_termination_reason(TerminationReason.SUCCESS)

        rec = IterationRecord(
            iteration=1,
            timestamp=123.0,
            instruction_summary="inst",
            worker_summary="work",
            test_passed=True,
            test_output_tail="all pass",
            lint_passed=True,
            lint_output_tail="",
        )
        state.add_iteration(rec)
        save_state(str(state_file), state)

        loaded = load_state(str(state_file), "round-trip goal")
        assert loaded.plan == "Goal: round-trip goal"
        assert loaded.evaluator_result["is_correct"] is True
        assert loaded.termination_reason == "success"
        assert len(loaded.iterations) == 1
        assert loaded.iterations[0]["lint_passed"] is True

class TestTerminationReasonConstants:
    def test_success(self):
        assert TerminationReason.SUCCESS == "success"

    def test_max_iterations_safety_limit(self):
        assert TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT == "max_iterations_safety_limit"

    def test_timeout(self):
        assert TerminationReason.TIMEOUT == "timeout"

    def test_user_rejected(self):
        assert TerminationReason.USER_REJECTED == "user_rejected"

    def test_tool_error(self):
        assert TerminationReason.TOOL_ERROR == "tool_error"

    def test_verification_failed(self):
        assert TerminationReason.VERIFICATION_FAILED == "verification_failed"

    def test_unrecoverable_error(self):
        assert TerminationReason.UNRECOVERABLE_ERROR == "unrecoverable_error"

class TestBuildEvaluatorResult:
    def test_tests_pass_no_lint(self):
        ev = build_evaluator_result(
            test_passed=True,
            test_output_tail="5 passed",
            lint_passed=None,
            lint_output_tail=None,
        )
        assert ev["is_correct"] is True
        assert ev["score"] == 1.0
        assert ev["issues"] == []
        assert ev["critical_gaps"] == []

    def test_tests_fail(self):
        ev = build_evaluator_result(
            test_passed=False,
            test_output_tail="FAILED test_foo",
            lint_passed=None,
            lint_output_tail=None,
        )
        assert ev["is_correct"] is False
        assert ev["score"] == 0.0
        assert "tests_failed" in ev["issues"]
        assert "tests_must_pass" in ev["critical_gaps"]

    def test_tests_pass_lint_fails(self):
        """Lint failure is recorded in issues but does not flip is_correct —
        test passage is the sole success gate in Phase 1."""
        ev = build_evaluator_result(
            test_passed=True,
            test_output_tail="5 passed",
            lint_passed=False,
            lint_output_tail="E501 line too long",
        )
        assert ev["is_correct"] is True   # tests passed → still correct
        assert ev["score"] == 1.0
        assert "lint_failed" in ev["issues"]
        assert "tests_must_pass" not in ev["critical_gaps"]

    def test_tests_fail_lint_also_fails(self):
        ev = build_evaluator_result(
            test_passed=False,
            test_output_tail="FAILED",
            lint_passed=False,
            lint_output_tail="E501",
        )
        assert ev["is_correct"] is False
        assert "tests_failed" in ev["issues"]
        assert "lint_failed" in ev["issues"]

    def test_tests_pass_lint_passes(self):
        ev = build_evaluator_result(
            test_passed=True,
            test_output_tail="5 passed",
            lint_passed=True,
            lint_output_tail="",
        )
        assert ev["is_correct"] is True
        assert ev["issues"] == []


def _make_config(tmp_path, *, max_iterations=3, max_seconds=3600,
                 require_approval=False, lint_cmd=None):
    """Build a minimal mock Config for use in loop tests."""
    cfg = MagicMock()
    cfg.is_remote = False
    cfg.local_repo_path = str(tmp_path)
    cfg.state_file = "state.json"
    cfg.goal = "make tests pass"
    cfg.test_cmd = "pytest"
    cfg.max_iterations = max_iterations
    cfg.max_seconds = max_seconds
    cfg.require_approval = require_approval
    cfg.model_name = "gemma4"
    cfg.llm_provider = "ollama_cloud"
    cfg.lint_cmd = lint_cmd
    return cfg


@patch("controller.loop.build_worker_agent")
@patch("controller.loop.run_worker_turn")
@patch("controller.loop.run_tests")
@patch("controller.loop.commit_iteration")
@patch("controller.loop.ensure_work_branch")
class TestLoopTerminationReasons:

    def test_success_sets_termination_reason(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        mock_worker.return_value = "Fixed the bug."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="1 passed")
        mock_commit.return_value = "abc12345"

        from controller.loop import run
        cfg = _make_config(tmp_path)
        result = run(cfg)

        assert result is True
        state_file = tmp_path / "state.json"
        with open(state_file) as f:
            data = json.load(f)
        assert data["termination_reason"] == "success"

    def test_max_iterations_sets_termination_reason(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        mock_worker.return_value = "Tried something."
        mock_tests.return_value = MagicMock(passed=False, returncode=1, output_tail="FAILED")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, max_iterations=2)
        result = run(cfg)

        assert result is False
        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data["termination_reason"] == "max_iterations_safety_limit"

    def test_user_rejected_sets_termination_reason(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        mock_worker.return_value = "Made a change."
        mock_tests.return_value = MagicMock(passed=False, returncode=1, output_tail="FAILED")
        mock_commit.return_value = ""

        from controller.loop import run, get_diff
        with patch("controller.loop.get_diff", return_value="diff --git ..."), \
             patch("builtins.input", return_value="n"):
            cfg = _make_config(tmp_path, require_approval=True)
            result = run(cfg)

        assert result is False
        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data["termination_reason"] == "user_rejected"

    def test_tool_error_sets_termination_reason(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        """A RuntimeError during run_worker_turn should result in tool_error."""
        mock_worker.side_effect = RuntimeError("LLM connection failed")

        from controller.loop import run
        cfg = _make_config(tmp_path)
        result = run(cfg)

        assert result is False
        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data["termination_reason"] == "tool_error"

    def test_plan_is_recorded_in_state(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        """state.plan should hold the instruction sent to the worker."""
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path)
        run(cfg)

        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data["plan"] is not None
        assert "make tests pass" in data["plan"]

    def test_evaluator_result_is_recorded_in_state(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path)
        run(cfg)

        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data["evaluator_result"] is not None
        assert data["evaluator_result"]["is_correct"] is True

@patch("controller.loop.build_worker_agent")
@patch("controller.loop.run_worker_turn")
@patch("controller.loop.run_tests")
@patch("controller.loop.run_lint")
@patch("controller.loop.commit_iteration")
@patch("controller.loop.ensure_work_branch")
class TestLoopLintIntegration:

    def test_lint_called_when_configured(
        self, mock_branch, mock_commit, mock_lint, mock_tests, mock_worker, mock_build, tmp_path
    ):
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_lint.return_value = MagicMock(passed=True, returncode=0, output_tail="")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, lint_cmd="flake8 .")
        run(cfg)

        mock_lint.assert_called_once()

    def test_lint_not_called_when_not_configured(
        self, mock_branch, mock_commit, mock_lint, mock_tests, mock_worker, mock_build, tmp_path
    ):
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, lint_cmd=None)
        run(cfg)

        mock_lint.assert_not_called()

    def test_lint_fields_in_iteration_record(
        self, mock_branch, mock_commit, mock_lint, mock_tests, mock_worker, mock_build, tmp_path
    ):
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_lint.return_value = MagicMock(passed=False, returncode=1, output_tail="E501 too long")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, lint_cmd="flake8 .")
        run(cfg)

        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        iteration = data["iterations"][0]
        assert iteration["lint_passed"] is False
        assert "E501" in iteration["lint_output_tail"]

    def test_lint_fail_does_not_block_success(
        self, mock_branch, mock_commit, mock_lint, mock_tests, mock_worker, mock_build, tmp_path
    ):
        """Lint failure must never block the loop from returning True when
        tests pass — lint is informational only in Phase 1."""
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_lint.return_value = MagicMock(passed=False, returncode=1, output_tail="lint errors")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, lint_cmd="flake8 .")
        result = run(cfg)

        assert result is True

    def test_lint_fields_none_when_not_configured(
        self, mock_branch, mock_commit, mock_lint, mock_tests, mock_worker, mock_build, tmp_path
    ):
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, lint_cmd=None)
        run(cfg)

        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        iteration = data["iterations"][0]
        assert iteration["lint_passed"] is None
        assert iteration["lint_output_tail"] is None
