
from __future__ import annotations

from codeferry.commands.registry import Command, CommandContext, CommandType


def _format_aliases(cmd: Command) -> str:
    if not cmd.aliases:
        return cmd.name
    return cmd.name + ", " + ", ".join(f"/{a}" for a in cmd.aliases)


async def handle_help(ctx: CommandContext) -> None:
    registry = ctx.config["registry"]

    if ctx.args:
        cmd = registry.find(ctx.args.lower())
        if cmd is None:
            ctx.ui.add_system_message(f"Unknown command: {ctx.args}. Type /help to view available commands.")
            return
        lines = [f"/{cmd.name}"]
        if cmd.aliases:
            lines[0] += f"  (aliases: {', '.join('/' + a for a in cmd.aliases)})"
        lines.append(f"  {cmd.description}")
        if cmd.usage:
            lines.append(f"  Usage: {cmd.usage}")
        if cmd.arg_prompt:
            lines.append(f"  Arguments: {cmd.arg_prompt}")
        ctx.ui.add_system_message("\n".join(lines))
        return

    commands = registry.list_commands()
    lines = ["Available commands:"]
    for cmd in commands:
        aliases_str = f"/{_format_aliases(cmd)}"
        lines.append(f"  {aliases_str:<24} {cmd.description}")
    lines.append("")
    lines.append("Type /help <command> to view detailed usage.")
    ctx.ui.add_system_message("\n".join(lines))


HELP_COMMAND = Command(
    name="help",
    aliases=["h", "?"],
    description="Show help information",
    usage="/help [command]",
    type=CommandType.LOCAL,
    handler=handle_help,
)
