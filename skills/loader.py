"""Skills loader and selector for the coding agent.

Responsibilities:
- Discover available skills by scanning the skills directory.
- Load SKILL.md content safely (handles missing/empty/invalid files gracefully).
- Select an appropriate skill based on the task description using deterministic
  keyword matching — no LLM call required for selection.

Design:
- ``SkillLoader`` is the core class; use it when you need fine control.
- Module-level helpers ``select_skill``, ``load_skill``, ``list_skills`` wrap
  ``SkillLoader`` for the common single-call use case.
- The selection mechanism lives entirely in ``_build_keyword_map()`` and
  ``_match_skill()``, so it can be replaced with an LLM-based selector later
  without changing ``SkillLoader`` or any call sites.

Safety:
- File reads are confined to the resolved skills root directory; paths that
  would escape it are rejected.
- Invalid or empty SKILL.md files produce a warning and an empty content
  string rather than raising an exception.
- ``select_skill`` never raises — it returns None for unknown tasks.
"""

import os
import pathlib
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SkillInfo:
    """Metadata and content for a single skill.

    Attributes:
        name:    The skill's directory name (e.g. ``"debugging"``).
        path:    Absolute path to the SKILL.md file.
        content: Full text content of the SKILL.md file.  Empty string if the
                 file was missing, empty, or unreadable.
    """
    name: str
    path: str
    content: str

    def __bool__(self) -> bool:
        """A skill is truthy only when it has actual content."""
        return bool(self.content.strip())


# ---------------------------------------------------------------------------
# Keyword map — the deterministic selection mechanism
# ---------------------------------------------------------------------------

# Maps skill name → list of trigger keywords/phrases (all lower-cased).
# The selector scores each skill by counting how many keyword tokens appear
# in the task description; the skill with the highest score wins.
#
# To replace this with an LLM-based selector later, implement a function
# with the same signature as ``_match_skill()`` and swap it in.

_DEFAULT_KEYWORD_MAP: dict[str, list[str]] = {
    "debugging": [
        "fix", "bug", "error", "fail", "failing", "broken", "debug",
        "exception", "traceback", "crash", "not working", "incorrect",
        "unexpected", "wrong", "issue", "problem", "diagnose", "runtime",
        "regression", "investigate",
    ],
    "testing": [
        "test", "tests", "unittest", "pytest", "coverage", "assert",
        "write test", "add test", "spec", "verify", "check", "passing",
        "suite", "fixture", "mock",
    ],
    "refactoring": [
        "refactor", "restructure", "reorganize", "clean up", "cleanup",
        "simplify", "improve", "maintainability", "duplication", "duplicate",
        "extract", "rename", "move", "modular", "readable", "readability",
        "decouple", "decompose",
    ],
    "code_review": [
        "review", "code review", "pr", "pull request", "diff",
        "quality", "security", "vulnerability", "audit", "inspect",
        "check for", "find bugs", "potential issue", "best practice",
        "maintainability", "code quality", "smell", "anti-pattern",
    ],
    "git": [
        "git", "commit", "branch", "diff", "status", "log", "history",
        "stage", "stash", "merge", "rebase", "checkout", "push", "pull",
        "repository", "repo", "changes",
    ],
}


def _match_skill(task: str, keyword_map: dict[str, list[str]]) -> Optional[str]:
    """Return the name of the best-matching skill for ``task``, or None.

    Scoring:
    - For each skill, count how many of its keywords are present in the
      lower-cased task string.
    - The skill with the highest count wins.
    - Ties are broken by the order in ``keyword_map`` (Python dict insertion
      order is stable in 3.7+).
    - If no keyword matches, return None so the caller can decide on a fallback.
    """
    task_lower = task.lower()
    scores: dict[str, int] = {}
    for skill_name, keywords in keyword_map.items():
        score = sum(1 for kw in keywords if kw in task_lower)
        scores[skill_name] = score

    best_name = max(scores, key=lambda k: scores[k])
    if scores[best_name] == 0:
        return None
    return best_name


# ---------------------------------------------------------------------------
# Skill loader
# ---------------------------------------------------------------------------

class SkillLoader:
    """Discovers and loads skills from a directory on disk.

    The expected layout is::

        <skills_dir>/
            <skill_name>/
                SKILL.md
            <skill_name>/
                SKILL.md
            ...

    Adding a new skill requires only creating a new sub-directory with a
    ``SKILL.md`` file — no Python changes are needed.

    Args:
        skills_dir: Path to the directory containing skill sub-directories.
                    Defaults to ``"skills"`` (relative to the current working
                    directory).
    """

    SKILL_FILENAME = "SKILL.md"

    def __init__(self, skills_dir: str = "skills") -> None:
        self._root = pathlib.Path(skills_dir).resolve()

    @property
    def root(self) -> pathlib.Path:
        return self._root

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_available(self) -> list[str]:
        """Return the names of all skills that have a readable SKILL.md."""
        if not self._root.is_dir():
            return []
        names = []
        for entry in sorted(self._root.iterdir()):
            if entry.is_dir() and (entry / self.SKILL_FILENAME).is_file():
                names.append(entry.name)
        return names

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, skill_name: str) -> Optional[SkillInfo]:
        """Load a skill by name.

        Returns:
            A ``SkillInfo`` instance.  ``content`` will be empty if the file
            is missing or cannot be read; the method still returns a
            ``SkillInfo`` (with empty content) rather than raising.

        Returns None only when ``skill_name`` would escape the root
        directory (path-traversal attempt).
        """
        # --- safety: resolve and check boundary ---
        skill_dir = (self._root / skill_name).resolve()
        try:
            skill_dir.relative_to(self._root)
        except ValueError:
            print(f"[SKILL] WARNING: '{skill_name}' escapes the skills root — ignoring.")
            return None

        skill_file = skill_dir / self.SKILL_FILENAME
        content = self._read_file_safe(skill_file)

        return SkillInfo(
            name=skill_name,
            path=str(skill_file),
            content=content,
        )

    def _read_file_safe(self, path: pathlib.Path) -> str:
        """Read a file and return its text content.

        Returns an empty string on any error (missing, permission denied,
        encoding error, etc.) and prints a warning.  Never raises.
        """
        if not path.exists():
            print(f"[SKILL] WARNING: SKILL.md not found at '{path}'.")
            return ""
        try:
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                print(f"[SKILL] WARNING: SKILL.md at '{path}' is empty.")
            return text
        except Exception as exc:
            print(f"[SKILL] WARNING: Could not read '{path}': {exc}")
            return ""

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select(
        self,
        task: str,
        keyword_map: Optional[dict[str, list[str]]] = None,
    ) -> Optional[SkillInfo]:
        """Select and load the most appropriate skill for a task description.

        Uses deterministic keyword matching — no LLM call.  The keyword map
        can be overridden to customise selection behaviour.

        Args:
            task:        Free-text description of the current task/goal.
            keyword_map: Optional override for the keyword→skill mapping.
                         When None, uses the built-in ``_DEFAULT_KEYWORD_MAP``.

        Returns:
            The best-matching ``SkillInfo``, or None if no skill matched or
            if the matched skill file cannot be found.
        """
        kw_map = keyword_map if keyword_map is not None else _DEFAULT_KEYWORD_MAP
        matched_name = _match_skill(task, kw_map)
        if matched_name is None:
            return None

        # Only return a skill that is actually available on disk
        available = self.list_available()
        if matched_name not in available:
            return None

        return self.load(matched_name)


# ---------------------------------------------------------------------------
# Module-level convenience helpers
# ---------------------------------------------------------------------------

def select_skill(
    task: str,
    skills_dir: str = "skills",
    keyword_map: Optional[dict[str, list[str]]] = None,
) -> Optional[SkillInfo]:
    """Select the best skill for a task description.

    Convenience wrapper around ``SkillLoader.select()``.

    Args:
        task:        Free-text description of the task or goal.
        skills_dir:  Path to the skills directory (default: ``"skills"``).
        keyword_map: Optional override for keyword matching.

    Returns:
        A ``SkillInfo`` with name, path, and content — or None if no skill
        matches or the skills directory does not exist.
    """
    return SkillLoader(skills_dir).select(task, keyword_map=keyword_map)


def load_skill(skill_name: str, skills_dir: str = "skills") -> Optional[SkillInfo]:
    """Load a specific skill by name.

    Convenience wrapper around ``SkillLoader.load()``.

    Args:
        skill_name:  The skill directory name (e.g. ``"debugging"``).
        skills_dir:  Path to the skills directory (default: ``"skills"``).

    Returns:
        A ``SkillInfo`` instance, or None if the name would escape the root.
    """
    return SkillLoader(skills_dir).load(skill_name)


def list_skills(skills_dir: str = "skills") -> list[str]:
    """Return the names of all available skills in the skills directory.

    Convenience wrapper around ``SkillLoader.list_available()``.
    """
    return SkillLoader(skills_dir).list_available()
