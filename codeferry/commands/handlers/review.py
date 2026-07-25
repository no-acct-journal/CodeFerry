from __future__ import annotations

from codeferry.commands.registry import Command, CommandContext, CommandType


REVIEW_PROMPT = (
    "Please review the code changes in the current git diff. Focus on:\n"
    "1. Logic errors\n"
    "2. Security issues\n"
    "3. Performance issues\n"
    "4. Code style"
)


async def handle_review(ctx: CommandContext) -> None:
    prompt = REVIEW_PROMPT
    if ctx.args:
        prompt += f"\n\nAdditional focus: {ctx.args}"
    ctx.ui.send_user_message(prompt)


REVIEW_COMMAND = Command(
    name="review",
    description="Review code changes",
    usage="/review [additional focus]",
    type=CommandType.PROMPT,
    handler=handle_review,
)
