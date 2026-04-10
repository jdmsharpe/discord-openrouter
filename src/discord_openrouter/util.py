from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

CHUNK_TEXT_SIZE = 3500
PDF_ENGINE_ALIASES = {
    "pdf-text": "cloudflare-ai",
}
SUPPORTED_PDF_ENGINES = ("cloudflare-ai", "mistral-ocr", "native")
SUPPORTED_PROMPT_CACHE_TTLS = ("5m", "1h")


@dataclass(slots=True)
class ModelPricing:
    prompt: float = 0.0
    completion: float = 0.0
    request: float = 0.0
    image: float = 0.0
    audio: float = 0.0
    web_search: float = 0.0
    internal_reasoning: float = 0.0
    input_cache_read: float = 0.0
    input_cache_write: float = 0.0


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
    cache_write_tokens: int = 0
    input_audio_tokens: int = 0
    input_video_tokens: int = 0
    output_audio_tokens: int = 0
    output_image_tokens: int = 0
    cost: float | None = None
    upstream_inference_cost: float | None = None
    server_tool_use: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class CostBreakdown:
    input: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    output: float = 0.0
    reasoning: float = 0.0
    request: float = 0.0
    web_search: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.input
            + self.cache_read
            + self.cache_write
            + self.output
            + self.reasoning
            + self.request
            + self.web_search
        )


@dataclass(slots=True)
class ChatSettings:
    model: str
    system_prompt: str = "You are a helpful assistant."
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    web_search: bool = False
    datetime: bool = False
    context_compression: bool | None = None
    prompt_cache_ttl: str | None = None
    reasoning_effort: str | None = None
    reasoning_max_tokens: int | None = None
    exclude_reasoning: bool = False
    pdf_engine: str | None = None


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
            web_search=_safe_float(pricing_payload.get("web_search")),
            internal_reasoning=_safe_float(pricing_payload.get("internal_reasoning")),
            input_cache_read=_safe_float(pricing_payload.get("input_cache_read")),
            input_cache_write=_safe_float(pricing_payload.get("input_cache_write")),
        ),
    )


def extract_usage(response_payload: dict[str, Any]) -> ChatUsage:
    usage = response_payload.get("usage") or {}
    completion_tokens = _safe_int(usage.get("completion_tokens") or usage.get("output_tokens"))
    prompt_tokens = _safe_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    cost_details = usage.get("cost_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    reasoning_tokens = _safe_int(
        usage.get("reasoning_tokens")
        or ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))
        or ((usage.get("output_tokens_details") or {}).get("reasoning_tokens"))
    )
    cached_tokens = _safe_int(prompt_details.get("cached_tokens"))
    cache_write_tokens = _safe_int(prompt_details.get("cache_write_tokens"))
    input_audio_tokens = _safe_int(prompt_details.get("audio_tokens"))
    input_video_tokens = _safe_int(prompt_details.get("video_tokens"))
    completion_details = (
        usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    )
    output_audio_tokens = _safe_int(completion_details.get("audio_tokens"))
    output_image_tokens = _safe_int(completion_details.get("image_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return ChatUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        input_audio_tokens=input_audio_tokens,
        input_video_tokens=input_video_tokens,
        output_audio_tokens=output_audio_tokens,
        output_image_tokens=output_image_tokens,
        cost=_safe_float_or_none(usage.get("cost")),
        upstream_inference_cost=_safe_float_or_none(cost_details.get("upstream_inference_cost")),
        server_tool_use=_coerce_int_mapping(usage.get("server_tool_use")),
    )


def calculate_cost_breakdown(
    model_info: ModelInfo | None,
    usage: ChatUsage,
) -> CostBreakdown | None:
    if model_info is None:
        return None
    pricing = model_info.pricing
    web_search_requests = extract_web_search_requests(usage.server_tool_use)
    uncached_prompt_tokens = max(
        usage.prompt_tokens - usage.cached_tokens - usage.cache_write_tokens,
        0,
    )
    non_reasoning_completion_tokens = max(
        usage.completion_tokens - usage.reasoning_tokens,
        0,
    )
    return CostBreakdown(
        input=uncached_prompt_tokens * pricing.prompt,
        cache_read=usage.cached_tokens * pricing.input_cache_read,
        cache_write=usage.cache_write_tokens * pricing.input_cache_write,
        output=non_reasoning_completion_tokens * pricing.completion,
        reasoning=usage.reasoning_tokens * pricing.internal_reasoning,
        request=pricing.request,
        web_search=web_search_requests * pricing.web_search,
    )


def calculate_cost(model_info: ModelInfo | None, usage: ChatUsage) -> float | None:
    breakdown = calculate_cost_breakdown(model_info, usage)
    if breakdown is None:
        return None
    return breakdown.total


def extract_web_search_requests(server_tool_use: dict[str, int]) -> int:
    return max(
        _safe_int(server_tool_use.get("web_search_requests")),
        _safe_int(server_tool_use.get("web_search")),
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


def extract_url_citations(message: dict[str, Any]) -> list[dict[str, str]]:
    annotations = message.get("annotations") or []
    if not isinstance(annotations, list):
        return []

    citations: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for annotation in annotations:
        if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
            continue
        citation_payload = annotation.get("url_citation") or {}
        if not isinstance(citation_payload, dict):
            continue

        url = citation_payload.get("url")
        if not isinstance(url, str):
            continue
        normalized_url = url.strip()
        if not normalized_url or normalized_url in seen_urls:
            continue

        title = citation_payload.get("title")
        content = citation_payload.get("content")
        seen_urls.add(normalized_url)
        citations.append(
            {
                "url": normalized_url,
                "title": title.strip()
                if isinstance(title, str) and title.strip()
                else normalized_url,
                "content": content.strip() if isinstance(content, str) else "",
            }
        )

    return citations


def chunk_text(text: str, *, chunk_size: int = CHUNK_TEXT_SIZE) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    return [normalized[i : i + chunk_size] for i in range(0, len(normalized), chunk_size)]


def truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def normalize_pdf_engine(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    normalized = PDF_ENGINE_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_PDF_ENGINES:
        supported = ", ".join(f"`{engine}`" for engine in SUPPORTED_PDF_ENGINES)
        raise ValueError(f"Unsupported PDF engine `{value}`. Supported values: {supported}.")
    return normalized


def build_pdf_plugins(pdf_engine: str | None) -> list[dict[str, Any]] | None:
    normalized = normalize_pdf_engine(pdf_engine)
    if normalized is None:
        return None
    return [
        {
            "id": "file-parser",
            "pdf": {
                "engine": normalized,
            },
        }
    ]


def build_web_plugin_override(*, enabled: bool) -> list[dict[str, Any]]:
    return [{"id": "web", "enabled": enabled}]


def build_web_search_tools() -> list[dict[str, Any]]:
    return [{"type": "openrouter:web_search"}]


def build_datetime_tools() -> list[dict[str, Any]]:
    return [{"type": "openrouter:datetime"}]


def build_context_compression_plugins(enabled: bool | None) -> list[dict[str, Any]] | None:
    if enabled is None:
        return None
    if enabled:
        return [{"id": "context-compression"}]
    return [{"id": "context-compression", "enabled": False}]


def build_prompt_cache_control(ttl: str | None) -> dict[str, Any] | None:
    normalized = (ttl or "").strip().lower()
    if not normalized:
        return None
    if normalized not in SUPPORTED_PROMPT_CACHE_TTLS:
        supported = ", ".join(f"`{value}`" for value in SUPPORTED_PROMPT_CACHE_TTLS)
        raise ValueError(f"Unsupported prompt cache TTL `{ttl}`. Supported values: {supported}.")

    payload: dict[str, Any] = {"type": "ephemeral"}
    if normalized != "5m":
        payload["ttl"] = normalized
    return payload


def describe_modalities(model_info: ModelInfo) -> str:
    inputs = ", ".join(model_info.input_modalities or ["text"])
    outputs = ", ".join(model_info.output_modalities or ["text"])
    return f"in: {inputs} | out: {outputs}"


def prompt_cache_supported_for_model(model: str) -> bool:
    return model.casefold().startswith("anthropic/")


def describe_chat_settings(settings: ChatSettings) -> str | None:
    parts: list[str] = []
    if settings.pdf_engine:
        parts.append(f"pdf `{settings.pdf_engine}`")
    if settings.context_compression is True:
        parts.append("context compression")
    elif settings.context_compression is False:
        parts.append("context compression off")
    if settings.prompt_cache_ttl:
        parts.append(f"prompt cache `{settings.prompt_cache_ttl}`")
    if settings.web_search:
        parts.append("web search")
    if settings.datetime:
        parts.append("datetime")
    if settings.reasoning_max_tokens is not None:
        parts.append(f"reasoning `{settings.reasoning_max_tokens}` tokens")
    elif settings.reasoning_effort:
        parts.append(f"reasoning `{settings.reasoning_effort}`")
    if settings.exclude_reasoning:
        parts.append("hidden reasoning")
    if not parts:
        return None
    return ", ".join(parts)


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
