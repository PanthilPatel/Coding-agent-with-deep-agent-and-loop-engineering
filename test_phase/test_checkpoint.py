"""test_checkpoint.py — Phase 7: Git Checkpoint Rollback & State Snapshots tests."""

import os
import sys
import tempfile
import shutil
import subprocess
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import Config
from controller.checkpoint import CheckpointManager
from orchestrator.engine import DeepAgentOrchestrator
from orchestrator.planner import Plan, SubTask, SubTaskStatus


@pytest.fixture
def temp_git_repo():
    """Fixture to initialize a temporary git repo for testing checkpoints."""
    repo_dir = tempfile.mkdtemp()
    
    # Configure git dummy user info locally for Windows tests
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    # Initial commit to have a HEAD reference
    readme = os.path.join(repo_dir, "README.md")
    with open(readme, "w") as f:
        f.write("# Initial project setup\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    yield repo_dir
    shutil.rmtree(repo_dir, ignore_errors=True)


class TestCheckpointManager:

    def test_checkpoint_on_clean_git_repo(self, temp_git_repo):
        mgr = CheckpointManager(temp_git_repo)
        assert mgr.is_git is True
        assert mgr.has_uncommitted_changes() is False

        # Create checkpoint on clean branch
        sha = mgr.create_checkpoint("Clean checkpoint")
        assert sha is not None
        assert len(sha) == 40  # Valid full Git SHA-1 length

    def test_checkpoint_and_rollback_on_dirty_repo(self, temp_git_repo):
        mgr = CheckpointManager(temp_git_repo)

        # 1. Modify existing file
        readme_path = os.path.join(temp_git_repo, "README.md")
        with open(readme_path, "a") as f:
            f.write("Line appended.\n")
        assert mgr.has_uncommitted_changes() is True

        # 2. Capture state checkpoint
        checkpoint_id = mgr.create_checkpoint("Dirty modified readme state")
        assert checkpoint_id is not None
        assert mgr.has_uncommitted_changes() is False

        # 3. Add a new untracked file to contaminate repository
        untracked_path = os.path.join(temp_git_repo, "contaminant.txt")
        with open(untracked_path, "w") as f:
            f.write("Untracked garbage\n")
        assert mgr.has_uncommitted_changes() is True

        # 4. Rollback
        success = mgr.rollback_to_checkpoint(checkpoint_id)
        assert success is True
        assert mgr.has_uncommitted_changes() is False
        assert not os.path.exists(untracked_path)

    def test_rollback_reverts_deleted_files(self, temp_git_repo):
        mgr = CheckpointManager(temp_git_repo)
        readme_path = os.path.join(temp_git_repo, "README.md")
        
        checkpoint_id = mgr.create_checkpoint("Pre-deletion state")

        # Delete README.md
        os.remove(readme_path)
        assert mgr.has_uncommitted_changes() is True

        # Rollback should restore README.md
        mgr.rollback_to_checkpoint(checkpoint_id)
        assert os.path.exists(readme_path)

    def test_graceful_degradation_non_git_directory(self):
        non_git_dir = tempfile.mkdtemp()
        try:
            mgr = CheckpointManager(non_git_dir)
            assert mgr.is_git is False
            assert mgr.has_uncommitted_changes() is False
            
            checkpoint = mgr.create_checkpoint("Non-git attempt")
            assert checkpoint is None

            rollback = mgr.rollback_to_checkpoint("some_id")
            assert rollback is False
        finally:
            shutil.rmtree(non_git_dir, ignore_errors=True)


class TestOrchestratorRollbackIntegration:

    def test_orchestrator_rolls_back_subtask_on_failure(self):
        # Configure Config to target a mock project path
        cfg = Config(repo_path=".", goal="1. Add contaminant")
        orchestrator = DeepAgentOrchestrator(cfg)

        # Build subtasks
        st1 = SubTask(id="sub_1", description="Create contaminant.txt")
        plan = Plan(goal=cfg.goal, subtasks=[st1], current_index=0)

        # Mock the CheckpointManager methods directly to verify rollback call
        with patch("orchestrator.engine.run_controller_loop", return_value=False):
            with patch("controller.checkpoint.CheckpointManager") as MockCheckpointMgrClass:
                mock_mgr_instance = MagicMock()
                MockCheckpointMgrClass.return_value = mock_mgr_instance
                mock_mgr_instance.create_checkpoint.return_value = "mock_sha"
                
                # Mock self.planner.replan to return a completed/halted plan immediately to avoid infinite recovery loop
                def mock_replan(p, failure_evidence=None):
                    p.subtasks[p.current_index].status = SubTaskStatus.FAILED
                    # Advance index to end to complete the run
                    p.current_index = len(p.subtasks)
                    return p
                orchestrator.planner.replan = mock_replan

                result = orchestrator.run(plan)
                assert result["success"] is False
                # Check that rollback was invoked with the mock checkpoint SHA
                mock_mgr_instance.rollback_to_checkpoint.assert_called_with("mock_sha")

