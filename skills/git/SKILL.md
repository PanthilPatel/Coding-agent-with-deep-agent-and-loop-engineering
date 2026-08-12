# Git Skill

## Purpose
Inspect repository state, review changes, manage commits, and perform branch-related operations.

## When to Use
- You need to understand what has changed in the repository
- You want to review a diff before committing
- You need to create or check a commit
- You need to understand the branch or commit history
- You are asked to stage, commit, or describe changes

## Procedure

### 1. Check the Current Repository State
- Use `git_status` to see what files have been modified, staged, or are untracked.
- This tells you the starting point before any operations.

### 2. Review What Changed
- Use `git_diff` to see the full diff of unstaged changes.
- Read the diff carefully:
  - Lines starting with `+` are additions.
  - Lines starting with `-` are deletions.
  - Context lines (no prefix) are unchanged.
- Verify the changes match what was intended.

### 3. Review Commit History (if needed)
- Use `git_log` to see recent commits on the current branch.
- This helps understand what the agent or developer has already done.
- Use `max_entries=N` to limit the number of commits shown.

### 4. Prepare a Commit (if needed)
- Before committing, confirm:
  - The changes are correct (review with `git_diff`)
  - The tests pass (use `run_tests_tool`)
  - The commit covers one logical change, not multiple unrelated ones
- Write a clear commit message:
  - Format: `<type>: <short description>` (e.g., `fix: correct off-by-one in loop`, `feat: add caching layer`)
  - Be specific about what changed and why

### 5. Create the Commit
- Use `git_commit` with a descriptive message.
- Note: If `--require-approval` is active, the controller handles commits — `git_commit` will refuse to run.

### 6. Verify After the Commit
- Run `git_status` to confirm the working tree is clean after the commit.
- Run `git_log` to confirm the commit appears in history with the correct message.

## Commit Message Format
```
<type>: <short description (50 chars or less)>

Optional longer explanation of why this change was made.
```

**Types:**
- `fix`: Bug fix
- `feat`: New feature
- `refactor`: Code restructuring without behavior change
- `test`: Adding or fixing tests
- `docs`: Documentation changes
- `chore`: Build, config, or tooling changes

## What NOT to Do
- Do not commit unverified or broken code
- Do not combine unrelated changes in a single commit
- Do not write vague commit messages like "fix stuff" or "changes"
- Do not commit files that contain secrets, API keys, or credentials
- Do not commit generated files (logs, __pycache__, .pyc files)

## Common Workflows

### Inspect and review
1. `git_status` → see what's changed
2. `git_diff` → read the changes
3. `git_log` → check recent history

### Review and commit
1. `run_tests_tool` → verify tests pass
2. `git_diff` → confirm changes are correct
3. `git_commit` → create the commit with a clear message
4. `git_status` → confirm clean working tree
