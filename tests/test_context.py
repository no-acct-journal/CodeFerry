from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from codeferry.context.manager import (
    AGGREGATE_CHAR_LIMIT,
    KEEP_MAX_TOKENS,
    KEEP_RECENT_TOKENS,
    MIN_KEEP_MESSAGES,
    PERSISTED_TAG,
    SINGLE_RESULT_CHAR_LIMIT,
    CompactCircuitBreaker,
    _align_keep_start_to_tool_pair,
    _compute_keep_start_index,
    apply_tool_result_budget,
    auto_compact,
    build_compact_messages,
    cleanup_tool_results,
    compute_compact_threshold,
    create_replacement_state,
    ensure_session_dir,
    extract_summary,
    make_persisted_preview,
    persist_tool_result,
    should_auto_compact,
)
from codeferry.conversation import (
    _CHARS_PER_TOKEN,
    ConversationManager,
    Message,
    ToolResultBlock,
    ToolUseBlock,
    estimate_tokens,
)

# ---------------------------------------------------------------------------
# persist_tool_result
# ---------------------------------------------------------------------------

class TestPersistToolResult:
    def test_writes_file(self, tmp_path: Path) -> None:
        fp = persist_tool_result("toolu_001", "hello world", tmp_path)
        assert fp.exists()
        assert fp.read_text() == "hello world"

    def test_idempotent(self, tmp_path: Path) -> None:
        persist_tool_result("toolu_002", "first", tmp_path)
        persist_tool_result("toolu_002", "second", tmp_path)
        fp = tmp_path / "toolu_002.txt"
        assert fp.read_text() == "first"

# ---------------------------------------------------------------------------
# make_persisted_preview
# ---------------------------------------------------------------------------

class TestMakePersistedPreview:
    def test_contains_tag_and_path(self, tmp_path: Path) -> None:
        content = "x" * 10_000
        preview = make_persisted_preview(content, tmp_path / "test.txt")
        assert preview.startswith(PERSISTED_TAG)
        assert "test.txt" in preview
        assert "</persisted-output>" in preview

    def test_preview_truncated(self, tmp_path: Path) -> None:
        content = "a" * 5_000
        preview = make_persisted_preview(content, tmp_path / "test.txt")
        lines = preview.split("\n")
        preview_line = [l for l in lines if l.startswith("aaa")]
        assert len(preview_line) == 1
        assert len(preview_line[0]) == 2_000

# ---------------------------------------------------------------------------
# apply_tool_result_budget
# ---------------------------------------------------------------------------

class TestApplyToolResultBudget:
    def test_single_oversized_persisted(self, tmp_path: Path) -> None:
        conv = ConversationManager()
        big_content = "x" * (SINGLE_RESULT_CHAR_LIMIT + 100)
        conv.history.append(
            Message(
                role="user",
                content="",
                tool_results=[
                    ToolResultBlock(
                        tool_use_id="toolu_big",
                        content=big_content,
                    )
                ],
            )
        )
        state = create_replacement_state()

        api_conv, records = apply_tool_result_budget(conv, tmp_path, state)

        tr = api_conv.history[0].tool_results[0]
        assert tr.content.startswith(PERSISTED_TAG)
        assert (tmp_path / "toolu_big.txt").exists()
        assert conv.history[0].tool_results[0].content == big_content  # Original content is unchanged.
        assert len(records) == 1 and records[0].tool_use_id == "toolu_big"

    def test_under_limit_untouched(self, tmp_path: Path) -> None:
        conv = ConversationManager()
        small_content = "x" * 100
        conv.history.append(
            Message(
                role="user",
                content="",
                tool_results=[
                    ToolResultBlock(tool_use_id="toolu_sm", content=small_content)
                ],
            )
        )
        state = create_replacement_state()

        api_conv, records = apply_tool_result_budget(conv, tmp_path, state)

        tr = api_conv.history[0].tool_results[0]
        assert tr.content == small_content
        assert not (tmp_path / "toolu_sm.txt").exists()
        assert records == []
        assert "toolu_sm" in state.seen_ids
        assert "toolu_sm" not in state.replacements

    def test_aggregate_limit(self, tmp_path: Path) -> None:
        conv = ConversationManager()
        results = []
        for i in range(5):
            results.append(
                ToolResultBlock(
                    tool_use_id=f"toolu_agg_{i}",
                    content="x" * (AGGREGATE_CHAR_LIMIT // 4),
                )
            )
        conv.history.append(Message(role="user", content="", tool_results=results))
        state = create_replacement_state()

        api_conv, _ = apply_tool_result_budget(conv, tmp_path, state)

        total = sum(len(tr.content) for tr in api_conv.history[0].tool_results)
        assert total <= AGGREGATE_CHAR_LIMIT
        # Original content is unchanged.
        orig_total = sum(len(tr.content) for tr in conv.history[0].tool_results)
        assert orig_total == 5 * (AGGREGATE_CHAR_LIMIT // 4)

    def test_already_persisted_skipped(self, tmp_path: Path) -> None:
        conv = ConversationManager()
        persisted_content = f"{PERSISTED_TAG}\nalready persisted\n</persisted-output>"
        conv.history.append(
            Message(
                role="user",
                content="",
                tool_results=[
                    ToolResultBlock(tool_use_id="toolu_done", content=persisted_content)
                ],
            )
        )
        state = create_replacement_state()

        api_conv, _ = apply_tool_result_budget(conv, tmp_path, state)

        tr = api_conv.history[0].tool_results[0]
        assert tr.content == persisted_content
        # Externally pre-tagged results are also recorded in state.replacements,
        # so repeated application remains byte-for-byte consistent later.
        assert state.replacements["toolu_done"] == persisted_content

# ---------------------------------------------------------------------------
# compute_compact_threshold
# ---------------------------------------------------------------------------

class TestComputeCompactThreshold:
    def test_auto_threshold(self) -> None:
        assert compute_compact_threshold(200_000) == 167_000

    def test_manual_threshold(self) -> None:
        assert compute_compact_threshold(200_000, manual=True) == 177_000

    def test_smaller_window(self) -> None:
        assert compute_compact_threshold(128_000) == 95_000

# ---------------------------------------------------------------------------
# should_auto_compact
# ---------------------------------------------------------------------------

class TestShouldAutoCompact:
    def test_below_threshold(self) -> None:
        assert not should_auto_compact(100_000, 200_000)

    def test_at_threshold(self) -> None:
        assert should_auto_compact(167_000, 200_000)

    def test_above_threshold(self) -> None:
        assert should_auto_compact(180_000, 200_000)

# ---------------------------------------------------------------------------
# extract_summary
# ---------------------------------------------------------------------------

class TestExtractSummary:
    def test_extracts_between_tags(self) -> None:
        output = "<analysis>blah</analysis>\n<summary>\nthe summary\n</summary>"
        assert extract_summary(output) == "the summary"

    def test_no_tags_returns_full(self) -> None:
        output = "no tags here"
        assert extract_summary(output) == output

    def test_only_summary_tag(self) -> None:
        output = "<summary>just this</summary>"
        assert extract_summary(output) == "just this"

# ---------------------------------------------------------------------------
# CompactCircuitBreaker
# ---------------------------------------------------------------------------

class TestCompactCircuitBreaker:
    def test_starts_closed(self) -> None:
        breaker = CompactCircuitBreaker()
        assert not breaker.is_open()

    def test_opens_after_max_failures(self) -> None:
        breaker = CompactCircuitBreaker(max_failures=3)
        breaker.record_failure()
        breaker.record_failure()
        assert not breaker.is_open()
        breaker.record_failure()
        assert breaker.is_open()

    def test_success_resets(self) -> None:
        breaker = CompactCircuitBreaker(max_failures=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        assert not breaker.is_open()
        breaker.record_failure()
        assert not breaker.is_open()

# ---------------------------------------------------------------------------
# build_compact_messages
# ---------------------------------------------------------------------------

class TestBuildCompactMessages:
    def test_basic_structure(self) -> None:
        msgs = build_compact_messages("the summary")
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert "[Summary]" in msgs[0].content
        assert "the summary" in msgs[0].content
        assert msgs[1].role == "assistant"
        assert "ReadFile" in msgs[1].content

# ---------------------------------------------------------------------------
# Session directory management
# ---------------------------------------------------------------------------

class TestSessionDir:
    def test_ensure_creates_dir(self, tmp_path: Path) -> None:
        session_dir = ensure_session_dir(str(tmp_path))
        assert session_dir.exists()
        assert session_dir.is_dir()

    def test_cleanup(self, tmp_path: Path) -> None:
        session_dir = ensure_session_dir(str(tmp_path))
        (session_dir / "test.txt").write_text("data")
        assert len(list(session_dir.iterdir())) == 1

        cleanup_tool_results(session_dir)
        assert session_dir.exists()
        assert len(list(session_dir.iterdir())) == 0


# ---------------------------------------------------------------------------
# Real usage anchors + incremental estimates (current_tokens)
# ---------------------------------------------------------------------------

class TestUsageAnchor:
    def test_cold_start_falls_back_to_char_estimate(self) -> None:
        """Without an anchor, current_tokens estimates the full history by character count."""
        conv = ConversationManager()
        conv.add_user_message("x" * 350)
        assert conv.baseline_tokens == 0
        # 350 characters / 3.5 == 100 tokens, matching estimate_tokens over the history.
        assert conv.current_tokens() == estimate_tokens(conv.history) == 100

    def test_anchor_aggregates_all_usage_components(self) -> None:
        """baseline = input + cache_read + cache_creation + output。"""
        conv = ConversationManager()
        conv.add_user_message("hi")
        conv.record_usage_anchor(
            input_tokens=1000,
            output_tokens=200,
            cache_read=5000,
            cache_creation=300,
        )
        assert conv.baseline_tokens == 1000 + 5000 + 300 + 200
        assert conv.anchor_count == len(conv.history)
        # Keep last_input_tokens in sync for backward-compatible readers.
        assert conv.last_input_tokens == conv.baseline_tokens

    def test_current_tokens_is_baseline_plus_increment(self) -> None:
        """With an anchor, only messages appended after the anchor are estimated."""
        conv = ConversationManager()
        conv.add_user_message("first turn")
        conv.record_usage_anchor(input_tokens=8000, output_tokens=100)
        baseline = conv.baseline_tokens  # 8100

        # No new messages yet, so this exactly equals the baseline and does not re-estimate history.
        assert conv.current_tokens() == baseline

        # Append a 700-character tool result, adding 200 estimated tokens above the baseline.
        conv.add_tool_results_message(
            [ToolResultBlock(tool_use_id="t1", content="y" * 700)]
        )
        assert conv.current_tokens() == baseline + 200
        # Messages before the anchor are trusted through the baseline and not double-counted.
        increment = estimate_tokens(conv.history[conv.anchor_count:])
        assert increment == 200

    def test_anchor_beats_char_estimate_after_cache_hit(self) -> None:
        """After a cache hit, the anchor for the real small input is lower than the
        character estimate for the same large history, so cached tokens are not
        counted twice.
        """
        conv = ConversationManager()
        conv.add_user_message("z" * 35000)  # Character estimation would produce 10000 tokens.
        # Cache hit: most of the prompt is read from cache, so real input is small.
        conv.record_usage_anchor(
            input_tokens=200, output_tokens=50, cache_read=9000
        )
        # The anchor reflects the real 9250, not the inflated character estimate.
        assert conv.current_tokens() == 9250
        assert conv.current_tokens() < estimate_tokens(conv.history)

    def test_replace_history_resets_anchor(self) -> None:
        """Compaction clears anchors so the next check starts cold."""
        conv = ConversationManager()
        conv.add_user_message("old turn")
        conv.record_usage_anchor(input_tokens=9000, output_tokens=100)
        assert conv.baseline_tokens > 0

        conv.replace_history([Message(role="user", content="summary " + "s" * 70)])
        assert conv.baseline_tokens == 0
        assert conv.anchor_count == 0
        assert conv.last_input_tokens == 0
        # Now this falls back to character estimation over the summarized history.
        assert conv.current_tokens() == estimate_tokens(conv.history)


class TestEstimateTokens:
    def test_empty(self) -> None:
        assert estimate_tokens([]) == 0

    def test_counts_text_thinking_tools_and_results(self) -> None:
        from codeferry.conversation import ThinkingBlock

        msgs = [
            Message(role="user", content="a" * 35),
            Message(
                role="assistant",
                content="b" * 35,
                thinking_blocks=[ThinkingBlock(thinking="c" * 35, signature="sig")],
                tool_uses=[ToolUseBlock("id", "Tool", {"k": "v"})],
            ),
            Message(
                role="user",
                content="",
                tool_results=[ToolResultBlock(tool_use_id="id", content="d" * 35)],
            ),
        ]
        # text(35) + text(35) + thinking(35) + tool name/arguments + result(35)
        est = estimate_tokens(msgs)
        # Lower bound: these four 35-character blocks alone are 140 chars / 3.5 = 40.
        assert est >= int(140 / _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Streaming usage -> anchor pipeline (cache field passthrough)
# ---------------------------------------------------------------------------

class TestStreamUsageCacheFields:
    def test_stream_end_carries_cache_fields(self) -> None:
        from codeferry.tools.base import StreamEnd

        end = StreamEnd(
            stop_reason="end_turn",
            input_tokens=1,
            output_tokens=2,
            cache_read=3,
            cache_creation=4,
        )
        assert end.cache_read == 3 and end.cache_creation == 4

    def test_collector_propagates_cache_fields_into_response(self) -> None:
        import asyncio

        from codeferry.agent import StreamCollector
        from codeferry.tools.base import StreamEnd

        async def _stream():
            yield StreamEnd(
                stop_reason="end_turn",
                input_tokens=1000,
                output_tokens=200,
                cache_read=5000,
                cache_creation=300,
            )

        async def _run():
            collector = StreamCollector()
            async for _ in collector.consume(_stream()):
                pass
            return collector.response

        resp = asyncio.run(_run())
        assert resp.cache_read == 5000
        assert resp.cache_creation == 300

        # Feeding this response into the anchor reproduces the full baseline.
        conv = ConversationManager()
        conv.record_usage_anchor(
            resp.input_tokens, resp.output_tokens,
            resp.cache_read, resp.cache_creation,
        )
        assert conv.baseline_tokens == 1000 + 5000 + 300 + 200


# ---------------------------------------------------------------------------
# Recent-verbatim retention window: keepStartIndex calculation + tool pairing
# ---------------------------------------------------------------------------

# _CHARS_PER_TOKEN == 3.5, so a message with N*3.5 characters estimates to about N tokens.
def _user(text_tokens: int) -> Message:
    return Message(role="user", content="u" * int(text_tokens * _CHARS_PER_TOKEN))


def _assistant(text_tokens: int) -> Message:
    return Message(role="assistant", content="a" * int(text_tokens * _CHARS_PER_TOKEN))


class TestComputeKeepStartIndex:
    def test_empty_history(self) -> None:
        assert _compute_keep_start_index([]) == 0

    def test_stops_at_token_floor(self) -> None:
        # 10 messages, about 4000 tokens each. Walking backward from the tail,
        # the third message reaches about 12000 >= KEEP_RECENT_TOKENS (10000),
        # so traversal stops and keeps the last 3 messages.
        msgs = [_user(4000) for _ in range(10)]
        keep_start = _compute_keep_start_index(msgs)
        kept = msgs[keep_start:]
        assert len(kept) == 3
        assert keep_start == 7
        assert estimate_tokens(kept) >= KEEP_RECENT_TOKENS

    def test_message_floor_when_tail_is_tiny(self) -> None:
        # Tiny messages never reach the token floor, so traversal eventually stops
        # at the MIN_KEEP_MESSAGES message-count floor.
        msgs = [_user(50) for _ in range(20)]
        keep_start = _compute_keep_start_index(msgs)
        assert len(msgs[keep_start:]) == MIN_KEEP_MESSAGES
        assert keep_start == 20 - MIN_KEEP_MESSAGES

    def test_max_cap_stops_swallowing_history(self) -> None:
        # A huge tail message (> KEEP_MAX_TOKENS) is kept because the final message
        # is never rejected, but traversal stops before including earlier messages.
        big = _user(KEEP_MAX_TOKENS // 1000 * 1000 + 5000)
        msgs = [_user(4000) for _ in range(6)] + [big]
        keep_start = _compute_keep_start_index(msgs)
        assert keep_start == len(msgs) - 1  # Keep only that huge tail message.
        assert estimate_tokens(msgs[keep_start:]) > KEEP_MAX_TOKENS

    def test_short_history_keeps_everything(self) -> None:
        # Fewer messages than MIN_KEEP_MESSAGES means keep_start walks all the way to 0.
        msgs = [_user(50) for _ in range(3)]
        assert _compute_keep_start_index(msgs) == 0


class TestAlignKeepStartToToolPair:
    def test_orphan_tool_result_pulled_back_to_tool_use(self) -> None:
        # assistant(tool_use) is at idx2 and user(tool_result) is at idx3. If
        # keep_start lands on idx3, it would keep a dangling tool_result, so it
        # is pulled back to idx2.
        msgs = [
            _user(10),
            _assistant(10),
            Message(role="assistant", content="call",
                    tool_uses=[ToolUseBlock("t1", "ReadFile", {})]),
            Message(role="user", content="",
                    tool_results=[ToolResultBlock("t1", "data")]),
        ]
        assert _align_keep_start_to_tool_pair(msgs, 3) == 2

    def test_non_tool_boundary_untouched(self) -> None:
        msgs = [_user(10), _assistant(10), _user(10)]
        assert _align_keep_start_to_tool_pair(msgs, 2) == 2

    def test_pairing_preserved_via_compute(self) -> None:
        # If the computed keep_start would split a tool_use/tool_result pair, it is corrected.
        msgs = [_user(4000) for _ in range(6)]
        # Make the message at the natural retention boundary a tool_result, with
        # its corresponding tool_use immediately before it.
        msgs[6:6] = []  # No-op retained only to make intent explicit.
        msgs = [
            _user(4000), _user(4000), _user(4000), _user(4000),
            Message(role="assistant", content="call",
                    tool_uses=[ToolUseBlock("tx", "Grep", {})]),
            Message(role="user", content="",
                    tool_results=[ToolResultBlock("tx", "y" * (4000 * 3))]),
            _user(4000),
        ]
        keep_start = _compute_keep_start_index(msgs)
        kept = msgs[keep_start:]
        # If a tool_result is kept, its corresponding tool_use must also be kept.
        kept_result_ids = {
            tr.tool_use_id for m in kept for tr in m.tool_results
        }
        kept_use_ids = {
            tu.tool_use_id for m in kept for tu in m.tool_uses
        }
        assert kept_result_ids <= kept_use_ids


# ---------------------------------------------------------------------------
# auto_compact: Keep recent messages verbatim + summarize only the prefix + reset anchors
# ---------------------------------------------------------------------------

class _SummaryClient:
    """A minimal streaming client that returns a fixed summary and records the history it was asked to summarize."""

    def __init__(self, summary_body: str = "PREFIX SUMMARY") -> None:
        self.summary_body = summary_body
        self.summarized_history: list[Message] | None = None

    async def stream(self, conversation, system=""):
        from codeferry.tools.base import StreamEnd, TextDelta

        # Snapshot the content sent to the summarizer, excluding the orchestrator's
        # extra opening prompt and trailing "please generate a summary" instruction.
        self.summarized_history = list(conversation.history)
        yield TextDelta(text=f"<summary>{self.summary_body}</summary>")
        yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=10)


def _make_long_conversation(n_tail: int = 6, tail_tokens: int = 4000) -> ConversationManager:
    conv = ConversationManager()
    # Old prefix worth summarizing, far above MIN_SUMMARIZE_PREFIX_TOKENS.
    for i in range(8):
        conv.history.append(_user(3000))
        conv.history.append(_assistant(3000))
    # Distinguishable recent tail messages that we assert are kept verbatim.
    for i in range(n_tail):
        conv.history.append(
            Message(role="user", content=f"RECENT_{i}_" + "z" * int(tail_tokens * _CHARS_PER_TOKEN))
        )
    return conv


@pytest.mark.asyncio
class TestAutoCompactKeepRecent:
    async def test_recent_messages_kept_verbatim(self, tmp_path: Path) -> None:
        conv = _make_long_conversation()
        # Snapshot which tail messages the retention window selected, so assertions
        # follow the algorithm instead of relying on a hard-coded count.
        keep_start = _compute_keep_start_index(conv.history)
        kept_before = list(conv.history[keep_start:])
        assert kept_before, "fixture should keep a non-empty tail"

        client = _SummaryClient()
        # Pin a high anchor so the auto-compaction threshold is triggered.
        conv.record_usage_anchor(input_tokens=200_000)

        result = await auto_compact(
            conv, client, context_window=200_000, session_dir=tmp_path,
        )

        # Compaction completed.
        from codeferry.context.manager import CompactEvent
        assert isinstance(result, CompactEvent)

        joined = "\n".join(m.content for m in conv.history)
        # Summary exists.
        assert "PREFIX SUMMARY" in joined
        # Kept recent text remains verbatim and was not rewritten into the summary.
        # The kept tail objects are the same message instances, reused unchanged.
        for m in kept_before:
            assert m in conv.history

    async def test_summary_only_covers_prefix(self, tmp_path: Path) -> None:
        conv = _make_long_conversation()
        keep_start = _compute_keep_start_index(conv.history)
        kept_contents = {m.content for m in conv.history[keep_start:]}
        client = _SummaryClient()
        conv.record_usage_anchor(input_tokens=200_000)

        await auto_compact(
            conv, client, context_window=200_000, session_dir=tmp_path,
        )

        # The history fed to the summarizer must not contain any kept tail messages;
        # the summary covers only messages[:keep_start].
        assert client.summarized_history is not None
        summarized_contents = {m.content for m in client.summarized_history}
        assert not (kept_contents & summarized_contents)

    async def test_tool_pair_not_split(self, tmp_path: Path) -> None:
        conv = ConversationManager()
        for i in range(8):
            conv.history.append(_user(3000))
            conv.history.append(_assistant(3000))
        # The recent tail ends with a tool_use/tool_result pair.
        conv.history.append(
            Message(role="assistant", content="calling",
                    tool_uses=[ToolUseBlock("tk", "Grep", {})])
        )
        conv.history.append(
            Message(role="user", content="",
                    tool_results=[ToolResultBlock("tk", "RESULT_DATA")])
        )
        conv.record_usage_anchor(input_tokens=200_000)
        client = _SummaryClient()

        await auto_compact(
            conv, client, context_window=200_000, session_dir=tmp_path,
        )

        # If the tool_result is kept, its corresponding tool_use must also be kept.
        result_ids = {tr.tool_use_id for m in conv.history for tr in m.tool_results}
        use_ids = {tu.tool_use_id for m in conv.history for tu in m.tool_uses}
        assert result_ids <= use_ids

    async def test_anchor_reset_after_compact(self, tmp_path: Path) -> None:
        conv = _make_long_conversation()
        conv.record_usage_anchor(input_tokens=200_000)
        assert conv.baseline_tokens > 0 and conv.anchor_count > 0
        client = _SummaryClient()

        await auto_compact(
            conv, client, context_window=200_000, session_dir=tmp_path,
        )

        # replace_history must have cleared the stale anchors.
        assert conv.baseline_tokens == 0
        assert conv.anchor_count == 0
        assert conv.last_input_tokens == 0

    async def test_too_few_messages_degrades_to_no_compaction(
        self, tmp_path: Path
    ) -> None:
        conv = ConversationManager()
        for i in range(3):
            conv.history.append(
                Message(role="user", content=f"ONLY_{i}_" + "z" * 100)
            )
        before = list(conv.history)
        client = _SummaryClient()

        result = await auto_compact(
            conv, client, context_window=200_000, session_dir=tmp_path,
            manual=True,
        )

        # Nothing can be summarized, so degrade gracefully: history is unchanged and no summary is added.
        assert result is None
        assert conv.history == before
        assert client.summarized_history is None

    async def test_event_carries_boundary_summary_and_keep(
        self, tmp_path: Path
    ) -> None:
        # The returned CompactEvent must pass a structured boundary (summary +
        # exact verbatim kept tail) to the session layer so it can persist a
        # compact_boundary record.
        conv = _make_long_conversation()
        keep_start = _compute_keep_start_index(conv.history)
        kept_before = list(conv.history[keep_start:])
        client = _SummaryClient()
        conv.record_usage_anchor(input_tokens=200_000)

        result = await auto_compact(
            conv, client, context_window=200_000, session_dir=tmp_path,
        )

        from codeferry.context.manager import CompactEvent

        assert isinstance(result, CompactEvent)
        assert result.boundary is not None
        assert result.boundary.summary == "PREFIX SUMMARY"
        # The kept tail exactly matches the content reused verbatim.
        assert result.boundary.keep == kept_before
