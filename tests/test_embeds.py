import pytest

pytest.importorskip("discord")

from discord import Colour

from discord_openrouter.cogs.openrouter.embeds import (
    append_citations_embed,
    append_flat_pricing_embed,
    append_usage_embed,
    build_model_list_embed,
)
from discord_openrouter.util import ChatUsage, extract_usage


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


def test_append_usage_embed_builds_one_cost_line():
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
    assert embeds[0].description == (
        "$0.0500 · 1k in (300 cached) / 500 out (200 thinking) · 2 searches · $1.50 today"
    )
    assert embeds[0].colour == Colour.blue()


def test_append_usage_embed_counts_searches_from_server_tool_use_details():
    embeds = []
    usage = extract_usage(
        {
            "usage": {
                "prompt_tokens": 5012,
                "completion_tokens": 310,
                "server_tool_use_details": {
                    "web_search_requests": 1,
                    "tool_calls_requested": 1,
                    "tool_calls_executed": 1,
                },
            }
        }
    )

    append_usage_embed(
        embeds,
        usage=usage,
        request_cost=0.0213,
        daily_cost=None,
    )

    assert embeds[0].description == "$0.0213 · 5k in / 310 out · 1 search"


def test_append_usage_embed_leaves_out_cache_writes_and_media_token_splits():
    embeds = []

    append_usage_embed(
        embeds,
        usage=ChatUsage(
            prompt_tokens=100,
            completion_tokens=50,
            reasoning_tokens=20,
            cached_tokens=30,
            cache_write_tokens=10,
            input_audio_tokens=5,
            input_video_tokens=4,
            output_audio_tokens=3,
            output_image_tokens=7,
            upstream_inference_cost=19.0,
            server_tool_use={"web_search_requests": 2},
        ),
        request_cost=427.0,
        daily_cost=500.0,
    )

    assert len(embeds) == 1
    assert embeds[0].description == (
        "$427.0000 · 100 in (30 cached) / 50 out (20 thinking) · 2 searches · $500.00 today"
    )


def test_append_usage_embed_shows_request_cost_to_four_decimals_and_daily_in_cents():
    embeds = []

    append_usage_embed(
        embeds,
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
        request_cost=0.0052,
        daily_cost=1.501,
    )

    assert embeds[0].description == "$0.0052 · 1 in / 1 out · $1.50 today"


def test_append_usage_embed_prefixes_estimated_request_cost():
    embeds = []

    append_usage_embed(
        embeds,
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
        request_cost=0.0052,
        daily_cost=0.0052,
        request_cost_is_estimate=True,
    )

    assert embeds[0].description == "est. $0.0052 · 1 in / 1 out · <$0.01 today"


def test_append_usage_embed_leaves_out_unknown_costs():
    embeds = []

    append_usage_embed(
        embeds,
        usage=ChatUsage(prompt_tokens=12_500, completion_tokens=405, cached_tokens=12_500),
        request_cost=None,
        daily_cost=None,
    )

    assert embeds[0].description == "12.5k in (12.5k cached) / 405 out"


def test_append_flat_pricing_embed_builds_one_cost_line():
    embeds = []

    append_flat_pricing_embed(
        embeds,
        request_cost=0.0052,
        daily_cost=1.501,
        details=["1 video", "720p"],
    )

    assert len(embeds) == 1
    assert embeds[0].description == "$0.0052 · 1 video · 720p · $1.50 today"
    assert embeds[0].colour == Colour.blue()


def test_append_flat_pricing_embed_prefixes_estimated_request_cost():
    embeds = []

    append_flat_pricing_embed(
        embeds,
        request_cost=0.00002,
        daily_cost=0.004,
        details=["1,234 chars", "alloy"],
        request_cost_is_estimate=True,
    )

    assert embeds[0].description == "est. <$0.0001 · 1,234 chars · alloy · <$0.01 today"


def test_append_flat_pricing_embed_shows_reported_token_counts():
    embeds = []

    append_flat_pricing_embed(
        embeds,
        request_cost=0.039,
        daily_cost=0.12,
        details=["1 image", "16:9"],
        usage=ChatUsage(prompt_tokens=12, completion_tokens=1_290, reasoning_tokens=200),
    )

    assert embeds[0].description == (
        "$0.0390 · 12 in / 1.3k out (200 thinking) · 1 image · 16:9 · $0.12 today"
    )


def test_append_flat_pricing_embed_leaves_out_unreported_token_counts():
    embeds = []

    append_flat_pricing_embed(
        embeds,
        request_cost=0.0008,
        daily_cost=0.02,
        details=["memo.m4a"],
        usage=ChatUsage(prompt_tokens=250),
    )

    assert embeds[0].description == "$0.0008 · 250 in · memo.m4a · $0.02 today"


def test_append_flat_pricing_embed_skips_empty_line():
    embeds = []

    append_flat_pricing_embed(embeds, request_cost=None, daily_cost=None)

    assert embeds == []


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


def test_build_model_list_embed_renders_every_live_output_modality(mixed_modality_models):
    embed = build_model_list_embed(list(mixed_modality_models.values()), query=None)

    assert embed.title == "Available Models"
    for label in (
        "out: text",
        "out: image, text",
        "out: image",
        "out: speech",
        "out: transcription",
        "out: video",
        "out: embeddings",
        "out: rerank",
        "out: text, audio",
    ):
        assert label in embed.description
    # `context_length: 0` (transcription/video/image-only entries) renders as unknown, not "0".
    assert (
        "`openai/gpt-transcribe`\nOpenAI: GPT Transcribe | ctx unknown | in: audio | out: transcription"
        in embed.description
    )
    assert len(embed.description) <= 4000

    filtered = build_model_list_embed(
        [mixed_modality_models["google/gemini-3.1-flash-tts-preview"]],
        query=None,
        output_modality="speech",
    )
    assert filtered.description.startswith("**Filters:** out=speech")
