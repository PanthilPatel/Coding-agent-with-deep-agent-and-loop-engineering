"""Terminal harness tools — Phase 1: General Tool System.

Provides:
- execute_command     : Run arbitrary shell commands, return structured dict.
- create_directory    : Create a directory (and parents).
- move_file           : Move / rename a file or directory.
- delete_file         : Delete a file or directory tree.
- list_directory      : List the immediate contents of a directory.

Design notes
------------
- Each function accepts a ``risk_tier`` parameter ("auto", "confirm", or
  "destructive") recording the caller's declared intent.  The value is stored
  as-is in the returned dict and is NOT enforced here — enforcement belongs to
  the Permission Harness landing in a later phase.  The parameter exists now
  so callers already express intent and the function signature will not change.
- These tools are deliberately NOT registered in build_tool_registry().
  They will be wired in once the Permission Harness gate is in place.
- All exceptions are caught; failures surface as structured dicts with
  status="error"/"exception"/"timeout" rather than propagating to the caller.
"""

import os
import shutil
import subprocess
import time
from typing import Optional

from tools.base import log_tool_call, log_tool_result


# ---------------------------------------------------------------------------
# execute_command
# ---------------------------------------------------------------------------

class FormattedCommandResult(dict):
    """Subclass of dict that formats string representations with combined stdout and stderr."""
    def __str__(self) -> str:
        code = self.get("exit_code", -1)
        stdout = self.get("stdout", "")
        stderr = self.get("stderr", "")
        status = self.get("status", "error")
        status_str = "SUCCESS" if status == "success" else "FAILED"
        return f"{status_str} (exit code {code}):\n{stdout}\n{stderr}"

    def __repr__(self) -> str:
        return self.__str__()


def execute_command(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 60,
    risk_tier: str = "auto",
) -> dict:
    """Run *command* in a subprocess and return a structured result dict.

    Args:
        command:   Shell command to execute.
        cwd:       Working directory for the subprocess (defaults to CWD).
        timeout:   Max seconds to wait; on expiry status becomes "timeout".
        risk_tier: Caller's declared intent. One of "auto", "confirm",
                   "destructive". Stored as-is; not enforced in this phase.

    Returns:
        {
            "command"           : str   — the command that was executed,
            "working_directory" : str   — resolved cwd used for execution,
            "risk_tier"         : str   — as passed by the caller,
            "stdout"            : str   — captured stdout,
            "stderr"            : str   — captured stderr,
            "exit_code"         : int   — process exit code (-1 on error),
            "execution_time"    : float — wall-clock seconds,
            "status"            : str   — "success", "error", "timeout", or
                                         "exception",
        }
    """
    log_tool_call("execute_command")

    resolved_cwd = os.path.abspath(cwd) if cwd else os.getcwd()
    result: dict = {
        "command": command,
        "working_directory": resolved_cwd,
        "risk_tier": risk_tier,
        "stdout": "",
        "stderr": "",
        "exit_code": -1,
        "execution_time": 0.0,
        "status": "exception",
    }

    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=resolved_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.monotonic() - start
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["exit_code"] = proc.returncode
        result["execution_time"] = round(elapsed, 4)
        result["status"] = "success" if proc.returncode == 0 else "error"
        log_tool_result(f"exit_code={proc.returncode} status={result['status']}")

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        result["stderr"] = f"TimeoutExpired: command exceeded {timeout}s limit."
        result["execution_time"] = round(elapsed, 4)
        result["status"] = "timeout"
        log_tool_result(f"TIMEOUT after {timeout}s")

    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - start
        result["stderr"] = f"{type(exc).__name__}: {exc}"
        result["execution_time"] = round(elapsed, 4)
        result["status"] = "exception"
        log_tool_result(f"EXCEPTION {type(exc).__name__}")

    return FormattedCommandResult(result)


# ---------------------------------------------------------------------------
# Filesystem helper tools
# ---------------------------------------------------------------------------

def create_directory(
    path: str,
    risk_tier: str = "auto",
) -> dict:
    """Create *path* and any missing parent directories.

    Args:
        path:      Directory path to create.
        risk_tier: Caller's declared intent (default "auto").

    Returns:
        {"status": "success"|"error", "path": str, "risk_tier": str,
         "message": str}
    """
    log_tool_call("create_directory")
    result = {"path": path, "risk_tier": risk_tier, "status": "error", "message": ""}
    try:
        os.makedirs(path, exist_ok=True)
        result["status"] = "success"
        result["message"] = f"Directory created (or already existed): {path}"
        log_tool_result(f"OK path={path}")
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"{type(exc).__name__}: {exc}"
        log_tool_result(f"ERROR {type(exc).__name__}")
    return result


def move_file(
    source: str,
    destination: str,
    risk_tier: str = "confirm",
) -> dict:
    """Move or rename *source* to *destination*.

    Args:
        source:      Source path (file or directory).
        destination: Destination path.
        risk_tier:   Caller's declared intent (default "confirm").

    Returns:
        {"status": "success"|"error", "source": str, "destination": str,
         "risk_tier": str, "message": str}
    """
    log_tool_call("move_file")
    result = {
        "source": source,
        "destination": destination,
        "risk_tier": risk_tier,
        "status": "error",
        "message": "",
    }
    try:
        shutil.move(source, destination)
        result["status"] = "success"
        result["message"] = f"Moved '{source}' to '{destination}'"
        log_tool_result(f"OK src={source} dst={destination}")
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"{type(exc).__name__}: {exc}"
        log_tool_result(f"ERROR {type(exc).__name__}")
    return result


def delete_file(
    path: str,
    risk_tier: str = "destructive",
) -> dict:
    """Delete the file or directory tree at *path*.

    Directories are removed recursively.  Non-existent paths are a no-op.

    Args:
        path:      File or directory path to delete.
        risk_tier: Caller's declared intent (default "destructive").

    Returns:
        {"status": "success"|"error", "path": str, "risk_tier": str,
         "message": str}
    """
    log_tool_call("delete_file")
    result = {"path": path, "risk_tier": risk_tier, "status": "error", "message": ""}
    try:
        if not os.path.exists(path):
            result["status"] = "success"
            result["message"] = f"Path does not exist (nothing to delete): {path}"
            log_tool_result("OK (already_absent)")
            return result
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        result["status"] = "success"
        result["message"] = f"Deleted: {path}"
        log_tool_result(f"OK path={path}")
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"{type(exc).__name__}: {exc}"
        log_tool_result(f"ERROR {type(exc).__name__}")
    return result


def list_directory(
    path: str,
    risk_tier: str = "auto",
) -> dict:
    """List the immediate children of the directory at *path*.

    Args:
        path:      Directory to list.
        risk_tier: Caller's declared intent (default "auto").

    Returns:
        {"status": "success"|"error", "path": str, "risk_tier": str,
         "entries": list[dict], "message": str}

        Each entry in ``entries``:
            {"name": str, "type": "file"|"directory", "size": int|None}
    """
    log_tool_call("list_directory")
    result: dict = {
        "path": path,
        "risk_tier": risk_tier,
        "status": "error",
        "entries": [],
        "message": "",
    }
    try:
        if not os.path.isdir(path):
            result["message"] = f"Not a directory: {path}"
            log_tool_result(f"ERROR not_a_directory path={path}")
            return result
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if os.path.isdir(full):
                entries.append({"name": name, "type": "directory", "size": None})
            else:
                entries.append({"name": name, "type": "file", "size": os.path.getsize(full)})
        result["status"] = "success"
        result["entries"] = entries
        result["message"] = f"{len(entries)} entries in '{path}'"
        log_tool_result(f"OK entries={len(entries)}")
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"{type(exc).__name__}: {exc}"
        log_tool_result(f"ERROR {type(exc).__name__}")
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "execute_command",
    "create_directory",
    "move_file",
    "delete_file",
    "list_directory",
]
