---
name: review
description: Review code changes
allowedTools:
  - Bash
  - ReadFile
  - Grep
  - Glob
mode: fork
context: none
---

# Task

You need to review the current code changes.

## Steps

1. Run `git diff` to inspect unstaged changes, and run `git diff --staged` to inspect staged changes
2. If both are empty, run `git log -1 --format=%H` to get the latest commit, then run `git diff HEAD~1` to inspect it
3. Review the changes file by file and analyze them across the following five dimensions:
   - **Logical correctness**: whether the algorithm is correct, edge cases are handled, and null/exception cases are covered
   - **Security**: whether there are injection vulnerabilities, sensitive information leaks, or permission issues
   - **Performance**: whether there are unnecessary loops, N+1 queries, or memory leak risks
   - **Code style**: whether naming is clear, project conventions are followed, and duplicate code exists
   - **Maintainability**: whether abstractions are reasonable, dependencies are clear, and the code is easy to test
4. Report issues by severity:
   - **Critical**: must be fixed; correctness or security issue
   - **Warning**: should be fixed; potential risk or clear room for improvement
   - **Info**: optional improvement; code quality suggestion
5. Also provide positive feedback for parts with good code quality

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
