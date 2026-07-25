from __future__ import annotations

from codeferry.commands.registry import Command, CommandContext, CommandType
from codeferry.conversation import ConversationManager


async def handle_session(ctx: CommandContext) -> None:
    sm = ctx.session_manager
    if sm is None:
        ctx.ui.add_system_message("Session manager is not initialized")
        return

    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "":
        if ctx.session:
            m = ctx.session.meta
            ts = m.last_active.strftime("%Y-%m-%d %H:%M")
            ctx.ui.add_system_message(
                f"Current session: {m.id}\n"
                f"  Title: {m.title or '(untitled)'}\n"
                f"  Messages: {m.message_count}\n"
                f"  Tokens: {m.total_tokens:,}\n"
                f"  Last active: {ts}"
            )
        else:
            ctx.ui.add_system_message("There is no active session")
        return

    if sub == "list":
        metas = sm.list()
        if not metas:
            ctx.ui.add_system_message("No saved sessions.")
            return
        lines: list[str] = ["Session list:"]
        for m in metas[:10]:
            ts = m.last_active.strftime("%Y-%m-%d %H:%M")
            title = m.title or "(untitled)"
            lines.append(f"  {m.id}  {title}  [{m.message_count} msgs, {ts}]")
        ctx.ui.add_system_message("\n".join(lines))

    elif sub == "resume":
        session_id = parts[1].strip() if len(parts) > 1 else ""
        if not session_id:
            metas = sm.list()
            if not metas:
                ctx.ui.add_system_message("No saved sessions.")
                return
            lines: list[str] = ["Resumable sessions (use /session resume <id> or /session resume <number>):"]
            for i, m in enumerate(metas[:15], 1):
                ts = m.last_active.strftime("%Y-%m-%d %H:%M")
                title = m.title or "(untitled)"
                lines.append(f"  {i}. [{m.id[:8]}]  {title}  ({m.message_count} msgs, {ts})")
            ctx.ui.add_system_message("\n".join(lines))
            ctx.config["_resume_candidates"] = [m.id for m in metas[:15]]
            return
        candidates = ctx.config.get("_resume_candidates", [])
        if session_id.isdigit() and candidates:
            idx = int(session_id) - 1
            if 0 <= idx < len(candidates):
                session_id = candidates[idx]
        result = sm.resume(session_id)
        if result is None:
            ctx.ui.add_system_message(f"Session not found: {session_id}")
            return
        if ctx.session:
            ctx.session.close()
        ctx.config["set_session"](result.session)
        conv = ConversationManager()
        for msg in result.messages:
            conv.history.append(msg)
        ctx.config["set_conversation"](conv)
        if ctx.agent:
            ctx.agent._loop_count = 0
        await ctx.config["render_restored"](result.messages)
        ctx.ui.add_system_message(
            f"Session resumed: {session_id} ({result.session.meta.message_count} msgs)"
        )


    elif sub == "new":
        if ctx.session:
            ctx.session.close()
        new_session = sm.create()
        ctx.config["set_session"](new_session)
        ctx.config["set_conversation"](ConversationManager())
        if ctx.agent:
            ctx.agent._loop_count = 0
        ctx.config["clear_chat"]()
        ctx.ui.add_system_message(f"New session created: {new_session.session_id}")

    elif sub == "delete":
        session_id = parts[1].strip() if len(parts) > 1 else ""
        if not session_id:
            ctx.ui.add_system_message("Usage: /session delete <id>")
            return
        if ctx.session and ctx.session.session_id == session_id:
            ctx.ui.add_system_message("Cannot delete the currently active session.")
            return
        if sm.delete(session_id):
            ctx.ui.add_system_message(f"Session deleted: {session_id}")
        else:
            ctx.ui.add_system_message(f"Session not found: {session_id}")


    else:
        ctx.ui.add_system_message(
            "Usage: /session [list | resume <id> | new | delete <id>]"
        )


SESSION_COMMAND = Command(
    name="session",
    description="Session management",
    usage="/session [list | resume <id> | new | delete <id>]",
    type=CommandType.LOCAL,
    handler=handle_session,
)
