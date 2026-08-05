from __future__ import annotations

from codeferry.commands.registry import Command, CommandContext, CommandType


async def handle_memory(ctx: CommandContext) -> None:
    mm = ctx.memory_manager
    if mm is None:
        ctx.ui.add_system_message("Memory manager is not initialized")
        return


    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "":
        display = mm.get_display_text()
        ctx.ui.add_system_message(display)

    elif sub == "list":
        display = mm.get_display_text()
        ctx.ui.add_system_message(display)

    elif sub == "clear":
        mm.clear()
        ctx.ui.add_system_message("All automatic memories have been cleared.")

    elif sub == "edit":
        ctx.ui.add_system_message(
            f"Edit memory files:\n"
            f"  User level: {mm.user_path}\n"
            f"  Project level: {mm.project_path}"
        )

    else:
        ctx.ui.add_system_message(
            "Usage: /memory [list | clear | edit]"
        )


MEMORY_COMMAND = Command(
    name="memory",
    description="Memory management",
    usage="/memory [list | clear | edit]",
    type=CommandType.LOCAL,
    handler=handle_memory,
)
