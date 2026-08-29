from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter import chat
from discord_openrouter.cogs.openrouter.attachments import AttachmentRequirements
from discord_openrouter.cogs.openrouter.chat import (
    _append_image_embeds,
    _build_request_plugins,
    _build_request_tools,
    _run_conversation_turn,
    _validate_model_input_modalities,
    _validate_prompt_cache_request,
    run_chat_command,
    validate_model_output_modalities,
)
from discord_openrouter.util import ChatSettings, Conversation, ModelInfo


def test_validate_model_input_modalities_blocks_missing_modalities():
    model_info = ModelInfo(
        id="openai/text-only",
        name="Text Only",
        input_modalities=["text", "image"],
        output_modalities=["text"],
    )

    error = _validate_model_input_modalities(
        model_info,
        AttachmentRequirements(required_input_modalities=frozenset({"audio", "image"})),
    )

    assert error is not None
    assert "`audio`" in error
    assert "`image`" not in error


def test_validate_model_input_modalities_allows_pdf_without_file_support():
    model_info = ModelInfo(
        id="openai/text-only",
        name="Text Only",
        input_modalities=["text"],
        output_modalities=["text"],
    )

    error = _validate_model_input_modalities(
        model_info,
        AttachmentRequirements(required_input_modalities=frozenset(), has_pdf=True),
    )

    assert error is None


@pytest.mark.parametrize(
    "model_id",
    [
        "bytedance-seed/seedream-5-0-pro",
        "google/gemini-3.1-flash-tts-preview",
        "openai/gpt-transcribe",
        "alibaba/happyhorse-1.1",
        "google/gemini-embedding-2",
        "cohere/rerank-4-pro",
    ],
)
def test_validate_model_output_modalities_blocks_live_non_text_models(
    mixed_modality_models, model_id
):
    error = validate_model_output_modalities(mixed_modality_models[model_id])

    assert error is not None
    assert f"`{model_id}` does not advertise text output" in error
    outputs = {m.casefold() for m in mixed_modality_models[model_id].output_modalities}
    hints = {
        hint
        for modality, hint in {
            "image": "/openrouter-media image",
            "video": "/openrouter-media video",
            "speech": "/openrouter-tools tts",
            "audio": "/openrouter-tools tts",
        }.items()
        if modality in outputs
    }
    if hints:
        assert all(hint in error for hint in hints)
    else:
        assert "no command that drives that output type" in error


def test_validate_model_output_modalities_allows_text_capable_and_unknown_models(
    mixed_modality_models,
):
    for model_id in (
        "deepseek/deepseek-v4-flash",
        "anthropic/claude-sonnet-4.5",
        "google/gemini-3.1-flash-image",
        "openai/gpt-audio",
    ):
        assert validate_model_output_modalities(mixed_modality_models[model_id]) is None
    assert validate_model_output_modalities(None) is None
    # No advertised outputs means the catalog did not say; treated as text like describe_modalities.
    assert validate_model_output_modalities(ModelInfo(id="x", name="x")) is None


@pytest.mark.asyncio
async def test_run_chat_command_rejects_non_text_output_model_before_request(
    mixed_modality_models,
):
    """A fuzzy `model` query that lands on a TTS entry fails pre-flight, not at OpenRouter."""
    cog = SimpleNamespace(
        logger=MagicMock(),
        conversation_histories={},
        channel_model_defaults={},
        openrouter_client=SimpleNamespace(
            get_model=AsyncMock(
                return_value=mixed_modality_models["google/gemini-3.1-flash-tts-preview"]
            ),
            create_chat_completion=AsyncMock(),
        ),
    )
    user = SimpleNamespace(id=7)
    ctx = SimpleNamespace(
        channel=SimpleNamespace(id=100),
        user=user,
        author=user,
        defer=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
        interaction=SimpleNamespace(id=555),
    )

    with (
        patch.object(chat, "send_embed_batches", new=AsyncMock()) as send,
        patch.object(chat, "error_embed") as error_embed_factory,
    ):
        await run_chat_command(cog, ctx=ctx, prompt="hello", model="gemini tts")

    message = error_embed_factory.call_args.args[0]
    assert "`google/gemini-3.1-flash-tts-preview` does not advertise text output" in message
    assert "/openrouter-tools tts" in message
    send.assert_awaited_once()
    cog.openrouter_client.create_chat_completion.assert_not_awaited()
    assert cog.conversation_histories == {}


def test_build_request_plugins_adds_pdf_parser_for_pdf_turns():
    plugins = _build_request_plugins(
        attachment_requirements=AttachmentRequirements(has_pdf=True),
        pdf_engine="pdf-text",
        context_compression=None,
    )

    assert plugins == [
        {"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}},
        {"id": "web", "enabled": False},
    ]
    assert _build_request_plugins(
        attachment_requirements=AttachmentRequirements(has_pdf=False),
        pdf_engine="cloudflare-ai",
        context_compression=None,
    ) == [{"id": "web", "enabled": False}]


def test_build_request_plugins_disables_deprecated_web_plugin_even_when_web_search_is_enabled():
    plugins = _build_request_plugins(
        attachment_requirements=AttachmentRequirements(has_pdf=True),
        pdf_engine="cloudflare-ai",
        context_compression=None,
    )

    assert plugins == [
        {"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}},
        {"id": "web", "enabled": False},
    ]


def test_build_request_plugins_can_include_context_compression():
    plugins = _build_request_plugins(
        attachment_requirements=AttachmentRequirements(has_pdf=False),
        pdf_engine=None,
        context_compression=True,
    )

    assert plugins == [
        {"id": "context-compression"},
        {"id": "web", "enabled": False},
    ]


def test_build_request_plugins_can_explicitly_disable_context_compression():
    plugins = _build_request_plugins(
        attachment_requirements=AttachmentRequirements(has_pdf=False),
        pdf_engine=None,
        context_compression=False,
    )

    assert plugins == [
        {"id": "context-compression", "enabled": False},
        {"id": "web", "enabled": False},
    ]


def test_build_request_tools_uses_openrouter_web_search_server_tool():
    assert _build_request_tools(web_search=True) == [{"type": "openrouter:web_search"}]
    assert _build_request_tools(web_search=False) is None


def test_build_request_tools_can_include_multiple_openrouter_server_tools():
    assert _build_request_tools(web_search=True, datetime=True) == [
        {"type": "openrouter:web_search"},
        {"type": "openrouter:datetime"},
    ]


def test_validate_prompt_cache_request_limits_explicit_cache_control_to_anthropic():
    assert (
        _validate_prompt_cache_request(model="anthropic/claude-sonnet-4.5", prompt_cache_ttl="1h")
        is None
    )

    error = _validate_prompt_cache_request(model="openai/gpt-5.2", prompt_cache_ttl="1h")

    assert error is not None
    assert "Anthropic" in error


def test_append_image_embeds_uses_attached_filenames_for_preview():
    embeds = []

    _append_image_embeds(
        embeds,
        {
            "images": [
                {"image_url": {"url": "https://cdn.example/generated.png"}},
            ]
        },
        attached_filenames=["chat_image_1.png"],
    )

    assert len(embeds) == 1
    assert embeds[0].image.url == "attachment://chat_image_1.png"


@pytest.mark.asyncio
async def test_run_conversation_turn_splits_long_embed_response_without_plain_text_fallback(
    monkeypatch,
):
    async def keep_typing_until_cancelled(_channel):
        import asyncio

        await asyncio.sleep(60)

    monkeypatch.setattr(chat, "keep_typing", keep_typing_until_cancelled)

    response_text = "x" * 7000
    response_payload = {
        "id": "response-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": response_text,
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "url": "https://example.com/source",
                                "title": "Example Source",
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
    model_info = ModelInfo(id="openai/test", name="Test", input_modalities=["text"])
    cog = SimpleNamespace(
        logger=MagicMock(),
        openrouter_client=SimpleNamespace(
            create_chat_completion=AsyncMock(return_value=response_payload),
            get_model=AsyncMock(return_value=model_info),
        ),
        _strip_previous_view=AsyncMock(),
        _create_button_view=MagicMock(return_value=object()),
        daily_costs={},
        views={},
        last_view_messages={},
    )
    conversation = Conversation(
        conversation_id=123,
        conversation_starter_id=456,
        channel_id=789,
        settings=ChatSettings(model="openai/test"),
    )
    conversation.append_user_message({"role": "user", "content": "hello"})
    send_reply = AsyncMock(side_effect=["first", "second"])

    success = await _run_conversation_turn(
        cog,
        conversation=conversation,
        send_reply=send_reply,
        user_id=456,
        channel=SimpleNamespace(),
    )

    assert success is True
    assert send_reply.await_count == 2
    assert all("content" not in call.kwargs for call in send_reply.await_args_list)
    assert not any(
        str(call.kwargs.get("content", "")).startswith("**Response:**")
        for call in send_reply.await_args_list
    )
    assert "view" not in send_reply.await_args_list[0].kwargs
    assert "view" in send_reply.await_args_list[-1].kwargs
