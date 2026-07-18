from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from codeferry.conversation import (
    ConversationManager,
    Message,
    ToolResultBlock,
    estimate_tokens,
)
from codeferry.serialization import build_messages

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SINGLE_RESULT_CHAR_LIMIT = 50_000
AGGREGATE_CHAR_LIMIT = 200_000
PREVIEW_CHARS = 2_000

KEEP_RECENT_TURNS = 10
OLD_RESULT_SNIP_CHARS = 2_000
SNIPPED_TAG = "<snipped>"

SUMMARY_OUTPUT_RESERVE = 20_000
AUTO_COMPACT_SAFETY_MARGIN = 13_000
MANUAL_COMPACT_SAFETY_MARGIN = 3_000

# Layer 2 "keep recent raw messages" window (matching Claude Code compact.ts
# buildPostCompactMessages messagesToKeep). During compaction, tail messages are
# kept verbatim and excluded from the summary until either the accumulated token
# count reaches KEEP_RECENT_TOKENS or at least MIN_KEEP_MESSAGES have been kept.
# Stop when the accumulated total would exceed KEEP_MAX_TOKENS, so one huge
# message cannot consume the whole keep window.
KEEP_RECENT_TOKENS = 10_000
MIN_KEEP_MESSAGES = 5
KEEP_MAX_TOKENS = 40_000

# If the prefix has fewer tokens than this threshold, summarizing it is not
# worthwhile: the summary round trip costs more than the space it recovers.
# Fall back to no compaction and keep the original history.
MIN_SUMMARIZE_PREFIX_TOKENS = 2_000

PERSISTED_TAG = "<persisted-output>"

SESSION_SUBDIR = ".codeferry/session/tool-results"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass
class CompactBoundary:
    """Structured Layer 2 compaction result for the session layer.

    `summary` is the model-generated summary of the compacted prefix; `keep`
    contains the recent tail messages that auto_compact preserved verbatim.
    The session layer, which owns the session id and file handles, writes both
    into a compact_boundary record so resume can reconstruct the compacted
    state. This keeps writes decoupled and lets auto_compact remain independent
    from session ownership.
    """

    summary: str
    keep: list[Message]


@dataclass
class CompactEvent:
    before_tokens: int
    # Filled when summarization succeeds so the caller can persist a
    # compact_boundary record. None when no summary was produced.
    boundary: CompactBoundary | None = None


# ---------------------------------------------------------------------------
# Content replacement state - Design B 
# ---------------------------------------------------------------------------

@dataclass
class ContentReplacementState:
    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass
class ContentReplacementRecord:
    tool_use_id: str
    replacement: str
    kind: str = "tool-result"


def create_replacement_state() -> ContentReplacementState:
    return ContentReplacementState()


def clone_replacement_state(src: ContentReplacementState) -> ContentReplacementState:
    return ContentReplacementState(
        seen_ids=set(src.seen_ids),
        replacements=dict(src.replacements),
    )


REPLACEMENT_RECORDS_FILENAME = "replacement_records.jsonl"


def append_replacement_records(
    session_dir: Path, records: list[ContentReplacementRecord]
) -> None:
    if not records:
        return
    path = session_dir / REPLACEMENT_RECORDS_FILENAME
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({
                "kind": r.kind,
                "tool_use_id": r.tool_use_id,
                "replacement": r.replacement,
            }, ensure_ascii=False) + "\n")


def load_replacement_records(session_dir: Path) -> list[ContentReplacementRecord]:
    path = session_dir / REPLACEMENT_RECORDS_FILENAME
    if not path.exists():
        return []
    out: list[ContentReplacementRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out.append(ContentReplacementRecord(
                kind=obj.get("kind", "tool-result"),
                tool_use_id=obj["tool_use_id"],
                replacement=obj["replacement"],
            ))
    return out


def reconstruct_replacement_state(
    messages: list[Message],
    records: list[ContentReplacementRecord],
    inherited_replacements: Mapping[str, str] | None = None,
) -> ContentReplacementState:
    state = create_replacement_state()
    candidate_ids: set[str] = set()
    for msg in messages:
        for tr in msg.tool_results:
            candidate_ids.add(tr.tool_use_id)
    state.seen_ids.update(candidate_ids)
    for r in records:
        if r.kind == "tool-result" and r.tool_use_id in candidate_ids:
            state.replacements[r.tool_use_id] = r.replacement
    if inherited_replacements:
        for tool_use_id, replacement in inherited_replacements.items():
            if tool_use_id in candidate_ids and tool_use_id not in state.replacements:
                state.replacements[tool_use_id] = replacement
    return state


# ---------------------------------------------------------------------------
# Session directory management
# ---------------------------------------------------------------------------

def ensure_session_dir(work_dir: str) -> Path:
    session_dir = Path(work_dir) / SESSION_SUBDIR
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def cleanup_tool_results(session_dir: Path) -> None:
    if session_dir.exists():
        shutil.rmtree(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Layer 1: Persist large tool results
# ---------------------------------------------------------------------------

def persist_tool_result(tool_use_id: str, content: str, session_dir: Path) -> Path:
    file_path = session_dir / f"{tool_use_id}.txt"
    try:
        fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except FileExistsError:
        pass
    return file_path


def make_persisted_preview(content: str, file_path: Path) -> str:
    size_kb = len(content.encode("utf-8")) // 1024
    preview = content[:PREVIEW_CHARS]
    return (
        f"{PERSISTED_TAG}\n"
        f"Output is too large ({size_kb}KB). Full content has been saved to:\n"
        f"{file_path}\n"
        f"\n"
        f"Preview (first 2KB):\n"
        f"{preview}\n"
        f"</persisted-output>"
    )


def _count_turns(messages: list[Message]) -> int:
    count = 0
    for m in messages:
        if m.role == "assistant" and not m.tool_uses:
            count += 1
    return count


def _copy_message_with_results(
    msg: Message, new_tool_results: list[ToolResultBlock]
) -> Message:
    return Message(
        role=msg.role,
        content=msg.content,
        tool_uses=list(msg.tool_uses),
        tool_results=new_tool_results,
        thinking_blocks=list(msg.thinking_blocks),
    )


def _snip_stale_messages(
    history: list[Message],
) -> list[Message]:
    total_turns = _count_turns(history)
    if total_turns <= KEEP_RECENT_TURNS:
        return history

    out: list[Message] = []
    turns_seen = 0
    old_boundary = total_turns - KEEP_RECENT_TURNS

    for msg in history:
        if msg.role == "assistant" and not msg.tool_uses:
            turns_seen += 1
        if turns_seen > old_boundary or not msg.tool_results:
            out.append(msg)
            continue

        new_results: list[ToolResultBlock] = []
        changed = False
        for tr in msg.tool_results:
            if (
                tr.content.startswith(SNIPPED_TAG)
                or tr.content.startswith(PERSISTED_TAG)
                or len(tr.content) <= OLD_RESULT_SNIP_CHARS
            ):
                new_results.append(tr)
                continue
            preview = tr.content[:200]
            orig_len = len(tr.content)
            new_content = (
                f"{SNIPPED_TAG}\n"
                f"(Old result was snipped; original length was {orig_len} characters)\n"
                f"{preview}\n"
                f"… (snipped)"
            )
            new_results.append(ToolResultBlock(
                tool_use_id=tr.tool_use_id,
                content=new_content,
                is_error=tr.is_error,
            ))
            changed = True

        out.append(_copy_message_with_results(msg, new_results) if changed else msg)

    return out


def apply_tool_result_budget(
    conversation: ConversationManager,
    session_dir: Path,
    state: ContentReplacementState,
) -> tuple[ConversationManager, list[ContentReplacementRecord]]:
    """
    Design B: do not mutate the original conversation.

    Return a new ConversationManager where tool_result.content has decisions
    from state.replacements applied, then run Pass 1 (single-result limit) and
    Pass 2 (aggregate limit) on fresh candidates from this turn. Pass 3 (stale
    snipping) runs on the new history and remains stateless; boundary drift is a
    known trade-off.

    state is mutated: ids with new decisions enter seen_ids, and ids with newly
    chosen replacements enter replacements.
    """
    new_records: list[ContentReplacementRecord] = []
    new_history: list[Message] = []

    for msg in conversation.history:
        if not msg.tool_results:
            new_history.append(msg)
            continue

        decisions: dict[str, str] = {}
        fresh: list[ToolResultBlock] = []

        for tr in msg.tool_results:
            if tr.tool_use_id in state.replacements:
                decisions[tr.tool_use_id] = state.replacements[tr.tool_use_id]
            elif tr.tool_use_id in state.seen_ids:
                decisions[tr.tool_use_id] = tr.content
            elif tr.content.startswith(PERSISTED_TAG):
                # Already tagged as persisted-output externally, such as by a
                # tool itself; treat it as a known decision.
                state.seen_ids.add(tr.tool_use_id)
                state.replacements[tr.tool_use_id] = tr.content
                decisions[tr.tool_use_id] = tr.content
                new_records.append(ContentReplacementRecord(
                    tool_use_id=tr.tool_use_id, replacement=tr.content,
                ))
            else:
                fresh.append(tr)

        # Pass 1: single-result limit
        persisted_p1: set[str] = set()
        for tr in fresh:
            if len(tr.content) > SINGLE_RESULT_CHAR_LIMIT:
                fp = persist_tool_result(tr.tool_use_id, tr.content, session_dir)
                preview = make_persisted_preview(tr.content, fp)
                decisions[tr.tool_use_id] = preview
                state.replacements[tr.tool_use_id] = preview
                state.seen_ids.add(tr.tool_use_id)
                new_records.append(ContentReplacementRecord(
                    tool_use_id=tr.tool_use_id, replacement=preview,
                ))
                persisted_p1.add(tr.tool_use_id)

        # Pass 2: aggregate limit
        remaining = [tr for tr in fresh if tr.tool_use_id not in persisted_p1]
        total = sum(len(c) for c in decisions.values()) + sum(
            len(tr.content) for tr in remaining
        )
        if total > AGGREGATE_CHAR_LIMIT:
            ranked = sorted(remaining, key=lambda tr: len(tr.content), reverse=True)
            for tr in ranked:
                if total <= AGGREGATE_CHAR_LIMIT:
                    break
                fp = persist_tool_result(tr.tool_use_id, tr.content, session_dir)
                preview = make_persisted_preview(tr.content, fp)
                old_len = len(tr.content)
                decisions[tr.tool_use_id] = preview
                state.replacements[tr.tool_use_id] = preview
                state.seen_ids.add(tr.tool_use_id)
                new_records.append(ContentReplacementRecord(
                    tool_use_id=tr.tool_use_id, replacement=preview,
                ))
                total -= old_len - len(preview)

        # Mark remaining unreplaced fresh results as seen but not replaced.
        for tr in fresh:
            if tr.tool_use_id not in state.replacements:
                state.seen_ids.add(tr.tool_use_id)
                decisions[tr.tool_use_id] = tr.content

        # Generate new tool_results while preserving the original order.
        new_tool_results = [
            ToolResultBlock(
                tool_use_id=tr.tool_use_id,
                content=decisions[tr.tool_use_id],
                is_error=tr.is_error,
            )
            for tr in msg.tool_results
        ]
        new_history.append(_copy_message_with_results(msg, new_tool_results))

    # Pass 3: snip stale results on the new history. This is stateless; boundary
    # drift is a known trade-off.
    new_history = _snip_stale_messages(new_history)

    new_conv = ConversationManager()
    new_conv.history = new_history
    new_conv.env_injected = conversation.env_injected
    new_conv.ltm_injected = conversation.ltm_injected
    new_conv.last_input_tokens = conversation.last_input_tokens
    new_conv.baseline_tokens = conversation.baseline_tokens
    new_conv.anchor_count = conversation.anchor_count

    return new_conv, new_records


# ---------------------------------------------------------------------------
# Layer 2: Whole-conversation summary (Auto-Compact)
# ---------------------------------------------------------------------------

def compute_compact_threshold(context_window: int, manual: bool = False) -> int:
    effective = context_window - SUMMARY_OUTPUT_RESERVE
    margin = MANUAL_COMPACT_SAFETY_MARGIN if manual else AUTO_COMPACT_SAFETY_MARGIN
    return effective - margin


def should_auto_compact(last_input_tokens: int, context_window: int) -> bool:
    return last_input_tokens >= compute_compact_threshold(context_window)


SUMMARY_PROMPT = """\
You are a conversation summarization assistant. You can only output plain text
and must not call any tools.

Generate a structured summary for the conversation below.

First use an <analysis> tag to reason through what happened in the
conversation; this part will be discarded. Then output the final summary inside
a <summary> tag.

<summary> must include the following 9 sections:

1. **Primary Request and Intent**: what the user wants to do
2. **Key Technical Concepts**: important technical points discussed
3. **Files and Code Sections**: files involved and key code snippets to retain
4. **Errors and Fixes**: errors encountered and how they were addressed
5. **Problem-Solving Process**: reasoning and methods used to solve the issue
6. **All User Messages**: all non-tool-result user messages, kept verbatim and not rewritten
7. **Pending Tasks**: work that remains unfinished
8. **Current Work**: the most recent work in progress, with the most detail
9. **Possible Next Steps**: what should happen next

Reminder: do not call any tools. Tool calls will be rejected and you will fail.
Only output plain text."""


def extract_summary(llm_output: str) -> str:
    start = llm_output.find("<summary>")
    end = llm_output.find("</summary>")
    if start == -1 or end == -1:
        return llm_output
    return llm_output[start + len("<summary>"):end].strip()


def build_compact_messages(
    summary: str,
    attachment: str = "",
    has_keep_tail: bool = False,
    transcript_path: str = "",
) -> list[Message]:
    content = "This session continues from an earlier conversation that was compacted because context space was running low. Here is the summary of the earlier conversation:\n\n" + summary
    if has_keep_tail:
        content += "\n\nRecent messages were preserved verbatim."
    if transcript_path:
        content += f"\n\nIf you need exact pre-compaction details such as code snippets or error messages, use ReadFile to read the full conversation transcript: {transcript_path}"
    if attachment:
        content += "\n\n---\n\n" + attachment
    return [
        Message(role="user", content=content),
    ]


# ---------------------------------------------------------------------------
# Post-compaction recovery state
# ---------------------------------------------------------------------------

# Limits for recovery attachments appended to the summary user message. Compact
# clears the working conversation; without these snapshots, the model would
# forget recently read files and the SOP for the currently running skill.
RECOVERY_FILE_LIMIT = 5
RECOVERY_TOKENS_PER_FILE = 5_000
RECOVERY_SKILLS_BUDGET = 25_000
RECOVERY_TOKENS_PER_SKILL = 5_000
_RECOVERY_CHARS_PER_TOKEN = 3.5


@dataclass
class FileReadRecord:
    path: str
    content: str
    timestamp: float


@dataclass
class SkillInvocationRecord:
    name: str
    body: str
    timestamp: float


class RecoveryState:
    """Per-agent snapshots that survive Layer 2 compaction.

    Records byte content returned by ReadFile and the SOP bodies attached when
    each skill was invoked. These records are reattached to the summary user
    message so the model retains usable working context even after the
    conversation history is compacted.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[str, FileReadRecord] = {}
        self._skills: dict[str, SkillInvocationRecord] = {}

    def record_file_read(self, path: str, content: str) -> None:
        if not path:
            return
        with self._lock:
            self._files[path] = FileReadRecord(
                path=path, content=content, timestamp=time.time()
            )

    def record_skill_invocation(self, name: str, body: str) -> None:
        if not name:
            return
        with self._lock:
            self._skills[name] = SkillInvocationRecord(
                name=name, body=body, timestamp=time.time()
            )

    def snapshot_files(self, limit: int) -> list[FileReadRecord]:
        with self._lock:
            records = list(self._files.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        if limit > 0:
            records = records[:limit]
        return records

    def snapshot_skills(self) -> list[SkillInvocationRecord]:
        with self._lock:
            records = list(self._skills.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records


def _approx_tokens(s: str) -> int:
    if not s:
        return 0
    return int(len(s) / _RECOVERY_CHARS_PER_TOKEN)


def _truncate_by_tokens(s: str, token_budget: int) -> str:
    if token_budget <= 0 or not s:
        return s
    if _approx_tokens(s) <= token_budget:
        return s
    max_chars = int(token_budget * _RECOVERY_CHARS_PER_TOKEN)
    if max_chars <= 0 or max_chars >= len(s):
        return s
    return s[:max_chars] + "\n... (content truncated)"


def _first_line(s: str) -> str:
    for line in s.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def build_recovery_attachment(
    state: RecoveryState | None,
    tool_schemas: list[Mapping[str, Any]] | None,
) -> str:
    """Render the four post-compaction attachment sections.

    Return "" when there is nothing useful to attach, so the caller can keep the
    summary message clean. `tool_schemas` should be the schemas the agent will
    send on the next request; their names and descriptions remind the model
    which tools are currently connected.
    """
    sections: list[str] = []

    if state is not None:
        files = state.snapshot_files(RECOVERY_FILE_LIMIT)
        if files:
            buf = ["## Recently Read Files\n",
                   "These snapshots are the last contents returned by the file-read tool. Re-read the files if you need current bytes.\n"]
            for rec in files:
                content = _truncate_by_tokens(rec.content, RECOVERY_TOKENS_PER_FILE)
                ts = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(rec.timestamp)
                )
                buf.append(f"### {rec.path}  (read {ts})\n")
                buf.append("```\n")
                buf.append(content)
                if not content.endswith("\n"):
                    buf.append("\n")
                buf.append("```\n")
            sections.append("".join(buf))

        skills = state.snapshot_skills()
        if skills:
            buf = ["## Activated Skills\n",
                   "The following skills were invoked in this session, and their trigger conditions still apply.\n"]
            used = 0
            emitted = False
            for sk in skills:
                body = _truncate_by_tokens(sk.body, RECOVERY_TOKENS_PER_SKILL)
                tokens = _approx_tokens(body) + _approx_tokens(sk.name) + 8
                if used + tokens > RECOVERY_SKILLS_BUDGET:
                    break
                used += tokens
                buf.append(f"### {sk.name}\n\n{body}\n")
                emitted = True
            if emitted:
                sections.append("".join(buf))

    if tool_schemas:
        buf = ["## Available Tools\n",
               "You can still call the following tools directly when needed:\n"]
        for t in tool_schemas:
            name = t.get("name") if isinstance(t, Mapping) else None
            if not name:
                continue
            desc = t.get("description", "") if isinstance(t, Mapping) else ""
            desc = _first_line(desc or "")
            if desc:
                buf.append(f"- {name} — {desc}\n")
            else:
                buf.append(f"- {name}\n")
        sections.append("".join(buf))

    if not sections:
        return ""

    sections.append(
        "## Note\n\nThe recovered context above was reconstructed. If you need exact code, "
        "error messages, or original user wording, use the file-read tool to read it again "
        "instead of guessing from the summary.\n"
    )
    return "\n".join(sections)


def _group_messages_by_turn(messages: list[Message]) -> list[list[Message]]:
    groups: list[list[Message]] = []
    current: list[Message] = []
    for msg in messages:
        current.append(msg)
        if msg.role == "assistant" and not msg.tool_uses:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _message_tokens(msg: Message) -> int:
    """Estimate token count for one message using the shared character heuristic."""
    return estimate_tokens([msg])


def _compute_keep_start_index(messages: list[Message]) -> int:
    """Decide how many tail messages to preserve verbatim during compaction.

    Walk `messages` from tail to head and accumulate estimated tokens one
    message at a time. The current message is included in the keep window while
    either fallback condition remains unsatisfied: accumulated tokens have not
    reached KEEP_RECENT_TOKENS, or fewer than MIN_KEEP_MESSAGES have been kept.
    Stop once including the next message would exceed KEEP_MAX_TOKENS, so one
    huge tail message cannot pull the entire history into the keep window.

    Return the index of the first kept message (keepStartIndex). After the
    initial pass, this index may be moved backward to ensure a kept tool_result
    is not separated from its corresponding tool_use; see
    `_align_keep_start_to_tool_pair`.
    """
    n = len(messages)
    if n == 0:
        return 0

    kept_tokens = 0
    kept_count = 0
    keep_start = n  # No messages kept yet.

    for i in range(n - 1, -1, -1):
        tok = _message_tokens(messages[i])

        # Once at least one message has been kept, stop if including this
        # message would exceed the hard limit. Never refuse to keep the final
        # message, even if it exceeds the limit by itself.
        if kept_count > 0 and kept_tokens + tok > KEEP_MAX_TOKENS:
            break

        kept_tokens += tok
        kept_count += 1
        keep_start = i

        # A fallback condition has been satisfied: either the token floor or the
        # message-count floor was reached. The recent raw window is enough.
        if kept_tokens >= KEEP_RECENT_TOKENS or kept_count >= MIN_KEEP_MESSAGES:
            break

    return _align_keep_start_to_tool_pair(messages, keep_start)


def _align_keep_start_to_tool_pair(messages: list[Message], keep_start: int) -> int:
    """Move keep_start backward so we never preserve an orphaned tool_result.

    A user message carrying tool_results pairs with the preceding assistant
    message that issued the corresponding tool_uses. If keep_start lands on
    such a user message, move it back to at least the paired assistant message
    so the tool_use/tool_result relationship stays intact. Prefer keeping one
    extra pair over keeping only half a pair, which would leave the model with a
    dangling tool_result it cannot attribute to any call.
    """
    while 0 < keep_start < len(messages):
        msg = messages[keep_start]
        if msg.role == "user" and msg.tool_results:
            prev = messages[keep_start - 1]
            if prev.role == "assistant" and prev.tool_uses:
                keep_start -= 1
                continue
        break
    return keep_start


def _prefix_too_small_to_compact(prefix: list[Message]) -> bool:
    """Return True when summarizing `prefix` would recover too little space."""
    if not prefix:
        return True
    return estimate_tokens(prefix) < MIN_SUMMARIZE_PREFIX_TOKENS


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class CompactCircuitBreaker:
    max_failures: int = 3
    consecutive_failures: int = field(default=0, init=False)

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0


    def is_open(self) -> bool:
        return self.consecutive_failures >= self.max_failures


# ---------------------------------------------------------------------------
# Auto-compact orchestrator
# ---------------------------------------------------------------------------

async def auto_compact(
    conversation: ConversationManager,
    client: Any,
    context_window: int,
    session_dir: Path,
    protocol: str = "anthropic",
    manual: bool = False,
    breaker: CompactCircuitBreaker | None = None,
    recovery: RecoveryState | None = None,
    tool_schemas: list[Mapping[str, Any]] | None = None,
    transcript_path: str = "",
) -> CompactEvent | str | None:
    threshold = compute_compact_threshold(context_window, manual=manual)

    # Use real API usage as the threshold anchor: current_tokens() returns the
    # last billing baseline (input + cache_read + cache_creation + output) plus
    # a character-based estimate for messages added after the anchor. On cold
    # start or right after compaction clears the anchor, it falls back to a
    # character estimate over the whole history.
    current = conversation.current_tokens()

    if not manual and current < threshold:
        return None

    if not manual and breaker is not None and breaker.is_open():
        return "Auto-compaction circuit breaker is open after 3 consecutive failures. Handle manually or use /compact."

    before_tokens = current

    # Decide how many tail messages to preserve verbatim. Only the prefix
    # messages[:keep_start] is summarized; messages[keep_start:] is kept as raw
    # recent context instead of a lossy summary.
    keep_start = _compute_keep_start_index(conversation.history)
    to_summarize = conversation.history[:keep_start]
    keep_tail = conversation.history[keep_start:]

    # If the prefix to summarize is too small, fall back to no compaction: either
    # all messages are inside the keep window (keep_start <= 0), or the recovered
    # tokens would not pay for the summary itself.
    if keep_start <= 0 or _prefix_too_small_to_compact(to_summarize):
        return None

    messages_for_summary = build_messages(list(to_summarize), protocol)

    summary_messages: list[dict[str, Any]] = [
        {"role": "user", "content": SUMMARY_PROMPT},
    ]
    summary_messages.extend(messages_for_summary)
    summary_messages.append(
        {"role": "user", "content": "Generate a structured summary from the conversation above. Remember: do not call any tools."}
    )

    summary_conv = ConversationManager()
    summary_conv.history = [
        Message(role="user", content=SUMMARY_PROMPT),
    ]
    # Summarize only the prefix; the kept tail is appended verbatim below during
    # reconstruction.
    for msg in to_summarize:
        summary_conv.history.append(msg)
    summary_conv.history.append(
        Message(role="user", content="Generate a structured summary from the conversation above. Remember: do not call any tools.")
    )

    max_retries = 3
    llm_output: str | None = None

    for attempt in range(max_retries):
        try:
            from codeferry.tools.base import StreamEnd, StreamEvent, TextDelta

            collected_text = ""
            async for event in client.stream(summary_conv, system=SUMMARY_PROMPT):
                if isinstance(event, TextDelta):
                    collected_text += event.text
                elif isinstance(event, StreamEnd):
                    pass
            llm_output = collected_text
            break

        except Exception as e:
            err_msg = str(e).lower()
            if "prompt" in err_msg and "long" in err_msg or "too many" in err_msg:
                groups = _group_messages_by_turn(summary_conv.history[1:-1])
                drop_count = max(1, len(groups) // 5)
                remaining = groups[drop_count:]
                summary_conv.history = (
                    [summary_conv.history[0]]
                    + [m for g in remaining for m in g]
                    + [summary_conv.history[-1]]
                )
                continue
            if breaker is not None:
                breaker.record_failure()
            return f"Summary generation failed: {e}"

    if llm_output is None:
        if breaker is not None:
            breaker.record_failure()
        return "Summary generation failed: still exceeded the context limit after multiple retries"

    summary = extract_summary(llm_output)
    attachment = build_recovery_attachment(recovery, tool_schemas)
    # Reconstruction = summary user message + raw tail.
    new_messages = build_compact_messages(
        summary,
        attachment=attachment,
        has_keep_tail=bool(keep_tail),
        transcript_path=transcript_path,
    )
    new_messages = new_messages + list(keep_tail)

    # replace_history swaps in the reconstructed conversation and clears usage
    # anchors (baseline_tokens / anchor_count / last_input_tokens). This is
    # required because the old anchor_count referred to the pre-compaction
    # message list and is now meaningless; without clearing it, current_tokens()
    # would estimate increments incorrectly. The next API response will re-anchor
    # usage against the reconstructed history.
    conversation.replace_history(new_messages)
    cleanup_tool_results(session_dir)

    if breaker is not None:
        breaker.record_success()

    # Hand the structured boundary (summary + preserved raw tail) to the session
    # layer, which persists it as a compact_boundary record. The keep tail is the
    # segment appended back into the reconstructed history.
    return CompactEvent(
        before_tokens=before_tokens,
        boundary=CompactBoundary(summary=summary, keep=list(keep_tail)),
    )
