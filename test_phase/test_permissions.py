"""test_permissions.py — Phase 2: Three-Tier Permission Harness unit tests.

Covers:
- PermissionTier and PermissionDecision enum values.
- check_permission:
    * Tier "auto" always returns ALLOWED without prompting.
    * Tier "confirm"/"destructive" in interactive mode prompts:
        - 'y' -> ALLOWED
        - anything else -> DENIED
    * Non-interactive mode denies without blocking on input().
    * Non-interactive + exact allowlist match -> ALLOWED, no prompt.
    * Non-interactive + substring-only match  -> DENIED (no glob/prefix match).
- execute_guarded:
    * Approved call actually invokes the function and returns its result.
    * Denied call returns the structured permission_denied dict without
      calling the function.
    * tool_name kwarg overrides func.__name__ in the audit log and denial dict.
- Audit trail:
    * Every check_permission call appends a record.
    * Record contains timestamp, tool_name, risk_tier, decision, reason.
    * Records are in order and reflect the correct decision.
"""

import os
import sys
import time
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller.permissions import (
    PermissionDecision,
    PermissionHarness,
    PermissionTier,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _harness(interactive=False, allowlist=None, prompter=None):
    """Convenience factory for PermissionHarness."""
    return PermissionHarness(
        interactive=interactive,
        allowlist=allowlist,
        prompter=prompter,
    )


def _noop_func(*args, **kwargs):
    """Simple stand-in tool function that records calls."""
    return {"status": "success", "called_with": (args, kwargs)}


# ---------------------------------------------------------------------------
# PermissionTier enum
# ---------------------------------------------------------------------------

class TestPermissionTier:
    def test_auto_value(self):
        assert PermissionTier.AUTO == "auto"

    def test_confirm_value(self):
        assert PermissionTier.CONFIRM == "confirm"

    def test_destructive_value(self):
        assert PermissionTier.DESTRUCTIVE == "destructive"

    def test_all_three_members(self):
        members = {t.value for t in PermissionTier}
        assert members == {"auto", "confirm", "destructive"}


# ---------------------------------------------------------------------------
# PermissionDecision enum
# ---------------------------------------------------------------------------

class TestPermissionDecision:
    def test_allowed_value(self):
        assert PermissionDecision.ALLOWED == "allowed"

    def test_denied_value(self):
        assert PermissionDecision.DENIED == "denied"

    def test_prompt_required_value(self):
        assert PermissionDecision.PROMPT_REQUIRED == "prompt_required"

    def test_all_three_members(self):
        members = {d.value for d in PermissionDecision}
        assert members == {"allowed", "denied", "prompt_required"}


# ---------------------------------------------------------------------------
# Tier 1 (auto) — always allowed, never prompts
# ---------------------------------------------------------------------------

class TestAutoTier:
    def test_auto_returns_allowed(self):
        h = _harness()
        decision, reason = h.check_permission("some_tool", "auto", {})
        assert decision == PermissionDecision.ALLOWED

    def test_auto_reason_text(self):
        h = _harness()
        _, reason = h.check_permission("some_tool", "auto", {})
        assert "auto" in reason.lower() or "approved" in reason.lower()

    def test_auto_does_not_call_prompter(self):
        prompter = MagicMock()
        h = _harness(interactive=True, prompter=prompter)
        h.check_permission("some_tool", "auto", {})
        prompter.assert_not_called()

    def test_auto_approved_in_non_interactive_mode(self):
        h = _harness(interactive=False)
        decision, _ = h.check_permission("some_tool", "auto", {})
        assert decision == PermissionDecision.ALLOWED

    def test_auto_approved_even_without_allowlist(self):
        h = _harness(interactive=False, allowlist=set())
        decision, _ = h.check_permission("some_tool", "auto", {})
        assert decision == PermissionDecision.ALLOWED


# ---------------------------------------------------------------------------
# Tier 2 / 3 — interactive mode
# ---------------------------------------------------------------------------

class TestInteractiveMode:
    def test_confirm_prompts_and_approves_on_y(self):
        prompter = MagicMock(return_value="y")
        h = _harness(interactive=True, prompter=prompter)
        decision, reason = h.check_permission("move_file", "confirm", {})
        prompter.assert_called_once()
        assert decision == PermissionDecision.ALLOWED
        assert "approved" in reason.lower()

    def test_confirm_prompts_and_denies_on_n(self):
        prompter = MagicMock(return_value="n")
        h = _harness(interactive=True, prompter=prompter)
        decision, reason = h.check_permission("move_file", "confirm", {})
        assert decision == PermissionDecision.DENIED
        assert "denied" in reason.lower()

    def test_destructive_prompts_and_approves_on_y(self):
        prompter = MagicMock(return_value="y")
        h = _harness(interactive=True, prompter=prompter)
        decision, _ = h.check_permission("delete_file", "destructive", {})
        prompter.assert_called_once()
        assert decision == PermissionDecision.ALLOWED

    def test_destructive_prompts_and_denies_on_n(self):
        prompter = MagicMock(return_value="n")
        h = _harness(interactive=True, prompter=prompter)
        decision, _ = h.check_permission("delete_file", "destructive", {})
        assert decision == PermissionDecision.DENIED

    def test_empty_answer_is_denied(self):
        """Default [y/N] means empty/Enter = No."""
        prompter = MagicMock(return_value="")
        h = _harness(interactive=True, prompter=prompter)
        decision, _ = h.check_permission("delete_file", "destructive", {})
        assert decision == PermissionDecision.DENIED

    def test_uppercase_Y_is_accepted(self):
        prompter = MagicMock(return_value="Y")
        h = _harness(interactive=True, prompter=prompter)
        decision, _ = h.check_permission("move_file", "confirm", {})
        assert decision == PermissionDecision.ALLOWED

    def test_eoferror_treated_as_denial(self):
        """If input() raises EOFError (piped stdin ends), treat as 'n'."""
        prompter = MagicMock(side_effect=EOFError)
        h = _harness(interactive=True, prompter=prompter)
        decision, _ = h.check_permission("delete_file", "destructive", {})
        assert decision == PermissionDecision.DENIED

    def test_keyboard_interrupt_treated_as_denial(self):
        prompter = MagicMock(side_effect=KeyboardInterrupt)
        h = _harness(interactive=True, prompter=prompter)
        decision, _ = h.check_permission("delete_file", "destructive", {})
        assert decision == PermissionDecision.DENIED


# ---------------------------------------------------------------------------
# Tier 2 / 3 — non-interactive mode (no allowlist)
# ---------------------------------------------------------------------------

class TestNonInteractiveMode:
    def test_confirm_denied_without_allowlist(self):
        h = _harness(interactive=False)
        decision, _ = h.check_permission("move_file", "confirm", {})
        assert decision == PermissionDecision.DENIED

    def test_destructive_denied_without_allowlist(self):
        h = _harness(interactive=False)
        decision, _ = h.check_permission("delete_file", "destructive", {})
        assert decision == PermissionDecision.DENIED

    def test_denial_reason_mentions_non_interactive(self):
        h = _harness(interactive=False)
        _, reason = h.check_permission("delete_file", "destructive", {})
        assert "non-interactive" in reason.lower() or "pre-approval" in reason.lower()

    def test_non_interactive_never_calls_prompter(self):
        """Even with a prompter injected, non-interactive must not call it."""
        prompter = MagicMock()
        h = _harness(interactive=False, prompter=prompter)
        h.check_permission("delete_file", "destructive", {})
        prompter.assert_not_called()

    def test_non_interactive_never_blocks_on_input(self):
        """Regression: prompter must not be the real ``input`` in non-interactive
        mode — guard against accidentally blocking on stdin."""
        blocked = []

        def blocking_input(prompt=""):
            blocked.append(True)
            raise RuntimeError("Should not have reached input()")

        h = _harness(interactive=False, prompter=blocking_input)
        h.check_permission("delete_file", "destructive", {})
        assert not blocked, "Non-interactive mode must NOT call the prompter"


# ---------------------------------------------------------------------------
# Non-interactive + allowlist (exact match only)
# ---------------------------------------------------------------------------

class TestAllowlist:
    def test_exact_match_is_allowed(self):
        h = _harness(interactive=False, allowlist={"delete_file"})
        decision, reason = h.check_permission("delete_file", "destructive", {})
        assert decision == PermissionDecision.ALLOWED
        assert "allowlisted" in reason.lower()

    def test_exact_match_confirm_tier_is_allowed(self):
        h = _harness(interactive=False, allowlist={"move_file"})
        decision, _ = h.check_permission("move_file", "confirm", {})
        assert decision == PermissionDecision.ALLOWED

    def test_substring_match_is_denied(self):
        """'execute_command' in allowlist must NOT approve 'execute_command_extra'."""
        h = _harness(interactive=False, allowlist={"execute_command"})
        decision, _ = h.check_permission("execute_command_extra", "confirm", {})
        assert decision == PermissionDecision.DENIED

    def test_prefix_match_is_denied(self):
        """A short allowlist entry must NOT approve a longer tool name."""
        h = _harness(interactive=False, allowlist={"pip install"})
        decision, _ = h.check_permission("pip install anything-malicious", "confirm", {})
        assert decision == PermissionDecision.DENIED

    def test_superset_name_not_approved_by_subset_entry(self):
        """Allowlist entry 'delete' must NOT approve tool named 'delete_file'."""
        h = _harness(interactive=False, allowlist={"delete"})
        decision, _ = h.check_permission("delete_file", "destructive", {})
        assert decision == PermissionDecision.DENIED

    def test_non_allowlisted_tool_denied_when_others_present(self):
        h = _harness(interactive=False, allowlist={"move_file"})
        decision, _ = h.check_permission("delete_file", "destructive", {})
        assert decision == PermissionDecision.DENIED

    def test_multiple_tools_in_allowlist(self):
        h = _harness(
            interactive=False,
            allowlist={"move_file", "execute_command"},
        )
        d1, _ = h.check_permission("move_file", "confirm", {})
        d2, _ = h.check_permission("execute_command", "confirm", {})
        assert d1 == PermissionDecision.ALLOWED
        assert d2 == PermissionDecision.ALLOWED

    def test_auto_tier_approved_regardless_of_allowlist(self):
        """Allowlist is irrelevant for auto tier — it is always approved."""
        h = _harness(interactive=False, allowlist=set())
        decision, _ = h.check_permission("any_tool", "auto", {})
        assert decision == PermissionDecision.ALLOWED

    def test_allowlist_not_called_by_prompter(self):
        """Allowlisted approval must happen silently, no prompt."""
        prompter = MagicMock()
        h = _harness(interactive=False, allowlist={"delete_file"}, prompter=prompter)
        h.check_permission("delete_file", "destructive", {})
        prompter.assert_not_called()


# ---------------------------------------------------------------------------
# execute_guarded
# ---------------------------------------------------------------------------

class TestExecuteGuarded:
    def test_approved_call_invokes_function(self):
        h = _harness(interactive=False)
        result = h.execute_guarded(_noop_func, "auto", "arg1", key="val")
        assert result["status"] == "success"

    def test_approved_call_passes_args_through(self):
        h = _harness(interactive=False)
        result = h.execute_guarded(_noop_func, "auto", "a", "b", x=1)
        assert result["called_with"] == (("a", "b"), {"x": 1})

    def test_denied_call_does_not_invoke_function(self):
        called = []

        def sentinel(*a, **kw):
            called.append(True)
            return {}

        h = _harness(interactive=False)
        h.execute_guarded(sentinel, "destructive")
        assert not called, "Function must NOT be called when permission is denied"

    def test_denied_returns_permission_denied_dict(self):
        h = _harness(interactive=False)
        result = h.execute_guarded(_noop_func, "destructive")
        assert result["status"] == "permission_denied"
        assert "message" in result
        assert "tool" in result

    def test_denied_tool_field_uses_func_name(self):
        h = _harness(interactive=False)
        result = h.execute_guarded(_noop_func, "destructive")
        assert result["tool"] == "_noop_func"

    def test_tool_name_kwarg_overrides_func_name(self):
        h = _harness(interactive=False)
        result = h.execute_guarded(_noop_func, "destructive", tool_name="my_custom_tool")
        assert result["tool"] == "my_custom_tool"

    def test_allowlist_with_tool_name_kwarg(self):
        """Allowlist match uses the resolved tool_name, not func.__name__."""
        h = _harness(interactive=False, allowlist={"my_custom_tool"})
        result = h.execute_guarded(
            _noop_func, "destructive", tool_name="my_custom_tool"
        )
        assert result["status"] == "success"

    def test_interactive_approved_executes(self):
        prompter = MagicMock(return_value="y")
        h = _harness(interactive=True, prompter=prompter)
        result = h.execute_guarded(_noop_func, "confirm")
        assert result["status"] == "success"

    def test_interactive_denied_returns_denial(self):
        prompter = MagicMock(return_value="n")
        h = _harness(interactive=True, prompter=prompter)
        result = h.execute_guarded(_noop_func, "confirm")
        assert result["status"] == "permission_denied"

    def test_risk_tier_not_discovered_by_calling_func(self):
        """The harness must accept risk_tier as an explicit argument — it must
        not call func to discover the tier.  We verify by passing a destructive
        tier for a function that would otherwise return 'success', and
        confirming the function is never called (non-interactive, no allowlist)."""
        called = []

        def sneaky_func():
            called.append(True)
            return {"status": "success"}

        h = _harness(interactive=False)
        result = h.execute_guarded(sneaky_func, "destructive")
        assert not called
        assert result["status"] == "permission_denied"


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_audit_log_starts_empty(self):
        h = _harness()
        assert h.audit_log == []

    def test_single_check_appends_one_record(self):
        h = _harness()
        h.check_permission("my_tool", "auto", {})
        assert len(h.audit_log) == 1

    def test_multiple_checks_append_in_order(self):
        h = _harness()
        h.check_permission("tool_a", "auto", {})
        h.check_permission("tool_b", "auto", {})
        h.check_permission("tool_c", "auto", {})
        assert len(h.audit_log) == 3
        assert h.audit_log[0]["tool_name"] == "tool_a"
        assert h.audit_log[1]["tool_name"] == "tool_b"
        assert h.audit_log[2]["tool_name"] == "tool_c"

    def test_record_has_required_keys(self):
        h = _harness()
        h.check_permission("my_tool", "auto", {})
        record = h.audit_log[0]
        assert "timestamp" in record
        assert "tool_name" in record
        assert "risk_tier" in record
        assert "decision" in record
        assert "reason" in record

    def test_record_timestamp_is_recent(self):
        before = time.time()
        h = _harness()
        h.check_permission("my_tool", "auto", {})
        after = time.time()
        ts = h.audit_log[0]["timestamp"]
        assert before <= ts <= after

    def test_record_stores_correct_tool_name(self):
        h = _harness()
        h.check_permission("delete_file", "auto", {})
        assert h.audit_log[0]["tool_name"] == "delete_file"

    def test_record_stores_correct_risk_tier(self):
        h = _harness()
        h.check_permission("my_tool", "destructive", {})
        assert h.audit_log[0]["risk_tier"] == "destructive"

    def test_record_decision_is_string(self):
        h = _harness()
        h.check_permission("my_tool", "auto", {})
        assert isinstance(h.audit_log[0]["decision"], str)

    def test_allowed_decision_recorded_correctly(self):
        h = _harness()
        h.check_permission("my_tool", "auto", {})
        assert h.audit_log[0]["decision"] == "allowed"

    def test_denied_decision_recorded_correctly(self):
        h = _harness(interactive=False)
        h.check_permission("delete_file", "destructive", {})
        assert h.audit_log[0]["decision"] == "denied"

    def test_audit_log_updated_by_execute_guarded(self):
        """execute_guarded internally calls check_permission, so audit grows."""
        h = _harness(interactive=False)
        h.execute_guarded(_noop_func, "auto")
        assert len(h.audit_log) == 1

    def test_audit_log_accumulates_across_execute_guarded_calls(self):
        h = _harness(interactive=False)
        h.execute_guarded(_noop_func, "auto")
        h.execute_guarded(_noop_func, "destructive")
        assert len(h.audit_log) == 2
        assert h.audit_log[0]["decision"] == "allowed"
        assert h.audit_log[1]["decision"] == "denied"

    def test_allowlisted_approval_recorded(self):
        h = _harness(interactive=False, allowlist={"delete_file"})
        h.check_permission("delete_file", "destructive", {})
        assert h.audit_log[0]["decision"] == "allowed"
        assert "allowlist" in h.audit_log[0]["reason"].lower()

    def test_interactive_approval_recorded(self):
        prompter = MagicMock(return_value="y")
        h = _harness(interactive=True, prompter=prompter)
        h.check_permission("move_file", "confirm", {})
        assert h.audit_log[0]["decision"] == "allowed"

    def test_interactive_denial_recorded(self):
        prompter = MagicMock(return_value="n")
        h = _harness(interactive=True, prompter=prompter)
        h.check_permission("move_file", "confirm", {})
        assert h.audit_log[0]["decision"] == "denied"


# ---------------------------------------------------------------------------
# Unknown / edge-case tiers
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_tier_fails_closed(self):
        """Any unrecognised tier string must result in DENIED, not ALLOWED."""
        h = _harness(interactive=False)
        decision, _ = h.check_permission("my_tool", "superpower", {})
        assert decision == PermissionDecision.DENIED

    def test_unknown_tier_recorded_in_audit(self):
        h = _harness(interactive=False)
        h.check_permission("my_tool", "superpower", {})
        assert h.audit_log[0]["decision"] == "denied"

    def test_empty_details_dict_is_accepted(self):
        h = _harness()
        decision, _ = h.check_permission("my_tool", "auto", {})
        assert decision == PermissionDecision.ALLOWED

    def test_rich_details_dict_is_accepted(self):
        h = _harness(interactive=False)
        details = {"command": "rm -rf /", "cwd": "/", "extra": [1, 2, 3]}
        decision, _ = h.check_permission("execute_command", "auto", details)
        assert decision == PermissionDecision.ALLOWED

    def test_harness_default_allowlist_is_empty_set(self):
        h = PermissionHarness()
        assert isinstance(h.allowlist, set)
        assert len(h.allowlist) == 0

    def test_harness_stores_interactive_flag(self):
        h = PermissionHarness(interactive=True)
        assert h.interactive is True

    def test_allowlist_accepts_list_input(self):
        """Allowlist should normalise any iterable to a set."""
        h = PermissionHarness(allowlist=["delete_file", "move_file"])
        assert "delete_file" in h.allowlist
        assert "move_file" in h.allowlist
