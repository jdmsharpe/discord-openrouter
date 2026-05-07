from __future__ import annotations

import logging

from discord import ApplicationContext, Attachment
from discord.commands import SlashCommandGroup, option
from discord.ext import commands, tasks

from ...config import (
    GUILD_IDS,
    OPENROUTER_DEFAULT_IMAGE_MODEL,
    OPENROUTER_DEFAULT_PDF_ENGINE,
    OPENROUTER_DEFAULT_STT_MODEL,
    OPENROUTER_DEFAULT_TEXT_MODEL,
    OPENROUTER_DEFAULT_TTS_MODEL,
    OPENROUTER_DEFAULT_VIDEO_MODEL,
)
from ...logging_setup import bind_request_id
from ...util import describe_chat_settings, prompt_cache_supported_for_model
from .chat import (
    handle_check_permissions,
    handle_on_message,
    run_chat_command,
)
from .chat import handle_new_message_in_conversation as handle_conversation_message
from .chat import (
    keep_typing as keep_typing_loop,
)
from .chat import regenerate_conversation_response as regenerate_response
from .client import OpenRouterApiError, build_openrouter_client
from .command_options import (
    IMAGE_ASPECT_RATIO_CHOICES,
    IMAGE_SIZE_CHOICES,
    MODALITY_CHOICES,
    MODEL_INPUT_MODALITY_CHOICES,
    MODEL_OUTPUT_MODALITY_CHOICES,
    MODEL_SCOPE_CHOICES,
    PDF_ENGINE_CHOICES,
    PROMPT_CACHE_TTL_CHOICES,
    REASONING_EFFORT_CHOICES,
    TTS_FORMAT_CHOICES,
    VIDEO_ASPECT_RATIO_CHOICES,
    VIDEO_RESOLUTION_CHOICES,
)
from .embed_delivery import send_embed_batches
from .embeds import (
    build_current_model_embed,
    build_model_list_embed,
    build_model_status_embed,
    error_embed,
)
from .image import run_image_command
from .speech import run_stt_command, run_tts_command
from .state import (
    ModalityModelStore,
    cleanup_conversation,
    create_button_view,
    find_active_conversation,
    prune_runtime_state,
    stop_conversation,
    strip_previous_view,
)
from .video import run_video_command


class OpenRouterCog(commands.Cog):
    openrouter = SlashCommandGroup("openrouter", "OpenRouter commands", guild_ids=GUILD_IDS)
    openrouter_media = SlashCommandGroup(
        "openrouter-media",
        "OpenRouter image and video commands",
        guild_ids=GUILD_IDS,
    )
    openrouter_tools = SlashCommandGroup(
        "openrouter-tools",
        "OpenRouter speech commands",
        guild_ids=GUILD_IDS,
    )

    def __init__(self, bot):
        self.logger = logging.getLogger(__name__)
        self.bot = bot
        self.openrouter_client = build_openrouter_client()
        self.conversation_histories = {}
        self.views = {}
        self.last_view_messages = {}
        self.daily_costs = {}
        self.channel_model_defaults: ModalityModelStore = {}

    def cog_unload(self):
        if self._runtime_cleanup_task.is_running():
            self._runtime_cleanup_task.cancel()

    async def _strip_previous_view(self, conversation_id: int) -> None:
        await strip_previous_view(self, conversation_id)

    async def _cleanup_conversation(self, user, conversation_id: int | None = None) -> None:
        await cleanup_conversation(self, user, conversation_id)

    async def _stop_conversation(self, conversation_id: int, user) -> None:
        await stop_conversation(self, conversation_id, user)

    async def _prune_runtime_state(self) -> None:
        await prune_runtime_state(self)

    def _create_button_view(self, user, conversation_id: int, tools=None):
        return create_button_view(self, user, conversation_id, tools=tools)

    async def handle_new_message_in_conversation(self, message, conversation):
        await handle_conversation_message(self, message, conversation)

    async def regenerate_conversation_response(self, interaction, conversation):
        await regenerate_response(self, interaction, conversation)

    async def keep_typing(self, channel):
        await keep_typing_loop(channel)

    @tasks.loop(minutes=15)
    async def _runtime_cleanup_task(self) -> None:
        await self._prune_runtime_state()

    @_runtime_cleanup_task.before_loop
    async def _before_runtime_cleanup_task(self) -> None:
        await self.bot.wait_until_ready()

    async def cog_before_invoke(self, ctx) -> None:
        """Bind a fresh request id on every slash-command entry into this cog."""
        bind_request_id()

    @commands.Cog.listener()
    async def on_ready(self):
        bot_user = self.bot.user
        bot_user_id = bot_user.id if bot_user is not None else "unknown"
        self.logger.info("Logged in as %s (ID: %s)", bot_user, bot_user_id)
        self.logger.info("Attempting to sync commands for guilds: %s", GUILD_IDS)
        if not self._runtime_cleanup_task.is_running():
            self._runtime_cleanup_task.start()
        try:
            await self.bot.sync_commands()
            self.logger.info("Commands synchronized successfully.")
        except Exception as error:
            self.logger.error("Error during command synchronization: %s", error, exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        bind_request_id()
        await handle_on_message(self, message)

    @commands.Cog.listener()
    async def on_error(self, event, *args, **kwargs):
        self.logger.error("Error in event %s: %s %s", event, args, kwargs, exc_info=True)

    @openrouter.command(
        name="check_permissions",
        description="Check if bot has necessary permissions in this channel",
    )
    async def check_permissions(self, ctx: ApplicationContext):
        await handle_check_permissions(self, ctx)

    @openrouter.command(
        name="chat",
        description="Starts a conversation with a model.",
    )
    @option("prompt", description="Prompt", required=True, type=str)
    @option(
        "persona",
        description="What role you want the model to emulate. (default: You are a helpful assistant.)",
        required=False,
        type=str,
    )
    @option(
        "model",
        description="OpenRouter model slug to use. (default: channel setting or OPENROUTER_DEFAULT_TEXT_MODEL)",
        required=False,
        type=str,
    )
    @option(
        "attachment",
        description="Attach an image, PDF, audio, video, or file. (default: not set)",
        required=False,
        type=Attachment,
    )
    @option(
        "pdf_engine",
        description=(
            "PDF parser for PDF attachments. "
            f"(default: {OPENROUTER_DEFAULT_PDF_ENGINE or 'OpenRouter-managed'})"
        ),
        required=False,
        type=str,
        choices=PDF_ENGINE_CHOICES,
    )
    @option(
        "temperature",
        description="Sampling temperature. (default: not set)",
        required=False,
        type=float,
    )
    @option(
        "top_p",
        description="Nucleus sampling value. (default: not set)",
        required=False,
        type=float,
    )
    @option(
        "max_tokens",
        description="Maximum completion tokens. (default: not set)",
        required=False,
        type=int,
    )
    @option(
        "context_compression",
        description="Override context compression. (default: not set / OpenRouter default)",
        required=False,
        type=bool,
    )
    @option(
        "prompt_cache_ttl",
        description="Anthropic prompt caching TTL. (default: not set)",
        required=False,
        type=str,
        choices=PROMPT_CACHE_TTL_CHOICES,
    )
    @option(
        "reasoning_effort",
        description="Reasoning effort for supported models. (default: not set)",
        required=False,
        type=str,
        choices=REASONING_EFFORT_CHOICES,
    )
    @option(
        "reasoning_max_tokens",
        description="Reasoning token budget for supported models. (default: not set)",
        required=False,
        type=int,
    )
    @option(
        "exclude_reasoning",
        description="Hide reasoning blocks while still using reasoning when supported. (default: false)",
        required=False,
        type=bool,
    )
    @option(
        "web_search",
        description="Enable OpenRouter web search for current information. (default: false)",
        required=False,
        type=bool,
    )
    @option(
        "datetime",
        description="Enable OpenRouter datetime awareness for time-sensitive prompts. (default: false)",
        required=False,
        type=bool,
    )
    async def chat(
        self,
        ctx: ApplicationContext,
        prompt: str,
        persona: str | None = None,
        model: str | None = None,
        attachment: Attachment | None = None,
        pdf_engine: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        context_compression: bool | None = None,
        prompt_cache_ttl: str | None = None,
        reasoning_effort: str | None = None,
        reasoning_max_tokens: int | None = None,
        exclude_reasoning: bool | None = None,
        web_search: bool | None = None,
        datetime: bool | None = None,
    ):
        await run_chat_command(
            self,
            ctx=ctx,
            prompt=prompt,
            model=model,
            persona=persona,
            attachment=attachment,
            pdf_engine=pdf_engine,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            context_compression=context_compression,
            prompt_cache_ttl=prompt_cache_ttl,
            web_search=bool(web_search),
            datetime=bool(datetime),
            reasoning_effort=reasoning_effort,
            reasoning_max_tokens=reasoning_max_tokens,
            exclude_reasoning=bool(exclude_reasoning),
        )

    @openrouter.command(
        name="models",
        description="Search the models available through OpenRouter.",
    )
    @option("query", description="Search text. (default: not set)", required=False, type=str)
    @option(
        "input_modality",
        description="Filter by input modality. (default: not set)",
        required=False,
        type=str,
        choices=MODEL_INPUT_MODALITY_CHOICES,
    )
    @option(
        "output_modality",
        description="Filter by output modality. (default: not set)",
        required=False,
        type=str,
        choices=MODEL_OUTPUT_MODALITY_CHOICES,
    )
    @option("limit", description="Max models to return. (default: 10)", required=False, type=int)
    @option(
        "refresh", description="Refresh cached models. (default: false)", required=False, type=bool
    )
    async def models(
        self,
        ctx: ApplicationContext,
        query: str | None = None,
        input_modality: str | None = None,
        output_modality: str | None = None,
        limit: int | None = None,
        refresh: bool | None = None,
    ):
        await ctx.defer()
        try:
            models = await self.openrouter_client.list_models(
                query=query,
                input_modality=input_modality,
                output_modality=output_modality,
                limit=limit or 10,
                refresh=bool(refresh),
            )
        except OpenRouterApiError as error:
            await send_embed_batches(
                ctx.followup.send,
                embed=error_embed(str(error)),
                logger=self.logger,
            )
            return

        await send_embed_batches(
            ctx.followup.send,
            embed=build_model_list_embed(
                models,
                query=query,
                input_modality=input_modality,
                output_modality=output_modality,
            ),
            logger=self.logger,
        )

    @openrouter.command(
        name="current_model",
        description="Show the active and default model state for this channel.",
    )
    async def current_model(self, ctx: ApplicationContext):
        user_id = ctx.user.id
        channel_id = ctx.channel.id if ctx.channel is not None else 0
        active_conversation = find_active_conversation(self, channel_id=channel_id, user_id=user_id)

        channel_defaults = {
            modality: self.channel_model_defaults[(channel_id, user_id, modality)]
            for modality in ("chat", "image", "video", "tts", "stt")
            if (channel_id, user_id, modality) in self.channel_model_defaults
        }
        global_defaults: dict[str, str | None] = {
            "chat": OPENROUTER_DEFAULT_TEXT_MODEL,
            "image": OPENROUTER_DEFAULT_IMAGE_MODEL,
            "video": OPENROUTER_DEFAULT_VIDEO_MODEL,
            "tts": OPENROUTER_DEFAULT_TTS_MODEL,
            "stt": OPENROUTER_DEFAULT_STT_MODEL,
        }
        embed = build_current_model_embed(
            active_model=active_conversation.settings.model if active_conversation else None,
            active_options=(
                describe_chat_settings(active_conversation.settings)
                if active_conversation is not None
                else None
            ),
            channel_defaults=channel_defaults,
            global_defaults=global_defaults,
        )
        await send_embed_batches(ctx.respond, embed=embed, logger=self.logger)

    @openrouter.command(
        name="switch_model",
        description="Switch the active conversation model, save a channel default, or both.",
    )
    @option("model", description="Model slug or search text.", required=True, type=str)
    @option(
        "scope",
        description="Where to apply the model change. (default: conversation)",
        required=False,
        type=str,
        choices=MODEL_SCOPE_CHOICES,
    )
    @option(
        "modality",
        description="Which modality's model to switch. (default: chat)",
        required=False,
        type=str,
        choices=MODALITY_CHOICES,
    )
    async def switch_model(
        self,
        ctx: ApplicationContext,
        model: str,
        scope: str | None = None,
        modality: str | None = None,
    ):
        await ctx.defer()
        resolved_scope = scope or "conversation"
        resolved_modality = modality or "chat"
        channel_id = ctx.channel.id if ctx.channel is not None else 0
        user_id = ctx.user.id
        active_conversation = find_active_conversation(self, channel_id=channel_id, user_id=user_id)

        try:
            model_info = await self.openrouter_client.get_model(model)
        except OpenRouterApiError as error:
            await send_embed_batches(
                ctx.followup.send,
                embed=error_embed(str(error)),
                logger=self.logger,
            )
            return

        resolved_model = model_info.id if model_info is not None else model.strip()
        lines = [f"**Resolved model:** `{resolved_model}`"]

        if resolved_modality == "chat":
            if resolved_scope in {"conversation", "both"}:
                if active_conversation is None:
                    if resolved_scope == "conversation":
                        self.channel_model_defaults[(channel_id, user_id, "chat")] = resolved_model
                        lines.append("**Conversation:** no active conversation to update")
                        lines.append("**Channel default:** updated (fallback)")
                        resolved_scope = "channel"
                    else:
                        lines.append("**Conversation:** no active conversation to update")
                else:
                    active_conversation.settings.model = resolved_model
                    if (
                        active_conversation.settings.prompt_cache_ttl
                        and not prompt_cache_supported_for_model(resolved_model)
                    ):
                        active_conversation.settings.prompt_cache_ttl = None
                        lines.append("**Prompt cache:** cleared (not supported by the new model)")
                    active_conversation.touch()
                    lines.append("**Conversation:** updated")
                    active_options = describe_chat_settings(active_conversation.settings)
                    if active_options:
                        lines.append(f"**Active options:** {active_options}")

            if resolved_scope in {"channel", "both"} and (
                "**Channel default:** updated (fallback)" not in lines
            ):
                self.channel_model_defaults[(channel_id, user_id, "chat")] = resolved_model
                lines.append("**Channel default:** updated")
        else:
            # non-chat modalities: scope is always channel (silently downgraded)
            self.channel_model_defaults[(channel_id, user_id, resolved_modality)] = resolved_model
            lines.append("**Channel default:** updated")

        if model_info is None:
            lines.append(
                "Model was not found in the cached catalog, so it was saved exactly as typed."
            )

        await send_embed_batches(
            ctx.followup.send,
            embed=build_model_status_embed(
                title="Model Updated",
                model=resolved_model,
                description="\n".join(lines[1:]) if len(lines) > 1 else None,
                model_info=model_info,
            ),
            logger=self.logger,
        )

    @openrouter_media.command(
        name="image",
        description="Generates or edits an image from a prompt.",
    )
    @option("prompt", description="Prompt", required=True, type=str)
    @option(
        "model",
        description=f"OpenRouter image model slug to use. (default: {OPENROUTER_DEFAULT_IMAGE_MODEL})",
        required=False,
        type=str,
    )
    @option(
        "aspect_ratio",
        description="Image aspect ratio. (default: not set)",
        required=False,
        type=str,
        choices=IMAGE_ASPECT_RATIO_CHOICES,
    )
    @option(
        "image_size",
        description="Image size hint. (default: not set)",
        required=False,
        type=str,
        choices=IMAGE_SIZE_CHOICES,
    )
    @option(
        "attachment",
        description="Image to edit or remix. (default: not set)",
        required=False,
        type=Attachment,
    )
    async def image(
        self,
        ctx: ApplicationContext,
        prompt: str,
        model: str | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        attachment: Attachment | None = None,
    ):
        await run_image_command(
            self,
            ctx=ctx,
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            attachment=attachment,
        )

    @openrouter_media.command(
        name="video",
        description="Generates a video from a prompt.",
    )
    @option("prompt", description="Prompt", required=True, type=str)
    @option(
        "model",
        description=f"OpenRouter video model slug to use. (default: {OPENROUTER_DEFAULT_VIDEO_MODEL})",
        required=False,
        type=str,
    )
    @option(
        "aspect_ratio",
        description="Video aspect ratio. (default: not set)",
        required=False,
        type=str,
        choices=VIDEO_ASPECT_RATIO_CHOICES,
    )
    @option(
        "resolution",
        description="Output resolution. (default: not set)",
        required=False,
        type=str,
        choices=VIDEO_RESOLUTION_CHOICES,
    )
    @option(
        "size",
        description="Exact size like 1280x720. (default: not set)",
        required=False,
        type=str,
    )
    @option(
        "attachment",
        description="Reference image. (default: not set)",
        required=False,
        type=Attachment,
    )
    @option(
        "duration",
        description="Video duration in seconds. (default: not set)",
        required=False,
        type=int,
        min_value=1,
    )
    @option(
        "generate_audio",
        description="Request audio output when supported. (default: not set / model default)",
        required=False,
        type=bool,
    )
    @option(
        "seed",
        description="Seed for repeatable results. (default: not set)",
        required=False,
        type=int,
    )
    async def video(
        self,
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
    ):
        await run_video_command(
            self,
            ctx=ctx,
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            size=size,
            attachment=attachment,
            duration=duration,
            generate_audio=generate_audio,
            seed=seed,
        )

    @openrouter_tools.command(
        name="tts",
        description="Converts text to speech audio.",
    )
    @option(
        "input",
        description="Text to convert to speech. (max length 4096 characters)",
        required=True,
        type=str,
    )
    @option(
        "model",
        description=f"OpenRouter TTS model slug to use. (default: {OPENROUTER_DEFAULT_TTS_MODEL})",
        required=False,
        type=str,
    )
    @option(
        "voice",
        description="Voice override. (default: model/provider default)",
        required=False,
        type=str,
    )
    @option(
        "instructions",
        description="Additional style instructions for the spoken delivery. (default: not set)",
        required=False,
        type=str,
    )
    @option(
        "response_format",
        description="Audio file format. (default: mp3)",
        required=False,
        type=str,
        choices=TTS_FORMAT_CHOICES,
    )
    async def tts(
        self,
        ctx: ApplicationContext,
        input: str,
        model: str | None = None,
        voice: str | None = None,
        instructions: str | None = None,
        response_format: str = "mp3",
    ):
        await run_tts_command(
            self,
            ctx=ctx,
            input_text=input,
            model=model,
            voice=voice,
            instructions=instructions,
            response_format=response_format,
        )

    @openrouter_tools.command(
        name="stt",
        description="Generates text from the input audio.",
    )
    @option(
        "attachment",
        description="Audio file to transcribe. Max 20 MiB. Common types: mp3, mp4, m4a, wav, ogg, flac.",
        required=True,
        type=Attachment,
    )
    @option(
        "model",
        description=f"OpenRouter STT model slug to use. (default: {OPENROUTER_DEFAULT_STT_MODEL})",
        required=False,
        type=str,
    )
    @option(
        "instructions",
        description="Additional transcription instructions. (default: accurate verbatim transcript)",
        required=False,
        type=str,
    )
    async def stt(
        self,
        ctx: ApplicationContext,
        attachment: Attachment,
        model: str | None = None,
        instructions: str | None = None,
    ):
        await run_stt_command(
            self,
            ctx=ctx,
            attachment=attachment,
            model=model,
            instructions=instructions,
        )
