from __future__ import annotations

import array
import base64
import io
import logging
import wave
from dataclasses import dataclass

import httpx
from discord import ApplicationContext, Attachment, Colour, Embed, File

from ...config import OPENROUTER_DEFAULT_STT_MODEL, OPENROUTER_DEFAULT_TTS_MODEL, SHOW_COST_EMBEDS
from ...cost_line import count_label
from ...util import (
    ChatUsage,
    ModelInfo,
    calculate_cost,
    extract_generation_usage,
    extract_message_text,
    extract_usage,
    resolve_request_cost,
    sanitize_assistant_message,
    truncate_text,
)
from .attachments import MAX_ATTACHMENT_SIZE, AttachmentInputError, build_user_content
from .client import OpenRouterApiError
from .embed_delivery import send_embed_batches
from .embeds import append_flat_pricing_embed, error_embed
from .safety import build_safety_identifier
from .state import track_daily_cost

logger = logging.getLogger(__name__)

TTS_MAX_CHARS = 4096
DEFAULT_TTS_RESPONSE_FORMAT = "mp3"
# OpenRouter labels TTS models' `architecture.output_modalities` as "speech"; only the
# multimodal audio chat models (gpt-audio, lyria) advertise "audio". Accept either.
TTS_OUTPUT_MODALITIES = frozenset({"audio", "speech"})
# `/audio/speech` returns pcm as 16-bit little-endian samples and names the rate and channel
# count in the Content-Type. The defaults apply when the header omits them and to the
# `pcm16` audio that OpenAI chat models stream, which carries no rate or channel count.
PCM_MEDIA_TYPES = frozenset({"audio/pcm", "audio/l16"})
PCM_SAMPLE_WIDTH_BYTES = 2
PCM_DEFAULT_SAMPLE_RATE = 24000
PCM_DEFAULT_CHANNELS = 1
# OpenAI's audio chat models (gpt-audio, gpt-audio-mini) stream audio only as `pcm16`
# (24 kHz mono 16-bit little-endian, no Content-Type per chunk) and require a voice; their
# catalog entries list no voices.
CHAT_AUDIO_PCM16_MODEL_PREFIXES = ("openai/",)
# Chat-audio models that speak a given text and so get the read-aloud system message.
# Lyria, the other `audio` output family, generates music from a prompt and does not.
CHAT_AUDIO_READ_ALOUD_MODEL_PREFIXES = ("openai/",)
CHAT_AUDIO_DEFAULT_VOICES = {"openai/": "alloy"}
# Upload limit used when neither the interaction nor the guild reports one.
DEFAULT_UPLOAD_LIMIT_BYTES = 10 * 1024 * 1024
SPEECH_MEDIA_TYPE_FORMATS = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/aac": "aac",
    "audio/x-aac": "aac",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}
STT_AUDIO_FORMAT_ALIASES = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/aiff": "aiff",
    "audio/x-aiff": "aiff",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/mp4": "mp4",
    "video/mp4": "mp4",
    "audio/webm": "webm",
    "audio/m4a": "m4a",
}


async def run_tts_command(
    cog,
    *,
    ctx: ApplicationContext,
    input_text: str,
    model: str | None = None,
    voice: str | None = None,
    instructions: str | None = None,
    response_format: str | None = None,
) -> None:
    await ctx.defer()
    chosen_format = response_format
    response_format = response_format or DEFAULT_TTS_RESPONSE_FORMAT

    if len(input_text) > TTS_MAX_CHARS:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(
                f"Text exceeds the {TTS_MAX_CHARS:,} character limit ({len(input_text):,} characters provided)."
            ),
            logger=cog.logger,
        )
        return

    channel_id = ctx.channel.id if ctx.channel is not None else 0
    resolved_model = (
        model
        or cog.channel_model_defaults.get((channel_id, ctx.author.id, "tts"))
        or OPENROUTER_DEFAULT_TTS_MODEL
    ).strip()
    if not resolved_model:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("No TTS model is configured for this bot."),
            logger=cog.logger,
        )
        return
    normalized_voice = (voice or "").strip() or None

    try:
        model_info = await cog.openrouter_client.get_model(resolved_model)
    except OpenRouterApiError as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return

    if model_info is not None and TTS_OUTPUT_MODALITIES.isdisjoint(model_info.output_modalities):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(
                f"`{model_info.id}` does not advertise audio output in the OpenRouter catalog."
            ),
            logger=cog.logger,
        )
        return

    try:
        if model_info is not None and _uses_speech_endpoint(model_info):
            result = await _synthesize_with_speech_endpoint(
                cog,
                ctx=ctx,
                model_info=model_info,
                input_text=input_text,
                voice=normalized_voice,
                instructions=instructions,
                response_format=response_format,
            )
        else:
            result = await _synthesize_with_chat_audio(
                cog,
                ctx=ctx,
                model_info=model_info,
                resolved_model=resolved_model,
                input_text=input_text,
                voice=normalized_voice,
                instructions=instructions,
                response_format=response_format,
            )
    except (OpenRouterApiError, TtsOutputError) as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return
    except Exception as error:
        cog.logger.error("TTS generation failed: %s", error, exc_info=True)
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return

    usage = result.usage
    request_cost = result.request_cost
    daily_cost = track_daily_cost(cog, ctx.author.id, request_cost)

    cog.logger.info(
        "COST | command=tts | user=%s | model=%s | chars=%s | prompt_tokens=%s"
        " | completion_tokens=%s | cost=%s | daily=%s",
        ctx.author.id,
        result.model,
        len(input_text),
        usage.prompt_tokens,
        usage.completion_tokens,
        f"${request_cost:.6f}" if request_cost is not None else "unknown",
        f"${daily_cost:.6f}" if daily_cost is not None else "unknown",
    )

    file_format = result.file_format
    format_line = (
        f"{file_format} (requested {chosen_format})"
        if chosen_format and file_format != chosen_format
        else file_format
    )
    description = (
        f"**Text:** {truncate_text(input_text, 1500)}\n"
        f"**Model:** `{result.model}`\n"
        f"**Voice:** {result.voice or 'model/provider default'}\n"
        + (f"**Instructions:** {truncate_text(instructions, 500)}\n" if instructions else "")
        + f"**Response Format:** {format_line}\n"
        + (
            f"**Transcript:** {truncate_text(result.transcript, 500)}\n"
            if result.transcript
            else ""
        )
    )
    embeds = [
        Embed(
            title="Text-to-Speech Generation",
            description=description,
            color=Colour.blue(),
        )
    ]
    if SHOW_COST_EMBEDS:
        append_flat_pricing_embed(
            embeds,
            request_cost=request_cost,
            daily_cost=daily_cost,
            details=[
                "cost unavailable" if request_cost is None else "",
                count_label(len(input_text), "char"),
                result.voice or "default voice",
            ],
            request_cost_is_estimate=result.request_cost_is_estimate,
            usage=usage,
        )

    extension = "ogg" if file_format == "opus" else file_format
    upload_limit = _upload_limit_bytes(ctx)
    if len(result.audio_bytes) <= upload_limit:
        await send_embed_batches(
            ctx.followup.send,
            embeds=embeds,
            file=File(io.BytesIO(result.audio_bytes), f"speech.{extension}"),
            logger=cog.logger,
        )
        return

    parts = _split_wav(result.audio_bytes, upload_limit) if file_format == "wav" else None
    if not parts:
        embeds.append(
            error_embed(
                f"The generated {file_format} audio is {_format_mib(len(result.audio_bytes))},"
                f" above the {_format_mib(upload_limit)} upload limit here, and only WAV"
                " audio can be split into parts. Try shorter text or the WAV format."
            )
        )
        await send_embed_batches(ctx.followup.send, embeds=embeds, logger=cog.logger)
        return

    # One part per message, so no single upload is above the limit.
    for index, part in enumerate(parts, start=1):
        part_file = File(io.BytesIO(part), f"speech-part-{index}-of-{len(parts)}.wav")
        if index == 1:
            await send_embed_batches(
                ctx.followup.send, embeds=embeds, file=part_file, logger=cog.logger
            )
        else:
            await send_embed_batches(ctx.followup.send, file=part_file, logger=cog.logger)


def _upload_limit_bytes(ctx: ApplicationContext) -> int:
    """Return the upload limit for this reply.

    Discord sends the limit with each interaction (`attachment_size_limit`); the guild's
    `filesize_limit` and then `DEFAULT_UPLOAD_LIMIT_BYTES` are used when it is missing.
    """
    for limit in (
        getattr(getattr(ctx, "interaction", None), "attachment_size_limit", None),
        getattr(getattr(ctx, "guild", None), "filesize_limit", None),
    ):
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            return limit
    return DEFAULT_UPLOAD_LIMIT_BYTES


def _format_mib(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MiB"


def _split_wav(wav_bytes: bytes, max_part_bytes: int) -> list[bytes] | None:
    """Split a PCM WAV file into consecutive WAV files of at most `max_part_bytes` each.

    Parts are cut on whole frames and each one has its own header. Returns `None` when the
    file cannot be read as PCM WAV or the limit leaves no room for a frame.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            pcm = source.readframes(source.getnframes())
    except (wave.Error, EOFError):
        return None
    frame_size = channels * sample_width
    if frame_size <= 0:
        return None
    header_size = len(_pcm_to_wav(b"", sample_rate=sample_rate, channels=channels))
    frames_per_part = (max_part_bytes - header_size) // frame_size
    if frames_per_part <= 0:
        return None
    bytes_per_part = frames_per_part * frame_size
    parts: list[bytes] = []
    for offset in range(0, len(pcm), bytes_per_part):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as part:
            part.setnchannels(channels)
            part.setsampwidth(sample_width)
            part.setframerate(sample_rate)
            part.writeframes(pcm[offset : offset + bytes_per_part])
        parts.append(buffer.getvalue())
    return parts


class TtsOutputError(RuntimeError):
    """Raised when a TTS response holds no audio the bot can send."""


@dataclass(slots=True)
class _TtsResult:
    audio_bytes: bytes
    file_format: str
    model: str
    voice: str | None
    usage: ChatUsage
    request_cost: float | None
    request_cost_is_estimate: bool = False
    transcript: str = ""


def _uses_speech_endpoint(model_info: ModelInfo) -> bool:
    """Return whether the model is served by `/audio/speech` instead of chat completions.

    OpenRouter answers 400 on `/chat/completions` for every model whose catalog output is
    `speech`; the `audio` output models (gpt-audio, lyria) are chat models and stay on the
    streamed chat path.
    """
    outputs = {modality.casefold() for modality in model_info.output_modalities}
    return "speech" in outputs and "audio" not in outputs


async def _synthesize_with_speech_endpoint(
    cog,
    *,
    ctx: ApplicationContext,
    model_info: ModelInfo,
    input_text: str,
    voice: str | None,
    instructions: str | None,
    response_format: str,
) -> _TtsResult:
    # Some providers refuse a request without a voice, so a blank voice option sends the
    # first voice the catalog lists for the model.
    resolved_voice = voice or (
        model_info.supported_voices[0] if model_info.supported_voices else None
    )
    # `/audio/speech` offers only mp3 and pcm; every other choice is delivered as WAV.
    requested_format = "mp3" if response_format == "mp3" else "pcm"
    response_payload = await cog.openrouter_client.create_audio_speech(
        model=model_info.id,
        input_text=input_text,
        voice=resolved_voice,
        response_format=requested_format,
        instructions=instructions,
        user=build_safety_identifier(ctx.author.id),
        session_id=f"tts:{ctx.interaction.id}",
    )
    audio_bytes = response_payload.get("audio_bytes") or b""
    if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
        raise TtsOutputError("The model responded, but no audio data was returned.")
    file_bytes, file_format = _speech_audio_file(
        bytes(audio_bytes),
        content_type=response_payload.get("content_type"),
        requested_format=response_payload.get("response_format") or requested_format,
    )

    generation = None
    generation_id = response_payload.get("generation_id")
    if generation_id:
        generation = await cog.openrouter_client.get_generation(generation_id)
    usage = extract_generation_usage(generation) if generation else ChatUsage()
    return _TtsResult(
        audio_bytes=file_bytes,
        file_format=file_format,
        model=model_info.id,
        voice=resolved_voice,
        usage=usage,
        request_cost=resolve_request_cost(usage),
    )


async def _synthesize_with_chat_audio(
    cog,
    *,
    ctx: ApplicationContext,
    model_info: ModelInfo | None,
    resolved_model: str,
    input_text: str,
    voice: str | None,
    instructions: str | None,
    response_format: str,
) -> _TtsResult:
    model_id = model_info.id if model_info is not None else resolved_model
    resolved_voice = (
        voice
        or (model_info.supported_voices[0] if model_info and model_info.supported_voices else None)
        or _chat_audio_default_voice(model_id)
    )
    streams_pcm16 = model_id.startswith(CHAT_AUDIO_PCM16_MODEL_PREFIXES)
    response_payload = await cog.openrouter_client.create_speech(
        model=model_id,
        input_text=input_text,
        voice=resolved_voice,
        response_format="pcm16" if streams_pcm16 else response_format,
        modalities=_resolve_audio_modalities(model_info),
        instructions=instructions,
        user=build_safety_identifier(ctx.author.id),
        session_id=f"tts:{ctx.interaction.id}",
        read_aloud=model_id.startswith(CHAT_AUDIO_READ_ALOUD_MODEL_PREFIXES),
    )
    audio_bytes = response_payload.get("audio_bytes") or b""
    if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
        raise TtsOutputError("The model responded, but no audio data was returned in the stream.")
    file_bytes, file_format = bytes(audio_bytes), response_format
    if streams_pcm16:
        file_bytes = _pcm_to_wav(
            file_bytes, sample_rate=PCM_DEFAULT_SAMPLE_RATE, channels=PCM_DEFAULT_CHANNELS
        )
        file_format = "wav"

    usage = extract_usage({"usage": response_payload.get("usage") or {}})
    reported_cost = resolve_request_cost(usage)
    request_cost = reported_cost if reported_cost is not None else calculate_cost(model_info, usage)
    return _TtsResult(
        audio_bytes=file_bytes,
        file_format=file_format,
        model=response_payload.get("model") or model_id,
        voice=resolved_voice,
        usage=usage,
        request_cost=request_cost,
        request_cost_is_estimate=reported_cost is None and request_cost is not None,
        transcript=_resolve_transcript(response_payload),
    )


def _chat_audio_default_voice(model_id: str) -> str | None:
    for prefix, default_voice in CHAT_AUDIO_DEFAULT_VOICES.items():
        if model_id.startswith(prefix):
            return default_voice
    return None


def _speech_audio_file(
    audio_bytes: bytes, *, content_type: str | None, requested_format: str
) -> tuple[bytes, str]:
    """Return the bytes to upload and their format for a `/audio/speech` response.

    Raw PCM (`audio/pcm;rate=24000;channels=1`, 16-bit little-endian) is wrapped in a WAV
    header so Discord can play it; `audio/L16` is the same samples in big-endian order. A
    missing or unrecognised Content-Type falls back to the requested format, so audio that
    has already been paid for is still sent.
    """
    media_type, params = _parse_content_type(content_type)
    if media_type and media_type not in PCM_MEDIA_TYPES | SPEECH_MEDIA_TYPE_FORMATS.keys():
        logger.warning(
            "Unexpected TTS Content-Type %r; treating the audio as the requested %s",
            content_type,
            requested_format,
        )
        media_type = ""
    if not media_type:
        media_type = "audio/mpeg" if requested_format == "mp3" else "audio/pcm"
    if media_type in PCM_MEDIA_TYPES:
        sample_rate = _positive_int(params.get("rate"), PCM_DEFAULT_SAMPLE_RATE)
        channels = _positive_int(params.get("channels"), PCM_DEFAULT_CHANNELS)
        pcm = _swap_16_bit_byte_order(audio_bytes) if media_type == "audio/l16" else audio_bytes
        return _pcm_to_wav(pcm, sample_rate=sample_rate, channels=channels), "wav"
    return audio_bytes, SPEECH_MEDIA_TYPE_FORMATS[media_type]


def _swap_16_bit_byte_order(pcm_bytes: bytes) -> bytes:
    samples = array.array("h")
    samples.frombytes(pcm_bytes[: len(pcm_bytes) - len(pcm_bytes) % 2])
    samples.byteswap()
    return samples.tobytes()


def _parse_content_type(content_type: str | None) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in (content_type or "").split(";")]
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, separator, value = part.partition("=")
        if separator:
            params[key.strip().casefold()] = value.strip().strip('"')
    return parts[0].casefold(), params


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _pcm_to_wav(pcm_bytes: bytes, *, sample_rate: int, channels: int) -> bytes:
    frame_size = PCM_SAMPLE_WIDTH_BYTES * channels
    usable_length = len(pcm_bytes) - len(pcm_bytes) % frame_size
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes[:usable_length])
    return buffer.getvalue()


def _resolve_transcript(response_payload: dict) -> str:
    transcript = response_payload.get("transcript")
    if isinstance(transcript, str) and transcript.strip():
        return transcript.strip()
    text = response_payload.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _resolve_audio_modalities(model_info) -> list[str]:
    if model_info is not None and "text" not in model_info.output_modalities:
        return ["audio"]
    return ["text", "audio"]


async def run_stt_command(
    cog,
    *,
    ctx: ApplicationContext,
    attachment: Attachment,
    model: str | None = None,
    instructions: str | None = None,
) -> None:
    await ctx.defer()

    if not _is_audio_attachment(attachment):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(
                "Attachment must be an audio file. Supports mp3, mp4, m4a, wav, webm, ogg, flac, aiff, and aac."
            ),
            logger=cog.logger,
        )
        return

    channel_id = ctx.channel.id if ctx.channel is not None else 0
    resolved_model = (
        model
        or cog.channel_model_defaults.get((channel_id, ctx.author.id, "stt"))
        or OPENROUTER_DEFAULT_STT_MODEL
    ).strip()
    if not resolved_model:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("No STT model is configured for this bot."),
            logger=cog.logger,
        )
        return

    try:
        model_info = await cog.openrouter_client.get_model(resolved_model)
    except OpenRouterApiError as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return

    if model_info is not None and "audio" not in model_info.input_modalities:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(
                f"`{model_info.id}` does not advertise audio input in the OpenRouter catalog."
            ),
            logger=cog.logger,
        )
        return
    if model_info is not None and "text" not in model_info.output_modalities:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(
                f"`{model_info.id}` does not advertise text output in the OpenRouter catalog."
            ),
            logger=cog.logger,
        )
        return

    try:
        attachment_parts = [await _build_stt_attachment_part(attachment)]
    except AttachmentInputError as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return
    except Exception as error:
        cog.logger.error("Failed to normalize STT attachment: %s", error, exc_info=True)
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("Failed to process the provided audio attachment."),
            logger=cog.logger,
        )
        return

    user_content = build_user_content(_build_stt_prompt(instructions), attachment_parts)

    try:
        response_payload = await cog.openrouter_client.create_chat_completion(
            model=resolved_model,
            messages=[{"role": "user", "content": user_content}],
            user=build_safety_identifier(ctx.author.id),
            session_id=f"stt:{ctx.interaction.id}",
        )
    except OpenRouterApiError as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return
    except Exception as error:
        cog.logger.error("STT generation failed: %s", error, exc_info=True)
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return

    choice = (response_payload.get("choices") or [None])[0]
    if not isinstance(choice, dict):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("OpenRouter returned no choices for this STT request."),
            logger=cog.logger,
        )
        return

    message_payload = choice.get("message") or {}
    if not isinstance(message_payload, dict):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("OpenRouter returned an unexpected STT response message."),
            logger=cog.logger,
        )
        return

    assistant_message = sanitize_assistant_message(message_payload)
    transcript = extract_message_text(assistant_message)
    if not transcript:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("The model responded, but no transcript text was returned."),
            logger=cog.logger,
        )
        return

    usage = extract_usage(response_payload)
    reported_cost = resolve_request_cost(usage)
    request_cost = reported_cost if reported_cost is not None else calculate_cost(model_info, usage)
    daily_cost = track_daily_cost(cog, ctx.author.id, request_cost)

    cog.logger.info(
        "COST | command=stt | user=%s | model=%s | file=%s | prompt_tokens=%s"
        " | completion_tokens=%s | cost=%s | daily=%s",
        ctx.author.id,
        model_info.id if model_info is not None else resolved_model,
        attachment.filename,
        usage.prompt_tokens,
        usage.completion_tokens,
        f"${request_cost:.6f}" if request_cost is not None else "unknown",
        f"${daily_cost:.6f}" if daily_cost is not None else "unknown",
    )

    description = (
        f"**Attachment:** {attachment.filename}\n"
        f"**Model:** `{model_info.id if model_info is not None else resolved_model}`\n"
        + (f"**Instructions:** {truncate_text(instructions, 500)}\n" if instructions else "")
        + f"**Transcript:**\n{truncate_text(transcript, 3000)}"
    )
    embeds = [
        Embed(
            title="Speech-to-Text",
            description=description,
            color=Colour.blue(),
        )
    ]
    if SHOW_COST_EMBEDS:
        append_flat_pricing_embed(
            embeds,
            request_cost=request_cost,
            daily_cost=daily_cost,
            details=[attachment.filename],
            request_cost_is_estimate=reported_cost is None and request_cost is not None,
            usage=usage,
        )

    await send_embed_batches(ctx.followup.send, embeds=embeds, logger=cog.logger)


def _build_stt_prompt(instructions: str | None) -> str:
    normalized_instructions = (instructions or "").strip()
    base_prompt = "Transcribe this audio accurately and return only the transcript."
    if not normalized_instructions:
        return base_prompt
    return f"{base_prompt}\nAdditional instructions: {normalized_instructions}"


def _is_audio_attachment(attachment: Attachment) -> bool:
    content_type = (attachment.content_type or "").split(";", 1)[0].strip().lower()
    if content_type:
        return content_type.startswith("audio/") or content_type in {
            "video/mp4",
            "audio/mp4",
            "application/octet-stream",
        }
    filename = attachment.filename.lower()
    return filename.endswith(
        (
            ".mp3",
            ".mp4",
            ".mpeg",
            ".mpga",
            ".m4a",
            ".wav",
            ".webm",
            ".ogg",
            ".flac",
            ".aiff",
            ".aif",
            ".aac",
            ".pcm16",
            ".pcm24",
        )
    )


async def _build_stt_attachment_part(attachment: Attachment) -> dict:
    if attachment.size and attachment.size > MAX_ATTACHMENT_SIZE:
        raise AttachmentInputError(
            f"Attachment `{attachment.filename}` exceeds the 20 MiB limit supported by this bot."
        )
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(attachment.url)
    response.raise_for_status()
    return {
        "type": "input_audio",
        "input_audio": {
            "data": base64.b64encode(response.content).decode("ascii"),
            "format": _audio_format_for_stt(attachment),
        },
    }


def _audio_format_for_stt(attachment: Attachment) -> str:
    content_type = (attachment.content_type or "").split(";", 1)[0].strip().lower()
    if content_type in STT_AUDIO_FORMAT_ALIASES:
        return STT_AUDIO_FORMAT_ALIASES[content_type]
    if "." in attachment.filename:
        extension = attachment.filename.rsplit(".", 1)[-1].lower()
        if extension in {"aif", "aifc"}:
            return "aiff"
        if extension in {"m4a", "aac", "aiff", "pcm16", "pcm24"}:
            return extension
        return extension
    return "mp3"


__all__ = ["run_stt_command", "run_tts_command"]
