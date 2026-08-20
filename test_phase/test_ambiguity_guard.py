"""Unit tests for ambiguity guard in PatchedFilesystemBackend."""

import os
import sys
import pytest
from langgraph.errors import GraphRecursionError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.worker import PatchedFilesystemBackend


@pytest.fixture
def repo_with_duplicate_lines(tmp_path):
    """Create a mock repository with duplicate lines across methods."""
    structures_file = tmp_path / "structures.py"
    structures_file.write_text(
        "class Stack:\n"
        "    def pop(self):\n"
        "        if not self._items:\n"
        "            raise IndexError('pop from empty stack')\n"
        "        return self._items.pop()\n\n"
        "class Queue:\n"
        "    def dequeue(self):\n"
        "        if not self._items:\n"
        "            raise IndexError('dequeue from empty queue')\n"
        "        # BUG: should remove and return the FIRST item\n"
        "        return self._items.pop()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_ambiguity_guard_rejection_and_fast_reject(repo_with_duplicate_lines):
    backend = PatchedFilesystemBackend(root_dir=str(repo_with_duplicate_lines))
    ambiguous_old = "        return self._items.pop()"
    new_str = "        return self._items.pop(0)"

    # 1. First ambiguous call is rejected and recorded
    res1 = backend.edit(
        file_path="structures.py",
        old_string=ambiguous_old,
        new_string=new_str,
    )
    assert res1.error is not None
    assert "REJECTED" in res1.error
    assert "matched multiple locations" in res1.error
    assert ("structures.py", ambiguous_old) in backend._rejected_ambiguous_strings

    # 2. Second identical call is immediately fast-rejected
    res2 = backend.edit(
        file_path="structures.py",
        old_string=ambiguous_old,
        new_string=new_str,
    )
    assert res2.error is not None
    assert "already rejected as ambiguous" in res2.error
    assert "Do not resubmit" in res2.error


def test_ambiguity_guard_unique_string_succeeds(repo_with_duplicate_lines):
    backend = PatchedFilesystemBackend(root_dir=str(repo_with_duplicate_lines))
    unique_old = (
        "        # BUG: should remove and return the FIRST item\n"
        "        return self._items.pop()"
    )
    unique_new = (
        "        # Fixed: FIFO order\n"
        "        return self._items.pop(0)"
    )

    # 3. Wider unique string succeeds and triggers short-circuit
    with pytest.raises(GraphRecursionError) as exc_info:
        backend.edit(
            file_path="structures.py",
            old_string=unique_old,
            new_string=unique_new,
        )

    assert "[SHORT_CIRCUIT]" in str(exc_info.value)
    
    # Check that file content was updated
    content = (repo_with_duplicate_lines / "structures.py").read_text(encoding="utf-8")
    assert "self._items.pop(0)" in content
    assert content.count("self._items.pop()") == 1
