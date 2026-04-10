from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    label: str
    description: str
    runtime_type: str
    payload_factory: Callable[[], dict[str, Any]]


class ToolSelectOption(TypedDict):
    label: str
    value: str
    description: str
    default: bool


def _build_web_search_tool() -> dict[str, Any]:
    return {"type": "openrouter:web_search"}


def _build_datetime_tool() -> dict[str, Any]:
    return {"type": "openrouter:datetime"}


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "web_search": ToolDefinition(
        name="web_search",
        label="Web Search",
        description="Ground answers with live web results.",
        runtime_type="openrouter:web_search",
        payload_factory=_build_web_search_tool,
    ),
    "datetime": ToolDefinition(
        name="datetime",
        label="Datetime",
        description="Let the model check the current date and time.",
        runtime_type="openrouter:datetime",
        payload_factory=_build_datetime_tool,
    ),
}


def get_tool_registry() -> dict[str, ToolDefinition]:
    return TOOL_REGISTRY


def get_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(TOOL_REGISTRY.values())


def is_known_tool(value: str) -> bool:
    return value in TOOL_REGISTRY


def resolve_tool_name(tool: dict[str, Any]) -> str | None:
    tool_type = tool.get("type")
    if not isinstance(tool_type, str):
        return None

    for definition in TOOL_REGISTRY.values():
        if definition.runtime_type == tool_type:
            return definition.name
    return None


def get_tool_select_options(selected_tool_names: set[str] | None = None) -> list[ToolSelectOption]:
    selected_tool_names = selected_tool_names or set()
    return [
        {
            "label": definition.label,
            "value": definition.name,
            "description": definition.description,
            "default": definition.name in selected_tool_names,
        }
        for definition in get_tool_definitions()
    ]


def build_runtime_tools(selected_tool_names: list[str] | set[str]) -> list[dict[str, Any]]:
    selected = set(selected_tool_names)
    return [
        definition.payload_factory()
        for definition in get_tool_definitions()
        if definition.name in selected
    ]

