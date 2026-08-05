# CodeFerry

CodeFerry is a terminal AI coding assistant built around a streaming agent loop,
tool execution, permission checks, slash commands, session persistence, memory,
skills, sub-agents, worktrees, teams, hooks, and MCP tool integration.

The project is implemented in Python with Textual for the terminal UI and supports
Anthropic, OpenAI Responses API, and OpenAI-compatible Chat Completions providers.

## Features

- Interactive terminal UI with streaming responses, tool call rendering, command
  completion, provider selection, session restore, and permission dialogs
- Non-interactive prompt mode for scripting and automation
- Provider support for `anthropic`, `openai`, and `openai-compat`
- Built-in tools for reading files, writing files, exact-string editing, shell
  commands, glob search, regex grep, user questions, worktree switching, skills,
  sub-agents, agent teams, and MCP tools
- Five-layer permission system with dangerous command blocking, path sandboxing,
  permission rules, permission modes, and human approval
- Project and user configuration layers under `.codeferry/` and `~/.codeferry/`
- Session persistence with JSONL records and resumable conversations
- Automatic and manual context compaction
- Long-term memory extraction into user-level and project-level memory files
- Built-in and custom skills loaded from Markdown frontmatter
- Built-in and custom sub-agent definitions loaded from Markdown frontmatter
- Git worktree isolation for exploratory or parallel agent work
- Multi-agent teams with teammate mailboxes, task tracking, trace trees, and
  optional coordinator mode
- Lifecycle hooks for commands, prompts, HTTP calls, and pre-tool rejection
- MCP server support for dynamically registering external tools

## Requirements

- Python 3.11 or newer
- A configured LLM provider API key
- Git, when using worktree, diff, review, or commit workflows
- Optional: `uv` for dependency management

Runtime dependencies are declared in `pyproject.toml`:

- `textual`
- `anthropic`
- `openai`
- `pyyaml`
- `pydantic`
- `mcp`
- `httpx`

## Installation

Using `uv`:

```bash
uv sync
uv run codeferry
```

Using `pip` in an existing virtual environment:

```bash
pip install -e .
codeferry
```

For development dependencies:

```bash
uv sync --group dev
```

or:

```bash
pip install -e .
pip install pytest pytest-asyncio
```

## Configuration

CodeFerry loads config files in this order:

1. `~/.codeferry/config.yaml`
2. `<project>/.codeferry/config.yaml`
3. `<project>/.codeferry/config.local.yaml`

Later files override or extend earlier files. At least one provider is required.

Create a project config:

```bash
mkdir -p .codeferry
```

Example `.codeferry/config.yaml`:

```yaml
providers:
  - name: claude
    protocol: anthropic
    base_url: https://api.anthropic.com
    model: claude-sonnet-4-5
    thinking: false

permission_mode: default
enable_fork: true
enable_verification_agent: true
teammate_mode: in-process
enable_coordinator_mode: false

worktree:
  symlink_directories:
    - node_modules
    - .venv
    - vendor
  stale_cleanup_interval: 3600
  stale_cutoff_hours: 24
```

Provider API keys can be set through environment variables:

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
```

On Windows PowerShell:

```powershell
$env:ANTHROPIC_API_KEY = "..."
$env:OPENAI_API_KEY = "..."
```

You can also set `api_key` directly in config, but environment variables are
preferred for secrets.

### Provider Examples

Anthropic:

```yaml
providers:
  - name: claude
    protocol: anthropic
    base_url: https://api.anthropic.com
    model: claude-sonnet-4-5
```

OpenAI Responses API:

```yaml
providers:
  - name: openai
    protocol: openai
    base_url: https://api.openai.com/v1
    model: gpt-4.1
```

OpenAI-compatible Chat Completions provider:

```yaml
providers:
  - name: local
    protocol: openai-compat
    base_url: http://localhost:11434/v1
    model: qwen2.5-coder
```

Optional provider fields:

```yaml
context_window: 200000
max_output_tokens: 8192
thinking: true
```

`context_window` is a manual override. When omitted, CodeFerry resolves the
window through provider metadata where available, then a built-in model map, then
a conservative default.

## Running CodeFerry

Interactive UI:

```bash
codeferry
```

Override permission mode:

```bash
codeferry --mode acceptEdits
```

Run a single non-interactive prompt:

```bash
codeferry -p "Summarize this repository"
```

Logs are written to:

```text
.codeferry/debug.log
```

## Permission Modes

CodeFerry classifies tools as `read`, `write`, or `command`.

| Mode | Read | Write | Command |
| --- | --- | --- | --- |
| `default` | allow | ask | ask |
| `acceptEdits` | allow | allow | ask |
| `plan` | allow | ask | ask |
| `bypassPermissions` | allow | allow | allow |
| `custom` | ask | ask | ask |
| `dontAsk` | allow | allow | allow |

Dangerous commands and paths outside the sandbox can still be blocked even when
a broad permission mode is selected.

Permission rules are loaded from:

1. `~/.codeferry/permissions.yaml`
2. `<project>/.codeferry/permissions.yaml`
3. `<project>/.codeferry/permissions.local.yaml`

Example:

```yaml
- rule: "Bash(git *)"
  effect: allow
- rule: "ReadFile(*.env*)"
  effect: deny
```

Rules use `Tool(pattern)` syntax. The pattern is matched with shell-style glob
matching against the tool's primary content field.

## Built-in Tools

| Tool | Category | Purpose |
| --- | --- | --- |
| `ReadFile` | read | Read a file with line numbers |
| `WriteFile` | write | Write a file, creating parent directories when needed |
| `EditFile` | write | Replace one unique exact string in a file |
| `Bash` | command | Execute a shell command |
| `Glob` | read | Find files by glob pattern |
| `Grep` | read | Search file contents by regex |
| `ToolSearch` | read | Load deferred tools by name or keyword search |
| `LoadSkill` | read | Activate a skill and register its specialized tools |
| `AskUserQuestion` | read | Ask the user structured questions from the agent loop |
| `ExitPlanMode` | read | Finish plan mode and show the approval dialog |
| `EnterWorktree` | command | Enter an existing CodeFerry-managed worktree |
| `ExitWorktree` | command | Exit a CodeFerry-managed worktree |
| `Agent` | command | Launch a sub-agent, forked agent, or teammate |
| `TeamCreate` | command | Create a multi-agent team |
| `TeamDelete` | command | Delete a multi-agent team |
| `SendMessage` | command | Send messages between teammates |
| `SyntheticOutput` | read | Emit coordinator-mode synthetic output |

MCP tools are registered dynamically and are exposed with generated names such as
`mcp_<server>_<tool>`.

## Slash Commands

| Command | Description |
| --- | --- |
| `/help [command]` | Show command help |
| `/compact [focus]` | Compact the current context |
| `/clear` | Clear conversation history and start a new session |
| `/plan [task]` | Switch to Plan mode |
| `/session [list | resume <id> | new | delete <id>]` | Manage saved sessions |
| `/mcp` | Show MCP server status |
| `/memory [list | clear | edit]` | Show or manage automatic memories |
| `/permission [mode <mode> | rules | add <rule> <effect> | reset]` | Manage permission mode and rules |
| `/rewind [checkpoint] [option]` | Restore code, conversation, or both from a checkpoint |
| `/status` | Show current mode, session, token, tool, memory, directory, and version info |
| `/skill list` | List loaded skills |
| `/skill info <name>` | Show skill metadata |
| `/skill reload` | Reload skills |
| `/worktree <create|list|enter|exit|status>` | Manage Git worktrees |
| `/tasks [info|cancel] [task-id]` | Inspect or cancel background tasks |
| `/trace` | Show the agent trace tree |
| `/review [focus]` | Ask the model to review current code changes |

Skills are also registered as slash commands after they are loaded. For example,
the built-in `commit`, `review`, and `test` skills can be invoked as `/commit`,
`/review`, and `/test`.

## Sessions

Sessions are stored under:

```text
.codeferry/sessions/
```

Each session is persisted as JSONL records. CodeFerry can resume sessions,
rebuild compacted state from compact boundary records, and preserve tool
use/tool result relationships.

Session titles are generated asynchronously from the conversation. Old sessions
are cleaned up automatically after the configured retention window.

## Memory and Project Instructions

CodeFerry loads persistent project instructions from:

1. `<project>/codeferry.md`
2. `<project>/.codeferry/codeferry.md`
3. `~/.codeferry/codeferry.md`

Instruction files support project-local includes:

```markdown
@include docs/architecture.md
```

Automatic memories are stored in:

```text
~/.codeferry/memories.md
<project>/.codeferry/memories.md
```

The memory system separates user-level preferences and feedback from
project-level knowledge and references.

## Skills

Skills are reusable task procedures defined in Markdown with YAML frontmatter.

Load order:

1. `<project>/.codeferry/skills`
2. `~/.codeferry/skills`
3. built-in skills

Project skills override user and built-in skills with the same name.

Example `.codeferry/skills/audit.md`:

```markdown
---
name: audit
description: Inspect security-sensitive code paths
allowedTools:
  - Bash
  - ReadFile
  - Grep
  - Glob
mode: inline
context: full
---

# Audit Skill

Review the requested area for security, correctness, and operational risks.

$ARGUMENTS
```

Directory skills are also supported:

```text
.codeferry/skills/my-skill/
  SKILL.md
  tool.json
  references/
```

When a directory skill contains `tool.json`, its specialized tools can be
registered when the skill is loaded.

Built-in skills:

- `commit`: inspect git diff and create a conventional commit
- `review`: review code changes for bugs, risks, and maintainability issues
- `test`: run tests and analyze results

## Sub-Agents

Sub-agents are Markdown definitions with YAML frontmatter.

Load order:

1. `<project>/.codeferry/agents`
2. `~/.codeferry/agents`
3. built-in agents

Example `.codeferry/agents/security-reviewer.md`:

```markdown
---
name: security-reviewer
description: Review security-sensitive changes
model: haiku
maxTurns: 20
permissionMode: dontAsk
disallowedTools:
  - WriteFile
  - EditFile
---

You are a security review specialist. Report concrete findings with file paths.
```

Built-in agent types:

- `Explore`: fast read-only codebase exploration
- `Plan`: read-only implementation planning
- `general-purpose`: independent context for self-contained tasks
- `Verification`: read-only verification agent, enabled with
  `enable_verification_agent: true`

The `Agent` tool can run a predefined sub-agent, fork the current conversation
when `enable_fork: true`, run in the background, or spawn a teammate inside a
team.

## Worktrees

CodeFerry can create and enter isolated Git worktrees. This is useful for
parallel agent work or experiments that should not touch the main working tree.

Commands:

```text
/worktree create <name> [base-branch]
/worktree list
/worktree enter <name>
/worktree exit [--remove] [--discard]
/worktree status
```

Worktree names are validated as slugs. Nested names such as `team/alice` are
flattened for branch names.

## Agent Teams

Teams let multiple long-running agents coordinate through tasks and mailboxes.

Typical flow:

1. The lead agent calls `TeamCreate`.
2. The lead spawns teammates with the `Agent` tool using `team_name` and `name`.
3. Teammates work in isolated worktrees.
4. Teammates communicate with `SendMessage`.
5. The lead receives teammate notifications and synthesizes results.
6. The team can be deleted with `TeamDelete` after members are idle.

`teammate_mode: in-process` runs teammates in-process. In interactive terminals,
CodeFerry can also detect supported pane backends such as tmux or iTerm2.

Coordinator mode can be enabled with:

```yaml
enable_coordinator_mode: true
```

When active, the lead's tools are narrowed to coordination and dispatch tools
after a team is created.

## MCP Servers

MCP servers can be configured in `.codeferry/config.yaml`.

Stdio server:

```yaml
mcp_servers:
  - name: github
    command: npx
    args:
      - -y
      - "@modelcontextprotocol/server-github"
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
```

HTTP server:

```yaml
mcp_servers:
  - name: remote
    url: https://api.example.com/mcp
    headers:
      Authorization: "Bearer ${TOKEN}"
```

Only explicitly declared environment variables are passed to child MCP
processes, plus `PATH`.

## Hooks

Hooks run actions on lifecycle events. Supported action types are:

- `command`
- `prompt`
- `http`
- `agent`

Supported events include:

- `session_start`
- `session_end`
- `turn_start`
- `turn_end`
- `pre_tool_use`
- `post_tool_use`
- `pre_send`
- `post_receive`
- `startup`
- `shutdown`
- `error`
- `compact`
- `permission_request`
- `file_change`
- `command_execute`

Example:

```yaml
hooks:
  - id: block-dangerous-rm
    event: pre_tool_use
    if: 'tool == "Bash" && args.command =~ /rm\s+-rf/'
    action:
      type: command
      command: echo dangerous command blocked
    reject: true

  - id: format-after-write
    event: post_tool_use
    if: 'tool == "WriteFile"'
    action:
      type: command
      command: ruff format .
    async: true
```

Conditions support:

- `==`
- `!=`
- `=~` for regular expressions
- `~=` for glob matching
- `&&` or `||` within a single condition expression

`reject: true` is only valid on `pre_tool_use`.

## Context Management

CodeFerry tracks token usage and can compact long conversations. Compaction
preserves a recent message tail and stores a compact boundary record so future
session resumes can rebuild the compacted state without replaying the original
prefix.

For large tool results, CodeFerry can persist oversized content and replace it
with stable preview tags to keep prompt-cache prefixes byte-identical across
turns.

Anthropic requests mark system, tool schemas, and the final user message tail
with ephemeral cache controls to improve prompt cache reuse.

## Project Layout

```text
codeferry/
  app.py                 Textual application
  agent.py               Agent loop, tool execution, compaction, events
  client.py              Anthropic, OpenAI, and OpenAI-compatible clients
  config.py              Config loading and merging
  validator.py           Config validation and context-window fallback
  prompts.py             System and environment prompt construction
  conversation.py        Internal conversation model
  serialization.py       Provider request serialization
  commands/              Slash command framework and handlers
  tools/                 Built-in tool implementations
  permissions/           Permission modes, sandbox, rules, dangerous command checks
  memory/                Sessions, memories, project instructions
  hooks/                 Lifecycle hook engine
  mcp/                   MCP client and tool wrappers
  skills/                Skill parser, loader, executor, built-ins
  agents/                Sub-agent parser, loader, fork, task, trace, notifications
  teams/                 Multi-agent team model, mailbox, backend spawning
  worktree/              Git worktree management and cleanup
tests/                   Unit and integration tests
```

## Development

Run tests:

```bash
uv run pytest tests
```

or, if dependencies are installed in the active environment:

```bash
python -m pytest tests
```

Run the standalone sub-agent verification script:

```bash
python tests/verify_subagent.py
```

Compile-check the package:

```bash
python -m compileall codeferry tests
```

## Current Status

CodeFerry is under active development. The codebase already includes the main
agent loop, UI, tools, commands, permissions, sessions, memory, skills,
sub-agents, worktrees, teams, hooks, MCP integration, and tests, but APIs and
configuration details may still change.
