---
name: test
description: Run tests and analyze the results
allowedTools:
  - Bash
  - ReadFile
  - Grep
  - Glob
mode: inline
---

# Task

You need to run the project's test suite and analyze the results.

## Steps

1. Detect the project type by checking, in priority order:
   - `pyproject.toml` or `setup.py` -> Python project, use `pytest`
   - `go.mod` -> Go project, use `go test ./...`
   - `package.json` -> Node.js project, use `npm test`
   - `Cargo.toml` -> Rust project, use `cargo test`
2. Run the corresponding test command and capture the full output
3. Analyze the test results:
   - If all tests pass: report the number of passed tests and coverage, if available
   - If there are failures: distinguish between two failure causes:
     a. **Code bug**: the expected assertion value is correct but the actual value is wrong, indicating an issue in the source code
     b. **Test bug**: the expected assertion value itself is wrong, or the test setup is incorrect, indicating the test needs to be fixed
4. For each failing test:
   - Identify the failure location (file name and test name)
   - Determine whether it is a code bug or a test bug
   - Provide a specific fix suggestion
5. If all tests pass, check whether there are obvious missing test scenarios:
   - Boundary value tests
   - Error path tests
   - Empty input/extreme value tests

$ARGUMENTS
