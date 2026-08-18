"""Orchestrator Execution Engine — Phase 6.

Coordinates goal execution by managing global plan progression and delegating
subtasks to controller.loop.run().
"""

import os
from typing import Any, Dict, List, Optional

from config import Config
from controller.loop import run as run_controller_loop
from controller.router import RouterState
from orchestrator.planner import GoalPlanner, Plan, SubTask, SubTaskStatus
from skills.loader import SkillLoader


class DeepAgentOrchestrator:
    """Orchestrates goal execution by decomposing goals and coordinating subtasks."""

    def __init__(self, config: Config, planner: Optional[GoalPlanner] = None):
        self.config = config
        self.planner = planner or GoalPlanner(
            model_name=getattr(config, "model_name", None),
            llm_provider=getattr(config, "llm_provider", None),
        )
        self.skills_dir = getattr(config, "skills_dir", None) or "skills"
        self.skill_loader = SkillLoader(self.skills_dir)

    def run(self, initial_plan: Optional[Plan] = None) -> Dict[str, Any]:
        """Execute the goal according to a structured plan.

        Returns:
            Dict containing:
              - "success": bool
              - "plan": Plan
              - "completed_subtasks": int
              - "total_subtasks": int
        """
        # Discover available skills
        available_skills = self.skill_loader.list_available()

        # Build initial plan if not provided
        plan = initial_plan or self.planner.decompose_goal(
            goal=self.config.goal,
            repo_summary="",
            available_skills=available_skills,
        )

        print(f"\n[PLAN] Orchestrator initialized with {len(plan.subtasks)} subtask(s):")
        for i, st in enumerate(plan.subtasks, 1):
            print(f"  {i}. [{st.id}] {st.description} (verification: {st.verification_strategy or 'pytest'})")

        completed_count = 0
        from controller.checkpoint import CheckpointManager
        checkpoint_mgr = CheckpointManager(self.config.local_repo_path)

        while plan.current_index < len(plan.subtasks):
            subtask = plan.current_subtask
            if not subtask:
                break

            subtask.status = SubTaskStatus.IN_PROGRESS
            print(f"\n[STEP] === Executing Subtask {plan.current_index + 1}/{len(plan.subtasks)}: '{subtask.description}' ===")

            # Create checkpoint before subtask actions
            checkpoint_id = checkpoint_mgr.create_checkpoint(f"subtask_{subtask.id}")

            # Configure a per-subtask Config instance
            subtask_config = self._build_subtask_config(subtask)

            # Delegate execution to controller loop
            success = run_controller_loop(subtask_config)

            if success:
                subtask.status = SubTaskStatus.COMPLETED
                subtask.result_summary = "Subtask completed and verified successfully."
                completed_count += 1
                plan.current_index += 1
                if checkpoint_id:
                    checkpoint_mgr.discard_checkpoint(checkpoint_id)
                print(f"[DONE] Subtask '{subtask.id}' COMPLETED.")
            else:
                # Subtask failed. Check if replanning should occur
                print(f"[ERROR] Subtask '{subtask.id}' failed verification.")
                
                # Check if this subtask was already a recovery/replan attempt
                if plan.metadata.get("replanned") and "_retry" in subtask.id:
                    subtask.status = SubTaskStatus.FAILED
                    # Rollback since the replan attempt failed permanently
                    if checkpoint_id:
                        checkpoint_mgr.rollback_to_checkpoint(checkpoint_id)
                    print(f"[ERROR] Replanned retry failed for subtask '{subtask.id}'. Halting orchestrator.")
                    break

                # Trigger replanner
                failure_evidence = f"Verification failed for subtask '{subtask.description}'."
                print("[RECOVERY] Triggering dynamic replanner for remaining queue...")
                plan = self.planner.replan(plan, failure_evidence=failure_evidence)

                # Rollback current subtask changes to last healthy checkpoint before trying recovery
                if checkpoint_id:
                    checkpoint_mgr.rollback_to_checkpoint(checkpoint_id)

                # Check if newly advanced subtask is still within bounds
                if plan.current_index >= len(plan.subtasks):
                    break

        all_success = plan.is_completed
        status_msg = "SUCCESS" if all_success else "FAILED"
        print(f"\n[DONE] Orchestrator finished with status: {status_msg} ({completed_count}/{len(plan.subtasks)} subtasks completed)")

        return {
            "success": all_success,
            "plan": plan,
            "completed_subtasks": completed_count,
            "total_subtasks": len(plan.subtasks),
        }


    def _build_subtask_config(self, subtask: SubTask) -> Config:
        """Create a dedicated Config instance for a single subtask execution."""
        # Include prompt injection for matched skills if any
        matching_skills = self.skill_loader.get_matching_skills(subtask.description)
        skill_names = [s.name for s in matching_skills]
        
        cfg_dict = {
            "repo_path": self.config.repo_path,
            "goal": subtask.description,
            "test_cmd": self.config.test_cmd,
            "max_iterations": self.config.max_iterations,
            "max_seconds": self.config.max_seconds,
            "require_approval": self.config.require_approval,
            "model_name": self.config.model_name,
            "state_file": self.config.state_file,
            "llm_provider": self.config.llm_provider,
            "lint_cmd": self.config.lint_cmd,
            "skills_dir": self.config.skills_dir,
        }
        if getattr(self.config, "mcp_config_path", None) is not None:
            cfg_dict["mcp_config_path"] = self.config.mcp_config_path

        subtask_cfg = Config(**cfg_dict)
        
        # Attach explicit verification strategy if declared on the subtask
        if subtask.verification_strategy:
            setattr(subtask_cfg, "verification_strategy", subtask.verification_strategy)
            setattr(subtask_cfg, "verification_kwargs", subtask.verification_spec)

        return subtask_cfg
