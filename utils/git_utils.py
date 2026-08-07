from git import Repo, InvalidGitRepositoryError

def get_repo(repo_path: str) -> Repo:
    try:
        return Repo(repo_path)
    except InvalidGitRepositoryError:
        return Repo.init(repo_path)

def ensure_work_branch(repo_path: str, branch_name: str) -> None:
    """Create (or switch to) a dedicated branch for the agent's work, so
    it never commits directly to the main branch."""
    repo = get_repo(repo_path)
    if branch_name in repo.heads:
        repo.heads[branch_name].checkout()
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
