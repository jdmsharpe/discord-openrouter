import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.embeds import append_usage_embed
from discord_openrouter.util import ChatUsage, ModelInfo, ModelPricing


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
    assert description.startswith("$0.0500")
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
    assert "input $60.0000" in lines[1]
    assert "cache read $150.0000" in lines[1]
    assert "cache write $60.0000" in lines[1]
    assert "output $60.0000" in lines[1]
    assert "reasoning $80.0000" in lines[1]
    assert "request $3.0000" in lines[1]
    assert "search $14.0000" in lines[1]
    assert "upstream $19.0000" in lines[1]
