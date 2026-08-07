import subprocess
from dataclasses import dataclass

@dataclass
class ExecResult:
    passed: bool
    returncode: int
    output_tail: str

def _run(cmd: str, cwd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

def run_tests(repo_path: str, test_cmd: str, tail_lines: int = 60) -> ExecResult:
    """Run the test command and return a pass/fail result with a tail of 
    the output, so the controller/agent don't need the full log."""
    try:
        proc = _run(test_cmd, cwd=repo_path)
    except subprocess.TimeoutExpired as e:
        return ExecResult(
            passed=False,
            returncode=-1,
            output_tail=f"Test command timed out after {e.timeout}s",
        )

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    tail = "\n".join(combined.strip().splitlines()[-tail_lines:])
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
    return ExecResult(passed=proc.returncode == 0, returncode=proc.returncode, output_tail=tail)


def failure_signature(result: ExecResult) -> str:
    """A short, stable-ish signature of a failure, used to detect if the 
    agent is repeating the same mistake across iterations."""
    lines = [l for l in result.output_tail.splitlines() if l.strip()]
    return  "\n".join(lines[-5:]) if  lines else str(result.returncode)