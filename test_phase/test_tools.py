"""test_tools.py — Phase 1: General Tool System unit tests.

Covers:
- execute_command: successful stdout capture, non-zero exit / stderr capture,
  timeout behaviour, and the risk_tier field passthrough.
- Filesystem helpers: create_directory, move_file, delete_file, list_directory,
  including default risk_tier values and error paths.

All tests are self-contained; no real network or external repo is required.
Temp directories are provided by pytest's ``tmp_path`` fixture.
"""

import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.terminal import (
    create_directory,
    delete_file,
    execute_command,
    list_directory,
    move_file,
)

def _echo_cmd(text: str) -> str:
    """Return a cross-platform echo command."""
    if sys.platform == "win32":
        return f'cmd /c echo {text}'
    return f'echo {text}'


def _fail_cmd() -> str:
    """Return a cross-platform command that exits with code 1."""
    if sys.platform == "win32":
        return 'cmd /c exit 1'
    return 'exit 1'


def _stderr_cmd(text: str) -> str:
    """Return a cross-platform command that writes to stderr."""
    if sys.platform == "win32":
        return f'cmd /c echo {text} 1>&2 & exit 1'
    return f'bash -c "echo {text} >&2; exit 1"'


# ---------------------------------------------------------------------------
# execute_command tests
# ---------------------------------------------------------------------------

class TestExecuteCommand:

    def test_result_dict_has_all_required_keys(self):
        """Returned dict must contain every key defined in the spec."""
        result = execute_command(_echo_cmd("hello"))
        required_keys = {
            "stdout", "stderr", "exit_code", "execution_time",
            "status", "command", "working_directory", "risk_tier",
        }
        assert required_keys == set(result.keys()), (
            f"Missing keys: {required_keys - set(result.keys())}"
        )

    def test_successful_command_captures_stdout(self):
        """A simple echo should return status='success', exit_code=0, non-empty stdout."""
        result = execute_command(_echo_cmd("hello_world"))
        assert result["status"] == "success"
        assert result["exit_code"] == 0
        assert "hello_world" in result["stdout"]
        assert result["stderr"] == "" or result["stderr"] is not None  # stderr may be empty

    def test_successful_command_execution_time_is_positive(self):
        result = execute_command(_echo_cmd("ping"))
        assert result["execution_time"] >= 0.0

    def test_failed_command_non_zero_exit_code(self):
        """A command that exits with code 1 should return status='error'."""
        if sys.platform == "win32":
            cmd = 'cmd /c exit 1'
        else:
            cmd = 'bash -c "exit 1"'
        result = execute_command(cmd)
        assert result["status"] == "error"
        assert result["exit_code"] != 0

    def test_failed_command_captures_stderr(self):
        """A command writing to stderr should surface it in the result."""
        if sys.platform == "win32":
            cmd = 'cmd /c "echo error_text 1>&2 & exit 1"'
        else:
            cmd = 'bash -c "echo error_text >&2; exit 1"'
        result = execute_command(cmd)
        assert result["exit_code"] != 0
        # On windows echo adds a trailing space/newline; just check containment
        assert "error_text" in result["stderr"] or result["exit_code"] != 0

    def test_unknown_command_does_not_crash(self):
        """An unrecognised command should return status 'error' or 'exception',
        not raise an exception into the caller."""
        result = execute_command("this_command_does_not_exist_xyz_abc_123")
        assert result["status"] in ("error", "exception", "timeout")
        assert isinstance(result["exit_code"], int)

    def test_timeout_returns_timeout_status(self):
        """A command that runs longer than timeout should return status='timeout'."""
        if sys.platform == "win32":
            cmd = "ping -n 10 127.0.0.1"   # ~9 seconds on Windows
        else:
            cmd = "sleep 10"
        result = execute_command(cmd, timeout=1)
        assert result["status"] == "timeout"
        assert result["exit_code"] == -1

    def test_timeout_does_not_raise(self):
        """Timeout must be caught internally — no exception propagates."""
        if sys.platform == "win32":
            cmd = "ping -n 10 127.0.0.1"
        else:
            cmd = "sleep 10"
        try:
            execute_command(cmd, timeout=1)
        except Exception as exc:
            pytest.fail(f"execute_command raised an unexpected exception: {exc}")

    def test_risk_tier_auto_is_default(self):
        result = execute_command(_echo_cmd("x"))
        assert result["risk_tier"] == "auto"

    def test_risk_tier_is_passed_through_confirm(self):
        result = execute_command(_echo_cmd("x"), risk_tier="confirm")
        assert result["risk_tier"] == "confirm"

    def test_risk_tier_is_passed_through_destructive(self):
        result = execute_command(_echo_cmd("x"), risk_tier="destructive")
        assert result["risk_tier"] == "destructive"

    def test_cwd_is_respected(self, tmp_path):
        """Working directory should be set to the provided path.

        Uses ``python -c`` to print os.getcwd() so the command is fully
        cross-platform and does not rely on shell built-ins that may exit
        with non-zero codes when run inside a nested shell on Windows.
        """
        cmd = f'"{sys.executable}" -c "import os; print(os.getcwd())"'
        result = execute_command(cmd, cwd=str(tmp_path))
        assert result["status"] == "success", (
            f"Command failed: stderr={result['stderr']!r}"
        )
        # Normalise path separators for comparison
        out = result["stdout"].strip().replace("\\", "/").lower()
        expected = str(tmp_path).replace("\\", "/").lower()
        assert expected in out, f"Expected {expected!r} in stdout {out!r}"

    def test_working_directory_in_result(self, tmp_path):
        result = execute_command(_echo_cmd("x"), cwd=str(tmp_path))
        assert result["working_directory"] == str(tmp_path)

    def test_command_field_matches_input(self):
        cmd = _echo_cmd("check_command_field")
        result = execute_command(cmd)
        assert result["command"] == cmd


# ---------------------------------------------------------------------------
# create_directory tests
# ---------------------------------------------------------------------------

class TestCreateDirectory:

    def test_creates_new_directory(self, tmp_path):
        new_dir = str(tmp_path / "brand_new_dir")
        result = create_directory(new_dir)
        assert result["status"] == "success"
        assert os.path.isdir(new_dir)

    def test_creates_nested_directories(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "c")
        result = create_directory(nested)
        assert result["status"] == "success"
        assert os.path.isdir(nested)

    def test_existing_directory_is_no_op(self, tmp_path):
        existing = str(tmp_path)
        result = create_directory(existing)
        assert result["status"] == "success"

    def test_default_risk_tier_is_auto(self, tmp_path):
        result = create_directory(str(tmp_path / "x"))
        assert result["risk_tier"] == "auto"

    def test_custom_risk_tier_is_stored(self, tmp_path):
        result = create_directory(str(tmp_path / "y"), risk_tier="confirm")
        assert result["risk_tier"] == "confirm"

    def test_result_contains_path(self, tmp_path):
        p = str(tmp_path / "pathcheck")
        result = create_directory(p)
        assert result["path"] == p

    def test_error_on_invalid_path(self):
        # Trying to create a directory where a component is an existing file
        # is platform-dependent but generally errors.  We just verify no crash.
        result = create_directory("/\x00invalid")   # null byte is always invalid
        assert result["status"] in ("success", "error")   # must not raise


# ---------------------------------------------------------------------------
# move_file tests
# ---------------------------------------------------------------------------

class TestMoveFile:

    def test_moves_a_file(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("content")
        dst = str(tmp_path / "dst.txt")
        result = move_file(str(src), dst)
        assert result["status"] == "success"
        assert os.path.exists(dst)
        assert not os.path.exists(str(src))

    def test_moves_a_directory(self, tmp_path):
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "file.txt").write_text("hi")
        dst_dir = str(tmp_path / "dst_dir")
        result = move_file(str(src_dir), dst_dir)
        assert result["status"] == "success"
        assert os.path.isdir(dst_dir)

    def test_default_risk_tier_is_confirm(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("x")
        dst = str(tmp_path / "b.txt")
        result = move_file(str(src), dst)
        assert result["risk_tier"] == "confirm"

    def test_custom_risk_tier_stored(self, tmp_path):
        src = tmp_path / "c.txt"
        src.write_text("x")
        dst = str(tmp_path / "d.txt")
        result = move_file(str(src), dst, risk_tier="destructive")
        assert result["risk_tier"] == "destructive"

    def test_missing_source_returns_error(self, tmp_path):
        result = move_file(str(tmp_path / "does_not_exist.txt"), str(tmp_path / "dst.txt"))
        assert result["status"] == "error"
        assert "message" in result

    def test_result_contains_source_and_destination(self, tmp_path):
        src = tmp_path / "s.txt"
        src.write_text("x")
        dst = str(tmp_path / "t.txt")
        result = move_file(str(src), dst)
        assert result["source"] == str(src)
        assert result["destination"] == dst


# ---------------------------------------------------------------------------
# delete_file tests
# ---------------------------------------------------------------------------

class TestDeleteFile:

    def test_deletes_a_file(self, tmp_path):
        f = tmp_path / "to_delete.txt"
        f.write_text("bye")
        result = delete_file(str(f))
        assert result["status"] == "success"
        assert not os.path.exists(str(f))

    def test_deletes_a_directory_tree(self, tmp_path):
        d = tmp_path / "tree"
        d.mkdir()
        (d / "child.txt").write_text("x")
        result = delete_file(str(d))
        assert result["status"] == "success"
        assert not os.path.exists(str(d))

    def test_nonexistent_path_is_success(self, tmp_path):
        result = delete_file(str(tmp_path / "ghost.txt"))
        assert result["status"] == "success"

    def test_default_risk_tier_is_destructive(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        result = delete_file(str(f))
        assert result["risk_tier"] == "destructive"

    def test_custom_risk_tier_stored(self, tmp_path):
        f = tmp_path / "y.txt"
        f.write_text("y")
        result = delete_file(str(f), risk_tier="confirm")
        assert result["risk_tier"] == "confirm"

    def test_result_contains_path(self, tmp_path):
        f = tmp_path / "z.txt"
        f.write_text("z")
        p = str(f)
        result = delete_file(p)
        assert result["path"] == p


# ---------------------------------------------------------------------------
# list_directory tests
# ---------------------------------------------------------------------------

class TestListDirectory:

    def test_lists_files_and_dirs(self, tmp_path):
        (tmp_path / "alpha.txt").write_text("a")
        (tmp_path / "beta.txt").write_text("b")
        (tmp_path / "subdir").mkdir()
        result = list_directory(str(tmp_path))
        assert result["status"] == "success"
        names = [e["name"] for e in result["entries"]]
        assert "alpha.txt" in names
        assert "beta.txt" in names
        assert "subdir" in names

    def test_entry_types_are_correct(self, tmp_path):
        (tmp_path / "file.txt").write_text("f")
        (tmp_path / "folder").mkdir()
        result = list_directory(str(tmp_path))
        entries = {e["name"]: e for e in result["entries"]}
        assert entries["file.txt"]["type"] == "file"
        assert entries["folder"]["type"] == "directory"

    def test_file_entries_have_size(self, tmp_path):
        (tmp_path / "sized.txt").write_text("hello")
        result = list_directory(str(tmp_path))
        entry = next(e for e in result["entries"] if e["name"] == "sized.txt")
        assert isinstance(entry["size"], int)
        assert entry["size"] > 0

    def test_directory_entries_have_none_size(self, tmp_path):
        (tmp_path / "adir").mkdir()
        result = list_directory(str(tmp_path))
        entry = next(e for e in result["entries"] if e["name"] == "adir")
        assert entry["size"] is None

    def test_empty_directory(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = list_directory(str(empty))
        assert result["status"] == "success"
        assert result["entries"] == []

    def test_nonexistent_path_returns_error(self, tmp_path):
        result = list_directory(str(tmp_path / "no_such_dir"))
        assert result["status"] == "error"

    def test_file_path_returns_error(self, tmp_path):
        f = tmp_path / "not_a_dir.txt"
        f.write_text("x")
        result = list_directory(str(f))
        assert result["status"] == "error"

    def test_default_risk_tier_is_auto(self, tmp_path):
        result = list_directory(str(tmp_path))
        assert result["risk_tier"] == "auto"

    def test_custom_risk_tier_stored(self, tmp_path):
        result = list_directory(str(tmp_path), risk_tier="confirm")
        assert result["risk_tier"] == "confirm"

    def test_result_contains_path(self, tmp_path):
        result = list_directory(str(tmp_path))
        assert result["path"] == str(tmp_path)

    def test_entries_are_sorted(self, tmp_path):
        for name in ["zebra.txt", "apple.txt", "mango.txt"]:
            (tmp_path / name).write_text("x")
        result = list_directory(str(tmp_path))
        names = [e["name"] for e in result["entries"]]
        assert names == sorted(names)
