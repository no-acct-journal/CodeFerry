from __future__ import annotations

import time
from typing import TYPE_CHECKING

from codeferry.commands.registry import Command, CommandContext, CommandType

if TYPE_CHECKING:
    from codeferry.agents.task_manager import TaskManager


def _format_elapsed(start: float, end: float | None) -> str:
    elapsed = (end or time.monotonic()) - start
    if elapsed >= 60:
        return f"{elapsed / 60:.1f}m"
    return f"{elapsed:.0f}s"


def _format_status(status: str) -> str:
    icons = {"running": "⏳", "completed": "✓", "failed": "✗", "cancelled": "⊘"}
    return f"{icons.get(status, '?')} {status}"


def create_tasks_handler(task_manager: TaskManager):


    async def handler(ctx: CommandContext) -> None:
        args = ctx.args.strip()
        parts = args.split(maxsplit=1) if args else []
        subcmd = parts[0] if parts else ""

        if subcmd == "info":
            if len(parts) < 2:
                ctx.ui.add_system_message("Usage: /tasks info <task-id>")
                return
            task_id = parts[1].strip()
            bg = task_manager.get(task_id)
            if bg is None:
                ctx.ui.add_system_message(f"Task not found: {task_id}")
                return
            elapsed = _format_elapsed(bg.start_time, bg.end_time)
            lines = [
                f"Task details: {task_id}",
                f"  Name:    {bg.name}",
                f"  Status:  {_format_status(bg.status)}",
                f"  Elapsed: {elapsed}",
                f"  Tokens:  ↑{bg.progress.input_tokens} ↓{bg.progress.output_tokens}",
            ]
            if bg.result:
                result_preview = bg.result[:2000]
                if len(bg.result) > 2000:
                    result_preview += "\n... (truncated)"
                lines.append(f"  Result:\n{result_preview}")
            ctx.ui.add_system_message("\n".join(lines))
            return

        if subcmd == "cancel":
            if len(parts) < 2:
                ctx.ui.add_system_message("Usage: /tasks cancel <task-id>")
                return
            task_id = parts[1].strip()
            if task_manager.cancel(task_id):
                ctx.ui.add_system_message(f"Cancelled task: {task_id}")
            else:
                ctx.ui.add_system_message(
                    f"Unable to cancel task: {task_id} (it may not exist or may already be complete)"
                )
            return

        # Default: list all tasks.
        tasks = task_manager.list_tasks()
        if not tasks:
            ctx.ui.add_system_message("No background tasks")
            return

        lines = ["Background Tasks:"]
        for bg in tasks:
            elapsed = _format_elapsed(bg.start_time, bg.end_time)
            lines.append(
                f"  [{bg.id}] {bg.name:<20} {_format_status(bg.status):<14} {elapsed}"
            )
        ctx.ui.add_system_message("\n".join(lines))

    return handler


def create_tasks_command(task_manager: TaskManager) -> Command:
    return Command(
        name="tasks",
        description="Manage background tasks (/tasks, /tasks info <id>, /tasks cancel <id>)",
        type=CommandType.LOCAL,
        handler=create_tasks_handler(task_manager),
        aliases=["task"],
        usage="/tasks [info|cancel] [task-id]",
    )
