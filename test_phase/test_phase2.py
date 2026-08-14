"""Phase 2 tests: tool system — git tools, run_tests_tool, registry.

All git and subprocess calls are mocked — no real LLM, no real git remote,
no network access.
"""

import os
import pytest
from unittest.mock import patch, MagicMock, call

from langchain_core.tools import BaseTool

from tools import build_tool_registry
from tools.base import log_tool_call, log_tool_result, safe_tool
from tools.git_tools import (
    make_git_status_tool,
    make_git_diff_tool,
    make_git_log_tool,
    make_git_commit_tool,
)
from tools.exec_tools import make_run_tests_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke(tool_fn, **kwargs):
    """Call a LangChain @tool by its underlying function directly."""
    return tool_fn.invoke(kwargs)


# ---------------------------------------------------------------------------
# 1. tools/base.py — safe_tool decorator
# ---------------------------------------------------------------------------

class TestSafeTool:
    def test_returns_value_on_success(self):
        @safe_tool
        def ok_func():
            return "all good"

        assert ok_func() == "all good"

    def test_catches_exception_and_returns_error_string(self):
        @safe_tool
        def bad_func():
            raise ValueError("something broke")

        result = bad_func()
        assert "ERROR" in result
        assert "ValueError" in result

    def test_preserves_function_name(self):
        @safe_tool
        def my_tool():
            return "ok"

        assert my_tool.__name__ == "my_tool"


# ---------------------------------------------------------------------------
# 2. git_status tool
# ---------------------------------------------------------------------------

class TestGitStatusTool:
    @patch("tools.git_tools.get_repo")
    def test_clean_repo_returns_clean_message(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = False
        mock_get_repo.return_value = mock_repo

        tool_fn = make_git_status_tool("/fake/repo")
        result = _invoke(tool_fn)

        assert "nothing to commit" in result

    @patch("tools.git_tools.get_repo")
    def test_dirty_repo_lists_modified_files(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = True
        modified = MagicMock()
        modified.a_path = "src/foo.py"
        mock_repo.index.diff.return_value = [modified]
        mock_repo.untracked_files = []
        mock_get_repo.return_value = mock_repo

        tool_fn = make_git_status_tool("/fake/repo")
        result = _invoke(tool_fn)

        assert "src/foo.py" in result

    @patch("tools.git_tools.get_repo")
    def test_git_error_returns_error_string(self, mock_get_repo):
        mock_get_repo.side_effect = Exception("not a git repo")

        tool_fn = make_git_status_tool("/fake/repo")
        result = _invoke(tool_fn)

        assert "ERROR" in result

    def test_is_langchain_base_tool(self):
        tool_fn = make_git_status_tool("/fake/repo")
        assert isinstance(tool_fn, BaseTool)

    def test_has_name_and_description(self):
        tool_fn = make_git_status_tool("/fake/repo")
        assert tool_fn.name
        assert tool_fn.description


# ---------------------------------------------------------------------------
# 3. git_diff tool
# ---------------------------------------------------------------------------

class TestGitDiffTool:
    @patch("tools.git_tools.get_diff")
    def test_returns_diff_output(self, mock_get_diff):
        mock_get_diff.return_value = "diff --git a/foo.py b/foo.py\n+added line"

        tool_fn = make_git_diff_tool("/fake/repo")
        result = _invoke(tool_fn)

        assert "+added line" in result
        mock_get_diff.assert_called_once_with("/fake/repo")

    @patch("tools.git_tools.get_diff")
    def test_clean_tree_returns_clean_message(self, mock_get_diff):
        mock_get_diff.return_value = ""

        tool_fn = make_git_diff_tool("/fake/repo")
        result = _invoke(tool_fn)

        assert "clean" in result.lower()

    @patch("tools.git_tools.get_diff")
    def test_git_error_returns_error_string(self, mock_get_diff):
        mock_get_diff.side_effect = RuntimeError("git failure")

        tool_fn = make_git_diff_tool("/fake/repo")
        result = _invoke(tool_fn)

        assert "ERROR" in result

    def test_is_langchain_base_tool(self):
        tool_fn = make_git_diff_tool("/fake/repo")
        assert isinstance(tool_fn, BaseTool)


# ---------------------------------------------------------------------------
# 4. git_log tool
# ---------------------------------------------------------------------------

class TestGitLogTool:
    @patch("tools.git_tools.get_repo")
    def test_returns_commit_lines(self, mock_get_repo):
        commit = MagicMock()
        commit.hexsha = "abcdef1234567890"
        commit.committed_datetime.strftime.return_value = "2024-01-01 12:00"
        commit.summary = "Fix the bug"
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = [commit]
        mock_get_repo.return_value = mock_repo

        tool_fn = make_git_log_tool("/fake/repo")
        result = _invoke(tool_fn, max_entries=5)

        assert "abcdef12" in result
        assert "Fix the bug" in result

    @patch("tools.git_tools.get_repo")
    def test_empty_repo_returns_no_commits_message(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = []
        mock_get_repo.return_value = mock_repo

        tool_fn = make_git_log_tool("/fake/repo")
        result = _invoke(tool_fn, max_entries=10)

        assert "no commits" in result

    @patch("tools.git_tools.get_repo")
    def test_caps_max_entries_at_50(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = []
        mock_get_repo.return_value = mock_repo

        tool_fn = make_git_log_tool("/fake/repo")
        _invoke(tool_fn, max_entries=999)

        # max_count passed to iter_commits should be capped at 50
        mock_repo.iter_commits.assert_called_once_with(max_count=50)

    @patch("tools.git_tools.get_repo")
    def test_git_error_returns_error_string(self, mock_get_repo):
        mock_get_repo.side_effect = Exception("git error")

        tool_fn = make_git_log_tool("/fake/repo")
        result = _invoke(tool_fn)

        assert "ERROR" in result

    def test_is_langchain_base_tool(self):
        assert isinstance(make_git_log_tool("/fake/repo"), BaseTool)


# ---------------------------------------------------------------------------
# 5. git_commit tool
# ---------------------------------------------------------------------------

class TestGitCommitTool:
    @patch("tools.git_tools.commit_iteration")
    def test_commits_when_approval_not_required(self, mock_commit):
        mock_commit.return_value = "deadbeef12345678"

        tool_fn = make_git_commit_tool("/fake/repo", require_approval=False)
        result = _invoke(tool_fn, message="fix: corrected off-by-one")

        assert "deadbeef" in result
        mock_commit.assert_called_once_with("/fake/repo", "fix: corrected off-by-one")

    @patch("tools.git_tools.commit_iteration")
    def test_nothing_to_commit_returns_clean_message(self, mock_commit):
        mock_commit.return_value = ""

        tool_fn = make_git_commit_tool("/fake/repo", require_approval=False)
        result = _invoke(tool_fn, message="empty commit")

        assert "Nothing to commit" in result

    def test_disabled_when_require_approval_true(self):
        """The commit tool must refuse to commit when require_approval=True
        so the agent cannot bypass the controller's approval gate."""
        tool_fn = make_git_commit_tool("/fake/repo", require_approval=True)
        result = _invoke(tool_fn, message="sneak commit")

        assert "ERROR" in result
        assert "require-approval" in result or "require_approval" in result

    @patch("tools.git_tools.commit_iteration")
    def test_commit_error_returns_error_string(self, mock_commit):
        mock_commit.side_effect = Exception("locked index")

        tool_fn = make_git_commit_tool("/fake/repo", require_approval=False)
        result = _invoke(tool_fn, message="failing commit")

        assert "ERROR" in result

    def test_is_langchain_base_tool(self):
        assert isinstance(make_git_commit_tool("/fake/repo", False), BaseTool)


# ---------------------------------------------------------------------------
# 6. run_tests_tool
# ---------------------------------------------------------------------------

class TestRunTestsTool:
    @patch("tools.exec_tools._run_tests")
    def test_passed_result_formatted_correctly(self, mock_run_tests):
        mock_run_tests.return_value = MagicMock(
            passed=True, returncode=0, output_tail="5 passed in 0.5s"
        )
        tool_fn = make_run_tests_tool("/fake/repo", "pytest")
        result = _invoke(tool_fn, extra_args="")

        assert "PASSED" in result
        assert "5 passed" in result
        mock_run_tests.assert_called_once_with("/fake/repo", "pytest")

    @patch("tools.exec_tools._run_tests")
    def test_failed_result_formatted_correctly(self, mock_run_tests):
        mock_run_tests.return_value = MagicMock(
            passed=False, returncode=1, output_tail="FAILED test_foo.py::test_bar"
        )
        tool_fn = make_run_tests_tool("/fake/repo", "pytest")
        result = _invoke(tool_fn, extra_args="")

        assert "FAILED" in result
        assert "test_bar" in result

    @patch("tools.exec_tools._run_tests")
    def test_extra_args_appended_to_command(self, mock_run_tests):
        mock_run_tests.return_value = MagicMock(
            passed=True, returncode=0, output_tail="1 passed"
        )
        tool_fn = make_run_tests_tool("/fake/repo", "pytest")
        _invoke(tool_fn, extra_args="-k test_specific")

        mock_run_tests.assert_called_once_with("/fake/repo", "pytest -k test_specific")

    @patch("tools.exec_tools._run_tests")
    def test_exception_returns_error_string(self, mock_run_tests):
        mock_run_tests.side_effect = RuntimeError("subprocess died")

        tool_fn = make_run_tests_tool("/fake/repo", "pytest")
        result = _invoke(tool_fn, extra_args="")

        assert "ERROR" in result

    def test_is_langchain_base_tool(self):
        assert isinstance(make_run_tests_tool("/fake/repo", "pytest"), BaseTool)

    def test_has_name_and_description(self):
        tool_fn = make_run_tests_tool("/fake/repo", "pytest")
        assert tool_fn.name
        assert tool_fn.description


# ---------------------------------------------------------------------------
# 7. Tool registry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_registry_returns_list(self):
        tools = build_tool_registry("/fake/repo", "pytest", require_approval=False)
        assert isinstance(tools, list)

    def test_registry_returns_expected_tool_count(self):
        tools = build_tool_registry("/fake/repo", "pytest")
        # run_tests_tool + git_status + git_diff + git_log + git_commit = 5
        assert len(tools) == 5

    def test_all_tools_are_langchain_basetools(self):
        tools = build_tool_registry("/fake/repo", "pytest")
        for t in tools:
            assert isinstance(t, BaseTool), f"{t} is not a BaseTool"

    def test_all_tools_have_name_and_description(self):
        tools = build_tool_registry("/fake/repo", "pytest")
        for t in tools:
            assert t.name, f"Tool missing name: {t}"
            assert t.description, f"Tool missing description: {t}"

    def test_expected_tool_names_present(self):
        tools = build_tool_registry("/fake/repo", "pytest")
        names = {t.name for t in tools}
        assert "run_tests_tool" in names
        assert "git_status" in names
        assert "git_diff" in names
        assert "git_log" in names
        assert "git_commit" in names

    def test_tool_names_are_unique(self):
        tools = build_tool_registry("/fake/repo", "pytest")
        names = [t.name for t in tools]
        assert len(names) == len(set(names)), "Duplicate tool names in registry"

    def test_registry_with_require_approval_false(self):
        """With require_approval=False, git_commit should work normally."""
        tools = build_tool_registry("/fake/repo", "pytest", require_approval=False)
        commit_tool = next(t for t in tools if t.name == "git_commit")
        assert commit_tool is not None

    @patch("tools.git_tools.commit_iteration")
    def test_git_commit_disabled_via_registry_when_approval_required(self, mock_commit):
        """With require_approval=True, git_commit from the registry returns
        an error without calling the underlying commit function."""
        tools = build_tool_registry("/fake/repo", "pytest", require_approval=True)
        commit_tool = next(t for t in tools if t.name == "git_commit")

        result = commit_tool.invoke({"message": "attempt commit"})

        assert "ERROR" in result
        mock_commit.assert_not_called()


# ---------------------------------------------------------------------------
# 8. build_worker_agent accepts extra_tools parameter
# ---------------------------------------------------------------------------

class TestBuildWorkerAgentToolsParam:
    @patch("agents.worker.create_deep_agent")
    @patch("agents.worker.FilesystemBackend")
    @patch("agents.worker._build_model")
    def test_extra_tools_passed_to_create_deep_agent(
        self, mock_model, mock_backend, mock_create
    ):
        """build_worker_agent must forward extra_tools to create_deep_agent
        as the tools= keyword argument."""
        mock_tool = MagicMock(spec=BaseTool)
        mock_create.return_value = MagicMock()

        from agents.worker import build_worker_agent
        build_worker_agent("/fake/repo", extra_tools=[mock_tool])

        call_kwargs = mock_create.call_args.kwargs
        assert "tools" in call_kwargs
        assert mock_tool in call_kwargs["tools"]

    @patch("agents.worker.create_deep_agent")
    @patch("agents.worker.FilesystemBackend")
    @patch("agents.worker._build_model")
    def test_no_extra_tools_passes_none(
        self, mock_model, mock_backend, mock_create
    ):
        """When extra_tools is None (default), tools= should be None."""
        mock_create.return_value = MagicMock()

        from agents.worker import build_worker_agent
        build_worker_agent("/fake/repo")

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs.get("tools") is None
