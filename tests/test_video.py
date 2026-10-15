from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")

from discord import Colour, Embed, HTTPException

from discord_openrouter.cogs.openrouter.client import OpenRouterApiError
from discord_openrouter.cogs.openrouter.embed_delivery import (
    DISCORD_EMBED_TOTAL_LIMIT,
    DISCORD_EMBEDS_PER_MESSAGE_LIMIT,
    count_embed_chars,
)
from discord_openrouter.cogs.openrouter.video import (
    EMBED_FIELD_VALUE_LIMIT,
    _add_download_fields,
    _build_download_fields,
    _build_pricing_details,
    _build_video_description,
    _coerce_str,
    _guess_video_extension,
    _is_image_attachment,
    _poll_until_complete,
    _validate_video_model_modalities,
    run_video_command,
)
from discord_openrouter.config import DEFAULT_VIDEO_MODEL
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

    def test_default_video_model_from_live_catalog_passes(self, mixed_modality_models):
        # Video-only entries reach the catalog now that listing passes output_modalities=all,
        # so this check runs for real against the default model's entry.
        assert DEFAULT_VIDEO_MODEL in mixed_modality_models
        info = mixed_modality_models[DEFAULT_VIDEO_MODEL]
        assert _validate_video_model_modalities(info, requires_image_input=False) is None
        assert _validate_video_model_modalities(info, requires_image_input=True) is None

    @pytest.mark.parametrize(
        "model_id",
        [
            "deepseek/deepseek-v4-flash",
            "bytedance-seed/seedream-5-0-pro",
            "google/gemini-3.8-flash-tts",
        ],
    )
    def test_blocks_live_models_without_video_output(self, mixed_modality_models, model_id):
        error = _validate_video_model_modalities(
            mixed_modality_models[model_id], requires_image_input=False
        )
        assert error is not None
        assert f"`{model_id}` does not advertise video output" in error


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
        assert out == ["1 video", "1080p", "16:9"]

    def test_plural_output(self):
        out = _build_pricing_details(aspect_ratio=None, resolution=None, size=None, output_count=3)
        assert out == ["3 videos"]

    def test_includes_size(self):
        out = _build_pricing_details(
            aspect_ratio=None, resolution=None, size="1280x720", output_count=1
        )
        assert out == ["1 video", "1280x720"]


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


_SIGNED_URL = (
    "https://storage.example.test/videos/job-1/0?X-Goog-Expires=86386&X-Goog-Signature=abc"
)
_UNSIGNED_URL = "https://openrouter.ai/api/v1/videos/job-1/content?index=0"
_EXPIRES_AT = 1791400000
_SIGNED_NOTE = (
    "Anyone who can see this message can download the video from these links "
    f"until <t:{_EXPIRES_AT}:f> (<t:{_EXPIRES_AT}:R>)."
)
_UNSIGNED_NOTE = (
    "These links need an OpenRouter API key in the `Authorization` header; "
    "opening one in a browser returns an authentication error."
)


class TestBuildDownloadFields:
    def test_signed_urls_are_posted_with_the_expiry_as_a_discord_timestamp(self):
        fields = _build_download_fields(
            {
                "urls": [_SIGNED_URL],
                "unsigned_urls": [_UNSIGNED_URL],
                "expires_at": _EXPIRES_AT,
            }
        )
        assert fields == [
            ("Downloads", f"1. [Video 1]({_SIGNED_URL})"),
            ("Link access", _SIGNED_NOTE),
        ]

    def test_signed_urls_without_expiry_say_the_links_end_with_the_stored_copy(self):
        fields = _build_download_fields({"urls": [_SIGNED_URL], "expires_at": "soon"})
        assert fields == [
            ("Downloads", f"1. [Video 1]({_SIGNED_URL})"),
            (
                "Link access",
                "Anyone who can see this message can download the video from these links "
                "until OpenRouter deletes its stored copy.",
            ),
        ]

    @pytest.mark.parametrize("urls", [None, [], ["", "  "]], ids=["absent", "empty", "blank"])
    def test_unsigned_urls_are_posted_only_when_signed_urls_are_absent(self, urls):
        status_response = {"unsigned_urls": [_UNSIGNED_URL], "expires_at": _EXPIRES_AT}
        if urls is not None:
            status_response["urls"] = urls
        assert _build_download_fields(status_response) == [
            ("Downloads", f"1. [Video 1]({_UNSIGNED_URL})"),
            ("Link access", _UNSIGNED_NOTE),
        ]

    @pytest.mark.parametrize(
        "expires_at", [None, "1791400000", True, 0, -5, float("inf"), float("nan")]
    )
    def test_unusable_expiry_values_fall_back_to_the_stored_copy_note(self, expires_at):
        [_, (name, note)] = _build_download_fields(
            {"urls": [_SIGNED_URL], "expires_at": expires_at}
        )
        assert name == "Link access"
        assert note.endswith("until OpenRouter deletes its stored copy.")

    def test_float_expiry_is_shown_as_whole_seconds(self):
        [_, (_, note)] = _build_download_fields(
            {"urls": [_SIGNED_URL], "expires_at": _EXPIRES_AT + 0.75}
        )
        assert note == _SIGNED_NOTE

    def test_no_fields_without_any_url(self):
        assert _build_download_fields({"urls": [], "unsigned_urls": []}) == []
        assert _build_download_fields({}) == []

    def test_long_signed_urls_are_split_across_fields_that_fit_discord_limits(self):
        # Live signed URLs are Google Cloud Storage V4 signed URLs of about 900 characters.
        long_urls = [
            f"https://storage.example.test/{index}?X-Goog-Signature={'f' * 850}"
            for index in range(3)
        ]
        fields = _build_download_fields({"urls": long_urls, "expires_at": _EXPIRES_AT})
        assert [name for name, _ in fields] == [
            "Downloads",
            "Downloads (continued)",
            "Downloads (continued)",
            "Link access",
        ]
        assert all(len(value) <= EMBED_FIELD_VALUE_LIMIT for _, value in fields)
        for index, url in enumerate(long_urls):
            assert fields[index][1] == f"{index + 1}. [Video {index + 1}]({url})"
        assert fields[-1] == ("Link access", _SIGNED_NOTE)

    def test_at_most_ten_links(self):
        urls = [f"https://s.test/{index}" for index in range(12)]
        [(_, links), _] = _build_download_fields({"urls": urls, "expires_at": _EXPIRES_AT})
        assert "10. [Video 10](https://s.test/9)" in links
        assert "Video 11" not in links


class TestAddDownloadFields:
    def test_fields_past_the_embed_total_move_to_new_embeds_before_the_cost_embed(self):
        video_embed = Embed(title="Video Generation", description="d" * 1700, color=Colour.blue())
        cost_embed = Embed(description="$0.0200 · 1 video · $0.02 today")
        embeds = [video_embed, cost_embed]
        signed_urls = [f"https://s.test/{index}?sig={'f' * 880}" for index in range(10)]
        fields = _build_download_fields({"urls": signed_urls, "expires_at": _EXPIRES_AT})

        _add_download_fields(embeds, fields)

        assert embeds[0] is video_embed
        assert embeds[-1] is cost_embed
        # 1,716 + 4 x ~930 characters fit in the video embed; the other 6 links and the
        # Link access note go into one new embed placed before the cost embed.
        assert len(embeds) == 3
        assert len(video_embed.fields) == 4
        assert all(count_embed_chars(embed) <= DISCORD_EMBED_TOTAL_LIMIT for embed in embeds)
        placed = [(f.name, f.value) for embed in embeds[:-1] for f in embed.fields]
        assert placed == fields
        assert cost_embed.fields == []

    def test_short_fields_stay_on_the_video_embed(self):
        video_embed = Embed(title="Video Generation", description="d")
        embeds = [video_embed]
        fields = _build_download_fields({"urls": [_SIGNED_URL], "expires_at": _EXPIRES_AT})

        _add_download_fields(embeds, fields)

        assert embeds == [video_embed]
        assert [(f.name, f.value) for f in video_embed.fields] == fields


class TestCoerceStr:
    def test_strips_whitespace(self):
        assert _coerce_str("  hello  ") == "hello"

    def test_returns_empty_string_for_non_strings(self):
        assert _coerce_str(None) == ""
        assert _coerce_str(42) == ""


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

    @pytest.mark.parametrize("status", ["cancelled", "expired", "CANCELLED"])
    def test_cancelled_or_expired_job_ends_polling_with_the_status(self, status):
        cog = _make_cog()
        # A list side effect raises on a third call, so a loop that kept polling fails fast.
        cog.openrouter_client.get_video_generation = AsyncMock(
            side_effect=[
                {"id": "j1", "status": "in_progress"},
                {"id": "j1", "status": status},
            ]
        )
        with (
            patch(
                "discord_openrouter.cogs.openrouter.video.asyncio.sleep", new=AsyncMock()
            ) as sleep,
            pytest.raises(OpenRouterApiError) as excinfo,
        ):
            asyncio.run(
                _poll_until_complete(cog, job_id="j1", polling_url=None, initial_status="pending")
            )
        assert str(excinfo.value) == (
            f"The video job ended with status `{status.lower()}`; no video was returned."
        )
        assert cog.openrouter_client.get_video_generation.await_count == 2
        assert sleep.await_count == 2

    @pytest.mark.parametrize("status", ["cancelled", "expired"])
    def test_cancelled_or_expired_submit_status_ends_without_polling(self, status):
        cog = _make_cog()
        cog.openrouter_client.get_video_generation = AsyncMock(
            side_effect=AssertionError("a job in a terminal status must not be polled")
        )
        with (
            patch("discord_openrouter.cogs.openrouter.video.asyncio.sleep", new=AsyncMock()),
            pytest.raises(OpenRouterApiError, match=f"ended with status `{status}`"),
        ):
            asyncio.run(
                _poll_until_complete(cog, job_id="j1", polling_url=None, initial_status=status)
            )
        cog.openrouter_client.get_video_generation.assert_not_awaited()

    def test_cancelled_job_error_includes_the_api_error_text(self):
        cog = _make_cog()
        cog.openrouter_client.get_video_generation = AsyncMock(
            side_effect=[{"id": "j1", "status": "expired", "error": "Job expired in queue."}]
        )
        with (
            patch("discord_openrouter.cogs.openrouter.video.asyncio.sleep", new=AsyncMock()),
            pytest.raises(OpenRouterApiError) as excinfo,
        ):
            asyncio.run(
                _poll_until_complete(cog, job_id="j1", polling_url=None, initial_status="pending")
            )
        assert str(excinfo.value) == (
            "The video job ended with status `expired`; no video was returned. "
            "Job expired in queue."
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

    def test_default_video_model_from_live_catalog_reaches_submit(self, mixed_modality_models):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=mixed_modality_models[DEFAULT_VIDEO_MODEL]
        )
        # Short-circuit after validation so the poll loop never starts.
        cog.openrouter_client.create_video_generation = AsyncMock(
            side_effect=OpenRouterApiError("quota exceeded")
        )
        with patch("discord_openrouter.cogs.openrouter.video.error_embed") as error_embed_factory:
            self._run(cog, ctx, model=DEFAULT_VIDEO_MODEL)
        cog.openrouter_client.create_video_generation.assert_awaited_once()
        error_embed_factory.assert_called_once_with("quota exceeded")

    @pytest.mark.parametrize(
        ("is_byok", "expected_line", "expected_daily_total"),
        [
            (True, "$0.5250 · 1 video · 720p · 16:9 · $0.53 today", 0.525),
            (False, "$0.0250 · 1 video · 720p · 16:9 · $0.03 today", 0.025),
        ],
        ids=["byok", "not-byok"],
    )
    def test_cost_line_and_daily_total_follow_byok_rule(
        self, monkeypatch, is_byok, expected_line, expected_daily_total
    ):
        monkeypatch.setattr("discord_openrouter.cogs.openrouter.video.SHOW_COST_EMBEDS", True)
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.video.VIDEO_POLL_INTERVAL_SECONDS", 0
        )
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.create_video_generation = AsyncMock(
            return_value={"id": "job-1", "status": "pending"}
        )
        cog.openrouter_client.get_video_generation = AsyncMock(
            return_value={
                "id": "job-1",
                "status": "completed",
                "unsigned_urls": ["https://example.test/video.mp4"],
                "usage": {
                    "cost": 0.025,
                    "is_byok": is_byok,
                    "cost_details": {"upstream_inference_cost": 0.5},
                },
            }
        )
        cog.openrouter_client.download_file_bytes = AsyncMock(return_value=(b"video", "video/mp4"))

        self._run(cog, ctx, model="m", resolution="720p", aspect_ratio="16:9")

        # The upload is a direct followup.send, not send_embed_batches.
        ctx.followup.send.assert_awaited_once()
        sent = ctx.followup.send.await_args.kwargs
        assert [file.filename for file in sent["files"]] == ["job-1_1.mp4"]
        cost_embed = sent["embeds"][-1]
        assert cost_embed.description == expected_line
        [(daily_total, _)] = cog.daily_costs.values()
        assert daily_total == pytest.approx(expected_daily_total)

    @staticmethod
    def _completed_job(cog, extra_fields, *, video_bytes=b"video"):
        cog.openrouter_client.create_video_generation = AsyncMock(
            return_value={"id": "job-1", "status": "pending"}
        )
        cog.openrouter_client.get_video_generation = AsyncMock(
            return_value={
                "id": "job-1",
                "status": "completed",
                "unsigned_urls": [_UNSIGNED_URL],
                "usage": {"cost": 0.02},
                **extra_fields,
            }
        )
        cog.openrouter_client.download_file_bytes = AsyncMock(
            return_value=(video_bytes, "video/mp4")
        )

    @staticmethod
    def _sent_fields(calls):
        return [
            (field.name, field.value)
            for call in calls
            for embed in call.kwargs.get("embeds") or [call.kwargs.get("embed")]
            if embed is not None
            for field in embed.fields
        ]

    @pytest.mark.parametrize(
        ("extra_fields", "expected_url", "expected_note", "unexpected_url"),
        [
            (
                {"urls": [_SIGNED_URL], "expires_at": _EXPIRES_AT},
                _SIGNED_URL,
                _SIGNED_NOTE,
                _UNSIGNED_URL,
            ),
            ({}, _UNSIGNED_URL, _UNSIGNED_NOTE, _SIGNED_URL),
        ],
        ids=["signed-urls", "unsigned-urls-only"],
    )
    def test_rejected_upload_posts_download_links_through_the_real_embed_sender(
        self, monkeypatch, extra_fields, expected_url, expected_note, unexpected_url
    ):
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.video.VIDEO_POLL_INTERVAL_SECONDS", 0
        )
        cog = _make_cog()
        ctx = _make_ctx()
        self._completed_job(cog, extra_fields)

        uploaded_sizes: list[list[int]] = []

        async def send(**kwargs):
            files = kwargs.get("files") or ([kwargs["file"]] if kwargs.get("file") else [])
            if not files:
                return
            # An upload reads each file from its current position to the end, so a retry
            # with the same File objects sends 0 bytes. Discord's limit here is 4 bytes.
            sizes = [len(file.fp.read()) for file in files]
            uploaded_sizes.append(sizes)
            if any(size > 4 for size in sizes):
                raise HTTPException(
                    SimpleNamespace(status=413, reason="Payload Too Large"),
                    "Request entity too large",
                )

        ctx.followup.send = AsyncMock(side_effect=send)
        # send_embed_batches is NOT patched: the link message goes through the real one.
        asyncio.run(run_video_command(cog, ctx=ctx, prompt="a ball", model="m"))

        # The download still uses unsigned_urls with the API key; only the posted links change.
        cog.openrouter_client.download_file_bytes.assert_awaited_once_with(_UNSIGNED_URL)
        calls = ctx.followup.send.await_args_list
        assert uploaded_sizes == [[len(b"video")]]
        assert [bool(call.kwargs.get("files") or call.kwargs.get("file")) for call in calls] == [
            True,
            False,
        ]
        assert self._sent_fields(calls[1:]) == [
            ("Downloads", f"1. [Video 1]({expected_url})"),
            ("Link access", expected_note),
        ]
        assert all(unexpected_url not in value for _, value in self._sent_fields(calls))

    @pytest.mark.parametrize("limit_source", ["interaction", "guild"])
    def test_video_above_the_upload_limit_is_linked_without_an_upload(
        self, monkeypatch, limit_source
    ):
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.video.VIDEO_POLL_INTERVAL_SECONDS", 0
        )
        cog = _make_cog()
        ctx = _make_ctx()
        if limit_source == "interaction":
            ctx.interaction = SimpleNamespace(id=999, attachment_size_limit=4)
        else:
            ctx.guild = SimpleNamespace(filesize_limit=4)
        self._completed_job(
            cog, {"urls": [_SIGNED_URL], "expires_at": _EXPIRES_AT}, video_bytes=b"video"
        )

        asyncio.run(run_video_command(cog, ctx=ctx, prompt="a ball", model="m"))

        calls = ctx.followup.send.await_args_list
        assert len(calls) == 1
        assert "files" not in calls[0].kwargs
        assert "file" not in calls[0].kwargs
        assert self._sent_fields(calls) == [
            ("Downloads", f"1. [Video 1]({_SIGNED_URL})"),
            ("Link access", _SIGNED_NOTE),
        ]

    def test_video_at_the_upload_limit_is_uploaded(self, monkeypatch):
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.video.VIDEO_POLL_INTERVAL_SECONDS", 0
        )
        cog = _make_cog()
        ctx = _make_ctx()
        ctx.interaction = SimpleNamespace(id=999, attachment_size_limit=len(b"video"))
        self._completed_job(cog, {"urls": [_SIGNED_URL], "expires_at": _EXPIRES_AT})

        asyncio.run(run_video_command(cog, ctx=ctx, prompt="a ball", model="m"))

        ctx.followup.send.assert_awaited_once()
        sent = ctx.followup.send.await_args.kwargs
        assert [file.filename for file in sent["files"]] == ["job-1_1.mp4"]
        assert self._sent_fields([ctx.followup.send.await_args]) == []

    def test_ten_long_signed_links_keep_every_embed_and_message_within_discord_limits(
        self, monkeypatch
    ):
        monkeypatch.setattr("discord_openrouter.cogs.openrouter.video.SHOW_COST_EMBEDS", True)
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.video.VIDEO_POLL_INTERVAL_SECONDS", 0
        )
        cog = _make_cog()
        ctx = _make_ctx()
        ctx.interaction = SimpleNamespace(id=999, attachment_size_limit=4)
        # Live signed URLs are about 905 characters long.
        signed_urls = [
            f"https://storage.example.test/{index}?X-Goog-Signature={'f' * 870}"
            for index in range(10)
        ]
        self._completed_job(cog, {"urls": signed_urls, "expires_at": _EXPIRES_AT})

        asyncio.run(run_video_command(cog, ctx=ctx, prompt="p" * 2000, model="m"))

        calls = ctx.followup.send.await_args_list
        assert len(calls) > 1
        for call in calls:
            assert "files" not in call.kwargs
            embeds = call.kwargs["embeds"]
            assert len(embeds) <= DISCORD_EMBEDS_PER_MESSAGE_LIMIT
            assert all(count_embed_chars(embed) <= DISCORD_EMBED_TOTAL_LIMIT for embed in embeds)
            assert sum(count_embed_chars(embed) for embed in embeds) <= DISCORD_EMBED_TOTAL_LIMIT
            assert all(
                len(field.value) <= EMBED_FIELD_VALUE_LIMIT for e in embeds for field in e.fields
            )
        fields = self._sent_fields(calls)
        assert [value for _, value in fields[:-1]] == [
            f"{index}. [Video {index}]({url})" for index, url in enumerate(signed_urls, start=1)
        ]
        assert fields[-1] == ("Link access", _SIGNED_NOTE)
        assert calls[0].kwargs["embeds"][0].title == "Video Generation"
        assert calls[-1].kwargs["embeds"][-1].description.startswith("$0.0200 · 1 video")

    @pytest.mark.parametrize("status", ["cancelled", "expired"])
    def test_cancelled_or_expired_job_shows_an_error_naming_the_status(self, monkeypatch, status):
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.video.VIDEO_POLL_INTERVAL_SECONDS", 0
        )
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.create_video_generation = AsyncMock(
            return_value={"id": "job-1", "status": "pending"}
        )
        cog.openrouter_client.get_video_generation = AsyncMock(
            side_effect=[{"id": "job-1", "status": status}]
        )
        with patch("discord_openrouter.cogs.openrouter.video.error_embed") as error_embed_factory:
            self._run(cog, ctx, model="m")
        error_embed_factory.assert_called_once_with(
            f"The video job ended with status `{status}`; no video was returned."
        )
        cog.openrouter_client.download_file_bytes.assert_not_awaited()
        assert cog.daily_costs == {}
