# Refactoring Skill

## Purpose
Restructure existing code to improve maintainability, reduce duplication, and improve organization — without changing external behavior.

## When to Use
- A function or class is too large and has multiple responsibilities
- There is duplicated logic across multiple files or functions
- Code is difficult to read, understand, or extend
- Module boundaries are unclear or poorly organized

## Core Rule
**Refactoring must not change observable behavior.**
If a refactor causes a test to fail, either the refactor introduced a bug or the test was testing an implementation detail it should not have been.

## Procedure

### 1. Read and Understand the Existing Code
- Use `read_file` to read all relevant files before touching anything.
- Map the dependencies: what calls this? What does it call?
- Do not refactor code you have not fully read.

### 2. Run the Existing Tests First
- Use `run_tests_tool` to confirm the baseline passes before any changes.
- If tests are already failing, apply the Debugging Skill first. Do not refactor broken code.

### 3. Identify the Specific Refactoring Goal
Choose one of:
- **Extract function/method**: Move a block of code into its own named function.
- **Eliminate duplication**: Identify repeated patterns and unify them.
- **Rename for clarity**: Rename poorly named variables, functions, or classes.
- **Reorganize modules**: Move code to a more appropriate file or package.
- **Simplify logic**: Replace complex conditionals or loops with clearer equivalents.

Work on ONE type of refactoring per iteration.

### 4. Apply the Change Incrementally
- Make the smallest refactoring step that improves the code.
- Prefer small, safe steps over large sweeping changes.
- Keep a clear mental model of before and after.

### 5. Run Tests After Each Change
- Use `run_tests_tool` after every meaningful change.
- If any test fails after a refactoring step, undo or fix the issue before continuing.
- A refactoring that breaks tests is a bug, not a style choice.

### 6. Review the Diff
- Use `git_diff` to inspect what has changed.
- Confirm the changes are limited to the intended scope.
- Look for unintended side effects.

### 7. Repeat for the Next Refactoring Goal
- Once one step is complete and tests pass, decide whether to continue.
- Commit clean checkpoints using `git_commit` after each successful step.

### 8. Summarize
- Describe what was restructured and why.
- Confirm behavior is unchanged (tests pass).

## What NOT to Do
- Do not add new features during a refactoring iteration
- Do not fix bugs during a refactoring iteration (do that separately)
- Do not rename everything at once — do it one symbol at a time
- Do not refactor code you do not have tests for (add tests first)

## Common Refactoring Patterns
- **Single Responsibility**: Each function does exactly one thing
- **DRY (Don't Repeat Yourself)**: Extract repeated logic into a shared helper
- **Early Return**: Replace deeply nested conditionals with early returns
- **Descriptive Names**: Replace `x`, `tmp`, `data` with meaningful names
- **Small Functions**: Functions should fit in one screen of text
