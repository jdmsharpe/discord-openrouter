from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import random
import time
from datetime import UTC, datetime
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
# Both listing endpoints return text-output models only unless asked otherwise
# (openapi.json: "Returns text-output models by default"), so every catalog fetch
# passes this or image/video/TTS models never reach `get_model` and the `/models` search.
MODEL_LIST_PARAMS: dict[str, str] = {"output_modalities": "all"}
# `/openrouter models output_modality:audio` should surface TTS models too: the catalog labels
# dedicated TTS models "speech" and only the multimodal audio chat models (gpt-audio, lyria)
# "audio" -- the same split `TTS_OUTPUT_MODALITIES` in speech.py accepts.
OUTPUT_MODALITY_FILTER_ALIASES: dict[str, frozenset[str]] = {
    "audio": frozenset({"audio", "speech"}),
}
# `POST /audio/speech` returns no usage or cost; the cost appears later under
# `GET /generation?id=<X-Generation-Id>`, which answers 404 until the record exists.
# `get_generation` waits this long before each lookup and gives up after this many.
GENERATION_LOOKUP_INTERVAL_SECONDS = 1.0
GENERATION_LOOKUP_ATTEMPTS = 4
GENERATION_LOOKUP_REQUEST_TIMEOUT_SECONDS = 5.0
GENERATION_LOOKUP_TIMEOUT_SECONDS = 6.0
PROVIDER_ERROR_MAX_CHARS = 500
# Audio chat models answer a plain user message as a conversation turn; this system
# message makes them speak the user's text instead.
READ_ALOUD_SYSTEM_PROMPT = (
    "Read the user's text aloud exactly as written. Do not answer it, add to it or comment on it."
)

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
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
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
    # `timeout` forwards to httpx.AsyncClient(timeout=...); httpx manages the deadline.
    timeout: httpx.Timeout | float,  # noqa: ASYNC109
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


class SpeechFormatRejectedError(OpenRouterApiError):
    """Raised when `/audio/speech` answers 400 because the model does not offer the format."""


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
        # Speech models that rejected mp3 with a 400; later requests ask them for pcm directly.
        self._pcm_only_speech_models: set[str] = set()

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
        user: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if modalities:
            payload["modalities"] = list(modalities)
        if image_config:
            payload["image_config"] = dict(image_config)
        if plugins:
            payload["plugins"] = [dict(plugin) for plugin in plugins]
        if tools:
            payload["tools"] = [dict(tool) for tool in tools]
        if cache_control:
            payload["cache_control"] = dict(cache_control)
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            # OpenRouter deprecated `max_tokens` in favor of `max_completion_tokens`.
            # Send the modern key so we are not relying on the deprecated alias.
            payload["max_completion_tokens"] = max_tokens
        reasoning_config = _build_reasoning_config(reasoning_effort=reasoning_effort)
        if reasoning_config is not None:
            payload["reasoning"] = reasoning_config
        if user:
            payload["user"] = user
        if session_id:
            payload["session_id"] = session_id[:128]

        timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
        response = await _request_with_retries(
            "POST",
            f"{OPENROUTER_BASE_URL}/chat/completions",
            timeout=timeout,
            headers=self._request_headers(),
            json_payload=payload,
        )

        if response.status_code >= 400:
            raise OpenRouterApiError(_extract_error_message(response))
        result = response.json()
        # OpenRouter returns HTTP 200 with an `error` object instead of `choices` when
        # the model fails after the request was accepted.
        if isinstance(result, dict) and result.get("error") and not result.get("choices"):
            raise OpenRouterApiError(_extract_error_message(response))
        return result

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
        read_aloud: bool = False,
    ) -> dict[str, Any]:
        """Stream speech from an `audio` output chat model.

        With `read_aloud`, a system message tells the model to speak the text as written
        (style `instructions` are appended to it) and the user message repeats that request
        above the quoted text; with the bare text as the user message the model answers it.
        Without it, the instructions are prepended to the text in a single user message.
        """
        if read_aloud:
            messages = [
                {"role": "system", "content": _build_read_aloud_system_prompt(instructions)},
                {"role": "user", "content": _build_read_aloud_user_prompt(input_text)},
            ]
        else:
            prompt_text = _build_tts_prompt(input_text=input_text, instructions=instructions)
            messages = [{"role": "user", "content": prompt_text}]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
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

    async def create_audio_speech(
        self,
        *,
        model: str,
        input_text: str,
        voice: str | None,
        response_format: str,
        instructions: str | None = None,
        user: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize speech with `POST /audio/speech`, the endpoint for `speech` models.

        `response_format` is `mp3` or `pcm`. When a model answers 400 to `mp3`, the request
        is repeated once with `pcm` and the model is remembered, so later requests ask it
        for `pcm` directly. The result holds the raw audio, its `Content-Type`, the format
        that was requested, and the `X-Generation-Id` header for `get_generation`.
        """
        if response_format != "pcm" and model in self._pcm_only_speech_models:
            response_format = "pcm"
        try:
            return await self._post_audio_speech(
                model=model,
                input_text=input_text,
                voice=voice,
                response_format=response_format,
                instructions=instructions,
                user=user,
                session_id=session_id,
            )
        except SpeechFormatRejectedError:
            if response_format == "pcm":
                raise
            logger.info("%s rejected response_format=%s; requesting pcm", model, response_format)
            self._pcm_only_speech_models.add(model)
            return await self._post_audio_speech(
                model=model,
                input_text=input_text,
                voice=voice,
                response_format="pcm",
                instructions=instructions,
                user=user,
                session_id=session_id,
            )

    async def _post_audio_speech(
        self,
        *,
        model: str,
        input_text: str,
        voice: str | None,
        response_format: str,
        instructions: str | None,
        user: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "input": input_text,
            "response_format": response_format,
        }
        if voice:
            payload["voice"] = voice
        normalized_instructions = (instructions or "").strip()
        if normalized_instructions:
            payload["instructions"] = normalized_instructions
        if user:
            payload["user"] = user
        if session_id:
            payload["session_id"] = session_id[:128]

        timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
        response = await _request_with_retries(
            "POST",
            f"{OPENROUTER_BASE_URL}/audio/speech",
            timeout=timeout,
            headers=self._request_headers(),
            json_payload=payload,
        )

        if response.status_code >= 400:
            message = _extract_error_message(response)
            if response.status_code == 400 and "response_format" in message:
                raise SpeechFormatRejectedError(message)
            raise OpenRouterApiError(message)
        return {
            "audio_bytes": response.content,
            "content_type": response.headers.get("Content-Type"),
            "response_format": response_format,
            "generation_id": response.headers.get("X-Generation-Id"),
        }

    async def get_generation(
        self,
        generation_id: str,
        *,
        attempts: int = GENERATION_LOOKUP_ATTEMPTS,
        interval_seconds: float = GENERATION_LOOKUP_INTERVAL_SECONDS,
        overall_timeout_seconds: float = GENERATION_LOOKUP_TIMEOUT_SECONDS,
    ) -> dict[str, Any] | None:
        """Return the `GET /generation?id=` record, or `None` if it does not appear in time.

        The endpoint answers 404 until OpenRouter has stored the record, so each attempt
        waits `interval_seconds` first. Each attempt is one request with no retry, and the
        whole lookup stops after `overall_timeout_seconds`, because the audio reply waits
        for it. Any other error also returns `None`: the caller only uses the record for
        the cost line.
        """
        try:
            async with asyncio.timeout(overall_timeout_seconds):
                return await self._poll_generation(
                    generation_id, attempts=attempts, interval_seconds=interval_seconds
                )
        except TimeoutError:
            logger.info(
                "Generation lookup for %s stopped after %.1fs",
                generation_id,
                overall_timeout_seconds,
            )
            return None

    async def _poll_generation(
        self, generation_id: str, *, attempts: int, interval_seconds: float
    ) -> dict[str, Any] | None:
        for _ in range(attempts):
            await asyncio.sleep(interval_seconds)
            try:
                async with httpx.AsyncClient(
                    timeout=GENERATION_LOOKUP_REQUEST_TIMEOUT_SECONDS, follow_redirects=True
                ) as client:
                    response = await client.request(
                        "GET",
                        f"{OPENROUTER_BASE_URL}/generation",
                        headers=self._request_headers(),
                        params={"id": generation_id},
                    )
            except httpx.RequestError as error:
                logger.warning("Generation lookup for %s failed: %s", generation_id, error)
                return None
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                logger.warning(
                    "Generation lookup for %s returned HTTP %s",
                    generation_id,
                    response.status_code,
                )
                return None
            try:
                data = response.json().get("data")
            except (ValueError, AttributeError):
                return None
            return data if isinstance(data, dict) else None
        return None

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
            wanted_outputs = OUTPUT_MODALITY_FILTER_ALIASES.get(
                output_modality.casefold(), frozenset({output_modality.casefold()})
            )
            models = [
                model
                for model in models
                if not wanted_outputs.isdisjoint(_casefolded(model.output_modalities))
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
            params=MODEL_LIST_PARAMS,
        )
        if response.status_code in {404, 405, 422}:
            response = await _request_with_retries(
                "GET",
                f"{OPENROUTER_BASE_URL}/models",
                timeout=30.0,
                headers=self._request_headers(),
                params=MODEL_LIST_PARAMS,
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


def _extract_error_message(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    message = _message_from_error_payload(payload)
    return message or f"OpenRouter request failed with status {response.status_code}."


def _extract_error_message_from_bytes(status_code: int, payload_bytes: bytes) -> str:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    message = _message_from_error_payload(payload)
    return message or f"OpenRouter request failed with status {status_code}."


def _message_from_error_payload(payload: Any) -> str | None:
    """Return the error text, followed by the provider's own message when one is present.

    For upstream failures OpenRouter's `error.message` is only "Provider returned error";
    the provider's response is in `error.metadata.raw`, usually a JSON string with its own
    `error.message`.
    """
    if not isinstance(payload, dict):
        return None
    error_payload = payload.get("error")
    if not isinstance(error_payload, dict):
        error_payload = {}
    message = error_payload.get("message") or payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    message = message.strip()
    metadata = error_payload.get("metadata")
    provider_message = _provider_error_message(
        metadata.get("raw") if isinstance(metadata, dict) else None
    )
    if provider_message and provider_message not in message:
        return f"{message}: {provider_message}"
    return message


def _provider_error_message(raw: Any) -> str | None:
    """Return the provider's message from `error.metadata.raw`.

    `raw` is usually a JSON string; a dict or list is read directly, and a list uses its
    first element. Text that is not JSON is returned as it is.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text[:PROVIDER_ERROR_MAX_CHARS]
        return _message_from_provider_body(parsed) or text[:PROVIDER_ERROR_MAX_CHARS]
    return _message_from_provider_body(raw)


def _message_from_provider_body(body: Any) -> str | None:
    if isinstance(body, list):
        return _message_from_provider_body(body[0]) if body else None
    if isinstance(body, str):
        return body.strip()[:PROVIDER_ERROR_MAX_CHARS] or None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    for candidate in (
        error.get("message") if isinstance(error, dict) else error,
        body.get("message"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:PROVIDER_ERROR_MAX_CHARS]
    return None


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

    audio_bytes = _decode_base64_chunks(audio_chunks)
    return {
        "audio_bytes": audio_bytes,
        "transcript": "".join(transcript_parts).strip(),
        "text": "".join(text_parts).strip(),
        "usage": usage,
        "model": resolved_model,
    }


def _decode_base64_chunks(chunks: list[str]) -> bytes:
    """Decode streamed base64 audio chunks.

    A chunk can end in `=` padding (each chunk encoded on its own) or stop in the middle of
    a 4-character group (one encoding split across chunks), so the text is decoded group by
    group and padded text is decoded where it ends.
    """
    decoded = bytearray()
    pending = ""
    for chunk in chunks:
        pending += chunk.strip()
        if pending.endswith("="):
            decoded += base64.b64decode(pending)
            pending = ""
            continue
        usable = len(pending) - len(pending) % 4
        decoded += base64.b64decode(pending[:usable])
        pending = pending[usable:]
    if pending:
        decoded += base64.b64decode(pending + "=" * (-len(pending) % 4))
    return bytes(decoded)


def _build_tts_prompt(*, input_text: str, instructions: str | None) -> str:
    normalized_instructions = (instructions or "").strip()
    if not normalized_instructions:
        return input_text
    return f"{normalized_instructions}\n\nText to speak:\n{input_text}"


def _build_read_aloud_system_prompt(instructions: str | None) -> str:
    normalized_instructions = (instructions or "").strip()
    if not normalized_instructions:
        return READ_ALOUD_SYSTEM_PROMPT
    return f"{READ_ALOUD_SYSTEM_PROMPT}\nDelivery instructions: {normalized_instructions}"


def _build_read_aloud_user_prompt(input_text: str) -> str:
    return f'Read this text aloud exactly as written:\n"""\n{input_text}\n"""'


def _build_reasoning_config(*, reasoning_effort: str | None) -> dict[str, Any] | None:
    """Build the `reasoning` request object.

    The bot exposes only a reasoning-effort option, so the object carries `effort`
    and nothing else; the raw request sends it as built here.
    """
    if not reasoning_effort:
        return None
    return {"effort": reasoning_effort}


def _casefolded(values: list[str]) -> set[str]:
    return {value.casefold() for value in values}


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
