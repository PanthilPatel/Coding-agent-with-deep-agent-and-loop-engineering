"""test_orchestrator.py — Phase 6: Deep Agent Orchestrator & Goal Decomposition tests.

Covers:
- SubTask and Plan model serialization and deserialization.
- GoalPlanner decompose_goal: single goal, multi-step heuristic decomposition.
- GoalPlanner replan: handling failures and inserting recovery + retry subtasks.
- DeepAgentOrchestrator.run(): happy path where all subtasks pass.
- DeepAgentOrchestrator.run(): failure path triggering replan then completing or failing.
- main.py CLI integration with --decompose and --orchestrate flags.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import Config
from orchestrator.planner import SubTask, SubTaskStatus, Plan, GoalPlanner
from orchestrator.engine import DeepAgentOrchestrator

class TestOrchestratorModels:
    def test_subtask_creation_and_to_dict(self):
        st = SubTask(
            id="task_1",
            description="Create index.html",
            verification_strategy="file_exists",
            verification_spec={"path": "index.html"},
            required_skills=["debugging"],
            status=SubTaskStatus.PENDING,
        )
        d = st.to_dict()
        assert d["id"] == "task_1"
        assert d["description"] == "Create index.html"
        assert d["verification_strategy"] == "file_exists"
        assert d["verification_spec"] == {"path": "index.html"}
        assert d["status"] == "pending"

    def test_subtask_from_dict_roundtrip(self):
        d = {
            "id": "t2",
            "description": "Fix calculation bug",
            "verification_strategy": "test_suite",
            "verification_spec": {},
            "required_skills": ["debugging", "testing"],
            "status": "completed",
            "result_summary": "Tests passed",
        }
        st = SubTask.from_dict(d)
        assert st.id == "t2"
        assert st.status == SubTaskStatus.COMPLETED
        assert st.result_summary == "Tests passed"
        assert st.to_dict()["status"] == "completed"

    def test_plan_progression_and_completion(self):
        st1 = SubTask(id="1", description="Step 1", status=SubTaskStatus.COMPLETED)
        st2 = SubTask(id="2", description="Step 2", status=SubTaskStatus.PENDING)
        plan = Plan(goal="Build app", subtasks=[st1, st2], current_index=1)

        assert plan.current_subtask == st2
        assert not plan.is_completed

        st2.status = SubTaskStatus.COMPLETED
        plan.current_index = 2
        assert plan.is_completed

    def test_plan_dict_serialization(self):
        st1 = SubTask(id="1", description="Step 1")
        plan = Plan(goal="Goal", subtasks=[st1], metadata={"author": "agent"})
        d = plan.to_dict()
        assert d["goal"] == "Goal"
        assert len(d["subtasks"]) == 1
        assert d["metadata"]["author"] == "agent"

        restored = Plan.from_dict(d)
        assert restored.goal == "Goal"
        assert restored.subtasks[0].id == "1"


# ===========================================================================
# 2. GoalPlanner Tests
# ===========================================================================

class TestGoalPlanner:
    def test_decompose_single_simple_goal(self):
        planner = GoalPlanner()
        plan = planner.decompose_goal("Fix bug in calculation", available_skills=["debugging", "testing"])
        assert len(plan.subtasks) == 1
        assert plan.subtasks[0].id == "step_1"
        assert plan.subtasks[0].description == "Fix bug in calculation"
        assert "debugging" in plan.subtasks[0].required_skills

    def test_decompose_numbered_multi_step_goal(self):
        planner = GoalPlanner()
        goal = (
            "1. Create a file named status.txt containing 'OK'\n"
            "2. Run test_suite to verify calculation"
        )
        plan = planner.decompose_goal(goal, available_skills=["testing"])
        assert len(plan.subtasks) == 2
        assert plan.subtasks[0].verification_strategy == "file_exists"
        assert plan.subtasks[0].verification_spec == {"path": "status.txt", "expected_content": "OK"}
        assert plan.subtasks[1].verification_strategy == "test_suite"

    def test_decompose_connector_multi_step_goal(self):
        planner = GoalPlanner()
        goal = "Create directory logs and then write file logs/app.log"
        plan = planner.decompose_goal(goal)
        assert len(plan.subtasks) == 2
        assert "directory" in plan.subtasks[0].description
        assert "app.log" in plan.subtasks[1].description

    def test_replan_inserts_recovery_and_retry(self):
        planner = GoalPlanner()
        st1 = SubTask(id="step_1", description="Write algorithm", status=SubTaskStatus.IN_PROGRESS)
        st2 = SubTask(id="step_2", description="Add docs")
        plan = Plan(goal="Write feature", subtasks=[st1, st2], current_index=0)

        replanned = planner.replan(plan, failure_evidence="AssertionError: 5 != 10")
        
        # Subtasks should now be: [failed step_1, step_1_recovery, step_1_retry, step_2]
        assert len(replanned.subtasks) == 4
        assert replanned.subtasks[0].status == SubTaskStatus.FAILED
        assert replanned.subtasks[1].id == "step_1_recovery"
        assert replanned.subtasks[2].id == "step_1_retry"
        assert replanned.subtasks[3].id == "step_2"
        assert replanned.current_index == 1
        assert replanned.metadata.get("replanned") is True


# ===========================================================================
# 3. DeepAgentOrchestrator Engine Tests
# ===========================================================================

class TestDeepAgentOrchestrator:
    def test_orchestrator_all_subtasks_succeed(self, tmp_path):
        cfg = Config(repo_path=str(tmp_path), goal="1. Step one\n2. Step two")
        orchestrator = DeepAgentOrchestrator(cfg)

        with patch("orchestrator.engine.run_controller_loop", return_value=True) as mock_loop:
            result = orchestrator.run()
            assert result["success"] is True
            assert result["completed_subtasks"] == 2
            assert result["total_subtasks"] == 2
            assert mock_loop.call_count == 2

    def test_orchestrator_subtask_replan_then_succeed(self, tmp_path):
        cfg = Config(repo_path=str(tmp_path), goal="1. Do task A\n2. Do task B")
        orchestrator = DeepAgentOrchestrator(cfg)

        # Sequence: task A fails -> replan (recovery succeeds -> retry succeeds) -> task B succeeds
        # Calls: 1 (task A fail), 2 (recovery pass), 3 (retry pass), 4 (task B pass)
        side_effects = [False, True, True, True]

        with patch("orchestrator.engine.run_controller_loop", side_effect=side_effects) as mock_loop:
            result = orchestrator.run()
            assert result["success"] is True
            assert result["completed_subtasks"] == 3
            assert mock_loop.call_count == 4

    def test_orchestrator_retry_fail_halts(self, tmp_path):
        cfg = Config(repo_path=str(tmp_path), goal="Step 1")
        orchestrator = DeepAgentOrchestrator(cfg)

        # Sequence: step 1 fails -> recovery pass -> retry fails -> halt
        side_effects = [False, True, False]

        with patch("orchestrator.engine.run_controller_loop", side_effect=side_effects):
            result = orchestrator.run()
            assert result["success"] is False
            assert not result["plan"].is_completed


# ===========================================================================
# 4. main.py CLI Flag Routing
# ===========================================================================

class TestMainOrchestratorFlag:
    def test_main_routes_to_orchestrator_when_flag_passed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        test_args = [
            "main.py",
            "--repo", str(tmp_path),
            "--goal", "multi-step task",
            "--decompose",
        ]

        mock_orch = MagicMock()
        mock_orch.run.return_value = {"success": True}
        mock_loop_run = MagicMock(return_value=True)

        with patch.object(sys, "argv", test_args):
            with patch("main.DeepAgentOrchestrator", return_value=mock_orch):
                with patch("main.run", mock_loop_run):
                    with patch.object(sys, "exit") as mock_exit:
                        import main
                        main.main()
                        mock_orch.run.assert_called_once()
                        mock_loop_run.assert_not_called()
                        mock_exit.assert_called_once_with(0)




