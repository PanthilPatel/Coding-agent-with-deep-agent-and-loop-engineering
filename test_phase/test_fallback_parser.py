"""Unit tests for fallback tool-call parsing in PatchedChatOllama."""

import json
import pytest
from langchain_core.messages import AIMessage
from agents.worker import PatchedChatOllama


@pytest.fixture
def offline_model():
    """Create a PatchedChatOllama instance without initializing Ollama connection."""
    return PatchedChatOllama.__new__(PatchedChatOllama)


def test_fallback_parser_closed_fence(offline_model):
    args = {
        "file_path": "structures.py",
        "old_string": "return self._items.pop()",
        "new_string": "return self._items.pop(0)",
    }
    payload = {"name": "edit_file", "arguments": args}
    content = f"```json\n{json.dumps(payload)}\n```"

    msg = AIMessage(content=content, tool_calls=[])
    result = offline_model._parse_fallback_tool_calls(msg)

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["name"] == "edit_file"
    assert tc["args"]["file_path"] == "structures.py"
    assert tc["args"]["old_string"] == args["old_string"]
    assert tc["args"]["new_string"] == args["new_string"]


def test_fallback_parser_unclosed_fence(offline_model):
    args = {
        "file_path": "structures.py",
        "old_string": "return self._items.pop()",
        "new_string": "return self._items.pop(0)",
    }
    payload = {"name": "edit_file", "arguments": args}
    content = f"```json\n{json.dumps(payload)}"

    msg = AIMessage(content=content, tool_calls=[])
    result = offline_model._parse_fallback_tool_calls(msg)

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["name"] == "edit_file"
    assert tc["args"]["file_path"] == "structures.py"
    assert tc["args"]["old_string"] == args["old_string"]
    assert tc["args"]["new_string"] == args["new_string"]


def test_fallback_parser_no_fence_with_prose(offline_model):
    args = {
        "file_path": "structures.py",
        "old_string": "return self._items.pop()",
        "new_string": "return self._items.pop(0)",
    }
    payload = {"name": "edit_file", "arguments": args}
    content = f"I will now fix the bug in structures.py:\n{json.dumps(payload)}\nThis completes the edit."

    msg = AIMessage(content=content, tool_calls=[])
    result = offline_model._parse_fallback_tool_calls(msg)

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["name"] == "edit_file"
    assert tc["args"]["file_path"] == "structures.py"
    assert tc["args"]["old_string"] == args["old_string"]
    assert tc["args"]["new_string"] == args["new_string"]
