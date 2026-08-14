"""Exec tools: run_tests_tool — a thin, agent-invokable wrapper around the
existing ``controller.executor.run_tests()`` function.

Important: The controller still calls ``run_tests()`` directly in its own
verification step (``controller/loop.py``). This tool is an *additional*
agent-facing interface that lets the agent request test output mid-turn —
it does NOT replace the controller's authoritative pass/fail gate.

``run_command`` and ``search_code`` are deliberately NOT implemented here
because ``FilesystemBackend`` already provides both capabilities through its
built-in ``execute`` and ``grep`` tools (verified from deepagents 0.7.5
source inspection).
"""

from langchain_core.tools import tool   

from controller.executor import run_tests as _run_tests
from tools.base import log_tool_call, log_tool_result


def make_run_tests_tool(repo_path: str, test_cmd: str):
    """Factory that returns a run_tests tool bound to a repo_path and
    the configured test_cmd."""

    @tool
    def run_tests_tool(extra_args: str = "") -> str:
        """Run the project's test suite and return a structured pass/fail
        result with the tail of the output.

        Note: The controller also runs tests independently after each worker
        turn as the authoritative pass/fail gate. This tool lets the agent
        check test results during its reasoning without waiting for the next
        controller iteration.

        Args:
            extra_args: Optional extra arguments appended to the test command
                (e.g. ``'-k test_foo'`` to run a single test). Leave blank
                to run the full suite.
        """
        log_tool_call("run_tests_tool")
        cmd = f"{test_cmd} {extra_args}".strip()
        try:
            result = _run_tests(repo_path, cmd)
            status = "PASSED" if result.passed else "FAILED"
            log_tool_result(f"{status} exit_code={result.returncode}")
            return (
                f"Tests {status} (exit code {result.returncode})\n\n"
                f"Output (last 60 lines):\n{result.output_tail}"
            )
        except Exception as exc:
            error = f"ERROR: {type(exc).__name__}: {exc}"
            log_tool_result(f"ERROR {type(exc).__name__}")
            return error

    return run_tests_tool
