"""Tests for new search, safe execution, and plan management tools."""

import os
import tempfile
import pytest

from tools.search_and_exec_tools import (
    make_grep_tool,
    make_safe_run_command_tool,
    make_update_plan_tool,
    is_command_safe,
    get_current_plan,
    reset_current_plan,
)

class TestSearchAndExecTools:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        reset_current_plan()

    def test_grep_finds_literal_matches(self):
        f1 = os.path.join(self.tmpdir, "structures.py")
        with open(f1, "w", encoding="utf-8") as f:
            f.write("class Queue:\n    def enqueue(self):\n        pass\n")
        f2 = os.path.join(self.tmpdir, "helper.py")
        with open(f2, "w", encoding="utf-8") as f:
            f.write("# helper module\n")

        grep_tool = make_grep_tool(self.tmpdir)
        res = grep_tool.invoke({"pattern": "class Queue"})
        assert "structures.py:1: class Queue:" in res
        assert "helper.py" not in res

    def test_grep_supports_regex(self):
        f1 = os.path.join(self.tmpdir, "structures.py")
        with open(f1, "w", encoding="utf-8") as f:
            f.write("def foo_123():\n    return 42\n")

        grep_tool = make_grep_tool(self.tmpdir)
        res = grep_tool.invoke({"pattern": r"def\s+foo_\d+", "is_regex": True})
        assert "structures.py:1: def foo_123():" in res

    def test_grep_returns_no_matches(self):
        grep_tool = make_grep_tool(self.tmpdir)
        res = grep_tool.invoke({"pattern": "nonexistent_symbol_xyz"})
        assert "No matches found" in res

    def test_is_command_safe_allowlist(self):
        assert is_command_safe("git status") is True
        assert is_command_safe("git log -1") is True
        assert is_command_safe("git diff structures.py") is True
        assert is_command_safe("pytest") is True
        assert is_command_safe("ls -la") is True
        assert is_command_safe("dir") is True
        assert is_command_safe("pwd") is True

        # Blocked commands
        assert is_command_safe("rm -rf .") is False
        assert is_command_safe("pip install requests") is False
        assert is_command_safe("curl http://example.com") is False
        assert is_command_safe("git status; rm -rf .") is False
        assert is_command_safe("git status | grep foo") is False

    def test_safe_run_command_executes_allowlisted(self):
        tool = make_safe_run_command_tool(self.tmpdir)
        res = tool.invoke({"command": "git status"})
        assert "Exit code" in res or "fatal" in res or "not a git repository" in res

    def test_safe_run_command_rejects_unsafe(self):
        tool = make_safe_run_command_tool(self.tmpdir)
        res = tool.invoke({"command": "rm -rf ."})
        assert "Error: Command 'rm -rf .' is not permitted in safe mode." in res

    def test_update_plan_maintains_and_formats_steps(self):
        tool = make_update_plan_tool()
        steps = [
            {"task": "Search code", "status": "done"},
            {"task": "Edit structures.py", "status": "in_progress"},
            {"task": "Run tests", "status": "pending"}
        ]
        res = tool.invoke({"steps": steps})
        assert "[X] 1. Search code (done)" in res
        assert "[>] 2. Edit structures.py (in_progress)" in res
        assert "[ ] 3. Run tests (pending)" in res

        current = get_current_plan()
        assert len(current) == 3
        assert current[0]["status"] == "done"
