"""controller/permissions.py — Phase 2: Three-Tier Permission Harness.

This module provides a lightweight, pluggable permission gate that sits in
front of every Phase 1 terminal/filesystem tool call.

Architecture
------------
* ``PermissionTier``    : enum of the three risk levels a tool can declare.
* ``PermissionDecision``: enum of outcomes returned by ``check_permission``.
* ``PermissionHarness`` : the gate object — owns configuration, the prompter,
  the audit trail, and the two public methods every caller uses:

      decision, reason = harness.check_permission(tool_name, risk_tier, details)
      result           = harness.execute_guarded(func, risk_tier, *args, **kwargs)

Allowlist semantics
-------------------
Matching is **exact tool-name only**.  A broader entry such as ``"pip install"``
will NOT approve a call for ``"pip install requests"`` — that substring/glob
behaviour would trivially bypass the gate.  Only the tool's registered name
(e.g. ``"execute_command"``, ``"delete_file"``) is compared.

Interactive prompting
---------------------
The prompter is injected at construction time (default: ``input``), making it
trivially replaceable in unit tests without ``builtins.input`` monkey-patching.

Audit trail
-----------
Every call to ``check_permission`` appends a dict to ``harness.audit_log``:

    {
        "timestamp"  : float  (time.time()),
        "tool_name"  : str,
        "risk_tier"  : str,
        "decision"   : str    (PermissionDecision.value),
        "reason"     : str,
    }
"""

import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PermissionTier(str, Enum):
    """The three risk tiers that a tool caller must declare."""
    AUTO        = "auto"
    CONFIRM     = "confirm"
    DESTRUCTIVE = "destructive"


class PermissionDecision(str, Enum):
    """The outcome returned by ``PermissionHarness.check_permission``."""
    ALLOWED        = "allowed"
    DENIED         = "denied"
    PROMPT_REQUIRED = "prompt_required"


# ---------------------------------------------------------------------------
# PermissionHarness
# ---------------------------------------------------------------------------

class PermissionHarness:
    """Three-tier permission gate for tool execution.

    Args:
        interactive: When ``True`` the harness will prompt the user for
                     ``confirm``/``destructive`` tier calls.  When ``False``
                     only allowlisted tools (exact name match) are approved.
        allowlist:   Optional set of tool names pre-approved for
                     non-interactive execution.  Matching is exact — no
                     substring or glob semantics.
        prompter:    Callable used to prompt the user.  Defaults to the
                     built-in ``input``.  Inject a mock in tests.
    """

    def __init__(
        self,
        interactive: bool = False,
        allowlist: Optional[Set[str]] = None,
        prompter: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.interactive: bool = interactive
        self.allowlist: Set[str] = set(allowlist) if allowlist else set()
        self._prompter: Callable[[str], str] = prompter if prompter is not None else input
        self.audit_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_permission(
        self,
        tool_name: str,
        risk_tier: str,
        details: dict,
    ) -> Tuple[PermissionDecision, str]:
        """Evaluate whether *tool_name* may execute at *risk_tier*.

        Args:
            tool_name: The registered name of the tool (exact string).
            risk_tier: One of ``"auto"``, ``"confirm"``, or ``"destructive"``.
            details:   Arbitrary context dict (args, cwd, …) surfaced to the
                       user prompt and the audit log.

        Returns:
            A ``(PermissionDecision, reason_str)`` tuple.
        """
        decision, reason = self._evaluate(tool_name, risk_tier, details)
        self._record(tool_name, risk_tier, decision, reason)
        return decision, reason

    def execute_guarded(
        self,
        func: Callable,
        risk_tier: str,
        *args: Any,
        tool_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Check permission THEN call *func* if approved.

        The ``risk_tier`` is provided **explicitly by the caller** — this
        method never calls ``func`` first to discover the tier, which would
        defeat the purpose of the gate.

        Args:
            func:      The underlying tool function to guard.
            risk_tier: The caller's declared risk tier string.
            *args:     Positional arguments forwarded to ``func``.
            tool_name: Override the tool name used for permission checks and
                       audit log.  Defaults to ``func.__name__``.
            **kwargs:  Keyword arguments forwarded to ``func``.

        Returns:
            The tool's real return value on approval, or a structured denial
            dict ``{"status": "permission_denied", "message": ...,
            "tool": ...}`` on refusal.
        """
        resolved_name = tool_name if tool_name is not None else func.__name__
        details: dict = {"args": args, "kwargs": kwargs}

        decision, reason = self.check_permission(resolved_name, risk_tier, details)

        if decision == PermissionDecision.ALLOWED:
            return func(*args, **kwargs)

        return {
            "status": "permission_denied",
            "message": reason,
            "tool": resolved_name,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        tool_name: str,
        risk_tier: str,
        details: dict,
    ) -> Tuple[PermissionDecision, str]:
        """Core decision logic — does NOT write to the audit log."""

        # Tier 1: auto — always approved, no prompt needed.
        if risk_tier == PermissionTier.AUTO:
            return PermissionDecision.ALLOWED, "Auto-approved"

        # Tier 2 & 3: confirm / destructive.
        if risk_tier in (PermissionTier.CONFIRM, PermissionTier.DESTRUCTIVE):

            # Non-interactive path.
            if not self.interactive:
                if tool_name in self.allowlist:
                    return PermissionDecision.ALLOWED, "Allowlisted"
                return (
                    PermissionDecision.DENIED,
                    "Permission denied: non-interactive mode requires pre-approval",
                )

            # Interactive path — prompt the user.
            prompt_text = (
                f"\n[PERMISSION] Tool '{tool_name}' requests tier='{risk_tier}'.\n"
                f"Details: {details}\n"
                f"Allow? [y/N]: "
            )
            try:
                answer = self._prompter(prompt_text).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"

            if answer == "y":
                return PermissionDecision.ALLOWED, "User approved"
            return PermissionDecision.DENIED, "User denied"

        # Unknown tier — fail closed.
        return (
            PermissionDecision.DENIED,
            f"Unknown risk_tier '{risk_tier}'; failing closed",
        )

    def _record(
        self,
        tool_name: str,
        risk_tier: str,
        decision: PermissionDecision,
        reason: str,
    ) -> None:
        """Append an entry to the audit log."""
        self.audit_log.append(
            {
                "timestamp": time.time(),
                "tool_name": tool_name,
                "risk_tier": risk_tier,
                "decision": decision.value,
                "reason": reason,
            }
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PermissionTier",
    "PermissionDecision",
    "PermissionHarness",
]
