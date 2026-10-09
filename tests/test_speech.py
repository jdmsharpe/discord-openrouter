from __future__ import annotations

import asyncio
import io
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.client import OpenRouterApiError
from discord_openrouter.cogs.openrouter.speech import (
    DEFAULT_UPLOAD_LIMIT_BYTES,
    TTS_MAX_CHARS,
    _audio_format_for_stt,
    _build_stt_prompt,
    _is_audio_attachment,
    _pcm_to_wav,
    _resolve_audio_modalities,
    _resolve_transcript,
    _speech_audio_file,
    _split_wav,
    _upload_limit_bytes,
    _uses_speech_endpoint,
    run_stt_command,
    run_tts_command,
)
from discord_openrouter.config import DEFAULT_STT_MODEL, DEFAULT_TTS_MODEL
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
            create_audio_speech=AsyncMock(),
            get_generation=AsyncMock(return_value=None),
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

    def test_accepts_model_advertising_speech_output(self):
        # The live catalog labels TTS models' output modality "speech", not "audio".
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=ModelInfo(id="openai/tts-1", name="tts", output_modalities=["speech"])
        )
        # Stub the speech endpoint to short-circuit on no-audio so we only verify validation.
        cog.openrouter_client.create_audio_speech = AsyncMock(
            return_value={"audio_bytes": b"", "content_type": None}
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, input_text="hello", model="openai/tts-1")

        cog.openrouter_client.create_audio_speech.assert_awaited_once()
        cog.openrouter_client.create_speech.assert_not_awaited()
        message = error_embed_factory.call_args.args[0]
        assert "no audio data" in message

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

    def test_default_tts_model_from_live_catalog_is_accepted(self, mixed_modality_models):
        # The real default entry advertises only `speech`, which TTS_OUTPUT_MODALITIES accepts.
        assert DEFAULT_TTS_MODEL in mixed_modality_models
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=mixed_modality_models[DEFAULT_TTS_MODEL]
        )
        cog.openrouter_client.create_audio_speech = AsyncMock(
            return_value={"audio_bytes": b"", "content_type": None}
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, input_text="hello")

        # The default is a `speech` model, which chat completions rejects with a 400.
        cog.openrouter_client.create_speech.assert_not_awaited()
        cog.openrouter_client.create_audio_speech.assert_awaited_once_with(
            model=DEFAULT_TTS_MODEL,
            input_text="hello",
            voice="Zephyr",
            response_format="mp3",
            instructions=None,
            user="42",
            session_id="tts:999",
        )
        assert "no audio data" in error_embed_factory.call_args.args[0]

    @pytest.mark.parametrize(
        "model_id",
        [
            "bytedance-seed/seedream-5-0-pro",
            "openai/gpt-transcribe",
            "deepseek/deepseek-v4-flash",
        ],
    )
    def test_rejects_live_models_without_audio_output(self, mixed_modality_models, model_id):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(return_value=mixed_modality_models[model_id])

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, input_text="hello", model=model_id)

        message = error_embed_factory.call_args.args[0]
        assert f"`{model_id}` does not advertise audio output" in message
        cog.openrouter_client.create_speech.assert_not_awaited()


def _pcm_samples(frame_count: int) -> bytes:
    return b"".join((index % 256).to_bytes(2, "little") for index in range(frame_count))


def _read_wav(data: bytes) -> tuple[int, int, int, int]:
    with wave.open(io.BytesIO(data), "rb") as wav_file:
        return (
            wav_file.getnchannels(),
            wav_file.getsampwidth(),
            wav_file.getframerate(),
            wav_file.getnframes(),
        )


class TestUsesSpeechEndpoint:
    def test_speech_models_use_the_speech_endpoint(self, mixed_modality_models):
        assert _uses_speech_endpoint(mixed_modality_models[DEFAULT_TTS_MODEL]) is True

    def test_audio_output_chat_models_stay_on_chat_completions(self, mixed_modality_models):
        assert _uses_speech_endpoint(mixed_modality_models["openai/gpt-audio"]) is False

    def test_model_listing_both_labels_stays_on_chat_completions(self):
        info = ModelInfo(id="m", name="m", output_modalities=["speech", "audio"])
        assert _uses_speech_endpoint(info) is False


class TestSpeechAudioFile:
    def test_wraps_pcm_in_a_wav_header_using_content_type_rate_and_channels(self):
        pcm = _pcm_samples(24000)

        data, file_format = _speech_audio_file(
            pcm, content_type="audio/pcm;rate=24000;channels=1", requested_format="pcm"
        )

        assert file_format == "wav"
        assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
        assert _read_wav(data) == (1, 2, 24000, 24000)
        assert data.endswith(pcm)

    def test_reads_other_rates_and_channel_counts(self):
        data, _ = _speech_audio_file(
            _pcm_samples(3200),
            content_type="audio/pcm; rate=16000; channels=2",
            requested_format="pcm",
        )

        assert _read_wav(data) == (2, 2, 16000, 1600)

    def test_drops_a_trailing_partial_frame(self):
        data, _ = _speech_audio_file(
            _pcm_samples(10) + b"\x01", content_type="audio/pcm", requested_format="pcm"
        )

        assert _read_wav(data) == (1, 2, 24000, 10)

    def test_missing_content_type_falls_back_to_the_requested_format(self):
        wav_data, wav_format = _speech_audio_file(
            _pcm_samples(4), content_type=None, requested_format="pcm"
        )
        mp3_data, mp3_format = _speech_audio_file(b"ID3", content_type="", requested_format="mp3")

        assert wav_format == "wav" and wav_data[:4] == b"RIFF"
        assert (mp3_data, mp3_format) == (b"ID3", "mp3")

    def test_mp3_passes_through(self):
        assert _speech_audio_file(b"ID3", content_type="audio/mpeg", requested_format="mp3") == (
            b"ID3",
            "mp3",
        )

    @pytest.mark.parametrize(
        ("content_type", "file_format"),
        [
            ("audio/ogg", "ogg"),
            ("audio/opus", "ogg"),
            ("audio/aac", "aac"),
            ("audio/flac", "flac"),
            ("audio/x-flac", "flac"),
            ("audio/x-wav", "wav"),
            ("audio/mp3", "mp3"),
        ],
    )
    def test_common_audio_types_keep_their_bytes_and_get_their_extension(
        self, content_type, file_format
    ):
        assert _speech_audio_file(b"data", content_type=content_type, requested_format="mp3") == (
            b"data",
            file_format,
        )

    def test_unknown_type_falls_back_to_requested_pcm_and_is_logged(self, caplog):
        pcm = _pcm_samples(8)

        with caplog.at_level("WARNING", logger="discord_openrouter.cogs.openrouter.speech"):
            data, file_format = _speech_audio_file(
                pcm, content_type="application/octet-stream", requested_format="pcm"
            )

        assert file_format == "wav"
        assert _read_wav(data) == (1, 2, 24000, 8)
        assert "application/octet-stream" in caplog.text

    def test_unknown_type_falls_back_to_requested_mp3(self):
        assert _speech_audio_file(
            b"ID3", content_type="application/octet-stream", requested_format="mp3"
        ) == (b"ID3", "mp3")

    def test_l16_big_endian_samples_are_swapped_into_the_wav(self):
        big_endian = b"\x01\x02\x03\x04"

        data, file_format = _speech_audio_file(
            big_endian, content_type="audio/L16;rate=16000;channels=1", requested_format="pcm"
        )

        assert file_format == "wav"
        assert _read_wav(data) == (1, 2, 16000, 2)
        assert data.endswith(b"\x02\x01\x04\x03")


class TestRunTtsCommandSpeechEndpoint:
    def _run(self, cog, ctx, **kwargs):
        with patch(
            "discord_openrouter.cogs.openrouter.speech.send_embed_batches", new=AsyncMock()
        ) as send:
            asyncio.run(run_tts_command(cog, ctx=ctx, **kwargs))
        return send

    def _speech_cog(self, model_info, *, generation=None):
        cog = _make_cog()
        cog.openrouter_client.get_model = AsyncMock(return_value=model_info)
        cog.openrouter_client.create_audio_speech = AsyncMock(
            return_value={
                "audio_bytes": _pcm_samples(48),
                "content_type": "audio/pcm;rate=24000;channels=1",
                "response_format": "pcm",
                "generation_id": "gen-tts-1",
            }
        )
        cog.openrouter_client.get_generation = AsyncMock(return_value=generation)
        return cog

    def test_sends_wav_with_catalog_voice_and_generation_cost(self, mixed_modality_models):
        cog = self._speech_cog(
            mixed_modality_models[DEFAULT_TTS_MODEL],
            generation={"total_cost": 0.001087, "tokens_prompt": 13, "tokens_completion": 120},
        )
        ctx = _make_ctx()

        send = self._run(cog, ctx, input_text="Hello there.")

        cog.openrouter_client.get_generation.assert_awaited_once_with("gen-tts-1")
        kwargs = send.await_args.kwargs
        sent_file = kwargs["file"]
        assert sent_file.filename == "speech.wav"
        audio = sent_file.fp.read()
        assert audio[:4] == b"RIFF"
        assert _read_wav(audio) == (1, 2, 24000, 48)
        description = kwargs["embeds"][0].description
        assert "**Voice:** Zephyr" in description
        # The format option was left at its default, so no "(requested ...)" note.
        assert "**Response Format:** wav\n" in description
        cost_line = kwargs["embeds"][-1].description
        assert cost_line.startswith("$0.0011 · 13 in / 120 out · 12 chars · Zephyr")
        assert cog.daily_costs

    def test_reports_cost_unavailable_when_generation_is_not_found(self, mixed_modality_models):
        cog = self._speech_cog(mixed_modality_models[DEFAULT_TTS_MODEL], generation=None)
        ctx = _make_ctx()

        send = self._run(cog, ctx, input_text="Hello there.")

        cog.openrouter_client.get_generation.assert_awaited_once_with("gen-tts-1")
        cost_line = send.await_args.kwargs["embeds"][-1].description
        assert cost_line == "cost unavailable · 12 chars · Zephyr"
        assert cog.daily_costs == {}

    def test_explicitly_chosen_format_is_shown_when_it_differs(self, mixed_modality_models):
        cog = self._speech_cog(mixed_modality_models[DEFAULT_TTS_MODEL])

        send = self._run(cog, _make_ctx(), input_text="Hi.", response_format="mp3")

        description = send.await_args.kwargs["embeds"][0].description
        assert "**Response Format:** wav (requested mp3)\n" in description

    def test_explicit_voice_overrides_catalog_voice(self, mixed_modality_models):
        cog = self._speech_cog(mixed_modality_models[DEFAULT_TTS_MODEL])

        send = self._run(cog, _make_ctx(), input_text="Hi.", voice="  Kore ")

        assert cog.openrouter_client.create_audio_speech.await_args.kwargs["voice"] == "Kore"
        assert "**Voice:** Kore" in send.await_args.kwargs["embeds"][0].description

    def test_sends_no_voice_when_catalog_lists_none(self):
        cog = self._speech_cog(
            ModelInfo(id="fish-audio/s1", name="s1", output_modalities=["speech"])
        )

        send = self._run(cog, _make_ctx(), input_text="Hi.")

        assert cog.openrouter_client.create_audio_speech.await_args.kwargs["voice"] is None
        assert (
            "**Voice:** model/provider default" in send.await_args.kwargs["embeds"][0].description
        )

    @pytest.mark.parametrize(
        ("choice", "requested"), [("mp3", "mp3"), ("wav", "pcm"), ("flac", "pcm"), ("opus", "pcm")]
    )
    def test_maps_format_choice_to_speech_endpoint_format(
        self, mixed_modality_models, choice, requested
    ):
        cog = self._speech_cog(mixed_modality_models[DEFAULT_TTS_MODEL])

        self._run(cog, _make_ctx(), input_text="Hi.", response_format=choice)

        kwargs = cog.openrouter_client.create_audio_speech.await_args.kwargs
        assert kwargs["response_format"] == requested

    def test_sends_mp3_unchanged_when_the_model_returns_mp3(self):
        cog = self._speech_cog(
            ModelInfo(
                id="hexgrad/kokoro-82m",
                name="kokoro",
                output_modalities=["speech"],
                supported_voices=["af_alloy"],
            )
        )
        cog.openrouter_client.create_audio_speech = AsyncMock(
            return_value={
                "audio_bytes": b"ID3-mp3-bytes",
                "content_type": "audio/mpeg",
                "response_format": "mp3",
                "generation_id": None,
            }
        )

        send = self._run(cog, _make_ctx(), input_text="Hi.")

        sent_file = send.await_args.kwargs["file"]
        assert sent_file.filename == "speech.mp3"
        assert sent_file.fp.read() == b"ID3-mp3-bytes"
        assert "**Response Format:** mp3\n" in send.await_args.kwargs["embeds"][0].description
        cog.openrouter_client.get_generation.assert_not_awaited()

    def test_api_error_is_shown(self, mixed_modality_models):
        cog = self._speech_cog(mixed_modality_models[DEFAULT_TTS_MODEL])
        cog.openrouter_client.create_audio_speech = AsyncMock(
            side_effect=OpenRouterApiError("An explicit voice is required for this TTS provider.")
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, _make_ctx(), input_text="Hi.")

        error_embed_factory.assert_called_once_with(
            "An explicit voice is required for this TTS provider."
        )


class TestRunTtsCommandChatAudio:
    def _run(self, cog, ctx, **kwargs):
        with patch(
            "discord_openrouter.cogs.openrouter.speech.send_embed_batches", new=AsyncMock()
        ) as send:
            asyncio.run(run_tts_command(cog, ctx=ctx, **kwargs))
        return send

    def _chat_cog(self, model_info, audio_bytes, model_id):
        cog = _make_cog()
        cog.openrouter_client.get_model = AsyncMock(return_value=model_info)
        cog.openrouter_client.create_speech = AsyncMock(
            return_value={
                "audio_bytes": audio_bytes,
                "transcript": "Hi.",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.002},
                "model": model_id,
            }
        )
        return cog

    @pytest.mark.parametrize("choice", ["mp3", "wav", "flac", "opus"])
    def test_openai_audio_model_streams_pcm16_with_alloy_and_sends_wav(
        self, mixed_modality_models, choice
    ):
        cog = self._chat_cog(
            mixed_modality_models["openai/gpt-audio"], _pcm_samples(240), "openai/gpt-audio"
        )

        send = self._run(
            cog, _make_ctx(), input_text="Hi.", model="openai/gpt-audio", response_format=choice
        )

        cog.openrouter_client.create_audio_speech.assert_not_awaited()
        cog.openrouter_client.get_generation.assert_not_awaited()
        cog.openrouter_client.create_speech.assert_awaited_once_with(
            model="openai/gpt-audio",
            input_text="Hi.",
            voice="alloy",
            response_format="pcm16",
            modalities=["text", "audio"],
            instructions=None,
            user="42",
            session_id="tts:999",
            read_aloud=True,
        )
        kwargs = send.await_args.kwargs
        assert kwargs["file"].filename == "speech.wav"
        audio = kwargs["file"].fp.read()
        assert audio[:4] == b"RIFF"
        assert _read_wav(audio) == (1, 2, 24000, 240)
        description = kwargs["embeds"][0].description
        expected_format = "wav" if choice == "wav" else f"wav (requested {choice})"
        assert f"**Response Format:** {expected_format}\n" in description
        assert "**Voice:** alloy" in description
        assert "**Transcript:** Hi." in description
        assert kwargs["embeds"][-1].description.startswith(
            "$0.0020 · 10 in / 20 out · 3 chars · alloy"
        )

    def test_openai_audio_model_with_default_format_shows_wav_only(self, mixed_modality_models):
        cog = self._chat_cog(
            mixed_modality_models["openai/gpt-audio"], _pcm_samples(4), "openai/gpt-audio"
        )

        send = self._run(cog, _make_ctx(), input_text="Hi.", model="openai/gpt-audio")

        assert cog.openrouter_client.create_speech.await_args.kwargs["response_format"] == "pcm16"
        description = send.await_args.kwargs["embeds"][0].description
        assert "**Response Format:** wav\n" in description

    def test_sends_the_resolved_catalog_id_not_the_typed_query(self, mixed_modality_models):
        cog = self._chat_cog(
            mixed_modality_models["openai/gpt-audio"], _pcm_samples(4), "openai/gpt-audio"
        )

        self._run(cog, _make_ctx(), input_text="Hi.", model="gpt audio")

        cog.openrouter_client.get_model.assert_awaited_once_with("gpt audio")
        kwargs = cog.openrouter_client.create_speech.await_args.kwargs
        assert kwargs["model"] == "openai/gpt-audio"
        assert kwargs["read_aloud"] is True

    def test_lyria_default_format_is_mp3(self):
        lyria = ModelInfo(
            id="google/lyria-3-clip-preview", name="lyria", output_modalities=["text", "audio"]
        )
        cog = self._chat_cog(lyria, b"ID3-audio", "google/lyria-3-clip-preview")

        send = self._run(cog, _make_ctx(), input_text="Hi.", model="google/lyria-3-clip-preview")

        assert cog.openrouter_client.create_speech.await_args.kwargs["response_format"] == "mp3"
        assert send.await_args.kwargs["file"].filename == "speech.mp3"
        assert "**Response Format:** mp3\n" in send.await_args.kwargs["embeds"][0].description

    def test_openai_audio_model_keeps_an_explicit_voice(self, mixed_modality_models):
        cog = self._chat_cog(
            mixed_modality_models["openai/gpt-audio"], _pcm_samples(4), "openai/gpt-audio"
        )

        self._run(cog, _make_ctx(), input_text="Hi.", model="openai/gpt-audio", voice="coral")

        assert cog.openrouter_client.create_speech.await_args.kwargs["voice"] == "coral"

    @pytest.mark.parametrize(
        ("choice", "filename"), [("mp3", "speech.mp3"), ("opus", "speech.ogg")]
    )
    def test_other_audio_models_keep_the_chosen_format_and_no_voice(self, choice, filename):
        lyria = ModelInfo(
            id="google/lyria-3-clip-preview", name="lyria", output_modalities=["text", "audio"]
        )
        cog = self._chat_cog(lyria, b"ID3-audio", "google/lyria-3-clip-preview")

        send = self._run(
            cog,
            _make_ctx(),
            input_text="Hi.",
            model="google/lyria-3-clip-preview",
            response_format=choice,
        )

        create_kwargs = cog.openrouter_client.create_speech.await_args.kwargs
        assert create_kwargs["voice"] is None
        assert create_kwargs["response_format"] == choice
        # Lyria generates music from the prompt, so it gets no read-aloud system message.
        assert create_kwargs["read_aloud"] is False
        kwargs = send.await_args.kwargs
        assert kwargs["file"].filename == filename
        assert kwargs["file"].fp.read() == b"ID3-audio"
        description = kwargs["embeds"][0].description
        assert f"**Response Format:** {choice}\n" in description
        assert "**Voice:** model/provider default" in description

    def test_chat_audio_model_uses_its_first_catalog_voice(self):
        info = ModelInfo(
            id="vendor/audio-chat",
            name="audio chat",
            output_modalities=["text", "audio"],
            supported_voices=["nova", "echo"],
        )
        cog = self._chat_cog(info, b"ID3", "vendor/audio-chat")

        self._run(cog, _make_ctx(), input_text="Hi.", model="vendor/audio-chat")

        assert cog.openrouter_client.create_speech.await_args.kwargs["voice"] == "nova"


class TestSplitWav:
    @pytest.mark.parametrize(("channels", "frames"), [(1, 1000), (2, 777)])
    def test_parts_fit_have_valid_headers_and_rejoin_to_the_original(self, channels, frames):
        pcm = _pcm_samples(frames * channels)
        wav_bytes = _pcm_to_wav(pcm, sample_rate=24000, channels=channels)
        limit = 44 + 300 * 2 * channels + 1

        parts = _split_wav(wav_bytes, limit)

        assert parts is not None and len(parts) == -(-frames // 300)
        rejoined = b""
        for part in parts:
            assert len(part) <= limit
            assert part[:4] == b"RIFF" and part[8:12] == b"WAVE"
            with wave.open(io.BytesIO(part), "rb") as wav_file:
                assert wav_file.getnchannels() == channels
                assert wav_file.getsampwidth() == 2
                assert wav_file.getframerate() == 24000
                rejoined += wav_file.readframes(wav_file.getnframes())
        assert rejoined == pcm

    def test_returns_none_for_non_wav_data(self):
        assert _split_wav(b"ID3-not-a-wav" * 10, 64) is None

    def test_returns_none_when_the_limit_cannot_hold_a_frame(self):
        wav_bytes = _pcm_to_wav(_pcm_samples(10), sample_rate=24000, channels=1)
        assert _split_wav(wav_bytes, 45) is None


class TestRunTtsCommandUploadLimit:
    def _run(self, cog, ctx, **kwargs):
        with patch(
            "discord_openrouter.cogs.openrouter.speech.send_embed_batches", new=AsyncMock()
        ) as send:
            asyncio.run(run_tts_command(cog, ctx=ctx, **kwargs))
        return send

    def _ctx(self, limit):
        ctx = _make_ctx()
        ctx.guild = SimpleNamespace(filesize_limit=limit)
        return ctx

    def _speech_cog(self, model_info, payload):
        cog = _make_cog()
        cog.openrouter_client.get_model = AsyncMock(return_value=model_info)
        cog.openrouter_client.create_audio_speech = AsyncMock(return_value=payload)
        return cog

    def _pcm_payload(self, frames):
        return {
            "audio_bytes": _pcm_samples(frames),
            "content_type": "audio/pcm;rate=24000;channels=1",
            "response_format": "pcm",
            "generation_id": None,
        }

    def test_oversized_wav_is_sent_as_numbered_parts_one_per_message(self, mixed_modality_models):
        cog = self._speech_cog(mixed_modality_models[DEFAULT_TTS_MODEL], self._pcm_payload(1000))
        limit = 44 + 2 * 400

        send = self._run(cog, self._ctx(limit), input_text="Hello there.")

        assert send.await_count == 3
        calls = send.await_args_list
        names = [call.kwargs["file"].filename for call in calls]
        assert names == [
            "speech-part-1-of-3.wav",
            "speech-part-2-of-3.wav",
            "speech-part-3-of-3.wav",
        ]
        assert calls[0].kwargs["embeds"][0].title == "Text-to-Speech Generation"
        assert calls[0].kwargs["embeds"][-1].description.startswith("cost unavailable")
        assert all("embeds" not in call.kwargs for call in calls[1:])
        rejoined = b""
        for call in calls:
            data = call.kwargs["file"].fp.read()
            assert len(data) <= limit
            with wave.open(io.BytesIO(data), "rb") as wav_file:
                rejoined += wav_file.readframes(wav_file.getnframes())
        assert rejoined == _pcm_samples(1000)

    def test_audio_within_the_limit_is_sent_as_one_file(self, mixed_modality_models):
        cog = self._speech_cog(mixed_modality_models[DEFAULT_TTS_MODEL], self._pcm_payload(1000))

        send = self._run(cog, self._ctx(44 + 2000), input_text="Hi.")

        send.assert_awaited_once()
        assert send.await_args.kwargs["file"].filename == "speech.wav"

    def test_oversized_mp3_gets_an_error_instead_of_an_upload(self):
        model_info = ModelInfo(
            id="hexgrad/kokoro-82m",
            name="kokoro",
            output_modalities=["speech"],
            supported_voices=["af_alloy"],
        )
        cog = self._speech_cog(
            model_info,
            {
                "audio_bytes": b"ID3" + b"\x00" * 2000,
                "content_type": "audio/mpeg",
                "response_format": "mp3",
                "generation_id": None,
            },
        )

        send = self._run(cog, self._ctx(1000), input_text="Hi.")

        send.assert_awaited_once()
        kwargs = send.await_args.kwargs
        assert "file" not in kwargs
        assert kwargs["embeds"][0].title == "Text-to-Speech Generation"
        error = kwargs["embeds"][-1]
        assert error.title == "Error"
        assert "upload limit" in error.description
        assert "mp3" in error.description

    def test_direct_messages_use_the_default_limit(self):
        assert DEFAULT_UPLOAD_LIMIT_BYTES == 10_485_760
        assert _upload_limit_bytes(SimpleNamespace()) == 10_485_760
        assert _upload_limit_bytes(SimpleNamespace(guild=None)) == 10_485_760
        assert (
            _upload_limit_bytes(SimpleNamespace(guild=SimpleNamespace(filesize_limit=26_214_400)))
            == 26_214_400
        )


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

    def test_default_stt_model_from_live_catalog_is_accepted(self, mixed_modality_models):
        assert DEFAULT_STT_MODEL in mixed_modality_models
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=mixed_modality_models[DEFAULT_STT_MODEL]
        )
        cog.openrouter_client.create_chat_completion = AsyncMock(
            return_value={
                "choices": [{"message": {"role": "assistant", "content": "hello world"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            }
        )
        audio_part = {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "mp3"}}

        with (
            patch(
                "discord_openrouter.cogs.openrouter.speech._build_stt_attachment_part",
                new=AsyncMock(return_value=audio_part),
            ),
            patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory,
        ):
            send = self._run(cog, ctx, attachment=_make_attachment())

        cog.openrouter_client.create_chat_completion.assert_awaited_once()
        error_embed_factory.assert_not_called()
        send.assert_awaited_once()

    def test_rejects_live_transcription_only_model(self, mixed_modality_models):
        # Dedicated transcription models advertise `transcription` output, not `text`; the STT
        # path drives chat completions, so they get the friendly pre-flight error.
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            return_value=mixed_modality_models["openai/gpt-transcribe"]
        )

        with patch("discord_openrouter.cogs.openrouter.speech.error_embed") as error_embed_factory:
            self._run(cog, ctx, attachment=_make_attachment(), model="openai/gpt-transcribe")

        message = error_embed_factory.call_args.args[0]
        assert "`openai/gpt-transcribe` does not advertise text output" in message
        cog.openrouter_client.create_chat_completion.assert_not_awaited()


def test_upload_limit_prefers_the_interaction_limit():
    ctx = SimpleNamespace(
        interaction=SimpleNamespace(id=1, attachment_size_limit=52_428_800),
        guild=SimpleNamespace(filesize_limit=10_485_760),
    )
    assert _upload_limit_bytes(ctx) == 52_428_800
    ctx.interaction.attachment_size_limit = None
    assert _upload_limit_bytes(ctx) == 10_485_760
