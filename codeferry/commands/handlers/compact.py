from __future__ import annotations

from codeferry.commands.registry import Command, CommandContext, CommandType


async def handle_compact(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent is not initialized")
        return


    input_tokens, _ = ctx.ui.get_token_count()
    if input_tokens < 5000:
        ctx.ui.add_system_message(f"Current token count is {input_tokens:,}; compaction is not needed")
        return

    from codeferry.agent import CompactNotification, ErrorEvent


    result = await ctx.agent.manual_compact(ctx.conversation)
    if isinstance(result, CompactNotification):
        # Persist compact_boundary so later resumes can rebuild the compacted state.
        # manual_compact has already rewritten ctx.conversation; the next _send_message
        # will capture history_cursor again, so no manual reset is needed here.
        if ctx.session is not None and result.boundary is not None:
            from codeferry.memory.session import make_compact_boundary

            ctx.session.append_record(
                make_compact_boundary(result.boundary.summary, result.boundary.keep)
            )
        ctx.ui.add_system_message(result.message)
    elif isinstance(result, ErrorEvent):
        ctx.ui.add_system_message(f"Compaction failed: {result.message}")


COMPACT_COMMAND = Command(
    name="compact",
    aliases=["c"],
    description="Compact context",
    usage="/compact [focus to preserve]",
    type=CommandType.LOCAL,
    handler=handle_compact,
)
