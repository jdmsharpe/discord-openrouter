from __future__ import annotations

from collections.abc import Iterable

from discord import Colour, Embed

from ...cost_line import count_label, format_cost_line
from ...util import (
    ChatUsage,
    ModelInfo,
    chunk_text,
    describe_modalities,
    extract_web_search_requests,
    truncate_text,
)


def _fit_markdown_entries(entries: list[str], max_length: int = 4000) -> str:
    """Fit complete Markdown entries without slicing through links."""

    accepted: list[str] = []
    for entry in entries:
        candidate = "\n".join([*accepted, entry])
        if len(candidate) > max_length:
            break
        accepted.append(entry)
    return "\n".join(accepted)


def error_embed(description: str) -> Embed:
    return Embed(
        title="Error",
        description=truncate_text(description, 4000),
        color=Colour.red(),
    )


def append_response_embeds(embeds: list[Embed], text: str) -> None:
    chunks = chunk_text(text or "(No text content returned.)") or ["(No text content returned.)"]
    for index, chunk in enumerate(chunks, start=1):
        title = "Response" if index == 1 else f"Response (Part {index})"
        embeds.append(Embed(title=title, description=chunk, color=Colour.blue()))


def append_reasoning_embeds(embeds: list[Embed], reasoning_text: str) -> None:
    if not reasoning_text:
        return
    for index, chunk in enumerate(chunk_text(reasoning_text, chunk_size=3000), start=1):
        title = "Thinking" if index == 1 else f"Thinking (Part {index})"
        embeds.append(
            Embed(
                title=title,
                description=f"||{chunk}||",
                color=Colour.light_grey(),
            )
        )


def append_citations_embed(embeds: list[Embed], citations: list[dict[str, str]]) -> None:
    if not citations:
        return

    lines = [
        f"{index}. [{citation['title']}]({citation['url']})"
        for index, citation in enumerate(citations[:10], start=1)
    ]
    if not lines:
        return

    description = _fit_markdown_entries(lines)
    if not description:
        return

    embeds.append(
        Embed(
            title="Sources",
            description=description,
            color=Colour.blue(),
        )
    )


def append_usage_embed(
    embeds: list[Embed],
    *,
    usage: ChatUsage,
    request_cost: float | None,
    daily_cost: float | None,
    request_cost_is_estimate: bool = False,
) -> None:
    details: list[str] = []
    web_search_requests = extract_web_search_requests(usage.server_tool_use)
    if web_search_requests:
        details.append(count_label(web_search_requests, "search", "searches"))
    line = format_cost_line(
        request_cost,
        daily_cost,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        thinking_tokens=usage.reasoning_tokens,
        details=details,
        estimated=request_cost_is_estimate,
    )
    embeds.append(Embed(description=line, color=Colour.blue()))


def append_flat_pricing_embed(
    embeds: list[Embed],
    *,
    request_cost: float | None,
    daily_cost: float | None,
    details: Iterable[str] = (),
    request_cost_is_estimate: bool = False,
    usage: ChatUsage | None = None,
) -> None:
    """Append the one-line cost embed for an image, speech or video command.

    Token counts show only when ``usage`` reports them; a zero count is left out.
    """
    line = format_cost_line(
        request_cost,
        daily_cost,
        input_tokens=(usage.prompt_tokens or None) if usage else None,
        output_tokens=(usage.completion_tokens or None) if usage else None,
        cached_tokens=usage.cached_tokens if usage else 0,
        thinking_tokens=usage.reasoning_tokens if usage else 0,
        details=details,
        estimated=request_cost_is_estimate,
    )
    if not line:
        return
    embeds.append(Embed(description=line, color=Colour.blue()))


def build_model_status_embed(
    *,
    title: str,
    model: str,
    description: str | None = None,
    model_info: ModelInfo | None = None,
) -> Embed:
    lines = [f"**Model:** `{model}`"]
    if model_info is not None:
        lines.append(f"**Input:** {', '.join(model_info.input_modalities or ['text'])}")
        lines.append(f"**Output:** {', '.join(model_info.output_modalities or ['text'])}")
    if description:
        lines.append(description)
    return Embed(title=title, description="\n".join(lines), color=Colour.green())


def build_model_list_embed(
    models,
    *,
    query: str | None,
    input_modality: str | None = None,
    output_modality: str | None = None,
) -> Embed:
    if not models:
        description = "No models matched your query."
    else:
        lines = []
        if input_modality or output_modality:
            filters = []
            if input_modality:
                filters.append(f"in={input_modality}")
            if output_modality:
                filters.append(f"out={output_modality}")
            lines.append(f"**Filters:** {' | '.join(filters)}")
        for model in models:
            context = f"{model.context_length:,}" if model.context_length else "unknown"
            lines.append(
                f"`{model.id}`\n{model.name} | ctx {context} | {describe_modalities(model)}"
            )
        description = "\n\n".join(lines)

    title = "Available Models" if not query else f"Model Search: {query}"
    return Embed(
        title=title,
        description=truncate_text(description, 4000),
        color=Colour.blue(),
    )


_MODALITY_LABELS: dict[str, str] = {
    "chat": "Chat",
    "image": "Image",
    "video": "Video",
    "tts": "TTS",
    "stt": "STT",
}
_MODALITY_ORDER = ["chat", "image", "video", "tts", "stt"]


def build_current_model_embed(
    *,
    active_model: str | None,
    active_options: str | None,
    channel_defaults: dict[str, str],
    global_defaults: dict[str, str | None],
) -> Embed:
    sections: list[str] = []
    for modality in _MODALITY_ORDER:
        label = _MODALITY_LABELS[modality]
        lines = [f"**{label}**"]
        if modality == "chat" and active_model:
            lines.append(f"  Active conversation: `{active_model}`")
            if active_options:
                lines.append(f"  Active options: {active_options}")
        channel_default = channel_defaults.get(modality)
        if channel_default:
            lines.append(f"  Channel default: `{channel_default}`")
        global_default = global_defaults.get(modality)
        if global_default:
            lines.append(f"  Global default: `{global_default}`")
        sections.append("\n".join(lines))
    return Embed(
        title="Current Model Settings",
        description="\n\n".join(sections),
        color=Colour.blue(),
    )
