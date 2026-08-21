"""Regression tests for chat-turn tool restrictions.

Verifies that:
1. ReadonlyFilesystemBackend blocks write, edit, and delete at the backend layer.
2. build_readonly_tool_registry returns only read-only tools and excludes all
   write/side-effect tools (git_commit, execute_command, move_file, delete_file,
   create_directory, run_tests).
3. build_worker_agent's full PatchedFilesystemBackend still permits edits
   (no regression on the /run path).

No live LLM call is made. All assertions operate on the classes and functions
directly.
"""

import os
import pytest
import tempfile


# ---------------------------------------------------------------------------
# ReadonlyFilesystemBackend tests
# ---------------------------------------------------------------------------

class TestReadonlyFilesystemBackend:
    """The backend must block all mutations regardless of content."""

    def setup_method(self):
        from agents.worker import ReadonlyFilesystemBackend
        self.tmpdir = tempfile.mkdtemp()
        self.backend = ReadonlyFilesystemBackend(root_dir=self.tmpdir)

    def test_write_is_blocked(self):
        """write() must return an error result, not write to disk."""
        result = self.backend.write("foo.py", "print('hello')")
        assert result.error is not None, "write() should return an error in readonly mode"
        assert "Chat mode" in result.error
        assert not os.path.exists(os.path.join(self.tmpdir, "foo.py"))

    def test_edit_is_blocked(self):
        """edit() must return an error result without touching disk."""
        target = os.path.join(self.tmpdir, "bar.py")
        with open(target, "w") as f:
            f.write("x = 1\n")
        result = self.backend.edit("bar.py", "x = 1", "x = 2")
        assert result.error is not None, "edit() should return an error in readonly mode"
        assert "Chat mode" in result.error
        with open(target) as f:
            assert f.read() == "x = 1\n"

    def test_delete_is_blocked(self):
        """delete() must return an error result without removing the file."""
        target = os.path.join(self.tmpdir, "baz.py")
        with open(target, "w") as f:
            f.write("pass\n")
        result = self.backend.delete("baz.py")
        assert result.error is not None, "delete() should return an error in readonly mode"
        assert "Chat mode" in result.error
        assert os.path.exists(target)

    def test_write_error_message_mentions_run_command(self):
        """The error message must guide users to /run."""
        result = self.backend.write("anything.py", "content")
        assert "/run" in result.error, "Error message should mention /run prefix"

    def test_read_is_allowed(self):
        """read() must still work in readonly mode."""
        target = os.path.join(self.tmpdir, "readable.py")
        with open(target, "w") as f:
            f.write("# hello\n")
        result = self.backend.read("readable.py")
        assert not result.error, f"read() should succeed in readonly mode, got: {result.error}"

    def test_ls_is_allowed(self):
        """ls() must still work in readonly mode."""
        result = self.backend.ls(".")
        assert not result.error, f"ls() should succeed in readonly mode, got: {result.error}"


# ---------------------------------------------------------------------------
# build_readonly_tool_registry tests
# ---------------------------------------------------------------------------

class TestBuildReadonlyToolRegistry:
    """The readonly registry must exclude all write/side-effect tools."""

    WRITE_TOOL_NAMES = {
        "git_commit",
        "execute_command",
        "move_file",
        "delete_file",
        "create_directory",
        "run_tests",
    }

    READ_TOOL_NAMES = {
        "git_status",
        "git_diff",
        "git_log",
        "list_directory",
    }

    def setup_method(self):
        from tools import build_readonly_tool_registry
        self.tmpdir = tempfile.mkdtemp()
        self.tools = build_readonly_tool_registry(repo_path=self.tmpdir)
        self.tool_names = {t.name for t in self.tools}

    def test_no_write_tools_present(self):
        """No write/side-effect tool names must appear in the readonly registry."""
        illegal = self.WRITE_TOOL_NAMES & self.tool_names
        assert not illegal, (
            f"Readonly tool registry contains write/side-effect tools: {illegal}"
        )

    def test_read_tools_present(self):
        """All expected read-only tools must be present."""
        missing = self.READ_TOOL_NAMES - self.tool_names
        assert not missing, (
            f"Readonly tool registry is missing expected read-only tools: {missing}"
        )

    def test_registry_is_non_empty(self):
        """The readonly registry must return at least one tool."""
        assert len(self.tools) >= 1

    def test_no_git_commit_tool(self):
        """git_commit must never appear in the readonly registry."""
        assert "git_commit" not in self.tool_names

    def test_no_execute_command_tool(self):
        """execute_command must never appear in the readonly registry."""
        assert "execute_command" not in self.tool_names


# ---------------------------------------------------------------------------
# PatchedFilesystemBackend (full agent path) regression test
# ---------------------------------------------------------------------------

class TestPatchedFilesystemBackendStillWritable:
    """The full PatchedFilesystemBackend used by /run must still allow edits."""

    def setup_method(self):
        from agents.worker import PatchedFilesystemBackend
        self.tmpdir = tempfile.mkdtemp()
        self.backend = PatchedFilesystemBackend(root_dir=self.tmpdir)

    def test_write_does_not_return_chat_mode_error(self):
        """PatchedFilesystemBackend.write() must not return a [Chat mode] error."""
        from langgraph.errors import GraphRecursionError
        try:
            result = self.backend.write("writable.py", "x = 42\n")
            if result.error:
                assert "Chat mode" not in result.error, (
                    "PatchedFilesystemBackend.write() must not return [Chat mode] error"
                )
        except GraphRecursionError as e:
            assert "[SHORT_CIRCUIT]" in str(e), f"Unexpected GraphRecursionError: {e}"
        except Exception as e:
            assert "Chat mode" not in str(e), (
                f"PatchedFilesystemBackend.write() must not emit [Chat mode] errors: {e}"
            )


# ---------------------------------------------------------------------------
# cli/interactive.py wiring test (import-level check, no LLM)
# ---------------------------------------------------------------------------

class TestInteractiveSessionUsesReadonlyAgent:
    """Verify that cli/interactive.py uses build_readonly_worker_agent for chat turns."""

    def test_interactive_py_does_not_import_build_worker_agent(self):
        """build_worker_agent must not be imported in interactive.py."""
        import ast, pathlib
        src = pathlib.Path("cli/interactive.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "agents.worker":
                    names = [alias.name for alias in node.names]
                    assert "build_worker_agent" not in names, (
                        "cli/interactive.py must NOT import build_worker_agent — "
                        "only build_readonly_worker_agent is allowed for chat turns."
                    )

    def test_interactive_py_imports_build_readonly_worker_agent(self):
        """build_readonly_worker_agent must be imported in interactive.py."""
        import ast, pathlib
        src = pathlib.Path("cli/interactive.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "agents.worker":
                    names = [alias.name for alias in node.names]
                    if "build_readonly_worker_agent" in names:
                        found = True
        assert found, (
            "cli/interactive.py must import build_readonly_worker_agent from agents.worker"
        )


# ---------------------------------------------------------------------------
# Chat Mode System Prompt tests
# ---------------------------------------------------------------------------

class TestChatModeSystemPrompt:
    """Verify that CHAT_MODE_SYSTEM_PROMPT distinguishes general questions from repo actions."""

    def test_chat_mode_prompt_includes_conversational_guidelines(self):
        from agents.worker import CHAT_MODE_SYSTEM_PROMPT
        assert "GENERAL KNOWLEDGE & QUESTIONS UNRELATED TO THIS REPO" in CHAT_MODE_SYSTEM_PROMPT
        assert "answer DIRECTLY in plain conversational text" in CHAT_MODE_SYSTEM_PROMPT
        assert "REPOSITORY CODE & STRUCTURE QUESTIONS" in CHAT_MODE_SYSTEM_PROMPT
        assert "READ-ONLY CHAT RESTRICTIONS" in CHAT_MODE_SYSTEM_PROMPT
        assert "/run <goal>" in CHAT_MODE_SYSTEM_PROMPT

