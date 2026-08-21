"""Tool registry for the coding agent.

This module collects all agent tools and exposes them in the format that
``deepagents.create_deep_agent(tools=...)`` expects
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

Phase 1 — General Tool System (terminal.py)
--------------------------------------------
``execute_command``, ``create_directory``, ``move_file``, ``delete_file``,
and ``list_directory`` are defined and exported below but are intentionally
**not** registered in ``build_tool_registry``.  They will be wired into the
live agent tool list once the Permission Harness (Phase 2/3) gate lands —
registering them without a confirmation gate would give the agent the ability
to execute and delete files with no user approval.
"""

import os
import re
from typing import Optional, List, Callable
from langchain_core.tools import tool as lc_tool, BaseTool

from controller.permissions import PermissionHarness, PermissionTier
from tools.exec_tools import make_run_tests_tool
from tools.git_tools import (
    make_git_commit_tool,
    make_git_diff_tool,
    make_git_log_tool,
    make_git_status_tool,
)

from tools.terminal import (  
    create_directory as _create_directory,
    delete_file as _delete_file,
    execute_command as _execute_command,
    list_directory as _list_directory,
    move_file as _move_file,
)

# Known destructive patterns to override misdeclared or underdeclared risk tiers
DESTRUCTIVE_COMMAND_PATTERNS = [
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b",
    r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r\b",
    r"\bdel\s+/[fF]\b",
    r"\brd\s+/[sS]\b",
    r"\bformat\b",
    r"\bgit\s+push\s+.*--force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bdrop\s+database\b",
    r"\bdrop\s+table\b",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # fork bomb
]


def _is_destructive_command(command: str) -> bool:
    """Check if command matches obviously destructive patterns."""
    for pattern in DESTRUCTIVE_COMMAND_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def build_tool_registry(
    repo_path: str,
    test_cmd: str,
    require_approval: bool = False,
    harness: Optional[PermissionHarness] = None,
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
        harness:          Optional PermissionHarness instance to gate tool execution.

    Returns:
        A list of LangChain ``BaseTool`` instances ready to pass to
        ``create_deep_agent(tools=...)``.
    """
    perm_harness = harness if harness is not None else PermissionHarness(interactive=False)

    # 1. Base / Git / Test tools
    tools: List[BaseTool] = [
        make_run_tests_tool(repo_path=repo_path, test_cmd=test_cmd),
        make_git_status_tool(repo_path=repo_path),
        make_git_diff_tool(repo_path=repo_path),
        make_git_log_tool(repo_path=repo_path),
        make_git_commit_tool(repo_path=repo_path, require_approval=require_approval),
    ]

    # 2. Phase 1 Terminal & Filesystem Tools guarded by PermissionHarness

    @lc_tool
    def execute_command(
        command: str,
        cwd: Optional[str] = None,
        timeout: int = 60,
        risk_tier: str = "auto",
    ) -> dict:
        """Run an arbitrary shell command and return its execution result.

        Args:
            command:   The shell command string to execute.
            cwd:       Optional working directory relative to repo or absolute.
            timeout:   Execution timeout in seconds (default 60).
            risk_tier: Declared risk level ('auto', 'confirm', or 'destructive').
        """
        # Safety net: force destructive tier if matching high-risk pattern
        effective_tier = "destructive" if _is_destructive_command(command) else risk_tier
        resolved_cwd = cwd or repo_path

        return perm_harness.execute_guarded(
            _execute_command,
            effective_tier,
            command=command,
            cwd=resolved_cwd,
            timeout=timeout,
            tool_name="execute_command",
        )

    @lc_tool
    def create_directory(path: str) -> dict:
        """Create a new directory and any missing parent directories (Tier: auto)."""
        resolved_path = path if os.path.isabs(path) else os.path.join(repo_path, path)
        return perm_harness.execute_guarded(
            _create_directory,
            "auto",
            path=resolved_path,
            tool_name="create_directory",
        )

    @lc_tool
    def move_file(source: str, destination: str) -> dict:
        """Move or rename a file or directory (Tier: confirm)."""
        res_src = source if os.path.isabs(source) else os.path.join(repo_path, source)
        res_dst = destination if os.path.isabs(destination) else os.path.join(repo_path, destination)
        return perm_harness.execute_guarded(
            _move_file,
            "confirm",
            source=res_src,
            destination=res_dst,
            tool_name="move_file",
        )

    @lc_tool
    def delete_file(path: str) -> dict:
        """Delete a file or directory tree (Tier: destructive)."""
        resolved_path = path if os.path.isabs(path) else os.path.join(repo_path, path)
        return perm_harness.execute_guarded(
            _delete_file,
            "destructive",
            path=resolved_path,
            tool_name="delete_file",
        )

    @lc_tool
    def list_directory(path: str = ".") -> dict:
        """List contents of a directory (Tier: auto)."""
        resolved_path = path if os.path.isabs(path) else os.path.join(repo_path, path)
        return perm_harness.execute_guarded(
            _list_directory,
            "auto",
            path=resolved_path,
            tool_name="list_directory",
        )

    tools.extend([
        execute_command,
        create_directory,
        move_file,
        delete_file,
        list_directory,
    ])

    return tools


def build_readonly_tool_registry(
    repo_path: str,
    harness: Optional[PermissionHarness] = None,
) -> list:
    """Build and return the minimal read-only tool list for chat-turn agents.

    Only includes tools that observe state and never mutate it:
      - list_directory  (browse repo structure)
      - git_status      (see what is staged/modified)
      - git_diff        (inspect current diff)
      - git_log         (review commit history)

    Explicitly excluded (all write / side-effect tools):
      run_tests, git_commit, execute_command, create_directory,
      move_file, delete_file.

    The backend (ReadonlyFilesystemBackend) separately blocks write_file,
    edit_file, and delete at the deepest layer. This registry exclusion is
    an additional defence-in-depth measure.

    Args:
        repo_path: Absolute path to the target repository.
        harness:   Optional PermissionHarness (read-only tools use 'auto' tier
                   so it is largely a no-op, but accepted for consistency).

    Returns:
        A list of LangChain BaseTool instances.
    """
    perm_harness = harness if harness is not None else PermissionHarness(interactive=False)

    @lc_tool
    def list_directory(path: str = ".") -> dict:
        """List contents of a directory (read-only, Tier: auto)."""
        resolved_path = path if os.path.isabs(path) else os.path.join(repo_path, path)
        return perm_harness.execute_guarded(
            _list_directory,
            "auto",
            path=resolved_path,
            tool_name="list_directory",
        )

    return [
        make_git_status_tool(repo_path=repo_path),
        make_git_diff_tool(repo_path=repo_path),
        make_git_log_tool(repo_path=repo_path),
        list_directory,
    ]


__all__ = [
    # Registry builders
    "build_tool_registry",
    "build_readonly_tool_registry",
    # Raw Phase 1 terminal tools
    "execute_command",
    "create_directory",
    "move_file",
    "delete_file",
    "list_directory",
]

