from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from discord import (
    ApplicationContext,
    Attachment,
    Colour,
    Embed,
    HTTPException,
    Interaction,
    Message,
    TextChannel,
    Thread,
)

from ...config import OPENROUTER_DEFAULT_PDF_ENGINE, OPENROUTER_DEFAULT_TEXT_MODEL, SHOW_COST_EMBEDS
from ...util import (
    ChatSettings,
    Conversation,
    build_context_compression_plugins,
    build_pdf_plugins,
    build_prompt_cache_control,
    build_web_plugin_override,
    calculate_cost,
    describe_chat_settings,
    extract_message_text,
    extract_reasoning_text,
    extract_url_citations,
    extract_usage,
    normalize_pdf_engine,
    prompt_cache_supported_for_model,
    sanitize_assistant_message,
    truncate_text,
)
from .attachments import (
    AttachmentInputError,
    AttachmentRequirements,
    build_attachment_parts,
    build_user_content,
    summarize_attachment_parts,
)
from .client import OpenRouterApiError
from .embeds import (
    append_citations_embed,
    append_reasoning_embeds,
    append_response_embeds,
    append_usage_embed,
    build_model_status_embed,
    error_embed,
)
from .image import build_image_assets, build_image_files
from .state import find_active_conversation, remember_view_state, track_daily_cost
from .tool_registry import build_runtime_tools


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
    pdf_engine: str | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
    context_compression: bool | None = None,
    prompt_cache_ttl: str | None = None,
    web_search: bool = False,
    datetime: bool = False,
    reasoning_effort: str | None = None,
    reasoning_max_tokens: int | None = None,
    exclude_reasoning: bool = False,
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
        resolved_pdf_engine = (
            normalize_pdf_engine(pdf_engine)
            if pdf_engine is not None
            else OPENROUTER_DEFAULT_PDF_ENGINE
        )
    except ValueError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return
    if reasoning_effort and reasoning_max_tokens is not None:
        await ctx.followup.send(
            embed=error_embed("Use either `reasoning_effort` or `reasoning_max_tokens`, not both.")
        )
        return
    if reasoning_max_tokens is not None and reasoning_max_tokens <= 0:
        await ctx.followup.send(
            embed=error_embed("`reasoning_max_tokens` must be greater than zero.")
        )
        return
    prompt_cache_error = _validate_prompt_cache_request(
        model=resolved_model,
        prompt_cache_ttl=prompt_cache_ttl,
    )
    if prompt_cache_error:
        await ctx.followup.send(embed=error_embed(prompt_cache_error))
        return
    try:
        cache_control = build_prompt_cache_control(prompt_cache_ttl)
    except ValueError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return

    try:
        attachment_parts = await build_attachment_parts([attachment] if attachment else [])
    except AttachmentInputError as error:
        await ctx.followup.send(embed=error_embed(str(error)))
        return
    except Exception as error:
        cog.logger.error("Failed to normalize slash attachment: %s", error, exc_info=True)
        await ctx.followup.send(embed=error_embed("Failed to process the provided attachment."))
        return

    attachment_requirements = summarize_attachment_parts(attachment_parts)
    validation_error = _validate_model_input_modalities(model_info, attachment_requirements)
    if validation_error:
        await ctx.followup.send(embed=error_embed(validation_error))
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
            context_compression=context_compression,
            prompt_cache_ttl=prompt_cache_ttl,
            web_search=web_search,
            datetime=datetime,
            reasoning_effort=reasoning_effort,
            reasoning_max_tokens=reasoning_max_tokens,
            exclude_reasoning=exclude_reasoning,
            pdf_engine=resolved_pdf_engine,
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
        request_plugins=_build_request_plugins(
            attachment_requirements=attachment_requirements,
            pdf_engine=resolved_pdf_engine,
            context_compression=context_compression,
        ),
        request_tools=_build_request_tools(web_search=web_search, datetime=datetime),
        request_cache_control=cache_control,
        intro_embeds=[
            build_model_status_embed(
                title="Conversation Started",
                model=resolved_model,
                description=_build_chat_settings_description(conversation.settings),
                model_info=model_info,
            )
        ],
    )
    if not success:
        cog.conversation_histories.pop(conversation.conversation_id, None)


async def handle_new_message_in_conversation(
    cog, message: Message, conversation: Conversation
) -> None:
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

    attachment_requirements = summarize_attachment_parts(attachment_parts)
    model_info = None
    if attachment_requirements.required_input_modalities:
        try:
            model_info = await cog.openrouter_client.get_model(conversation.settings.model)
        except OpenRouterApiError as error:
            await message.reply(embed=error_embed(str(error)))
            return
        validation_error = _validate_model_input_modalities(model_info, attachment_requirements)
        if validation_error:
            await message.reply(embed=error_embed(validation_error))
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
        model_info_hint=model_info,
        request_plugins=_build_request_plugins(
            attachment_requirements=attachment_requirements,
            pdf_engine=conversation.settings.pdf_engine,
            context_compression=conversation.settings.context_compression,
        ),
        request_tools=_build_request_tools(
            web_search=conversation.settings.web_search,
            datetime=conversation.settings.datetime,
        ),
        request_cache_control=build_prompt_cache_control(conversation.settings.prompt_cache_ttl),
    )
    if not success:
        _remove_last_user_message(conversation)


async def regenerate_conversation_response(
    cog, interaction: Interaction, conversation: Conversation
) -> None:
    removed_assistant: dict[str, Any] | None = None
    if conversation.messages and conversation.messages[-1].get("role") == "assistant":
        removed_assistant = conversation.messages.pop()
        conversation.touch()

    channel = interaction.channel
    user = interaction.user
    if channel is None or user is None:
        if removed_assistant is not None:
            conversation.append_assistant_message(removed_assistant)
        await interaction.followup.send(
            "Could not resolve the current interaction context.",
            ephemeral=True,
        )
        return
    if not isinstance(channel, (TextChannel, Thread)):
        if removed_assistant is not None:
            conversation.append_assistant_message(removed_assistant)
        await interaction.followup.send(
            "This channel does not support regenerated replies.",
            ephemeral=True,
        )
        return

    attachment_requirements = _summarize_last_user_attachment_requirements(conversation)

    success = await _run_conversation_turn(
        cog,
        conversation=conversation,
        send_reply=channel.send,
        user_id=user.id,
        channel=channel,
        request_plugins=_build_request_plugins(
            attachment_requirements=attachment_requirements,
            pdf_engine=conversation.settings.pdf_engine,
            context_compression=conversation.settings.context_compression,
        ),
        request_tools=_build_request_tools(
            web_search=conversation.settings.web_search,
            datetime=conversation.settings.datetime,
        ),
        request_cache_control=build_prompt_cache_control(conversation.settings.prompt_cache_ttl),
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
    request_plugins: list[dict[str, Any]] | None = None,
    request_tools: list[dict[str, Any]] | None = None,
    request_cache_control: dict[str, Any] | None = None,
    intro_embeds: list[Embed] | None = None,
) -> bool:
    typing_task = asyncio.create_task(keep_typing(channel))
    response_text = ""

    try:
        response_payload = await cog.openrouter_client.create_chat_completion(
            model=conversation.settings.model,
            messages=conversation.build_api_messages(),
            plugins=request_plugins,
            tools=request_tools,
            cache_control=request_cache_control,
            temperature=conversation.settings.temperature,
            top_p=conversation.settings.top_p,
            max_tokens=conversation.settings.max_tokens,
            reasoning_effort=conversation.settings.reasoning_effort,
            reasoning_max_tokens=conversation.settings.reasoning_max_tokens,
            exclude_reasoning=conversation.settings.exclude_reasoning,
            user=str(user_id),
            session_id=str(conversation.conversation_id),
        )

        choice = (response_payload.get("choices") or [None])[0]
        if not isinstance(choice, dict):
            raise OpenRouterApiError("OpenRouter returned no choices for this request.")

        message_payload = choice.get("message") or {}
        if not isinstance(message_payload, dict):
            raise OpenRouterApiError("OpenRouter returned an unexpected response message.")

        assistant_message = sanitize_assistant_message(message_payload)
        reasoning_text = extract_reasoning_text(assistant_message)
        citations = extract_url_citations(assistant_message)
        response_text = extract_message_text(assistant_message)
        image_assets: list[tuple[str, bytes]] = []
        if assistant_message.get("images"):
            try:
                image_assets = await build_image_assets(
                    assistant_message.get("images") or [],
                    filename_prefix="chat_image",
                )
            except Exception as error:
                cog.logger.warning(
                    "Failed to prepare generated chat images: %s", error, exc_info=True
                )
        usage = extract_usage(response_payload)
        tool_call_counts = _extract_tool_call_counts(assistant_message)
        model_info = model_info_hint or await cog.openrouter_client.get_model(
            conversation.settings.model
        )
        estimated_cost = calculate_cost(model_info, usage)
        request_cost = usage.cost if usage.cost is not None else estimated_cost
        daily_cost = track_daily_cost(cog, user_id, request_cost)
        response_id = (
            response_payload.get("id") if isinstance(response_payload.get("id"), str) else "unknown"
        )
        cost_source = "api" if usage.cost is not None else "estimate"

        cog.logger.info(
            "COST | command=chat | response_id=%s | cost_source=%s | user=%s | model=%s"
            " | prompt_tokens=%s | completion_tokens=%s | total_tokens=%s"
            " | cached_tokens=%s | reasoning_tokens=%s | server_tools=%s | tool_calls=%s"
            " | api_cost=%s | estimated_cost=%s | cost=%s | daily=%s",
            response_id,
            cost_source,
            user_id,
            conversation.settings.model,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
            usage.cached_tokens,
            usage.reasoning_tokens,
            _format_tool_counts(usage.server_tool_use),
            _format_tool_counts(tool_call_counts),
            f"${usage.cost:.6f}" if usage.cost is not None else "unknown",
            f"${estimated_cost:.6f}" if estimated_cost is not None else "unknown",
            f"${request_cost:.6f}" if request_cost is not None else "unknown",
            f"${daily_cost:.6f}" if daily_cost is not None else "unknown",
        )

        embeds: list[Embed] = list(intro_embeds or [])
        append_reasoning_embeds(embeds, reasoning_text)
        if response_text or not image_assets:
            append_response_embeds(embeds, response_text)
        append_citations_embed(embeds, citations)
        _append_image_embeds(
            embeds,
            assistant_message,
            attached_filenames=[filename for filename, _ in image_assets],
        )
        if SHOW_COST_EMBEDS:
            append_usage_embed(
                embeds,
                usage=usage,
                request_cost=request_cost,
                daily_cost=daily_cost,
                model_info=model_info,
                request_cost_is_estimate=usage.cost is None and request_cost is not None,
            )

        conversation.append_assistant_message(assistant_message)
        await cog._strip_previous_view(conversation.conversation_id)
        view = cog._create_button_view(
            user_id,
            conversation.conversation_id,
            tools=request_tools,
        )

        try:
            send_kwargs: dict[str, Any] = {
                "embeds": embeds,
                "view": view,
            }
            if image_assets:
                send_kwargs["files"] = build_image_files(image_assets)
            reply_message = await send_reply(**send_kwargs)
        except HTTPException:
            fallback_text = response_text or "No text content returned."
            fallback_kwargs: dict[str, Any] = {
                "content": truncate_text(fallback_text, 1900),
                "view": view,
            }
            if image_assets:
                fallback_kwargs["files"] = build_image_files(image_assets)
            reply_message = await send_reply(**fallback_kwargs)

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


def _append_image_embeds(
    embeds: list[Embed],
    assistant_message: dict[str, Any],
    *,
    attached_filenames: list[str] | None = None,
) -> None:
    images = assistant_message.get("images") or []
    filenames = list(attached_filenames or [])
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
        embed = Embed(
            title="Generated Image" if index == 1 else f"Generated Image ({index})",
            description=description,
            color=Colour.green(),
        )
        if index <= len(filenames):
            embed.set_image(url=f"attachment://{filenames[index - 1]}")
        embeds.append(embed)


def _build_request_plugins(
    *,
    attachment_requirements: AttachmentRequirements,
    pdf_engine: str | None,
    context_compression: bool | None = None,
) -> list[dict[str, Any]] | None:
    plugins: list[dict[str, Any]] = []
    if attachment_requirements.has_pdf:
        plugins.extend(build_pdf_plugins(pdf_engine) or [])
    plugins.extend(build_context_compression_plugins(context_compression) or [])
    plugins.extend(build_web_plugin_override(enabled=False))
    return plugins or None


def _build_request_tools(
    *,
    web_search: bool = False,
    datetime: bool = False,
) -> list[dict[str, Any]] | None:
    selected_tool_names: list[str] = []
    if web_search:
        selected_tool_names.append("web_search")
    if datetime:
        selected_tool_names.append("datetime")
    tools = build_runtime_tools(selected_tool_names)
    return tools or None


def _validate_prompt_cache_request(*, model: str, prompt_cache_ttl: str | None) -> str | None:
    if not prompt_cache_ttl:
        return None
    if prompt_cache_supported_for_model(model):
        return None
    return (
        "`prompt_cache_ttl` currently targets Anthropic chat models on OpenRouter. "
        "Most other providers already use automatic prompt caching when supported."
    )


def _build_chat_settings_description(settings: ChatSettings) -> str | None:
    summary = describe_chat_settings(settings)
    if not summary:
        return None
    return f"**Options:** {summary}"


def _validate_model_input_modalities(
    model_info,
    attachment_requirements: AttachmentRequirements,
) -> str | None:
    if model_info is None or not attachment_requirements.required_input_modalities:
        return None

    supported_modalities = {modality.casefold() for modality in model_info.input_modalities}
    missing_modalities = sorted(
        modality
        for modality in attachment_requirements.required_input_modalities
        if modality.casefold() not in supported_modalities
    )
    if not missing_modalities:
        return None

    if len(missing_modalities) == 1:
        return (
            f"`{model_info.id}` does not advertise `{missing_modalities[0]}` input "
            "in the OpenRouter catalog."
        )
    formatted_modalities = ", ".join(f"`{modality}`" for modality in missing_modalities)
    return (
        f"`{model_info.id}` does not advertise the required input modalities "
        f"in the OpenRouter catalog: {formatted_modalities}."
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


def _summarize_last_user_attachment_requirements(
    conversation: Conversation,
) -> AttachmentRequirements:
    for message in reversed(conversation.messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            return AttachmentRequirements()
        attachment_parts = [
            item
            for item in content
            if isinstance(item, dict) and str(item.get("type") or "").strip().lower() != "text"
        ]
        return summarize_attachment_parts(attachment_parts)
    return AttachmentRequirements()
