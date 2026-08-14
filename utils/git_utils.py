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

def get_diff(repo_path: str) -> str:
    repo = get_repo(repo_path)
    return repo.git.diff()

def commit_iteration(repo_path: str, message: str) -> str:
    """Stage and commit all current changes. Returns the commit hash, or
    an empty string if there was nothing to commit."""
    repo = get_repo(repo_path)
    repo.git.add(A=True)
    if not repo.is_dirty(untracked_files=True) and not repo.index.diff("HEAD"):
        return ""
    commit = repo.index.commit(message)
    return commit.hexsha
