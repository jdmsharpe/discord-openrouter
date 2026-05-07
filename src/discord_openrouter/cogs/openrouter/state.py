from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, TypeAlias

from discord import Member, Message, User

from ...util import Conversation
from .tool_registry import get_tool_registry
from .views import ButtonView

UserId: TypeAlias = int
ConversationId: TypeAlias = int
ConversationStore: TypeAlias = dict[ConversationId, Conversation]
ViewStore: TypeAlias = dict[ConversationId, tuple[UserId, ButtonView, datetime]]
ViewMessageStore: TypeAlias = dict[ConversationId, tuple[UserId, Message, datetime]]
DailyCostStore: TypeAlias = dict[tuple[UserId, str], tuple[float, datetime]]
Modality: TypeAlias = str  # "chat" | "image" | "video" | "tts" | "stt"
ModalityModelStore: TypeAlias = dict[tuple[int, int, Modality], str]

MAX_ACTIVE_CONVERSATIONS = 100
MAX_VIEW_STATES = 200
CONVERSATION_TTL = timedelta(hours=12)
VIEW_STATE_TTL = timedelta(hours=12)
DAILY_COST_RETENTION_DAYS = 30


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _extract_daily_total(value: float | tuple[float, datetime]) -> float:
    return value[0] if isinstance(value, tuple) else value


def remember_view_state(
    cog,
    user_or_id: Member | User | int,
    conversation_id: ConversationId,
    view: ButtonView,
    message: Message,
) -> None:
    user_id = user_or_id if isinstance(user_or_id, int) else user_or_id.id
    now = _now_utc()
    cog.views[conversation_id] = (user_id, view, now)
    cog.last_view_messages[conversation_id] = (user_id, message, now)


async def strip_previous_view(cog, conversation_id: ConversationId) -> None:
    message_state = cog.last_view_messages.pop(conversation_id, None)
    if message_state is None:
        return
    _, previous_message, _ = message_state
    try:
        await previous_message.edit(view=None)
    except Exception as error:
        cog.logger.debug("Could not edit previous message: %s", error)


async def _drop_conversation_view_state(cog, conversation_id: ConversationId) -> None:
    await strip_previous_view(cog, conversation_id)
    cog.views.pop(conversation_id, None)


async def cleanup_conversation(
    cog, user_or_id: Member | User | int, conversation_id: int | None = None
) -> None:
    user_id = user_or_id if isinstance(user_or_id, int) else user_or_id.id
    if conversation_id is not None:
        cog.conversation_histories.pop(conversation_id, None)
        await _drop_conversation_view_state(cog, conversation_id)
    else:
        matching_ids = [
            convo_id
            for convo_id, conversation in cog.conversation_histories.items()
            if conversation.conversation_starter_id == user_id
        ]
        for convo_id in matching_ids:
            cog.conversation_histories.pop(convo_id, None)
            await _drop_conversation_view_state(cog, convo_id)
    await prune_runtime_state(cog)


async def stop_conversation(cog, conversation_id: int, user_or_id: Member | User | int) -> None:
    await cleanup_conversation(cog, user_or_id, conversation_id)


async def prune_runtime_state(cog) -> None:
    now = _now_utc()
    stale_conversation_ids = [
        conversation_id
        for conversation_id, conversation in cog.conversation_histories.items()
        if now - conversation.updated_at > CONVERSATION_TTL
    ]

    active_conversations = [
        (conversation_id, conversation)
        for conversation_id, conversation in cog.conversation_histories.items()
        if conversation_id not in stale_conversation_ids
    ]
    overflow = len(active_conversations) - MAX_ACTIVE_CONVERSATIONS
    if overflow > 0:
        active_conversations.sort(key=lambda item: item[1].updated_at)
        stale_conversation_ids.extend(
            conversation_id for conversation_id, _ in active_conversations[:overflow]
        )

    for conversation_id in dict.fromkeys(stale_conversation_ids):
        cog.conversation_histories.pop(conversation_id, None)

    tracked_conversation_ids = set(cog.views) | set(cog.last_view_messages)
    stale_view_conversation_ids = []
    for conversation_id in tracked_conversation_ids:
        if conversation_id not in cog.conversation_histories:
            stale_view_conversation_ids.append(conversation_id)
            continue
        message_state = cog.last_view_messages.get(conversation_id)
        if message_state and now - message_state[2] > VIEW_STATE_TTL:
            stale_view_conversation_ids.append(conversation_id)

    for conversation_id in dict.fromkeys(stale_view_conversation_ids):
        await _drop_conversation_view_state(cog, conversation_id)

    overflow_view_states = len(cog.views) - MAX_VIEW_STATES
    if overflow_view_states > 0:
        sorted_view_ids = sorted(
            cog.views, key=lambda conversation_id: cog.views[conversation_id][2]
        )
        for conversation_id in sorted_view_ids[:overflow_view_states]:
            await _drop_conversation_view_state(cog, conversation_id)

    prune_daily_costs(cog)


def prune_daily_costs(cog) -> None:
    cutoff = date.today() - timedelta(days=DAILY_COST_RETENTION_DAYS)
    expired_keys = [key for key in cog.daily_costs if date.fromisoformat(key[1]) < cutoff]
    for key in expired_keys:
        cog.daily_costs.pop(key, None)


def track_daily_cost(cog, user_id: int, cost: float | None) -> float | None:
    if cost is None:
        return None
    prune_daily_costs(cog)
    key = (user_id, date.today().isoformat())
    current_total = _extract_daily_total(cog.daily_costs.get(key, 0.0))
    new_total = current_total + cost
    cog.daily_costs[key] = (new_total, _now_utc())
    return new_total


def create_button_view(
    cog,
    user_or_id: Member | User | int,
    conversation_id: int,
    tools: list[dict[str, Any]] | None = None,
) -> ButtonView:
    user_id = user_or_id if isinstance(user_or_id, int) else user_or_id.id
    return ButtonView(
        conversation_starter_id=user_id,
        conversation_id=conversation_id,
        initial_tools=tools,
        get_conversation=lambda cid: cog.conversation_histories.get(cid),
        on_regenerate=cog.regenerate_conversation_response,
        on_stop=cog._stop_conversation,
        on_tools_changed=lambda selected_values, conversation: handle_tools_changed(
            selected_values,
            conversation,
        ),
    )


def handle_tools_changed(
    selected_values: list[str], conversation: Conversation
) -> tuple[set[str], str | None]:
    registry = get_tool_registry()
    active_names = {value for value in selected_values if value in registry}
    conversation.settings.web_search = "web_search" in active_names
    conversation.settings.datetime = "datetime" in active_names
    conversation.touch()
    return active_names, None


def find_active_conversation(cog, *, channel_id: int, user_id: int) -> Conversation | None:
    matches = [
        conversation
        for conversation in cog.conversation_histories.values()
        if conversation.channel_id == channel_id and conversation.conversation_starter_id == user_id
    ]
    if not matches:
        return None
    matches.sort(key=lambda conversation: conversation.updated_at, reverse=True)
    return matches[0]
