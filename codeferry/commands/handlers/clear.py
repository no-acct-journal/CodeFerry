from __future__ import annotations

from codeferry.commands.registry import Command, CommandContext, CommandType
from codeferry.conversation import ConversationManager


async def handle_clear(ctx: CommandContext) -> None:
    if ctx.session:
        ctx.session.close()

    if ctx.session_manager:
        new_session = ctx.session_manager.create()
        ctx.config["set_session"](new_session)


    ctx.config["set_conversation"](ConversationManager())

    if ctx.agent:
        ctx.agent._loop_count = 0
        ctx.agent.clear_active_skills()

    ctx.config["clear_chat"]()
    ctx.ui.refresh_status()
    ctx.ui.add_system_message("Conversation cleared. A new session has been created.")


CLEAR_COMMAND = Command(
    name="clear",
    description="Clear conversation history",
    usage="/clear",
    type=CommandType.LOCAL_UI,
    handler=handle_clear,
)
