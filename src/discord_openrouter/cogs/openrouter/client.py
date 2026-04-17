from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib
import json
import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    pass

from ...config import (
    OPENROUTER_API_KEY,
    OPENROUTER_APP_CATEGORIES,
    OPENROUTER_APP_NAME,
    OPENROUTER_MODEL_CACHE_TTL_SECONDS,
    OPENROUTER_SITE_URL,
)
from ...util import ModelInfo, parse_model_info

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_API_ATTEMPTS = 5
INITIAL_RETRY_DELAY_SECONDS = 0.5
RETRY_JITTER_RATIO = 0.25

logger = logging.getLogger(__name__)


def parse_retry_after(retry_after: str | None) -> float | None:
    if not retry_after:
        return None
    retry_after = retry_after.strip()
    with contextlib.suppress(ValueError):
        return max(0.0, float(retry_after))
    with contextlib.suppress(TypeError, ValueError, OverflowError):
        retry_at = parsedate_to_datetime(retry_after)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    return None


def compute_retry_delay(attempt: int, *, retry_after: str | None = None) -> float:
    parsed_retry_after = parse_retry_after(retry_after)
    if parsed_retry_after is not None:
        return parsed_retry_after
    base_delay = INITIAL_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
    return base_delay + random.uniform(0.0, base_delay * RETRY_JITTER_RATIO)


async def _request_with_retries(
    method: str,
    url: str,
    *,
    timeout: httpx.Timeout | float,
    headers: dict[str, str] | None = None,
    json_payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """Perform an httpx request with exponential backoff on 429/5xx and transport errors."""
    request_kwargs: dict[str, Any] = {}
    if headers is not None:
        request_kwargs["headers"] = headers
    if json_payload is not None:
        request_kwargs["json"] = json_payload
    if params is not None:
        request_kwargs["params"] = params

    for attempt in range(1, MAX_API_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.request(method, url, **request_kwargs)
        except asyncio.CancelledError:
            raise
        except httpx.RequestError as error:
            if attempt >= MAX_API_ATTEMPTS:
                raise OpenRouterApiError(
                    f"OpenRouter {method} {url} failed after {MAX_API_ATTEMPTS} attempts: {error}"
                ) from error
            delay = compute_retry_delay(attempt)
            logger.warning(
                "OpenRouter %s %s failed on attempt %d/%d (%s); retrying in %.2fs",
                method,
                url,
                attempt,
                MAX_API_ATTEMPTS,
                error,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_API_ATTEMPTS:
            delay = compute_retry_delay(
                attempt,
                retry_after=(
                    response.headers.get("Retry-After") if response.status_code == 429 else None
                ),
            )
            logger.warning(
                "OpenRouter %s %s returned HTTP %s on attempt %d/%d; retrying in %.2fs",
                method,
                url,
                response.status_code,
                attempt,
                MAX_API_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        return response

    raise RuntimeError(f"OpenRouter {method} {url} retry loop exited unexpectedly")


class OpenRouterApiError(RuntimeError):
    """Raised when an OpenRouter request fails."""


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: str,
        site_url: str | None = None,
        app_name: str | None = None,
        app_categories: str | None = None,
        model_cache_ttl_seconds: int = OPENROUTER_MODEL_CACHE_TTL_SECONDS,
    ):
        self.api_key = api_key
        self.site_url = site_url
        self.app_name = app_name
        self.app_categories = app_categories
        self.model_cache_ttl_seconds = model_cache_ttl_seconds
        self._models_cache: list[ModelInfo] = []
        self._models_cache_expires_at = 0.0
        self._models_lock = asyncio.Lock()

    async def create_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        modalities: list[str] | None = None,
        image_config: dict[str, Any] | None = None,
        plugins: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        cache_control: dict[str, Any] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        reasoning_max_tokens: int | None = None,
        exclude_reasoning: bool = False,
        user: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        openrouter_sdk = self._import_openrouter_sdk()
        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.site_url:
            client_kwargs["http_referer"] = self.site_url
        if self.app_name:
            client_kwargs["x_open_router_title"] = self.app_name

        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if modalities:
            request_kwargs["modalities"] = list(modalities)
        if image_config:
            request_kwargs["image_config"] = dict(image_config)
        if plugins:
            request_kwargs["plugins"] = [dict(plugin) for plugin in plugins]
        if tools:
            request_kwargs["tools"] = [dict(tool) for tool in tools]
        if cache_control:
            request_kwargs["cache_control"] = dict(cache_control)
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        if top_p is not None:
            request_kwargs["top_p"] = top_p
        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens
        reasoning_config = _build_reasoning_config(
            reasoning_effort=reasoning_effort,
            reasoning_max_tokens=reasoning_max_tokens,
            exclude_reasoning=exclude_reasoning,
        )
        if reasoning_config is not None:
            request_kwargs["reasoning"] = reasoning_config
        if user:
            request_kwargs["user"] = user
        if session_id:
            request_kwargs["session_id"] = session_id[:128]

        try:
            async with openrouter_sdk.OpenRouter(**client_kwargs) as client:
                response = await client.chat.send_async(**request_kwargs)
        except ModuleNotFoundError as error:
            raise OpenRouterApiError(
                "The `openrouter` Python package is not installed. Run `python -m pip install .`."
            ) from error
        except Exception as error:
            raise OpenRouterApiError(str(error)) from error
        return _to_plain_dict(response)

    async def create_speech(
        self,
        *,
        model: str,
        input_text: str,
        voice: str | None,
        response_format: str,
        modalities: list[str] | None = None,
        instructions: str | None = None,
        user: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        prompt_text = _build_tts_prompt(input_text=input_text, instructions=instructions)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt_text}],
            "modalities": list(modalities or ["text", "audio"]),
            "audio": {
                "format": response_format,
            },
            "stream": True,
        }
        if voice:
            payload["audio"]["voice"] = voice
        if user:
            payload["user"] = user
        if session_id:
            payload["session_id"] = session_id[:128]
        return await self._stream_audio_completion(payload)

    async def create_video_generation(
        self,
        *,
        model: str,
        prompt: str,
        duration: int | None = None,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
        size: str | None = None,
        input_references: list[dict[str, Any]] | None = None,
        generate_audio: bool | None = None,
        seed: int | None = None,
        provider: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
        }
        if duration is not None:
            payload["duration"] = duration
        if resolution:
            payload["resolution"] = resolution
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if size:
            payload["size"] = size
        if input_references:
            payload["input_references"] = [dict(reference) for reference in input_references]
        if generate_audio is not None:
            payload["generate_audio"] = generate_audio
        if seed is not None:
            payload["seed"] = seed
        if provider:
            payload["provider"] = dict(provider)

        timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
        response = await _request_with_retries(
            "POST",
            f"{OPENROUTER_BASE_URL}/videos",
            timeout=timeout,
            headers=self._request_headers(),
            json_payload=payload,
        )

        if response.status_code >= 400:
            raise OpenRouterApiError(_extract_error_message(response))
        return response.json()

    async def get_video_generation(
        self,
        *,
        job_id: str | None = None,
        polling_url: str | None = None,
    ) -> dict[str, Any]:
        if not polling_url and not job_id:
            raise OpenRouterApiError("A video polling URL or job ID is required.")

        timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
        response = await _request_with_retries(
            "GET",
            polling_url or f"{OPENROUTER_BASE_URL}/videos/{job_id}",
            timeout=timeout,
            headers=self._request_headers(),
        )

        if response.status_code >= 400:
            raise OpenRouterApiError(_extract_error_message(response))
        return response.json()

    async def download_file_bytes(self, url: str) -> tuple[bytes, str | None]:
        headers = self._request_headers() if "openrouter.ai/" in url else None
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        response = await _request_with_retries(
            "GET",
            url,
            timeout=timeout,
            headers=headers,
        )

        if response.status_code >= 400:
            raise OpenRouterApiError(_extract_error_message(response))
        return response.content, response.headers.get("Content-Type")

    async def list_models(
        self,
        *,
        query: str | None = None,
        limit: int = 10,
        refresh: bool = False,
        input_modality: str | None = None,
        output_modality: str | None = None,
    ) -> list[ModelInfo]:
        if limit <= 0:
            return []
        models = await self._get_cached_models(refresh=refresh)
        if input_modality:
            models = [
                model
                for model in models
                if input_modality.casefold() in _casefolded(model.input_modalities)
            ]
        if output_modality:
            models = [
                model
                for model in models
                if output_modality.casefold() in _casefolded(model.output_modalities)
            ]
        if not query:
            return sorted(models, key=lambda model: model.name.casefold())[:limit]

        needle = query.strip().casefold()
        ranked_matches: list[tuple[int, str, ModelInfo]] = []
        for model in models:
            haystacks = [
                model.id.casefold(),
                model.name.casefold(),
                (model.canonical_slug or "").casefold(),
                (model.description or "").casefold(),
            ]
            if needle == haystacks[0] or needle == haystacks[1] or needle == haystacks[2]:
                rank = 0
            elif haystacks[0].startswith(needle) or haystacks[1].startswith(needle):
                rank = 1
            elif any(needle in haystack for haystack in haystacks[:3]):
                rank = 2
            elif needle in haystacks[3]:
                rank = 3
            else:
                continue
            ranked_matches.append((rank, model.name.casefold(), model))

        ranked_matches.sort(key=lambda item: (item[0], item[1]))
        return [model for _, _, model in ranked_matches[:limit]]

    async def get_model(self, model_query: str, *, refresh: bool = False) -> ModelInfo | None:
        normalized_query = model_query.strip()
        if not normalized_query:
            return None
        exact_matches = await self.list_models(query=normalized_query, limit=25, refresh=refresh)
        normalized_casefold = normalized_query.casefold()
        for model in exact_matches:
            if normalized_casefold in {
                model.id.casefold(),
                model.name.casefold(),
                (model.canonical_slug or "").casefold(),
            }:
                return model
        return exact_matches[0] if exact_matches else None

    async def _get_cached_models(self, *, refresh: bool) -> list[ModelInfo]:
        now = time.monotonic()
        if not refresh and self._models_cache and now < self._models_cache_expires_at:
            return list(self._models_cache)

        async with self._models_lock:
            now = time.monotonic()
            if not refresh and self._models_cache and now < self._models_cache_expires_at:
                return list(self._models_cache)
            self._models_cache = await self._fetch_models_from_api()
            self._models_cache_expires_at = now + self.model_cache_ttl_seconds
            return list(self._models_cache)

    async def _stream_audio_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
        async with (
            httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client,
            client.stream(
                "POST",
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=self._request_headers(),
                json=payload,
            ) as response,
        ):
            if response.status_code >= 400:
                body = await response.aread()
                raise OpenRouterApiError(
                    _extract_error_message_from_bytes(response.status_code, body)
                )
            return await _collect_audio_stream(response.aiter_lines())

    async def _fetch_models_from_api(self) -> list[ModelInfo]:
        response = await _request_with_retries(
            "GET",
            f"{OPENROUTER_BASE_URL}/models/user",
            timeout=30.0,
            headers=self._request_headers(),
        )
        if response.status_code in {404, 405, 422}:
            response = await _request_with_retries(
                "GET",
                f"{OPENROUTER_BASE_URL}/models",
                timeout=30.0,
                headers=self._request_headers(),
                params={"output_modalities": "all"},
            )

        if response.status_code >= 400:
            raise OpenRouterApiError(_extract_error_message(response))

        payload = response.json()
        return [parse_model_info(item) for item in payload.get("data") or []]

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-OpenRouter-Title"] = self.app_name
        if self.app_categories:
            headers["X-OpenRouter-Categories"] = self.app_categories
        return headers

    @staticmethod
    def _import_openrouter_sdk():
        return importlib.import_module("openrouter")


def _extract_error_message(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error_payload = payload.get("error") or {}
        message = error_payload.get("message") or payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return f"OpenRouter request failed with status {response.status_code}."


def _extract_error_message_from_bytes(status_code: int, payload_bytes: bytes) -> str:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        error_payload = payload.get("error") or {}
        message = error_payload.get("message") or payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return f"OpenRouter request failed with status {status_code}."


async def _collect_audio_stream(lines) -> dict[str, Any]:
    audio_chunks: list[str] = []
    transcript_parts: list[str] = []
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    resolved_model: str | None = None

    async for raw_line in lines:
        line = raw_line.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        if isinstance(chunk.get("model"), str):
            resolved_model = chunk["model"]
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]

        choice = (chunk.get("choices") or [None])[0]
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            continue

        audio_payload = delta.get("audio") or {}
        if isinstance(audio_payload, dict):
            if isinstance(audio_payload.get("data"), str) and audio_payload["data"]:
                audio_chunks.append(audio_payload["data"])
            if isinstance(audio_payload.get("transcript"), str) and audio_payload["transcript"]:
                transcript_parts.append(audio_payload["transcript"])

        content = delta.get("content")
        if isinstance(content, str) and content:
            text_parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text:
                    text_parts.append(text)

    audio_bytes = base64.b64decode("".join(audio_chunks)) if audio_chunks else b""
    return {
        "audio_bytes": audio_bytes,
        "transcript": "".join(transcript_parts).strip(),
        "text": "".join(text_parts).strip(),
        "usage": usage,
        "model": resolved_model,
    }


def _build_tts_prompt(*, input_text: str, instructions: str | None) -> str:
    normalized_instructions = (instructions or "").strip()
    if not normalized_instructions:
        return input_text
    return f"{normalized_instructions}\n\nText to speak:\n{input_text}"


def _build_reasoning_config(
    *,
    reasoning_effort: str | None,
    reasoning_max_tokens: int | None,
    exclude_reasoning: bool,
) -> dict[str, Any] | None:
    config: dict[str, Any] = {}
    explicit_reasoning_control = bool(reasoning_effort) or reasoning_max_tokens is not None
    if reasoning_effort:
        config["effort"] = reasoning_effort
    if reasoning_max_tokens is not None:
        config["max_tokens"] = reasoning_max_tokens
    if exclude_reasoning:
        config["exclude"] = True
        if not explicit_reasoning_control:
            config["enabled"] = True
    return config or None


def _casefolded(values: list[str]) -> set[str]:
    return {value.casefold() for value in values}


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(exclude_none=True)
    raise OpenRouterApiError("Unexpected response type returned by the OpenRouter SDK.")


def build_openrouter_client() -> OpenRouterClient:
    if not OPENROUTER_API_KEY:
        raise OpenRouterApiError("OPENROUTER_API_KEY is not configured.")
    return OpenRouterClient(
        api_key=OPENROUTER_API_KEY,
        site_url=OPENROUTER_SITE_URL,
        app_name=OPENROUTER_APP_NAME,
        app_categories=OPENROUTER_APP_CATEGORIES,
        model_cache_ttl_seconds=OPENROUTER_MODEL_CACHE_TTL_SECONDS,
    )
