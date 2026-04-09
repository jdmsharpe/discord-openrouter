from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from discord import ApplicationContext, Attachment, Colour, Embed, HTTPException, Interaction, Message, TextChannel

from ...config import OPENROUTER_DEFAULT_TEXT_MODEL, SHOW_COST_EMBEDS
from ...util import (
    ChatSettings,
    Conversation,
    calculate_cost,
    extract_message_text,
    extract_reasoning_text,
    extract_usage,
    sanitize_assistant_message,
    truncate_text,
)
from .attachments import AttachmentInputError, build_attachment_parts, build_user_content
from .client import OpenRouterApiError
from .embeds import (
    append_reasoning_embeds,
    append_response_embeds,
    append_usage_embed,
    build_model_status_embed,
    error_embed,
)
from .state import create_button_view, find_active_conversation, remember_view_state, track_daily_cost


async def keep_typing(channel) -> None:
    try:
        while True:
            async with channel.typing():
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        raise


async def handle_on_message(cog, message: Message) -> None:
    if message.author == cog.bot.user:
        return

    conversation = find_active_conversation(
        cog,
        channel_id=message.channel.id,
        user_id=message.author.id,
    )
    if conversation is None:
        return

    await handle_new_message_in_conversation(cog, message, conversation)


async def handle_check_permissions(cog, ctx: ApplicationContext) -> None:
    if ctx.guild is None:
        await ctx.respond("This command can only be used in a server.")
        return

    channel = ctx.channel
    if not isinstance(channel, TextChannel):
        await ctx.respond("Cannot check permissions in this channel type.")
        return

    permissions = channel.permissions_for(ctx.guild.me)
    if permissions.read_messages and permissions.read_message_history:
        await ctx.respond("Bot has permission to read messages and message history.")
        return

    await ctx.respond("Bot is missing necessary permissions in this channel.")


async def run_chat_command(
    cog,
    *,
    ctx: ApplicationContext,
    prompt: str,
    model: str | None = None,
    persona: str | None = None,
    attachment: Attachment | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> None:
    await ctx.defer()

    channel = ctx.channel
    if channel is None:
        await ctx.followup.send(embed=error_embed("Could not resolve the current channel."))
        return

    user = ctx.user
    existing = find_active_conversation(cog, channel_id=channel.id, user_id=user.id)
    if existing is not None:
        await cog._cleanup_conversation(user.id, existing.conversation_id)

    resolved_model, model_info = await _resolve_model_for_request(
        cog,
        requested_model=model,
        channel_id=channel.id,
        user_id=user.id,
    )

    try:
        attachment_parts = await build_attachment_parts([attachment] if attachment else [])
    except AttachmentInputError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return
    except Exception as error:
        cog.logger.error("Failed to normalize slash attachment: %s", error, exc_info=True)
        await ctx.followup.send(embed=error_embed("Failed to process the provided attachment."))
        return

    user_content = build_user_content(prompt, attachment_parts)
    if not user_content:
        await ctx.followup.send(embed=error_embed("Please provide a prompt or attachment."))
        return

    conversation = Conversation(
        conversation_id=ctx.interaction.id,
        conversation_starter_id=user.id,
        channel_id=channel.id,
        settings=ChatSettings(
            model=resolved_model,
            system_prompt=persona or "You are a helpful assistant.",
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        ),
    )
    conversation.append_user_message({"role": "user", "content": user_content})
    cog.conversation_histories[conversation.conversation_id] = conversation

    success = await _run_conversation_turn(
        cog,
        conversation=conversation,
        send_reply=ctx.followup.send,
        user_id=user.id,
        channel=channel,
        model_info_hint=model_info,
        intro_embeds=[
            build_model_status_embed(
                title="Conversation Started",
                model=resolved_model,
                model_info=model_info,
            )
        ],
    )
    if not success:
        cog.conversation_histories.pop(conversation.conversation_id, None)


async def handle_new_message_in_conversation(cog, message: Message, conversation: Conversation) -> None:
    if message.author.id != conversation.conversation_starter_id or conversation.paused:
        return

    try:
        attachment_parts = await build_attachment_parts(message.attachments)
    except AttachmentInputError as error:
        await message.reply(embed=error_embed(str(error)))
        return
    except Exception as error:
        cog.logger.error("Failed to normalize follow-up attachments: %s", error, exc_info=True)
        await message.reply(embed=error_embed("Failed to process one or more attachments."))
        return

    user_content = build_user_content(message.content, attachment_parts)
    if not user_content:
        return

    conversation.append_user_message({"role": "user", "content": user_content})
    success = await _run_conversation_turn(
        cog,
        conversation=conversation,
        send_reply=message.reply,
        user_id=message.author.id,
        channel=message.channel,
    )
    if not success:
        _remove_last_user_message(conversation)


async def regenerate_conversation_response(cog, interaction: Interaction, conversation: Conversation) -> None:
    removed_assistant: dict[str, Any] | None = None
    if conversation.messages and conversation.messages[-1].get("role") == "assistant":
        removed_assistant = conversation.messages.pop()
        conversation.touch()

    channel = interaction.channel
    if channel is None:
        if removed_assistant is not None:
            conversation.append_assistant_message(removed_assistant)
        await interaction.followup.send(
            "Could not resolve the current channel.",
            ephemeral=True,
        )
        return

    success = await _run_conversation_turn(
        cog,
        conversation=conversation,
        send_reply=channel.send,
        user_id=interaction.user.id,
        channel=channel,
    )
    if not success and removed_assistant is not None:
        conversation.append_assistant_message(removed_assistant)


async def _run_conversation_turn(
    cog,
    *,
    conversation: Conversation,
    send_reply: Callable[..., Awaitable[Any]],
    user_id: int,
    channel,
    model_info_hint=None,
    intro_embeds: list[Embed] | None = None,
) -> bool:
    typing_task = asyncio.create_task(keep_typing(channel))
    response_text = ""

    try:
        response_payload = await cog.openrouter_client.create_chat_completion(
            model=conversation.settings.model,
            messages=conversation.build_api_messages(),
            temperature=conversation.settings.temperature,
            top_p=conversation.settings.top_p,
            max_tokens=conversation.settings.max_tokens,
            reasoning_effort=conversation.settings.reasoning_effort,
            user=str(user_id),
            session_id=str(conversation.conversation_id),
        )

        choice = ((response_payload.get("choices") or [None])[0])
        if not isinstance(choice, dict):
            raise OpenRouterApiError("OpenRouter returned no choices for this request.")

        message_payload = choice.get("message") or {}
        if not isinstance(message_payload, dict):
            raise OpenRouterApiError("OpenRouter returned an unexpected response message.")

        assistant_message = sanitize_assistant_message(message_payload)
        reasoning_text = extract_reasoning_text(assistant_message)
        response_text = extract_message_text(assistant_message)
        usage = extract_usage(response_payload)
        tool_call_counts = _extract_tool_call_counts(assistant_message)
        model_info = model_info_hint or await cog.openrouter_client.get_model(conversation.settings.model)
        request_cost = usage.cost if usage.cost is not None else calculate_cost(model_info, usage)
        daily_cost = track_daily_cost(cog, user_id, request_cost)

        cog.logger.info(
            "COST | command=chat | user=%s | model=%s | prompt_tokens=%s | completion_tokens=%s"
            " | cached_tokens=%s | reasoning_tokens=%s | server_tools=%s | tool_calls=%s"
            " | cost=%s | daily=%s",
            user_id,
            conversation.settings.model,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.cached_tokens,
            usage.reasoning_tokens,
            _format_tool_counts(usage.server_tool_use),
            _format_tool_counts(tool_call_counts),
            f"${request_cost:.6f}" if request_cost is not None else "unknown",
            f"${daily_cost:.6f}" if daily_cost is not None else "unknown",
        )

        embeds: list[Embed] = list(intro_embeds or [])
        append_reasoning_embeds(embeds, reasoning_text)
        append_response_embeds(embeds, response_text)
        _append_image_embeds(embeds, assistant_message)
        if SHOW_COST_EMBEDS:
            append_usage_embed(
                embeds,
                usage=usage,
                request_cost=request_cost,
                daily_cost=daily_cost,
            )

        conversation.append_assistant_message(assistant_message)
        await cog._strip_previous_view(conversation.conversation_id)
        view = cog._create_button_view(user_id, conversation.conversation_id)

        try:
            reply_message = await send_reply(embeds=embeds, view=view)
        except HTTPException:
            fallback_text = response_text or "No text content returned."
            reply_message = await send_reply(content=truncate_text(fallback_text, 1900), view=view)

        remember_view_state(cog, user_id, conversation.conversation_id, view, reply_message)
        return True
    except Exception as error:
        cog.logger.error("Error while running conversation turn: %s", error, exc_info=True)
        await _safe_error_reply(send_reply, str(error))
        return False
    finally:
        typing_task.cancel()


async def _resolve_model_for_request(
    cog,
    *,
    requested_model: str | None,
    channel_id: int,
    user_id: int,
):
    channel_default = cog.channel_model_defaults.get((channel_id, user_id))
    raw_model = (requested_model or channel_default or OPENROUTER_DEFAULT_TEXT_MODEL).strip()
    model_info = await cog.openrouter_client.get_model(raw_model)
    if model_info is not None:
        return model_info.id, model_info
    return raw_model, None


async def _safe_error_reply(send_reply: Callable[..., Awaitable[Any]], description: str) -> None:
    try:
        await send_reply(embed=error_embed(description))
    except Exception:
        await send_reply(content=truncate_text(description, 1900))


def _remove_last_user_message(conversation: Conversation) -> None:
    if conversation.messages and conversation.messages[-1].get("role") == "user":
        conversation.messages.pop()
        conversation.touch()


def _append_image_embeds(embeds: list[Embed], assistant_message: dict[str, Any]) -> None:
    images = assistant_message.get("images") or []
    for index, image_payload in enumerate(images[:4], start=1):
        if not isinstance(image_payload, dict):
            continue
        image_url = ((image_payload.get("image_url") or {}).get("url")) or image_payload.get("url")
        if isinstance(image_url, str) and image_url.startswith("data:"):
            description = "Model returned an inline image payload."
        elif isinstance(image_url, str) and image_url:
            description = image_url
        else:
            description = "Model returned an image attachment."
        embeds.append(
            Embed(
                title="Generated Image" if index == 1 else f"Generated Image ({index})",
                description=description,
                color=Colour.green(),
            )
        )


def _extract_tool_call_counts(message: dict[str, Any]) -> dict[str, int]:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return {}

    counts: dict[str, int] = {}
    for item in tool_calls:
        if not isinstance(item, dict):
            continue
        function_payload = item.get("function")
        if isinstance(function_payload, dict) and isinstance(function_payload.get("name"), str):
            name = function_payload["name"].strip()
        elif isinstance(item.get("name"), str):
            name = item["name"].strip()
        elif isinstance(item.get("type"), str):
            name = item["type"].strip()
        else:
            name = ""
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def _format_tool_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{name} x{count}" for name, count in sorted(counts.items()))
