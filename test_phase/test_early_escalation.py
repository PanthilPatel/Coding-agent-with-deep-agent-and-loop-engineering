"""Unit test for early escalation on recursion limit in controller/loop.py."""

import os
import sys
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import Config
from controller.state import RunState
from controller.evaluator import VerificationResult
from controller.loop import run


def test_early_escalation_on_recursion_limit(tmp_path, capsys):
    cfg = Config(
        repo_path=str(tmp_path),
        goal="fix something",
        max_iterations=3,
        test_cmd="pytest",
    )
    setattr(cfg, "verification_strategy", "test_suite")

    mock_agent_local = MagicMock(name="mock_agent_local")
    mock_agent_nvidia = MagicMock(name="mock_agent_nvidia")
    mock_agent_local.escalated_to_nvidia = False
    mock_agent_nvidia.escalated_to_nvidia = True

    with patch.dict(os.environ, {"NVIDIA_API_KEY": "fake_key"}), \
         patch("controller.loop.load_state", return_value=RunState(goal="fix something")), \
         patch("controller.loop.save_state"), \
         patch("controller.loop.ensure_work_branch"), \
         patch("controller.loop.build_worker_agent", side_effect=[mock_agent_local, mock_agent_nvidia]) as mock_build_agent, \
         patch("controller.loop.run_worker_turn", side_effect=[
             "Agent hit recursion limit of 30 steps due to repeating operations.",
             "File changes made successfully. Turn completed.",
         ]), \
         patch("controller.loop.GeneralEvaluator") as mock_eval_cls, \
         patch("controller.loop.commit_iteration", return_value="abc12345"):

        mock_eval_inst = MagicMock()
        mock_eval_inst.evaluate.side_effect = [
            VerificationResult(strategy="test_suite", passed=False, issues=["tests_failed"], evidence="AssertionError"),
            VerificationResult(strategy="test_suite", passed=True, issues=[], evidence="All tests passed"),
        ]
        mock_eval_cls.return_value = mock_eval_inst

        success = run(cfg)

        assert success is True
        assert mock_build_agent.call_count == 2
        calls = mock_build_agent.call_args_list
        assert "nvidia" in str(calls[1])

        captured = capsys.readouterr().out
        assert "Escalating to NVIDIA NIM" in captured
        assert "immediately after agent hit recursion limit" in captured
