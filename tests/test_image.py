from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.client import OpenRouterApiError
from discord_openrouter.cogs.openrouter.image import (
    _build_image_config,
    _build_image_description,
    _build_pricing_details,
    _decode_data_url,
    _guess_extension_from_url,
    _is_image_attachment,
    _resolve_image_modalities,
    _validate_image_model_modalities,
    build_image_assets,
    build_image_files,
    run_image_command,
)
from discord_openrouter.util import ModelInfo


def _make_attachment(
    *,
    filename: str = "photo.png",
    content_type: str | None = "image/png",
    size: int = 2048,
    url: str = "https://example.test/photo.png",
) -> SimpleNamespace:
    return SimpleNamespace(filename=filename, content_type=content_type, size=size, url=url)


def _make_ctx() -> SimpleNamespace:
    user = SimpleNamespace(id=42)
    return SimpleNamespace(
        channel=SimpleNamespace(id=900),
        user=user,
        author=user,
        defer=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
        interaction=SimpleNamespace(id=999),
    )


def _make_cog() -> SimpleNamespace:
    return SimpleNamespace(
        logger=MagicMock(),
        channel_model_defaults={},
        daily_costs={},
        openrouter_client=SimpleNamespace(
            get_model=AsyncMock(return_value=None),
            create_chat_completion=AsyncMock(),
        ),
    )


class TestValidateImageModelModalities:
    def test_blocks_missing_image_output(self):
        info = ModelInfo(id="openai/text-only", name="Text Only", output_modalities=["text"])
        error = _validate_image_model_modalities(info, requires_image_input=False)
        assert error is not None
        assert "image output" in error

    def test_blocks_missing_image_input_for_edits(self):
        info = ModelInfo(
            id="openai/image-output-only",
            name="Image Output Only",
            input_modalities=["text"],
            output_modalities=["image"],
        )
        error = _validate_image_model_modalities(info, requires_image_input=True)
        assert error is not None
        assert "image input" in error

    def test_allows_image_editing_models(self):
        info = ModelInfo(
            id="openai/image-editor",
            name="Image Editor",
            input_modalities=["text", "image"],
            output_modalities=["image", "text"],
        )
        assert _validate_image_model_modalities(info, requires_image_input=True) is None

    def test_returns_none_for_unknown_model(self):
        # When the catalog doesn't know about the model, we trust the user.
        assert _validate_image_model_modalities(None, requires_image_input=True) is None


class TestIsImageAttachment:
    @pytest.mark.parametrize("content_type", ["image/png", "image/jpeg", "image/webp"])
    def test_image_content_type_accepted(self, content_type: str):
        assert _is_image_attachment(_make_attachment(content_type=content_type)) is True

    def test_text_content_type_rejected(self):
        assert _is_image_attachment(_make_attachment(content_type="text/plain")) is False

    def test_falls_back_to_extension_when_content_type_blank(self):
        assert _is_image_attachment(_make_attachment(content_type=None, filename="photo.JPG"))
        assert not _is_image_attachment(_make_attachment(content_type="", filename="photo.txt"))

    def test_strips_codec_suffix_from_content_type(self):
        assert _is_image_attachment(_make_attachment(content_type="image/png; codecs=1"))


class TestResolveImageModalities:
    def test_image_only_when_text_unsupported(self):
        info = ModelInfo(id="m", name="m", output_modalities=["image"])
        assert _resolve_image_modalities(info) == ["image"]

    def test_image_and_text_when_text_supported(self):
        info = ModelInfo(id="m", name="m", output_modalities=["image", "text"])
        assert _resolve_image_modalities(info) == ["image", "text"]

    def test_image_and_text_when_model_unknown(self):
        assert _resolve_image_modalities(None) == ["image", "text"]


class TestBuildImageConfig:
    def test_empty_when_neither_provided(self):
        assert _build_image_config(aspect_ratio=None, image_size=None) == {}

    def test_includes_only_provided_fields(self):
        assert _build_image_config(aspect_ratio="16:9", image_size=None) == {"aspect_ratio": "16:9"}
        assert _build_image_config(aspect_ratio=None, image_size="1024x1024") == {
            "image_size": "1024x1024"
        }

    def test_includes_both_when_provided(self):
        assert _build_image_config(aspect_ratio="1:1", image_size="512x512") == {
            "aspect_ratio": "1:1",
            "image_size": "512x512",
        }


class TestBuildImageDescription:
    def test_includes_required_fields(self):
        out = _build_image_description(
            prompt="A cat",
            model="openai/dall-e-3",
            mode="Image Generation",
            aspect_ratio=None,
            image_size=None,
            response_text="",
        )
        assert "A cat" in out
        assert "openai/dall-e-3" in out
        assert "Image Generation" in out
        assert "Aspect Ratio" not in out

    def test_includes_optional_fields_when_provided(self):
        out = _build_image_description(
            prompt="A cat",
            model="m",
            mode="Image Editing",
            aspect_ratio="1:1",
            image_size="1024x1024",
            response_text="extra notes",
        )
        assert "**Aspect Ratio:** 1:1" in out
        assert "**Image Size:** 1024x1024" in out
        assert "**Notes:** extra notes" in out


class TestBuildPricingDetails:
    def test_collects_provided_fields(self):
        details = _build_pricing_details(
            mode="Image Generation", aspect_ratio="16:9", image_size="1024x1024"
        )
        assert "image generation" in details
        assert "16:9" in details
        assert "1024x1024" in details

    def test_excludes_unset_fields(self):
        assert _build_pricing_details(mode="Image Editing", aspect_ratio=None, image_size=None) == (
            "image editing"
        )


class TestGuessExtensionFromUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://x.test/photo.png", "png"),
            ("https://x.test/Photo.JPG?token=abc", "jpg"),
            ("https://x.test/cat.webp", "webp"),
            ("https://x.test/anim.gif?cachebust=1", "gif"),
        ],
    )
    def test_recognizes_common_extensions(self, url: str, expected: str):
        assert _guess_extension_from_url(url) == expected

    def test_falls_back_to_png_when_no_extension_match(self):
        assert _guess_extension_from_url("https://x.test/raw") == "png"


class TestDecodeDataUrl:
    def test_extracts_bytes_and_extension(self):
        payload = base64.b64encode(b"hello").decode("ascii")
        data_url = f"data:image/jpeg;base64,{payload}"
        body, extension = _decode_data_url(data_url)
        assert body == b"hello"
        assert extension == "jpeg"

    def test_handles_unknown_mime_with_safe_default(self):
        # Without a slash in mime, helpers default to "png".
        payload = base64.b64encode(b"x").decode("ascii")
        body, extension = _decode_data_url(f"data:something;base64,{payload}")
        assert body == b"x"
        assert extension == "png"


class TestBuildImageFiles:
    def test_creates_one_file_per_asset(self):
        files = build_image_files([("a.png", b"123"), ("b.png", b"456")])
        assert [file.filename for file in files] == ["a.png", "b.png"]


class TestBuildImageAssets:
    def test_decodes_data_urls_without_network(self):
        payload = base64.b64encode(b"data-bytes").decode("ascii")
        images = [{"image_url": {"url": f"data:image/png;base64,{payload}"}}]
        result = asyncio.run(build_image_assets(images))
        assert result == [("image_1.png", b"data-bytes")]

    def test_skips_entries_without_url(self):
        images = [{"image_url": {}}, {}, {"url": ""}]
        assert asyncio.run(build_image_assets(images)) == []


class TestRunImageCommand:
    def _run(self, cog, ctx, **kwargs):
        with patch(
            "discord_openrouter.cogs.openrouter.image.send_embed_batches", new=AsyncMock()
        ) as send:
            asyncio.run(run_image_command(cog, ctx=ctx, **kwargs))
        return send

    def test_rejects_when_no_model_configured(self, monkeypatch):
        cog = _make_cog()
        ctx = _make_ctx()
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.image.OPENROUTER_DEFAULT_IMAGE_MODEL", ""
        )
        with patch("discord_openrouter.cogs.openrouter.image.error_embed") as error_embed_factory:
            self._run(cog, ctx, prompt="a cat")
        assert "No image model" in error_embed_factory.call_args.args[0]

    def test_rejects_non_image_attachment(self):
        cog = _make_cog()
        ctx = _make_ctx()
        attachment = _make_attachment(filename="doc.pdf", content_type="application/pdf")
        with patch("discord_openrouter.cogs.openrouter.image.error_embed") as error_embed_factory:
            self._run(cog, ctx, prompt="edit this", model="openai/dall-e-3", attachment=attachment)
        assert "Only image attachments" in error_embed_factory.call_args.args[0]

    def test_rejects_when_modality_validation_fails(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=ModelInfo(id="t", name="t", output_modalities=["text"])
        )
        with patch("discord_openrouter.cogs.openrouter.image.error_embed") as error_embed_factory:
            self._run(cog, ctx, prompt="a cat", model="t")
        assert "image output" in error_embed_factory.call_args.args[0]

    def test_propagates_api_error_during_get_model(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            side_effect=OpenRouterApiError("upstream timeout")
        )
        with patch("discord_openrouter.cogs.openrouter.image.error_embed") as error_embed_factory:
            self._run(cog, ctx, prompt="a cat", model="any/model")
        error_embed_factory.assert_called_once_with("upstream timeout")
