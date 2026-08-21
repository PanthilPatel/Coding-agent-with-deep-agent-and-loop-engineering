"""CLI entry point for the installable `codeagent` command.

Thin wrapper around main.py that:
- Default / no arguments: Auto-detects current working directory (cwd) as target repo
  and launches the interactive REPL against that directory.
- Preserves all flag-based CLI arguments (--goal, --repo, --benchmark, --max-iterations, etc.)
  by delegating directly to main.py's argument parsing and execution pipeline.
- Handles non-git cwd gracefully by initializing a git repository.
"""

import os
import sys
from git import Repo, InvalidGitRepositoryError

from main import parse_args, main as main_run


def ensure_git_repo(path: str) -> None:
    """Ensure the target directory is a valid git repository.
    
    If not, initialize a git repository so branching, checkpointing,
    and diff mechanisms function without crashing.
    """
    try:
        Repo(path)
    except InvalidGitRepositoryError:
        print(f"[codeagent] Initializing new git repository in '{path}'...")
        Repo.init(path)


def main(args=None) -> None:
    """Main CLI entrypoint for `codeagent`."""
    if args is None:
        args = sys.argv[1:]

    # Parse arguments using the canonical argument parser from main.py
    parsed_args = parse_args(args)

    # Auto-detect CWD as target repo if --repo was not explicitly provided and not in benchmark mode
    if not parsed_args.benchmark and not parsed_args.repo:
        cwd = os.getcwd()
        ensure_git_repo(cwd)
        parsed_args.repo = cwd

    # Pass the resolved arguments to main.py's execution logic
    # Reconstructing sys.argv or invoking main's workflow:
    # Since main.py main() calls parse_args() with sys.argv[1:],
    # if --repo was auto-injected, update sys.argv so main() picks it up seamlessly.
    if "--repo" not in args and not parsed_args.benchmark:
        # Prepend --repo <cwd> to sys.argv
        sys.argv = [sys.argv[0], "--repo", parsed_args.repo] + args

    main_run()


if __name__ == "__main__":
    main()
