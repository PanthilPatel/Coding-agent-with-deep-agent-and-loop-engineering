import os
import json
import pytest
from unittest.mock import patch, MagicMock
from config import Config
from controller.executor import ExecResult, run_tests, run_lint, failure_signature
from controller.state import IterationRecord, RunState, load_state, save_state

def test_config_initialization(tmp_path):
    repo_dir = tmp_path / "mock_repo"
    repo_dir.mkdir()
    
    with patch.dict(os.environ, {"OLLAMA_API_KEY": "test_key", "LLM_PROVIDER": "ollama_cloud"}):
        cfg = Config(repo_path=str(repo_dir), goal="fix bugs")
        assert cfg.repo_path == os.path.abspath(str(repo_dir))
        assert cfg.goal == "fix bugs"
        assert cfg.llm_provider == "ollama_cloud"

def test_config_missing_api_key(tmp_path):
    repo_dir = tmp_path / "mock_repo"
    repo_dir.mkdir()
    
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(EnvironmentError, match="OLLAMA_API_KEY is not set."):
            Config(repo_path=str(repo_dir), goal="fix bugs", llm_provider="ollama_cloud")

def test_config_invalid_repo():
    with patch.dict(os.environ, {"OLLAMA_API_KEY": "test_key"}):
        with pytest.raises(FileNotFoundError):
            Config(repo_path="nonexistent_path_12345", goal="fix bugs")

def test_failure_signature():
    res = ExecResult(passed=False, returncode=1, output_tail="line1\nline2\nline3\nline4\nline5\nline6")
    sig = failure_signature(res)
    assert sig == "line2\nline3\nline4\nline5\nline6"

    res_short = ExecResult(passed=False, returncode=2, output_tail="")
    assert failure_signature(res_short) == "2"

def test_run_state_transitions():
    state = RunState(goal="fix unit tests")
    assert state.same_failure_count == 0
    assert state.last_failure_signature is None

    # First failure
    force_new = state.note_failure("error_abc")
    assert not force_new
    assert state.same_failure_count == 1
    assert state.last_failure_signature == "error_abc"

    # Second identical failure
    force_new = state.note_failure("error_abc")
    assert force_new
    assert state.same_failure_count == 2

    # Different failure signature resets count
    force_new = state.note_failure("error_xyz")
    assert not force_new
    assert state.same_failure_count == 1
    assert state.last_failure_signature == "error_xyz"

    # Success resets tracking
    state.note_success()
    assert state.same_failure_count == 0
    assert state.last_failure_signature is None

def test_save_and_load_state(tmp_path):
    state_file = tmp_path / "state.json"
    state = RunState(goal="optimize code")
    
    record = IterationRecord(
        iteration=1,
        timestamp=123456.78,
        instruction_summary="instruction summary",
        worker_summary="worker summary",
        test_passed=True,
        test_output_tail="all green"
    )
    state.add_iteration(record)
    save_state(str(state_file), state)

    loaded = load_state(str(state_file), "optimize code")
    assert loaded.goal == "optimize code"
    assert len(loaded.iterations) == 1
    assert loaded.iterations[0]["worker_summary"] == "worker summary"
    assert loaded.iterations[0]["test_passed"] is True

def test_is_git_url():
    from utils.git_remote import is_git_url
    assert is_git_url("https://github.com/user/repo.git") is True
    assert is_git_url("git@github.com:user/repo.git") is True
    assert is_git_url("https://github.com/user/repo") is True
    assert is_git_url("c:\\Users\\Admin\\Documents\\repo") is False
    assert is_git_url("/home/user/repo") is False

def test_config_with_remote_git_url():
    with patch.dict(os.environ, {"OLLAMA_API_KEY": "test_key", "LLM_PROVIDER": "ollama_cloud"}):
        cfg = Config(repo_path="https://github.com/user/my-awesome-repo.git", goal="fix stuff")
        assert cfg.is_remote is True
        assert cfg.repo_path == "https://github.com/user/my-awesome-repo.git"
        assert cfg.local_repo_path.endswith(os.path.join("workspace_clones", "my-awesome-repo"))

