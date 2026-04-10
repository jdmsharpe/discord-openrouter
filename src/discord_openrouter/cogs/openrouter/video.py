from __future__ import annotations

import asyncio
import io
import mimetypes
import time
from typing import Any
from urllib.parse import urlparse

from discord import ApplicationContext, Attachment, Colour, Embed, File, HTTPException

from ...config import OPENROUTER_DEFAULT_VIDEO_MODEL, SHOW_COST_EMBEDS
from .attachments import AttachmentInputError, build_attachment_parts
from .client import OpenRouterApiError
from .embeds import append_flat_pricing_embed, error_embed
from .state import track_daily_cost

VIDEO_GENERATION_TIMEOUT_SECONDS = 600
VIDEO_POLL_INTERVAL_SECONDS = 15


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

    resolved_model = (model or OPENROUTER_DEFAULT_VIDEO_MODEL).strip()
    if not resolved_model:
        await ctx.followup.send(embed=error_embed("No video model is configured for this bot."))
        return

    if size and (aspect_ratio or resolution):
        await ctx.followup.send(
            embed=error_embed("Use either `size` or `resolution`/`aspect_ratio`, not both."),
        )
        return

    if attachment is not None and not _is_image_attachment(attachment):
        await ctx.followup.send(
            embed=error_embed("Only image attachments can be used as `/openrouter video` references."),
        )
        return

    try:
        model_info = await cog.openrouter_client.get_model(resolved_model)
    except OpenRouterApiError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return

    modality_error = _validate_video_model_modalities(
        model_info,
        requires_image_input=attachment is not None,
    )
    if modality_error:
        await ctx.followup.send(embed=error_embed(modality_error))
        return

    input_references: list[dict[str, Any]] | None = None
    if attachment is not None:
        try:
            attachment_parts = await build_attachment_parts([attachment])
        except AttachmentInputError as error:
            await ctx.followup.send(embed=error_embed(str(error)))
            return
        except Exception as error:
            cog.logger.error("Failed to normalize video reference attachment: %s", error, exc_info=True)
            await ctx.followup.send(
                embed=error_embed("Failed to process the provided image attachment."),
            )
            return

        input_references = [
            part
            for part in attachment_parts
            if isinstance(part, dict) and str(part.get("type") or "").strip().lower() == "image_url"
        ]
        if not input_references:
            await ctx.followup.send(
                embed=error_embed("The reference attachment must be an image."),
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
        await ctx.followup.send(embed=error_embed(str(error)))
        return
    except OpenRouterApiError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return
    except Exception as error:
        cog.logger.error("Video generation failed: %s", error, exc_info=True)
        await ctx.followup.send(embed=error_embed(str(error)))
        return

    unsigned_urls = [
        url
        for url in (status_response.get("unsigned_urls") or [])
        if isinstance(url, str) and url.strip()
    ]
    if not unsigned_urls:
        await ctx.followup.send(
            embed=error_embed("The video job completed, but no downloadable video URLs were returned."),
        )
        return

    job_id = _coerce_str(status_response.get("id")) or _coerce_str(submit_response.get("id")) or "video"
    video_assets = await _download_video_assets(cog, unsigned_urls, job_id=job_id)
    request_cost = _safe_float_or_none((status_response.get("usage") or {}).get("cost"))
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

    files = [
        File(io.BytesIO(video_bytes), filename=filename)
        for filename, video_bytes in video_assets
    ]
    try:
        if files:
            await ctx.followup.send(embeds=embeds, files=files)
            return
    except HTTPException as error:
        cog.logger.warning("Failed to upload generated videos to Discord: %s", error, exc_info=True)

    download_links = "\n".join(
        f"{index}. [Video {index}]({url})" for index, url in enumerate(unsigned_urls[:10], start=1)
    )
    if download_links:
        embeds[0].add_field(name="Downloads", value=download_links, inline=False)
    await ctx.followup.send(embeds=embeds)


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

    while status not in {"completed", "failed"}:
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
            cog.logger.warning("Failed to download generated video %s: %s", index, error, exc_info=True)
            continue
        filename = f"{job_id}_{index}.{_guess_video_extension(url, content_type)}"
        assets.append((filename, video_bytes))
    return assets


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
) -> str:
    details = ["video generation", f"{output_count} output{'s' if output_count != 1 else ''}"]
    if resolution:
        details.append(resolution)
    if aspect_ratio:
        details.append(aspect_ratio)
    if size:
        details.append(size)
    return " · ".join(details)


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


def _safe_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["run_video_command"]
