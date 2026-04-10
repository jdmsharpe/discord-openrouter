import asyncio
import base64
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord_openrouter.cogs.openrouter.client import OpenRouterClient, _collect_audio_stream
from discord_openrouter.util import ModelInfo, ModelPricing


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **_kwargs):
        return self.payload


class _FakeChat:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def send_async(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.payload)


class _FakeOpenRouter:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = _FakeChat(
            {
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            }
        )
        _FakeOpenRouter.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_create_chat_completion_uses_sdk_and_reasoning(monkeypatch):
    client = OpenRouterClient(
        api_key="test-key",
        site_url="https://example.com",
        app_name="discord-openrouter",
    )
    monkeypatch.setattr(
        client,
        "_import_openrouter_sdk",
        lambda: SimpleNamespace(OpenRouter=_FakeOpenRouter),
    )

    payload = asyncio.run(
        client.create_chat_completion(
            model="minimax/minimax-m2.7",
            messages=[{"role": "user", "content": "hello"}],
            modalities=["image", "text"],
            image_config={"aspect_ratio": "16:9", "image_size": "2K"},
            plugins=[{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}],
            cache_control={"type": "ephemeral", "ttl": "1h"},
            temperature=0.5,
            top_p=0.9,
            max_tokens=256,
            reasoning_effort="high",
            user="123",
            session_id="abc",
        )
    )

    instance = _FakeOpenRouter.instances[-1]
    assert instance.kwargs["api_key"] == "test-key"
    assert instance.kwargs["http_referer"] == "https://example.com"
    assert instance.kwargs["x_open_router_title"] == "discord-openrouter"
    assert instance.chat.calls[0]["model"] == "minimax/minimax-m2.7"
    assert instance.chat.calls[0]["modalities"] == ["image", "text"]
    assert instance.chat.calls[0]["image_config"] == {"aspect_ratio": "16:9", "image_size": "2K"}
    assert instance.chat.calls[0]["plugins"] == [{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}]
    assert instance.chat.calls[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert instance.chat.calls[0]["reasoning"] == {"effort": "high"}
    assert payload["usage"]["total_tokens"] == 30


def test_create_chat_completion_supports_reasoning_budget_and_exclusion(monkeypatch):
    client = OpenRouterClient(api_key="test-key")
    monkeypatch.setattr(
        client,
        "_import_openrouter_sdk",
        lambda: SimpleNamespace(OpenRouter=_FakeOpenRouter),
    )

    asyncio.run(
        client.create_chat_completion(
            model="anthropic/claude-sonnet-4.5",
            messages=[{"role": "user", "content": "hello"}],
            reasoning_max_tokens=2048,
            exclude_reasoning=True,
        )
    )

    instance = _FakeOpenRouter.instances[-1]
    assert instance.chat.calls[0]["reasoning"] == {
        "max_tokens": 2048,
        "exclude": True,
    }


def test_create_chat_completion_can_request_hidden_reasoning_only(monkeypatch):
    client = OpenRouterClient(api_key="test-key")
    monkeypatch.setattr(
        client,
        "_import_openrouter_sdk",
        lambda: SimpleNamespace(OpenRouter=_FakeOpenRouter),
    )

    asyncio.run(
        client.create_chat_completion(
            model="openai/gpt-5.2",
            messages=[{"role": "user", "content": "hello"}],
            exclude_reasoning=True,
        )
    )

    instance = _FakeOpenRouter.instances[-1]
    assert instance.chat.calls[0]["reasoning"] == {
        "exclude": True,
        "enabled": True,
    }


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
                id="minimax/minimax-m2.7",
                name="MiniMax M2.7",
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

    first = asyncio.run(client.list_models(query="minimax", limit=5))
    second = asyncio.run(client.list_models(query="mini", limit=5))
    third = asyncio.run(client.list_models(output_modality="audio", limit=5))
    fourth = asyncio.run(client.list_models(input_modality="image", limit=5))

    assert [model.id for model in first] == ["minimax/minimax-m2.7"]
    assert [model.id for model in second][:2] == [
        "minimax/minimax-m2.7",
        "openai/gpt-audio-mini",
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
            ModelInfo(id="minimax/minimax-m2.7", name="MiniMax M2.7"),
        ]
    )

    model = asyncio.run(client.get_model("minimax/minimax-m2.7"))

    assert model is not None
    assert model.id == "minimax/minimax-m2.7"


def test_fetch_models_fallback_requests_all_modalities(monkeypatch):
    class _FakeHttpResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        calls = []

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *, headers=None, params=None):
            self.calls.append({"url": url, "headers": headers, "params": params})
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

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_FakeAsyncClient))

    client = OpenRouterClient(api_key="test-key")
    models = asyncio.run(client._fetch_models_from_api())

    assert [model.id for model in models] == ["openai/gpt-image-1"]
    assert _FakeAsyncClient.calls[0]["url"].endswith("/models/user")
    assert _FakeAsyncClient.calls[0]["params"] is None
    assert _FakeAsyncClient.calls[1]["url"].endswith("/models")
    assert _FakeAsyncClient.calls[1]["params"] == {"output_modalities": "all"}


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
