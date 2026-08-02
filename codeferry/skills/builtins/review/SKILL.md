---
name: review
description: Review code changes for bugs, risks, and maintainability issues
allowedTools:
  - Bash
  - ReadFile
  - Grep
  - Glob
mode: fork
context: none
---

# Review Skill

## Goal

Review the current code changes and report concrete findings that the author can
act on.

## Workflow

1. Run `git diff` to inspect unstaged changes, and run `git diff --staged` to inspect staged changes
2. If both are empty, run `git log -1 --format=%H` to get the latest commit, then run `git diff HEAD~1` to inspect it
3. Review the changes file by file across these dimensions:
   - **Correctness**: algorithms, edge cases, null handling, exceptions, and regressions
   - **Security**: injection risks, sensitive data exposure, permission issues, and unsafe defaults
   - **Performance**: unnecessary loops, N+1 queries, excess I/O, memory pressure, and avoidable blocking work
   - **Project fit**: naming, style, local conventions, duplication, and API consistency
   - **Maintainability**: abstraction boundaries, dependency direction, testability, and future change risk
4. Report issues by severity:
   - **Critical**: must be fixed; correctness, data loss, or security issue
   - **Warning**: should be fixed; likely bug, brittle behavior, or missing test coverage
   - **Info**: optional improvement; readability, consistency, or maintainability suggestion
5. Include positive feedback only when there is a specific implementation detail worth calling out
6. If there are no findings, say so clearly and mention any residual risk or tests that were not run

## Output Format

```
## Review Report

### Critical
- [file:line] Issue description and suggested fix

### Warning
- [file:line] Issue description and suggested fix

### Info
- [file:line] Improvement suggestion

### Positive Feedback
- Notable design or implementation worth highlighting
```

$ARGUMENTS
