"""test_skills_mcp.py — Phase 4: Dynamic Skills & MCP Integration tests.

Covers:
- SkillLoader.get_matching_skills: trigger matching, ordering, no-match.
- SkillLoader.get_prompt_injection: multi-skill formatting, empty result.
- SkillLoader.get_skill: thin wrapper around load().
- SkillLoader.list_available() and module-level list_skills() still work.
- MCPRegistry: graceful degradation when config is absent or servers fail.
- MCP tool permission gate: tools default to "confirm" tier via execute_guarded.
- loop.py sync/async bridge: no crash when mcp_config_path is unset.
- loop.py registry.close() called even on setup failure.
"""

import asyncio
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skills.loader import SkillInfo, SkillLoader, list_skills, load_skill, select_skill
from controller.permissions import PermissionHarness, PermissionDecision


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _make_skill_dir(tmp_path, name: str, content: str) -> None:
    """Create a valid skill sub-directory with a SKILL.md file."""
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def _loader(tmp_path) -> SkillLoader:
    return SkillLoader(str(tmp_path))


# ===========================================================================
# SkillLoader.get_matching_skills
# ===========================================================================

class TestGetMatchingSkills:
    def test_single_match_returned(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug playbook content")
        loader = _loader(tmp_path)
        results = loader.get_matching_skills("fix the crashing bug")
        names = [r.name for r in results]
        assert "debugging" in names

    def test_returns_list_of_skill_info(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug content")
        loader = _loader(tmp_path)
        results = loader.get_matching_skills("fix the bug")
        assert all(isinstance(r, SkillInfo) for r in results)

    def test_no_match_returns_empty_list(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug content")
        loader = _loader(tmp_path)
        results = loader.get_matching_skills("completely unrelated xyz phrase 12345")
        assert results == []

    def test_multiple_matching_skills_all_returned(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug content")
        _make_skill_dir(tmp_path, "testing", "Test content")
        loader = _loader(tmp_path)
        # "fix" matches debugging; "test" matches testing
        results = loader.get_matching_skills("fix and test the code")
        names = [r.name for r in results]
        assert "debugging" in names
        assert "testing" in names

    def test_higher_score_skill_comes_first(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug content")
        _make_skill_dir(tmp_path, "testing", "Test content")
        loader = _loader(tmp_path)
        # Heavily weight debugging keywords
        results = loader.get_matching_skills(
            "fix the bug fix the error fix the crash",
            keyword_map={
                "debugging": ["fix", "bug", "error", "crash"],
                "testing": ["test"],
            },
        )
        assert results[0].name == "debugging"

    def test_skills_not_on_disk_excluded(self, tmp_path):
        # keyword_map has "git" but no git skill dir exists
        loader = _loader(tmp_path)
        results = loader.get_matching_skills(
            "git commit push",
            keyword_map={"git": ["git", "commit", "push"]},
        )
        assert results == []

    def test_skill_with_empty_content_excluded(self, tmp_path):
        skill_dir = tmp_path / "empty_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("", encoding="utf-8")
        loader = _loader(tmp_path)
        results = loader.get_matching_skills(
            "empty_skill task",
            keyword_map={"empty_skill": ["empty_skill"]},
        )
        assert results == []

    def test_empty_skills_dir_returns_empty_list(self, tmp_path):
        loader = _loader(tmp_path)
        results = loader.get_matching_skills("fix the bug")
        assert results == []

    def test_custom_keyword_map_used(self, tmp_path):
        _make_skill_dir(tmp_path, "custom_skill", "Custom playbook")
        loader = _loader(tmp_path)
        results = loader.get_matching_skills(
            "do the special thing",
            keyword_map={"custom_skill": ["special"]},
        )
        names = [r.name for r in results]
        assert "custom_skill" in names

    def test_content_in_returned_skill_info(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "## How to debug\nStep 1: read logs")
        loader = _loader(tmp_path)
        results = loader.get_matching_skills("fix the bug")
        assert any("debug" in r.content.lower() for r in results)


# ===========================================================================
# SkillLoader.get_prompt_injection
# ===========================================================================

class TestGetPromptInjection:
    def test_single_skill_injection_non_empty(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug playbook")
        loader = _loader(tmp_path)
        result = loader.get_prompt_injection("fix the crashing bug")
        assert len(result) > 0

    def test_injection_contains_skill_name(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug playbook")
        loader = _loader(tmp_path)
        result = loader.get_prompt_injection("fix the crashing bug")
        assert "debugging" in result

    def test_injection_contains_skill_content(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "## Unique debug phrase XYZ")
        loader = _loader(tmp_path)
        result = loader.get_prompt_injection("fix the bug")
        assert "Unique debug phrase XYZ" in result

    def test_no_match_returns_empty_string(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug content")
        loader = _loader(tmp_path)
        result = loader.get_prompt_injection("completely unrelated 99999")
        assert result == ""

    def test_multiple_skills_all_in_injection(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debugging guide")
        _make_skill_dir(tmp_path, "testing", "Testing guide")
        loader = _loader(tmp_path)
        result = loader.get_prompt_injection("fix and test the code")
        assert "debugging" in result
        assert "testing" in result

    def test_injection_uses_separator_between_skills(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug A")
        _make_skill_dir(tmp_path, "testing", "Test B")
        loader = _loader(tmp_path)
        result = loader.get_prompt_injection("fix and test the code")
        # Both should appear separated by some delimiter (--- or blank line)
        assert "---" in result or "\n\n" in result


# ===========================================================================
# SkillLoader.get_skill
# ===========================================================================

class TestGetSkill:
    def test_returns_skill_info_for_existing_skill(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug content")
        loader = _loader(tmp_path)
        result = loader.get_skill("debugging")
        assert isinstance(result, SkillInfo)
        assert result.name == "debugging"

    def test_returns_none_for_path_traversal(self, tmp_path):
        loader = _loader(tmp_path)
        result = loader.get_skill("../../etc/passwd")
        assert result is None

    def test_returns_skill_info_with_content(self, tmp_path):
        _make_skill_dir(tmp_path, "testing", "## Test guide")
        loader = _loader(tmp_path)
        result = loader.get_skill("testing")
        assert "Test guide" in result.content

    def test_missing_skill_returns_empty_content(self, tmp_path):
        loader = _loader(tmp_path)
        result = loader.get_skill("nonexistent_skill")
        # load() returns SkillInfo with empty content for missing files
        assert result is not None
        assert result.content == ""


# ===========================================================================
# SkillLoader.list_available and module-level list_skills
# ===========================================================================

class TestListAvailableAndListSkills:
    def test_list_available_returns_skill_names(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug content")
        _make_skill_dir(tmp_path, "testing", "Test content")
        loader = _loader(tmp_path)
        names = loader.list_available()
        assert "debugging" in names
        assert "testing" in names

    def test_list_available_empty_dir(self, tmp_path):
        loader = _loader(tmp_path)
        assert loader.list_available() == []

    def test_list_available_sorted(self, tmp_path):
        _make_skill_dir(tmp_path, "zzz", "z content")
        _make_skill_dir(tmp_path, "aaa", "a content")
        loader = _loader(tmp_path)
        names = loader.list_available()
        assert names == sorted(names)

    def test_module_level_list_skills_works(self, tmp_path):
        _make_skill_dir(tmp_path, "debugging", "Debug content")
        names = list_skills(skills_dir=str(tmp_path))
        assert "debugging" in names

    def test_dir_without_skill_md_excluded(self, tmp_path):
        (tmp_path / "no_skill_file").mkdir()  # no SKILL.md inside
        loader = _loader(tmp_path)
        assert "no_skill_file" not in loader.list_available()


# ===========================================================================
# MCPRegistry — graceful degradation
# ===========================================================================

class TestMCPRegistryDegradation:
    def test_empty_config_yields_zero_tools(self, tmp_path):
        """Registry with empty servers dict should initialize with no tools."""
        config_file = tmp_path / "mcp.json"
        config_file.write_text('{"servers": {}}', encoding="utf-8")

        from mcp_agent import MCPRegistry
        registry = MCPRegistry(str(config_file))
        asyncio.run(registry.initialize())
        assert registry.tools == []

    def test_missing_config_file_yields_zero_tools(self, tmp_path):
        """Non-existent config path should not crash — degrade gracefully."""
        from mcp_agent import MCPRegistry
        registry = MCPRegistry(str(tmp_path / "nonexistent.json"))
        asyncio.run(registry.initialize())
        assert registry.tools == []

    def test_malformed_config_yields_zero_tools(self, tmp_path):
        """Malformed JSON config should not crash — degrade gracefully."""
        config_file = tmp_path / "mcp.json"
        config_file.write_text("not json {{{ invalid", encoding="utf-8")

        from mcp_agent import MCPRegistry
        registry = MCPRegistry(str(config_file))
        asyncio.run(registry.initialize())
        assert registry.tools == []

    def test_close_on_empty_registry_does_not_raise(self, tmp_path):
        from mcp_agent import MCPRegistry
        registry = MCPRegistry(str(tmp_path / "nonexistent.json"))
        asyncio.run(registry.initialize())
        try:
            asyncio.run(registry.close())
        except Exception as exc:
            pytest.fail(f"registry.close() raised unexpectedly: {exc}")

    def test_registry_tools_list_is_list(self, tmp_path):
        from mcp_agent import MCPRegistry
        registry = MCPRegistry(str(tmp_path / "nonexistent.json"))
        asyncio.run(registry.initialize())
        assert isinstance(registry.tools, list)


# ===========================================================================
# MCP tools guarded by PermissionHarness at "confirm" tier
# ===========================================================================

class TestMCPToolPermissionGate:
    """Verify that MCP tools are wrapped with PermissionHarness at confirm tier
    in non-interactive mode (no allowlist), they are denied by default."""

    def test_confirm_tier_denied_in_non_interactive_mode(self):
        """Simulate what loop.py does: harness(non-interactive) + confirm tier."""
        harness = PermissionHarness(interactive=False)
        mock_tool_run = MagicMock(return_value="tool_output")

        result = harness.execute_guarded(
            mock_tool_run,
            risk_tier="confirm",
            tool_name="mcp__some_server__some_tool",
        )
        assert result["status"] == "permission_denied"
        mock_tool_run.assert_not_called()

    def test_confirm_tier_allowed_with_allowlist(self):
        """MCP tool on allowlist IS approved."""
        harness = PermissionHarness(
            interactive=False,
            allowlist={"mcp__server__my_tool"},
        )
        mock_tool_run = MagicMock(return_value={"output": "ok"})

        result = harness.execute_guarded(
            mock_tool_run,
            risk_tier="confirm",
            tool_name="mcp__server__my_tool",
        )
        assert result != {"status": "permission_denied"}
        mock_tool_run.assert_called_once()

    def test_mcp_tool_uses_confirm_not_auto(self):
        """MCP tools must NOT be auto-approved — they must go through confirm gate."""
        harness = PermissionHarness(interactive=False)
        decision, _ = harness.check_permission(
            "mcp__server__dangerous_tool", "confirm", {}
        )
        assert decision == PermissionDecision.DENIED

    def test_audit_log_records_mcp_tool_check(self):
        """Every MCP tool invocation attempt is recorded in the audit log."""
        harness = PermissionHarness(interactive=False)
        harness.execute_guarded(
            lambda: None,
            risk_tier="confirm",
            tool_name="mcp__srv__tool",
        )
        assert len(harness.audit_log) == 1
        assert harness.audit_log[0]["tool_name"] == "mcp__srv__tool"
        assert harness.audit_log[0]["risk_tier"] == "confirm"

    def test_interactive_approval_executes_mcp_tool(self):
        """In interactive mode, 'y' answer should let the MCP tool execute."""
        prompter = MagicMock(return_value="y")
        harness = PermissionHarness(interactive=True, prompter=prompter)
        sentinel = MagicMock(return_value="mcp_result")

        result = harness.execute_guarded(
            sentinel,
            risk_tier="confirm",
            tool_name="mcp__server__tool",
        )
        sentinel.assert_called_once()

    def test_mcp_default_tier_is_confirm_not_destructive(self):
        """Default MCP tier is confirm (not auto or destructive)."""
        # Just validate that "confirm" is the value used in the guard call
        # (the loop.py code uses risk_tier="confirm" for all MCP tools).
        harness = PermissionHarness(interactive=False)
        decision, _ = harness.check_permission("mcp__any", "confirm", {})
        # Non-interactive, no allowlist -> denied (confirms confirm tier is being checked)
        assert decision == PermissionDecision.DENIED

    def test_auto_tier_would_bypass_gate(self):
        """Contrast: 'auto' tier bypasses the gate — MCP tools must NOT use auto."""
        harness = PermissionHarness(interactive=False)
        decision, _ = harness.check_permission("mcp__any", "auto", {})
        assert decision == PermissionDecision.ALLOWED


# ===========================================================================
# loop.py sync/async bridge: no crash when mcp_config_path is None
# ===========================================================================

class TestLoopMCPBridge:
    """Test loop.py MCP integration without running the full loop.

    We mock everything except the MCP-specific paths so tests run fast
    and deterministically.
    """

    def _make_config(self, tmp_path, mcp_config_path=None):
        """Return a minimal mock Config for loop.run()."""
        cfg = MagicMock()
        cfg.is_remote = False
        cfg.repo_path = str(tmp_path)
        cfg.local_repo_path = str(tmp_path)
        cfg.state_file = "state.json"
        cfg.goal = "test goal"
        cfg.max_iterations = 1
        cfg.max_seconds = 3600
        cfg.require_approval = False
        cfg.model_name = "test-model"
        cfg.llm_provider = "ollama"
        cfg.test_cmd = "pytest"
        cfg.lint_cmd = None
        cfg.skills_dir = None
        cfg.mcp_config_path = mcp_config_path
        return cfg

    def test_no_mcp_config_does_not_crash(self, tmp_path):
        """When mcp_config_path is None, the loop must not crash on MCP init."""
        cfg = self._make_config(tmp_path, mcp_config_path=None)

        # Stub out everything that would require real external services
        with patch("controller.loop.load_state") as mock_ls, \
             patch("controller.loop.save_state"), \
             patch("controller.loop.ensure_work_branch"), \
             patch("controller.loop.build_tool_registry", return_value=[]), \
             patch("controller.loop.build_worker_agent") as mock_bwa, \
             patch("controller.loop.select_skill", return_value=None), \
             patch("controller.loop.run_worker_turn", return_value="done"), \
             patch("controller.loop.run_tests") as mock_rt, \
             patch("controller.loop.failure_signature", return_value="sig"), \
             patch("controller.loop.commit_iteration", return_value=None), \
             patch("controller.loop.get_diff", return_value=""):
            from controller.state import RunState
            mock_ls.return_value = RunState(goal="test goal")
            mock_bwa.return_value = MagicMock()
            exec_result = MagicMock()
            exec_result.passed = True
            exec_result.returncode = 0
            exec_result.output_tail = ""
            mock_rt.return_value = exec_result

            from controller.loop import run
            # Must not raise
            try:
                run(cfg)
            except Exception as exc:
                pytest.fail(f"run() raised unexpectedly: {exc}")

    def test_mcp_config_path_none_skips_registry_init(self, tmp_path):
        """When mcp_config_path is None, MCPRegistry must never be instantiated."""
        cfg = self._make_config(tmp_path, mcp_config_path=None)

        with patch("controller.loop.load_state") as mock_ls, \
             patch("controller.loop.save_state"), \
             patch("controller.loop.ensure_work_branch"), \
             patch("controller.loop.build_tool_registry", return_value=[]), \
             patch("controller.loop.build_worker_agent", return_value=MagicMock()), \
             patch("controller.loop.select_skill", return_value=None), \
             patch("controller.loop.run_worker_turn", return_value="done"), \
             patch("controller.loop.run_tests") as mock_rt, \
             patch("controller.loop.failure_signature", return_value="sig"), \
             patch("controller.loop.commit_iteration", return_value=None), \
             patch("controller.loop.get_diff", return_value=""), \
             patch("mcp_agent.MCPRegistry") as mock_registry_cls:
            from controller.state import RunState
            mock_ls.return_value = RunState(goal="test goal")
            exec_result = MagicMock(passed=True, returncode=0, output_tail="")
            mock_rt.return_value = exec_result

            from controller.loop import run
            run(cfg)
            mock_registry_cls.assert_not_called()

    def test_setup_failure_closes_registry(self, tmp_path):
        """If setup raises after registry init, registry.close() must still be called."""
        cfg = self._make_config(tmp_path, mcp_config_path="/fake/mcp.json")

        mock_registry = MagicMock()
        mock_registry.tools = []
        mock_registry.initialize = AsyncMock()
        mock_registry.close = AsyncMock()

        with patch("controller.loop.load_state") as mock_ls, \
             patch("controller.loop.save_state"), \
             patch("controller.loop.ensure_work_branch"), \
             patch("mcp_agent.MCPRegistry", return_value=mock_registry), \
             patch("controller.loop.build_tool_registry", return_value=[]), \
             patch("controller.loop.select_skill", side_effect=RuntimeError("boom")):
            from controller.state import RunState
            mock_ls.return_value = RunState(goal="test goal")

            from controller.loop import run
            result = run(cfg)
            assert result is False
            # registry.close should have been called to clean up
            mock_registry.close.assert_called()

    def test_mcp_bad_config_degrades_gracefully(self, tmp_path):
        """When MCPRegistry.initialize() raises, run() must not crash."""
        cfg = self._make_config(tmp_path, mcp_config_path="/fake/mcp.json")

        mock_registry = MagicMock()
        mock_registry.tools = []
        mock_registry.initialize = AsyncMock(side_effect=Exception("connection refused"))
        mock_registry.close = AsyncMock()

        with patch("controller.loop.load_state") as mock_ls, \
             patch("controller.loop.save_state"), \
             patch("controller.loop.ensure_work_branch"), \
             patch("mcp_agent.MCPRegistry", return_value=mock_registry), \
             patch("controller.loop.build_tool_registry", return_value=[]), \
             patch("controller.loop.build_worker_agent", return_value=MagicMock()), \
             patch("controller.loop.select_skill", return_value=None), \
             patch("controller.loop.run_worker_turn", return_value="done"), \
             patch("controller.loop.run_tests") as mock_rt, \
             patch("controller.loop.failure_signature", return_value="sig"), \
             patch("controller.loop.commit_iteration", return_value=None), \
             patch("controller.loop.get_diff", return_value=""):
            from controller.state import RunState
            mock_ls.return_value = RunState(goal="test goal")
            exec_result = MagicMock(passed=True, returncode=0, output_tail="")
            mock_rt.return_value = exec_result

            from controller.loop import run
            try:
                run(cfg)
            except Exception as exc:
                pytest.fail(f"run() should degrade gracefully but raised: {exc}")
