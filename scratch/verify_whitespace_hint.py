"""Standalone verification for whitespace hint feedback in PatchedFilesystemBackend.edit()."""

import sys
import os
import tempfile

WORKSPACE = r"d:\Skyllect Intership\Coding agent with deep agents and loop engineering"
sys.path.insert(0, WORKSPACE)
os.chdir(WORKSPACE)

from agents.worker import PatchedFilesystemBackend

PASS = "PASS"
FAIL = "FAIL"

def run_tests():
    all_ok = True

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "todo.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write(
                "class TodoManager:\n"
                "    def pending_tasks(self):\n"
                "        # BUG: condition is inverted\n"
                "        return [t for t in self.storage.all() if t.completed]\n"
            )

        backend = PatchedFilesystemBackend(root_dir=tmpdir)

        # 1. Provide an old_string with wrong leading whitespace (10 spaces instead of 4/8)
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

        ok_hint = (
            res.error is not None
            and "due to whitespace/indentation mismatch" in res.error
            and "Here is the exact matching snippet with correct indentation from the file:" in res.error
            and "    def pending_tasks(self):\n        # BUG: condition is inverted\n        return [t for t in self.storage.all() if t.completed]" in res.error
        )

        print(f"  [{PASS if ok_hint else FAIL}] (1) Whitespace mismatch returns actionable snippet hint")
        all_ok = all_ok and ok_hint
        if not ok_hint:
            print("Received error:", res.error)

        # 2. Use the corrected snippet from the hint, edit should succeed and trigger SHORT_CIRCUIT
        correct_old = "    def pending_tasks(self):\n        # BUG: condition is inverted\n        return [t for t in self.storage.all() if t.completed]"
        correct_new = "    def pending_tasks(self):\n        return [t for t in self.storage.all() if not t.completed]"

        ok_success = False
        try:
            backend.edit(
                file_path="todo.py",
                old_string=correct_old,
                new_string=correct_new,
            )
        except Exception as e:
            if "[SHORT_CIRCUIT]" in str(e):
                ok_success = True
                with open(src, "r", encoding="utf-8") as f:
                    content = f.read()
                ok_success = ok_success and "if not t.completed" in content

        print(f"  [{PASS if ok_success else FAIL}] (2) Corrected snippet succeeds with SHORT_CIRCUIT")
        all_ok = all_ok and ok_success

    if all_ok:
        print("\nAll standalone whitespace hint tests PASSED.")
    else:
        print("\nSome tests FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
