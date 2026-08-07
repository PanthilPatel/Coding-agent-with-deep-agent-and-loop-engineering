import os
import re
from git import Repo

def is_git_url(path_or_url: str) -> bool:
    """Check if the given path or URL is a remote Git repository URL."""
    if not path_or_url:
        return False
    # Check for HTTP/HTTPS, SSH git URLs, or ending in .git
    git_url_patterns = [
        r"^https?://.*\.git$",
        r"^git@.*:.*\.git$",
        r"^https?://github\.com/.*",
        r"\.git$"
    ]
    return any(re.match(pattern, path_or_url) for pattern in git_url_patterns)

def clone_repo(repo_url: str, dest_path: str, github_token: str = None) -> Repo:
    """Clone a remote repository to dest_path, injecting a token if provided."""
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    # If token is provided and URL is HTTPS GitHub URL, inject token for auth
    clone_url = repo_url
    if github_token and "github.com" in repo_url and repo_url.startswith("https://"):
        # Format: https://<token>@github.com/user/repo.git
        clone_url = repo_url.replace("https://", f"https://{github_token}@")
        
    return Repo.clone_from(clone_url, dest_path)

def push_to_remote(repo_path: str, branch_name: str, remote_name: str = "origin") -> None:
    """Push local branch commits to the remote repository."""
    repo = Repo(repo_path)
    remote = repo.remote(name=remote_name)
    # Push branch, set upstream
    remote.push(refspec=f"{branch_name}:{branch_name}", set_upstream=True)
