
"""Skills package for the coding agent.

Skills are reusable instruction sets that teach the agent HOW to approach
a particular type of task. They are NOT tools — tools perform actions,
skills provide procedure and strategy.

Usage::

    from skills import load_skill, select_skill, list_skills

    skill = select_skill("fix the failing authentication test", skills_dir="skills/")
    if skill:
        print(f"[SKILL] {skill.name}")
        # Provide skill.content to the agent as context
"""

from skills.loader import SkillInfo, SkillLoader, select_skill, load_skill, list_skills

__all__ = [
    "SkillInfo",
    "SkillLoader",
    "select_skill",
    "load_skill",
    "list_skills",
]
