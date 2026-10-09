"""The keyed `user` identifier and the request bodies that carry it."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter import chat, safety, speech
from discord_openrouter.cogs.openrouter import client as client_module
from discord_openrouter.cogs.openrouter.client import OpenRouterApiError, OpenRouterClient
from discord_openrouter.cogs.openrouter.image import run_image_command
from discord_openrouter.cogs.openrouter.safety import (
    SAFETY_IDENTIFIER_KEY_LABEL,
    build_safety_identifier,
    derive_safety_identifier_key,
)
from discord_openrouter.cogs.openrouter.speech import run_stt_command, run_tts_command
from discord_openrouter.config import DEFAULT_TTS_MODEL, auth
from discord_openrouter.util import ChatSettings, Conversation
from tests.conftest import TEST_SAFETY_IDENTIFIER_KEY

USER_ID = 987654321098765432
OTHER_USER_ID = 123456789012345678
EXPECTED_IDENTIFIER = hmac.new(
    TEST_SAFETY_IDENTIFIER_KEY, str(USER_ID).encode(), hashlib.sha256
).hexdigest()


def _hmac_hex(key: bytes, user_id: int) -> str:
    return hmac.new(key, str(user_id).encode(), hashlib.sha256).hexdigest()


def _exec_fresh_module(name: str, path: str | None) -> ModuleType:
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_safety_with_env(monkeypatch, env: dict[str, str | None]) -> ModuleType:
    """Run auth.py and safety.py as fresh modules under ``env``.

    The fresh auth module replaces ``discord_openrouter.config.auth`` in ``sys.modules``
    for the test only, so the import in safety.py reads it; ``load_dotenv`` is stubbed so
    a local .env cannot fill in a variable the test leaves unset.
    """
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    dotenv = ModuleType("dotenv")
    dotenv.load_dotenv = lambda *_args, **_kwargs: False  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)

    fresh_auth = _exec_fresh_module("discord_openrouter.config.auth", auth.__file__)
    monkeypatch.setitem(sys.modules, "discord_openrouter.config.auth", fresh_auth)
    return _exec_fresh_module("discord_openrouter.cogs.openrouter.safety", safety.__file__)


class TestKeyDerivation:
    def test_secret_is_the_key_when_set(self):
        assert derive_safety_identifier_key("abc", "bot-token") == b"abc"

    def test_bot_token_fallback_is_an_hmac_with_the_shared_label(self):
        key = derive_safety_identifier_key(None, "bot-token")
        assert SAFETY_IDENTIFIER_KEY_LABEL == b"safety-identifier-v1"
        assert key == hmac.new(b"bot-token", b"safety-identifier-v1", hashlib.sha256).digest()
        assert key != b"bot-token"

    def test_no_key_without_secret_or_bot_token(self):
        assert derive_safety_identifier_key(None, None) is None
        assert derive_safety_identifier_key("", "") is None

    @pytest.mark.parametrize(
        ("secret", "bot_token", "expected_key"),
        [
            pytest.param("env-secret", "env-bot-token", b"env-secret", id="secret-set"),
            pytest.param(
                None,
                "env-bot-token",
                hmac.new(b"env-bot-token", SAFETY_IDENTIFIER_KEY_LABEL, hashlib.sha256).digest(),
                id="bot-token-fallback",
            ),
            pytest.param(
                "   ",
                "env-bot-token",
                hmac.new(b"env-bot-token", SAFETY_IDENTIFIER_KEY_LABEL, hashlib.sha256).digest(),
                id="blank-secret-uses-bot-token",
            ),
            pytest.param(None, None, None, id="neither-set"),
        ],
    )
    def test_module_key_is_read_from_the_environment(
        self, monkeypatch, secret, bot_token, expected_key
    ):
        fresh_safety = _load_safety_with_env(
            monkeypatch,
            {
                "SAFETY_IDENTIFIER_SECRET": secret,
                "BOT_TOKEN": bot_token,
                "OPENROUTER_API_KEY": "env-api-key",
            },
        )

        module_key = fresh_safety.SAFETY_IDENTIFIER_KEY
        assert module_key == expected_key
        identifier = fresh_safety.build_safety_identifier(USER_ID)
        if expected_key is None:
            assert identifier is None
        else:
            assert identifier == _hmac_hex(expected_key, USER_ID)

    def test_openrouter_api_key_is_never_an_input(self):
        """OpenRouter holds OPENROUTER_API_KEY, so a key derived from it would let
        OpenRouter rebuild the mapping by hashing known Discord user IDs."""
        assert "OPENROUTER_API_KEY" not in vars(safety)


class TestBuildSafetyIdentifier:
    def test_is_a_64_char_lowercase_hex_hmac_of_the_user_id(self):
        value = build_safety_identifier(USER_ID)
        assert value == EXPECTED_IDENTIFIER
        assert value is not None
        assert len(value) == 64
        assert value == value.lower()
        int(value, 16)

    def test_is_deterministic_per_user_and_key(self):
        assert build_safety_identifier(USER_ID) == build_safety_identifier(USER_ID)

    def test_differs_across_users(self):
        assert build_safety_identifier(USER_ID) != build_safety_identifier(OTHER_USER_ID)

    def test_differs_across_keys(self, monkeypatch):
        first = build_safety_identifier(USER_ID)
        monkeypatch.setattr(safety, "SAFETY_IDENTIFIER_KEY", b"another-secret")
        assert build_safety_identifier(USER_ID) != first

    def test_is_not_the_raw_id_or_an_unkeyed_hash(self):
        value = build_safety_identifier(USER_ID)
        assert value not in {
            USER_ID,
            str(USER_ID),
            format(USER_ID, "x"),
            hex(USER_ID),
            hashlib.sha256(str(USER_ID).encode()).hexdigest(),
        }

    def test_returns_none_without_a_key(self, monkeypatch):
        monkeypatch.setattr(safety, "SAFETY_IDENTIFIER_KEY", None)
        assert build_safety_identifier(USER_ID) is None


# Request bodies: the real OpenRouterClient builds each payload, and the HTTP layer is
# replaced by a recorder that answers 500 so every command stops after its request.


class _RecordedBodies:
    def __init__(self) -> None:
        self.bodies: list[tuple[str, dict]] = []

    async def request_with_retries(self, method, url, *, json_payload=None, **_kwargs):
        self.bodies.append((url, json_payload))
        return SimpleNamespace(
            status_code=500,
            json=lambda: {"error": {"message": "stopped by test"}},
            headers={},
        )

    async def stream_audio_completion(self, payload):
        self.bodies.append(("stream:/chat/completions", payload))
        raise OpenRouterApiError("stopped by test")


@pytest.fixture
def recorder(monkeypatch):
    recorded = _RecordedBodies()
    monkeypatch.setattr(client_module, "_request_with_retries", recorded.request_with_retries)
    monkeypatch.setattr(
        OpenRouterClient, "_stream_audio_completion", recorded.stream_audio_completion
    )
    return recorded


def _client(model_info=None) -> OpenRouterClient:
    client = OpenRouterClient(api_key="test-key")
    client.get_model = AsyncMock(return_value=model_info)  # type: ignore[method-assign]
    return client


def _cog(client: OpenRouterClient) -> SimpleNamespace:
    return SimpleNamespace(
        logger=MagicMock(),
        channel_model_defaults={},
        daily_costs={},
        openrouter_client=client,
        _strip_previous_view=AsyncMock(),
        _create_button_view=MagicMock(return_value=object()),
        views={},
        last_view_messages={},
    )


def _ctx() -> SimpleNamespace:
    user = SimpleNamespace(id=USER_ID)
    return SimpleNamespace(
        channel=SimpleNamespace(id=900),
        user=user,
        author=user,
        defer=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
        interaction=SimpleNamespace(id=555),
    )


def _assert_carries_identifier_only(recorder: _RecordedBodies, expected_url_suffix: str):
    assert len(recorder.bodies) == 1
    url, body = recorder.bodies[0]
    assert url.endswith(expected_url_suffix)
    assert body["user"] == EXPECTED_IDENTIFIER
    serialized = json.dumps(body)
    assert str(USER_ID) not in serialized
    assert format(USER_ID, "x") not in serialized
    assert hashlib.sha256(str(USER_ID).encode()).hexdigest() not in serialized


async def _run_chat_turn(cog) -> None:
    conversation = Conversation(
        conversation_id=555,
        conversation_starter_id=USER_ID,
        channel_id=900,
        settings=ChatSettings(model="openai/test"),
    )
    conversation.append_user_message({"role": "user", "content": "hello"})
    with patch.object(chat, "keep_typing", new=AsyncMock()):
        await chat._run_conversation_turn(
            cog,
            conversation=conversation,
            send_reply=AsyncMock(),
            user_id=USER_ID,
            channel=SimpleNamespace(),
        )


def _run_image(cog) -> None:
    with patch("discord_openrouter.cogs.openrouter.image.send_embed_batches", new=AsyncMock()):
        asyncio.run(run_image_command(cog, ctx=_ctx(), prompt="A lighthouse."))


def _run_tts(cog, model: str) -> None:
    with patch.object(speech, "send_embed_batches", new=AsyncMock()):
        asyncio.run(run_tts_command(cog, ctx=_ctx(), input_text="Hello.", model=model))


def _run_stt(cog) -> None:
    attachment = SimpleNamespace(
        filename="speech.mp3",
        content_type="audio/mpeg",
        size=1024,
        url="https://example.test/speech.mp3",
    )
    part = {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "mp3"}}
    with (
        patch.object(speech, "send_embed_batches", new=AsyncMock()),
        patch.object(speech, "_build_stt_attachment_part", new=AsyncMock(return_value=part)),
    ):
        asyncio.run(run_stt_command(cog, ctx=_ctx(), attachment=attachment))


def test_chat_request_body_carries_identifier_not_user_id(recorder):
    asyncio.run(_run_chat_turn(_cog(_client())))
    _assert_carries_identifier_only(recorder, "/chat/completions")


def test_image_request_body_carries_identifier_not_user_id(recorder):
    _run_image(_cog(_client()))
    _assert_carries_identifier_only(recorder, "/chat/completions")


def test_audio_speech_request_body_carries_identifier_not_user_id(recorder, mixed_modality_models):
    cog = _cog(_client(mixed_modality_models[DEFAULT_TTS_MODEL]))
    _run_tts(cog, DEFAULT_TTS_MODEL)
    _assert_carries_identifier_only(recorder, "/audio/speech")


def test_chat_audio_request_body_carries_identifier_not_user_id(recorder, mixed_modality_models):
    cog = _cog(_client(mixed_modality_models["openai/gpt-audio"]))
    _run_tts(cog, "openai/gpt-audio")
    _assert_carries_identifier_only(recorder, "/chat/completions")


def test_stt_request_body_carries_identifier_not_user_id(recorder):
    _run_stt(_cog(_client()))
    _assert_carries_identifier_only(recorder, "/chat/completions")


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(lambda cog, _models: asyncio.run(_run_chat_turn(cog)), id="chat"),
        pytest.param(lambda cog, _models: _run_image(cog), id="image"),
        pytest.param(lambda cog, _models: _run_stt(cog), id="stt"),
        pytest.param(
            lambda cog, models: (
                setattr(cog.openrouter_client, "get_model", AsyncMock(return_value=models[0])),
                _run_tts(cog, DEFAULT_TTS_MODEL),
            ),
            id="audio-speech",
        ),
        pytest.param(
            lambda cog, models: (
                setattr(cog.openrouter_client, "get_model", AsyncMock(return_value=models[1])),
                _run_tts(cog, "openai/gpt-audio"),
            ),
            id="chat-audio",
        ),
    ],
)
def test_request_bodies_omit_user_without_a_key(monkeypatch, recorder, mixed_modality_models, run):
    monkeypatch.setattr(safety, "SAFETY_IDENTIFIER_KEY", None)
    models = (mixed_modality_models[DEFAULT_TTS_MODEL], mixed_modality_models["openai/gpt-audio"])

    run(_cog(_client()), models)

    assert len(recorder.bodies) == 1
    _url, body = recorder.bodies[0]
    assert "user" not in body
    assert str(USER_ID) not in json.dumps(body)
