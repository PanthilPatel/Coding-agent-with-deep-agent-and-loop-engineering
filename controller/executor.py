import subprocess
from dataclasses import dataclass
from typing import Optional
import sys
import os

@dataclass
class ExecResult:
    passed: bool
    returncode: int
    output_tail: str

def _run(cmd: str, cwd: str, timeout: int = 300, env: dict = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )

def run_tests(
    repo_path: str,
    test_cmd: str,
    tail_lines: int = 60,
    target_test_path: Optional[str] = None,
) -> ExecResult:
    """Run the test command and return a pass/fail result with a tail of 
    the output, so the controller/agent don't need the full log."""
    try:
        # Rewrite pytest command to run with active Python interpreter
        cmd_to_run = test_cmd
        if test_cmd.startswith("pytest"):
            # Resolve target_test_path to absolute or dot
            abs_repo = os.path.abspath(repo_path)
            if target_test_path:
                abs_target = os.path.abspath(target_test_path)
                if abs_target == abs_repo:
                    target_arg = "."
                elif abs_target.startswith(abs_repo):
                    target_arg = os.path.relpath(abs_target, abs_repo)
                else:
                    target_arg = abs_target
            else:
                target_arg = "."

            cmd_parts = test_cmd.split()
            # If plain "pytest" without extra arguments, construct standard scoped invocation
            if len(cmd_parts) == 1:
                cmd_to_run = f'"{sys.executable}" -m pytest -p no:cacheprovider -o rootdir="{abs_repo}" "{target_arg}"'
            else:
                cmd_to_run = f'"{sys.executable}" -m ' + test_cmd
                if "-p no:cacheprovider" not in cmd_to_run:
                    cmd_to_run += " -p no:cacheprovider"
                if "-o rootdir=" not in cmd_to_run and "--rootdir" not in cmd_to_run:
                    cmd_to_run += f' -o rootdir="{abs_repo}"'
                # Append target argument if no explicit positional test path in parts
                has_path_arg = any(not p.startswith("-") for p in cmd_parts[1:])
                if not has_path_arg:
                    cmd_to_run += f' "{target_arg}"'

        print(f"[TEST] Executing command: {cmd_to_run}")

        env = os.environ.copy()
        current_pythonpath = env.get("PYTHONPATH", "")
        # Prepend repo_path to PYTHONPATH
        if current_pythonpath:
            env["PYTHONPATH"] = repo_path + os.pathsep + current_pythonpath
        else:
            env["PYTHONPATH"] = repo_path

        proc = _run(cmd_to_run, cwd=repo_path, env=env)
    except subprocess.TimeoutExpired as e:
        return ExecResult(
            passed=False,
            returncode=-1,
            output_tail=f"Test command timed out after {e.timeout}s",
        )

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    tail = "\n".join(combined.strip().splitlines()[-tail_lines:])
    if len(tail) > 1500:
        tail = "..." + tail[-1497:]
    return ExecResult(passed=proc.returncode == 0, returncode=proc.returncode, output_tail=tail)

def run_lint(repo_path: str, lint_cmd: str,tail_lines: int = 40) -> ExecResult:
    """Run a lint/type-check command as an extra pass/fail gate."""
    try:
        proc = _run(lint_cmd, cwd=repo_path)
    except subprocess.TimeoutExpired as e:
        return ExecResult(
            passed=False,
            returncode=-1,
            output_tail=f"Lint command timed out after {e.timeout}s",
        )
    
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    tail = "\n".join(combined.strip().splitlines()[-tail_lines:])
    if len(tail) > 1500:
        tail = "..." + tail[-1497:]
    return ExecResult(passed=proc.returncode == 0, returncode=proc.returncode, output_tail=tail)


def failure_signature(result: ExecResult) -> str:
    """A short, stable-ish signature of a failure, used to detect if the 
    agent is repeating the same mistake across iterations."""
    lines = [l for l in result.output_tail.splitlines() if l.strip()]
    return  "\n".join(lines[-5:]) if  lines else str(result.returncode)