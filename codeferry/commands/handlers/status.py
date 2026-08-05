from __future__ import annotations

import os

from codeferry.commands.registry import Command, CommandContext, CommandType


VERSION = "v0.9.0"


async def handle_status(ctx: CommandContext) -> None:
    lines = ["codeferry Status", "─────────────"]

    mode = ctx.agent.permission_mode.value if ctx.agent else "unknown"
    lines.append(f"Mode: {mode}")

    if ctx.session:
        m = ctx.session.meta
        lines.append(f"Session: {m.id} ({m.message_count} messages)")
    else:
        lines.append("Session: none")

    input_tokens, output_tokens = ctx.ui.get_token_count()
    context_window = ctx.agent.context_window if ctx.agent else 200_000
    pct = int(input_tokens / context_window * 100) if context_window else 0
    lines.append(f"Token: {input_tokens:,} / {context_window:,} ({pct}%)")

    if ctx.agent:
        enabled = [t for t in ctx.agent.registry.list_tools()
                   if ctx.agent.registry.is_enabled(t.name)]
        lines.append(f"Tools: {len(enabled)} enabled")


    if ctx.memory_manager:
        content = ctx.memory_manager.load()
        mem_lines = [l for l in content.split("\n") if l.strip().startswith("- ")]
        lines.append(f"Memory: {len(mem_lines)} entries")

    work_dir = ctx.agent.work_dir if ctx.agent else os.getcwd()
    lines.append(f"Working directory: {work_dir}")
    lines.append(f"Version: {VERSION}")

    ctx.ui.add_system_message("\n".join(lines))


STATUS_COMMAND = Command(
    name="status",
    aliases=["s"],
    description="Show status information",
    usage="/status",
    type=CommandType.LOCAL,
    handler=handle_status,
)
