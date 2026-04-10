from __future__ import annotations

import base64
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 60.0

AUDIO_FORMAT_ALIASES = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/mp4": "mp4",
    "audio/webm": "webm",
}


class AttachmentLike(Protocol):
    filename: str
    url: str
    size: int
    content_type: str | None


class AttachmentInputError(RuntimeError):
    """Raised when a Discord attachment cannot be normalized for OpenRouter."""


@dataclass(slots=True, frozen=True)
class AttachmentRequirements:
    required_input_modalities: frozenset[str] = field(default_factory=frozenset)
    has_pdf: bool = False


async def build_attachment_parts(attachments: Sequence[AttachmentLike]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for attachment in attachments:
        content_type = _guess_content_type(attachment)
        if attachment.size and attachment.size > MAX_ATTACHMENT_SIZE:
            raise AttachmentInputError(
                f"Attachment `{attachment.filename}` exceeds the 20 MiB limit supported by this bot."
            )

        if content_type.startswith("image/"):
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": attachment.url},
                }
            )
            continue

        if content_type == "application/pdf":
            parts.append(
                {
                    "type": "file",
                    "file": {
                        "filename": attachment.filename,
                        "file_data": attachment.url,
                    },
                }
            )
            continue

        raw_bytes = await _download_attachment_bytes(attachment.url)
        if content_type.startswith("audio/"):
            parts.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": base64.b64encode(raw_bytes).decode("ascii"),
                        "format": _audio_format(attachment.filename, content_type),
                    },
                }
            )
            continue

        data_url = _build_data_url(content_type, raw_bytes)
        if content_type.startswith("video/"):
            parts.append(
                {
                    "type": "video_url",
                    "video_url": {"url": data_url},
                }
            )
            continue

        parts.append(
            {
                "type": "file",
                "file": {
                    "filename": attachment.filename,
                    "file_data": data_url,
                },
            }
        )

    return parts


def build_user_content(prompt: str | None, attachment_parts: Sequence[dict[str, Any]]) -> Any:
    normalized_prompt = (prompt or "").strip()
    if not attachment_parts:
        return normalized_prompt

    parts: list[dict[str, Any]] = []
    if normalized_prompt:
        parts.append({"type": "text", "text": normalized_prompt})
    parts.extend(attachment_parts)
    return parts


def summarize_attachment_parts(
    attachment_parts: Sequence[dict[str, Any]],
) -> AttachmentRequirements:
    required_input_modalities: set[str] = set()
    has_pdf = False

    for part in attachment_parts:
        part_type = str(part.get("type") or "").strip().lower()
        if part_type == "image_url":
            required_input_modalities.add("image")
            continue
        if part_type == "input_audio":
            required_input_modalities.add("audio")
            continue
        if part_type == "video_url":
            required_input_modalities.add("video")
            continue
        if part_type != "file":
            continue

        file_payload = part.get("file") or {}
        if not isinstance(file_payload, dict):
            required_input_modalities.add("file")
            continue

        filename = str(file_payload.get("filename") or "").strip().lower()
        file_data = str(file_payload.get("file_data") or "").strip().lower()
        if filename.endswith(".pdf") or file_data.startswith("data:application/pdf"):
            has_pdf = True
        else:
            required_input_modalities.add("file")

    return AttachmentRequirements(
        required_input_modalities=frozenset(required_input_modalities),
        has_pdf=has_pdf,
    )


async def _download_attachment_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(url)
    response.raise_for_status()
    return response.content


def _guess_content_type(attachment: AttachmentLike) -> str:
    if attachment.content_type:
        return attachment.content_type.split(";", 1)[0].strip().lower()
    guessed_type = mimetypes.guess_type(attachment.filename)[0]
    return (guessed_type or "application/octet-stream").lower()


def _build_data_url(content_type: str, raw_bytes: bytes) -> str:
    encoded = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _audio_format(filename: str, content_type: str) -> str:
    if content_type in AUDIO_FORMAT_ALIASES:
        return AUDIO_FORMAT_ALIASES[content_type]
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return "mp3"
