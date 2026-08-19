"""test_loop_recovery.py — Phase 5: Multi-Step Loop Engineering & Error Recovery tests.

Covers:
- RouterState enum and decide_next_step transitions (CONTINUE, REPLAN, RECOVER, COMPLETE, FAILED).
- VerificationResult vs legacy dict compatibility in decide_next_step.
- build_tool_registry registering Phase 1 tools guarded with PermissionHarness.
- execute_command per-call risk_tier honor & destructive pattern safety net override.
- Error recovery feedback propagation in loop instructions.
- Standardized logging output tags format.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller.router import decide_next_step, RouterState, RoutingDecision
from controller.evaluator import VerificationResult
from controller.state import TerminationReason
from controller.permissions import PermissionHarness, PermissionDecision
from tools import build_tool_registry
from tools.terminal import execute_command as raw_execute_command


# ===========================================================================
# 1. RouterState & decide_next_step Tests
# ===========================================================================

class TestRouterTransitions:
    def test_complete_state_on_success_legacy_dict(self):
        eval_res = {"is_correct": True, "score": 1.0}
        decision = decide_next_step(eval_res, current_iteration=1, max_iterations=5, elapsed_seconds=10, max_seconds=100)
        assert decision.state == RouterState.COMPLETE
        assert decision.should_continue is False
        assert decision.termination_reason == TerminationReason.SUCCESS
        assert decision["continue"] is False

    def test_complete_state_on_success_verification_result(self):
        vr = VerificationResult(passed=True, strategy="file_exists", evidence="File found")
        decision = decide_next_step(vr, current_iteration=1, max_iterations=5, elapsed_seconds=10, max_seconds=100)
        assert decision.state == RouterState.COMPLETE
        assert decision.should_continue is False
        assert decision.termination_reason == TerminationReason.SUCCESS

    def test_timeout_transition_to_failed(self):
        eval_res = {"is_correct": False}
        decision = decide_next_step(eval_res, current_iteration=1, max_iterations=5, elapsed_seconds=105, max_seconds=100)
        assert decision.state == RouterState.FAILED
        assert decision.should_continue is False
        assert decision.termination_reason == TerminationReason.TIMEOUT

    def test_max_iterations_transition_to_failed(self):
        eval_res = {"is_correct": False}
        decision = decide_next_step(eval_res, current_iteration=5, max_iterations=5, elapsed_seconds=10, max_seconds=100)
        assert decision.state == RouterState.FAILED
        assert decision.should_continue is False
        assert decision.termination_reason == TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT

    def test_repeated_failure_threshold_transition_to_failed(self):
        eval_res = {"is_correct": False}
        decision = decide_next_step(
            eval_res, current_iteration=2, max_iterations=5, elapsed_seconds=10, max_seconds=100,
            same_failure_count=3, max_same_failures=3
        )
        assert decision.state == RouterState.FAILED
        assert decision.should_continue is False
        assert decision.termination_reason == TerminationReason.VERIFICATION_FAILED

    def test_replan_transition_on_repeated_failure(self):
        eval_res = {"is_correct": False}
        decision = decide_next_step(
            eval_res, current_iteration=2, max_iterations=5, elapsed_seconds=10, max_seconds=100,
            same_failure_count=2, max_same_failures=5
        )
        assert decision.state == RouterState.REPLAN
        assert decision.should_continue is True
        assert decision.suggested_action is not None

    def test_recover_transition_on_single_failure_or_issues(self):
        eval_res = {"is_correct": False, "issues": ["tests_failed"]}
        decision = decide_next_step(
            eval_res, current_iteration=1, max_iterations=5, elapsed_seconds=10, max_seconds=100,
            same_failure_count=1
        )
        assert decision.state == RouterState.RECOVER
        assert decision.should_continue is True
        assert decision.suggested_action is not None

    def test_continue_state_normal(self):
        # Empty issues and same_failure_count 0 without success
        eval_res = {"is_correct": False, "issues": []}
        decision = decide_next_step(
            eval_res, current_iteration=1, max_iterations=5, elapsed_seconds=10, max_seconds=100,
            same_failure_count=0
        )
        assert decision.state == RouterState.CONTINUE
        assert decision.should_continue is True

    def test_replan_transition_on_repeated_worker_summary(self):
        eval_res = {"is_correct": False}
        decision = decide_next_step(
            eval_res,
            current_iteration=2,
            max_iterations=5,
            elapsed_seconds=10,
            max_seconds=100,
            worker_summary="Created status.txt with SYSTEM_OK content.",
            previous_worker_summary="Created status.txt with SYSTEM_OK content.",
        )
        assert decision.state == RouterState.REPLAN
        assert decision.should_continue is True
        assert "Near-identical worker output" in decision.reason

    def test_dict_backwards_compatibility(self):
        eval_res = {"is_correct": True}
        decision = decide_next_step(eval_res, current_iteration=1, max_iterations=5, elapsed_seconds=10, max_seconds=100)
        assert decision["continue"] is False
        assert decision.get("continue") is False
        assert decision.get("termination_reason") == TerminationReason.SUCCESS
        assert "state" in decision.to_dict()


# ===========================================================================
# 2. Tool Registry & Permission Harness Integration
# ===========================================================================

class TestToolRegistryPermissions:
    def test_build_tool_registry_includes_phase1_tools(self, tmp_path):
        tools = build_tool_registry(repo_path=str(tmp_path), test_cmd="pytest")
        tool_names = [t.name for t in tools]
        for expected in ["execute_command", "create_directory", "move_file", "delete_file", "list_directory"]:
            assert expected in tool_names

    def test_create_directory_and_list_directory_auto_approved(self, tmp_path):
        harness = PermissionHarness(interactive=False)
        tools = {t.name: t for t in build_tool_registry(repo_path=str(tmp_path), test_cmd="pytest", harness=harness)}
        
        # create_directory
        res = tools["create_directory"].run({"path": "new_dir"})
        assert res.get("status") == "success"
        assert (tmp_path / "new_dir").is_dir()

        # list_directory
        res_ls = tools["list_directory"].run({"path": "."})
        assert res_ls.get("status") == "success"
        assert any(e["name"] == "new_dir" for e in res_ls.get("entries", []))

    def test_move_file_confirm_tier_denied_in_non_interactive(self, tmp_path):
        harness = PermissionHarness(interactive=False)
        tools = {t.name: t for t in build_tool_registry(repo_path=str(tmp_path), test_cmd="pytest", harness=harness)}
        
        (tmp_path / "src.txt").write_text("hello")
        res = tools["move_file"].run({"source": "src.txt", "destination": "dst.txt"})
        assert res.get("status") == "permission_denied"

    def test_delete_file_destructive_tier_denied_in_non_interactive(self, tmp_path):
        harness = PermissionHarness(interactive=False)
        tools = {t.name: t for t in build_tool_registry(repo_path=str(tmp_path), test_cmd="pytest", harness=harness)}
        
        (tmp_path / "file.txt").write_text("hello")
        res = tools["delete_file"].run({"path": "file.txt"})
        assert res.get("status") == "permission_denied"

    def test_execute_command_honors_caller_risk_tier_auto(self, tmp_path):
        harness = PermissionHarness(interactive=False)
        tools = {t.name: t for t in build_tool_registry(repo_path=str(tmp_path), test_cmd="pytest", harness=harness)}
        
        # 'auto' tier is allowed in non-interactive
        res = tools["execute_command"].run({"command": "python -c \"print('ran')\"", "risk_tier": "auto"})
        assert res.get("status") == "success"
        assert "ran" in res.get("stdout", "")

    def test_execute_command_honors_caller_risk_tier_confirm(self, tmp_path):
        harness = PermissionHarness(interactive=False)
        tools = {t.name: t for t in build_tool_registry(repo_path=str(tmp_path), test_cmd="pytest", harness=harness)}
        
        # 'confirm' tier is denied in non-interactive
        res = tools["execute_command"].run({"command": "python -c \"print('ran')\"", "risk_tier": "confirm"})
        assert res.get("status") == "permission_denied"

    def test_execute_command_destructive_safety_net_override(self, tmp_path):
        harness = PermissionHarness(interactive=False)
        tools = {t.name: t for t in build_tool_registry(repo_path=str(tmp_path), test_cmd="pytest", harness=harness)}
        
        # Caller claims "auto", but command is destructive -> overridden to destructive -> denied in non-interactive
        res = tools["execute_command"].run({"command": "rm -rf /some/path", "risk_tier": "auto"})
        assert res.get("status") == "permission_denied"


# ===========================================================================
# 3. Instruction Composition with Error Recovery
# ===========================================================================

class TestInstructionComposition:
    def test_build_instruction_includes_error_feedback(self):
        from controller.loop import build_instruction
        inst = build_instruction(
            goal="Fix bug in calculation",
            last_output_tail="AssertionError: 5 != 10",
            force_new_strategy=True,
            skill_content="Follow debugging steps.",
            error_feedback="Targeted fix required for discounts calculation."
        )
        assert "Goal: Fix bug in calculation" in inst
        assert "Approach guide" in inst
        assert "Error recovery note:" in inst
        assert "Targeted fix required for discounts calculation." in inst
        assert "Latest verification output:" in inst
        assert "Do not repeat the same approach" in inst


# ===========================================================================
# 4. Standardized Logging Tags Verification
# ===========================================================================

class TestStandardizedLoggingTags:
    def test_loop_run_prints_standard_tags(self, tmp_path, capsys):
        from controller.loop import run
        from config import Config
        from controller.state import RunState
        
        cfg = Config(repo_path=str(tmp_path), goal="test tag printing", max_iterations=1)
        
        with patch("controller.loop.load_state", return_value=RunState(goal="test tag printing")), \
             patch("controller.loop.save_state"), \
             patch("controller.loop.ensure_work_branch"), \
             patch("controller.loop.build_worker_agent", return_value=MagicMock()), \
             patch("controller.loop.run_worker_turn", return_value="Summary of work"), \
             patch("controller.loop.run_tests", return_value=MagicMock(passed=True, returncode=0, output_tail="All passed")), \
             patch("controller.loop.commit_iteration", return_value="abc12345"):
            
            success = run(cfg)
            assert success is True
            captured = capsys.readouterr().out
            
            # Verify required standardized tags
            for tag in ["[STEP]", "[PLAN]", "[AGENT]", "[VERIFY]", "[RESULT]", "[DONE]"]:
                assert tag in captured, f"Expected tag {tag} in stdout"

    def test_loop_replan_on_repeated_worker_summary(self, tmp_path, capsys):
        from controller.loop import run
        from config import Config
        from controller.state import RunState
        
        cfg = Config(repo_path=str(tmp_path), goal="fix bug", max_iterations=3)
        
        summaries = [
            "Attempted fix on calculate() by changing return value.",
            "Attempted fix on calculate() by changing return value.",
            "Completely different approach using refactored method.",
        ]
        
        with patch("controller.loop.load_state", return_value=RunState(goal="fix bug")), \
             patch("controller.loop.save_state"), \
             patch("controller.loop.ensure_work_branch"), \
             patch("controller.loop.build_worker_agent", return_value=MagicMock()), \
             patch("controller.loop.run_worker_turn", side_effect=summaries) as mock_worker, \
             patch("controller.loop.run_tests", side_effect=[
                 MagicMock(passed=False, returncode=1, output_tail="Fail 1"),
                 MagicMock(passed=False, returncode=1, output_tail="Fail 2"),
                 MagicMock(passed=True, returncode=0, output_tail="All passed"),
             ]), \
             patch("controller.loop.commit_iteration", return_value="abc12345"):
            
            success = run(cfg)
            assert success is True
            captured = capsys.readouterr().out
            assert "[RECOVERY] Router triggered REPLAN: Near-identical worker output detected" in captured
            # Check that iteration 3 received updated instruction with force_new_strategy
            assert mock_worker.call_count == 3
            iter3_instruction = mock_worker.call_args_list[2][0][1]
            assert "Do not repeat the same approach" in iter3_instruction
            assert "Error recovery note:" in iter3_instruction
            assert "Repeated failure occurred." in iter3_instruction
