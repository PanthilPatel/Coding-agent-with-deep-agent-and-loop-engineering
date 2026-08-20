"""Unit tests for non-test-driven goal verification flow, goal-type classifier, and reviewer subagent."""

import os
import sys
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import Config
from orchestrator.planner import GoalPlanner
from controller.state import RunState, TerminationReason
from controller.loop import run, build_instruction
from agents.worker import review_diff, REVIEWER_SUBAGENT


def test_classify_goal_type():
    planner = GoalPlanner()
    
    test_driven_goals = [
        "fix failing unit tests",
        "make test_calculator.py pass",
        "fix bug in structures.py",
        "resolve pytest assertion errors",
        "debug crash in worker.py",
        "make all tests pass",
    ]
    for g in test_driven_goals:
        assert planner.classify_goal_type(g) == "test_driven", f"Expected test_driven for: {g}"

    non_test_driven_goals = [
        "add a size() method to the Stack class in structures.py",
        "refactor user authentication module",
        "explain how the router works",
        "add rate limiting to the API endpoints",
        "document the public API in README.md",
    ]
    for g in non_test_driven_goals:
        assert planner.classify_goal_type(g) == "non_test_driven", f"Expected non_test_driven for: {g}"


def test_build_instruction_conditional_pytest_rules():
    inst_test = build_instruction(
        goal="fix tests",
        last_output_tail="AssertionError: 1 != 2",
        force_new_strategy=False,
        last_returncode=1,
        is_test_driven=True,
    )
    assert "Pytest failure traceback:" in inst_test
    assert "NEVER edit test files." in inst_test

    inst_non_test = build_instruction(
        goal="add size method",
        last_output_tail="Reviewer rejected: missing docstring",
        force_new_strategy=False,
        last_returncode=1,
        is_test_driven=False,
    )
    assert "Previous verification feedback:" in inst_non_test
    assert "Pytest failure traceback:" not in inst_non_test
    assert "NEVER edit test files." not in inst_non_test


def test_review_diff_mocked():
    with patch("agents.worker._build_model") as mock_build:
        mock_model = MagicMock()
        mock_build.return_value = mock_model

        mock_model.invoke.return_value = MagicMock(content="APPROVE Clean implementation of size() method.")
        ok, reason = review_diff("diff --git a/structures.py", "add size method")
        assert ok is True
        assert "APPROVE" in reason

        mock_model.invoke.return_value = MagicMock(content="REJECT The size method raises an AttributeError.")
        ok, reason = review_diff("diff --git a/structures.py", "add size method")
        assert ok is False
        assert "REJECT" in reason


def test_non_test_driven_flow_approved_and_confirmed(tmp_path, capsys):
    cfg = Config(
        repo_path=str(tmp_path),
        goal="add a size() method to the Stack class in structures.py",
        max_iterations=3,
    )

    mock_agent = MagicMock()

    with patch("controller.loop.load_state", return_value=RunState(goal=cfg.goal)), \
         patch("controller.loop.save_state"), \
         patch("controller.loop.ensure_work_branch"), \
         patch("controller.loop.build_worker_agent", return_value=mock_agent), \
         patch("controller.loop.run_worker_turn", return_value="Added size() method."), \
         patch("controller.loop.get_diff", return_value="+    def size(self):\n+        return len(self._items)"), \
         patch("agents.worker.review_diff", return_value=(True, "APPROVE looks solid")), \
         patch("builtins.input", return_value="y"), \
         patch("controller.loop.commit_iteration", return_value="abc12345"):

        success = run(cfg)

        assert success is True
        captured = capsys.readouterr().out
        assert "[REVIEWER] Verdict: APPROVE — APPROVE looks solid" in captured
        assert "[PERMISSION] Code diff inspection for goal completion:" in captured
        assert "[DONE] Goal met after 1 iteration(s)." in captured


def test_non_test_driven_flow_rejected_by_human(tmp_path, capsys):
    cfg = Config(
        repo_path=str(tmp_path),
        goal="add a size() method to the Stack class in structures.py",
        max_iterations=3,
    )

    mock_agent = MagicMock()

    with patch("controller.loop.load_state", return_value=RunState(goal=cfg.goal)), \
         patch("controller.loop.save_state"), \
         patch("controller.loop.ensure_work_branch"), \
         patch("controller.loop.build_worker_agent", return_value=mock_agent), \
         patch("controller.loop.run_worker_turn", return_value="Added size() method."), \
         patch("controller.loop.get_diff", return_value="+    def size(self):\n+        return 0"), \
         patch("agents.worker.review_diff", return_value=(True, "APPROVE looks solid")), \
         patch("builtins.input", return_value="n"), \
         patch("controller.loop.commit_iteration", return_value="abc12345"):

        success = run(cfg)

        assert success is False
        captured = capsys.readouterr().out
        assert "[PERMISSION] Change rejected by user; stopping." in captured
