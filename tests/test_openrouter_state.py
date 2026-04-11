import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.state import handle_tools_changed, prune_runtime_state
from discord_openrouter.util import ChatSettings, Conversation


def test_prune_runtime_state_removes_stale_entries_and_preserves_active_entries():
    now = datetime.now(timezone.utc)
    stale_conversation = Conversation(
        conversation_id=1,
        conversation_starter_id=11,
        channel_id=100,
        settings=ChatSettings(model="openai/gpt-4o-mini"),
        updated_at=now - timedelta(hours=13),
    )
    active_conversation = Conversation(
        conversation_id=2,
        conversation_starter_id=22,
        channel_id=100,
        settings=ChatSettings(model="minimax/minimax-m2.7"),
        updated_at=now - timedelta(minutes=10),
    )

    stale_message = MagicMock()
    stale_message.edit = AsyncMock()
    orphan_message = MagicMock()
    orphan_message.edit = AsyncMock()
    active_message = MagicMock()
    active_message.edit = AsyncMock()

    old_day = (date.today() - timedelta(days=31)).isoformat()
    today = date.today().isoformat()

    cog = SimpleNamespace(
        logger=MagicMock(),
        conversation_histories={1: stale_conversation, 2: active_conversation},
        views={
            1: (11, MagicMock(), now),
            2: (22, MagicMock(), now),
            3: (33, MagicMock(), now),
        },
        last_view_messages={
            1: (11, stale_message, now),
            2: (22, active_message, now),
            3: (33, orphan_message, now),
        },
        daily_costs={
            (11, old_day): (2.5, now - timedelta(days=31)),
            (22, today): (1.0, now),
        },
    )

    asyncio.run(prune_runtime_state(cog))

    assert set(cog.conversation_histories) == {2}
    assert set(cog.views) == {2}
    assert set(cog.last_view_messages) == {2}
    stale_message.edit.assert_awaited_once_with(view=None)
    orphan_message.edit.assert_awaited_once_with(view=None)
    active_message.edit.assert_not_awaited()
    assert (11, old_day) not in cog.daily_costs
    assert (22, today) in cog.daily_costs


def test_handle_tools_changed_updates_conversation_settings():
    conversation = Conversation(
        conversation_id=1,
        conversation_starter_id=11,
        channel_id=100,
        settings=ChatSettings(
            model="openai/gpt-5.2",
            web_search=False,
            datetime=False,
        ),
    )

    active_names, error = handle_tools_changed(["web_search", "datetime"], conversation)

    assert error is None
    assert active_names == {"web_search", "datetime"}
    assert conversation.settings.web_search is True
    assert conversation.settings.datetime is True


def test_handle_tools_changed_ignores_unknown_values_and_disables_unselected_tools():
    conversation = Conversation(
        conversation_id=1,
        conversation_starter_id=11,
        channel_id=100,
        settings=ChatSettings(
            model="openai/gpt-5.2",
            web_search=True,
            datetime=True,
        ),
    )

    active_names, error = handle_tools_changed(["not_real"], conversation)

    assert error is None
    assert active_names == set()
    assert conversation.settings.web_search is False
    assert conversation.settings.datetime is False


def test_modality_choices_covers_all_expected_modalities():
    from discord_openrouter.cogs.openrouter.command_options import MODALITY_CHOICES

    values = {choice.value for choice in MODALITY_CHOICES}
    assert values == {"chat", "image", "video", "tts", "stt"}
