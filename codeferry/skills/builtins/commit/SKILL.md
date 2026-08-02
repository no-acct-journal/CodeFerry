---
name: commit
description: Analyze git diff and create a conventional commit
allowedTools:
  - Bash
  - ReadFile
  - Grep
mode: inline
context: full
---

# Commit Skill

## Goal

Help the user inspect the current repository changes and create a focused
conventional commit.

## Workflow

1. Run `git status` to inspect the current change state
2. Run `git diff` and `git diff --staged` to review the specific changes
3. Analyze the changes and choose the commit type and scope:
   - feat: new feature
   - fix: bug fix
   - docs: documentation changes
   - refactor: refactoring
   - test: tests
   - chore: build/tooling changes
4. Generate a commit message in the format `type(scope): description`
5. If the user provides extra context, incorporate it into the message
6. Add relevant files individually; never add sensitive files such as `.env` or credentials
7. Run `git commit -m "generated message"`
8. If the changes span more than 10 files or multiple unrelated areas, recommend splitting them into multiple commits before committing

## Notes

- Do not use `git add -A` or `git add .`; add files individually
- Write the commit message in English
- Keep the description under 72 characters
- After committing, report the final commit hash and message

$ARGUMENTS
