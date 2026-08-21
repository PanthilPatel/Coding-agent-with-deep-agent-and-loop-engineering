"""Git utility functions for local repository operations.

Wraps GitPython operations used by the controller loop and tools package.
Provides repository initialization, branch management, diff retrieval, and
staging/committing iterations.
"""

from git import Repo, InvalidGitRepositoryError


def get_repo(repo_path: str) -> Repo:
    """Return a GitPython Repo object for repo_path, initializing if needed."""
    try:
        return Repo(repo_path)
    except InvalidGitRepositoryError:
        return Repo.init(repo_path)

def ensure_work_branch(repo_path: str, branch_name: str) -> None:
    """Create (or switch to) a dedicated branch for the agent's work, so
    it never commits directly to the main branch."""
    repo = get_repo(repo_path)
    try:
        if not repo.head.is_detached and repo.active_branch.name == branch_name:
            # Untrack internal bookkeeping files if they were previously tracked
            for exc in EXCLUDED_INTERNAL_FILES:
                try:
                    repo.git.rm("--cached", exc, "-f")
                except Exception:
                    pass
            return
    except (TypeError, ValueError):
        pass

    if branch_name in repo.heads:
        try:
            repo.heads[branch_name].checkout()
        except Exception:
            # If checkout fails because generated files (like state.json) differ,
            # force checkout the work branch so agent startup is not blocked.
            repo.git.checkout(branch_name, "--force")
    else:
        repo.git.checkout("-b", branch_name)

    # Ensure internal bookkeeping files are untracked on the work branch
    for exc in EXCLUDED_INTERNAL_FILES:
        try:
            repo.git.rm("--cached", exc, "-f")
        except Exception:
            pass


EXCLUDED_INTERNAL_FILES = {"state.json", ".agent_state.json"}


def get_diff(repo_path: str) -> str:
    repo = get_repo(repo_path)
    # Get unstaged + staged diff
    diff_text = repo.git.diff("HEAD") if repo.heads else repo.git.diff()
    if not diff_text:
        diff_text = repo.git.diff()

    # Filter out internal bookkeeping files (e.g. state.json) from the diff output
    diff_lines = diff_text.splitlines()
    filtered_diff = []
    skipping = False

    for line in diff_lines:
        if line.startswith("diff --git "):
            parts = line.split()
            # e.g. diff --git a/state.json b/state.json
            file_a = parts[2][2:] if len(parts) > 2 and parts[2].startswith("a/") else ""
            file_b = parts[3][2:] if len(parts) > 3 and parts[3].startswith("b/") else ""
            if file_a in EXCLUDED_INTERNAL_FILES or file_b in EXCLUDED_INTERNAL_FILES:
                skipping = True
            else:
                skipping = False
        if not skipping:
            filtered_diff.append(line)

    return "\n".join(filtered_diff).strip()


def commit_iteration(repo_path: str, message: str) -> str:
    """Stage and commit all current changes, excluding internal state files.
    Returns the commit hash, or an empty string if there was nothing to commit."""
    repo = get_repo(repo_path)
    
    # Ensure state.json is ignored/not tracked by git if present in repo root
    import os
    gitignore_path = os.path.join(repo_path, ".gitignore")
    try:
        existing_ignore = ""
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8") as f:
                existing_ignore = f.read()
        needed = [f for f in EXCLUDED_INTERNAL_FILES if f not in existing_ignore]
        if needed:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                if existing_ignore and not existing_ignore.endswith("\n"):
                    f.write("\n")
                for item in needed:
                    f.write(f"{item}\n")
    except Exception:
        pass

    repo.git.add(A=True)
    # Unstage internal bookkeeping files if git added them
    for exc in EXCLUDED_INTERNAL_FILES:
        try:
            repo.git.reset("HEAD", exc)
        except Exception:
            pass

    if not repo.is_dirty(untracked_files=True) and not repo.index.diff("HEAD"):
        return ""
    commit = repo.index.commit(message)
    return commit.hexsha
