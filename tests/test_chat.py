import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.attachments import AttachmentRequirements
from discord_openrouter.cogs.openrouter.chat import (
    _append_image_embeds,
    _build_request_plugins,
    _build_request_tools,
    _validate_prompt_cache_request,
    _validate_model_input_modalities,
)
from discord_openrouter.util import ModelInfo


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
    assert (
        _build_request_plugins(
            attachment_requirements=AttachmentRequirements(has_pdf=False),
            pdf_engine="cloudflare-ai",
            context_compression=None,
        )
        == [{"id": "web", "enabled": False}]
    )


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
    assert _validate_prompt_cache_request(model="anthropic/claude-sonnet-4.5", prompt_cache_ttl="1h") is None

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
