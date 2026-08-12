"""Tool registry for the coding agent.

This module collects all Phase 2 tools and exposes them as a list in the
format that ``deepagents.create_deep_agent(tools=...)`` expects
(``Sequence[BaseTool | Callable | dict]``).

Usage::

    from tools import build_tool_registry

    tools = build_tool_registry(
        repo_path="/path/to/repo",
        test_cmd="pytest",
        require_approval=False,
    )
    agent = create_deep_agent(model=model, tools=tools, backend=backend, ...)

Note on overlapping capabilities
---------------------------------
``FilesystemBackend`` (deepagents 0.7.5, verified) already exposes the
following tools to the agent via ``FilesystemMiddleware``:

    ls, read_file, write_file, edit_file, delete, glob, grep, execute

- ``grep`` covers regex/text search across the repo (ripgrep-backed).
- ``execute`` covers arbitrary shell command execution.

Therefore this registry deliberately does NOT add ``search_code`` or
``run_command`` — they would duplicate built-in capabilities and give the
agent two competing interfaces for the same operations.

The tools added here are for capabilities genuinely absent from the backend:
- Structured test results (``run_tests_tool``)
- Git inspection and commit operations (``git_status``, ``git_diff``,
  ``git_log``, ``git_commit``)
"""

from tools.exec_tools import make_run_tests_tool
from tools.git_tools import (
    make_git_commit_tool,
    make_git_diff_tool,
    make_git_log_tool,
    make_git_status_tool,
)


def build_tool_registry(
    repo_path: str,
    test_cmd: str,
    require_approval: bool = False,
) -> list:
    """Build and return the list of agent tools for the given configuration.

    All tools are bound to ``repo_path`` at construction time so they always
    operate within the configured repository root.

    Args:
        repo_path:        Absolute path to the target repository.
        test_cmd:         The test command used by the project (e.g. ``pytest``).
        require_approval: When True, ``git_commit`` is disabled (returns an
                          error string) so the agent cannot commit around the
                          controller's user-approval gate.

    Returns:
        A list of LangChain ``BaseTool`` instances ready to pass to
        ``create_deep_agent(tools=...)``.
    """
    return [
        make_run_tests_tool(repo_path=repo_path, test_cmd=test_cmd),
        make_git_status_tool(repo_path=repo_path),
        make_git_diff_tool(repo_path=repo_path),
        make_git_log_tool(repo_path=repo_path),
        make_git_commit_tool(repo_path=repo_path, require_approval=require_approval),
    ]


__all__ = ["build_tool_registry"]
