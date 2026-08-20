"""Orchestrator Planner Module — Phase 6: Goal Decomposition & Planning.

Defines:
- SubTaskStatus enum (PENDING, IN_PROGRESS, COMPLETED, FAILED, SKIPPED)
- SubTask model (id, description, verification_strategy, verification_spec, required_skills, status, result_summary)
- Plan model (goal, subtasks, current_index, metadata)
- GoalPlanner (decompose_goal, replan)
"""

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class SubTaskStatus(str, Enum):
    """Lifecycle status of a subtask within a plan."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SubTask:
    """A single verifiable step within a multi-step execution plan."""
    id: str
    description: str
    verification_strategy: Optional[str] = None
    verification_spec: Dict[str, Any] = field(default_factory=dict)
    required_skills: List[str] = field(default_factory=list)
    status: SubTaskStatus = SubTaskStatus.PENDING
    result_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "verification_strategy": self.verification_strategy,
            "verification_spec": self.verification_spec,
            "required_skills": self.required_skills,
            "status": self.status.value if isinstance(self.status, SubTaskStatus) else str(self.status),
            "result_summary": self.result_summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubTask":
        raw_status = data.get("status", SubTaskStatus.PENDING)
        try:
            status_enum = SubTaskStatus(raw_status)
        except ValueError:
            status_enum = SubTaskStatus.PENDING

        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            description=data.get("description", ""),
            verification_strategy=data.get("verification_strategy"),
            verification_spec=data.get("verification_spec", {}),
            required_skills=data.get("required_skills", []),
            status=status_enum,
            result_summary=data.get("result_summary"),
        )


@dataclass
class Plan:
    """A collection of subtasks ordered for execution."""
    goal: str
    subtasks: List[SubTask]
    current_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def current_subtask(self) -> Optional[SubTask]:
        if 0 <= self.current_index < len(self.subtasks):
            return self.subtasks[self.current_index]
        return None

    @property
    def is_completed(self) -> bool:
        """A plan is completed if all remaining subtasks after current index or all non-failed subtasks are completed."""
        if not self.subtasks:
            return True
        last_st = self.subtasks[-1]
        return self.current_index >= len(self.subtasks) and last_st.status in (
            SubTaskStatus.COMPLETED, SubTaskStatus.SKIPPED
        )


    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "subtasks": [st.to_dict() for st in self.subtasks],
            "current_index": self.current_index,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        subtasks_raw = data.get("subtasks", [])
        subtasks = [SubTask.from_dict(st) if isinstance(st, dict) else st for st in subtasks_raw]
        return cls(
            goal=data.get("goal", ""),
            subtasks=subtasks,
            current_index=data.get("current_index", 0),
            metadata=data.get("metadata", {}),
        )


class GoalPlanner:
    """Plans and decomposes high-level goals into executable subtasks."""

    def __init__(self, model_name: Optional[str] = None, llm_provider: Optional[str] = None):
        self.model_name = model_name
        self.llm_provider = llm_provider

    def _match_skills(self, text: str, available_skills: List[str]) -> List[str]:
        """Match relevant skills for task text from available skills using keyword map."""
        from skills.loader import _DEFAULT_KEYWORD_MAP
        text_lower = text.lower()
        matched = []
        for skill_name in available_skills:
            if re.search(rf"\b{re.escape(skill_name.lower())}\b(?!\.[a-zA-Z0-9_-]+)", text_lower):
                matched.append(skill_name)
                continue
            keywords = _DEFAULT_KEYWORD_MAP.get(skill_name, [])
            if any(re.search(rf"\b{re.escape(kw.lower())}\b(?!\.[a-zA-Z0-9_-]+)", text_lower) for kw in keywords):
                matched.append(skill_name)
        return matched

    def decompose_goal(
        self,
        goal: str,
        repo_summary: str = "",
        available_skills: Optional[List[str]] = None,
    ) -> Plan:
        """Decompose a high-level goal into a structured Plan with SubTasks.

        If decomposition fails or the goal is single-step, generates a single default subtask.
        """
        available = available_skills or []
        
        steps = self._extract_steps_heuristically(goal)
        if steps and len(steps) > 1:
            subtasks = []
            for idx, desc in enumerate(steps, 1):
                strategy, spec = self._infer_verification(desc)
                matched_skills = self._match_skills(desc, available)
                subtasks.append(
                    SubTask(
                        id=f"step_{idx}",
                        description=desc,
                        verification_strategy=strategy,
                        verification_spec=spec,
                        required_skills=matched_skills,
                    )
                )
            return Plan(goal=goal, subtasks=subtasks)

        strategy, spec = self._infer_verification(goal)
        matched_skills = self._match_skills(goal, available)
        default_subtask = SubTask(
            id="step_1",
            description=goal,
            verification_strategy=strategy,
            verification_spec=spec,
            required_skills=matched_skills,
        )
        return Plan(goal=goal, subtasks=[default_subtask])


    def replan(self, current_plan: Plan, failure_evidence: str) -> Plan:
        """Adjust remaining uncompleted subtasks based on failure evidence."""
        curr_idx = current_plan.current_index
        if curr_idx >= len(current_plan.subtasks):
            return current_plan

        failed_st = current_plan.subtasks[curr_idx]
        failed_st.status = SubTaskStatus.FAILED
        failed_st.result_summary = f"Failed with evidence: {failure_evidence[:200]}"

        recovery_id = f"{failed_st.id}_recovery"
        retry_id = f"{failed_st.id}_retry"

        recovery_subtask = SubTask(
            id=recovery_id,
            description=f"Analyze and isolate cause of failure for '{failed_st.description}'. Evidence: {failure_evidence[:150]}",
            verification_strategy=None,
            required_skills=["debugging"] if "debugging" in failed_st.required_skills or not failed_st.required_skills else failed_st.required_skills,
        )

        retry_subtask = SubTask(
            id=retry_id,
            description=f"Re-implement fix for: {failed_st.description} with updated approach.",
            verification_strategy=failed_st.verification_strategy,
            verification_spec=failed_st.verification_spec,
            required_skills=failed_st.required_skills,
        )

        remaining = current_plan.subtasks[curr_idx + 1:]
        new_subtasks = current_plan.subtasks[:curr_idx] + [failed_st, recovery_subtask, retry_subtask] + remaining
        
        return Plan(
            goal=current_plan.goal,
            subtasks=new_subtasks,
            current_index=curr_idx + 1,
            metadata={**current_plan.metadata, "replanned": True, "last_failure": failure_evidence[:200]},
        )

    def _extract_steps_heuristically(self, goal: str) -> List[str]:
        """Extract multi-step subtasks if goal contains numbered lists, bullet points, or semicolons/and-then connectors."""

        numbered = re.findall(r"(?:^|\n)\s*\d+[\.\)]\s*(.+?)(?=(?:\n\s*\d+[\.\)]|\Z))", goal, re.DOTALL)
        if len(numbered) >= 2:
            return [n.strip() for n in numbered if n.strip()]

        bullets = re.findall(r"(?:^|\n)\s*[-*]\s*(.+?)(?=(?:\n\s*[-*]|\Z))", goal, re.DOTALL)
        if len(bullets) >= 2:
            return [b.strip() for b in bullets if b.strip()]

        if " then " in goal.lower() or "; " in goal or " and then " in goal.lower():
            # Split by delimiters
            parts = re.split(r";|\b(?:and\s+then|then)\b", goal, flags=re.IGNORECASE)
            cleaned = [p.strip().rstrip(".") for p in parts if p.strip()]
            if len(cleaned) >= 2:
                return cleaned

        return []

    def classify_goal_type(self, goal: str) -> str:
        """Classify a goal into 'test_driven' vs 'non_test_driven'.

        - 'test_driven': goals centered around tests, assertions, bug fixes, or pytest failures.
        - 'non_test_driven': feature additions, refactoring, documentation, exploratory tasks, etc.
        """
        g_lower = goal.lower().strip()
        test_driven_patterns = [
            r"\b(?:fix|resolve|repair|debug|investigate|correct)\b.*?\b(?:test|tests|assertion|traceback|failure|bug|failing|error|crash)\b",
            r"\b(?:debug|traceback|crash|assertionerror)\b",
            r"\b(?:make|get)\b.*?\b(?:pass|passing|green)\b",
            r"\b(?:failing|failed|broken)\s+(?:tests?|suites?|unit tests?)\b",
            r"\b(?:unit\s+tests?|pytest|unittest|test_suite|tests?)\b",
            r"\ball\s+tests?\s+pass\b",
        ]
        if any(re.search(pat, g_lower) for pat in test_driven_patterns):
            return "test_driven"
        return "non_test_driven"

    def _infer_verification(self, description: str) -> tuple[Optional[str], Dict[str, Any]]:
        """Infer an explicit verification strategy and kwargs from subtask description."""
        desc_lower = description.lower()

        file_match = re.search(r"(?:create|write|add|generate)\s+(?:a\s+)?file\s+(?:named\s+)?['\"]?([a-zA-Z0-9_\-\.\/\\]+)['\"]?", desc_lower)
        if file_match:
            filepath = file_match.group(1)
            content_match = re.search(r"containing\s+['\"]([^'\"]+)['\"]", description, re.IGNORECASE)
            spec: Dict[str, Any] = {"path": filepath}
            if content_match:
                spec["expected_content"] = content_match.group(1)
            return ("file_exists", spec)

        dir_match = re.search(r"(?:create|make|add)\s+directory\s+['\"]?([a-zA-Z0-9_\-\.\/\\]+)['\"]?", desc_lower)
        if dir_match:
            return ("directory_exists", {"path": dir_match.group(1)})

        if "test_suite" in desc_lower:
            return ("test_suite", {})

        return (None, {})
