"""controller/checkpoint.py — Phase 7: Git Checkpoint Rollback & State Snapshots.

Provides the CheckpointManager class to handle git checkpointing, rollback, and state management.
"""

import os
import subprocess
from typing import Optional


class CheckpointManager:
    """Manages repository state checkpoints using git branch snapshots or stashing."""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.is_git = self._check_is_git()

    def _check_is_git(self) -> bool:
        if not os.path.exists(self.repo_path):
            return False
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False
            )
            return res.returncode == 0 and "true" in res.stdout.lower()
        except Exception:
            return False

    def create_checkpoint(self, label: str) -> Optional[str]:
        """Creates a git lightweight checkpoint commit on a temporary checkpoint branch or stashes changes."""
        if not self.is_git:
            print("[CHECKPOINT] Warning: repo_path is not a git repository. Skipping checkpoint creation.")
            return None

        try:
            # We want to create a clean commit representing the current state.
            # If there are uncommitted changes, stage and commit them as a checkpoint.
            if self.has_uncommitted_changes():
                subprocess.run(["git", "add", "-A"], cwd=self.repo_path, check=True)
                commit_msg = f"[CHECKPOINT] {label}"
                res = subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=self.repo_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=True
                )
                # Get HEAD SHA
                sha_res = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.repo_path,
                    stdout=subprocess.PIPE,
                    text=True,
                    check=True
                )
                sha = sha_res.stdout.strip()
                print(f"[CHECKPOINT] Created checkpoint commit {sha} for label '{label}'")
                return sha
            else:
                # No uncommitted changes, return current HEAD SHA
                sha_res = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.repo_path,
                    stdout=subprocess.PIPE,
                    text=True,
                    check=True
                )
                sha = sha_res.stdout.strip()
                print(f"[CHECKPOINT] Created checkpoint {sha} (clean HEAD) for label '{label}'")
                return sha
        except Exception as e:
            print(f"[CHECKPOINT] Error creating checkpoint: {e}")
            return None

    def rollback_to_checkpoint(self, checkpoint_id: Optional[str] = None) -> bool:
        """Restores the repository state to the specified checkpoint ID, discarding dirty changes."""
        if not self.is_git:
            print("[ROLLBACK] Warning: repo_path is not a git repository. Skipping rollback.")
            return False

        if not checkpoint_id:
            print("[ROLLBACK] No checkpoint ID specified. Cannot rollback.")
            return False

        try:
            # Hard reset to checkpoint_id
            print(f"[ROLLBACK] Restoring repository state to {checkpoint_id}")
            subprocess.run(["git", "reset", "--hard", checkpoint_id], cwd=self.repo_path, check=True)
            # Clean untracked files
            subprocess.run(["git", "clean", "-fdx"], cwd=self.repo_path, check=True)
            return True
        except Exception as e:
            print(f"[ROLLBACK] Error rolling back to checkpoint: {e}")
            return False

    def discard_checkpoint(self, checkpoint_id: Optional[str] = None) -> None:
        """Cleans up checkpoint references if any. Minimal placeholder logic for lightweight commits."""
        pass

    def has_uncommitted_changes(self) -> bool:
        """Checks if there are modified, staged, or untracked changes in the repo."""
        if not self.is_git:
            return False
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            return bool(res.stdout.strip())
        except Exception:
            return False
