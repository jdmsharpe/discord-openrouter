from __future__ import annotations

import base64
import io
from typing import Any

import httpx
from discord import ApplicationContext, Attachment, Colour, Embed, File

from ...config import OPENROUTER_DEFAULT_IMAGE_MODEL, SHOW_COST_EMBEDS
from ...util import (
    calculate_cost,
    extract_message_text,
    extract_usage,
    sanitize_assistant_message,
    truncate_text,
)
from .attachments import AttachmentInputError, build_attachment_parts, build_user_content
from .client import OpenRouterApiError
from .embed_delivery import send_embed_batches
from .embeds import append_flat_pricing_embed, error_embed
from .state import track_daily_cost


async def run_image_command(
    cog,
    *,
    ctx: ApplicationContext,
    prompt: str,
    model: str | None = None,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    attachment: Attachment | None = None,
) -> None:
    await ctx.defer()

    channel_id = ctx.channel.id if ctx.channel is not None else 0
    resolved_model = (
        model
        or cog.channel_model_defaults.get((channel_id, ctx.author.id, "image"))
        or OPENROUTER_DEFAULT_IMAGE_MODEL
    ).strip()
    if not resolved_model:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("No image model is configured for this bot."),
            logger=cog.logger,
        )
        return

    if attachment is not None and not _is_image_attachment(attachment):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("Only image attachments can be used with `/openrouter-media image`."),
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

    modality_error = _validate_image_model_modalities(
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

    try:
        attachment_parts = await build_attachment_parts([attachment] if attachment else [])
    except AttachmentInputError as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return
    except Exception as error:
        cog.logger.error("Failed to normalize image attachment: %s", error, exc_info=True)
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("Failed to process the provided image attachment."),
            logger=cog.logger,
        )
        return

    user_content = build_user_content(prompt, attachment_parts)
    if not user_content:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("Please provide a prompt or image attachment."),
            logger=cog.logger,
        )
        return

    image_config = _build_image_config(aspect_ratio=aspect_ratio, image_size=image_size)
    modalities = _resolve_image_modalities(model_info)
    mode = "Image Editing" if attachment is not None else "Image Generation"

    try:
        response_payload = await cog.openrouter_client.create_chat_completion(
            model=resolved_model,
            messages=[{"role": "user", "content": user_content}],
            modalities=modalities,
            image_config=image_config or None,
            user=str(ctx.author.id),
            session_id=f"image:{ctx.interaction.id}",
        )
    except OpenRouterApiError as error:
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return
    except Exception as error:
        cog.logger.error("Image generation failed: %s", error, exc_info=True)
        await send_embed_batches(
            ctx.followup.send, embed=error_embed(str(error)), logger=cog.logger
        )
        return

    choice = (response_payload.get("choices") or [None])[0]
    if not isinstance(choice, dict):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("OpenRouter returned no choices for this image request."),
            logger=cog.logger,
        )
        return

    message_payload = choice.get("message") or {}
    if not isinstance(message_payload, dict):
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed("OpenRouter returned an unexpected image response message."),
            logger=cog.logger,
        )
        return

    assistant_message = sanitize_assistant_message(message_payload)
    image_assets = await build_image_assets(assistant_message.get("images") or [])
    files = build_image_files(image_assets)
    if not files:
        await send_embed_batches(
            ctx.followup.send,
            embed=error_embed(
                "The model responded, but no images were returned in the response payload."
            ),
            logger=cog.logger,
        )
        return

    usage = extract_usage(response_payload)
    request_cost = usage.cost if usage.cost is not None else calculate_cost(model_info, usage)
    daily_cost = track_daily_cost(cog, ctx.author.id, request_cost)
    response_text = extract_message_text(assistant_message)

    cog.logger.info(
        "COST | command=image | user=%s | model=%s | images=%s | prompt_tokens=%s"
        " | completion_tokens=%s | cached_tokens=%s | cost=%s | daily=%s",
        ctx.author.id,
        resolved_model,
        len(files),
        usage.prompt_tokens,
        usage.completion_tokens,
        usage.cached_tokens,
        f"${request_cost:.6f}" if request_cost is not None else "unknown",
        f"${daily_cost:.6f}" if daily_cost is not None else "unknown",
    )

    embed = Embed(
        title=mode,
        description=_build_image_description(
            prompt=prompt,
            model=model_info.id if model_info is not None else resolved_model,
            mode=mode,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            response_text=response_text,
        ),
        color=Colour.blue(),
    )
    embed.set_image(url=f"attachment://{files[0].filename}")
    embeds = [embed]
    if SHOW_COST_EMBEDS:
        append_flat_pricing_embed(
            embeds,
            request_cost=request_cost,
            daily_cost=daily_cost,
            details=_build_pricing_details(
                mode=mode, aspect_ratio=aspect_ratio, image_size=image_size
            ),
            request_cost_is_estimate=usage.cost is None and request_cost is not None,
        )

    await send_embed_batches(
        ctx.followup.send,
        embeds=embeds,
        files=files,
        logger=cog.logger,
    )


def _is_image_attachment(attachment: Attachment) -> bool:
    content_type = (attachment.content_type or "").split(";", 1)[0].strip().lower()
    if content_type:
        return content_type.startswith("image/")
    filename = attachment.filename.lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def _resolve_image_modalities(model_info) -> list[str]:
    if model_info is not None and "text" not in model_info.output_modalities:
        return ["image"]
    return ["image", "text"]


def _validate_image_model_modalities(model_info, *, requires_image_input: bool) -> str | None:
    if model_info is None:
        return None
    if "image" not in model_info.output_modalities:
        return f"`{model_info.id}` does not advertise image output in the OpenRouter catalog."
    if requires_image_input and "image" not in model_info.input_modalities:
        return f"`{model_info.id}` does not advertise image input in the OpenRouter catalog."
    return None


def _build_image_config(*, aspect_ratio: str | None, image_size: str | None) -> dict[str, str]:
    image_config: dict[str, str] = {}
    if aspect_ratio:
        image_config["aspect_ratio"] = aspect_ratio
    if image_size:
        image_config["image_size"] = image_size
    return image_config


def _build_image_description(
    *,
    prompt: str,
    model: str,
    mode: str,
    aspect_ratio: str | None,
    image_size: str | None,
    response_text: str,
) -> str:
    lines = [
        f"**Prompt:** {truncate_text(prompt, 1500)}",
        f"**Model:** `{model}`",
        f"**Mode:** {mode}",
    ]
    if aspect_ratio:
        lines.append(f"**Aspect Ratio:** {aspect_ratio}")
    if image_size:
        lines.append(f"**Image Size:** {image_size}")
    if response_text:
        lines.append(f"**Notes:** {truncate_text(response_text, 500)}")
    return "\n".join(lines)


def _build_pricing_details(*, mode: str, aspect_ratio: str | None, image_size: str | None) -> str:
    details = [mode.lower()]
    if aspect_ratio:
        details.append(aspect_ratio)
    if image_size:
        details.append(image_size)
    return " · ".join(details)


async def build_image_assets(
    images: list[dict[str, Any]], *, filename_prefix: str = "image"
) -> list[tuple[str, bytes]]:
    assets: list[tuple[str, bytes]] = []
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for index, image in enumerate(images, start=1):
            raw_url = ((image.get("image_url") or {}).get("url")) or image.get("url")
            if not isinstance(raw_url, str) or not raw_url:
                continue
            if raw_url.startswith("data:"):
                image_bytes, extension = _decode_data_url(raw_url)
            else:
                response = await client.get(raw_url)
                response.raise_for_status()
                image_bytes = response.content
                extension = _guess_extension_from_url(raw_url)
            assets.append((f"{filename_prefix}_{index}.{extension}", image_bytes))
    return assets


def build_image_files(image_assets: list[tuple[str, bytes]]) -> list[File]:
    return [
        File(io.BytesIO(image_bytes), filename=filename) for filename, image_bytes in image_assets
    ]


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    header, encoded = data_url.split(",", 1)
    mime_type = header.split(":", 1)[1].split(";", 1)[0].strip().lower()
    extension = mime_type.split("/", 1)[-1] if "/" in mime_type else "png"
    return base64.b64decode(encoded), extension


def _guess_extension_from_url(url: str) -> str:
    lower_url = url.lower()
    for extension in ("png", "jpg", "jpeg", "webp", "gif"):
        if f".{extension}" in lower_url:
            return extension
    return "png"


__all__ = ["build_image_assets", "build_image_files", "run_image_command"]
