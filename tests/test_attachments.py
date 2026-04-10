import asyncio
import base64
from dataclasses import dataclass

from discord_openrouter.cogs.openrouter.attachments import (
    build_attachment_parts,
    summarize_attachment_parts,
)


@dataclass
class _FakeAttachment:
    filename: str
    url: str
    size: int
    content_type: str | None


def test_build_attachment_parts_normalizes_supported_modalities(monkeypatch):
    sample_bytes = b"sample-bytes"

    async def _fake_download(_url: str) -> bytes:
        return sample_bytes

    monkeypatch.setattr(
        "discord_openrouter.cogs.openrouter.attachments._download_attachment_bytes",
        _fake_download,
    )

    attachments = [
        _FakeAttachment(
            filename="photo.png",
            url="https://cdn.example/photo.png",
            size=1024,
            content_type="image/png",
        ),
        _FakeAttachment(
            filename="report.pdf",
            url="https://cdn.example/report.pdf",
            size=2048,
            content_type="application/pdf",
        ),
        _FakeAttachment(
            filename="clip.mp3",
            url="https://cdn.example/clip.mp3",
            size=2048,
            content_type="audio/mpeg",
        ),
        _FakeAttachment(
            filename="movie.mp4",
            url="https://cdn.example/movie.mp4",
            size=4096,
            content_type="video/mp4",
        ),
        _FakeAttachment(
            filename="notes.txt",
            url="https://cdn.example/notes.txt",
            size=512,
            content_type="text/plain",
        ),
    ]

    parts = asyncio.run(build_attachment_parts(attachments))

    assert parts[0] == {
        "type": "image_url",
        "image_url": {"url": "https://cdn.example/photo.png"},
    }
    assert parts[1] == {
        "type": "file",
        "file": {
            "filename": "report.pdf",
            "file_data": "https://cdn.example/report.pdf",
        },
    }
    assert parts[2] == {
        "type": "input_audio",
        "input_audio": {
            "data": base64.b64encode(sample_bytes).decode("ascii"),
            "format": "mp3",
        },
    }
    assert parts[3] == {
        "type": "video_url",
        "video_url": {
            "url": f"data:video/mp4;base64,{base64.b64encode(sample_bytes).decode('ascii')}",
        },
    }
    assert parts[4] == {
        "type": "file",
        "file": {
            "filename": "notes.txt",
            "file_data": f"data:text/plain;base64,{base64.b64encode(sample_bytes).decode('ascii')}",
        },
    }


def test_summarize_attachment_parts_tracks_modalities_and_pdfs():
    summary = summarize_attachment_parts(
        [
            {
                "type": "image_url",
                "image_url": {"url": "https://cdn.example/photo.png"},
            },
            {
                "type": "input_audio",
                "input_audio": {"data": "abc", "format": "mp3"},
            },
            {
                "type": "video_url",
                "video_url": {"url": "data:video/mp4;base64,abc"},
            },
            {
                "type": "file",
                "file": {
                    "filename": "report.pdf",
                    "file_data": "https://cdn.example/report.pdf",
                },
            },
            {
                "type": "file",
                "file": {
                    "filename": "notes.txt",
                    "file_data": "data:text/plain;base64,abc",
                },
            },
        ]
    )

    assert summary.has_pdf is True
    assert summary.required_input_modalities == frozenset({"audio", "file", "image", "video"})
