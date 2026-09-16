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

- Chat uses the official `openrouter` Python SDK. The typed request model `ChatRequestReasoning` declares only
  `effort` and `summary`, and its generated serializer drops undeclared keys silently — so the `reasoning` object can carry an
  effort and nothing else. `reasoning_max_tokens`/`exclude_reasoning` slash options were removed in v1.6.0 for exactly this
  reason (they rendered in the settings embed but never reached the wire). `tests/test_openrouter_client.py::test_installed_sdk_still_models_only_effort_and_summary` fails if the SDK ever grows those fields, at which point the options
  can be reinstated for real (last checked against openrouter 1.1.144 on 2026-09-15: still exactly `effort` + `summary`; `chat.send_async`'s 49 parameters and the `ChatResult` / `ChatChoice` / `ChatUsage` / `CostDetails` models are unchanged since 1.1.121). Video and model listing go through raw `httpx` calls wrapped by `_request_with_retries` in `client.py` (exponential backoff + jitter on 429/500/502/503/504, respects `Retry-After` header). Image and STT go through `create_chat_completion` on the typed SDK instead, and TTS streams over raw `httpx` in `_stream_audio_completion` with no retry wrapper.
- `OPENROUTER_APP_CATEGORIES` reaches OpenRouter on both transports: the raw `httpx` paths send it as the `X-OpenRouter-Categories` header via `_request_headers`, and `create_chat_completion` passes it as `x_open_router_categories` to the typed SDK client (fixed in v1.6.1 — it previously never reached the SDK path). Pinned by `tests/test_openrouter_client.py::test_create_chat_completion_uses_sdk_and_reasoning` (set) and `::test_create_chat_completion_omits_categories_when_unset` (absent), and the real constructor call in `::test_installed_openrouter_sdk_matches_client_usage`.
- TTS model validation in `speech.py` accepts either `audio` or `speech` in `output_modalities` (`TTS_OUTPUT_MODALITIES`): the live catalog labels dedicated TTS models `speech` and only multimodal audio chat models (gpt-audio, lyria) `audio`, so an `audio`-only check rejected every real TTS model once its metadata resolved (fixed in v1.6.1). Covered by `tests/test_speech.py::TestRunTtsCommand::test_accepts_model_advertising_speech_output` and `::test_rejects_when_model_does_not_advertise_audio_output`.
- The model catalog lists **every** output modality since v1.7.0 (image-only, speech, transcription, video, embeddings, rerank entries are new; image+text models such as the default image model were already in the text-default list): both listing calls send `output_modalities=all` (`MODEL_LIST_PARAMS`; previously only the `/v1/models` fallback did, so a keyed bot saw text-output models only). Checked keyless on 2026-08-28: 540 entries with the parameter vs. 396 without; the extra `architecture.output_modalities` labels are `image` (image-only), `speech` (dedicated TTS), `transcription`, `video`, `embeddings`, and `rerank`, and every entry parses through `parse_model_info` unchanged (media entries price `prompt`/`completion` as `"0"` with per-image keys such as `image_token`/`image_output` that the parser ignores; `context_length` is `0` for many, which the list embed renders as `ctx unknown`). Consequences: `MODEL_OUTPUT_MODALITY_CHOICES` offers one `/openrouter models` filter per label (8 of the 25 allowed); `audio` also matches `speech` via `OUTPUT_MODALITY_FILTER_ALIASES` in `client.py`; `/openrouter chat` and `/openrouter switch_model` run `validate_model_output_modalities` (`chat.py`, un-prefixed because `cog.py` imports it) to reject non-text-output entries before the request or any state write, since a fuzzy `model` query can now resolve to a TTS or image-only model — `switch_model` guards only `modality=chat`, since the other modalities are validated by the command that drives them; the image/video/TTS pre-flight checks now run against real entries for the default models. `/openrouter-tools stt` still requires `text` output, so dedicated `transcription` models get the existing friendly error — the STT path drives chat completions, not a transcription endpoint. Pinned by `tests/test_util.py::test_parse_model_info_handles_every_live_output_modality`, `tests/test_openrouter_client.py::test_fetch_models_primary_requests_all_modalities` / `::test_list_models_output_filters_cover_every_live_modality`, `tests/test_chat.py::test_run_chat_command_rejects_non_text_output_model_before_request`, `tests/test_openrouter_cog.py::TestModels::test_every_modality_filter_choice_matches_a_live_catalog_entry`, `tests/test_openrouter_cog.py::TestSwitchModel::test_chat_scope_conversation_rejects_non_text_output_model`, and the live-catalog cases in `tests/test_image.py`, `tests/test_video.py`, and `tests/test_speech.py`.
- Pricing is fetched **dynamically** from OpenRouter's `/v1/models/user` endpoint, falling back to `/v1/models` on 404/405/422 (both with `output_modalities=all`) — there is no local `pricing.yaml` in this bot. Metadata is cached per `OPENROUTER_MODEL_CACHE_TTL_SECONDS`. See https://github.com/pydantic/genai-prices/blob/main/prices/providers/openrouter.yml for a third-party cross-reference of all 500+ OpenRouter model prices.
- Conversation state is pruned on a 15-minute `tasks.loop`. `CONVERSATION_TTL`, `MAX_ACTIVE_CONVERSATIONS`, `VIEW_STATE_TTL`, and `DAILY_COST_RETENTION_DAYS` live in `cogs/openrouter/state.py`.
- Every slash command enters via `cog_before_invoke` which binds a fresh request id via `discord_openrouter.logging_setup.bind_request_id`. `on_message` does the same for follow-up messages.
- `LOG_FORMAT=json` switches log output to JSON lines suitable for log aggregators; leave unset for human-readable text mode.
- **Async file I/O**: blocking `open()` and `pathlib` methods (`read_bytes`, `write_bytes`, `unlink`, etc.) inside `async def` freeze the Discord event loop and stall every concurrent slash command. Wrap them with `asyncio.to_thread(...)` so the I/O runs on a worker thread. Enforced by `ruff` (`ASYNC230`/`ASYNC240`).
