import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
from agents.worker import load_agent_md, build_worker_agent, MAX_AGENT_MD_CHARS, WORKER_SYSTEM_PROMPT


def test_agent_md_absent_returns_empty_string(tmp_path):
    assert load_agent_md(str(tmp_path)) == ""


def test_agent_md_empty_repo_path_returns_empty_string():
    assert load_agent_md("") == ""
    assert load_agent_md(None) == ""


def test_agent_md_present_is_injected(tmp_path):
    agent_md = tmp_path / "AGENT.md"
    agent_md.write_text("Always use 4 spaces and write comprehensive docstrings.", encoding="utf-8")

    notes = load_agent_md(str(tmp_path))
    assert "## Project-specific notes (from AGENT.md)" in notes
    assert "Always use 4 spaces and write comprehensive docstrings." in notes


def test_agent_md_oversized_is_truncated_with_warning(tmp_path, capsys):
    agent_md = tmp_path / "AGENT.md"
    large_content = "X" * (MAX_AGENT_MD_CHARS + 5000)
    agent_md.write_text(large_content, encoding="utf-8")

    notes = load_agent_md(str(tmp_path))
    captured = capsys.readouterr().out

    assert "Warning: AGENT.md in" in captured
    assert "truncated" in captured
    assert "[... AGENT.md truncated, 5000 characters omitted ...]" in notes
    # Check total length is bounded
    assert len(notes) < MAX_AGENT_MD_CHARS + 200


def test_build_worker_agent_injects_agent_md(tmp_path):
    agent_md = tmp_path / "AGENT.md"
    agent_md.write_text("Special convention: all classes must have a validate() method.", encoding="utf-8")

    with patch("agents.worker.create_deep_agent") as mock_create_agent, \
         patch("agents.worker._build_model") as mock_build_model:
        mock_build_model.return_value = MagicMock()

        build_worker_agent(repo_path=str(tmp_path))

        mock_create_agent.assert_called_once()
        _, kwargs = mock_create_agent.call_args
        system_prompt = kwargs.get("system_prompt", "")
        assert "## Project-specific notes (from AGENT.md)" in system_prompt
        assert "Special convention: all classes must have a validate() method." in system_prompt
