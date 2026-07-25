from __future__ import annotations

from codeferry.commands.registry import Command, CommandContext, CommandType
from codeferry.permissions import PermissionMode


_MODE_NAMES = {m.value: m for m in PermissionMode}


async def handle_permission(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent is not initialized")
        return

    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "":
        mode = ctx.agent.permission_mode
        checker = ctx.agent.permission_checker
        rule_count = 0
        if checker and checker.rule_engine:
            tiers = checker.rule_engine._load_tiers()
            rule_count = sum(len(t) for t in tiers)
        ctx.ui.add_system_message(
            f"Permission status\n"
            f"  Current mode: {mode.value}\n"
            f"  Rule count: {rule_count}"
        )

    elif sub == "mode":
        mode_str = parts[1].strip() if len(parts) > 1 else ""
        if not mode_str:
            modes = ", ".join(_MODE_NAMES.keys())
            ctx.ui.add_system_message(f"Usage: /permission mode <mode>\nAvailable: {modes}")
            return
        mode = _MODE_NAMES.get(mode_str)
        if mode is None:
            modes = ", ".join(_MODE_NAMES.keys())
            ctx.ui.add_system_message(f"Unknown mode: {mode_str}\nAvailable: {modes}")
            return
        ctx.agent.set_permission_mode(mode)
        ctx.ui.refresh_status()
        ctx.ui.add_system_message(f"Permission mode switched to: {mode.value}")

    elif sub == "rules":
        checker = ctx.agent.permission_checker
        if not checker or not checker.rule_engine:
            ctx.ui.add_system_message("Rule engine is not initialized")
            return
        tiers = checker.rule_engine._load_tiers()
        names = ["User level", "Project level", "Local level"]
        lines: list[str] = ["Permission rules:"]
        for name, rules in zip(names, tiers):
            if rules:
                lines.append(f"  [{name}]")
                for r in rules:
                    lines.append(f"    {r.tool_name}({r.pattern}) → {r.effect}")
            else:
                lines.append(f"  [{name}] (no rules)")
        ctx.ui.add_system_message("\n".join(lines))

    elif sub == "add":
        rule_str = parts[1].strip() if len(parts) > 1 else ""
        if not rule_str:
            ctx.ui.add_system_message("Usage: /permission add <rule> <effect>")
            return
        from codeferry.permissions.rules import Rule, parse_rule
        rule_parts = rule_str.rsplit(None, 1)
        if len(rule_parts) < 2 or rule_parts[1] not in ("allow", "deny"):
            ctx.ui.add_system_message(
                "Usage: /permission add <Tool(pattern)> <allow|deny>\n"
                "Example: /permission add Bash(git*) allow"
            )
            return
        try:
            rule = parse_rule(rule_parts[0], rule_parts[1])
        except ValueError as e:
            ctx.ui.add_system_message(str(e))
            return
        checker = ctx.agent.permission_checker
        if checker and checker.rule_engine:
            checker.rule_engine.append_local_rule(rule)
            ctx.ui.add_system_message(f"Rule added: {rule.tool_name}({rule.pattern}) → {rule.effect}")
        else:
            ctx.ui.add_system_message("Rule engine is not initialized")


    elif sub == "reset":
        checker = ctx.agent.permission_checker
        if checker and checker.rule_engine and checker.rule_engine._local_path:
            path = checker.rule_engine._local_path
            if path.exists():
                path.write_text("", encoding="utf-8")
            ctx.ui.add_system_message("Local rules have been cleared")
        else:
            ctx.ui.add_system_message("No local rules file")

    else:
        ctx.ui.add_system_message(
            "Usage: /permission [mode <mode> | rules | add <rule> <effect> | reset]"
        )


PERMISSION_COMMAND = Command(
    name="permission",
    description="Permission management",
    usage="/permission [mode <mode> | rules | add <rule> | reset]",
    type=CommandType.LOCAL,
    handler=handle_permission,
)
