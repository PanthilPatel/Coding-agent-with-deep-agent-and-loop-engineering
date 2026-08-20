"""Unit test for interactive session persistence, checkpointer, and thread_id support."""

import os
import sys
import threading
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import Config
from langgraph.checkpoint.memory import MemorySaver
from agents.worker import build_worker_agent, run_worker_turn, TerminalLogCallbackHandler
from cli.interactive import run_interactive


def test_build_worker_agent_accepts_checkpointer(tmp_path):
    checkpointer = MemorySaver()
    with patch("agents.worker.create_deep_agent") as mock_create_agent:
        mock_create_agent.return_value = MagicMock()
        agent = build_worker_agent(str(tmp_path), checkpointer=checkpointer)
        assert agent is not None
        mock_create_agent.assert_called_once()
        kwargs = mock_create_agent.call_args[1]
        assert kwargs.get("checkpointer") is checkpointer


def test_run_worker_turn_passes_thread_id_and_cancellation():
    mock_agent = MagicMock()
    mock_agent.invoke.return_value = {
        "messages": [MagicMock(content="Hello from persistent agent!")]
    }

    cancellation_event = threading.Event()
    res = run_worker_turn(
        agent=mock_agent,
        instruction="Hi there",
        thread_id="test_session_123",
        cancellation_event=cancellation_event,
    )

    assert res == "Hello from persistent agent!"
    mock_agent.invoke.assert_called_once()
    call_kwargs = mock_agent.invoke.call_args[1]
    cfg = call_kwargs.get("config", {})
    assert cfg.get("configurable", {}).get("thread_id") == "test_session_123"


def test_cancellation_interruption_handling():
    handler = TerminalLogCallbackHandler(cancellation_event=threading.Event())
    handler.cancellation_event.set()

    with pytest.raises(InterruptedError, match="Turn interrupted by user"):
        handler.on_tool_start({"name": "test_tool"}, "input")

    with pytest.raises(InterruptedError, match="Turn interrupted by user"):
        handler.on_llm_start({"name": "test_llm"}, ["prompt"])


def test_run_interactive_help_and_exit(tmp_path, capsys):
    cfg = Config(repo_path=str(tmp_path), goal="", model_name="test-model")

    mock_agent = MagicMock()

    with patch("cli.interactive.build_worker_agent", return_value=mock_agent), \
         patch("builtins.input", side_effect=["/help", "exit"]):

        run_interactive(cfg)

        captured = capsys.readouterr().out
        assert "Coding Agent (Session)" in captured
        assert "Available Commands:" in captured
        assert "/run [goal]" in captured
        assert "Exiting..." in captured


def test_run_interactive_single_turn_and_run_command(tmp_path, capsys):
    cfg = Config(repo_path=str(tmp_path), goal="", model_name="test-model")

    mock_agent = MagicMock()

    with patch("cli.interactive.build_worker_agent", return_value=mock_agent), \
         patch("cli.interactive.run_worker_turn", return_value="I inspected the repo."), \
         patch("cli.interactive.run_controller_loop", return_value=True) as mock_loop, \
         patch("builtins.input", side_effect=["what did you see?", "/run fix bugs", "exit"]):

        run_interactive(cfg)

        captured = capsys.readouterr().out
        assert "I inspected the repo." in captured
        assert "Starting autonomous fix loop for goal: 'fix bugs'..." in captured
        assert "Autonomous loop completed: SUCCESS" in captured
        assert mock_loop.call_count == 1
