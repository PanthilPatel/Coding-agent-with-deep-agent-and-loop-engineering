"""Search, safe execution, and plan management tools.

Provides:
- make_grep_tool: Fast cross-file text and regex search with result capping.
- make_safe_run_command_tool: Restricted execution for safe, read-only inspection commands.
- make_update_plan_tool: Structured todo/plan step tracking with terminal visibility.
"""

import os
import re
import fnmatch
import subprocess
from typing import Optional, List, Dict, Any
from langchain_core.tools import tool

from tools.base import log_tool_call, log_tool_result


# ---------------------------------------------------------------------------
# Tool 1: Grep / Cross-File Search
# ---------------------------------------------------------------------------

def make_grep_tool(repo_path: str, max_results: int = 50):
    """Factory that returns a cross-file grep search tool scoped to repo_path."""

    @tool
    def grep(pattern: str, path: str = ".", glob: Optional[str] = None, is_regex: bool = False) -> str:
        """Search file contents across the repository for a text pattern or regular expression.

        Args:
            pattern: The text string or regular expression to search for.
            path: Subdirectory or relative file path to limit search scope (defaults to repo root ".").
            glob: Optional file pattern filter (e.g. "*.py", "*.json", "*structures*").
            is_regex: Whether to treat pattern as a regular expression (default False for literal match).

        Returns:
            List of matching files, line numbers, and matching line content (capped at 50 results).
        """
        log_tool_call(f"grep: {pattern}")
        target_dir = os.path.abspath(os.path.join(repo_path, path.lstrip("/\\")))
        if not os.path.exists(target_dir):
            error = f"Error: Target path '{path}' does not exist."
            log_tool_result("PATH_NOT_FOUND")
            return error

        matches = []
        try:
            regex = re.compile(pattern if is_regex else re.escape(pattern), re.IGNORECASE)
        except Exception as e:
            error = f"Error: Invalid regular expression '{pattern}': {e}"
            log_tool_result("INVALID_REGEX")
            return error

        # Excluded directories
        excluded_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".gemini"}

        if os.path.isfile(target_dir):
            files_to_scan = [target_dir]
        else:
            files_to_scan = []
            for root, dirs, files in os.walk(target_dir):
                dirs[:] = [d for d in dirs if d not in excluded_dirs]
                for file in files:
                    if glob and not fnmatch.fnmatch(file, glob):
                        continue
                    files_to_scan.append(os.path.join(root, file))

        for fpath in files_to_scan:
            if len(matches) >= max_results:
                break
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            rel_path = os.path.relpath(fpath, repo_path).replace("\\", "/")
                            clean_line = line.rstrip("\r\n")
                            # Truncate very long lines to avoid flooding context
                            if len(clean_line) > 200:
                                clean_line = clean_line[:200] + "..."
                            matches.append(f"{rel_path}:{line_num}: {clean_line}")
                            if len(matches) >= max_results:
                                break
            except Exception:
                continue

        if not matches:
            res = f"No matches found for '{pattern}'."
            log_tool_result("NO_MATCHES")
            return res

        truncated_note = f"\n[Showing first {max_results} matches]" if len(matches) >= max_results else ""
        res = "\n".join(matches) + truncated_note
        log_tool_result(f"FOUND {len(matches)} matches")
        return res

    return grep


# ---------------------------------------------------------------------------
# Tool 2: Safe run_command (Restricted Allowlist)
# ---------------------------------------------------------------------------

SAFE_COMMAND_PREFIXES = (
    "git status",
    "git diff",
    "git log",
    "git branch",
    "git show",
    "pytest",
    "python -m pytest",
    "ls",
    "dir",
    "pwd",
)

def is_command_safe(cmd_str: str) -> bool:
    """Validate whether a command string matches the safe read-only allowlist."""
    stripped = cmd_str.strip()
    if not stripped:
        return False
    # Forbid command chaining / piping / redirection operators that could bypass the allowlist
    if any(sep in stripped for sep in [";", "&&", "||", "|", ">", "<", "`", "$("]):
        return False
    
    # Check against allowlist prefixes
    lower = stripped.lower()
    for prefix in SAFE_COMMAND_PREFIXES:
        if lower == prefix or lower.startswith(prefix + " "):
            return True
    return False


def make_safe_run_command_tool(repo_path: str):
    """Factory that returns a restricted run_command tool for safe read-only inspection."""

    @tool
    def run_command(command: str) -> str:
        """Execute a safe, read-only inspection shell command in the repository.

        Permitted commands (ALLOWLIST ONLY):
          - git status
          - git diff
          - git log
          - git branch
          - git show
          - pytest / python -m pytest
          - ls / dir
          - pwd

        All other shell commands (e.g. rm, pip, curl, write ops, chaining operators) are strictly rejected.

        Args:
            command: The command string to execute.
        """
        log_tool_call(f"run_command: {command}")
        if not is_command_safe(command):
            allowed_list = ", ".join(SAFE_COMMAND_PREFIXES)
            msg = (
                f"Error: Command '{command}' is not permitted in safe mode. "
                f"Only read-only inspection commands are allowed: [{allowed_list}]. "
                "Arbitrary shell commands and mutating commands are strictly blocked."
            )
            log_tool_result("BLOCKED_NOT_ON_ALLOWLIST")
            return msg

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
            )
            output = proc.stdout
            if proc.stderr:
                output = f"{output}\n{proc.stderr}" if output else proc.stderr
            out_str = output.strip() or "(no output)"
            log_tool_result(f"exit_code={proc.returncode}")
            return f"Exit code {proc.returncode}:\n{out_str}"
        except subprocess.TimeoutExpired:
            log_tool_result("TIMEOUT")
            return "Error: Command execution timed out after 60s."
        except Exception as e:
            log_tool_result(f"EXCEPTION {e}")
            return f"Error executing command: {e}"

    return run_command


# ---------------------------------------------------------------------------
# Tool 3: Plan / Todo Tracker (update_plan)
# ---------------------------------------------------------------------------

_current_session_plan: List[Dict[str, str]] = []

def get_current_plan() -> List[Dict[str, str]]:
    """Return the active session plan items."""
    return list(_current_session_plan)

def reset_current_plan() -> None:
    """Clear the active session plan."""
    global _current_session_plan
    _current_session_plan = []


def make_update_plan_tool():
    """Factory that returns an update_plan tool for tracking multi-step tasks."""

    @tool
    def update_plan(steps: List[Dict[str, str]]) -> str:
        """Update and display the task execution plan / todo list for the current goal.

        Args:
            steps: List of task items, each having 'task' (description) and 'status' ('pending', 'in_progress', 'done').
                   Example: [
                       {"task": "Search for failing test definition", "status": "done"},
                       {"task": "Inspect structures.py implementation", "status": "in_progress"},
                       {"task": "Apply fix to dequeue method", "status": "pending"},
                       {"task": "Verify tests pass", "status": "pending"}
                   ]
        """
        global _current_session_plan
        log_tool_call("update_plan")

        valid_statuses = {"pending", "in_progress", "done"}
        normalized = []

        for step in steps:
            if not isinstance(step, dict):
                continue
            task_desc = str(step.get("task") or step.get("description") or "").strip()
            status = str(step.get("status") or "pending").lower().strip()
            if status not in valid_statuses:
                status = "pending"
            if task_desc:
                normalized.append({"task": task_desc, "status": status})

        _current_session_plan = normalized

        # Terminal representation
        status_icons = {
            "done": "[X]",
            "in_progress": "[>]",
            "pending": "[ ]"
        }

        print("\n=== CURRENT EXECUTION PLAN ===")
        lines = []
        for idx, item in enumerate(_current_session_plan, 1):
            icon = status_icons.get(item["status"], "[ ]")
            plan_line = f"  {icon} {idx}. {item['task']} ({item['status']})"
            print(plan_line)
            lines.append(plan_line)
        print("==============================\n")

        log_tool_result(f"PLAN_UPDATED ({len(_current_session_plan)} steps)")
        return "Plan updated successfully:\n" + "\n".join(lines)

    return update_plan
