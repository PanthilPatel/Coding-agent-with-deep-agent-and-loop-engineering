"""Git tools: git_status, git_diff, git_log, git_commit.

All tools are thin wrappers around the existing ``utils/git_utils.py``
functions — no new git logic is introduced here.

Safety:
- ``git_commit`` is gated: it may only be used when ``require_approval`` is
  False in the active run.  The tool accepts an explicit ``allow_commit``
  flag that the registry sets at build time so the agent cannot bypass the
  user's approval gate.
- All tools catch git exceptions and return an error string rather than
  propagating into the agent loop.
"""

from langchain_core.tools import tool

from tools.base import log_tool_call, log_tool_result, safe_tool
from utils.git_utils import get_repo, get_diff, commit_iteration


# ---------------------------------------------------------------------------
# git_status
# ---------------------------------------------------------------------------

def make_git_status_tool(repo_path: str):
    """Factory that returns a git_status tool bound to a specific repo_path."""

    @tool
    def git_status() -> str:
        """Return the current git status of the repository, showing which
        files are modified, staged, or untracked."""
        log_tool_call("git_status")
        try:
            repo = get_repo(repo_path)
            status_lines = []
            if repo.is_dirty(untracked_files=True):
                for item in repo.index.diff(None):
                    status_lines.append(f"  modified: {item.a_path}")
                for item in repo.untracked_files:
                    status_lines.append(f"  untracked: {item}")
                for item in repo.index.diff("HEAD"):
                    status_lines.append(f"  staged:   {item.a_path}")
            result = "\n".join(status_lines) if status_lines else "nothing to commit, working tree clean"
            log_tool_result("OK")
            return result
        except Exception as exc:
            error = f"ERROR: {type(exc).__name__}: {exc}"
            log_tool_result(f"ERROR {type(exc).__name__}")
            return error

    return git_status


# ---------------------------------------------------------------------------
# git_diff
# ---------------------------------------------------------------------------

def make_git_diff_tool(repo_path: str):
    """Factory that returns a git_diff tool bound to a specific repo_path."""

    @tool
    def git_diff() -> str:
        """Return the full unified diff of all unstaged changes in the
        repository (equivalent to ``git diff``)."""
        log_tool_call("git_diff")
        try:
            diff = get_diff(repo_path)
            result = diff if diff.strip() else "(no diff — working tree is clean)"
            log_tool_result("OK")
            return result
        except Exception as exc:
            error = f"ERROR: {type(exc).__name__}: {exc}"
            log_tool_result(f"ERROR {type(exc).__name__}")
            return error

    return git_diff


# ---------------------------------------------------------------------------
# git_log
# ---------------------------------------------------------------------------

def make_git_log_tool(repo_path: str):
    """Factory that returns a git_log tool bound to a specific repo_path."""

    @tool
    def git_log(max_entries: int = 10) -> str:
        """Return the last N commit log entries (default 10) from the current
        branch.

        Args:
            max_entries: Maximum number of log entries to return (1-50).
        """
        log_tool_call("git_log")
        try:
            max_entries = max(1, min(int(max_entries), 50))
            repo = get_repo(repo_path)
            entries = []
            for commit in repo.iter_commits(max_count=max_entries):
                entries.append(
                    f"{commit.hexsha[:8]}  {commit.committed_datetime.strftime('%Y-%m-%d %H:%M')}  {commit.summary}"
                )
            result = "\n".join(entries) if entries else "(no commits found)"
            log_tool_result("OK")
            return result
        except Exception as exc:
            error = f"ERROR: {type(exc).__name__}: {exc}"
            log_tool_result(f"ERROR {type(exc).__name__}")
            return error

    return git_log


# ---------------------------------------------------------------------------
# git_commit  (optional — only safe when require_approval=False)
# ---------------------------------------------------------------------------

def make_git_commit_tool(repo_path: str, require_approval: bool):
    """Factory that returns a git_commit tool.

    When ``require_approval`` is True the returned tool always returns an
    error message explaining it is disabled — the agent cannot bypass the
    user's approval gate.
    """

    @tool
    def git_commit(message: str) -> str:
        """Stage all changes and create a git commit with the given message.

        This tool is disabled when --require-approval is active; in that case
        commits are handled by the controller after user confirmation.

        Args:
            message: The commit message to use (required).
        """
        log_tool_call("git_commit")
        if require_approval:
            msg = "ERROR: git_commit is disabled when --require-approval is active. The controller handles commits after user confirmation."
            log_tool_result("DISABLED require_approval=True")
            return msg
        try:
            hexsha = commit_iteration(repo_path, message)
            if hexsha:
                result = f"Committed {hexsha[:8]}: {message}"
                log_tool_result(f"OK hash={hexsha[:8]}")
            else:
                result = "Nothing to commit — working tree was clean."
                log_tool_result("OK nothing_to_commit")
            return result
        except Exception as exc:
            error = f"ERROR: {type(exc).__name__}: {exc}"
            log_tool_result(f"ERROR {type(exc).__name__}")
            return error

    return git_commit
