from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from discord import ButtonStyle, Interaction
from discord.ui import Button, View, button


async def _send_interaction_error(interaction: Interaction, context: str, error: Exception) -> None:
    logging.error("Error in %s: %s", context, error, exc_info=True)
    message = f"An error occurred while {context}."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class ButtonView(View):
    def __init__(
        self,
        *,
        conversation_starter_id: int,
        conversation_id: int,
        get_conversation: Callable[[int], Any | None],
        on_regenerate: Callable[[Interaction, Any], Awaitable[None]],
        on_stop: Callable[[int, Any], Awaitable[None]],
    ):
        super().__init__(timeout=None)
        self.conversation_starter_id = conversation_starter_id
        self.conversation_id = conversation_id
        self._get_conversation = get_conversation
        self._on_regenerate = on_regenerate
        self._on_stop = on_stop

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
