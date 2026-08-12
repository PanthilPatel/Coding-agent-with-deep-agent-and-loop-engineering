# Testing Skill

## Purpose
Write, run, and improve tests; verify behavior; and increase test coverage.

## When to Use
- You need to write new unit or integration tests
- You need to verify that code behaves correctly
- You need to increase coverage for an existing module
- You need to check whether existing tests adequately cover a feature

## Procedure

### 1. Understand What Needs to Be Tested
- Read the target function, class, or module thoroughly using `read_file`.
- Identify the inputs, outputs, side effects, and edge cases.
- Understand the existing test structure and conventions by reading existing test files.

### 2. Identify Coverage Gaps
- Use `grep` to search for existing tests related to the module.
- List the scenarios that are not currently tested:
  - Happy path (expected inputs → expected outputs)
  - Edge cases (empty input, boundary values, None/null)
  - Error paths (invalid input, exceptions expected)
  - Regression cases (known past bugs)

### 3. Write the Tests
- Follow the existing test file structure and naming conventions.
- Each test should test exactly ONE behavior or scenario.
- Name tests descriptively: `test_<what>_<condition>_<expected_result>`.
- Use fixtures and mocks where appropriate to isolate the unit under test.
- Do NOT write tests that depend on external services, networks, or live APIs.

### 4. Run the New Tests
- Use `run_tests_tool` to run the newly written tests.
- Verify each new test passes.
- If a new test fails immediately, fix the test logic (not the production code) unless the production code is genuinely broken.

### 5. Run the Full Test Suite
- After new tests pass individually, run the complete test suite.
- Verify no existing tests have regressed.

### 6. Iterate if Needed
- If tests reveal unexpected behavior in production code, apply the Debugging Skill for that specific failure.
- If test setup is complex, simplify using better fixtures or mocks.

### 7. Review Coverage
- Confirm the scenarios you intended to cover are now tested.
- Summarize what was added and why.

## Best Practices
- One assertion per test where possible (makes failures easier to diagnose)
- Mock external dependencies (filesystem, network, databases)
- Avoid testing implementation details — test observable behavior
- Tests should be fast, isolated, and repeatable
- A test that always passes regardless of code changes is not a useful test

## Common Pitfalls
- Writing tests that only pass if the code is correct (tautological tests)
- Testing private internals instead of public behavior
- Skipping edge cases (None, empty, boundary values)
- Letting tests become so complex they themselves need to be debugged
