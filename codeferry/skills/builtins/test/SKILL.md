---
name: test
description: Run tests and analyze the results
allowedTools:
  - Bash
  - ReadFile
  - Grep
  - Glob
mode: inline
context: full
---

# Test Skill

## Goal

Run the appropriate project test suite and explain the result in a way that helps
the user decide the next action.

## Workflow

1. Detect the project type by checking, in priority order:
   - `pyproject.toml` or `setup.py` -> Python project, use `pytest`
   - `go.mod` -> Go project, use `go test ./...`
   - `package.json` -> Node.js project, use `npm test`
   - `Cargo.toml` -> Rust project, use `cargo test`
2. Run the corresponding test command and capture the full output
3. If the test command is missing or unsupported, report the detected project files and the command you would expect
4. Analyze the test results:
   - If all tests pass: report the number of passed tests and coverage, if available
   - If there are failures: distinguish between two failure causes:
     - **Code bug**: the expected assertion value is correct but the actual value is wrong, indicating an issue in the source code
     - **Test bug**: the expected assertion value itself is wrong, or the test setup is incorrect, indicating the test needs to be fixed
5. For each failing test:
   - Identify the failure location (file name and test name)
   - Determine whether it is a code bug or a test bug
   - Provide a specific fix suggestion
6. If all tests pass, check whether there are obvious missing test scenarios:
   - Boundary value tests
   - Error path tests
   - Empty input/extreme value tests

## Output Format

```
## Test Report

### Command
- `test command`

### Result
- Pass/fail summary, including counts and coverage when available

### Findings
- Failure analysis or missing test scenarios

### Next Action
- Concrete recommendation
```

$ARGUMENTS
