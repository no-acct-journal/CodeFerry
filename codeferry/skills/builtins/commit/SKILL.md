---
name: commit
description: Analyze git diff and create a conventional commit
allowedTools:
  - Bash
  - ReadFile
  - Grep
mode: inline
---

# Task

You need to help the user create a git commit.

## Steps

1. Run `git status` to inspect the current change state
2. Run `git diff` and `git diff --staged` to review the specific changes
3. Analyze the changes and determine the commit type and scope:
   - feat: new feature
   - fix: bug fix
   - docs: documentation changes
   - refactor: refactoring
   - test: tests
   - chore: build/tooling changes
4. Generate a commit message in the format: `type(scope): description`
5. Use `git add` to add relevant files individually (do not add sensitive files such as `.env` or credentials)
6. Run `git commit -m "generated message"`
7. If the user provides additional context, incorporate it into the commit message
8. If the changes span more than 10 files, recommend splitting them into multiple commits

## Notes

- Do not use `git add -A` or `git add .`; add files individually
- Write the commit message in English
- Keep the description under 72 characters

$ARGUMENTS
