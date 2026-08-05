from __future__ import annotations

from typing import TYPE_CHECKING

from codeferry.commands.registry import Command, CommandContext, CommandType

if TYPE_CHECKING:
    from codeferry.worktree.manager import WorktreeManager


def create_worktree_command(manager: WorktreeManager) -> Command:


    async def handle_worktree(ctx: CommandContext) -> None:
        args = ctx.args.strip()
        if not args:
            ctx.ui.add_system_message(
                "Usage:\n"
                "  /worktree create <name> [base-branch]\n"
                "  /worktree list\n"
                "  /worktree enter <name>\n"
                "  /worktree exit [--remove] [--discard]\n"
                "  /worktree status"
            )
            return

        parts = args.split()
        sub = parts[0]
        rest = parts[1:]

        if sub == "create":
            await _handle_create(ctx, manager, rest)
        elif sub == "list":
            _handle_list(ctx, manager)
        elif sub == "enter":
            await _handle_enter(ctx, manager, rest)
        elif sub == "exit":
            await _handle_exit(ctx, manager, rest)
        elif sub == "status":
            _handle_status(ctx, manager)
        else:
            ctx.ui.add_system_message(f"Unknown subcommand: {sub}")

    return Command(
        name="worktree",
        aliases=["wt"],
        description="Manage Git worktrees",
        usage="/worktree <create|list|enter|exit|status>",
        type=CommandType.LOCAL,
        handler=handle_worktree,
    )


async def _handle_create(
    ctx: CommandContext,
    manager: WorktreeManager,
    args: list[str],
) -> None:
    if not args:
        ctx.ui.add_system_message("Usage: /worktree create <name> [base-branch]")
        return

    name = args[0]
    base_branch = args[1] if len(args) > 1 else "HEAD"

    try:
        wt = await manager.create(name, base_branch)
    except Exception as e:
        ctx.ui.add_system_message(f"Failed to create worktree: {e}")
        return

    try:
        session = await manager.enter(name)
        if ctx.agent:
            ctx.agent.work_dir = wt.path
    except Exception as e:
        ctx.ui.add_system_message(
            f"Worktree was created but could not be entered: {e}\nPath: {wt.path}"
        )
        return

    ctx.ui.add_system_message(
        f"Created and entered worktree: {name}\n"
        f"Path: {wt.path}\n"
        f"Branch: {wt.branch}\n"
        f"Based on: {base_branch}"
    )


def _handle_list(ctx: CommandContext, manager: WorktreeManager) -> None:
    worktrees = manager.list_worktrees()
    if not worktrees:
        ctx.ui.add_system_message("No active worktrees")
        return

    current = manager.current_session
    lines = ["Active Worktrees:", "─────────────────"]
    for wt in worktrees:
        marker = " <- current" if current and current.worktree_name == wt.name else ""
        lines.append(
            f"  {wt.name}{marker}\n"
            f"    Path: {wt.path}\n"
            f"    Branch: {wt.branch}\n"
            f"    Created: {wt.created.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    ctx.ui.add_system_message("\n".join(lines))


async def _handle_enter(
    ctx: CommandContext,
    manager: WorktreeManager,
    args: list[str],
) -> None:
    if not args:
        ctx.ui.add_system_message("Usage: /worktree enter <name>")
        return

    name = args[0]
    try:
        session = await manager.enter(name)
        if ctx.agent:
            ctx.agent.work_dir = session.worktree_path
        ctx.ui.add_system_message(f"Entered worktree: {name}\nPath: {session.worktree_path}")
    except Exception as e:
        ctx.ui.add_system_message(f"Failed to enter worktree: {e}")


async def _handle_exit(
    ctx: CommandContext,
    manager: WorktreeManager,
    args: list[str],
) -> None:
    session = manager.get_current_session()
    if session is None:
        ctx.ui.add_system_message("You are not currently in a worktree")
        return

    remove = "--remove" in args
    discard = "--discard" in args
    action = "remove" if remove else "keep"

    try:
        await manager.exit(session.worktree_name, action=action, discard_changes=discard)
        if ctx.agent:
            ctx.agent.work_dir = session.original_cwd
        msg = f"Exited worktree: {session.worktree_name}"
        if remove:
            msg += " (removed)"
        ctx.ui.add_system_message(msg)
    except Exception as e:
        ctx.ui.add_system_message(f"Failed to exit worktree: {e}")


def _handle_status(ctx: CommandContext, manager: WorktreeManager) -> None:
    session = manager.get_current_session()
    if session is None:
        ctx.ui.add_system_message("You are not currently in a worktree")
        return

    lines = [
        "Worktree Session Status:",
        "──────────────────",
        f"  Name: {session.worktree_name}",
        f"  Path: {session.worktree_path}",
        f"  Original directory: {session.original_cwd}",
        f"  Original branch: {session.original_branch}",
    ]
    ctx.ui.add_system_message("\n".join(lines))
