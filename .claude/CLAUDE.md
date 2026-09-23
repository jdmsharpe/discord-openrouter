# Discord OpenRouter Bot - Developer Reference

## Quick Start

```bash
uv sync --extra dev                   # creates .venv from uv.lock (no pip inside — use `uv pip` if needed)
cp .env.example .env                  # then fill in required values
git config core.hooksPath .githooks   # enable repo pre-commit hook
uv run python src/bot.py   # or: docker compose up --build
```

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Discord bot token |
| `GUILD_IDS` | Yes | Comma-separated Discord guild IDs for slash command registration; empty or unset registers no commands at all |
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key |
| `OPENROUTER_DEFAULT_TEXT_MODEL` | No | Default chat model when no channel or conversation default is set |
| `OPENROUTER_DEFAULT_IMAGE_MODEL` | No | Default for `/openrouter-media image` |
| `OPENROUTER_DEFAULT_VIDEO_MODEL` | No | Default for `/openrouter-media video` |
| `OPENROUTER_DEFAULT_TTS_MODEL` | No | Default for `/openrouter-tools tts` |
| `OPENROUTER_DEFAULT_STT_MODEL` | No | Default for `/openrouter-tools stt` |
| `OPENROUTER_DEFAULT_PDF_ENGINE` | No | PDF parser for attachments (`cloudflare-ai`, `mistral-ocr`, `native`) |
| `OPENROUTER_SITE_URL` | No | App attribution header sent to OpenRouter for rankings |
| `OPENROUTER_APP_NAME` | No | App attribution title |
| `OPENROUTER_APP_CATEGORIES` | No | App attribution categories |
| `OPENROUTER_MODEL_CACHE_TTL_SECONDS` | No | How long to cache the `/v1/models/user?output_modalities=all` catalog (default: 300) |
| `SHOW_COST_EMBEDS` | No | Show token/cost embeds on responses (default: `true`; accepts `true/1/yes`) |
| `LOG_FORMAT` | No | `text` (default) or `json` for structured JSON-lines output |

`validate_required_config()` raises `RuntimeError` at startup for missing/blank `BOT_TOKEN` or `OPENROUTER_API_KEY`.

## Gotchas

- Uses **`py-cord`** (not `discord.py`). The slash-command API differs; don't mix docs between the two.
- `GUILD_IDS` must list at least one guild ID; empty or unset registers the commands **nowhere** — not globally, not per-guild (`_parse_guild_ids("")` returns an empty list, not `None`, and py-cord only registers a command globally when `guild_ids is None`). Set it to a test guild ID during development for instant updates.
- Unlike the other AI bots in this family, discord-openrouter does **not** ship a `pricing.yaml`. Pricing is fetched dynamically from OpenRouter's `/v1/models/user` endpoint (falling back to `/v1/models`) and cached per `OPENROUTER_MODEL_CACHE_TTL_SECONDS`.
- **Both** listing calls pass `output_modalities=all` (`MODEL_LIST_PARAMS` in `client.py`) because the endpoints return text-output models by default (openapi.json: "Returns text-output models by default"). Without it image-only, video, and TTS models never reach `get_model`, so every media pre-flight check silently skips — which is exactly what happened with a valid key before v1.7.0, when only the fallback call carried the parameter.

## Supported Entry Points

- Launcher: `python src/bot.py` remains supported and delegates to `discord_openrouter.bot.main`.
- Cog composition contract:

  ```python
  from discord_openrouter import OpenRouterCog

  bot.add_cog(OpenRouterCog(bot=bot))
  ```

- `discord_openrouter.bot.main()` calls `validate_required_config()` and `configure_logging()` before connecting.

## Package Layout

```text
src/
├── bot.py                           # Thin repo-local launcher
└── discord_openrouter/
    ├── __init__.py
    ├── bot.py
    ├── logging_setup.py             # Structured logging + request-id ContextVar
    ├── util.py
    ├── config/
    │   ├── __init__.py
    │   └── auth.py
    └── cogs/openrouter/
        ├── __init__.py
        ├── attachments.py
        ├── chat.py
        ├── client.py                # Retry-wrapped httpx calls + dynamic model catalog
        ├── cog.py
        ├── command_options.py
        ├── embed_delivery.py        # Discord embed batching (6000-char/10-embed caps)
        ├── embeds.py
        ├── image.py
        ├── speech.py
        ├── state.py                 # Conversation TTL + prune logic
        ├── tool_registry.py
        ├── video.py
        └── views.py
```

Only `src/bot.py` remains at the repo root; code imports should target `discord_openrouter...`.

## Testing And Patch Targets

- `pytest` runs with `pythonpath = ["src"]`.
- The test suite targets the namespaced package layout under `discord_openrouter...`.
- Runtime state pruning is covered in `tests/test_openrouter_state.py`.
- Retry-loop semantics are covered in `tests/test_openrouter_client.py`.
- `tests/fixtures/openrouter_models_mixed.json` holds one real catalog entry per output-modality shape (captured keyless from `/api/v1/models?output_modalities=all`); `tests/conftest.py` exposes it as the session fixtures `mixed_modality_catalog` (raw dicts) and `mixed_modality_models` (parsed `ModelInfo` keyed by id). Use it whenever a test needs a realistic image/speech/transcription/video/embeddings/rerank entry instead of a hand-written `ModelInfo`.
- New tests and patches should target real owners under `discord_openrouter...`.
- Examples:
  - `discord_openrouter.cogs.openrouter.client.OpenRouterClient`
  - `discord_openrouter.cogs.openrouter.client._request_with_retries`
  - `discord_openrouter.cogs.openrouter.state.prune_runtime_state`
  - `discord_openrouter.cogs.openrouter.views.ButtonView`

## Validation Commands

```bash
ruff check src/ tests/
ruff format src/ tests/
pyright src/
pytest -q
```

- The repo pre-commit hook under `.githooks/pre-commit` runs `ruff format` (auto-applied + re-staged), then `ruff check` (blocking), then `pyright` and `pytest --collect-only` as warning-only smoke tests. Resolve tools from `.venv/bin` or `.venv/Scripts` first, then `PATH`.

## Provider Notes

- Every OpenRouter call is a raw `httpx` request in `client.py`; the bot does not depend on the `openrouter` Python SDK. Chat, image and STT (`create_chat_completion`, `POST /chat/completions`), video, and model listing go through `_request_with_retries` (exponential backoff + jitter on 429/500/502/503/504, respects `Retry-After` header; transport errors, read timeouts included, are retried as well). TTS streams over raw `httpx` in `_stream_audio_completion` with no retry wrapper. `create_chat_completion` returns the parsed JSON body unchanged, so `message.annotations`, `message.images`, `usage.server_tool_use_details`, `usage.completion_tokens_details.image_tokens` and `usage.cost` reach the callers; it raises `OpenRouterApiError` with the API's error message on a non-2xx status and on an HTTP 200 body that carries `error` without `choices`. Pinned by `tests/test_openrouter_client.py::test_create_chat_completion_posts_payload_with_attribution_headers` (exact URL, headers and JSON body), `::test_create_chat_completion_maps_4xx_to_api_error` and `::test_create_chat_completion_raises_on_error_body_with_http_200`.
- Why the chat path no longer uses the `openrouter` SDK (the last locked version was openrouter 1.2.19): the SDK converted each response through typed models that drop fields the bot reads. Checked live on 2026-09-22 with the bot's web-search request (`deepseek/deepseek-v4-flash`, `tools=[{"type": "openrouter:web_search"}]`): a raw POST returned `choices[0].message.annotations` (10 `url_citation` entries) and the SDK result returned none, so `extract_url_citations` got nothing and the Sources embed never appeared. The SDK was also slow: the same `google/gemini-3.1-flash-image` request (`modalities=["image", "text"]`) finished in about 10 s raw (`usage.completion_tokens_details` = `{"reasoning_tokens": 0, "image_tokens": 1120, "audio_tokens": 0}`, `cost` 0.0672035) and was still waiting for response headers after 150 s through the SDK; the web-search request took 75 s through the SDK and 10 s raw. The SDK's request body was the raw body plus `"stream": false`, and a raw request with `"stream": false` also completed in 9 s. Rechecked on 2026-09-22 through the new `OpenRouterClient.create_chat_completion`: the web-search request took 35 s and returned 25 annotations (15 unique citation URLs) with `server_tool_use_details` = 5 web searches; the image request took 10 s and returned one PNG with `image_tokens` 1120 and `cost` 0.0672045. Pinned by `::test_create_chat_completion_returns_web_search_annotations_and_tool_counts` and `::test_create_chat_completion_returns_image_tokens_and_images`.
- Server-tool counts: the live usage object (2026-09-22) carries `server_tool_use_details` = `{"web_search_requests": 2, "tool_calls_requested": 2, "tool_calls_executed": 2}` and has no `server_tool_use` key. `extract_usage` (`util.py`) reads `server_tool_use_details` and reads `server_tool_use` only when that key is absent; `extract_web_search_requests` takes `web_search_requests` from the result, which sets the "N searches" count and the `search` cost row in the usage embed. Pinned by `tests/test_util.py::test_extract_usage_reads_server_tool_use_details` / `::test_extract_usage_prefers_server_tool_use_details_over_server_tool_use` and `tests/test_embeds.py::test_append_usage_embed_counts_searches_from_server_tool_use_details`.
- Reasoning: `_build_reasoning_config` builds `{"effort": <value>}` and nothing else, and the raw request sends it as built; `::test_create_chat_completion_sends_exactly_the_effort_reasoning_object` pins that body for every `REASONING_EFFORT_CHOICES` value. The `reasoning_max_tokens`/`exclude_reasoning` slash options were removed in v1.6.0 because the SDK's `ChatRequestReasoning` model serialised only `effort` and `summary`, so those settings never reached the wire. The raw request has no such limit, so reinstating them is now possible as a separate change.
- Attribution headers: every request built by `_request_headers` sends `Authorization` and `Content-Type` plus `HTTP-Referer` (`OPENROUTER_SITE_URL`), `X-OpenRouter-Title` (`OPENROUTER_APP_NAME`) and `X-OpenRouter-Categories` (`OPENROUTER_APP_CATEGORIES`), each of the last three only when set. Chat requests carry them like every other call. Pinned by `::test_create_chat_completion_posts_payload_with_attribution_headers` (set), `::test_create_chat_completion_omits_categories_when_unset` (absent) and `::test_request_headers_use_documented_openrouter_names`.
- Conversation history: `sanitize_assistant_message` keeps `annotations`, and chat responses now include them, so an assistant turn that used web search or PDF parsing is stored with its annotations and sends them back to OpenRouter on the next turn (the SDK path dropped them from the response). Checked live on 2026-09-22: a follow-up turn whose history held a web-search reply stored by `sanitize_assistant_message` (5 annotations plus `reasoning`) was accepted and answered normally.
- TTS model validation in `speech.py` accepts either `audio` or `speech` in `output_modalities` (`TTS_OUTPUT_MODALITIES`): the live catalog labels dedicated TTS models `speech` and only multimodal audio chat models (gpt-audio, lyria) `audio`, so an `audio`-only check rejected every real TTS model once its metadata resolved (fixed in v1.6.1). Covered by `tests/test_speech.py::TestRunTtsCommand::test_accepts_model_advertising_speech_output` and `::test_rejects_when_model_does_not_advertise_audio_output`.
- The model catalog lists **every** output modality since v1.7.0 (image-only, speech, transcription, video, embeddings, rerank entries are new; image+text models such as the default image model were already in the text-default list): both listing calls send `output_modalities=all` (`MODEL_LIST_PARAMS`; previously only the `/v1/models` fallback did, so a keyed bot saw text-output models only). Checked keyless on 2026-08-28: 540 entries with the parameter vs. 396 without; the extra `architecture.output_modalities` labels are `image` (image-only), `speech` (dedicated TTS), `transcription`, `video`, `embeddings`, and `rerank`, and every entry parses through `parse_model_info` unchanged (media entries price `prompt`/`completion` as `"0"` with per-image keys such as `image_token`/`image_output` that the parser ignores; `context_length` is `0` for many, which the list embed renders as `ctx unknown`). Consequences: `MODEL_OUTPUT_MODALITY_CHOICES` offers one `/openrouter models` filter per label (8 of the 25 allowed); `audio` also matches `speech` via `OUTPUT_MODALITY_FILTER_ALIASES` in `client.py`; `/openrouter chat` and `/openrouter switch_model` run `validate_model_output_modalities` (`chat.py`, un-prefixed because `cog.py` imports it) to reject non-text-output entries before the request or any state write, since a fuzzy `model` query can now resolve to a TTS or image-only model — `switch_model` guards only `modality=chat`, since the other modalities are validated by the command that drives them; the image/video/TTS pre-flight checks now run against real entries for the default models. `/openrouter-tools stt` still requires `text` output, so dedicated `transcription` models get the existing friendly error — the STT path drives chat completions, not a transcription endpoint. Pinned by `tests/test_util.py::test_parse_model_info_handles_every_live_output_modality`, `tests/test_openrouter_client.py::test_fetch_models_primary_requests_all_modalities` / `::test_list_models_output_filters_cover_every_live_modality`, `tests/test_chat.py::test_run_chat_command_rejects_non_text_output_model_before_request`, `tests/test_openrouter_cog.py::TestModels::test_every_modality_filter_choice_matches_a_live_catalog_entry`, `tests/test_openrouter_cog.py::TestSwitchModel::test_chat_scope_conversation_rejects_non_text_output_model`, and the live-catalog cases in `tests/test_image.py`, `tests/test_video.py`, and `tests/test_speech.py`.
- Pricing is fetched **dynamically** from OpenRouter's `/v1/models/user` endpoint, falling back to `/v1/models` on 404/405/422 (both with `output_modalities=all`) — there is no local `pricing.yaml` in this bot. Metadata is cached per `OPENROUTER_MODEL_CACHE_TTL_SECONDS`. See https://github.com/pydantic/genai-prices/blob/main/prices/providers/openrouter.yml for a third-party cross-reference of all 500+ OpenRouter model prices.
- Conversation state is pruned on a 15-minute `tasks.loop`. `CONVERSATION_TTL`, `MAX_ACTIVE_CONVERSATIONS`, `VIEW_STATE_TTL`, and `DAILY_COST_RETENTION_DAYS` live in `cogs/openrouter/state.py`.
- Every slash command enters via `cog_before_invoke` which binds a fresh request id via `discord_openrouter.logging_setup.bind_request_id`. `on_message` does the same for follow-up messages.
- `LOG_FORMAT=json` switches log output to JSON lines suitable for log aggregators; leave unset for human-readable text mode.
- **Async file I/O**: blocking `open()` and `pathlib` methods (`read_bytes`, `write_bytes`, `unlink`, etc.) inside `async def` freeze the Discord event loop and stall every concurrent slash command. Wrap them with `asyncio.to_thread(...)` so the I/O runs on a worker thread. Enforced by `ruff` (`ASYNC230`/`ASYNC240`).
