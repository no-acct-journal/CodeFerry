from __future__ import annotations

from typing import TYPE_CHECKING

from codeferry.commands.registry import Command, CommandContext, CommandType

if TYPE_CHECKING:
    from codeferry.skills.loader import SkillLoader


async def handle_skill(ctx: CommandContext) -> None:
    parts = ctx.args.strip().split(maxsplit=1)
    subcmd = parts[0] if parts else "list"
    sub_args = parts[1] if len(parts) > 1 else ""

    loader: SkillLoader | None = ctx.config.get("skill_loader")
    if loader is None:
        ctx.ui.add_system_message("Skill system is not initialized")
        return

    if subcmd == "list":
        _handle_list(ctx, loader)
    elif subcmd == "info":
        _handle_info(ctx, loader, sub_args)
    elif subcmd == "reload":
        await _handle_reload(ctx, loader)
    else:
        ctx.ui.add_system_message(
            f"Unknown subcommand: {subcmd}\nUsage: /skill list | /skill info <name> | /skill reload"
        )


def _handle_list(ctx: CommandContext, loader: SkillLoader) -> None:
    catalog = loader.get_catalog()
    if not catalog:
        ctx.ui.add_system_message("No skills loaded")
        return

    lines = ["Loaded Skills:"]
    for name, desc in catalog:
        source = loader.get_source_label(name)
        lines.append(f"  {name:<20} {desc}  [{source}]")
    ctx.ui.add_system_message("\n".join(lines))


def _handle_info(ctx: CommandContext, loader: SkillLoader, name: str) -> None:
    if not name:
        ctx.ui.add_system_message("Usage: /skill info <name>")
        return

    skill = loader.get(name)
    if skill is None:
        ctx.ui.add_system_message(f"Skill not found: {name}")
        return

    source = loader.get_source_label(name)
    lines = [
        f"Skill: {skill.name}",
        f"Description: {skill.description}",
        f"Mode: {skill.mode}",
        f"Context: {skill.context}",
        f"Model: {skill.model or '(default)'}",
        f"AllowedTools: {', '.join(skill.allowed_tools) or '(all)'}",
        f"Source: {source}",
        f"Path: {skill.source_path or '(builtin)'}",
        f"Directory: {skill.is_directory}",
    ]
    ctx.ui.add_system_message("\n".join(lines))


async def _handle_reload(ctx: CommandContext, loader: SkillLoader) -> None:
    skills = loader.reload()

    registry = ctx.config.get("registry")
    if registry is not None:
        from codeferry.commands.handlers.skill_register import register_skill_commands
        register_skill_commands(registry, loader, ctx.config.get("skill_executor"))

    ctx.ui.add_system_message(f"Reloaded {len(skills)} skills")


SKILL_COMMAND = Command(
    name="skill",
    description="Manage skill packages",
    usage="/skill list | /skill info <name> | /skill reload",
    type=CommandType.LOCAL,
    handler=handle_skill,
    aliases=["skills"],
)
