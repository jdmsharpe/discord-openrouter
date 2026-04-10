from __future__ import annotations

import base64
import io

import httpx
from discord import ApplicationContext, Attachment, Colour, Embed, File

from ...config import OPENROUTER_DEFAULT_STT_MODEL, OPENROUTER_DEFAULT_TTS_MODEL, SHOW_COST_EMBEDS
from ...util import calculate_cost, extract_message_text, extract_usage, sanitize_assistant_message, truncate_text
from .attachments import AttachmentInputError, MAX_ATTACHMENT_SIZE, build_user_content
from .client import OpenRouterApiError
from .embeds import append_flat_pricing_embed, error_embed
from .state import track_daily_cost

TTS_MAX_CHARS = 4096
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
    response_format: str = "mp3",
) -> None:
    await ctx.defer()

    if len(input_text) > TTS_MAX_CHARS:
        await ctx.followup.send(
            embed=error_embed(
                f"Text exceeds the {TTS_MAX_CHARS:,} character limit ({len(input_text):,} characters provided)."
            )
        )
        return

    resolved_model = (model or OPENROUTER_DEFAULT_TTS_MODEL).strip()
    if not resolved_model:
        await ctx.followup.send(embed=error_embed("No TTS model is configured for this bot."))
        return
    normalized_voice = (voice or "").strip() or None

    try:
        model_info = await cog.openrouter_client.get_model(resolved_model)
    except OpenRouterApiError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return

    if model_info is not None and "audio" not in model_info.output_modalities:
        await ctx.followup.send(
            embed=error_embed(
                f"`{model_info.id}` does not advertise audio output in the OpenRouter catalog."
            )
        )
        return

    try:
        response_payload = await cog.openrouter_client.create_speech(
            model=resolved_model,
            input_text=input_text,
            voice=normalized_voice,
            response_format=response_format,
            modalities=_resolve_audio_modalities(model_info),
            instructions=instructions,
            user=str(ctx.author.id),
            session_id=f"tts:{ctx.interaction.id}",
        )
    except OpenRouterApiError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return
    except Exception as error:
        cog.logger.error("TTS generation failed: %s", error, exc_info=True)
        await ctx.followup.send(embed=error_embed(str(error)))
        return

    audio_bytes = response_payload.get("audio_bytes") or b""
    if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
        await ctx.followup.send(
            embed=error_embed("The model responded, but no audio data was returned in the stream.")
        )
        return

    usage = extract_usage({"usage": response_payload.get("usage") or {}})
    request_cost = usage.cost if usage.cost is not None else calculate_cost(model_info, usage)
    daily_cost = track_daily_cost(cog, ctx.author.id, request_cost)
    transcript = _resolve_transcript(response_payload)
    actual_model = response_payload.get("model") or (model_info.id if model_info is not None else resolved_model)

    cog.logger.info(
        "COST | command=tts | user=%s | model=%s | chars=%s | prompt_tokens=%s"
        " | completion_tokens=%s | cost=%s | daily=%s",
        ctx.author.id,
        actual_model,
        len(input_text),
        usage.prompt_tokens,
        usage.completion_tokens,
        f"${request_cost:.6f}" if request_cost is not None else "unknown",
        f"${daily_cost:.6f}" if daily_cost is not None else "unknown",
    )

    description = (
        f"**Text:** {truncate_text(input_text, 1500)}\n"
        f"**Model:** `{actual_model}`\n"
        f"**Voice:** {normalized_voice or 'model/provider default'}\n"
        + (
            f"**Instructions:** {truncate_text(instructions, 500)}\n"
            if instructions
            else ""
        )
        + f"**Response Format:** {response_format}\n"
        + (f"**Transcript:** {truncate_text(transcript, 500)}\n" if transcript else "")
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
            details=f"{len(input_text):,} chars · {normalized_voice or 'default voice'}",
            request_cost_is_estimate=usage.cost is None and request_cost is not None,
        )

    extension = "ogg" if response_format == "opus" else response_format
    await ctx.followup.send(
        embeds=embeds,
        file=File(io.BytesIO(bytes(audio_bytes)), f"speech.{extension}"),
    )


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
        await ctx.followup.send(
            embed=error_embed(
                "Attachment must be an audio file. Supports mp3, mp4, m4a, wav, webm, ogg, flac, aiff, and aac."
            )
        )
        return

    resolved_model = (model or OPENROUTER_DEFAULT_STT_MODEL).strip()
    if not resolved_model:
        await ctx.followup.send(embed=error_embed("No STT model is configured for this bot."))
        return

    try:
        model_info = await cog.openrouter_client.get_model(resolved_model)
    except OpenRouterApiError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return

    if model_info is not None and "audio" not in model_info.input_modalities:
        await ctx.followup.send(
            embed=error_embed(
                f"`{model_info.id}` does not advertise audio input in the OpenRouter catalog."
            )
        )
        return
    if model_info is not None and "text" not in model_info.output_modalities:
        await ctx.followup.send(
            embed=error_embed(
                f"`{model_info.id}` does not advertise text output in the OpenRouter catalog."
            )
        )
        return

    try:
        attachment_parts = [await _build_stt_attachment_part(attachment)]
    except AttachmentInputError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return
    except Exception as error:
        cog.logger.error("Failed to normalize STT attachment: %s", error, exc_info=True)
        await ctx.followup.send(embed=error_embed("Failed to process the provided audio attachment."))
        return

    user_content = build_user_content(_build_stt_prompt(instructions), attachment_parts)

    try:
        response_payload = await cog.openrouter_client.create_chat_completion(
            model=resolved_model,
            messages=[{"role": "user", "content": user_content}],
            user=str(ctx.author.id),
            session_id=f"stt:{ctx.interaction.id}",
        )
    except OpenRouterApiError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return
    except Exception as error:
        cog.logger.error("STT generation failed: %s", error, exc_info=True)
        await ctx.followup.send(embed=error_embed(str(error)))
        return

    choice = ((response_payload.get("choices") or [None])[0])
    if not isinstance(choice, dict):
        await ctx.followup.send(embed=error_embed("OpenRouter returned no choices for this STT request."))
        return

    message_payload = choice.get("message") or {}
    if not isinstance(message_payload, dict):
        await ctx.followup.send(
            embed=error_embed("OpenRouter returned an unexpected STT response message.")
        )
        return

    assistant_message = sanitize_assistant_message(message_payload)
    transcript = extract_message_text(assistant_message)
    if not transcript:
        await ctx.followup.send(
            embed=error_embed("The model responded, but no transcript text was returned.")
        )
        return

    usage = extract_usage(response_payload)
    request_cost = usage.cost if usage.cost is not None else calculate_cost(model_info, usage)
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
        + (
            f"**Instructions:** {truncate_text(instructions, 500)}\n"
            if instructions
            else ""
        )
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
            details=attachment.filename,
            request_cost_is_estimate=usage.cost is None and request_cost is not None,
        )

    await ctx.followup.send(embeds=embeds)


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
