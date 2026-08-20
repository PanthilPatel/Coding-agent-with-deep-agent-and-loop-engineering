"""Base tool abstraction and shared logging for the tools package.

All tools in this package follow the same logging convention:
    [TOOL] <name>
    [TOOL] result=OK / result=FAILED / result=ERROR ...
"""

import functools
from typing import Any


# Tool log prefix used across all tool modules — keep consistent with the
# existing [worker], [agent], [tests], [git] prefix style in loop.py
_TOOL_PREFIX = "[TOOL]"


def log_tool_call(name: str) -> None:
    """Print a tool-invocation line to stdout."""
    print(f"{_TOOL_PREFIX} {name}")


def log_tool_result(result_summary: str) -> None:
    """Print a one-line tool-result summary to stdout."""
    print(f"{_TOOL_PREFIX} result={result_summary}")


def safe_tool(func):
    """Decorator that catches any unhandled exception from a tool function
    and converts it to a structured error-string result instead of letting
    it propagate into the agent loop.

    Usage::

        @safe_tool
        def my_tool(arg: str) -> str:
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            error_msg = f"Tool Error: ERROR {type(exc).__name__}: {exc}. Please check your arguments and try again."
            log_tool_result(f"ERROR tool={func.__name__} exc={type(exc).__name__}")
            return error_msg
    return wrapper
