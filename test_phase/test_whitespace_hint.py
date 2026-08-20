"""Unit tests for whitespace-mismatch hint in PatchedFilesystemBackend.edit()."""

import pytest
from langgraph.errors import GraphRecursionError
from agents.worker import PatchedFilesystemBackend


@pytest.fixture
def repo_with_todo(tmp_path):
    """Create a mock repository with indentation in a function."""
    todo_file = tmp_path / "todo.py"
    todo_file.write_text(
        "class TodoManager:\n"
        "    def pending_tasks(self):\n"
        "        # BUG: condition is inverted\n"
        "        return [t for t in self.storage.all() if t.completed]\n",
        encoding="utf-8",
    )
    return tmp_path


def test_whitespace_hint_provides_actionable_snippet(repo_with_todo):
    backend = PatchedFilesystemBackend(root_dir=str(repo_with_todo))
    bad_whitespace_old = (
        "def pending_tasks(self):\n"
        "          # BUG: condition is inverted\n"
        "          return [t for t in self.storage.all() if t.completed]"
    )

    res = backend.edit(
        file_path="todo.py",
        old_string=bad_whitespace_old,
        new_string="def pending_tasks(self):\n        return [t for t in self.storage.all() if not t.completed]",
    )

    assert res.error is not None
    assert "due to whitespace/indentation mismatch" in res.error
    assert "Here is the exact matching snippet with correct indentation from the file:" in res.error
    expected_snippet = "    def pending_tasks(self):\n        # BUG: condition is inverted\n        return [t for t in self.storage.all() if t.completed]"
    assert expected_snippet in res.error


def test_whitespace_corrected_edit_succeeds(repo_with_todo):
    backend = PatchedFilesystemBackend(root_dir=str(repo_with_todo))
    correct_old = (
        "    def pending_tasks(self):\n"
        "        # BUG: condition is inverted\n"
        "        return [t for t in self.storage.all() if t.completed]"
    )
    correct_new = (
        "    def pending_tasks(self):\n"
        "        # Fixed: return pending tasks\n"
        "        return [t for t in self.storage.all() if not t.completed]"
    )

    with pytest.raises(GraphRecursionError) as exc_info:
        backend.edit(
            file_path="todo.py",
            old_string=correct_old,
            new_string=correct_new,
        )

    assert "[SHORT_CIRCUIT]" in str(exc_info.value)
    content = (repo_with_todo / "todo.py").read_text(encoding="utf-8")
    assert "if not t.completed" in content
