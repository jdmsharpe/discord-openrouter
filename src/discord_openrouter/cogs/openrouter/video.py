from __future__ import annotations

import asyncio
import io
import math
import mimetypes
import time
from typing import Any
from urllib.parse import urlparse

from discord import ApplicationContext, Attachment, Colour, Embed, File, HTTPException

from ...config import OPENROUTER_DEFAULT_VIDEO_MODEL, SHOW_COST_EMBEDS
from ...cost_line import count_label
from ...util import extract_usage, resolve_request_cost
from .attachments import AttachmentInputError, build_attachment_parts, upload_limit_bytes
from .client import OpenRouterApiError
from .embed_delivery import DISCORD_EMBED_TOTAL_LIMIT, count_embed_chars, send_embed_batches
from .embeds import append_flat_pricing_embed, error_embed
from .state import track_daily_cost

VIDEO_GENERATION_TIMEOUT_SECONDS = 600
VIDEO_POLL_INTERVAL_SECONDS = 15
# Statuses after which a job no longer changes (OpenRouter's VideoGenerationResponse
# `status` enum is pending, in_progress, completed, failed, cancelled, expired).
VIDEO_ENDED_WITHOUT_VIDEO_STATUSES = frozenset({"cancelled", "expired"})
VIDEO_TERMINAL_STATUSES = frozenset({"completed", "failed"}) | VIDEO_ENDED_WITHOUT_VIDEO_STATUSES
VIDEO_DOWNLOAD_LINK_LIMIT = 10
EMBED_FIELD_VALUE_LIMIT = 1024


async def run_video_command(
    cog,
    *,
    ctx: ApplicationContext,
    prompt: str,
    model: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    size: str | None = None,
    attachment: Attachment | None = None,
    duration: int | None = None,
    generate_audio: bool | None = None,
    seed: int | None = None,
) -> None:
    await ctx.defer()

    channel_id = ctx.channel.id if ctx.channel is not None else 0
    resolved_model = (
        model
        or cog.channel_model_defaults.get((channel_id, ctx.author.id, "video"))
        or OPENROUTER_DEFAULT_VIDEO_MODEL
    ).strip()
    if not resolved_model:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("No video model is configured for this bot."),
            logger=cog.logger,
        )
        return

    if size and (aspect_ratio or resolution):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("Use either `size` or `resolution`/`aspect_ratio`, not both."),
            logger=cog.logger,
        )
        return

    if attachment is not None and not _is_image_attachment(attachment):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(
                "Only image attachments can be used as `/openrouter-media video` references."
            ),
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

    modality_error = _validate_video_model_modalities(
        model_info,
        requires_image_input=attachment is not None,
    )
    if modality_error:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(modality_error),
            logger=cog.logger,
        )
        return

    input_references: list[dict[str, Any]] | None = None
    if attachment is not None:
        try:
            attachment_parts = await build_attachment_parts([attachment])
        except AttachmentInputError as error:
            await send_embed_batches(
                ctx.followup.send,
                embed=error_embed(str(error)),
                logger=cog.logger,
            )
            return
        except Exception as error:
            cog.logger.error(
                "Failed to normalize video reference attachment: %s", error, exc_info=True
            )
            await send_embed_batches(
                ctx.followup.send,
                embed=error_embed("Failed to process the provided image attachment."),
                logger=cog.logger,
            )
            return

        input_references = [
            part
            for part in attachment_parts
            if isinstance(part, dict) and str(part.get("type") or "").strip().lower() == "image_url"
        ]
        if not input_references:
            await send_embed_batches(
                ctx.followup.send,
                embed=error_embed("The reference attachment must be an image."),
                logger=cog.logger,
            )
            return

    try:
        submit_response = await cog.openrouter_client.create_video_generation(
            model=resolved_model,
            prompt=prompt,
            duration=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            size=size,
            input_references=input_references,
            generate_audio=generate_audio,
            seed=seed,
        )
        status_response = await _poll_until_complete(
            cog,
            job_id=_coerce_str(submit_response.get("id")),
            polling_url=_coerce_str(submit_response.get("polling_url")),
            initial_status=_coerce_str(submit_response.get("status")) or "pending",
        )
    except TimeoutError as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return
    except OpenRouterApiError as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return
    except Exception as error:
        cog.logger.error("Video generation failed: %s", error, exc_info=True)
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return

    unsigned_urls = _url_list(status_response.get("unsigned_urls"))
    if not unsigned_urls:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(
                "The video job completed, but no downloadable video URLs were returned."
            ),
            logger=cog.logger,
        )
        return

    job_id = (
        _coerce_str(status_response.get("id")) or _coerce_str(submit_response.get("id")) or "video"
    )
    video_assets = await _download_video_assets(cog, unsigned_urls, job_id=job_id)
    request_cost = resolve_request_cost(extract_usage(status_response))
    daily_cost = track_daily_cost(cog, ctx.author.id, request_cost)

    cog.logger.info(
        "COST | command=video | user=%s | model=%s | outputs=%s | cost=%s | daily=%s",
        ctx.author.id,
        resolved_model,
        len(unsigned_urls),
        f"${request_cost:.6f}" if request_cost is not None else "unknown",
        f"${daily_cost:.6f}" if daily_cost is not None else "unknown",
    )

    embed = Embed(
        title="Video Generation",
        description=_build_video_description(
            prompt=prompt,
            model=model_info.id if model_info is not None else resolved_model,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            size=size,
            duration=duration,
            generate_audio=generate_audio,
            seed=seed,
            output_count=len(unsigned_urls),
            used_reference_image=attachment is not None,
        ),
        color=Colour.blue(),
    )
    embeds = [embed]
    if SHOW_COST_EMBEDS:
        append_flat_pricing_embed(
            embeds,
            request_cost=request_cost,
            daily_cost=daily_cost,
            details=_build_pricing_details(
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                size=size,
                output_count=len(unsigned_urls),
            ),
        )

    upload_limit = upload_limit_bytes(ctx)
    oversized = [
        (filename, len(video_bytes))
        for filename, video_bytes in video_assets
        if len(video_bytes) > upload_limit
    ]
    if oversized:
        cog.logger.info(
            "Generated video is above the %s-byte upload limit; posting download links: %s",
            upload_limit,
            oversized,
        )
    elif video_assets:
        files = [
            File(io.BytesIO(video_bytes), filename=filename)
            for filename, video_bytes in video_assets
        ]
        # A direct send, so a rejected upload raises here and the links below are posted.
        try:
            await ctx.followup.send(embeds=embeds, files=files)
            return
        except HTTPException as error:
            cog.logger.warning(
                "Failed to upload generated videos to Discord: %s", error, exc_info=True
            )

    _add_download_fields(embeds, _build_download_fields(status_response))
    await send_embed_batches(ctx.followup.send, embeds=embeds, logger=cog.logger)


async def _poll_until_complete(
    cog,
    *,
    job_id: str | None,
    polling_url: str | None,
    initial_status: str,
) -> dict[str, Any]:
    status = initial_status.strip().lower()
    start_time = time.monotonic()
    latest_payload: dict[str, Any] = {
        "id": job_id,
        "polling_url": polling_url,
        "status": status,
    }

    while status not in VIDEO_TERMINAL_STATUSES:
        if time.monotonic() - start_time > VIDEO_GENERATION_TIMEOUT_SECONDS:
            raise TimeoutError("Video generation timed out after 10 minutes.")
        await asyncio.sleep(VIDEO_POLL_INTERVAL_SECONDS)
        latest_payload = await cog.openrouter_client.get_video_generation(
            job_id=job_id,
            polling_url=polling_url,
        )
        status = _coerce_str(latest_payload.get("status")).strip().lower() or "pending"

    if status == "failed":
        error_message = _coerce_str(latest_payload.get("error")) or "Video generation failed."
        raise OpenRouterApiError(error_message)
    if status in VIDEO_ENDED_WITHOUT_VIDEO_STATUSES:
        error_message = f"The video job ended with status `{status}`; no video was returned."
        details = _coerce_str(latest_payload.get("error"))
        if details:
            error_message = f"{error_message} {details}"
        raise OpenRouterApiError(error_message)
    return latest_payload


async def _download_video_assets(
    cog,
    urls: list[str],
    *,
    job_id: str,
) -> list[tuple[str, bytes]]:
    assets: list[tuple[str, bytes]] = []
    for index, url in enumerate(urls, start=1):
        try:
            video_bytes, content_type = await cog.openrouter_client.download_file_bytes(url)
        except Exception as error:
            cog.logger.warning(
                "Failed to download generated video %s: %s", index, error, exc_info=True
            )
            continue
        filename = f"{job_id}_{index}.{_guess_video_extension(url, content_type)}"
        assets.append((filename, video_bytes))
    return assets


def _url_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [url.strip() for url in value if isinstance(url, str) and url.strip()]


def _build_download_fields(status_response: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the embed fields that link to the videos when they could not be attached.

    OpenRouter's signed `urls` work without an API key until `expires_at`, so anyone who
    can see the message can download the video. `unsigned_urls` need the API key in the
    `Authorization` header, so they are posted only when `urls` is absent or empty.
    """
    signed_urls = _url_list(status_response.get("urls"))
    if signed_urls:
        urls = signed_urls
        expires_at = _coerce_unix_seconds(status_response.get("expires_at"))
        if expires_at is not None:
            note = (
                "Anyone who can see this message can download the video from these links "
                f"until <t:{expires_at}:f> (<t:{expires_at}:R>)."
            )
        else:
            note = (
                "Anyone who can see this message can download the video from these links "
                "until OpenRouter deletes its stored copy."
            )
    else:
        urls = _url_list(status_response.get("unsigned_urls"))
        note = (
            "These links need an OpenRouter API key in the `Authorization` header; "
            "opening one in a browser returns an authentication error."
        )
    if not urls:
        return []

    lines = [
        f"{index}. [Video {index}]({url})"
        for index, url in enumerate(urls[:VIDEO_DOWNLOAD_LINK_LIMIT], start=1)
    ]
    fields = [
        ("Downloads" if index == 0 else "Downloads (continued)", value)
        for index, value in enumerate(_pack_field_values(lines))
    ]
    fields.append(("Link access", note))
    return fields


def _add_download_fields(embeds: list[Embed], fields: list[tuple[str, str]]) -> None:
    """Add the link fields to the video embed, keeping every embed within Discord's limit.

    A field that would take an embed past `DISCORD_EMBED_TOTAL_LIMIT` characters starts a
    new embed, placed after the ones already holding links and before the cost embed.
    `send_embed_batches` then splits the embeds into messages that fit.
    """
    target = embeds[0]
    position = 1
    for name, value in fields:
        if count_embed_chars(target) + len(name) + len(value) > DISCORD_EMBED_TOTAL_LIMIT:
            target = Embed(color=Colour.blue())
            embeds.insert(position, target)
            position += 1
        target.add_field(name=name, value=value, inline=False)


def _pack_field_values(lines: list[str]) -> list[str]:
    """Join lines into values that each fit in one embed field."""
    values: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate) > EMBED_FIELD_VALUE_LIMIT:
            values.append(current)
            current = line
        else:
            current = candidate
    if current:
        values.append(current)
    return values


def _coerce_unix_seconds(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    seconds = int(value)
    return seconds if seconds > 0 else None


def _is_image_attachment(attachment: Attachment) -> bool:
    content_type = (attachment.content_type or "").split(";", 1)[0].strip().lower()
    if content_type:
        return content_type.startswith("image/")
    filename = attachment.filename.lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def _validate_video_model_modalities(model_info, *, requires_image_input: bool) -> str | None:
    if model_info is None:
        return None
    if "video" not in model_info.output_modalities:
        return f"`{model_info.id}` does not advertise video output in the OpenRouter catalog."
    if requires_image_input and "image" not in model_info.input_modalities:
        return (
            f"`{model_info.id}` does not advertise image input in the OpenRouter catalog "
            "for reference-image video generation."
        )
    return None


def _build_video_description(
    *,
    prompt: str,
    model: str,
    aspect_ratio: str | None,
    resolution: str | None,
    size: str | None,
    duration: int | None,
    generate_audio: bool | None,
    seed: int | None,
    output_count: int,
    used_reference_image: bool,
) -> str:
    lines = [
        f"**Prompt:** {prompt[:1500] if len(prompt) <= 1500 else prompt[:1497] + '...'}",
        f"**Model:** `{model}`",
        f"**Mode:** {'Image-to-Video' if used_reference_image else 'Text-to-Video'}",
        f"**Outputs:** {output_count}",
    ]
    if aspect_ratio:
        lines.append(f"**Aspect Ratio:** {aspect_ratio}")
    if resolution:
        lines.append(f"**Resolution:** {resolution}")
    if size:
        lines.append(f"**Size:** {size}")
    if duration is not None:
        lines.append(f"**Duration:** {duration} seconds")
    if generate_audio is not None:
        lines.append(f"**Audio:** {'Enabled' if generate_audio else 'Disabled'}")
    if seed is not None:
        lines.append(f"**Seed:** {seed}")
    return "\n".join(lines)


def _build_pricing_details(
    *,
    aspect_ratio: str | None,
    resolution: str | None,
    size: str | None,
    output_count: int,
) -> list[str]:
    details = [count_label(output_count, "video")]
    if resolution:
        details.append(resolution)
    if aspect_ratio:
        details.append(aspect_ratio)
    if size:
        details.append(size)
    return details


def _guess_video_extension(url: str, content_type: str | None) -> str:
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type:
        guessed_extension = mimetypes.guess_extension(normalized_content_type) or ""
        if guessed_extension:
            return guessed_extension.lstrip(".")

    path = urlparse(url).path
    if "." in path:
        return path.rsplit(".", 1)[-1].lower()
    return "mp4"


def _coerce_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = ["run_video_command"]
