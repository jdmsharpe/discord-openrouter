import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.embeds import (
    append_citations_embed,
    append_flat_pricing_embed,
    append_usage_embed,
)
from discord_openrouter.util import ChatUsage, ModelInfo, ModelPricing


def test_long_citation_links_are_kept_complete_or_omitted():
    first_url = "https://example.com/" + "a" * 3500
    second_url = "https://example.org/" + "b" * 1000
    embeds = []
    append_citations_embed(
        embeds,
        [
            {"title": "First", "url": first_url},
            {"title": "Second", "url": second_url},
        ],
    )

    assert f"[First]({first_url})" in embeds[0].description
    assert second_url not in embeds[0].description
    assert len(embeds[0].description) <= 4000


def test_append_usage_embed_matches_compact_footer_convention():
    embeds = []

    append_usage_embed(
        embeds,
        usage=ChatUsage(
            prompt_tokens=1_000,
            completion_tokens=500,
            cached_tokens=300,
            reasoning_tokens=200,
            server_tool_use={"web_search": 2},
        ),
        request_cost=0.05,
        daily_cost=1.50,
    )

    assert len(embeds) == 1
    description = embeds[0].description
    assert description is not None
    assert description.startswith("$0.05")
    assert "1,000 tokens in (300 cached)" in description
    assert "500 tokens out (200 reasoning)" in description
    assert "2 searches" in description
    assert "daily $1.50" in description
    assert "\n" not in description


def test_append_usage_embed_adds_second_line_for_cost_breakdown_and_upstream():
    embeds = []
    model_info = ModelInfo(
        id="anthropic/claude-sonnet-4.5",
        name="Claude Sonnet 4.5",
        pricing=ModelPricing(
            prompt=1.0,
            completion=2.0,
            request=3.0,
            web_search=7.0,
            internal_reasoning=4.0,
            input_cache_read=5.0,
            input_cache_write=6.0,
        ),
    )

    append_usage_embed(
        embeds,
        usage=ChatUsage(
            prompt_tokens=100,
            completion_tokens=50,
            reasoning_tokens=20,
            cached_tokens=30,
            cache_write_tokens=10,
            upstream_inference_cost=19.0,
            server_tool_use={"web_search_requests": 2},
        ),
        request_cost=427.0,
        daily_cost=500.0,
        model_info=model_info,
    )

    assert len(embeds) == 1
    description = embeds[0].description
    assert description is not None
    lines = description.splitlines()
    assert len(lines) == 2
    assert "input $60.00" in lines[1]
    assert "cache read $150.00" in lines[1]
    assert "cache write $60.00" in lines[1]
    assert "output $60.00" in lines[1]
    assert "reasoning $80.00" in lines[1]
    assert "request $3.00" in lines[1]
    assert "search $14.00" in lines[1]
    assert "upstream $19.00" in lines[1]


def test_append_usage_embed_uses_subcent_and_round_up_currency_formatting():
    embeds = []
    model_info = ModelInfo(
        id="openai/gpt-5.2",
        name="GPT-5.2",
        pricing=ModelPricing(
            prompt=0.0048,
            completion=0.0004,
        ),
    )

    append_usage_embed(
        embeds,
        usage=ChatUsage(
            prompt_tokens=1,
            completion_tokens=1,
        ),
        request_cost=0.0052,
        daily_cost=1.501,
        model_info=model_info,
    )

    assert len(embeds) == 1
    description = embeds[0].description
    assert description is not None
    lines = description.splitlines()
    assert lines[0].startswith("<$0.01")
    assert "daily $1.51" in lines[0]
    assert "input <$0.01" in lines[1]
    assert "output <$0.01" in lines[1]


def test_append_usage_embed_prefixes_estimated_request_cost():
    embeds = []

    append_usage_embed(
        embeds,
        usage=ChatUsage(
            prompt_tokens=1,
            completion_tokens=1,
        ),
        request_cost=0.0052,
        daily_cost=0.0052,
        request_cost_is_estimate=True,
    )

    assert len(embeds) == 1
    description = embeds[0].description
    assert description is not None
    assert description.startswith("est. <$0.01")
    assert "daily <$0.01" in description


def test_append_flat_pricing_embed_uses_compact_currency_formatting():
    embeds = []

    append_flat_pricing_embed(
        embeds,
        request_cost=0.0052,
        daily_cost=1.501,
        details="video generation",
    )

    assert len(embeds) == 1
    description = embeds[0].description
    assert description == "<$0.01 · video generation · daily $1.51"


def test_append_flat_pricing_embed_prefixes_estimated_request_cost():
    embeds = []

    append_flat_pricing_embed(
        embeds,
        request_cost=0.0052,
        daily_cost=1.501,
        details="video generation",
        request_cost_is_estimate=True,
    )

    assert len(embeds) == 1
    description = embeds[0].description
    assert description == "est. <$0.01 · video generation · daily $1.51"


def test_build_current_model_embed_shows_all_modalities_with_no_channel_defaults():
    from discord_openrouter.cogs.openrouter.embeds import build_current_model_embed

    embed = build_current_model_embed(
        active_model=None,
        active_options=None,
        channel_defaults={},
        global_defaults={
            "chat": "openai/gpt-4o-mini",
            "image": "openai/dall-e-3",
            "video": "runway/gen3",
            "tts": "openai/tts-1",
            "stt": "openai/whisper-1",
        },
    )

    desc = embed.description or ""
    assert "Chat" in desc
    assert "Image" in desc
    assert "Video" in desc
    assert "TTS" in desc
    assert "STT" in desc
    # No channel defaults set — "Channel default" line should not appear
    assert "Channel default" not in desc
    assert "openai/gpt-4o-mini" in desc
    assert "openai/dall-e-3" in desc


def test_build_current_model_embed_shows_channel_default_only_when_set():
    from discord_openrouter.cogs.openrouter.embeds import build_current_model_embed

    embed = build_current_model_embed(
        active_model="anthropic/claude-sonnet-4-5",
        active_options="web search",
        channel_defaults={"chat": "openai/gpt-4o", "image": "black-forest-labs/flux-1"},
        global_defaults={
            "chat": "openai/gpt-4o-mini",
            "image": "openai/dall-e-3",
            "video": "runway/gen3",
            "tts": "openai/tts-1",
            "stt": "openai/whisper-1",
        },
    )

    desc = embed.description or ""
    # Chat has active conversation + channel default
    assert "anthropic/claude-sonnet-4-5" in desc
    assert "web search" in desc
    assert "openai/gpt-4o" in desc
    # Image has channel default
    assert "black-forest-labs/flux-1" in desc
    # Video/TTS/STT have no channel default
    assert "runway/gen3" in desc
    # Channel default line appears exactly twice (chat and image)
    assert desc.count("Channel default") == 2


def test_build_current_model_embed_omits_active_conversation_line_when_no_active_conversation():
    from discord_openrouter.cogs.openrouter.embeds import build_current_model_embed

    embed = build_current_model_embed(
        active_model=None,
        active_options=None,
        channel_defaults={},
        global_defaults={
            "chat": "openai/gpt-4o-mini",
            "image": None,
            "video": None,
            "tts": None,
            "stt": None,
        },
    )

    desc = embed.description or ""
    assert "Active conversation" not in desc
