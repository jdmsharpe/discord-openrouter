import os
from typing import Any

from ..util import normalize_pdf_engine

try:
    from dotenv import load_dotenv as _load_dotenv
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal test environments

    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

else:
    load_dotenv = _load_dotenv


load_dotenv()

TRUE_ENV_VALUES = frozenset({"true", "1", "yes"})
REQUIRED_ENV_VARS = ("BOT_TOKEN", "OPENROUTER_API_KEY")
DEFAULT_TEXT_MODEL = "moonshotai/kimi-k2.6"
DEFAULT_IMAGE_MODEL = "openai/gpt-5.4-image-2"
DEFAULT_VIDEO_MODEL = "kwaivgi/kling-video-o1"
DEFAULT_TTS_MODEL = "google/gemini-3.1-flash-tts-preview"
DEFAULT_STT_MODEL = "openai/gpt-audio"
DEFAULT_PDF_ENGINE = None
DEFAULT_MODEL = DEFAULT_TEXT_MODEL
DEFAULT_APP_NAME = "discord-openrouter"
DEFAULT_MODEL_CACHE_TTL_SECONDS = 300


def _get_env_or_none(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped_value = value.strip()
    return stripped_value or None


def _parse_guild_ids(raw_guild_ids: str) -> list[int]:
    guild_ids: list[int] = []
    for token in raw_guild_ids.split(","):
        stripped_token = token.strip()
        if not stripped_token:
            continue
        try:
            guild_ids.append(int(stripped_token))
        except ValueError as exc:
            raise RuntimeError(
                "Invalid GUILD_IDS value. Expected a comma-separated list of integers, "
                f"but received invalid token: {stripped_token!r}."
            ) from exc
    return guild_ids


def _parse_bool_env(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE_ENV_VALUES


def _parse_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    stripped_value = raw_value.strip()
    if not stripped_value:
        return default
    try:
        return int(stripped_value)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid {name} value. Expected an integer, got {raw_value!r}."
        ) from exc


def _parse_pdf_engine_env(name: str) -> str | None:
    raw_value = _get_env_or_none(name)
    try:
        return normalize_pdf_engine(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {name} value. {exc}") from exc


def _parse_csv_header_env(name: str) -> str | None:
    raw_value = _get_env_or_none(name)
    if raw_value is None:
        return None
    values = [token.strip() for token in raw_value.split(",") if token.strip()]
    return ",".join(values) or None


def validate_required_config() -> None:
    missing_vars = [name for name in REQUIRED_ENV_VARS if _get_env_or_none(name) is None]
    if missing_vars:
        missing_list = ", ".join(missing_vars)
        raise RuntimeError(
            "Missing required environment configuration: "
            f"{missing_list}. Please set these variables before starting the bot."
        )


BOT_TOKEN = _get_env_or_none("BOT_TOKEN")
GUILD_IDS = _parse_guild_ids(os.getenv("GUILD_IDS", ""))
OPENROUTER_API_KEY = _get_env_or_none("OPENROUTER_API_KEY")
OPENROUTER_DEFAULT_TEXT_MODEL = (
    _get_env_or_none("OPENROUTER_DEFAULT_TEXT_MODEL")
    or _get_env_or_none("OPENROUTER_DEFAULT_MODEL")
    or DEFAULT_TEXT_MODEL
)
OPENROUTER_DEFAULT_IMAGE_MODEL = (
    _get_env_or_none("OPENROUTER_DEFAULT_IMAGE_MODEL")
    or _get_env_or_none("OPENROUTER_IMAGE_MODEL")
    or DEFAULT_IMAGE_MODEL
)
OPENROUTER_DEFAULT_VIDEO_MODEL = (
    _get_env_or_none("OPENROUTER_DEFAULT_VIDEO_MODEL")
    or _get_env_or_none("OPENROUTER_VIDEO_MODEL")
    or DEFAULT_VIDEO_MODEL
)
OPENROUTER_DEFAULT_TTS_MODEL = (
    _get_env_or_none("OPENROUTER_DEFAULT_TTS_MODEL")
    or _get_env_or_none("OPENROUTER_TTS_MODEL")
    or DEFAULT_TTS_MODEL
)
OPENROUTER_DEFAULT_STT_MODEL = _get_env_or_none("OPENROUTER_DEFAULT_STT_MODEL") or DEFAULT_STT_MODEL
OPENROUTER_DEFAULT_PDF_ENGINE = _parse_pdf_engine_env("OPENROUTER_DEFAULT_PDF_ENGINE")
OPENROUTER_DEFAULT_MODEL = OPENROUTER_DEFAULT_TEXT_MODEL
OPENROUTER_IMAGE_MODEL = OPENROUTER_DEFAULT_IMAGE_MODEL
OPENROUTER_VIDEO_MODEL = OPENROUTER_DEFAULT_VIDEO_MODEL
OPENROUTER_TTS_MODEL = OPENROUTER_DEFAULT_TTS_MODEL
OPENROUTER_SITE_URL = _get_env_or_none("OPENROUTER_SITE_URL")
OPENROUTER_APP_NAME = _get_env_or_none("OPENROUTER_APP_NAME") or DEFAULT_APP_NAME
OPENROUTER_APP_CATEGORIES = _parse_csv_header_env("OPENROUTER_APP_CATEGORIES")
OPENROUTER_MODEL_CACHE_TTL_SECONDS = _parse_int_env(
    "OPENROUTER_MODEL_CACHE_TTL_SECONDS",
    DEFAULT_MODEL_CACHE_TTL_SECONDS,
)
SHOW_COST_EMBEDS = _parse_bool_env("SHOW_COST_EMBEDS")
