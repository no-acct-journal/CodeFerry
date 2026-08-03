"""Tests for the four-layer context window resolution logic.

Layers, from highest to lowest priority:
  1. Explicit context_window from config (> 0): explicit override.
  2. Value auto-fetched from the provider's /v1/models endpoint (anthropic only).
  3. Built-in "model name -> window" mapping table (substring matching).
  4. Conservative default (claude -> 200000, otherwise -> 128000).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from codeferry.client import resolve_context_window
from codeferry.config import ProviderConfig
from codeferry.validator import (
    ConfigError,
    lookup_model_context_window,
    validate_providers,
)


def _provider(**overrides) -> ProviderConfig:
    base = dict(
        name="p",
        protocol="anthropic",
        base_url="https://example.test",
        model="claude-sonnet-4-6",
        api_key="k",
    )
    base.update(overrides)
    return ProviderConfig(**base)


# ---------------------------------------------------------------------------
# Layer 1: Config-provided values have the highest priority
# ---------------------------------------------------------------------------

class TestConfigPriority:
    def test_explicit_config_wins_over_mapping_table(self):
        # claude maps to 200000 by default, but the explicit window must override it.
        p = _provider(model="claude-sonnet-4-6", context_window=4096)
        assert p.get_context_window() == 4096

    def test_explicit_config_wins_over_fetched_value(self):
        p = _provider(context_window=4096)
        # Even a cached auto-fetched value must not override explicit config.
        p.set_fetched_context_window(999_000)
        assert p.get_context_window() == 4096

    def test_explicit_config_wins_over_default(self):
        # "mystery-model" has no mapping table entry and would default to 128000.
        p = _provider(model="mystery-model", context_window=321_000)
        assert p.get_context_window() == 321_000


# ---------------------------------------------------------------------------
# Layer 3: Built-in mapping table, using substring matching by model family
# ---------------------------------------------------------------------------

class TestMappingTable:
    @pytest.mark.parametrize(
        "model, expected",
        [
            # Contains the "1m" substring (including the "-1m" suffix) -> 1,000,000.
            ("claude-sonnet-4-6-1m", 1_000_000),
            ("some-model-1m", 1_000_000),
            ("gpt-4.1", 1_000_000),
            ("gpt-4.1-mini", 1_000_000),
            ("gpt-4o", 128_000),
            ("gpt-4o-mini", 128_000),
            ("gpt-4-turbo", 128_000),
            ("o1", 200_000),
            ("o1-preview", 200_000),
            ("o3-mini", 200_000),
            ("o4-mini", 200_000),
            ("gpt-3.5-turbo", 16_385),
            ("claude-opus-4-6", 200_000),
            ("CLAUDE-OPUS-4-6", 200_000),  # Case-insensitive.
        ],
    )
    def test_mapping_hits(self, model, expected):
        assert lookup_model_context_window(model) == expected
        # Without config or auto-fetching, get_context_window must return the same result.
        assert _provider(model=model).get_context_window() == expected

    def test_specificity_order_gpt_4_1_before_generic(self):
        # "gpt-4.1" must win even when there is no more specific match.
        assert lookup_model_context_window("gpt-4.1-nano") == 1_000_000

    def test_no_match_returns_zero(self):
        assert lookup_model_context_window("totally-unknown-model") == 0


# ---------------------------------------------------------------------------
# Layer 4: Conservative defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_claude_default(self):
        # A claude name with no other signals hits the "claude" mapping entry.
        assert _provider(model="claude-future-99").get_context_window() == 200_000

    def test_unknown_model_default(self):
        assert _provider(model="some-llm-v2").get_context_window() == 128_000


# ---------------------------------------------------------------------------
# Layer 2: Auto-fetching, caching, and graceful degradation
# ---------------------------------------------------------------------------

class TestAutoFetch:
    @pytest.mark.asyncio
    async def test_fetch_success_is_cached_and_used(self):
        p = _provider(model="claude-sonnet-4-6")
        fake = AsyncMock()
        fake.fetch_model_context_window = AsyncMock(return_value=555_000)
        with patch("codeferry.client.create_client", return_value=fake) as mk:
            await resolve_context_window(p)
            # The layer-2 value has priority over the mapping table (200000).
            assert p.get_context_window() == 555_000
            # The second resolution must not issue another network request because it is cached.
            await resolve_context_window(p)
            mk.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_raises_degrades_to_mapping_table(self):
        p = _provider(model="claude-sonnet-4-6")
        fake = AsyncMock()
        fake.fetch_model_context_window = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        with patch("codeferry.client.create_client", return_value=fake):
            # This should not raise.
            await resolve_context_window(p)
        # Falls back to the mapping table for claude.
        assert p.get_context_window() == 200_000

    @pytest.mark.asyncio
    async def test_fetch_returns_none_degrades_to_default(self):
        p = _provider(model="totally-unknown-model")
        fake = AsyncMock()
        fake.fetch_model_context_window = AsyncMock(return_value=None)
        with patch("codeferry.client.create_client", return_value=fake):
            await resolve_context_window(p)
        # Nothing was fetched and nothing matched, so use the conservative default.
        assert p.get_context_window() == 128_000

    @pytest.mark.asyncio
    async def test_client_construction_failure_degrades(self):
        # For example, create_client may raise for a missing API key; it must be swallowed.
        p = _provider(model="claude-sonnet-4-6")
        with patch(
            "codeferry.client.create_client",
            side_effect=Exception("no api key"),
        ):
            await resolve_context_window(p)
        assert p.get_context_window() == 200_000

    @pytest.mark.asyncio
    async def test_non_anthropic_provider_is_not_fetched(self):
        p = _provider(protocol="openai-compat", model="gpt-4o")
        with patch("codeferry.client.create_client") as mk:
            await resolve_context_window(p)
            mk.assert_not_called()
        # Resolved entirely through the mapping table.
        assert p.get_context_window() == 128_000

    @pytest.mark.asyncio
    async def test_explicit_config_skips_fetch(self):
        p = _provider(model="claude-sonnet-4-6", context_window=4096)
        with patch("codeferry.client.create_client") as mk:
            await resolve_context_window(p)
            mk.assert_not_called()
        assert p.get_context_window() == 4096

    @pytest.mark.asyncio
    async def test_zero_or_negative_fetch_is_ignored(self):
        p = _provider(model="claude-sonnet-4-6")
        fake = AsyncMock()
        fake.fetch_model_context_window = AsyncMock(return_value=0)
        with patch("codeferry.client.create_client", return_value=fake):
            await resolve_context_window(p)
        # 0 must never be cached; resolution still goes through the mapping table.
        assert p._fetched_context_window == 0
        assert p.get_context_window() == 200_000


# ---------------------------------------------------------------------------
# Validator: Unset context_window stays at 0 ("unset"), and values are validated
# ---------------------------------------------------------------------------

class TestValidator:
    def test_unset_context_window_defaults_to_zero(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "anthropic",
                    "base_url": "u",
                    "model": "claude-sonnet-4-6",
                }
            ]
        )
        # 0 means "unset"; actual resolution happens when get_context_window() is called.
        assert cleaned[0]["context_window"] == 0

    def test_explicit_context_window_preserved(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "anthropic",
                    "base_url": "u",
                    "model": "claude-sonnet-4-6",
                    "context_window": 50_000,
                }
            ]
        )
        assert cleaned[0]["context_window"] == 50_000

    @pytest.mark.parametrize("bad", [-1, "200000", True, 3.5])
    def test_invalid_context_window_rejected(self, bad):
        with pytest.raises(ConfigError):
            validate_providers(
                [
                    {
                        "name": "p",
                        "protocol": "anthropic",
                        "base_url": "u",
                        "model": "claude-sonnet-4-6",
                        "context_window": bad,
                    }
                ]
            )
