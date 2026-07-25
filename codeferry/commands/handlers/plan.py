from __future__ import annotations

from codeferry.commands.registry import Command, CommandContext, CommandType


async def handle_plan(ctx: CommandContext) -> None:
    ctx.ui.set_plan_mode(True)
    ctx.ui.add_system_message("Switched to Plan mode - read-only; writes and command execution are disabled")
    if ctx.args:
        ctx.ui.send_user_message(ctx.args)

 
PLAN_COMMAND = Command(
    name="plan",
    aliases=["p"],
    description="Switch to Plan mode",
    usage="/plan [task description]",
    type=CommandType.LOCAL_UI,
    handler=handle_plan,
)
