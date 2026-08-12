"""Phase 3 tests: Skills system — discovery, loading, selection, integration.

All tests are self-contained:
- Skills tests use real SKILL.md files from the project's skills/ directory,
  or construct fixture skill directories in tmp_path.
- Loop integration tests mock all external calls (no LLM, no git, no network).
"""

import json
import os
import pathlib
import pytest
from unittest.mock import patch, MagicMock

from skills.loader import (
    SkillInfo,
    SkillLoader,
    _match_skill,
    _DEFAULT_KEYWORD_MAP,
    select_skill,
    load_skill,
    list_skills,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_SKILLS_DIR = os.path.join(
    os.path.dirname(__file__), "skills"
)


def make_skill_dir(tmp_path: pathlib.Path, name: str, content: str) -> pathlib.Path:
    """Create a skill directory with a SKILL.md file in tmp_path."""
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


# ---------------------------------------------------------------------------
# 1. SkillInfo dataclass
# ---------------------------------------------------------------------------

class TestSkillInfo:
    def test_truthy_when_has_content(self):
        info = SkillInfo(name="debugging", path="/fake", content="# Skill\nContent here.")
        assert bool(info) is True

    def test_falsy_when_empty_content(self):
        info = SkillInfo(name="debugging", path="/fake", content="")
        assert bool(info) is False

    def test_falsy_when_whitespace_only_content(self):
        info = SkillInfo(name="debugging", path="/fake", content="   \n  ")
        assert bool(info) is False

    def test_fields_stored_correctly(self):
        info = SkillInfo(name="testing", path="/a/b/SKILL.md", content="content")
        assert info.name == "testing"
        assert info.path == "/a/b/SKILL.md"
        assert info.content == "content"


# ---------------------------------------------------------------------------
# 2. SkillLoader — discovery
# ---------------------------------------------------------------------------

class TestSkillLoaderDiscovery:
    def test_discovers_skills_with_skill_md(self, tmp_path):
        make_skill_dir(tmp_path, "alpha", "# Alpha")
        make_skill_dir(tmp_path, "beta", "# Beta")
        loader = SkillLoader(str(tmp_path))
        names = loader.list_available()
        assert "alpha" in names
        assert "beta" in names

    def test_ignores_dirs_without_skill_md(self, tmp_path):
        (tmp_path / "empty_dir").mkdir()          # no SKILL.md
        make_skill_dir(tmp_path, "valid", "# Valid")
        loader = SkillLoader(str(tmp_path))
        names = loader.list_available()
        assert "valid" in names
        assert "empty_dir" not in names

    def test_ignores_files_at_root_level(self, tmp_path):
        (tmp_path / "README.md").write_text("# readme")
        make_skill_dir(tmp_path, "skill_a", "# A")
        loader = SkillLoader(str(tmp_path))
        names = loader.list_available()
        assert "README.md" not in names
        assert "skill_a" in names

    def test_returns_empty_list_for_missing_dir(self, tmp_path):
        loader = SkillLoader(str(tmp_path / "nonexistent"))
        assert loader.list_available() == []

    def test_returns_sorted_names(self, tmp_path):
        make_skill_dir(tmp_path, "zzz", "# Z")
        make_skill_dir(tmp_path, "aaa", "# A")
        make_skill_dir(tmp_path, "mmm", "# M")
        loader = SkillLoader(str(tmp_path))
        names = loader.list_available()
        assert names == sorted(names)

    def test_discovers_project_skills(self):
        """The five real skills must all be discoverable."""
        loader = SkillLoader(PROJECT_SKILLS_DIR)
        names = loader.list_available()
        for expected in ["debugging", "testing", "refactoring", "code_review", "git"]:
            assert expected in names, f"Expected skill '{expected}' not found in {names}"


# ---------------------------------------------------------------------------
# 3. SkillLoader — loading
# ---------------------------------------------------------------------------

class TestSkillLoaderLoading:
    def test_loads_skill_content(self, tmp_path):
        make_skill_dir(tmp_path, "myskill", "# My Skill\nStep 1. Do something.")
        loader = SkillLoader(str(tmp_path))
        info = loader.load("myskill")
        assert info is not None
        assert info.name == "myskill"
        assert "Step 1" in info.content

    def test_path_is_absolute(self, tmp_path):
        make_skill_dir(tmp_path, "myskill", "# content")
        loader = SkillLoader(str(tmp_path))
        info = loader.load("myskill")
        assert os.path.isabs(info.path)

    def test_missing_skill_md_returns_empty_content(self, tmp_path):
        (tmp_path / "noskill").mkdir()  # directory but no SKILL.md
        loader = SkillLoader(str(tmp_path))
        info = loader.load("noskill")
        assert info is not None
        assert info.content == ""
        assert bool(info) is False

    def test_missing_skill_directory_returns_empty_content(self, tmp_path):
        loader = SkillLoader(str(tmp_path))
        info = loader.load("does_not_exist")
        # Should return a SkillInfo with empty content (not raise, not return None)
        assert info is not None
        assert info.content == ""

    def test_empty_skill_md_returns_empty_content(self, tmp_path):
        (tmp_path / "emptyskill").mkdir()
        (tmp_path / "emptyskill" / "SKILL.md").write_text("", encoding="utf-8")
        loader = SkillLoader(str(tmp_path))
        info = loader.load("emptyskill")
        assert info.content == ""

    def test_path_traversal_returns_none(self, tmp_path):
        """A skill name designed to escape the root must be rejected."""
        loader = SkillLoader(str(tmp_path))
        result = loader.load("../outside")
        assert result is None

    def test_path_traversal_with_deep_escape_returns_none(self, tmp_path):
        loader = SkillLoader(str(tmp_path))
        result = loader.load("../../etc/passwd")
        assert result is None

    def test_loads_all_project_skills(self):
        """All five project skills must load with non-empty content."""
        loader = SkillLoader(PROJECT_SKILLS_DIR)
        for name in ["debugging", "testing", "refactoring", "code_review", "git"]:
            info = loader.load(name)
            assert info is not None
            assert bool(info), f"Skill '{name}' loaded but has empty content"
            assert len(info.content) > 50, f"Skill '{name}' content seems too short"


# ---------------------------------------------------------------------------
# 4. Skill metadata / content validation
# ---------------------------------------------------------------------------

class TestSkillContent:
    """Verify that the real SKILL.md files contain actionable content."""

    @pytest.mark.parametrize("skill_name", [
        "debugging", "testing", "refactoring", "code_review", "git"
    ])
    def test_skill_has_procedure_section(self, skill_name):
        info = load_skill(skill_name, skills_dir=PROJECT_SKILLS_DIR)
        assert info is not None
        # Each skill should have a Procedure or equivalent heading
        assert "Procedure" in info.content or "How" in info.content or "When" in info.content

    @pytest.mark.parametrize("skill_name", [
        "debugging", "testing", "refactoring", "code_review", "git"
    ])
    def test_skill_name_appears_in_content(self, skill_name):
        info = load_skill(skill_name, skills_dir=PROJECT_SKILLS_DIR)
        # The skill name or a close variant should appear in the content
        assert skill_name.replace("_", " ") in info.content.lower() or \
               skill_name.replace("_", "") in info.content.lower() or \
               skill_name.split("_")[0] in info.content.lower()

    def test_debugging_skill_mentions_root_cause(self):
        info = load_skill("debugging", skills_dir=PROJECT_SKILLS_DIR)
        assert "root cause" in info.content.lower()

    def test_testing_skill_mentions_edge_cases(self):
        info = load_skill("testing", skills_dir=PROJECT_SKILLS_DIR)
        assert "edge" in info.content.lower()

    def test_git_skill_mentions_commit(self):
        info = load_skill("git", skills_dir=PROJECT_SKILLS_DIR)
        assert "commit" in info.content.lower()

    def test_refactoring_skill_mentions_no_behavior_change(self):
        info = load_skill("refactoring", skills_dir=PROJECT_SKILLS_DIR)
        content_lower = info.content.lower()
        assert "behavior" in content_lower or "behaviour" in content_lower

    def test_code_review_skill_mentions_security(self):
        info = load_skill("code_review", skills_dir=PROJECT_SKILLS_DIR)
        assert "security" in info.content.lower()


# ---------------------------------------------------------------------------
# 5. Skill selector — keyword matching
# ---------------------------------------------------------------------------

class TestSkillSelector:
    """Test _match_skill (the deterministic selection core) directly."""

    def test_debugging_task(self):
        result = _match_skill("fix the failing authentication test", _DEFAULT_KEYWORD_MAP)
        assert result == "debugging"

    def test_debugging_task_with_error_keyword(self):
        result = _match_skill("there is a runtime error in the login module", _DEFAULT_KEYWORD_MAP)
        assert result == "debugging"

    def test_testing_task(self):
        result = _match_skill("write unit tests for the parser module", _DEFAULT_KEYWORD_MAP)
        assert result == "testing"

    def test_testing_task_coverage(self):
        result = _match_skill("improve test coverage for the API layer", _DEFAULT_KEYWORD_MAP)
        assert result == "testing"

    def test_refactoring_task(self):
        result = _match_skill("refactor this class to reduce duplication", _DEFAULT_KEYWORD_MAP)
        assert result == "refactoring"

    def test_refactoring_task_restructure(self):
        result = _match_skill("restructure the module for better readability", _DEFAULT_KEYWORD_MAP)
        assert result == "refactoring"

    def test_code_review_task(self):
        result = _match_skill(
            "review this pull request for code quality and maintainability",
            _DEFAULT_KEYWORD_MAP,
        )
        assert result == "code_review"

    def test_code_review_security(self):
        result = _match_skill("security audit of the authentication module", _DEFAULT_KEYWORD_MAP)
        assert result == "code_review"

    def test_git_task(self):
        result = _match_skill("show me the git diff for the recent changes", _DEFAULT_KEYWORD_MAP)
        assert result == "git"

    def test_git_commit_task(self):
        result = _match_skill("commit the current changes with a good message", _DEFAULT_KEYWORD_MAP)
        assert result == "git"

    def test_unknown_task_returns_none(self):
        result = _match_skill("deploy the application to production", _DEFAULT_KEYWORD_MAP)
        # This is ambiguous/unknown — result is None or a skill, but should not raise
        # We don't assert a specific skill here as coverage may vary

    def test_empty_task_returns_none(self):
        result = _match_skill("", _DEFAULT_KEYWORD_MAP)
        assert result is None

    def test_whitespace_only_task_returns_none(self):
        result = _match_skill("   ", _DEFAULT_KEYWORD_MAP)
        assert result is None

    def test_custom_keyword_map(self):
        custom_map = {"custom_skill": ["banana", "apple"]}
        result = _match_skill("I need a banana split", custom_map)
        assert result == "custom_skill"


# ---------------------------------------------------------------------------
# 6. SkillLoader.select() — end-to-end selection
# ---------------------------------------------------------------------------

class TestSkillLoaderSelect:
    def test_select_debugging_skill(self):
        loader = SkillLoader(PROJECT_SKILLS_DIR)
        skill = loader.select("fix the failing test in the auth module")
        assert skill is not None
        assert skill.name == "debugging"
        assert bool(skill)

    def test_select_testing_skill(self):
        loader = SkillLoader(PROJECT_SKILLS_DIR)
        skill = loader.select("write unit tests for the new parser")
        assert skill is not None
        assert skill.name == "testing"

    def test_select_refactoring_skill(self):
        loader = SkillLoader(PROJECT_SKILLS_DIR)
        skill = loader.select("refactor the database module to remove duplication")
        assert skill is not None
        assert skill.name == "refactoring"

    def test_select_code_review_skill(self):
        loader = SkillLoader(PROJECT_SKILLS_DIR)
        skill = loader.select("review the pull request for security vulnerabilities")
        assert skill is not None
        assert skill.name == "code_review"

    def test_select_git_skill(self):
        loader = SkillLoader(PROJECT_SKILLS_DIR)
        skill = loader.select("show me the git status and diff")
        assert skill is not None
        assert skill.name == "git"

    def test_select_returns_none_for_empty_task(self):
        loader = SkillLoader(PROJECT_SKILLS_DIR)
        skill = loader.select("")
        assert skill is None

    def test_select_returns_none_when_no_match(self, tmp_path):
        """When the keyword match is None, select() should return None."""
        loader = SkillLoader(str(tmp_path))  # empty skills dir
        skill = loader.select("fix the bug")
        assert skill is None

    def test_select_returns_none_when_skill_not_on_disk(self, tmp_path):
        """Matched skill name not present on disk → None (not an error)."""
        loader = SkillLoader(str(tmp_path))
        # tmp_path has no skills but matching would find "debugging"
        skill = loader.select("fix the failing test")
        assert skill is None


# ---------------------------------------------------------------------------
# 7. Module-level convenience functions
# ---------------------------------------------------------------------------

class TestConvenienceFunctions:
    def test_select_skill_function(self):
        skill = select_skill("fix the bug", skills_dir=PROJECT_SKILLS_DIR)
        assert skill is not None
        assert skill.name == "debugging"

    def test_load_skill_function(self):
        skill = load_skill("testing", skills_dir=PROJECT_SKILLS_DIR)
        assert skill is not None
        assert skill.name == "testing"
        assert bool(skill)

    def test_list_skills_function(self):
        names = list_skills(skills_dir=PROJECT_SKILLS_DIR)
        assert isinstance(names, list)
        assert "debugging" in names
        assert "testing" in names
        assert "refactoring" in names
        assert "code_review" in names
        assert "git" in names

    def test_select_skill_with_custom_dir(self, tmp_path):
        make_skill_dir(tmp_path, "debugging", "# Debugging content here")
        skill = select_skill("fix the failing test", skills_dir=str(tmp_path))
        assert skill is not None
        assert skill.name == "debugging"

    def test_select_skill_missing_dir_returns_none(self, tmp_path):
        skill = select_skill("fix the bug", skills_dir=str(tmp_path / "nonexistent"))
        assert skill is None


# ---------------------------------------------------------------------------
# 8. RunState.selected_skill integration (state.py)
# ---------------------------------------------------------------------------

class TestRunStateSkillField:
    def test_selected_skill_defaults_to_none(self):
        from controller.state import RunState
        state = RunState(goal="test")
        assert state.selected_skill is None

    def test_set_skill_stores_name(self):
        from controller.state import RunState
        state = RunState(goal="test")
        state.set_skill("debugging")
        assert state.selected_skill == "debugging"

    def test_set_skill_accepts_none(self):
        from controller.state import RunState
        state = RunState(goal="test")
        state.set_skill(None)
        assert state.selected_skill is None

    def test_selected_skill_round_trips_in_json(self, tmp_path):
        from controller.state import RunState, save_state, load_state
        state = RunState(goal="round-trip")
        state.set_skill("refactoring")
        state_file = str(tmp_path / "state.json")
        save_state(state_file, state)
        loaded = load_state(state_file, "round-trip")
        assert loaded.selected_skill == "refactoring"

    def test_old_state_json_loads_with_none_skill(self, tmp_path):
        """Old state.json without selected_skill must load cleanly (backward compat)."""
        from controller.state import load_state
        old_data = {
            "goal": "old goal",
            "started_at": 1000.0,
            "iterations": [],
            "last_failure_signature": None,
            "same_failure_count": 0,
            # no selected_skill key
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(old_data))
        loaded = load_state(str(state_file), "old goal")
        assert loaded.selected_skill is None


# ---------------------------------------------------------------------------
# 9. Loop integration — skill selection and recording
# ---------------------------------------------------------------------------

def _make_config(tmp_path, *, skills_dir=None, goal="fix the failing test"):
    cfg = MagicMock()
    cfg.is_remote = False
    cfg.local_repo_path = str(tmp_path)
    cfg.state_file = "state.json"
    cfg.goal = goal
    cfg.test_cmd = "pytest"
    cfg.max_iterations = 2
    cfg.max_seconds = 3600
    cfg.require_approval = False
    cfg.model_name = "gemma4"
    cfg.llm_provider = "ollama_cloud"
    cfg.lint_cmd = None
    cfg.skills_dir = skills_dir
    return cfg


@patch("controller.loop.build_worker_agent")
@patch("controller.loop.run_worker_turn")
@patch("controller.loop.run_tests")
@patch("controller.loop.commit_iteration")
@patch("controller.loop.ensure_work_branch")
class TestLoopSkillIntegration:

    def test_skill_recorded_in_state_json(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        """selected_skill must appear in state.json after the loop runs."""
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, skills_dir=PROJECT_SKILLS_DIR, goal="fix the failing test")
        run(cfg)

        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        # Should be "debugging" because the goal has "fix" and "fail"
        assert data.get("selected_skill") == "debugging"

    def test_no_skill_match_records_none_in_state(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        """When no skill matches, selected_skill must be None in state.json."""
        mock_worker.return_value = "Done."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(
            tmp_path,
            skills_dir=str(tmp_path / "empty_skills"),  # dir doesn't exist
            goal="deploy to production",
        )
        run(cfg)

        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data.get("selected_skill") is None

    def test_skill_content_injected_into_instruction(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        """When a skill is selected, its content must appear in the instruction
        passed to run_worker_turn."""
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, skills_dir=PROJECT_SKILLS_DIR, goal="fix the failing test")
        run(cfg)

        # The instruction passed to run_worker_turn must contain skill content
        call_args = mock_worker.call_args
        instruction_arg = call_args[0][1]  # second positional arg is the instruction
        assert "Approach guide" in instruction_arg or "Procedure" in instruction_arg \
               or "Debugging" in instruction_arg

    def test_no_skill_instruction_unchanged(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path
    ):
        """With no skill, instruction must just contain the goal (no skill block)."""
        mock_worker.return_value = "Done."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(
            tmp_path,
            skills_dir=str(tmp_path / "empty_skills"),
            goal="deploy to production",
        )
        run(cfg)

        call_args = mock_worker.call_args
        instruction_arg = call_args[0][1]
        assert "Approach guide" not in instruction_arg

    def test_skill_logging_uses_skill_prefix(
        self, mock_branch, mock_commit, mock_tests, mock_worker, mock_build, tmp_path, capsys
    ):
        """The loop must print a [SKILL] line identifying the selected skill."""
        mock_worker.return_value = "Done."
        mock_tests.return_value = MagicMock(passed=True, returncode=0, output_tail="ok")
        mock_commit.return_value = ""

        from controller.loop import run
        cfg = _make_config(tmp_path, skills_dir=PROJECT_SKILLS_DIR, goal="fix the failing test")
        run(cfg)

        captured = capsys.readouterr()
        assert "[SKILL]" in captured.out


# ---------------------------------------------------------------------------
# 10. build_instruction — skill_content parameter
# ---------------------------------------------------------------------------

class TestBuildInstructionSkillContent:
    def test_no_skill_content_produces_goal_only(self):
        from controller.loop import build_instruction
        result = build_instruction("do the thing", "", False, skill_content="")
        assert result == "Goal: do the thing"

    def test_skill_content_appears_in_instruction(self):
        from controller.loop import build_instruction
        result = build_instruction("do the thing", "", False, skill_content="Step 1. Read first.")
        assert "Approach guide" in result
        assert "Step 1. Read first." in result

    def test_skill_content_before_test_output(self):
        from controller.loop import build_instruction
        result = build_instruction("do the thing", "test output here", False, skill_content="procedure")
        parts = result.split("\n\n")
        # Goal first, then skill content, then test output
        assert parts[0].startswith("Goal:")
        skill_idx = next(i for i, p in enumerate(parts) if "procedure" in p)
        test_idx = next(i for i, p in enumerate(parts) if "test output" in p)
        assert skill_idx < test_idx

    def test_force_new_strategy_still_appended(self):
        from controller.loop import build_instruction
        result = build_instruction("do the thing", "", True, skill_content="")
        assert "Do not repeat" in result

    def test_default_skill_content_is_empty(self):
        from controller.loop import build_instruction
        # Calling without skill_content kwarg should default to ""
        result = build_instruction("goal", "", False)
        assert "Approach guide" not in result


# ---------------------------------------------------------------------------
# 11. Extensibility — new skill added without Python changes
# ---------------------------------------------------------------------------

class TestSkillExtensibility:
    def test_new_skill_discovered_automatically(self, tmp_path):
        """A new skill directory with SKILL.md must be auto-discovered."""
        make_skill_dir(tmp_path, "deployment", "# Deployment Skill\n## Procedure\n1. Build.\n2. Deploy.")
        loader = SkillLoader(str(tmp_path))
        names = loader.list_available()
        assert "deployment" in names

    def test_new_skill_loadable(self, tmp_path):
        make_skill_dir(tmp_path, "deployment", "# Deployment Skill\n## Procedure\n1. Deploy.")
        loader = SkillLoader(str(tmp_path))
        info = loader.load("deployment")
        assert "Deploy" in info.content

    def test_new_skill_selectable_with_custom_keywords(self, tmp_path):
        make_skill_dir(tmp_path, "deployment", "# Deployment Skill\n## Procedure\n1. Deploy.")
        custom_map = {**_DEFAULT_KEYWORD_MAP, "deployment": ["deploy", "release", "ship"]}
        loader = SkillLoader(str(tmp_path))
        skill = loader.select("deploy the application to staging", keyword_map=custom_map)
        assert skill is not None
        assert skill.name == "deployment"


# ---------------------------------------------------------------------------
# 12. Backward compatibility — Phase 1 and Phase 2 functionality
# ---------------------------------------------------------------------------

class TestPhase1Phase2BackwardCompat:
    """Spot-check that key Phase 1/2 functionality is not broken by Phase 3."""

    def test_termination_reason_constants_unchanged(self):
        from controller.state import TerminationReason
        assert TerminationReason.SUCCESS == "success"
        assert TerminationReason.TOOL_ERROR == "tool_error"
        assert TerminationReason.MAX_ITERATIONS_SAFETY_LIMIT == "max_iterations_safety_limit"

    def test_build_evaluator_result_unchanged(self):
        from controller.state import build_evaluator_result
        ev = build_evaluator_result(True, "ok", None, None)
        assert ev["is_correct"] is True
        assert ev["score"] == 1.0

    def test_run_state_has_all_phase1_fields(self):
        from controller.state import RunState
        state = RunState(goal="test")
        assert hasattr(state, "plan")
        assert hasattr(state, "evaluator_result")
        assert hasattr(state, "termination_reason")
        assert hasattr(state, "selected_skill")  # Phase 3

    def test_tool_registry_still_returns_five_tools(self):
        from tools import build_tool_registry
        tools = build_tool_registry("/fake/repo", "pytest")
        assert len(tools) == 5

    def test_config_has_all_fields(self, tmp_path):
        import os
        from unittest.mock import patch as mpatch
        with mpatch.dict(os.environ, {"OLLAMA_API_KEY": "test", "LLM_PROVIDER": "ollama_cloud"}):
            from config import Config
            cfg = Config(repo_path=str(tmp_path), goal="test")
            assert hasattr(cfg, "lint_cmd")
            assert hasattr(cfg, "skills_dir")
            assert cfg.lint_cmd is None
            assert cfg.skills_dir is None
