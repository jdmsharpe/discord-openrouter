from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.client import OpenRouterApiError
from discord_openrouter.cogs.openrouter.speech import (
    TTS_MAX_CHARS,
    _audio_format_for_stt,
    _build_stt_prompt,
    _is_audio_attachment,
    _resolve_audio_modalities,
    _resolve_transcript,
    run_stt_command,
    run_tts_command,
)
from discord_openrouter.util import ModelInfo


def _make_attachment(
    *,
    filename: str = "speech.mp3",
    content_type: str | None = "audio/mpeg",
    size: int = 1024,
    url: str = "https://example.test/speech.mp3",
) -> SimpleNamespace:
    return SimpleNamespace(
        filename=filename,
        content_type=content_type,
        size=size,
        url=url,
    )


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
            create_speech=AsyncMock(),
            create_chat_completion=AsyncMock(),
        ),
    )


class TestResolveTranscript:
    def test_prefers_transcript_field(self):
        assert _resolve_transcript({"transcript": "  hello  ", "text": "ignore"}) == "hello"

    def test_falls_back_to_text_field(self):
        assert _resolve_transcript({"text": " world "}) == "world"

    def test_returns_empty_string_when_neither_present(self):
        assert _resolve_transcript({}) == ""

    def test_ignores_blank_transcript(self):
        # Whitespace-only transcript should fall through to `text`.
        assert _resolve_transcript({"transcript": "   ", "text": "alt"}) == "alt"

    def test_ignores_non_string_transcript(self):
        assert _resolve_transcript({"transcript": 123, "text": "ok"}) == "ok"


class TestResolveAudioModalities:
    def test_audio_only_when_text_unsupported(self):
        info = ModelInfo(id="m", name="m", output_modalities=["audio"])
        assert _resolve_audio_modalities(info) == ["audio"]

    def test_text_and_audio_when_text_supported(self):
        info = ModelInfo(id="m", name="m", output_modalities=["text", "audio"])
        assert _resolve_audio_modalities(info) == ["text", "audio"]

    def test_text_and_audio_when_model_info_unknown(self):
        assert _resolve_audio_modalities(None) == ["text", "audio"]


class TestBuildSttPrompt:
    def test_returns_base_prompt_without_instructions(self):
        prompt = _build_stt_prompt(None)
        assert prompt.startswith("Transcribe this audio")
        assert "Additional instructions" not in prompt

    def test_blank_instructions_treated_as_none(self):
        assert _build_stt_prompt("   ") == _build_stt_prompt(None)

    def test_appends_user_instructions(self):
        prompt = _build_stt_prompt("preserve filler words")
        assert "Additional instructions: preserve filler words" in prompt


class TestIsAudioAttachment:
    @pytest.mark.parametrize("content_type", ["audio/mpeg", "audio/wav", "audio/ogg"])
    def test_audio_content_type_accepted(self, content_type: str):
        assert _is_audio_attachment(_make_attachment(content_type=content_type)) is True

    def test_video_mp4_accepted(self):
        # Discord sometimes labels .mp4 audio as video/mp4; OpenRouter still accepts it.
        assert _is_audio_attachment(_make_attachment(content_type="video/mp4")) is True

    def test_octet_stream_accepted(self):
        assert _is_audio_attachment(_make_attachment(content_type="application/octet-stream"))

    def test_text_content_type_rejected(self):
        assert _is_audio_attachment(_make_attachment(content_type="text/plain")) is False

    def test_falls_back_to_extension_when_content_type_blank(self):
        assert (
            _is_audio_attachment(_make_attachment(content_type=None, filename="clip.flac")) is True
        )
        assert _is_audio_attachment(_make_attachment(content_type="", filename="clip.txt")) is False

    def test_strips_charset_suffix_from_content_type(self):
        # Content-types may include "; charset=binary" or similar; the lookup must ignore that.
        assert _is_audio_attachment(_make_attachment(content_type="audio/wav; codecs=1")) is True


class TestAudioFormatForStt:
    def test_known_alias_uses_canonical_format(self):
        assert _audio_format_for_stt(_make_attachment(content_type="audio/mpeg")) == "mp3"
        assert _audio_format_for_stt(_make_attachment(content_type="video/mp4")) == "mp4"

    def test_aif_alias_normalizes_to_aiff(self):
        attachment = _make_attachment(content_type="application/octet-stream", filename="clip.aif")
        assert _audio_format_for_stt(attachment) == "aiff"

    def test_unknown_content_type_uses_extension(self):
        attachment = _make_attachment(content_type="application/octet-stream", filename="clip.opus")
        assert _audio_format_for_stt(attachment) == "opus"

    def test_no_extension_defaults_to_mp3(self):
        attachment = _make_attachment(content_type="application/octet-stream", filename="anonymous")
        assert _audio_format_for_stt(attachment) == "mp3"


class TestRunTtsCommand:
    def _run(self, cog, ctx, **kwargs):
        with patch(
            "discord_openrouter.cogs.openrouter.speech.send_embed_batches", new=AsyncMock()
        ) as send:
            asyncio.run(run_tts_command(cog, ctx=ctx, **kwargs))
        return send

    def test_rejects_text_over_limit(self):
        cog = _make_cog()
        ctx = _make_ctx()
        oversized = "x" * (TTS_MAX_CHARS + 1)

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            send = self._run(cog, ctx, input_text=oversized)

        ctx.defer.assert_awaited_once()
        message = error_embed_factory.call_args.args[0]
        assert "exceeds" in message
        send.assert_awaited_once()
        cog.openrouter_client.get_model.assert_not_awaited()

    def test_rejects_when_no_model_resolved(self, monkeypatch):
        cog = _make_cog()
        ctx = _make_ctx()
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.speech.OPENROUTER_DEFAULT_TTS_MODEL", ""
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, input_text="hello")

        message = error_embed_factory.call_args.args[0]
        assert "No TTS model" in message
        cog.openrouter_client.get_model.assert_not_awaited()

    def test_rejects_when_model_does_not_advertise_audio_output(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=ModelInfo(
                id="openai/gpt-text-only", name="text-only", output_modalities=["text"]
            )
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, input_text="hello", model="openai/gpt-text-only")

        message = error_embed_factory.call_args.args[0]
        assert "does not advertise audio output" in message

    def test_propagates_api_error_during_get_model(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            side_effect=OpenRouterApiError("upstream timeout")
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, input_text="hello", model="openai/tts-1")

        error_embed_factory.assert_called_once_with("upstream timeout")
        cog.openrouter_client.create_speech.assert_not_awaited()

    def test_rejects_when_no_audio_bytes_returned(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=ModelInfo(id="openai/tts-1", name="tts", output_modalities=["audio"])
        )
        cog.openrouter_client.create_speech = AsyncMock(
            return_value={"audio_bytes": b"", "usage": {}}
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, input_text="hello", model="openai/tts-1")

        message = error_embed_factory.call_args.args[0]
        assert "no audio data" in message

    def test_uses_channel_default_model(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.channel_model_defaults[(900, 42, "tts")] = "openai/tts-1-hd"
        cog.openrouter_client.get_model = AsyncMock(
            return_value=ModelInfo(id="openai/tts-1-hd", name="tts-hd", output_modalities=["audio"])
        )
        # Stub create_speech to short-circuit on no-audio so we only verify routing.
        cog.openrouter_client.create_speech = AsyncMock(
            return_value={"audio_bytes": b"", "usage": {}}
        )
        with patch("discord_openrouter.cogs.openrouter.speech.error_embed"):
            self._run(cog, ctx, input_text="hello")

        cog.openrouter_client.get_model.assert_awaited_once_with("openai/tts-1-hd")


class TestRunSttCommand:
    def _run(self, cog, ctx, **kwargs):
        with patch(
            "discord_openrouter.cogs.openrouter.speech.send_embed_batches", new=AsyncMock()
        ) as send:
            asyncio.run(run_stt_command(cog, ctx=ctx, **kwargs))
        return send

    def test_rejects_non_audio_attachment(self):
        cog = _make_cog()
        ctx = _make_ctx()
        attachment = _make_attachment(filename="doc.txt", content_type="text/plain")

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, attachment=attachment)

        message = error_embed_factory.call_args.args[0]
        assert "must be an audio file" in message

    def test_rejects_when_no_model_resolved(self, monkeypatch):
        cog = _make_cog()
        ctx = _make_ctx()
        monkeypatch.setattr(
            "discord_openrouter.cogs.openrouter.speech.OPENROUTER_DEFAULT_STT_MODEL", ""
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, attachment=_make_attachment())

        message = error_embed_factory.call_args.args[0]
        assert "No STT model" in message

    def test_rejects_when_model_lacks_audio_input(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=ModelInfo(
                id="text-only",
                name="text-only",
                input_modalities=["text"],
                output_modalities=["text"],
            )
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, attachment=_make_attachment(), model="text-only")

        message = error_embed_factory.call_args.args[0]
        assert "does not advertise audio input" in message

    def test_rejects_when_model_lacks_text_output(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=ModelInfo(
                id="audio-only",
                name="audio-only",
                input_modalities=["audio"],
                output_modalities=["audio"],
            )
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, attachment=_make_attachment(), model="audio-only")

        message = error_embed_factory.call_args.args[0]
        assert "does not advertise text output" in message

    def test_propagates_api_error_during_get_model(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            side_effect=OpenRouterApiError("upstream auth error")
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, attachment=_make_attachment(), model="any/stt")

        error_embed_factory.assert_called_once_with("upstream auth error")
