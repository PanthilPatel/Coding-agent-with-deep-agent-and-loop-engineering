"""Phase 6 tests: Evaluator, Router, and Loop integration.

Includes characterization tests written before refactoring, isolation tests for
evaluator and router, and full loop integration tests.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock, call

from config import Config
from controller.state import RunState, TerminationReason, load_state
from controller import loop

try:
    from controller.evaluator import evaluate_iteration, EvaluatorResult
    from controller.router import decide_next_step, RouterDecision
except ImportError:
    evaluate_iteration = None
    EvaluatorResult = None
    decide_next_step = None
    RouterDecision = None


class TestPhase6Characterization:
    @pytest.fixture
    def mock_config(self, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        state_file = repo_dir / "state.json"
        
        cfg = MagicMock(spec=Config)
        cfg.repo_path = str(repo_dir)
        cfg.local_repo_path = str(repo_dir)
        cfg.goal = "Fix the failing tests"
        cfg.test_cmd = "pytest"
        cfg.max_iterations = 3
        cfg.max_seconds = 100
        cfg.require_approval = False
        cfg.model_name = "test-model"
        cfg.state_file = "state.json"
        cfg.llm_provider = "ollama_cloud"
        cfg.is_remote = False
        cfg.lint_cmd = None
        cfg.skills_dir = None
        cfg.mcp_config_path = None
        return cfg

    @patch("controller.loop.ensure_work_branch")
    @patch("controller.loop.build_worker_agent")
    @patch("controller.loop.run_worker_turn")
    @patch("controller.loop.run_tests")
    @patch("controller.loop.commit_iteration")
    @patch("controller.loop.select_skill")
    def test_characterization_sequence_fail_fail_pass(
        self, mock_skill, mock_commit, mock_tests, mock_worker, mock_agent, mock_branch, mock_config
    ):
        mock_skill.return_value = None
        mock_worker.side_effect = ["Tried fix 1", "Tried fix 2", "Tried fix 3"]
        
        res1 = MagicMock(passed=False, returncode=1, output_tail="FAIL test_1")
        res2 = MagicMock(passed=False, returncode=1, output_tail="FAIL test_1")
        res3 = MagicMock(passed=True, returncode=0, output_tail="PASS test_1")
        mock_tests.side_effect = [res1, res2, res3]
        mock_commit.return_value = "commit_123"

        success = loop.run(mock_config)

        assert success is True
        assert mock_tests.call_count == 3
        
        state_path = os.path.join(mock_config.local_repo_path, mock_config.state_file)
        state = load_state(state_path, mock_config.goal)
        assert state.termination_reason == TerminationReason.SUCCESS
        assert len(state.iterations) == 3
        assert state.iterations[0]["test_passed"] is False
        assert state.iterations[1]["test_passed"] is False
        assert state.iterations[2]["test_passed"] is True

    @patch("controller.loop.ensure_work_branch")
    @patch("controller.loop.build_worker_agent")
    @patch("controller.loop.run_worker_turn")
    @patch("controller.loop.run_tests")
    @patch("controller.loop.commit_iteration")
    @patch("controller.loop.select_skill")
    def test_characterization_sequence_fail_until_max_iterations(
        self, mock_skill, mock_commit, mock_tests, mock_worker, mock_agent, mock_branch, mock_config
    ):
        mock_skill.return_value = None
        mock_worker.return_value = "Tried fix"
        mock_tests.return_value = MagicMock(passed=False, returncode=1, output_tail="FAIL test_1")
        mock_commit.return_value = "commit_123"

        success = loop.run(mock_config)

        assert success is False
        assert mock_tests.call_count == mock_config.max_iterations
        
        state_path = os.path.join(mock_config.local_repo_path, mock_config.state_file)
        state = load_state(state_path, mock_config.goal)
        assert state.termination_reason == TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT
        assert len(state.iterations) == mock_config.max_iterations

class TestEvaluatorUnit:
    def test_evaluator_all_pass(self):
        if evaluate_iteration is None:
            pytest.skip("controller.evaluator not imported yet")
        res = evaluate_iteration(
            test_passed=True,
            test_output_tail="",
            lint_passed=True,
            lint_output_tail="",
            same_failure_count=0,
        )
        assert res["is_correct"] is True
        assert res["score"] == 1.0
        assert res["issues"] == []
        assert res["critical_gaps"] == []
        assert "All verification checks passed" in res["feedback"]

    def test_evaluator_tests_passed_lint_failed(self):
        if evaluate_iteration is None:
            pytest.skip("controller.evaluator not imported yet")
        res = evaluate_iteration(
            test_passed=True,
            test_output_tail="",
            lint_passed=False,
            lint_output_tail="unused import x",
            same_failure_count=0,
        )
        assert res["is_correct"] is True
        assert res["score"] == 1.0
        assert "lint_failed" in res["issues"]
        assert res["critical_gaps"] == []
        assert "lint reported issues" in res["feedback"]


    def test_evaluator_tests_failed_first_time(self):
        if evaluate_iteration is None:
            pytest.skip("controller.evaluator not imported yet")
        res = evaluate_iteration(
            test_passed=False,
            test_output_tail="AssertionError: 1 != 2",
            lint_passed=None,
            lint_output_tail=None,
            same_failure_count=1,
        )
        assert res["is_correct"] is False
        assert res["score"] == 0.0
        assert "tests_failed" in res["issues"]
        assert "tests_must_pass" in res["critical_gaps"]

    def test_evaluator_repeated_failures_critical_gap(self):
        if evaluate_iteration is None:
            pytest.skip("controller.evaluator not imported yet")
        res = evaluate_iteration(
            test_passed=False,
            test_output_tail="AssertionError: 1 != 2",
            lint_passed=None,
            lint_output_tail=None,
            same_failure_count=3,
        )
        assert res["is_correct"] is False
        assert res["score"] == 0.0
        assert "tests_failed" in res["issues"]
        assert "repeated_failure" in res["issues"]
        assert "tests_must_pass" in res["critical_gaps"]
        assert "repeated_same_failure" in res["critical_gaps"]
        assert "Repeated failure (3 times)" in res["feedback"]

class TestRouterUnit:
    def test_router_success(self):
        if decide_next_step is None:
            pytest.skip("controller.router not imported yet")
        eval_res = {
            "is_correct": True,
            "score": 1.0,
            "issues": [],
            "critical_gaps": [],
            "feedback": "All passed",
        }
        dec = decide_next_step(
            evaluator_result=eval_res,
            current_iteration=1,
            max_iterations=5,
            elapsed_seconds=10.0,
            max_seconds=100.0,
            same_failure_count=0,
        )
        assert dec["continue"] is False
        assert dec["termination_reason"] == TerminationReason.SUCCESS

    def test_router_continue_on_failure(self):
        if decide_next_step is None:
            pytest.skip("controller.router not imported yet")
        eval_res = {
            "is_correct": False,
            "score": 0.0,
            "issues": ["tests_failed"],
            "critical_gaps": ["tests_must_pass"],
            "feedback": "Failed",
        }
        dec = decide_next_step(
            evaluator_result=eval_res,
            current_iteration=2,
            max_iterations=5,
            elapsed_seconds=20.0,
            max_seconds=100.0,
            same_failure_count=1,
        )
        assert dec["continue"] is True
        assert dec["termination_reason"] is None

    def test_router_max_iterations_reached(self):
        if decide_next_step is None:
            pytest.skip("controller.router not imported yet")
        eval_res = {
            "is_correct": False,
            "score": 0.0,
            "issues": ["tests_failed"],
            "critical_gaps": ["tests_must_pass"],
            "feedback": "Failed",
        }
        dec = decide_next_step(
            evaluator_result=eval_res,
            current_iteration=5,
            max_iterations=5,
            elapsed_seconds=50.0,
            max_seconds=100.0,
            same_failure_count=1,
        )
        assert dec["continue"] is False
        assert dec["termination_reason"] == TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT

    def test_router_timeout(self):
        if decide_next_step is None:
            pytest.skip("controller.router not imported yet")
        eval_res = {
            "is_correct": False,
            "score": 0.0,
            "issues": ["tests_failed"],
            "critical_gaps": ["tests_must_pass"],
            "feedback": "Failed",
        }
        dec = decide_next_step(
            evaluator_result=eval_res,
            current_iteration=2,
            max_iterations=5,
            elapsed_seconds=120.0,
            max_seconds=100.0,
            same_failure_count=1,
        )
        assert dec["continue"] is False
        assert dec["termination_reason"] == TerminationReason.TIMEOUT

    def test_router_repeated_failure_unrecoverable_threshold(self):
        if decide_next_step is None:
            pytest.skip("controller.router not imported yet")
        eval_res = {
            "is_correct": False,
            "score": 0.0,
            "issues": ["tests_failed", "repeated_failure"],
            "critical_gaps": ["tests_must_pass", "repeated_same_failure"],
            "feedback": "Repeated failure (4 times)",
        }
        dec = decide_next_step(
            evaluator_result=eval_res,
            current_iteration=4,
            max_iterations=10,
            elapsed_seconds=30.0,
            max_seconds=100.0,
            same_failure_count=4,
            max_same_failures=4,
        )
        assert dec["continue"] is False
        assert dec["termination_reason"] == TerminationReason.VERIFICATION_FAILED
