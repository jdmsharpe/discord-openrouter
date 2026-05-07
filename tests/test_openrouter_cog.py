from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.client import OpenRouterApiError
from discord_openrouter.cogs.openrouter.cog import OpenRouterCog
from discord_openrouter.util import ChatSettings, Conversation, ModelInfo


def _make_cog(*, openrouter_client=None) -> OpenRouterCog:
    """Build an OpenRouterCog without touching the real httpx-backed client."""
    bot = MagicMock()
    client = openrouter_client or MagicMock()
    with patch(
        "discord_openrouter.cogs.openrouter.cog.build_openrouter_client",
        return_value=client,
    ):
        cog = OpenRouterCog(bot=bot)
    cog.openrouter_client = client
    return cog


def _make_ctx(*, channel_id: int | None = 100, user_id: int = 7) -> SimpleNamespace:
    """Build a minimal ApplicationContext-like object for slash-command tests."""
    channel = SimpleNamespace(id=channel_id) if channel_id is not None else None
    user = SimpleNamespace(id=user_id)
    return SimpleNamespace(
        channel=channel,
        user=user,
        author=user,
        respond=AsyncMock(),
        defer=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
        interaction=SimpleNamespace(id=999),
    )


def _make_conversation(
    *, conversation_id: int = 5, user_id: int = 7, channel_id: int = 100, model: str = "openai/gpt-4o-mini"
) -> Conversation:
    return Conversation(
        conversation_id=conversation_id,
        conversation_starter_id=user_id,
        channel_id=channel_id,
        settings=ChatSettings(model=model),
    )


def _serialize_command_group_payload(group):
    return {
        "name": group.name,
        "description": group.description,
        "options": [
            {
                "name": command.name,
                "description": command.description,
                "options": [
                    option.to_dict()
                    for option in command.options
                    if option.input_type is not None
                ],
                "type": 1,
                "nsfw": False,
            }
            for command in group.subcommands
        ],
        "nsfw": False,
    }


class TestInit:
    def test_initial_state(self):
        cog = _make_cog()
        assert cog.conversation_histories == {}
        assert cog.views == {}
        assert cog.last_view_messages == {}
        assert cog.daily_costs == {}
        assert cog.channel_model_defaults == {}

    def test_registered_command_groups_fit_discord_size_limit(self):
        """Discord rejects any single top-level command payload over 8000 bytes."""

        cog = _make_cog()
        commands_by_name = {command.name: command for command in cog.get_commands()}

        assert set(commands_by_name) == {
            "openrouter",
            "openrouter-media",
            "openrouter-tools",
        }
        assert [command.name for command in commands_by_name["openrouter"].subcommands] == [
            "check_permissions",
            "chat",
            "models",
            "current_model",
            "switch_model",
        ]
        assert [command.name for command in commands_by_name["openrouter-media"].subcommands] == [
            "image",
            "video",
        ]
        assert [command.name for command in commands_by_name["openrouter-tools"].subcommands] == [
            "tts",
            "stt",
        ]

        payload_sizes = {
            name: len(
                json.dumps(
                    _serialize_command_group_payload(command),
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            for name, command in commands_by_name.items()
        }

        assert payload_sizes["openrouter"] < 8000
        assert payload_sizes["openrouter-media"] < 8000
        assert payload_sizes["openrouter-tools"] < 8000


class TestCogUnload:
    def test_cancels_running_cleanup_task(self):
        cog = _make_cog()
        cog._runtime_cleanup_task = MagicMock()
        cog._runtime_cleanup_task.is_running.return_value = True
        cog.cog_unload()
        cog._runtime_cleanup_task.cancel.assert_called_once()

    def test_does_not_cancel_when_task_idle(self):
        cog = _make_cog()
        cog._runtime_cleanup_task = MagicMock()
        cog._runtime_cleanup_task.is_running.return_value = False
        cog.cog_unload()
        cog._runtime_cleanup_task.cancel.assert_not_called()


class TestCogBeforeInvoke:
    def test_binds_a_fresh_request_id(self):
        cog = _make_cog()
        with patch("discord_openrouter.cogs.openrouter.cog.bind_request_id") as bind:
            asyncio.run(cog.cog_before_invoke(MagicMock()))
        bind.assert_called_once_with()


class TestCurrentModel:
    def test_no_active_conversation_falls_back_to_global_defaults(self):
        cog = _make_cog()
        ctx = _make_ctx()

        with patch(
            "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
        ) as send, patch(
            "discord_openrouter.cogs.openrouter.cog.build_current_model_embed"
        ) as build_embed:
            asyncio.run(cog.current_model.callback(cog, ctx))

        build_embed.assert_called_once()
        kwargs = build_embed.call_args.kwargs
        assert kwargs["active_model"] is None
        assert kwargs["active_options"] is None
        assert kwargs["channel_defaults"] == {}
        assert set(kwargs["global_defaults"].keys()) == {"chat", "image", "video", "tts", "stt"}
        send.assert_awaited_once()

    def test_uses_active_conversation_settings_when_present(self):
        cog = _make_cog()
        conversation = _make_conversation(model="anthropic/claude-haiku")
        cog.conversation_histories[conversation.conversation_id] = conversation
        ctx = _make_ctx(channel_id=conversation.channel_id, user_id=conversation.conversation_starter_id)

        with patch(
            "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
        ), patch(
            "discord_openrouter.cogs.openrouter.cog.build_current_model_embed"
        ) as build_embed, patch(
            "discord_openrouter.cogs.openrouter.cog.describe_chat_settings",
            return_value="model=anthropic/claude-haiku",
        ):
            asyncio.run(cog.current_model.callback(cog, ctx))

        kwargs = build_embed.call_args.kwargs
        assert kwargs["active_model"] == "anthropic/claude-haiku"
        assert kwargs["active_options"] == "model=anthropic/claude-haiku"

    def test_includes_channel_defaults_for_present_modalities(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.channel_model_defaults[(100, 7, "chat")] = "moonshotai/kimi-k2"
        cog.channel_model_defaults[(100, 7, "image")] = "openai/dall-e-3"

        with patch(
            "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
        ), patch(
            "discord_openrouter.cogs.openrouter.cog.build_current_model_embed"
        ) as build_embed:
            asyncio.run(cog.current_model.callback(cog, ctx))

        defaults = build_embed.call_args.kwargs["channel_defaults"]
        assert defaults == {"chat": "moonshotai/kimi-k2", "image": "openai/dall-e-3"}

    def test_handles_missing_channel(self):
        cog = _make_cog()
        ctx = _make_ctx(channel_id=None)

        with patch(
            "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
        ), patch(
            "discord_openrouter.cogs.openrouter.cog.build_current_model_embed"
        ) as build_embed:
            asyncio.run(cog.current_model.callback(cog, ctx))

        # Verifies channel_id=0 path doesn't blow up; no channel defaults.
        assert build_embed.call_args.kwargs["channel_defaults"] == {}


class TestSwitchModel:
    @staticmethod
    def _patches():
        return (
            patch(
                "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
            ),
            patch("discord_openrouter.cogs.openrouter.cog.build_model_status_embed"),
        )

    def test_chat_modality_with_no_active_conversation_falls_back_to_channel(self):
        cog = _make_cog()
        ctx = _make_ctx()
        model_info = ModelInfo(id="google/gemini-3-pro", name="Gemini 3 Pro")
        cog.openrouter_client.get_model = AsyncMock(return_value=model_info)
        send, build = self._patches()
        with send, build as build_status:
            asyncio.run(
                cog.switch_model.callback(cog, ctx, model="google/gemini-3-pro")
            )

        assert cog.channel_model_defaults[(100, 7, "chat")] == "google/gemini-3-pro"
        description = build_status.call_args.kwargs["description"]
        assert "no active conversation" in description
        assert "Channel default" in description

    def test_chat_scope_conversation_with_active_conversation(self):
        cog = _make_cog()
        ctx = _make_ctx()
        conversation = _make_conversation()
        cog.conversation_histories[conversation.conversation_id] = conversation
        model_info = ModelInfo(id="anthropic/claude-sonnet-4.6", name="Claude Sonnet 4.6")
        cog.openrouter_client.get_model = AsyncMock(return_value=model_info)
        send, build = self._patches()

        with send, build:
            asyncio.run(
                cog.switch_model.callback(
                    cog, ctx, model="anthropic/claude-sonnet-4.6", scope="conversation"
                )
            )

        assert conversation.settings.model == "anthropic/claude-sonnet-4.6"
        # No channel default written when scope is conversation-only.
        assert (100, 7, "chat") not in cog.channel_model_defaults

    def test_chat_scope_both_writes_conversation_and_channel_default(self):
        cog = _make_cog()
        ctx = _make_ctx()
        conversation = _make_conversation()
        cog.conversation_histories[conversation.conversation_id] = conversation
        model_info = ModelInfo(id="x-ai/grok-4", name="Grok 4")
        cog.openrouter_client.get_model = AsyncMock(return_value=model_info)
        send, build = self._patches()

        with send, build:
            asyncio.run(
                cog.switch_model.callback(cog, ctx, model="x-ai/grok-4", scope="both")
            )

        assert conversation.settings.model == "x-ai/grok-4"
        assert cog.channel_model_defaults[(100, 7, "chat")] == "x-ai/grok-4"

    def test_chat_clears_prompt_cache_when_new_model_unsupported(self):
        cog = _make_cog()
        ctx = _make_ctx()
        conversation = _make_conversation(model="anthropic/claude-sonnet-4")
        conversation.settings.prompt_cache_ttl = "5m"
        cog.conversation_histories[conversation.conversation_id] = conversation
        model_info = ModelInfo(id="openai/gpt-4o-mini", name="GPT-4o Mini")
        cog.openrouter_client.get_model = AsyncMock(return_value=model_info)
        send, build = self._patches()

        with send, build, patch(
            "discord_openrouter.cogs.openrouter.cog.prompt_cache_supported_for_model",
            return_value=False,
        ):
            asyncio.run(
                cog.switch_model.callback(cog, ctx, model="openai/gpt-4o-mini", scope="conversation")
            )

        assert conversation.settings.prompt_cache_ttl is None

    def test_non_chat_modality_writes_channel_default_only(self):
        cog = _make_cog()
        ctx = _make_ctx()
        # Even when an active conversation exists, non-chat modality should not touch it.
        conversation = _make_conversation(model="openai/gpt-4o-mini")
        cog.conversation_histories[conversation.conversation_id] = conversation
        model_info = ModelInfo(id="openai/dall-e-3", name="DALL-E 3")
        cog.openrouter_client.get_model = AsyncMock(return_value=model_info)
        send, build = self._patches()

        with send, build:
            asyncio.run(
                cog.switch_model.callback(
                    cog, ctx, model="openai/dall-e-3", scope="conversation", modality="image"
                )
            )

        assert cog.channel_model_defaults[(100, 7, "image")] == "openai/dall-e-3"
        # Conversation must remain untouched.
        assert conversation.settings.model == "openai/gpt-4o-mini"

    def test_unknown_model_kept_as_typed(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(return_value=None)
        send, build = self._patches()

        with send, build as build_status:
            asyncio.run(
                cog.switch_model.callback(cog, ctx, model="  vendor/unknown-model  ")
            )

        # Whitespace stripped; channel default still set.
        assert cog.channel_model_defaults[(100, 7, "chat")] == "vendor/unknown-model"
        description = build_status.call_args.kwargs["description"]
        assert "not found in the cached catalog" in description

    def test_api_error_short_circuits_with_error_embed(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.get_model = AsyncMock(
            side_effect=OpenRouterApiError("rate limited")
        )

        with patch(
            "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
        ) as send, patch(
            "discord_openrouter.cogs.openrouter.cog.error_embed"
        ) as error_embed_factory:
            asyncio.run(cog.switch_model.callback(cog, ctx, model="any/model"))

        error_embed_factory.assert_called_once_with("rate limited")
        send.assert_awaited_once()
        # No model status embed should be built when the API errored.
        assert (100, 7, "chat") not in cog.channel_model_defaults


class TestModels:
    def test_propagates_api_error_with_error_embed(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.list_models = AsyncMock(
            side_effect=OpenRouterApiError("upstream down")
        )

        with patch(
            "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
        ) as send, patch(
            "discord_openrouter.cogs.openrouter.cog.error_embed"
        ) as error_embed_factory:
            asyncio.run(cog.models.callback(cog, ctx))

        error_embed_factory.assert_called_once_with("upstream down")
        send.assert_awaited_once()

    def test_passes_filters_through(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.list_models = AsyncMock(return_value=[])

        with patch(
            "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
        ), patch(
            "discord_openrouter.cogs.openrouter.cog.build_model_list_embed"
        ):
            asyncio.run(
                cog.models.callback(
                    cog,
                    ctx,
                    query="claude",
                    input_modality="text",
                    output_modality="image",
                    limit=25,
                    refresh=True,
                )
            )

        cog.openrouter_client.list_models.assert_awaited_once_with(
            query="claude",
            input_modality="text",
            output_modality="image",
            limit=25,
            refresh=True,
        )

    def test_default_limit_is_ten_when_none(self):
        cog = _make_cog()
        ctx = _make_ctx()
        cog.openrouter_client.list_models = AsyncMock(return_value=[])

        with patch(
            "discord_openrouter.cogs.openrouter.cog.send_embed_batches", new=AsyncMock()
        ), patch(
            "discord_openrouter.cogs.openrouter.cog.build_model_list_embed"
        ):
            asyncio.run(cog.models.callback(cog, ctx))

        kwargs = cog.openrouter_client.list_models.await_args.kwargs
        assert kwargs["limit"] == 10
        assert kwargs["refresh"] is False
