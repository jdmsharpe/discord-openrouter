from __future__ import annotations

from discord import Colour, Embed

from ...util import (
    ChatUsage,
    ModelInfo,
    calculate_cost_breakdown,
    chunk_text,
    describe_modalities,
    extract_web_search_requests,
    truncate_text,
)


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

    embeds.append(
        Embed(
            title="Sources",
            description=truncate_text("\n".join(lines), 4000),
            color=Colour.blue(),
        )
    )


def append_usage_embed(
    embeds: list[Embed],
    *,
    usage: ChatUsage,
    request_cost: float | None,
    daily_cost: float | None,
    model_info: ModelInfo | None = None,
) -> None:
    parts: list[str] = []
    if request_cost is not None:
        parts.append(f"${request_cost:.4f}")

    in_part = f"{usage.prompt_tokens:,} tokens in"
    in_details: list[str] = []
    if usage.cached_tokens:
        in_details.append(f"{usage.cached_tokens:,} cached")
    if usage.cache_write_tokens:
        in_details.append(f"{usage.cache_write_tokens:,} cache write")
    if usage.input_audio_tokens:
        in_details.append(f"{usage.input_audio_tokens:,} audio")
    if usage.input_video_tokens:
        in_details.append(f"{usage.input_video_tokens:,} video")
    if in_details:
        in_part += f" ({', '.join(in_details)})"

    out_part = f"{usage.completion_tokens:,} tokens out"
    out_details: list[str] = []
    if usage.reasoning_tokens:
        out_details.append(f"{usage.reasoning_tokens:,} reasoning")
    if usage.output_audio_tokens:
        out_details.append(f"{usage.output_audio_tokens:,} audio")
    if usage.output_image_tokens:
        out_details.append(f"{usage.output_image_tokens:,} image")
    if out_details:
        out_part += f" ({', '.join(out_details)})"
    parts.append(f"{in_part} / {out_part}")

    web_search_requests = extract_web_search_requests(usage.server_tool_use)
    if web_search_requests:
        suffix = "es" if web_search_requests != 1 else ""
        parts.append(f"{web_search_requests} search{suffix}")
    if daily_cost is not None:
        parts.append(f"daily ${daily_cost:.2f}")

    description = " · ".join(parts)
    cost_detail_line = _build_cost_detail_line(usage=usage, model_info=model_info)
    if cost_detail_line:
        description += "\n" + cost_detail_line

    embeds.append(Embed(description=description, color=Colour.blue()))


def append_flat_pricing_embed(
    embeds: list[Embed],
    *,
    request_cost: float | None,
    daily_cost: float | None,
    details: str | None = None,
) -> None:
    parts: list[str] = []
    if request_cost is not None:
        parts.append(f"${request_cost:.4f}")
    if details:
        parts.append(details)
    if daily_cost is not None:
        parts.append(f"daily ${daily_cost:.2f}")
    if not parts:
        return
    embeds.append(Embed(description=" · ".join(parts), color=Colour.blue()))


def _build_cost_detail_line(*, usage: ChatUsage, model_info: ModelInfo | None) -> str | None:
    breakdown = calculate_cost_breakdown(model_info, usage)
    parts: list[str] = []
    if breakdown is not None:
        _append_cost_part(parts, "input", breakdown.input)
        _append_cost_part(parts, "cache read", breakdown.cache_read)
        _append_cost_part(parts, "cache write", breakdown.cache_write)
        _append_cost_part(parts, "output", breakdown.output)
        _append_cost_part(parts, "reasoning", breakdown.reasoning)
        _append_cost_part(parts, "request", breakdown.request)
        _append_cost_part(parts, "search", breakdown.web_search)
    if usage.upstream_inference_cost is not None:
        _append_cost_part(parts, "upstream", usage.upstream_inference_cost)
    return " · ".join(parts) or None


def _append_cost_part(parts: list[str], label: str, amount: float) -> None:
    if amount <= 0:
        return
    parts.append(f"{label} ${amount:.4f}")


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


def build_current_model_embed(
    *,
    active_model: str | None,
    active_options: str | None,
    channel_default: str | None,
    global_default: str,
) -> Embed:
    lines = [
        f"**Active conversation:** `{active_model or 'none'}`",
        f"**Channel default:** `{channel_default or 'none'}`",
        f"**Global fallback:** `{global_default}`",
    ]
    if active_options:
        lines.append(f"**Active options:** {active_options}")
    return Embed(
        title="Current Model State",
        description="\n".join(lines),
        color=Colour.blue(),
    )
