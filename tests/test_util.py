import pytest

from discord_openrouter.util import (
    ChatSettings,
    ChatUsage,
    CostBreakdown,
    ModelInfo,
    ModelPricing,
    build_context_compression_plugins,
    build_prompt_cache_control,
    build_pdf_plugins,
    calculate_cost,
    calculate_cost_breakdown,
    describe_chat_settings,
    extract_url_citations,
    extract_reasoning_text,
    extract_usage,
    extract_web_search_requests,
    normalize_pdf_engine,
    parse_model_info,
    prompt_cache_supported_for_model,
)


def test_extract_reasoning_text_deduplicates_repeated_blocks():
    repeated = (
        "The user is saying 'hi dere' which is a casual, informal greeting. "
        "I should respond in a friendly, casual way."
    )

    reasoning_text = extract_reasoning_text(
        {
            "reasoning": repeated,
            "reasoning_details": [
                {"type": "reasoning.summary", "summary": repeated},
                {"type": "reasoning.text", "text": "Second unique thought."},
                {"type": "reasoning.text", "text": "Second unique thought."},
            ],
        }
    )

    assert reasoning_text == repeated + "\n\nSecond unique thought."


def test_extract_url_citations_deduplicates_urls_and_keeps_titles():
    citations = extract_url_citations(
        {
            "annotations": [
                {
                    "type": "url_citation",
                    "url_citation": {
                        "url": "https://openrouter.ai/docs",
                        "title": "OpenRouter Docs",
                        "content": "Official docs",
                    },
                },
                {
                    "type": "url_citation",
                    "url_citation": {
                        "url": "https://openrouter.ai/docs",
                        "title": "Duplicate",
                    },
                },
                {
                    "type": "url_citation",
                    "url_citation": {
                        "url": "https://github.com/openrouter",
                    },
                },
            ]
        }
    )

    assert citations == [
        {
            "url": "https://openrouter.ai/docs",
            "title": "OpenRouter Docs",
            "content": "Official docs",
        },
        {
            "url": "https://github.com/openrouter",
            "title": "https://github.com/openrouter",
            "content": "",
        },
    ]


def test_extract_usage_reads_openrouter_cost_cache_and_server_tools():
    usage = extract_usage(
        {
            "usage": {
                "prompt_tokens": 132,
                "completion_tokens": 263,
                "total_tokens": 395,
                "cost": 0.0032,
                "cost_details": {
                    "upstream_inference_cost": 19,
                },
                "prompt_tokens_details": {
                    "cached_tokens": 64,
                    "cache_write_tokens": 32,
                    "audio_tokens": 16,
                    "video_tokens": 8,
                },
                "completion_tokens_details": {
                    "reasoning_tokens": 239,
                    "audio_tokens": 12,
                    "image_tokens": 4,
                },
                "server_tool_use": {
                    "web_search": 2,
                    "ignored_zero": 0,
                },
            }
        }
    )

    assert usage.prompt_tokens == 132
    assert usage.completion_tokens == 263
    assert usage.total_tokens == 395
    assert usage.cached_tokens == 64
    assert usage.cache_write_tokens == 32
    assert usage.input_audio_tokens == 16
    assert usage.input_video_tokens == 8
    assert usage.reasoning_tokens == 239
    assert usage.output_audio_tokens == 12
    assert usage.output_image_tokens == 4
    assert usage.cost == 0.0032
    assert usage.upstream_inference_cost == 19.0
    assert usage.server_tool_use == {"web_search": 2}


def test_extract_web_search_requests_supports_both_known_keys():
    assert extract_web_search_requests({"web_search": 2}) == 2
    assert extract_web_search_requests({"web_search_requests": 3}) == 3
    assert extract_web_search_requests({"web_search": 2, "web_search_requests": 3}) == 3


def test_parse_model_info_reads_extended_pricing_fields():
    model = parse_model_info(
        {
            "id": "openai/gpt-5.2",
            "name": "GPT-5.2",
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.1",
                "completion": "0.2",
                "request": "0.3",
                "image": "0.4",
                "audio": "0.5",
                "web_search": "0.6",
                "internal_reasoning": "0.7",
                "input_cache_read": "0.8",
                "input_cache_write": "0.9",
            },
        }
    )

    assert model.pricing.prompt == 0.1
    assert model.pricing.completion == 0.2
    assert model.pricing.request == 0.3
    assert model.pricing.image == 0.4
    assert model.pricing.audio == 0.5
    assert model.pricing.web_search == 0.6
    assert model.pricing.internal_reasoning == 0.7
    assert model.pricing.input_cache_read == 0.8
    assert model.pricing.input_cache_write == 0.9


def test_calculate_cost_accounts_for_reasoning_cache_and_web_search_pricing():
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
    usage = ChatUsage(
        prompt_tokens=100,
        completion_tokens=50,
        reasoning_tokens=20,
        cached_tokens=30,
        cache_write_tokens=10,
        server_tool_use={"web_search_requests": 2},
    )

    assert calculate_cost(model_info, usage) == 427.0


def test_calculate_cost_breakdown_returns_component_totals():
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
    usage = ChatUsage(
        prompt_tokens=100,
        completion_tokens=50,
        reasoning_tokens=20,
        cached_tokens=30,
        cache_write_tokens=10,
        server_tool_use={"web_search_requests": 2},
    )

    assert calculate_cost_breakdown(model_info, usage) == CostBreakdown(
        input=60.0,
        cache_read=150.0,
        cache_write=60.0,
        output=60.0,
        reasoning=80.0,
        request=3.0,
        web_search=14.0,
    )


def test_normalize_pdf_engine_aliases_deprecated_values():
    assert normalize_pdf_engine("pdf-text") == "cloudflare-ai"
    assert normalize_pdf_engine(" CLOUDFlare-AI ") == "cloudflare-ai"


def test_normalize_pdf_engine_rejects_invalid_values():
    with pytest.raises(ValueError, match="Unsupported PDF engine"):
        normalize_pdf_engine("not-real")


def test_build_pdf_plugins_returns_expected_payload():
    assert build_pdf_plugins("pdf-text") == [
        {
            "id": "file-parser",
            "pdf": {
                "engine": "cloudflare-ai",
            },
        }
    ]
    assert build_pdf_plugins(None) is None


def test_build_context_compression_plugins_returns_expected_payload():
    assert build_context_compression_plugins(True) == [{"id": "context-compression"}]
    assert build_context_compression_plugins(False) == [{"id": "context-compression", "enabled": False}]
    assert build_context_compression_plugins(None) is None


def test_build_prompt_cache_control_supports_documented_ttls():
    assert build_prompt_cache_control("5m") == {"type": "ephemeral"}
    assert build_prompt_cache_control("1h") == {"type": "ephemeral", "ttl": "1h"}
    assert build_prompt_cache_control(None) is None


def test_build_prompt_cache_control_rejects_invalid_values():
    with pytest.raises(ValueError, match="Unsupported prompt cache TTL"):
        build_prompt_cache_control("24h")


def test_prompt_cache_supported_for_model_matches_current_explicit_scope():
    assert prompt_cache_supported_for_model("anthropic/claude-sonnet-4.5") is True
    assert prompt_cache_supported_for_model("openai/gpt-5.2") is False


def test_describe_chat_settings_summarizes_active_options():
    summary = describe_chat_settings(
        ChatSettings(
            model="anthropic/claude-sonnet-4.5",
            pdf_engine="mistral-ocr",
            context_compression=False,
            prompt_cache_ttl="1h",
            web_search=True,
            reasoning_max_tokens=2048,
            exclude_reasoning=True,
        )
    )

    assert summary == (
        "pdf `mistral-ocr`, context compression off, prompt cache `1h`, "
        "web search, reasoning `2048` tokens, hidden reasoning"
    )
