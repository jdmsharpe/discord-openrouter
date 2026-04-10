import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")

from discord.ui import Button, Select

from discord_openrouter.cogs.openrouter.views import ButtonView


def _make_button_view(
    *,
    initial_tools=None,
    get_conversation=None,
    on_regenerate=None,
    on_stop=None,
    on_tools_changed=None,
):
    return ButtonView(
        conversation_starter_id=123,
        conversation_id=42,
        initial_tools=initial_tools,
        get_conversation=get_conversation or MagicMock(return_value=None),
        on_regenerate=on_regenerate or AsyncMock(),
        on_stop=on_stop or AsyncMock(),
        on_tools_changed=on_tools_changed or MagicMock(return_value=(set(), None)),
    )


class TestButtonView:
    def test_view_includes_tool_select_with_initial_defaults(self):
        view = _make_button_view(initial_tools=[{"type": "openrouter:web_search"}])

        tool_select = next(component for component in view.children if isinstance(component, Select))

        assert tool_select.placeholder == "Tools"
        defaults = {option.value for option in tool_select.options if option.default}
        assert defaults == {"web_search"}

    def test_owner_can_update_tools(self):
        conversation = SimpleNamespace()
        on_tools_changed = MagicMock(return_value=({"datetime"}, None))
        view = _make_button_view(
            get_conversation=MagicMock(return_value=conversation),
            on_tools_changed=on_tools_changed,
        )
        tool_select = next(component for component in view.children if isinstance(component, Select))

        interaction = MagicMock()
        interaction.user.id = 123
        interaction.response.send_message = AsyncMock()
        tool_select._values = ["datetime"]

        asyncio.run(tool_select.callback(interaction))

        on_tools_changed.assert_called_once_with(["datetime"], conversation)
        interaction.response.send_message.assert_awaited_once_with(
            "Tools updated: datetime.",
            ephemeral=True,
            delete_after=3,
        )
        defaults = {option.value for option in tool_select.options if option.default}
        assert defaults == {"datetime"}

    def test_owner_can_regenerate(self):
        conversation = SimpleNamespace()
        on_regenerate = AsyncMock()
        view = _make_button_view(
            get_conversation=MagicMock(return_value=conversation),
            on_regenerate=on_regenerate,
        )
        regenerate_button = next(
            component
            for component in view.children
            if isinstance(component, Button) and component.emoji.name == "🔄"
        )

        interaction = MagicMock()
        interaction.user.id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        asyncio.run(regenerate_button.callback(interaction))

        interaction.response.defer.assert_awaited_once()
        on_regenerate.assert_awaited_once_with(interaction, conversation)
        interaction.followup.send.assert_awaited_once()

    def test_non_owner_cannot_stop(self):
        conversation = SimpleNamespace()
        on_stop = AsyncMock()
        view = _make_button_view(
            get_conversation=MagicMock(return_value=conversation),
            on_stop=on_stop,
        )
        stop_button = next(
            component
            for component in view.children
            if isinstance(component, Button) and component.emoji.name == "⏹️"
        )

        interaction = MagicMock()
        interaction.user.id = 999
        interaction.response.send_message = AsyncMock()

        asyncio.run(stop_button.callback(interaction))

        interaction.response.send_message.assert_awaited_once()
        on_stop.assert_not_awaited()
