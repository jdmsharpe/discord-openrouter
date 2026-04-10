import importlib
import sys
from contextlib import suppress

import pytest

MODULE_NAME = "discord_openrouter.config.auth"


def _import_fresh_auth_module(monkeypatch=None):
    sys.modules.pop(MODULE_NAME, None)
    if monkeypatch is not None:
        with suppress(ModuleNotFoundError):
            monkeypatch.setattr("dotenv.load_dotenv", lambda *_, **__: None)
    return importlib.import_module(MODULE_NAME)


def test_validate_required_config_reports_missing_vars(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    auth = _import_fresh_auth_module(monkeypatch)

    with pytest.raises(RuntimeError, match="BOT_TOKEN, OPENROUTER_API_KEY"):
        auth.validate_required_config()


def test_validate_required_config_rejects_whitespace_only_values(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "   ")
    monkeypatch.setenv("OPENROUTER_API_KEY", "\t")

    auth = _import_fresh_auth_module(monkeypatch)

    with pytest.raises(RuntimeError, match="BOT_TOKEN, OPENROUTER_API_KEY"):
        auth.validate_required_config()


def test_validate_required_config_allows_present_vars(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "discord-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")

    auth = _import_fresh_auth_module(monkeypatch)

    auth.validate_required_config()


def test_invalid_guild_ids_raise_clear_error(monkeypatch):
    monkeypatch.setenv("GUILD_IDS", "123, nope, 456")

    with pytest.raises(RuntimeError, match="invalid token: 'nope'"):
        _import_fresh_auth_module(monkeypatch)


def test_default_model_and_cache_ttl_are_applied(monkeypatch):
    monkeypatch.delenv("OPENROUTER_DEFAULT_TEXT_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_VIDEO_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_TTS_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_STT_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_PDF_ENGINE", raising=False)
    monkeypatch.delenv("OPENROUTER_APP_CATEGORIES", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_VIDEO_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_TTS_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL_CACHE_TTL_SECONDS", raising=False)

    auth = _import_fresh_auth_module(monkeypatch)

    assert auth.OPENROUTER_DEFAULT_TEXT_MODEL == auth.DEFAULT_TEXT_MODEL
    assert auth.OPENROUTER_DEFAULT_IMAGE_MODEL == auth.DEFAULT_IMAGE_MODEL
    assert auth.OPENROUTER_DEFAULT_VIDEO_MODEL == auth.DEFAULT_VIDEO_MODEL
    assert auth.OPENROUTER_DEFAULT_TTS_MODEL == auth.DEFAULT_TTS_MODEL
    assert auth.OPENROUTER_DEFAULT_STT_MODEL == auth.DEFAULT_STT_MODEL
    assert auth.OPENROUTER_DEFAULT_PDF_ENGINE == auth.DEFAULT_PDF_ENGINE
    assert auth.OPENROUTER_APP_CATEGORIES is None
    assert auth.OPENROUTER_DEFAULT_MODEL == auth.DEFAULT_TEXT_MODEL
    assert auth.OPENROUTER_IMAGE_MODEL == auth.DEFAULT_IMAGE_MODEL
    assert auth.OPENROUTER_VIDEO_MODEL == auth.DEFAULT_VIDEO_MODEL
    assert auth.OPENROUTER_TTS_MODEL == auth.DEFAULT_TTS_MODEL
    assert auth.OPENROUTER_MODEL_CACHE_TTL_SECONDS == auth.DEFAULT_MODEL_CACHE_TTL_SECONDS


def test_legacy_model_env_vars_fallback_to_new_names(monkeypatch):
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "legacy/text-model")
    monkeypatch.setenv("OPENROUTER_IMAGE_MODEL", "legacy/image-model")
    monkeypatch.setenv("OPENROUTER_VIDEO_MODEL", "legacy/video-model")
    monkeypatch.setenv("OPENROUTER_TTS_MODEL", "legacy/tts-model")
    monkeypatch.delenv("OPENROUTER_DEFAULT_TEXT_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_VIDEO_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_TTS_MODEL", raising=False)

    auth = _import_fresh_auth_module(monkeypatch)

    assert auth.OPENROUTER_DEFAULT_TEXT_MODEL == "legacy/text-model"
    assert auth.OPENROUTER_DEFAULT_IMAGE_MODEL == "legacy/image-model"
    assert auth.OPENROUTER_DEFAULT_VIDEO_MODEL == "legacy/video-model"
    assert auth.OPENROUTER_DEFAULT_TTS_MODEL == "legacy/tts-model"


def test_pdf_engine_env_normalizes_deprecated_alias(monkeypatch):
    monkeypatch.setenv("OPENROUTER_DEFAULT_PDF_ENGINE", "pdf-text")

    auth = _import_fresh_auth_module(monkeypatch)

    assert auth.OPENROUTER_DEFAULT_PDF_ENGINE == "cloudflare-ai"


def test_invalid_pdf_engine_env_raises_clear_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_DEFAULT_PDF_ENGINE", "unsupported-engine")

    with pytest.raises(RuntimeError, match="Invalid OPENROUTER_DEFAULT_PDF_ENGINE value"):
        _import_fresh_auth_module(monkeypatch)


def test_app_categories_env_is_normalized_as_csv(monkeypatch):
    monkeypatch.setenv("OPENROUTER_APP_CATEGORIES", " productivity, discord bots , ai ")

    auth = _import_fresh_auth_module(monkeypatch)

    assert auth.OPENROUTER_APP_CATEGORIES == "productivity,discord bots,ai"
