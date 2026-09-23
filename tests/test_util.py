import pytest

from discord_openrouter.util import (
    ChatSettings,
    ChatUsage,
    CostBreakdown,
    ModelInfo,
    ModelPricing,
    build_context_compression_plugins,
    build_datetime_tools,
    build_pdf_plugins,
    build_prompt_cache_control,
    build_web_plugin_override,
    build_web_search_tools,
    calculate_cost,
    calculate_cost_breakdown,
    describe_chat_settings,
    describe_modalities,
    extract_reasoning_text,
    extract_url_citations,
    extract_usage,
    extract_web_search_requests,
    normalize_pdf_engine,
    parse_model_info,
    prompt_cache_supported_for_model,
    resolve_request_cost,
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
    assert usage.is_byok is False
    assert usage.upstream_inference_cost == 19.0
    assert usage.server_tool_use == {"web_search": 2}


def test_extract_usage_reads_server_tool_use_details():
    usage = extract_usage(
        {
            "usage": {
                "prompt_tokens": 5012,
                "completion_tokens": 310,
                "server_tool_use_details": {
                    "web_search_requests": 2,
                    "tool_calls_requested": 2,
                    "tool_calls_executed": 2,
                },
            }
        }
    )

    assert usage.server_tool_use == {
        "web_search_requests": 2,
        "tool_calls_requested": 2,
        "tool_calls_executed": 2,
    }
    assert extract_web_search_requests(usage.server_tool_use) == 2


def test_extract_usage_prefers_server_tool_use_details_over_server_tool_use():
    usage = extract_usage(
        {
            "usage": {
                "server_tool_use_details": {"web_search_requests": 3},
                "server_tool_use": {"web_search": 1},
            }
        }
    )

    assert usage.server_tool_use == {"web_search_requests": 3}


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


def test_parse_model_info_handles_every_live_output_modality(mixed_modality_catalog):
    """Every entry shape from the enlarged catalog parses and prices cleanly.

    Both listing calls pass ``output_modalities=all`` (``MODEL_LIST_PARAMS``), so
    image-only, speech, transcription, video, embeddings, and rerank entries reach
    this parser for the first time. The fixture holds one real entry per shape.
    """
    parsed = {entry["id"]: parse_model_info(entry) for entry in mixed_modality_catalog}

    assert {tuple(model.output_modalities) for model in parsed.values()} == {
        ("text",),
        ("image", "text"),
        ("image",),
        ("speech",),
        ("transcription",),
        ("video",),
        ("embeddings",),
        ("rerank",),
        ("text", "audio"),
    }
    for model in parsed.values():
        assert model.id and model.name and model.canonical_slug and model.description
        assert calculate_cost(model, ChatUsage(prompt_tokens=10, completion_tokens=5)) is not None
        assert describe_modalities(model).startswith("in: ")

    # Media entries price per image/request under keys the parser does not model
    # (`image_token`, `image_output`, `audio_output`) while `prompt`/`completion` are "0".
    seedream = parsed["bytedance-seed/seedream-5-0-pro"]
    assert (seedream.pricing.prompt, seedream.pricing.completion, seedream.pricing.image) == (
        0.0,
        0.0,
        0.003,
    )
    assert seedream.context_length == 0
    assert parsed["alibaba/happyhorse-1.1"].pricing == ModelPricing()
    assert parsed["openai/gpt-transcribe"].input_modalities == ["audio"]
    assert parsed["openai/gpt-transcribe"].pricing.prompt == 0.0045
    assert parsed["google/gemini-embedding-2"].pricing.audio == 0.0000065
    assert parsed["openai/gpt-audio"].pricing.audio == 0.000032
    # The tiered `overrides` array is ignored rather than fatal.
    assert parsed["anthropic/claude-sonnet-4.5"].pricing.input_cache_write == 0.00000375


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


@pytest.mark.parametrize(
    ("byok_field", "expected_is_byok", "expected_cost"),
    [
        ({"is_byok": True}, True, 0.0105),
        ({"is_byok": False}, False, 0.0005),
        ({}, False, 0.0005),
    ],
    ids=["byok", "not-byok", "byok-missing"],
)
def test_resolve_request_cost_adds_upstream_cost_only_for_byok(
    byok_field, expected_is_byok, expected_cost
):
    usage = extract_usage(
        {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost": 0.0005,
                "cost_details": {"upstream_inference_cost": 0.01},
                **byok_field,
            }
        }
    )

    assert usage.is_byok is expected_is_byok
    assert resolve_request_cost(usage) == pytest.approx(expected_cost)


def test_resolve_request_cost_uses_cost_when_byok_has_no_upstream_cost():
    usage = ChatUsage(cost=0.0005, is_byok=True)

    assert resolve_request_cost(usage) == 0.0005


def test_resolve_request_cost_returns_none_without_reported_cost():
    usage = ChatUsage(is_byok=True, upstream_inference_cost=0.01)

    assert resolve_request_cost(usage) is None


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


def test_build_web_plugin_override_returns_expected_payload():
    assert build_web_plugin_override(enabled=False) == [{"id": "web", "enabled": False}]


def test_build_web_search_tools_returns_expected_payload():
    assert build_web_search_tools() == [{"type": "openrouter:web_search"}]


def test_build_datetime_tools_returns_expected_payload():
    assert build_datetime_tools() == [{"type": "openrouter:datetime"}]


def test_build_context_compression_plugins_returns_expected_payload():
    assert build_context_compression_plugins(True) == [{"id": "context-compression"}]
    assert build_context_compression_plugins(False) == [
        {"id": "context-compression", "enabled": False}
    ]
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
            datetime=True,
            reasoning_effort="high",
        )
    )

    assert summary == (
        "pdf `mistral-ocr`, context compression off, prompt cache `1h`, "
        "web search, datetime, reasoning `high`"
    )
