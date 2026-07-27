

from __future__ import annotations

import copy

from codeferry.conversation import ConversationManager, Message, ToolResultBlock

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

FORK_BOILERPLATE = f"""{FORK_BOILERPLATE_TAG}
You are a forked worker process. You are not the main Agent.
Rules (non-negotiable):
1. Do not fork again.
2. Do not chat, ask questions, or request confirmation.
3. Use tools directly: read files, search code, and make changes.
4. Stay strictly within the scope of the task assigned to you.
5. Keep the final report under 500 words, using this format:

Scope: [the task assigned to you]
Result: [completed/partially completed/failed + brief explanation]
Key files: [list of key file paths]
Files changed: [list of changed file paths]
Issues: [problems encountered, or None]
</fork_boilerplate>"""


class ForkError(Exception):
    pass


def build_forked_messages(
    conversation: ConversationManager,
    task: str,
) -> ConversationManager:
    for msg in conversation.history:
        if FORK_BOILERPLATE_TAG in msg.content:
            raise ForkError(
                "Cannot fork from a forked agent. "
                "Fork nesting is not allowed."
            )

    fork_conv = ConversationManager()
    fork_conv.history = copy.deepcopy(conversation.history)
    fork_conv.env_injected = conversation.env_injected
    fork_conv.ltm_injected = conversation.ltm_injected


    if fork_conv.history:
        last = fork_conv.history[-1]
        if last.role == "assistant" and last.tool_uses:
            existing_result_ids = set()
            if len(fork_conv.history) >= 2:
                candidate = fork_conv.history[-1]
                if candidate.tool_results:
                    existing_result_ids = {
                        tr.tool_use_id for tr in candidate.tool_results
                    }

            pending = [
                tu
                for tu in last.tool_uses
                if tu.tool_use_id not in existing_result_ids
            ]
            if pending:
                placeholders = [
                    ToolResultBlock(
                        tool_use_id=tu.tool_use_id,
                        content="interrupted",
                        is_error=False,
                    )
                    for tu in pending
                ]
                fork_conv.history.append(
                    Message(
                        role="user",
                        content="",
                        tool_results=placeholders,
                    )
                )

    fork_conv.add_user_message(f"{FORK_BOILERPLATE}\n\nYour task:\n{task}")
    return fork_conv
