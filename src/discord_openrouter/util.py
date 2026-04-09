from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

CHUNK_TEXT_SIZE = 3500


@dataclass(slots=True)
class ModelPricing:
    prompt: float = 0.0
    completion: float = 0.0
    request: float = 0.0
    image: float = 0.0
    audio: float = 0.0


@dataclass(slots=True)
class ModelInfo:
    id: str
    name: str
    canonical_slug: str | None = None
    description: str | None = None
    context_length: int | None = None
    input_modalities: list[str] = field(default_factory=list)
    output_modalities: list[str] = field(default_factory=list)
    pricing: ModelPricing = field(default_factory=ModelPricing)


@dataclass(slots=True)
class ChatUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost: float | None = None
    server_tool_use: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ChatSettings:
    model: str
    system_prompt: str = "You are a helpful assistant."
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None


@dataclass(slots=True)
class Conversation:
    conversation_id: int
    conversation_starter_id: int
    channel_id: int
    settings: ChatSettings
    messages: list[dict[str, Any]] = field(default_factory=list)
    paused: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def append_user_message(self, message: dict[str, Any]) -> None:
        self.messages.append(deepcopy(message))
        self.touch()

    def append_assistant_message(self, message: dict[str, Any]) -> None:
        self.messages.append(deepcopy(message))
        self.touch()

    def pop_last_assistant_message(self) -> bool:
        if not self.messages:
            return False
        if self.messages[-1].get("role") != "assistant":
            return False
        self.messages.pop()
        self.touch()
        return True

    def build_api_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.settings.system_prompt:
            messages.append({"role": "system", "content": self.settings.system_prompt})
        messages.extend(deepcopy(self.messages))
        return messages


def parse_model_info(raw_model: dict[str, Any]) -> ModelInfo:
    pricing_payload = raw_model.get("pricing") or {}
    architecture = raw_model.get("architecture") or {}
    return ModelInfo(
        id=raw_model.get("id") or "unknown-model",
        name=raw_model.get("name") or raw_model.get("id") or "Unknown Model",
        canonical_slug=raw_model.get("canonical_slug"),
        description=raw_model.get("description"),
        context_length=raw_model.get("context_length"),
        input_modalities=list(architecture.get("input_modalities") or []),
        output_modalities=list(architecture.get("output_modalities") or []),
        pricing=ModelPricing(
            prompt=_safe_float(pricing_payload.get("prompt")),
            completion=_safe_float(pricing_payload.get("completion")),
            request=_safe_float(pricing_payload.get("request")),
            image=_safe_float(pricing_payload.get("image")),
            audio=_safe_float(pricing_payload.get("audio")),
        ),
    )


def extract_usage(response_payload: dict[str, Any]) -> ChatUsage:
    usage = response_payload.get("usage") or {}
    completion_tokens = _safe_int(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )
    prompt_tokens = _safe_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    prompt_details = usage.get("prompt_tokens_details") or {}
    reasoning_tokens = _safe_int(
        usage.get("reasoning_tokens")
        or ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))
        or ((usage.get("output_tokens_details") or {}).get("reasoning_tokens"))
    )
    cached_tokens = _safe_int(prompt_details.get("cached_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return ChatUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        cost=_safe_float_or_none(usage.get("cost")),
        server_tool_use=_coerce_int_mapping(usage.get("server_tool_use")),
    )


def calculate_cost(model_info: ModelInfo | None, usage: ChatUsage) -> float | None:
    if model_info is None:
        return None
    pricing = model_info.pricing
    return (
        usage.prompt_tokens * pricing.prompt
        + usage.completion_tokens * pricing.completion
        + pricing.request
    )


def sanitize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {
        "role": message.get("role", "assistant"),
        "content": message.get("content") or "",
    }
    for key in (
        "reasoning",
        "reasoning_details",
        "annotations",
        "images",
        "tool_calls",
        "refusal",
        "name",
    ):
        value = message.get(key)
        if value not in (None, [], ""):
            sanitized[key] = deepcopy(value)
    return sanitized


def extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "output_text"}:
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(part for part in parts if part).strip()
    return ""


def extract_reasoning_text(message: dict[str, Any]) -> str:
    reasoning = message.get("reasoning")
    parts: list[str] = []
    seen_parts: set[str] = set()
    if isinstance(reasoning, str) and reasoning.strip():
        _append_unique_text_part(parts, seen_parts, reasoning)

    reasoning_details = message.get("reasoning_details") or []
    if isinstance(reasoning_details, list):
        for item in reasoning_details:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning.summary":
                summary = item.get("summary")
                if isinstance(summary, str) and summary.strip():
                    _append_unique_text_part(parts, seen_parts, summary)
            elif item.get("type") == "reasoning.text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    _append_unique_text_part(parts, seen_parts, text)
    return "\n\n".join(part for part in parts if part)


def chunk_text(text: str, *, chunk_size: int = CHUNK_TEXT_SIZE) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    return [normalized[i : i + chunk_size] for i in range(0, len(normalized), chunk_size)]


def truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def describe_modalities(model_info: ModelInfo) -> str:
    inputs = ", ".join(model_info.input_modalities or ["text"])
    outputs = ", ".join(model_info.output_modalities or ["text"])
    return f"in: {inputs} | out: {outputs}"


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, raw_count in value.items():
        if not isinstance(key, str):
            continue
        count = _safe_int(raw_count)
        if count > 0:
            result[key] = count
    return result


def _append_unique_text_part(parts: list[str], seen_parts: set[str], value: str) -> None:
    normalized = value.strip()
    if not normalized or normalized in seen_parts:
        return
    seen_parts.add(normalized)
    parts.append(normalized)
