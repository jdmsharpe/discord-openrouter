import asyncio
import base64
import json
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from discord_openrouter.cogs.openrouter import client as client_module
from discord_openrouter.cogs.openrouter.client import (
    OpenRouterApiError,
    OpenRouterClient,
    _collect_audio_stream,
)
from discord_openrouter.cogs.openrouter.command_options import REASONING_EFFORT_CHOICES
from discord_openrouter.util import (
    ModelInfo,
    ModelPricing,
    extract_url_citations,
    extract_usage,
    extract_web_search_requests,
    sanitize_assistant_message,
)

_BASIC_CHAT_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
}

# Shape of a live `openrouter:web_search` response from deepseek/deepseek-v4-flash.
_WEB_SEARCH_CHAT_RESPONSE = {
    "id": "gen-web-search",
    "model": "deepseek/deepseek-v4-flash",
    "choices": [
        {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "OpenRouter shipped two changes this week.",
                "refusal": None,
                "reasoning": None,
                "annotations": [
                    {
                        "type": "url_citation",
                        "url_citation": {
                            "url": "https://openrouter.ai/announcements/first",
                            "title": "First announcement",
                            "content": "Details of the first change.",
                            "start_index": 0,
                            "end_index": 10,
                        },
                    },
                    {
                        "type": "url_citation",
                        "url_citation": {
                            "url": "https://openrouter.ai/announcements/second",
                            "title": "Second announcement",
                            "content": "Details of the second change.",
                            "start_index": 11,
                            "end_index": 20,
                        },
                    },
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 5012,
        "completion_tokens": 310,
        "total_tokens": 5322,
        "cost": 0.0213,
        "server_tool_use_details": {
            "web_search_requests": 2,
            "tool_calls_requested": 2,
            "tool_calls_executed": 2,
        },
    },
}

# Shape of a live image response from google/gemini-3.1-flash-image.
_IMAGE_CHAT_RESPONSE = {
    "id": "gen-image",
    "model": "google/gemini-3.1-flash-image",
    "choices": [
        {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "",
                "images": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
                    }
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 12,
        "completion_tokens": 1120,
        "total_tokens": 1132,
        "cost": 0.0672035,
        "completion_tokens_details": {
            "reasoning_tokens": 0,
            "image_tokens": 1120,
            "audio_tokens": 0,
        },
    },
}


class _FakeChatHttpResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}

    def json(self):
        return self._payload


def _install_fake_chat_http(monkeypatch, *, status_code=200, payload=None):
    """Replace `httpx` in client.py; every request returns `payload` and is recorded."""
    calls: list[dict] = []
    response_payload = _BASIC_CHAT_RESPONSE if payload is None else payload

    class _FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, *, headers=None, json=None, params=None):
            calls.append({"method": method, "url": url, "headers": headers, "json": json})
            return _FakeChatHttpResponse(status_code, response_payload)

    fake_httpx = SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)
    return calls


def test_create_chat_completion_posts_payload_with_attribution_headers(monkeypatch):
    calls = _install_fake_chat_http(monkeypatch)
    client = OpenRouterClient(
        api_key="test-key",
        site_url="https://example.com",
        app_name="discord-openrouter",
        app_categories="productivity,discord bots",
    )

    payload = asyncio.run(
        client.create_chat_completion(
            model="moonshotai/kimi-k2.6",
            messages=[{"role": "user", "content": "hello"}],
            modalities=["image", "text"],
            image_config={"aspect_ratio": "16:9", "image_size": "2K"},
            plugins=[{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}],
            tools=[{"type": "openrouter:web_search"}],
            cache_control={"type": "ephemeral", "ttl": "1h"},
            temperature=0.5,
            top_p=0.9,
            max_tokens=256,
            reasoning_effort="high",
            user="123",
            session_id="abc",
        )
    )

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == f"{client_module.OPENROUTER_BASE_URL}/chat/completions"
    assert calls[0]["headers"] == {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://example.com",
        "X-OpenRouter-Title": "discord-openrouter",
        "X-OpenRouter-Categories": "productivity,discord bots",
    }
    # OpenRouter deprecated `max_tokens`; the client sends `max_completion_tokens` instead.
    assert calls[0]["json"] == {
        "model": "moonshotai/kimi-k2.6",
        "messages": [{"role": "user", "content": "hello"}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": "16:9", "image_size": "2K"},
        "plugins": [{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}],
        "tools": [{"type": "openrouter:web_search"}],
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "temperature": 0.5,
        "top_p": 0.9,
        "max_completion_tokens": 256,
        "reasoning": {"effort": "high"},
        "user": "123",
        "session_id": "abc",
    }
    assert payload == _BASIC_CHAT_RESPONSE


def test_create_chat_completion_omits_categories_when_unset(monkeypatch):
    calls = _install_fake_chat_http(monkeypatch)
    client = OpenRouterClient(api_key="test-key", site_url="https://example.com")

    asyncio.run(
        client.create_chat_completion(
            model="moonshotai/kimi-k2.6",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert "X-OpenRouter-Categories" not in calls[0]["headers"]
    assert "X-OpenRouter-Title" not in calls[0]["headers"]
    assert calls[0]["json"] == {
        "model": "moonshotai/kimi-k2.6",
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_create_chat_completion_sends_exactly_the_effort_reasoning_object(monkeypatch):
    """The `reasoning` object on the wire is `{"effort": <choice>}` for every offered effort."""
    calls = _install_fake_chat_http(monkeypatch)
    client = OpenRouterClient(api_key="test-key")

    async def send_every_effort():
        for choice in REASONING_EFFORT_CHOICES:
            await client.create_chat_completion(
                model="anthropic/claude-sonnet-4.5",
                messages=[{"role": "user", "content": "hello"}],
                reasoning_effort=choice.value,
            )

    asyncio.run(send_every_effort())

    assert [call["json"]["reasoning"] for call in calls] == [
        {"effort": choice.value} for choice in REASONING_EFFORT_CHOICES
    ]


def test_create_chat_completion_omits_reasoning_without_effort(monkeypatch):
    calls = _install_fake_chat_http(monkeypatch)
    client = OpenRouterClient(api_key="test-key")

    asyncio.run(
        client.create_chat_completion(
            model="openai/gpt-5.2",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert "reasoning" not in calls[0]["json"]


def test_create_chat_completion_returns_web_search_annotations_and_tool_counts(monkeypatch):
    _install_fake_chat_http(monkeypatch, payload=_WEB_SEARCH_CHAT_RESPONSE)
    client = OpenRouterClient(api_key="test-key")

    payload = asyncio.run(
        client.create_chat_completion(
            model="deepseek/deepseek-v4-flash",
            messages=[{"role": "user", "content": "What changed in OpenRouter this week?"}],
            tools=[{"type": "openrouter:web_search"}],
        )
    )

    assert payload == _WEB_SEARCH_CHAT_RESPONSE
    assistant_message = sanitize_assistant_message(payload["choices"][0]["message"])
    assert extract_url_citations(assistant_message) == [
        {
            "url": "https://openrouter.ai/announcements/first",
            "title": "First announcement",
            "content": "Details of the first change.",
        },
        {
            "url": "https://openrouter.ai/announcements/second",
            "title": "Second announcement",
            "content": "Details of the second change.",
        },
    ]
    usage = extract_usage(payload)
    assert usage.server_tool_use == {
        "web_search_requests": 2,
        "tool_calls_requested": 2,
        "tool_calls_executed": 2,
    }
    assert extract_web_search_requests(usage.server_tool_use) == 2


def test_create_chat_completion_returns_image_tokens_and_images(monkeypatch):
    _install_fake_chat_http(monkeypatch, payload=_IMAGE_CHAT_RESPONSE)
    client = OpenRouterClient(api_key="test-key")

    payload = asyncio.run(
        client.create_chat_completion(
            model="google/gemini-3.1-flash-image",
            messages=[{"role": "user", "content": "A lighthouse in a storm."}],
            modalities=["image", "text"],
        )
    )

    assert payload == _IMAGE_CHAT_RESPONSE
    assistant_message = sanitize_assistant_message(payload["choices"][0]["message"])
    assert assistant_message["images"] == _IMAGE_CHAT_RESPONSE["choices"][0]["message"]["images"]
    usage = extract_usage(payload)
    assert usage.output_image_tokens == 1120
    assert usage.cost == 0.0672035


def test_create_chat_completion_maps_4xx_to_api_error(monkeypatch):
    calls = _install_fake_chat_http(
        monkeypatch,
        status_code=400,
        payload={"error": {"code": 400, "message": "nope/model is not a valid model ID"}},
    )
    client = OpenRouterClient(api_key="test-key")

    with pytest.raises(OpenRouterApiError, match="nope/model is not a valid model ID"):
        asyncio.run(
            client.create_chat_completion(
                model="nope/model",
                messages=[{"role": "user", "content": "hello"}],
            )
        )

    # 4xx responses other than 429 are returned by the retry helper without a retry.
    assert len(calls) == 1


def test_create_chat_completion_raises_on_error_body_with_http_200(monkeypatch):
    _install_fake_chat_http(
        monkeypatch,
        payload={"error": {"code": 502, "message": "Provider returned error"}},
    )
    client = OpenRouterClient(api_key="test-key")

    with pytest.raises(OpenRouterApiError, match="Provider returned error"):
        asyncio.run(
            client.create_chat_completion(
                model="deepseek/deepseek-v4-flash",
                messages=[{"role": "user", "content": "hello"}],
            )
        )


def test_request_headers_use_documented_openrouter_names():
    client = OpenRouterClient(
        api_key="test-key",
        site_url="https://example.com",
        app_name="discord-openrouter",
        app_categories="productivity,discord bots",
    )

    headers = client._request_headers()

    assert headers["Authorization"] == "Bearer test-key"
    assert headers["HTTP-Referer"] == "https://example.com"
    assert headers["X-OpenRouter-Title"] == "discord-openrouter"
    assert headers["X-OpenRouter-Categories"] == "productivity,discord bots"
    assert "X-Title" not in headers


def test_list_models_uses_cache_and_filters(monkeypatch):
    client = OpenRouterClient(api_key="test-key", model_cache_ttl_seconds=300)
    client._fetch_models_from_api = AsyncMock(
        return_value=[
            ModelInfo(
                id="openai/gpt-4o-mini",
                name="GPT-4o Mini",
                pricing=ModelPricing(),
                input_modalities=["text", "image"],
                output_modalities=["text"],
            ),
            ModelInfo(
                id="openai/gpt-audio-mini",
                name="GPT Audio Mini",
                pricing=ModelPricing(),
                input_modalities=["text"],
                output_modalities=["text", "audio"],
            ),
            ModelInfo(
                id="moonshotai/kimi-k2.6",
                name="Kimi K2.6",
                pricing=ModelPricing(),
                input_modalities=["text"],
                output_modalities=["text"],
            ),
            ModelInfo(
                id="anthropic/claude-sonnet-4.5",
                name="Claude Sonnet 4.5",
                input_modalities=["text", "image", "file"],
                output_modalities=["text"],
            ),
        ]
    )

    first = asyncio.run(client.list_models(query="kimi", limit=5))
    second = asyncio.run(client.list_models(query="mini", limit=5))
    third = asyncio.run(client.list_models(output_modality="audio", limit=5))
    fourth = asyncio.run(client.list_models(input_modality="image", limit=5))

    assert [model.id for model in first] == ["moonshotai/kimi-k2.6"]
    assert [model.id for model in second] == [
        "openai/gpt-audio-mini",
        "openai/gpt-4o-mini",
    ]
    assert [model.id for model in third] == ["openai/gpt-audio-mini"]
    assert [model.id for model in fourth] == [
        "anthropic/claude-sonnet-4.5",
        "openai/gpt-4o-mini",
    ]
    client._fetch_models_from_api.assert_awaited_once()


def test_get_model_prefers_exact_match(monkeypatch):
    client = OpenRouterClient(api_key="test-key")
    client.list_models = AsyncMock(
        return_value=[
            ModelInfo(id="openai/gpt-4o-mini", name="GPT-4o Mini"),
            ModelInfo(id="moonshotai/kimi-k2.6", name="Kimi K2.6"),
        ]
    )

    model = asyncio.run(client.get_model("moonshotai/kimi-k2.6"))

    assert model is not None
    assert model.id == "moonshotai/kimi-k2.6"


def test_fetch_models_fallback_requests_all_modalities(monkeypatch):
    class _FakeHttpResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        calls: ClassVar[list] = []

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, *, headers=None, params=None, json=None):
            self.calls.append({"method": method, "url": url, "headers": headers, "params": params})
            if url.endswith("/models/user"):
                return _FakeHttpResponse(404, {"error": {"message": "not found"}})
            return _FakeHttpResponse(
                200,
                {
                    "data": [
                        {
                            "id": "openai/gpt-image-1",
                            "name": "GPT Image 1",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["image"],
                            },
                            "pricing": {},
                        }
                    ]
                },
            )

    fake_httpx = SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)

    client = OpenRouterClient(api_key="test-key")
    models = asyncio.run(client._fetch_models_from_api())

    assert [model.id for model in models] == ["openai/gpt-image-1"]
    assert _FakeAsyncClient.calls[0]["url"].endswith("/models/user")
    assert _FakeAsyncClient.calls[0]["params"] == {"output_modalities": "all"}
    assert _FakeAsyncClient.calls[1]["url"].endswith("/models")
    assert _FakeAsyncClient.calls[1]["params"] == {"output_modalities": "all"}


def test_fetch_models_primary_requests_all_modalities(monkeypatch, mixed_modality_catalog):
    """`/models/user` defaults to text-output models, so the filter goes on the first call too."""

    class _FakeHttpResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        calls: ClassVar[list] = []

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, *, headers=None, params=None, json=None):
            self.calls.append({"method": method, "url": url, "params": params})
            return _FakeHttpResponse(200, {"data": mixed_modality_catalog})

    fake_httpx = SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)

    client = OpenRouterClient(api_key="test-key")
    models = asyncio.run(client._fetch_models_from_api())

    assert len(_FakeAsyncClient.calls) == 1
    assert _FakeAsyncClient.calls[0]["url"].endswith("/models/user")
    assert _FakeAsyncClient.calls[0]["params"] == client_module.MODEL_LIST_PARAMS
    assert _FakeAsyncClient.calls[0]["params"] == {"output_modalities": "all"}
    assert [model.id for model in models] == [entry["id"] for entry in mixed_modality_catalog]
    assert {"speech", "transcription", "video", "embeddings", "rerank"} <= {
        modality for model in models for modality in model.output_modalities
    }


def test_list_models_output_filters_cover_every_live_modality(mixed_modality_models):
    client = OpenRouterClient(api_key="test-key", model_cache_ttl_seconds=300)
    client._fetch_models_from_api = AsyncMock(return_value=list(mixed_modality_models.values()))

    async def run_filters():
        results = {}
        for output_modality in (
            "text",
            "image",
            "audio",
            "speech",
            "video",
            "embeddings",
            "transcription",
            "rerank",
        ):
            models = await client.list_models(output_modality=output_modality, limit=50)
            results[output_modality] = sorted(model.id for model in models)
        return results

    results = asyncio.run(run_filters())

    assert results["text"] == [
        "anthropic/claude-sonnet-4.5",
        "deepseek/deepseek-v4-flash",
        "google/gemini-3.1-flash-image",
        "openai/gpt-audio",
    ]
    assert results["image"] == [
        "bytedance-seed/seedream-5-0-pro",
        "google/gemini-3.1-flash-image",
    ]
    # "audio" also surfaces the dedicated TTS models, which the catalog labels "speech".
    assert results["audio"] == ["google/gemini-3.8-flash-tts", "openai/gpt-audio"]
    assert results["speech"] == ["google/gemini-3.8-flash-tts"]
    assert results["video"] == ["alibaba/happyhorse-1.1"]
    assert results["embeddings"] == ["google/gemini-embedding-2"]
    assert results["transcription"] == ["openai/gpt-transcribe"]
    assert results["rerank"] == ["cohere/rerank-4-pro"]
    client._fetch_models_from_api.assert_awaited_once()


class _AsyncLineIterator:
    def __init__(self, lines):
        self._iter = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as error:
            raise StopAsyncIteration from error


def test_collect_audio_stream_assembles_audio_usage_and_transcript():
    audio_bytes = b"hello audio"
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    encoded_a = encoded[:7]
    encoded_b = encoded[7:]
    result = asyncio.run(
        _collect_audio_stream(
            _AsyncLineIterator(
                [
                    'data: {"id":"chunk-1","model":"openai/gpt-audio-mini","choices":[{"delta":{"audio":{"data":"'
                    + encoded_a
                    + '","transcript":"Hello "}}}]}\n',
                    'data: {"choices":[{"delta":{"content":"world","audio":{"data":"'
                    + encoded_b
                    + '","transcript":"world"}}}],"usage":{"prompt_tokens":12,"completion_tokens":34,"cost":0.0012}}\n',
                    "data: [DONE]\n",
                ]
            )
        )
    )

    assert result["audio_bytes"] == audio_bytes
    assert result["transcript"] == "Hello world"
    assert result["text"] == "world"
    assert result["model"] == "openai/gpt-audio-mini"
    assert result["usage"]["cost"] == 0.0012


def test_request_with_retries_retries_on_5xx_then_succeeds(monkeypatch):
    from discord_openrouter.cogs.openrouter.client import _request_with_retries

    call_count = {"n": 0}

    class _FakeHttpResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.headers: dict[str, str] = {}

    class _FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def request(self, method, url, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return _FakeHttpResponse(503)
            return _FakeHttpResponse(200)

    fake_httpx = SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)
    # Skip real sleeps between retries.
    monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())

    response = asyncio.run(_request_with_retries("GET", "https://example.com/x", timeout=5.0))

    assert response.status_code == 200
    assert call_count["n"] == 3


def test_request_with_retries_raises_after_max_attempts(monkeypatch):
    from discord_openrouter.cogs.openrouter.client import (
        MAX_API_ATTEMPTS,
        OpenRouterApiError,
        _request_with_retries,
    )

    real_connect_error = client_module.httpx.ConnectError

    class _FlakyAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def request(self, method, url, **_kwargs):
            raise real_connect_error("boom")

    fake_httpx = SimpleNamespace(
        AsyncClient=_FlakyAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)
    monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())

    with pytest.raises(OpenRouterApiError) as exc_info:
        asyncio.run(_request_with_retries("GET", "https://example.com/x", timeout=5.0))

    assert f"{MAX_API_ATTEMPTS} attempts" in str(exc_info.value)


def test_create_video_generation_uses_videos_endpoint(monkeypatch):
    class _FakeHttpResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.headers = {}

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        calls: ClassVar[list] = []

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, *, headers=None, json=None, params=None):
            self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
            return _FakeHttpResponse(
                202,
                {
                    "id": "job-123",
                    "polling_url": "https://openrouter.ai/api/v1/videos/job-123",
                    "status": "pending",
                },
            )

    fake_httpx = SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)

    client = OpenRouterClient(
        api_key="test-key",
        site_url="https://example.com",
        app_name="discord-openrouter",
    )
    payload = asyncio.run(
        client.create_video_generation(
            model="google/veo-3.1",
            prompt="A lighthouse in a storm.",
            duration=6,
            resolution="720p",
            aspect_ratio="16:9",
            input_references=[
                {
                    "type": "image_url",
                    "image_url": {"url": "https://cdn.example/reference.png"},
                }
            ],
            generate_audio=False,
            seed=7,
        )
    )

    assert payload["id"] == "job-123"
    assert _FakeAsyncClient.calls[0]["url"].endswith("/videos")
    assert _FakeAsyncClient.calls[0]["json"] == {
        "model": "google/veo-3.1",
        "prompt": "A lighthouse in a storm.",
        "duration": 6,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "input_references": [
            {
                "type": "image_url",
                "image_url": {"url": "https://cdn.example/reference.png"},
            }
        ],
        "generate_audio": False,
        "seed": 7,
    }


def test_get_video_generation_and_download_file_bytes(monkeypatch):
    class _FakeHttpResponse:
        def __init__(self, status_code, payload=None, content=b"", headers=None):
            self.status_code = status_code
            self._payload = payload
            self.content = content
            self.headers = headers or {}

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        calls: ClassVar[list] = []

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, *, headers=None, params=None, json=None):
            self.calls.append({"method": method, "url": url, "headers": headers})
            if url.endswith("/videos/job-123"):
                return _FakeHttpResponse(
                    200,
                    {
                        "id": "job-123",
                        "status": "completed",
                        "unsigned_urls": [
                            "https://openrouter.ai/api/v1/videos/job-123/content?index=0"
                        ],
                    },
                )
            return _FakeHttpResponse(
                200,
                content=b"video-bytes",
                headers={"Content-Type": "video/mp4"},
            )

    fake_httpx = SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)

    client = OpenRouterClient(api_key="test-key")
    status_payload = asyncio.run(client.get_video_generation(job_id="job-123"))
    file_bytes, content_type = asyncio.run(
        client.download_file_bytes("https://openrouter.ai/api/v1/videos/job-123/content?index=0")
    )

    assert status_payload["status"] == "completed"
    assert file_bytes == b"video-bytes"
    assert content_type == "video/mp4"
    assert _FakeAsyncClient.calls[0]["url"].endswith("/videos/job-123")
    assert _FakeAsyncClient.calls[1]["url"].endswith("/videos/job-123/content?index=0")


class _ScriptedHttpResponse:
    def __init__(self, status_code, *, payload=None, content=b"", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _install_scripted_http(monkeypatch, responses):
    """Replace `httpx` in client.py; each request returns the next scripted response."""
    calls: list[dict] = []
    queue = list(responses)

    class _FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, *, headers=None, json=None, params=None):
            calls.append(
                {"method": method, "url": url, "headers": headers, "json": json, "params": params}
            )
            return queue.pop(0)

    fake_httpx = SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)
    monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
    return calls


# Shape of a live `/audio/speech` reply from a Gemini TTS model: raw PCM, no JSON body.
_PCM_SPEECH_HEADERS = {
    "Content-Type": "audio/pcm;rate=24000;channels=1",
    "X-Generation-Id": "gen-tts-1",
}
_GEMINI_MP3_REJECTION = {
    "error": {"message": 'Gemini TTS only supports response_format="pcm". Got "mp3".', "code": 400}
}


def test_create_audio_speech_posts_speech_request(monkeypatch):
    calls = _install_scripted_http(
        monkeypatch,
        [_ScriptedHttpResponse(200, content=b"\x01\x00\x02\x00", headers=_PCM_SPEECH_HEADERS)],
    )
    client = OpenRouterClient(api_key="test-key", app_name="discord-openrouter")

    result = asyncio.run(
        client.create_audio_speech(
            model="google/gemini-3.8-flash-tts",
            input_text="Hello there.",
            voice="Zephyr",
            response_format="pcm",
            instructions="  Speak calmly.  ",
            user="42",
            session_id="tts:999",
        )
    )

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == f"{client_module.OPENROUTER_BASE_URL}/audio/speech"
    assert calls[0]["headers"]["X-OpenRouter-Title"] == "discord-openrouter"
    assert calls[0]["json"] == {
        "model": "google/gemini-3.8-flash-tts",
        "input": "Hello there.",
        "response_format": "pcm",
        "voice": "Zephyr",
        "instructions": "Speak calmly.",
        "user": "42",
        "session_id": "tts:999",
    }
    assert result == {
        "audio_bytes": b"\x01\x00\x02\x00",
        "content_type": "audio/pcm;rate=24000;channels=1",
        "response_format": "pcm",
        "generation_id": "gen-tts-1",
    }


def test_create_audio_speech_omits_blank_voice_and_instructions(monkeypatch):
    calls = _install_scripted_http(
        monkeypatch,
        [_ScriptedHttpResponse(200, content=b"ID3", headers={"Content-Type": "audio/mpeg"})],
    )
    client = OpenRouterClient(api_key="test-key")

    asyncio.run(
        client.create_audio_speech(
            model="hexgrad/kokoro-82m",
            input_text="Hi.",
            voice=None,
            response_format="mp3",
            instructions="   ",
        )
    )

    assert calls[0]["json"] == {
        "model": "hexgrad/kokoro-82m",
        "input": "Hi.",
        "response_format": "mp3",
    }


def test_create_audio_speech_retries_with_pcm_when_mp3_is_rejected(monkeypatch):
    calls = _install_scripted_http(
        monkeypatch,
        [
            _ScriptedHttpResponse(400, payload=_GEMINI_MP3_REJECTION),
            _ScriptedHttpResponse(200, content=b"\x00\x00", headers=_PCM_SPEECH_HEADERS),
            _ScriptedHttpResponse(200, content=b"\x00\x00", headers=_PCM_SPEECH_HEADERS),
        ],
    )
    client = OpenRouterClient(api_key="test-key")
    kwargs = {
        "model": "google/gemini-3.8-flash-tts",
        "input_text": "Hi.",
        "voice": "Zephyr",
        "response_format": "mp3",
    }

    first = asyncio.run(client.create_audio_speech(**kwargs))
    second = asyncio.run(client.create_audio_speech(**kwargs))

    assert [call["json"]["response_format"] for call in calls] == ["mp3", "pcm", "pcm"]
    assert first["response_format"] == "pcm"
    assert second["response_format"] == "pcm"


def test_create_audio_speech_raises_other_400s_without_retry(monkeypatch):
    calls = _install_scripted_http(
        monkeypatch,
        [
            _ScriptedHttpResponse(
                400,
                payload={
                    "error": {"message": "An explicit voice is required for this TTS provider."}
                },
            )
        ],
    )
    client = OpenRouterClient(api_key="test-key")

    with pytest.raises(OpenRouterApiError, match="explicit voice is required"):
        asyncio.run(
            client.create_audio_speech(
                model="google/gemini-3.8-flash-tts",
                input_text="Hi.",
                voice=None,
                response_format="mp3",
            )
        )

    assert len(calls) == 1


def test_create_audio_speech_raises_when_pcm_is_rejected(monkeypatch):
    calls = _install_scripted_http(
        monkeypatch,
        [
            _ScriptedHttpResponse(
                400, payload={"error": {"message": 'Unsupported response_format "pcm".'}}
            )
        ],
    )
    client = OpenRouterClient(api_key="test-key")

    with pytest.raises(OpenRouterApiError, match="Unsupported response_format"):
        asyncio.run(
            client.create_audio_speech(
                model="vendor/mp3-only-tts", input_text="Hi.", voice="a", response_format="pcm"
            )
        )

    assert len(calls) == 1


_GENERATION_RECORD = {
    "data": {
        "id": "gen-tts-1",
        "total_cost": 0.001087,
        "usage": 0.001087,
        "upstream_inference_cost": 0,
        "is_byok": False,
        "tokens_prompt": 13,
        "tokens_completion": 120,
        "api_type": "tts",
    }
}
_GENERATION_NOT_FOUND = {"error": {"message": "Generation gen-tts-1 not found", "code": 404}}


def test_get_generation_polls_until_the_record_exists(monkeypatch):
    calls = _install_scripted_http(
        monkeypatch,
        [
            _ScriptedHttpResponse(404, payload=_GENERATION_NOT_FOUND),
            _ScriptedHttpResponse(404, payload=_GENERATION_NOT_FOUND),
            _ScriptedHttpResponse(200, payload=_GENERATION_RECORD),
        ],
    )
    client = OpenRouterClient(api_key="test-key")

    record = asyncio.run(client.get_generation("gen-tts-1", attempts=4, interval_seconds=1.0))

    assert record == _GENERATION_RECORD["data"]
    assert len(calls) == 3
    assert all(call["method"] == "GET" for call in calls)
    assert calls[0]["url"] == f"{client_module.OPENROUTER_BASE_URL}/generation"
    assert calls[0]["params"] == {"id": "gen-tts-1"}
    # Each lookup waits first: the record never exists right after the request.
    assert [call.args for call in client_module.asyncio.sleep.await_args_list] == [(1.0,)] * 3


def test_get_generation_returns_none_when_record_never_appears(monkeypatch):
    calls = _install_scripted_http(
        monkeypatch,
        [_ScriptedHttpResponse(404, payload=_GENERATION_NOT_FOUND) for _ in range(4)],
    )
    client = OpenRouterClient(api_key="test-key")

    assert asyncio.run(client.get_generation("gen-tts-1", attempts=4)) is None
    assert len(calls) == 4


def test_get_generation_returns_none_on_other_errors(monkeypatch):
    calls = _install_scripted_http(
        monkeypatch, [_ScriptedHttpResponse(401, payload={"error": {"message": "No auth"}})]
    )
    client = OpenRouterClient(api_key="test-key")

    assert asyncio.run(client.get_generation("gen-tts-1", attempts=4)) is None
    assert len(calls) == 1


def test_create_speech_keeps_the_streamed_chat_audio_payload(monkeypatch):
    # The `audio` output models (gpt-audio, lyria) stay on streamed chat completions.
    client = OpenRouterClient(api_key="test-key")
    stream = AsyncMock(return_value={"audio_bytes": b"x"})
    monkeypatch.setattr(client, "_stream_audio_completion", stream)

    asyncio.run(
        client.create_speech(
            model="openai/gpt-audio",
            input_text="Hello.",
            voice="alloy",
            response_format="flac",
            modalities=["text", "audio"],
            instructions="Whisper.",
            user="42",
            session_id="tts:999",
        )
    )

    assert stream.await_args.args[0] == {
        "model": "openai/gpt-audio",
        "messages": [{"role": "user", "content": "Whisper.\n\nText to speak:\nHello."}],
        "modalities": ["text", "audio"],
        "audio": {"format": "flac", "voice": "alloy"},
        "stream": True,
        "user": "42",
        "session_id": "tts:999",
    }


def test_error_message_includes_the_provider_message_from_metadata_raw(monkeypatch):
    raw = json.dumps(
        {
            "error": {
                "message": "Missing required parameter: 'audio.voice'.",
                "type": "invalid_request_error",
            }
        }
    )
    _install_scripted_http(
        monkeypatch,
        [
            _ScriptedHttpResponse(
                400,
                payload={
                    "error": {
                        "message": "Provider returned error",
                        "code": 400,
                        "metadata": {"raw": raw, "provider_name": "OpenAI"},
                    }
                },
            )
        ],
    )
    client = OpenRouterClient(api_key="test-key")

    with pytest.raises(OpenRouterApiError) as exc_info:
        asyncio.run(client.create_chat_completion(model="openai/gpt-audio", messages=[], user="1"))

    assert str(exc_info.value) == (
        "Provider returned error: Missing required parameter: 'audio.voice'."
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            {"message": "Provider returned error", "metadata": {"raw": "upstream timed out"}},
            "Provider returned error: upstream timed out",
        ),
        ({"message": "Provider returned error", "metadata": {}}, "Provider returned error"),
        ({"message": "Rate limited", "metadata": {"raw": None}}, "Rate limited"),
    ],
)
def test_streamed_error_message_reads_metadata_raw(error, expected):
    from discord_openrouter.cogs.openrouter.client import _extract_error_message_from_bytes

    body = json.dumps({"error": error}).encode()

    assert _extract_error_message_from_bytes(400, body) == expected


def test_create_speech_read_aloud_sends_a_system_message_with_instructions(monkeypatch):
    client = OpenRouterClient(api_key="test-key")
    stream = AsyncMock(return_value={"audio_bytes": b"x"})
    monkeypatch.setattr(client, "_stream_audio_completion", stream)

    asyncio.run(
        client.create_speech(
            model="openai/gpt-audio-mini",
            input_text="Hello there.",
            voice="alloy",
            response_format="pcm16",
            modalities=["text", "audio"],
            instructions="  Whisper.  ",
            read_aloud=True,
        )
    )

    assert stream.await_args.args[0]["messages"] == [
        {
            "role": "system",
            "content": "Read the user's text aloud exactly as written. Do not answer it, add to it"
            " or comment on it.\nDelivery instructions: Whisper.",
        },
        {
            "role": "user",
            "content": 'Read this text aloud exactly as written:\n"""\nHello there.\n"""',
        },
    ]


def test_create_speech_read_aloud_without_instructions(monkeypatch):
    client = OpenRouterClient(api_key="test-key")
    stream = AsyncMock(return_value={"audio_bytes": b"x"})
    monkeypatch.setattr(client, "_stream_audio_completion", stream)

    asyncio.run(
        client.create_speech(
            model="openai/gpt-audio-mini",
            input_text="Hello there.",
            voice="alloy",
            response_format="pcm16",
            read_aloud=True,
        )
    )

    assert stream.await_args.args[0]["messages"] == [
        {"role": "system", "content": client_module.READ_ALOUD_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": 'Read this text aloud exactly as written:\n"""\nHello there.\n"""',
        },
    ]


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_get_generation_does_not_retry_rate_limits_or_server_errors(monkeypatch, status_code):
    calls = _install_scripted_http(
        monkeypatch,
        [
            _ScriptedHttpResponse(
                status_code, payload={"error": {"message": "busy"}}, headers={"Retry-After": "60"}
            )
        ],
    )
    client = OpenRouterClient(api_key="test-key")

    assert asyncio.run(client.get_generation("gen-tts-1", attempts=4)) is None
    assert len(calls) == 1


def test_get_generation_stops_at_the_overall_timeout(monkeypatch):
    class _HangingAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **_kwargs):
            await asyncio.Event().wait()

    fake_httpx = SimpleNamespace(
        AsyncClient=_HangingAsyncClient,
        Timeout=lambda **_kwargs: None,
        RequestError=client_module.httpx.RequestError,
    )
    monkeypatch.setattr(client_module, "httpx", fake_httpx)
    client = OpenRouterClient(api_key="test-key")

    async def run():
        loop = asyncio.get_running_loop()
        started = loop.time()
        record = await client.get_generation(
            "gen-tts-1", interval_seconds=0.0, overall_timeout_seconds=0.05
        )
        return record, loop.time() - started

    record, elapsed = asyncio.run(run())

    assert record is None
    assert elapsed < 1.0
    assert client_module.GENERATION_LOOKUP_TIMEOUT_SECONDS == 6.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {"error": {"message": "Quota exceeded for this project."}},
            "Provider returned error: Quota exceeded for this project.",
        ),
        (
            [{"error": {"code": 400, "message": "Invalid voice name."}}],
            "Provider returned error: Invalid voice name.",
        ),
        (
            json.dumps([{"error": {"code": 400, "message": "Invalid voice name."}}]),
            "Provider returned error: Invalid voice name.",
        ),
        ({"message": "Model is overloaded."}, "Provider returned error: Model is overloaded."),
        ([], "Provider returned error"),
    ],
)
def test_error_message_reads_dict_and_list_metadata_raw(raw, expected):
    from discord_openrouter.cogs.openrouter.client import _extract_error_message_from_bytes

    body = json.dumps({"error": {"message": "Provider returned error", "metadata": {"raw": raw}}})

    assert _extract_error_message_from_bytes(400, body.encode()) == expected


def test_collect_audio_stream_decodes_chunks_that_each_carry_padding():
    # Each chunk encoded on its own, so padding appears in the middle of the joined text.
    pieces = [b"ab", b"cde", b"f", b"\x00\x01\x02\x03"]
    lines = [
        'data: {"choices":[{"delta":{"audio":{"data":"'
        + base64.b64encode(piece).decode("ascii")
        + '"}}}]}\n'
        for piece in pieces
    ] + ["data: [DONE]\n"]

    result = asyncio.run(_collect_audio_stream(_AsyncLineIterator(lines)))

    assert result["audio_bytes"] == b"".join(pieces)


@pytest.mark.parametrize("cut", [1, 2, 3, 5, 6, 7])
def test_decode_base64_chunks_handles_splits_inside_a_group(cut):
    from discord_openrouter.cogs.openrouter.client import _decode_base64_chunks

    raw = bytes(range(20))
    encoded = base64.b64encode(raw).decode("ascii")

    assert _decode_base64_chunks([encoded[:cut], encoded[cut:]]) == raw
    assert (
        _decode_base64_chunks([base64.b64encode(b"ab").decode(), encoded[:cut], encoded[cut:]])
        == b"ab" + raw
    )
