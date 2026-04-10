from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import Future as ConcurrentFuture
from typing import Any

from discord import ButtonStyle, Interaction, SelectOption
from discord.ui import Button, Select, View, button

from .tool_registry import get_tool_select_options, is_known_tool, resolve_tool_name


async def _send_interaction_error(interaction: Interaction, context: str, error: Exception) -> None:
    logging.error("Error in %s: %s", context, error, exc_info=True)
    message = f"An error occurred while {context}."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def _build_view_on_running_loop(view: View, *, timeout: float | None) -> None:
    View.__init__(view, timeout=timeout)


def _initialize_view(view: View, *, timeout: float | None) -> None:
    """Build a discord View even when tests construct it outside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_build_view_on_running_loop(view, timeout=timeout))
        finally:
            loop.close()
        view._stopped = ConcurrentFuture()
    else:
        View.__init__(view, timeout=timeout)


class ButtonView(View):
    def __init__(
        self,
        *,
        conversation_starter_id: int,
        conversation_id: int,
        initial_tools: list[dict[str, Any]] | None = None,
        get_conversation: Callable[[int], Any | None],
        on_regenerate: Callable[[Interaction, Any], Awaitable[None]],
        on_stop: Callable[[int, Any], Awaitable[None]],
        on_tools_changed: Callable[[list[str], Any], tuple[set[str], str | None]],
    ):
        _initialize_view(self, timeout=None)
        self.conversation_starter_id = conversation_starter_id
        self.conversation_id = conversation_id
        self._get_conversation = get_conversation
        self._on_regenerate = on_regenerate
        self._on_stop = on_stop
        self._on_tools_changed = on_tools_changed
        self._add_tool_select(initial_tools or [])

    async def wait(self) -> bool:
        """Support wait() even when the view was constructed outside a running loop."""
        if isinstance(self._stopped, ConcurrentFuture):
            return await asyncio.wrap_future(self._stopped)
        return await super().wait()

    def _add_tool_select(self, initial_tools: list[dict[str, Any]]) -> None:
        selected_tool_names = {
            name for tool in initial_tools if (name := resolve_tool_name(tool)) is not None
        }
        options = [
            SelectOption(**option) for option in get_tool_select_options(selected_tool_names)
        ]
        tool_select = Select(
            placeholder="Tools",
            options=options,
            min_values=0,
            max_values=len(options),
            row=1,
        )

        async def _tool_callback(interaction: Interaction) -> None:
            await self.tool_select_callback(interaction, tool_select)

        tool_select.callback = _tool_callback
        self.add_item(tool_select)

    async def tool_select_callback(self, interaction: Interaction, tool_select: Select) -> None:
        try:
            user = interaction.user
            if user is None or user.id != self.conversation_starter_id:
                await interaction.response.send_message(
                    "You are not allowed to change tools for this conversation.",
                    ephemeral=True,
                )
                return

            conversation = self._get_conversation(self.conversation_id)
            if conversation is None:
                await interaction.response.send_message(
                    "No active conversation found.",
                    ephemeral=True,
                )
                return

            selected_values = [value for value in tool_select.values if is_known_tool(value)]
            active_names, error_message = self._on_tools_changed(selected_values, conversation)
            if error_message:
                await interaction.response.send_message(error_message, ephemeral=True)
                return

            for child in self.children:
                if isinstance(child, Select):
                    for option in child.options:
                        option.default = option.value in active_names
                    break

            status = ", ".join(sorted(active_names)) if active_names else "none"
            await interaction.response.send_message(
                f"Tools updated: {status}.",
                ephemeral=True,
                delete_after=3,
            )
        except Exception as error:
            await _send_interaction_error(interaction, "updating tools", error)

    @button(emoji="🔄", style=ButtonStyle.green, row=0)
    async def regenerate_button(self, _: Button, interaction: Interaction):
        try:
            user = interaction.user
            if user is None or user.id != self.conversation_starter_id:
                await interaction.response.send_message(
                    "You are not allowed to regenerate this response.",
                    ephemeral=True,
                )
                return

            conversation = self._get_conversation(self.conversation_id)
            if conversation is None:
                await interaction.response.send_message(
                    "No active conversation found.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            await self._on_regenerate(interaction, conversation)
            await interaction.followup.send(
                "Response regenerated.",
                ephemeral=True,
                delete_after=3,
            )
        except Exception as error:
            await _send_interaction_error(interaction, "regenerating the response", error)

    @button(emoji="⏯️", style=ButtonStyle.gray, row=0)
    async def play_pause_button(self, _: Button, interaction: Interaction):
        try:
            user = interaction.user
            if user is None or user.id != self.conversation_starter_id:
                await interaction.response.send_message(
                    "You are not allowed to pause this conversation.",
                    ephemeral=True,
                )
                return

            conversation = self._get_conversation(self.conversation_id)
            if conversation is None:
                await interaction.response.send_message(
                    "No active conversation found.",
                    ephemeral=True,
                )
                return

            conversation.paused = not conversation.paused
            conversation.touch()
            status = "paused" if conversation.paused else "resumed"
            await interaction.response.send_message(
                f"Conversation {status}. Press again to toggle.",
                ephemeral=True,
                delete_after=3,
            )
        except Exception as error:
            await _send_interaction_error(interaction, "toggling pause", error)

    @button(emoji="⏹️", style=ButtonStyle.blurple, row=0)
    async def stop_button(self, _: Button, interaction: Interaction):
        try:
            user = interaction.user
            if user is None or user.id != self.conversation_starter_id:
                await interaction.response.send_message(
                    "You are not allowed to end this conversation.",
                    ephemeral=True,
                )
                return

            conversation = self._get_conversation(self.conversation_id)
            if conversation is None:
                await interaction.response.send_message(
                    "No active conversation found.",
                    ephemeral=True,
                )
                return

            await self._on_stop(self.conversation_id, self.conversation_starter_id)
            await interaction.response.send_message(
                "Conversation ended.",
                ephemeral=True,
                delete_after=3,
            )
        except Exception as error:
            await _send_interaction_error(interaction, "ending the conversation", error)
