# Code Review Skill

## Purpose
Review code for correctness, quality, potential bugs, security issues, and maintainability.

## When to Use
- You have been asked to review a code diff or pull request
- You want to identify potential bugs before they reach production
- You need to assess the quality or security posture of a module
- You want to check whether code meets a particular standard

## Procedure

### 1. Understand the Context
- Read the goal or description of the code being reviewed.
- If a diff is available, use `git_diff` to see what changed.
- If reviewing a file from scratch, use `read_file` to read it in full.

### 2. Check Correctness
Ask yourself:
- Does the code do what it claims to do?
- Are there logical errors, off-by-one mistakes, or wrong conditions?
- Are edge cases handled? (empty input, None/null, boundary values)
- Does the code handle errors and exceptions appropriately?
- Are there race conditions or concurrency issues?

### 3. Check Security
Look for:
- **Injection vulnerabilities**: Is user input ever passed to `eval`, `exec`, shell commands, or SQL queries without sanitization?
- **Path traversal**: Are file paths validated to stay within expected boundaries?
- **Hardcoded credentials**: Any API keys, passwords, or secrets in the code?
- **Insecure defaults**: Is authentication or authorization bypassed by default?
- **Sensitive data exposure**: Is sensitive information logged or returned in error messages?

### 4. Check Maintainability
Ask yourself:
- Is the code readable? Would someone unfamiliar with it understand it in 5 minutes?
- Are functions and variables named clearly?
- Is there duplicated logic that could be extracted?
- Are there magic numbers or strings that should be named constants?
- Is the code overly complex for what it does?

### 5. Check Tests
- Are there tests for the changed code?
- Do the tests cover the happy path, edge cases, and error paths?
- Would the tests catch a regression if someone changed this code?

### 6. Compile the Findings
Organize findings by severity:
- **Critical**: Will cause incorrect behavior, data loss, or security breach
- **Major**: Will likely cause bugs or makes future changes difficult
- **Minor**: Style, naming, or clarity issues that don't affect behavior

### 7. Report Findings Clearly
For each finding:
- State the file and line number
- Describe the issue specifically
- Explain why it matters
- Suggest a concrete fix

Do not say "this could be better" without explaining how.

### 8. Summarize
- State the overall quality of the code
- Highlight the most important issues to address first

## What NOT to Do
- Do not apply stylistic preferences as if they are bugs
- Do not report problems you cannot concretely explain
- Do not suggest wholesale rewrites unless truly necessary
- Do not ignore legitimate security concerns because the fix is complex
