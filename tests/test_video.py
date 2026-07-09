from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.client import OpenRouterApiError
from discord_openrouter.cogs.openrouter.video import (
    _build_pricing_details,
    _build_video_description,
    _coerce_str,
    _guess_video_extension,
    _is_image_attachment,
    _poll_until_complete,
    _safe_float_or_none,
    _validate_video_model_modalities,
    run_video_command,
)
from discord_openrouter.util import ModelInfo


def _make_attachment(
    *,
    filename: str = "ref.png",
    content_type: str | None = "image/png",
    size: int = 2048,
    url: str = "https://example.test/ref.png",
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
            create_video_generation=AsyncMock(),
            get_video_generation=AsyncMock(),
            download_file_bytes=AsyncMock(),
        ),
    )


class TestValidateVideoModelModalities:
    def test_blocks_missing_video_output(self):
        info = ModelInfo(id="t", name="t", input_modalities=["text"], output_modalities=["text"])
        error = _validate_video_model_modalities(info, requires_image_input=False)
        assert error is not None
        assert "video output" in error

    def test_blocks_missing_image_input_for_reference_videos(self):
        info = ModelInfo(id="v", name="v", input_modalities=["text"], output_modalities=["video"])
        error = _validate_video_model_modalities(info, requires_image_input=True)
        assert error is not None
        assert "image input" in error

    def test_allows_reference_image_video_models(self):
        info = ModelInfo(
            id="v",
            name="v",
            input_modalities=["text", "image"],
            output_modalities=["video"],
        )
        assert _validate_video_model_modalities(info, requires_image_input=True) is None

    def test_returns_none_when_model_unknown(self):
        assert _validate_video_model_modalities(None, requires_image_input=True) is None


class TestIsImageAttachment:
    def test_image_content_type_accepted(self):
        assert _is_image_attachment(_make_attachment(content_type="image/png")) is True

    def test_video_content_type_rejected(self):
        assert _is_image_attachment(_make_attachment(content_type="video/mp4")) is False

    def test_extension_fallback_when_no_content_type(self):
        assert _is_image_attachment(_make_attachment(content_type=None, filename="hero.webp"))


class TestBuildVideoDescription:
    def test_truncates_long_prompts(self):
        long_prompt = "x" * 2000
        out = _build_video_description(
            prompt=long_prompt,
            model="m",
            aspect_ratio=None,
            resolution=None,
            size=None,
            duration=None,
            generate_audio=None,
            seed=None,
            output_count=1,
            used_reference_image=False,
        )
        # Truncated to 1497 chars + ellipsis.
        prompt_line = next(line for line in out.splitlines() if line.startswith("**Prompt:**"))
        assert prompt_line.endswith("...")

    def test_text_to_video_when_no_reference(self):
        out = _build_video_description(
            prompt="x",
            model="m",
            aspect_ratio="16:9",
            resolution="1080p",
            size=None,
            duration=10,
            generate_audio=False,
            seed=42,
            output_count=2,
            used_reference_image=False,
        )
        assert "Text-to-Video" in out
        assert "**Aspect Ratio:** 16:9" in out
        assert "**Resolution:** 1080p" in out
        assert "**Duration:** 10 seconds" in out
        assert "**Audio:** Disabled" in out
        assert "**Seed:** 42" in out

    def test_image_to_video_when_reference_used(self):
        out = _build_video_description(
            prompt="x",
            model="m",
            aspect_ratio=None,
            resolution=None,
            size="1280x720",
            duration=None,
            generate_audio=True,
            seed=None,
            output_count=1,
            used_reference_image=True,
        )
        assert "Image-to-Video" in out
        assert "**Audio:** Enabled" in out
        assert "**Size:** 1280x720" in out


class TestBuildPricingDetails:
    def test_singular_output(self):
        out = _build_pricing_details(
            aspect_ratio="16:9", resolution="1080p", size=None, output_count=1
        )
        assert "1 output" in out and "1 outputs" not in out

    def test_plural_output(self):
        out = _build_pricing_details(aspect_ratio=None, resolution=None, size=None, output_count=3)
        assert "3 outputs" in out


class TestGuessVideoExtension:
    def test_uses_content_type_when_available(self):
        # mimetypes returns ".mp4" for "video/mp4"
        assert _guess_video_extension("https://x/y", "video/mp4") == "mp4"

    def test_falls_back_to_url_extension(self):
        assert _guess_video_extension("https://x/y/clip.mov", None) == "mov"

    def test_strips_codec_suffix_from_content_type(self):
        assert _guess_video_extension("https://x/y", "video/mp4; codecs=avc1") == "mp4"

    def test_default_when_neither_works(self):
        assert _guess_video_extension("https://x/no-ext", None) == "mp4"


class TestCoerceStr:
    def test_strips_whitespace(self):
        assert _coerce_str("  hello  ") == "hello"

    def test_returns_empty_string_for_non_strings(self):
        assert _coerce_str(None) == ""
        assert _coerce_str(42) == ""


class TestSafeFloatOrNone:
    def test_returns_none_for_none(self):
        assert _safe_float_or_none(None) is None

    def test_parses_numeric_string(self):
        assert _safe_float_or_none("3.14") == pytest.approx(3.14)

    def test_returns_none_for_invalid(self):
        assert _safe_float_or_none("not a number") is None


class TestPollUntilComplete:
    def test_returns_immediately_when_already_completed(self):
        cog = _make_cog()
        result = asyncio.run(
            _poll_until_complete(cog, job_id="j1", polling_url=None, initial_status="completed")
        )
        assert result["status"] == "completed"
        cog.openrouter_client.get_video_generation.assert_not_awaited()

    def test_raises_immediately_when_initial_status_is_failed(self):
        cog = _make_cog()
        with pytest.raises(OpenRouterApiError):
            asyncio.run(
                _poll_until_complete(cog, job_id="j1", polling_url=None, initial_status="failed")
            )

    def test_polls_until_completion(self):
        cog = _make_cog()
        cog.openrouter_client.get_video_generation = AsyncMock(
            return_value={"id": "j1", "status": "completed", "videos": [{"url": "x"}]}
        )
        with patch(
            "discord_openrouter.cogs.openrouter.video.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            result = asyncio.run(
                _poll_until_complete(
                    cog, job_id="j1", polling_url="https://poll", initial_status="pending"
                )
            )
        assert result["status"] == "completed"
        assert result["videos"][0]["url"] == "x"
        sleep.assert_awaited_once()

    def test_raises_when_polling_returns_failed_with_error(self):
        cog = _make_cog()
        cog.openrouter_client.get_video_generation = AsyncMock(
            return_value={"id": "j1", "status": "failed", "error": "model overloaded"}
        )
        with (
            patch("discord_openrouter.cogs.openrouter.video.asyncio.sleep", new=AsyncMock()),
            pytest.raises(OpenRouterApiError, match="model overloaded"),
        ):
            asyncio.run(
                _poll_until_complete(cog, job_id="j1", polling_url=None, initial_status="pending")
            )

    def test_raises_timeout_when_elapsed_exceeds_limit(self):
        cog = _make_cog()
        cog.openrouter_client.get_video_generation = AsyncMock(
            return_value={"id": "j1", "status": "pending"}
        )
        # Patch the timeout to a negative value so the very first elapsed-time check fires.
        # Patching time.monotonic instead is unsafe: asyncio's event loop probes the clock
        # internally during setup, so a call-counter-based stub yields surprising values.
        with (
            patch("discord_openrouter.cogs.openrouter.video.asyncio.sleep", new=AsyncMock()),
            patch("discord_openrouter.cogs.openrouter.video.VIDEO_GENERATION_TIMEOUT_SECONDS", -1),
            pytest.raises(TimeoutError),
        ):
            asyncio.run(
                _poll_until_complete(cog, job_id="j1", polling_url=None, initial_status="pending")
            )


class TestRunVideoCommand:
    def _run(self, cog, ctx, **kwargs):
        with patch(
            "discord_openrouter.cogs.openrouter.video.send_embed_batches", new=AsyncMock()
        ) as send:
            asyncio.run(run_video_command(cog, ctx=ctx, prompt="kwargs", **kwargs))
        return send

    def test_rejects_when_no_model_configured(self, monkeypatch):
        cog = _make_cog()
        ctx = _make_ctx()
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.video.OPENROUTER_DEFAULT_VIDEO_MODEL", ""
        )
        with patch("discord_openrouter.cogs.openrouter.video.error_embed") as error_embed_factory:
            self._run(cog, ctx)
        assert "No video model" in error_embed_factory.call_args.args[0]

    def test_rejects_when_size_combined_with_aspect_or_resolution(self):
        cog = _make_cog()
        ctx = _make_ctx()
        with patch("discord_openrouter.cogs.openrouter.video.error_embed") as error_embed_factory:
            self._run(cog, ctx, model="m", size="1280x720", aspect_ratio="16:9")
        assert "Use either" in error_embed_factory.call_args.args[0]

    def test_rejects_non_image_attachment(self):
        cog = _make_cog()
        ctx = _make_ctx()
        attachment = _make_attachment(filename="clip.mp4", content_type="video/mp4")
        with patch("discord_openrouter.cogs.openrouter.video.error_embed") as error_embed_factory:
            self._run(cog, ctx, model="m", attachment=attachment)
        assert "image attachments" in error_embed_factory.call_args.args[0]

    def test_propagates_api_error_during_get_model(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(side_effect=OpenRouterApiError("upstream auth"))
        with patch("discord_openrouter.cogs.openrouter.video.error_embed") as error_embed_factory:
            self._run(cog, ctx, model="m")
        error_embed_factory.assert_called_once_with("upstream auth")

    def test_rejects_when_model_modality_invalid(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=ModelInfo(id="t", name="t", output_modalities=["text"])
        )
        with patch("discord_openrouter.cogs.openrouter.video.error_embed") as error_embed_factory:
            self._run(cog, ctx, model="t")
        assert "video output" in error_embed_factory.call_args.args[0]
