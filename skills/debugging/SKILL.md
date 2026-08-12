# Debugging Skill

## Purpose
Diagnose and fix software bugs, failing tests, runtime errors, and unexpected behavior.

## When to Use
- A test is failing and the root cause is unknown
- There is a runtime error or exception in the code
- The program produces incorrect or unexpected output
- A previously passing test has regressed

## Procedure

### 1. Reproduce the Failure
- Read the test output or error message carefully.
- Identify the exact assertion, exception, or mismatch.
- Note the file name, line number, and test name if available.

### 2. Inspect Relevant Files
- Use `read_file` to open the failing test and the code it tests.
- Do NOT edit before you understand the code.
- Read any helper modules, fixtures, or dependencies involved.

### 3. Search for Related Code
- Use `grep` to search for the failing symbol, function, or class name.
- Look for recent changes in the area that may have introduced the regression.

### 4. Identify the Root Cause
- Trace the execution path from the test failure back to the source.
- Distinguish between the symptom (what fails) and the root cause (why it fails).
- Do not fix symptoms — fix the root cause.

### 5. Make the Smallest Appropriate Change
- Apply the minimal change that corrects the root cause.
- Do not refactor unrelated code in the same iteration.
- Prefer targeted edits over wholesale rewrites.

### 6. Run the Relevant Tests
- Use `run_tests_tool` to verify your fix.
- If a single test is relevant, run it in isolation first.
- Then run the full test suite to check for regressions.

### 7. Analyze Failures if Tests Still Fail
- Re-read the new error output carefully.
- Do not blindly retry the same fix.
- Identify what is still wrong and repeat from Step 2.

### 8. Iterate When Necessary
- If a fix approach has failed twice, try a meaningfully different strategy.
- Use `git_diff` to review what you have changed so far.

### 9. Verify the Final Result
- Confirm the originally failing test now passes.
- Confirm no previously passing tests are now failing.
- Summarize what was changed and why.

## Common Pitfalls
- Fixing the symptom instead of the root cause
- Making multiple unrelated changes in one iteration (makes failures harder to diagnose)
- Repeating the same failed fix without changing strategy
- Not reading the code before editing it
