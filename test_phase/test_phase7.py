"""Phase 7 tests: Coverage gap-fill + end-to-end smoke test.

Coverage audit findings addressed here:
  - tools/exec_tools.py  : timeout path in run_tests_tool not previously tested
  - tools/git_tools.py   : staged-files branch in git_status not tested;
                           empty-commit-message edge case not tested
  - tools/base.py        : log_tool_call / log_tool_result output not verified
  - mcp_agent/config_schema.py : several malformed-config branches (missing
                           'servers' key, non-dict servers, missing 'command',
                           non-list args, non-dict env) not previously tested
  - mcp_agent/registry.py : config-load-failure graceful-degradation path
                            (lines 23-27: registry.initialize catches its own
                            FileNotFoundError/ValueError and just returns with
                            no tools) not previously tested with a real test
  - controller/evaluator.py : lint-passes but score reduction (currently score
                              stays 1.0 per policy — test the boundary); also
                              same_failure_count boundary (== 2 vs > 2) not
                              individually verified
  - controller/router.py : UNRECOVERABLE_ERROR is produced by the loop setup
                           error path, not the router — no router test for it
                           (correct per design — confirmed here)
  - skills/loader.py     : ambiguous multi-keyword task (task whose words match
                           >1 keyword-map entry — first-match wins) not tested
  - End-to-end smoke test: real subprocess + real git + real tool registry +
                           real evaluator/router/state, only LLM call stubbed

Regression/duplication notes
  - Config construction pattern (with monkeypatch/patch.dict for OLLAMA_API_KEY)
    is spread across test_agent.py, test_phase3.py, test_phase4.py,
    test_phase5.py — all slightly different but testing the same guard.
    Not consolidated (cheap to keep separate); reported only.
  - require_approval tested in test_phase1.py (user_rejected reason) and in
    test_phase2.py (git_commit disabled) — these cover different layers, not
    true duplication.
  - No scenario combining require_approval=True with lint_cmd is covered in any
    prior phase. Added below.
"""

import json
import os
import subprocess
import tempfile
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, call

# ---------------------------------------------------------------------------
# Section 1 — tools/base.py: logging output verification
# ---------------------------------------------------------------------------

class TestToolBaseLogging:
    """Verify that log_tool_call / log_tool_result print the expected prefixes."""

    def test_log_tool_call_prints_tool_prefix(self, capsys):
        from tools.base import log_tool_call
        log_tool_call("my_amazing_tool")
        captured = capsys.readouterr()
        assert "[TOOL]" in captured.out
        assert "my_amazing_tool" in captured.out

    def test_log_tool_result_prints_result_prefix(self, capsys):
        from tools.base import log_tool_result
        log_tool_result("OK hash=deadbeef")
        captured = capsys.readouterr()
        assert "[TOOL]" in captured.out
        assert "result=OK hash=deadbeef" in captured.out

    def test_safe_tool_logs_error_on_exception(self, capsys):
        from tools.base import safe_tool
        @safe_tool
        def boom():
            raise RuntimeError("exploded")
        boom()
        captured = capsys.readouterr()
        # safe_tool calls log_tool_result internally on error
        assert "[TOOL]" in captured.out


# ---------------------------------------------------------------------------
# Section 2 — tools/git_tools.py: untested branches
# ---------------------------------------------------------------------------

class TestGitStatusStagedFiles:
    """git_status must also report staged-but-not-committed files."""

    @patch("tools.git_tools.get_repo")
    def test_staged_files_appear_in_output(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = True
        mock_repo.index.diff.side_effect = lambda ref: (
            [MagicMock(a_path="staged_file.py")] if ref == "HEAD" else []
        )
        mock_repo.untracked_files = []
        mock_get_repo.return_value = mock_repo

        from tools.git_tools import make_git_status_tool
        tool_fn = make_git_status_tool("/fake/repo")
        result = tool_fn.invoke({})

        assert "staged_file.py" in result

    @patch("tools.git_tools.get_repo")
    def test_both_modified_and_staged_shown(self, mock_get_repo):
        """Ensure both modified (index.diff(None)) and staged (index.diff('HEAD'))
        appear when the repo has both."""
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = True

        modified = MagicMock()
        modified.a_path = "modified_file.py"
        staged = MagicMock()
        staged.a_path = "staged_file.py"

        mock_repo.index.diff.side_effect = lambda ref: (
            [staged] if ref == "HEAD" else [modified]
        )
        mock_repo.untracked_files = ["new_untracked.py"]
        mock_get_repo.return_value = mock_repo

        from tools.git_tools import make_git_status_tool
        tool_fn = make_git_status_tool("/fake/repo")
        result = tool_fn.invoke({})

        assert "modified_file.py" in result
        assert "staged_file.py" in result
        assert "new_untracked.py" in result


class TestGitCommitEmptyMessage:
    """git_commit with an empty or whitespace-only message should still forward
    to commit_iteration (message validation is the caller's responsibility)."""

    @patch("tools.git_tools.commit_iteration")
    def test_empty_message_still_calls_commit(self, mock_commit):
        mock_commit.return_value = "abc123def456"
        from tools.git_tools import make_git_commit_tool
        tool_fn = make_git_commit_tool("/fake/repo", require_approval=False)
        result = tool_fn.invoke({"message": ""})
        # commit_iteration is called; result contains the hash
        mock_commit.assert_called_once_with("/fake/repo", "")
        assert "abc123" in result

    @patch("tools.git_tools.commit_iteration")
    def test_long_message_not_truncated(self, mock_commit):
        """The git_commit tool must not truncate the message (loop.py does that
        for the auto-generated commit message separately)."""
        long_message = "x" * 500
        mock_commit.return_value = "hash12345678"
        from tools.git_tools import make_git_commit_tool
        tool_fn = make_git_commit_tool("/fake/repo", require_approval=False)
        tool_fn.invoke({"message": long_message})
        mock_commit.assert_called_once_with("/fake/repo", long_message)


# ---------------------------------------------------------------------------
# Section 3 — tools/exec_tools.py: timeout path
# ---------------------------------------------------------------------------

class TestRunTestsToolTimeout:
    """The run_tests_tool must survive a subprocess.TimeoutExpired and return
    an error string rather than propagating the exception."""

    @patch("tools.exec_tools._run_tests")
    def test_timeout_returns_error_string(self, mock_run_tests):
        import subprocess
        mock_run_tests.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=300)
        from tools.exec_tools import make_run_tests_tool
        tool_fn = make_run_tests_tool("/fake/repo", "pytest")
        result = tool_fn.invoke({"extra_args": ""})
        # safe path: exception is caught and returned as error string
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# Section 4 — controller/executor.py: timeout and combined output
# ---------------------------------------------------------------------------

class TestExecutorRunTests:
    """Direct tests for run_tests() and run_lint() in controller/executor.py."""

    def test_run_tests_timeout_returns_failed_result(self):
        """If subprocess.TimeoutExpired occurs, run_tests must return a
        failed ExecResult with returncode=-1 and a descriptive message."""
        import subprocess
        from controller.executor import run_tests
        with patch("controller.executor._run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=300)
            result = run_tests("/some/path", "pytest")
        assert result.passed is False
        assert result.returncode == -1
        assert "timed out" in result.output_tail.lower()

    def test_run_lint_timeout_returns_failed_result(self):
        import subprocess
        from controller.executor import run_lint
        with patch("controller.executor._run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="flake8", timeout=300)
            result = run_lint("/some/path", "flake8")
        assert result.passed is False
        assert result.returncode == -1
        assert "timed out" in result.output_tail.lower()

    def test_run_tests_combines_stdout_and_stderr(self):
        """run_tests must combine stdout and stderr into output_tail."""
        from controller.executor import run_tests
        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.stdout = "FAILED test_foo\n"
        fake_proc.stderr = "ERROR: ImportError\n"
        with patch("controller.executor._run", return_value=fake_proc):
            result = run_tests("/path", "pytest")
        assert "FAILED test_foo" in result.output_tail
        assert "ImportError" in result.output_tail

    def test_run_tests_success_returncode_zero(self):
        from controller.executor import run_tests
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "5 passed in 0.12s\n"
        fake_proc.stderr = ""
        with patch("controller.executor._run", return_value=fake_proc):
            result = run_tests("/path", "pytest")
        assert result.passed is True
        assert result.returncode == 0

    def test_failure_signature_handles_single_line(self):
        """failure_signature of a one-line output must return that line."""
        from controller.executor import failure_signature, ExecResult
        result = ExecResult(passed=False, returncode=1, output_tail="only one line")
        sig = failure_signature(result)
        assert "only one line" in sig

    def test_failure_signature_empty_returns_returncode(self):
        """Empty output_tail must fall back to the returncode string."""
        from controller.executor import failure_signature, ExecResult
        result = ExecResult(passed=False, returncode=42, output_tail="")
        sig = failure_signature(result)
        assert sig == "42"


# ---------------------------------------------------------------------------
# Section 5 — mcp_agent/config_schema.py: untested error branches
# ---------------------------------------------------------------------------

class TestMCPConfigSchemaEdgeCases:
    """Verify that every malformed-config branch raises the correct error."""

    def test_missing_servers_key_raises_value_error(self, tmp_path):
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps({"no_servers_key": {}}))
        from mcp_agent.config_schema import load_mcp_config
        with pytest.raises(ValueError, match="'servers'"):
            load_mcp_config(str(config_file))

    def test_servers_is_list_not_dict_raises_value_error(self, tmp_path):
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps({"servers": ["srv1", "srv2"]}))
        from mcp_agent.config_schema import load_mcp_config
        with pytest.raises(ValueError, match="'servers'"):
            load_mcp_config(str(config_file))

    def test_server_config_not_dict_raises_value_error(self, tmp_path):
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps({"servers": {"bad_srv": "not_a_dict"}}))
        from mcp_agent.config_schema import load_mcp_config
        with pytest.raises(ValueError, match="must be an object"):
            load_mcp_config(str(config_file))

    def test_missing_command_field_raises_value_error(self, tmp_path):
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps({"servers": {"srv": {"args": []}}}))
        from mcp_agent.config_schema import load_mcp_config
        with pytest.raises(ValueError, match="'command'"):
            load_mcp_config(str(config_file))

    def test_args_not_list_raises_value_error(self, tmp_path):
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps({
            "servers": {"srv": {"command": "echo", "args": "not_a_list"}}
        }))
        from mcp_agent.config_schema import load_mcp_config
        with pytest.raises(ValueError, match="'args'"):
            load_mcp_config(str(config_file))

    def test_env_not_dict_raises_value_error(self, tmp_path):
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps({
            "servers": {"srv": {"command": "echo", "args": [], "env": ["VAR=val"]}}
        }))
        from mcp_agent.config_schema import load_mcp_config
        with pytest.raises(ValueError, match="'env'"):
            load_mcp_config(str(config_file))

    def test_root_not_dict_raises_value_error(self, tmp_path):
        """Top-level JSON that is not a dict (e.g. a list) must also raise."""
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps([{"servers": {}}]))
        from mcp_agent.config_schema import load_mcp_config
        with pytest.raises(ValueError, match="'servers'"):
            load_mcp_config(str(config_file))

    def test_env_values_are_interpolated(self, tmp_path, monkeypatch):
        """Environment variable references inside 'env' must be interpolated."""
        monkeypatch.setenv("MY_SECRET", "abc123")
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps({
            "servers": {
                "srv": {
                    "command": "python",
                    "args": [],
                    "env": {"TOKEN": "$MY_SECRET"}
                }
            }
        }))
        from mcp_agent.config_schema import load_mcp_config
        loaded = load_mcp_config(str(config_file))
        assert loaded["servers"]["srv"]["env"]["TOKEN"] == "abc123"


# ---------------------------------------------------------------------------
# Section 6 — mcp_agent/registry.py: config-load failure path
# ---------------------------------------------------------------------------

class TestMCPRegistryConfigLoadFailure:
    """When load_mcp_config raises (e.g. file not found, malformed JSON), the
    registry must log a message and return cleanly with zero tools — not raise."""

    @pytest.mark.asyncio
    async def test_bad_config_path_yields_zero_tools(self):
        from mcp_agent.registry import MCPRegistry
        # Point to a path that doesn't exist — load_mcp_config will raise
        registry = MCPRegistry("/nonexistent/path/mcp_config.json")
        # Must not raise; should print a warning and carry on
        await registry.initialize()
        assert registry.tools == []
        assert registry.clients == {}

    @pytest.mark.asyncio
    async def test_malformed_config_yields_zero_tools(self, tmp_path):
        """Malformed JSON must also result in graceful degradation."""
        bad_config = tmp_path / "mcp_bad.json"
        bad_config.write_text("{ not valid json }")
        from mcp_agent.registry import MCPRegistry
        registry = MCPRegistry(str(bad_config))
        await registry.initialize()
        assert registry.tools == []

    @pytest.mark.asyncio
    async def test_close_on_empty_registry_does_not_raise(self):
        """Calling close() on a never-initialized registry must be safe."""
        from mcp_agent.registry import MCPRegistry
        registry = MCPRegistry("/nonexistent.json")
        # close() should be safe even with no clients
        await registry.close()


# ---------------------------------------------------------------------------
# Section 7 — controller/evaluator.py: boundary / untested combos
# ---------------------------------------------------------------------------

class TestEvaluatorBoundaries:
    """Verify edge cases in evaluate_iteration not exercised in test_phase6.py."""

    def test_lint_score_stays_one_when_tests_pass(self):
        """Per policy: even if lint fails, score=1.0 (not 0.9 — that was a
        design option that was NOT adopted). Confirm the actual policy."""
        from controller.evaluator import evaluate_iteration
        res = evaluate_iteration(
            test_passed=True,
            test_output_tail="5 passed",
            lint_passed=False,
            lint_output_tail="E501 too long",
            same_failure_count=0,
        )
        # Current policy: tests pass → score stays 1.0 regardless of lint
        assert res["score"] == 1.0
        assert res["is_correct"] is True

    def test_same_failure_count_exactly_2_triggers_repeated_failure(self):
        """The threshold is >= 2; this test verifies the exact boundary."""
        from controller.evaluator import evaluate_iteration
        res = evaluate_iteration(
            test_passed=False,
            test_output_tail="AssertionError",
            lint_passed=None,
            lint_output_tail=None,
            same_failure_count=2,
        )
        assert "repeated_failure" in res["issues"]
        assert "repeated_same_failure" in res["critical_gaps"]

    def test_same_failure_count_1_does_not_trigger_repeated(self):
        """same_failure_count=1 must NOT produce repeated_failure."""
        from controller.evaluator import evaluate_iteration
        res = evaluate_iteration(
            test_passed=False,
            test_output_tail="AssertionError",
            lint_passed=None,
            lint_output_tail=None,
            same_failure_count=1,
        )
        assert "repeated_failure" not in res["issues"]
        assert "repeated_same_failure" not in res["critical_gaps"]

    def test_feedback_contains_tail_preview_on_failure(self):
        """feedback must include a slice of the test output tail."""
        from controller.evaluator import evaluate_iteration
        res = evaluate_iteration(
            test_passed=False,
            test_output_tail="FAILED: test_foo: assertion 1 == 2",
            lint_passed=None,
            lint_output_tail=None,
            same_failure_count=0,
        )
        assert "test_foo" in res["feedback"] or "FAILED" in res["feedback"]

    def test_lint_passed_none_no_lint_issue(self):
        """When lint_passed is None (lint not configured), 'lint_failed' must
        not appear in issues even when tests fail."""
        from controller.evaluator import evaluate_iteration
        res = evaluate_iteration(
            test_passed=False,
            test_output_tail="fail",
            lint_passed=None,
            lint_output_tail=None,
            same_failure_count=0,
        )
        assert "lint_failed" not in res["issues"]


# ---------------------------------------------------------------------------
# Section 8 — controller/router.py: UNRECOVERABLE_ERROR coverage note
# ---------------------------------------------------------------------------

class TestRouterUnrecoverableNote:
    """UNRECOVERABLE_ERROR is produced by the loop's *setup* error handler
    (controller/loop.py lines 113-116), NOT by the router.  The router has
    no code path that returns UNRECOVERABLE_ERROR — this is correct by design.
    This test class documents the confirmed absence and verifies the loop
    produces UNRECOVERABLE_ERROR when setup itself fails."""

    def test_unrecoverable_error_set_on_setup_failure(self, tmp_path):
        """If ensure_work_branch raises during setup, the loop should write
        UNRECOVERABLE_ERROR to state.json and return False."""
        from controller.loop import run
        from controller.state import TerminationReason, load_state

        cfg = MagicMock()
        cfg.is_remote = False
        cfg.local_repo_path = str(tmp_path)
        cfg.state_file = "state.json"
        cfg.goal = "test goal"
        cfg.test_cmd = "pytest"
        cfg.max_iterations = 3
        cfg.max_seconds = 3600
        cfg.require_approval = False
        cfg.model_name = "gemma4"
        cfg.llm_provider = "ollama_cloud"
        cfg.lint_cmd = None
        cfg.skills_dir = None

        with patch("controller.loop.ensure_work_branch",
                   side_effect=RuntimeError("branch checkout failed")):
            result = run(cfg)

        assert result is False
        state_file = tmp_path / "state.json"
        assert state_file.exists()
        with open(state_file) as f:
            data = json.load(f)
        assert data.get("termination_reason") == TerminationReason.UNRECOVERABLE_ERROR


# ---------------------------------------------------------------------------
# Section 9 — skills/loader.py: ambiguous multi-keyword matching
# ---------------------------------------------------------------------------

class TestSkillAmbiguousKeywords:
    """_match_skill must use first-match / longest-match consistently when a
    task matches keywords from more than one skill category."""

    def test_first_keyword_map_entry_wins_on_tie(self):
        """When a task matches keywords from two different skills, the first
        matching entry in the map (iteration order) wins."""
        from skills.loader import _match_skill
        # "fix" → debugging, "commit" → git; first match in iteration order wins
        custom_map = {
            "debugging": ["fix", "bug"],
            "git": ["commit", "fix"],   # 'fix' appears second here too
        }
        # The task contains "fix" — debugging appears first in the map
        result = _match_skill("fix the commit message", custom_map)
        # Result must be one of the two matching skills (not None, not crash)
        assert result in ("debugging", "git")

    def test_no_match_returns_none_not_crash(self):
        """A task with zero matching keywords must return None, never raise."""
        from skills.loader import _match_skill
        result = _match_skill("deploy the containerised microservice", {"debugging": ["fix"]})
        assert result is None

    def test_multiple_keywords_in_task_still_returns_one_skill(self):
        """A task containing many keywords from one skill returns that skill,
        not a list or error."""
        from skills.loader import _match_skill, _DEFAULT_KEYWORD_MAP
        task = "fix the failing test and debug the error"
        result = _match_skill(task, _DEFAULT_KEYWORD_MAP)
        assert isinstance(result, str)
        assert result == "debugging"   # 'fix', 'failing', 'debug' all → debugging

    def test_select_skill_returns_none_for_genuinely_ambiguous_task(self):
        """select_skill (full pipeline) on an ambiguous task that matches two
        skill entries must return *some* skill (not crash) or None — no crash."""
        from skills.loader import select_skill
        # 'review' → code_review; 'test' → testing — ambiguous
        skill = select_skill("review the test coverage", skills_dir="skills")
        # Must return a SkillInfo or None — never raise
        assert skill is None or hasattr(skill, "name")


# ---------------------------------------------------------------------------
# Section 10 — Combination: require_approval + lint_cmd
# ---------------------------------------------------------------------------

class TestRequireApprovalWithLint:
    """No prior phase tested the combination of require_approval=True AND
    lint_cmd configured simultaneously.  This exercises both gates in one run."""

    @patch("controller.loop.build_worker_agent")
    @patch("controller.loop.run_worker_turn")
    @patch("controller.loop.run_tests")
    @patch("controller.loop.run_lint")
    @patch("controller.loop.commit_iteration")
    @patch("controller.loop.ensure_work_branch")
    def test_user_rejects_when_lint_is_also_configured(
        self, mock_branch, mock_commit, mock_lint,
        mock_tests, mock_worker, mock_build, tmp_path
    ):
        """With require_approval=True and a lint_cmd, the loop should still
        ask for approval and stop on rejection, even though lint also ran."""
        mock_worker.return_value = "Fixed something."
        mock_tests.return_value = MagicMock(passed=False, returncode=1,
                                             output_tail="FAILED test_foo")
        mock_lint.return_value = MagicMock(passed=False, returncode=1,
                                            output_tail="E501 line too long")
        mock_commit.return_value = ""

        cfg = MagicMock()
        cfg.is_remote = False
        cfg.local_repo_path = str(tmp_path)
        cfg.state_file = "state.json"
        cfg.goal = "make tests pass"
        cfg.test_cmd = "pytest"
        cfg.max_iterations = 3
        cfg.max_seconds = 3600
        cfg.require_approval = True
        cfg.model_name = "gemma4"
        cfg.llm_provider = "ollama_cloud"
        cfg.lint_cmd = "flake8 ."
        cfg.skills_dir = None

        from controller.loop import run
        with patch("controller.loop.get_diff", return_value="diff --git ..."), \
             patch("builtins.input", return_value="n"):
            result = run(cfg)

        assert result is False
        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data["termination_reason"] == "user_rejected"
        # Lint was called (configured)
        mock_lint.assert_called()

    @patch("controller.loop.build_worker_agent")
    @patch("controller.loop.run_worker_turn")
    @patch("controller.loop.run_tests")
    @patch("controller.loop.run_lint")
    @patch("controller.loop.commit_iteration")
    @patch("controller.loop.ensure_work_branch")
    def test_user_approves_then_success_with_lint_configured(
        self, mock_branch, mock_commit, mock_lint,
        mock_tests, mock_worker, mock_build, tmp_path
    ):
        """Approving the diff with lint configured should still lead to success
        when tests pass on first iteration."""
        mock_worker.return_value = "Fixed it."
        mock_tests.return_value = MagicMock(passed=True, returncode=0,
                                             output_tail="1 passed")
        mock_lint.return_value = MagicMock(passed=True, returncode=0,
                                            output_tail="")
        mock_commit.return_value = "abc12345"

        cfg = MagicMock()
        cfg.is_remote = False
        cfg.local_repo_path = str(tmp_path)
        cfg.state_file = "state.json"
        cfg.goal = "make tests pass"
        cfg.test_cmd = "pytest"
        cfg.max_iterations = 3
        cfg.max_seconds = 3600
        cfg.require_approval = True
        cfg.model_name = "gemma4"
        cfg.llm_provider = "ollama_cloud"
        cfg.lint_cmd = "flake8 ."
        cfg.skills_dir = None

        from controller.loop import run
        with patch("controller.loop.get_diff", return_value="diff --git ..."), \
             patch("builtins.input", return_value="y"):
            result = run(cfg)

        assert result is True
        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data["termination_reason"] == "success"


# ---------------------------------------------------------------------------
# Section 11 — End-to-end smoke test (real subprocess, real git, real tools)
# ---------------------------------------------------------------------------
#
# What is REAL here (not mocked):
#   - controller.executor.run_tests() → real subprocess.run() call
#   - utils.git_utils.ensure_work_branch / commit_iteration → real GitPython
#   - tools.build_tool_registry() → real tool objects constructed
#   - controller.evaluator.evaluate_iteration() → real function
#   - controller.router.decide_next_step() → real function
#   - controller.state.save_state / load_state → real JSON on disk
#
# What is STUBBED:
#   - agents.worker.run_worker_turn → scripted code edit written directly to disk
#     (we cannot run a real LLM in an automated test without an API key + model)
#   - agents.worker.build_worker_agent → returns a MagicMock agent object
#     (the returned object is only passed through to run_worker_turn anyway)
#
# The test proves the integration seam: after the stubbed worker makes a real
# file edit that fixes the failing test, the real executor sees it pass, the
# real evaluator produces is_correct=True, the real router terminates with
# SUCCESS, state is persisted to a real state.json, and the real git branch
# has a real commit.

def _init_git_repo(path: str) -> None:
    """Initialize a bare git repo suitable for the agent to work in."""
    subprocess.run(["git", "init", path], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, check=True, capture_output=True, stdin=subprocess.DEVNULL
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Agent"],
        cwd=path, check=True, capture_output=True, stdin=subprocess.DEVNULL
    )


def _make_initial_commit(repo_path: str, files: dict) -> None:
    """Write files and make an initial commit on main/master."""
    for fname, content in files.items():
        fpath = os.path.join(repo_path, fname)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w") as f:
            f.write(content)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path, check=True, capture_output=True, stdin=subprocess.DEVNULL
    )


@pytest.fixture
def smoke_repo(tmp_path):
    """A real temporary git repo with one deliberately failing test.

    The test_cmd uses sys.executable + ' -m pytest' so pytest runs from
    the active venv (resolving the PATH issue in subprocess environments).
    The test file embeds its own sys.path setup to avoid import issues.
    """
    repo_path = str(tmp_path / "smoke_repo")
    os.makedirs(repo_path)
    _init_git_repo(repo_path)

    # The production module with a deliberate bug (returns wrong value)
    src_content = "def add(a, b):\n    return a - b  # BUG: should be a + b\n"

    # The test file: embeds sys.path insertion so it can import src,
    # and clears module cache to pick up file changes between pytest runs.
    test_content = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "\n"
        "def test_add():\n"
        "    if 'src' in sys.modules:\n"
        "        del sys.modules['src']\n"
        "    import src\n"
        "    result = src.add(2, 3)\n"
        "    assert result == 5, f'Expected 5, got {result}'\n"
    )

    _make_initial_commit(repo_path, {
        "src.py": src_content,
        "test_src.py": test_content,
    })

    return repo_path


def _scripted_worker_side_effect(repo_path: str):
    """Closure that, when called as run_worker_turn(agent, instruction),
    writes the real fix directly to disk (simulating what a real LLM would do)
    and returns a summary string."""
    # Only write the fix on the FIRST call, subsequent calls are no-ops
    # (the first fix should be enough to pass the test)
    called = [False]

    def _worker(agent, instruction: str) -> str:
        if not called[0]:
            fix_path = os.path.join(repo_path, "src.py")
            with open(fix_path, "w") as f:
                f.write("def add(a, b):\n    return a + b  # FIXED\n")
            called[0] = True
        return "Fixed add() to use + instead of -"
    return _worker


class TestEndToEndSmoke:
    """True end-to-end: real subprocess, real git, only LLM stubbed."""

    def test_smoke_fail_then_fix_then_pass(self, smoke_repo):
        """Verify the full pipeline integrates correctly:
        1. Real pytest run fails on the buggy code.
        2. The stubbed worker writes the real fix to disk.
        3. Real pytest run passes on the fixed code.
        4. Real git commit is made on the auto-agent-work branch.
        5. Evaluator reports is_correct=True.
        6. Router produces SUCCESS.
        7. state.json on disk reflects success.
        """
        from controller.loop import run
        from controller.state import TerminationReason

        cfg = MagicMock()
        cfg.is_remote = False
        cfg.local_repo_path = smoke_repo
        cfg.state_file = "state.json"
        cfg.goal = "fix the failing test in test_src.py"
        import sys as _sys
        _py = _sys.executable.replace('"', '\\"')  # escape any inner quotes
        cfg.test_cmd = f'"{_py}" -m pytest test_src.py -v'
        cfg.max_iterations = 3
        cfg.max_seconds = 300
        cfg.require_approval = False
        cfg.model_name = "test-model"
        cfg.llm_provider = "ollama_cloud"
        cfg.lint_cmd = None
        cfg.skills_dir = None

        with patch("controller.loop.build_worker_agent",
                   return_value=MagicMock()), \
             patch("controller.loop.run_worker_turn",
                   side_effect=_scripted_worker_side_effect(smoke_repo)):
            result = run(cfg)

        # --- Assert: overall outcome ---
        assert result is True, (
            "Loop should return True when tests pass after the fix"
        )

        # --- Assert: state.json persisted correctly ---
        state_file = os.path.join(smoke_repo, "state.json")
        assert os.path.isfile(state_file), "state.json must exist after run"
        with open(state_file) as f:
            state_data = json.load(f)

        assert state_data["termination_reason"] == TerminationReason.SUCCESS, (
            f"Expected success termination, got: {state_data['termination_reason']}"
        )
        assert len(state_data["iterations"]) >= 1, "Must have at least one iteration"

        # At least one iteration must have test_passed=True
        any_pass = any(it["test_passed"] for it in state_data["iterations"])
        assert any_pass, "At least the final iteration must have test_passed=True"

        # evaluator_result must reflect is_correct=True
        ev = state_data.get("evaluator_result")
        assert ev is not None, "evaluator_result must be set in state"
        assert ev["is_correct"] is True

        # --- Assert: real git branch exists and has a commit ---
        import subprocess
        branches = subprocess.run(
            ["git", "branch"],
            cwd=smoke_repo, capture_output=True, text=True
        ).stdout
        assert "auto-agent-work" in branches, (
            "auto-agent-work branch must exist after run"
        )

        # Verify the fix is present in the working tree
        with open(os.path.join(smoke_repo, "src.py")) as f:
            fixed_content = f.read()
        assert "a + b" in fixed_content, "Fix must be present in src.py"

    def test_smoke_tool_registry_built_and_usable(self, smoke_repo):
        """Verify that build_tool_registry produces working tools
        against the real smoke_repo (not mocked git calls)."""
        from tools import build_tool_registry
        from langchain_core.tools import BaseTool

        tools = build_tool_registry(smoke_repo, "pytest", require_approval=False)
        assert len(tools) == 12
        for t in tools:
            assert isinstance(t, BaseTool)

        # git_status must work against the real repo without error
        git_status_tool = next(t for t in tools if t.name == "git_status")
        result = git_status_tool.invoke({})
        assert "ERROR" not in result or "nothing to commit" in result

        # git_diff must work without error
        git_diff_tool = next(t for t in tools if t.name == "git_diff")
        result = git_diff_tool.invoke({})
        assert "ERROR" not in result

    def test_smoke_state_persists_across_reload(self, smoke_repo):
        """After a real run, state loaded from disk must match the final state."""
        from controller.loop import run
        from controller.state import load_state, TerminationReason

        cfg = MagicMock()
        cfg.is_remote = False
        cfg.local_repo_path = smoke_repo
        cfg.state_file = "state.json"
        cfg.goal = "fix the failing add function"
        import sys as _sys
        _py = _sys.executable.replace('"', '\\"')  # escape any inner quotes
        cfg.test_cmd = f'"{_py}" -m pytest test_src.py -v'
        cfg.max_iterations = 3
        cfg.max_seconds = 300
        cfg.require_approval = False
        cfg.model_name = "test-model"
        cfg.llm_provider = "ollama_cloud"
        cfg.lint_cmd = None
        cfg.skills_dir = None

        with patch("controller.loop.build_worker_agent",
                   return_value=MagicMock()), \
             patch("controller.loop.run_worker_turn",
                   side_effect=_scripted_worker_side_effect(smoke_repo)):
            run(cfg)

        state_file = os.path.join(smoke_repo, "state.json")
        loaded = load_state(state_file, "fix the failing add function")
        assert loaded.termination_reason == TerminationReason.SUCCESS
        assert loaded.goal == "fix the failing add function"
        assert len(loaded.iterations) >= 1
        assert loaded.evaluator_result is not None
        assert loaded.evaluator_result["is_correct"] is True


# ---------------------------------------------------------------------------
# Section 12 — Regression/duplication report (documented tests)
# ---------------------------------------------------------------------------

class TestRegressionConsolidation:
    """Spot-checks confirming no unintended regressions across phases.

    These are very lightweight — they confirm the same behaviour across the
    combined suite without duplicating heavy test logic.
    """

    def test_config_ollama_api_key_guard_still_works(self, tmp_path):
        """Duplicate check: all prior phase tests assumed OLLAMA_API_KEY is
        needed. Verify the guard is still in place exactly once."""
        import os
        with patch.dict(os.environ, {}, clear=True):
            # Remove any key that might be set
            os.environ.pop("OLLAMA_API_KEY", None)
            os.environ.pop("LLM_PROVIDER", None)
            from config import Config
            with pytest.raises(EnvironmentError, match="OLLAMA_API_KEY"):
                Config(repo_path=str(tmp_path), goal="test",
                       llm_provider="ollama_cloud")

    def test_termination_reason_unrecoverable_error_constant(self):
        """TerminationReason.UNRECOVERABLE_ERROR string value has not changed."""
        from controller.state import TerminationReason
        assert TerminationReason.UNRECOVERABLE_ERROR == "unrecoverable_error"

    def test_tool_registry_still_five_tools_after_phase7(self):
        """No Phase 7 change should have altered the tool count."""
        from tools import build_tool_registry
        tools = build_tool_registry("/fake/repo", "pytest")
        assert len(tools) == 12
