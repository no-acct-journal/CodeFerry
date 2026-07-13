
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager
from mewcode.serialization import (
    build_anthropic_messages,
    build_chat_completion_messages,
    build_openai_input,
)
from mewcode.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)


# Cap timeout for automatic model metadata fetch so a slow or hung
# /v1/models endpoint cannot block startup. On timeout, fall back to None
# (unknown) and let the next context-window resolution layer take over.
ANTHROPIC_MODEL_FETCH_TIMEOUT = 3.0


_EPHEMERAL = {"type": "ephemeral"}


def _mark_last_user_tail_for_cache(messages: list[dict[str, Any]]) -> None:
    """Attach cache_control to the last block of the final user message.

    Mutates `messages` in place. Anthropic caches the prefix up to and including
    this block; on later requests, byte-identical prefixes pay only 10% for
    cache-hit tokens. Anthropic-protocol messages only.
    """
    if not messages:
        return
    # Scan backward for the last user message; an assistant tail cannot anchor cache.
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            # Upgrade string content to block form so cache_control can be attached.
            msg["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": _EPHEMERAL,
            }]
        elif isinstance(content, list) and content:
            last = content[-1]
            if isinstance(last, dict):
                last["cache_control"] = _EPHEMERAL
        return


def _mark_last_tool_for_cache(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a shallow copy of tools with cache_control on the last tool.

    Tool schemas are stable across turns, so marking the list tail caches the
    entire tool block. We avoid mutating the caller's list because those schemas
    are often module-level singletons in the registry.
    """
    if not tools:
        return tools
    marked = list(tools)
    last = dict(marked[-1])
    last["cache_control"] = _EPHEMERAL
    marked[-1] = last
    return marked


class LLMError(Exception):
    pass


class AuthenticationError(LLMError):
    pass


class RateLimitError(LLMError):


    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NetworkError(LLMError):
    pass


class LLMClient(ABC):
    @abstractmethod
    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta("")

    def set_max_output_tokens(self, tokens: int) -> None:
        pass


def _supports_adaptive_thinking(model: str) -> bool:
    for family in ("claude-opus-4-", "claude-sonnet-4-"):
        if model.startswith(family):
            rest = model[len(family):]
            if rest and rest[0].isdigit() and int(rest[0]) >= 6:
                return True
    return False


class AnthropicClient(LLMClient):
    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.thinking = config.thinking
        self.max_output_tokens = config.get_max_output_tokens()
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "Anthropic API key not found. "
                "Set it in .mewcode/config.yaml or via ANTHROPIC_API_KEY env var."
            )
        self._client = AsyncAnthropic(api_key=api_key, base_url=config.base_url)

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    async def fetch_model_context_window(self) -> int | None:
        """Query max_input_tokens from an Anthropic-compatible /v1/models/{model}
        endpoint (layer 2 of context-window resolution).

        Best-effort: any error—non-Anthropic endpoint, network failure, timeout,
        missing field—returns ``None`` instead of raising so the caller can fall
        back to the next layer. Blocks no longer than ANTHROPIC_MODEL_FETCH_TIMEOUT
        and never propagates exceptions, so safe to call at startup.
        """
        try:
            info = await self._client.models.retrieve(
                self.model, timeout=ANTHROPIC_MODEL_FETCH_TIMEOUT
            )
            window = getattr(info, "max_input_tokens", None)
            if isinstance(window, int) and window > 0:
                return window
            return None
        except Exception:
            return None

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        import anthropic as _anthropic

        messages = build_anthropic_messages(conversation.get_messages())

        # Mark prompt-cache breakpoints on the longest stable prefix: system,
        # tools, and the tail of the last user message. Anthropic caches up to
        # each breakpoint and byte-compares on the next request—ContentReplacementState
        # in context.manager keeps tool_result content after breakpoints stable.
        _mark_last_user_tail_for_cache(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
        if tools:
            kwargs["tools"] = _mark_last_tool_for_cache(tools)

        if self.thinking:
            if _supports_adaptive_thinking(self.model):
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 0}
            else:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": max(self.max_output_tokens - 1, 1024),
                }

        current_tool_name = ""
        current_tool_id = ""
        json_accum = ""
        in_thinking = False
        thinking_accum = ""
        thinking_signature = ""

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        if block.type == "thinking":
                            in_thinking = True
                            thinking_accum = ""
                            thinking_signature = ""
                        elif block.type == "tool_use":
                            current_tool_name = block.name
                            current_tool_id = block.id
                            json_accum = ""
                            yield ToolCallStart(
                                tool_name=current_tool_name,
                                tool_id=current_tool_id,
                            )
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield TextDelta(text=delta.text)
                        elif delta.type == "thinking_delta":
                            thinking_accum += delta.thinking
                            yield ThinkingDelta(text=delta.thinking)
                        elif delta.type == "signature_delta":
                            thinking_signature = delta.signature
                        elif delta.type == "input_json_delta":
                            json_accum += delta.partial_json
                            yield ToolCallDelta(text=delta.partial_json)
                    elif event.type == "content_block_stop":
                        if in_thinking:
                            yield ThinkingComplete(
                                thinking=thinking_accum,
                                signature=thinking_signature,
                            )
                            in_thinking = False
                        if current_tool_name:
                            try:
                                args = json.loads(json_accum) if json_accum else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield ToolCallComplete(
                                tool_id=current_tool_id,
                                tool_name=current_tool_name,
                                arguments=args,
                            )
                            current_tool_name = ""
                            current_tool_id = ""
                            json_accum = ""
                    elif event.type == "message_stop":
                        pass

                final = await stream.get_final_message()
                usage = final.usage
                yield StreamEnd(
                    stop_reason=final.stop_reason or "end_turn",
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_creation=getattr(
                        usage, "cache_creation_input_tokens", 0
                    ) or 0,
                )

        except _anthropic.AuthenticationError as e:
            raise AuthenticationError(f"Invalid API key: {e}") from e
        except _anthropic.RateLimitError as e:
            retry = e.response.headers.get("retry-after") if e.response else None
            raise RateLimitError(
                f"Rate limited. {f'Retry after {retry}s.' if retry else 'Please wait.'}",
                retry_after=float(retry) if retry else None,
            ) from e
        except _anthropic.APIConnectionError as e:
            raise NetworkError(f"Network error: {e}") from e
        except _anthropic.APIStatusError as e:
            raise LLMError(f"API error ({e.status_code}): {e.message}") from e


class OpenAIClient(LLMClient):
    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.max_output_tokens = config.get_max_output_tokens()
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "OpenAI API key not found. "
                "Set it in .mewcode/config.yaml or via OPENAI_API_KEY env var."
            )
        self._client = AsyncOpenAI(api_key=api_key, base_url=config.base_url)

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        import openai as _openai

        input_messages = build_openai_input(conversation.get_messages())

        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "stream": True,
        }
        if system:
            kwargs["instructions"] = system
        if tools:
            kwargs["tools"] = tools

        current_tool_name = ""
        current_call_id = ""
        json_accum = ""

        try:
            response_stream = await self._client.responses.create(**kwargs)
            async for event in response_stream:
                if event.type == "response.output_text.delta":
                    yield TextDelta(text=event.delta)
                elif event.type == "response.function_call_arguments.delta":
                    if not current_tool_name:
                        current_tool_name = getattr(event, "name", "") or ""
                        current_call_id = getattr(event, "call_id", "") or ""
                        if current_tool_name:
                            yield ToolCallStart(
                                tool_name=current_tool_name,
                                tool_id=current_call_id,
                            )
                    json_accum += event.delta
                    yield ToolCallDelta(text=event.delta)
                elif event.type == "response.function_call_arguments.done":
                    if not current_tool_name:
                        current_tool_name = getattr(event, "name", "") or ""
                        current_call_id = getattr(event, "call_id", "") or ""
                    try:
                        args = json.loads(json_accum) if json_accum else {}
                    except json.JSONDecodeError:
                        args = {}
                    yield ToolCallComplete(
                        tool_id=current_call_id,
                        tool_name=current_tool_name,
                        arguments=args,
                    )
                    current_tool_name = ""
                    current_call_id = ""
                    json_accum = ""
                elif event.type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", "") == "function_call":
                        current_tool_name = getattr(item, "name", "")
                        current_call_id = getattr(item, "call_id", "")
                        json_accum = ""
                        yield ToolCallStart(
                            tool_name=current_tool_name,
                            tool_id=current_call_id,
                        )
                elif event.type == "response.completed":
                    resp = getattr(event, "response", None)
                    usage = getattr(resp, "usage", None) if resp else None
                    # Responses API exposes cache hits via
                    # input_tokens_details.cached_tokens; no creation count.
                    # input_tokens *includes* cached tokens, so subtract them to
                    # keep input + cache_read additive, aligned with Anthropic.
                    details = getattr(usage, "input_tokens_details", None)
                    cache_read = getattr(details, "cached_tokens", 0) or 0
                    input_tokens = getattr(usage, "input_tokens", 0) or 0
                    yield StreamEnd(
                        stop_reason="end_turn",
                        input_tokens=max(input_tokens - cache_read, 0),
                        output_tokens=getattr(usage, "output_tokens", 0) or 0,
                        cache_read=cache_read,
                        cache_creation=0,
                    )

        except _openai.AuthenticationError as e:
            raise AuthenticationError(f"Invalid API key: {e}") from e
        except _openai.RateLimitError as e:
            retry = None
            if hasattr(e, "response") and e.response is not None:
                retry = e.response.headers.get("retry-after")
            raise RateLimitError(
                f"Rate limited. {f'Retry after {retry}s.' if retry else 'Please wait.'}",
                retry_after=float(retry) if retry else None,
            ) from e
        except _openai.APIConnectionError as e:
            raise NetworkError(f"Network error: {e}") from e
        except _openai.APIStatusError as e:
            raise LLMError(f"API error ({e.status_code}): {e.message}") from e


class OpenAICompatClient(LLMClient):
    """Client for OpenAI-compatible providers using the Chat Completions API.

    Unlike ``OpenAIClient``, which targets the newer Responses API (``/responses``),
    this client uses the widely supported Chat Completions endpoint
    (``/chat/completions``), so it works with any provider exposing an OpenAI-
    compatible interface (e.g. vLLM, Ollama, Together, Azure OpenAI).
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.max_output_tokens = config.get_max_output_tokens()
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "OpenAI-compatible API key not found. "
                "Set it in .mewcode/config.yaml or via OPENAI_API_KEY env var."
            )
        self._client = AsyncOpenAI(api_key=api_key, base_url=config.base_url)

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert tool schemas to Chat Completions format.

        The tool registry emits Responses API-style dicts for ``openai``::

            {"type": "function", "name": "...", "description": "...",
             "parameters": {...}}

        Chat Completions nests name/description/parameters under ``function``::

            {"type": "function", "function": {"name": "...",
             "description": "...", "parameters": {...}}}
        """
        converted: list[dict[str, Any]] = []
        for t in tools:
            converted.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", t.get("input_schema", {})),
                },
            })
        return converted

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        import openai as _openai

        messages = build_chat_completion_messages(conversation.get_messages())

        # Prepend a system message when one is provided.
        if system:
            messages = [{"role": "system", "content": system}] + messages

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        # State for accumulating streaming tool calls. Chat Completions streams
        # deltas by index in the tool_calls list; track each in-flight call by index.
        active_calls: dict[int, dict[str, str]] = {}  # index -> {id, name, args}

        try:
            response = await self._client.chat.completions.create(**kwargs)
            async for chunk in response:
                if not chunk.choices:
                    # Final chunk with usage data only.
                    if chunk.usage:
                        # Some compat providers report cache hits via
                        # prompt_tokens_details.cached_tokens; most do not
                        # (cache_read stays 0). prompt_tokens includes cached
                        # tokens—subtract them to keep input + cache_read additive.
                        # No provider reports a creation count.
                        details = getattr(
                            chunk.usage, "prompt_tokens_details", None
                        )
                        cache_read = getattr(details, "cached_tokens", 0) or 0
                        prompt_tokens = chunk.usage.prompt_tokens or 0
                        yield StreamEnd(
                            stop_reason="end_turn",
                            input_tokens=max(prompt_tokens - cache_read, 0),
                            output_tokens=chunk.usage.completion_tokens or 0,
                            cache_read=cache_read,
                            cache_creation=0,
                        )
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # --- Text content ---
                if delta and delta.content:
                    yield TextDelta(text=delta.content)

                # --- Tool call deltas ---
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in active_calls:
                            active_calls[idx] = {"id": "", "name": "", "args": ""}
                        call = active_calls[idx]

                        if tc.id:
                            call["id"] = tc.id
                        if tc.function and tc.function.name:
                            call["name"] = tc.function.name
                            yield ToolCallStart(
                                tool_name=call["name"],
                                tool_id=call["id"],
                            )
                        if tc.function and tc.function.arguments:
                            call["args"] += tc.function.arguments
                            yield ToolCallDelta(text=tc.function.arguments)

                # --- Finish reason ---
                if choice.finish_reason in ("tool_calls", "stop"):
                    if choice.finish_reason == "tool_calls":
                        for _idx, call in sorted(active_calls.items()):
                            try:
                                args = json.loads(call["args"]) if call["args"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield ToolCallComplete(
                                tool_id=call["id"],
                                tool_name=call["name"],
                                arguments=args,
                            )
                        active_calls.clear()

        except _openai.AuthenticationError as e:
            raise AuthenticationError(f"Invalid API key: {e}") from e
        except _openai.RateLimitError as e:
            retry = None
            if hasattr(e, "response") and e.response is not None:
                retry = e.response.headers.get("retry-after")
            raise RateLimitError(
                f"Rate limited. {f'Retry after {retry}s.' if retry else 'Please wait.'}",
                retry_after=float(retry) if retry else None,
            ) from e
        except _openai.APIConnectionError as e:
            raise NetworkError(f"Network error: {e}") from e
        except _openai.APIStatusError as e:
            raise LLMError(f"API error ({e.status_code}): {e.message}") from e


def create_client(config: ProviderConfig) -> LLMClient:
    if config.protocol == "anthropic":
        return AnthropicClient(config)
    elif config.protocol == "openai":
        return OpenAIClient(config)
    elif config.protocol == "openai-compat":
        return OpenAICompatClient(config)
    raise ValueError(f"Unknown protocol: {config.protocol}")


async def resolve_context_window(config: ProviderConfig) -> None:
    """Layer 2 of context-window resolution: for anthropic-protocol providers,
    fetch max_input_tokens once from {base_url}/v1/models/{model} and cache it
    on ``config`` via set_fetched_context_window so later config.get_context_window()
    calls use it without another network round trip.

    Fully best-effort and never raises: non-anthropic providers, client construction
    failure (e.g. missing API key), fetch failure or timeout leave the cache
    unchanged so get_context_window() falls back to the built-in map / default.
    Safe at startup—blocks no longer than the fetch timeout and cannot crash.
    """
    # An explicit window in config wins in get_context_window(); a value already
    # cached from a prior call needs no refetch—skip the network request.
    if config.context_window > 0 or config._fetched_context_window > 0:
        return
    if config.protocol != "anthropic":
        return

    try:
        client = create_client(config)
    except Exception:
        return
    fetch = getattr(client, "fetch_model_context_window", None)
    if fetch is None:
        return

    try:
        window = await fetch()
    except Exception:
        window = None
    if window:
        config.set_fetched_context_window(window)
