# Discord OpenRouter Bot - Developer Reference

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in required values
python src/bot.py      # or: docker compose up --build
```

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Discord bot token |
| `GUILD_IDS` | Yes | Comma-separated Discord guild IDs for slash command registration |
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key |
| `OPENROUTER_DEFAULT_TEXT_MODEL` | No | Default chat model when no channel or conversation default is set |
| `OPENROUTER_DEFAULT_IMAGE_MODEL` | No | Default for `/openrouter image` |
| `OPENROUTER_DEFAULT_VIDEO_MODEL` | No | Default for `/openrouter video` |
| `OPENROUTER_DEFAULT_TTS_MODEL` | No | Default for `/openrouter tts` |
| `OPENROUTER_DEFAULT_STT_MODEL` | No | Default for `/openrouter stt` |
| `OPENROUTER_DEFAULT_PDF_ENGINE` | No | PDF parser for attachments (`cloudflare-ai`, `mistral-ocr`, `native`) |
| `OPENROUTER_SITE_URL` | No | App attribution header sent to OpenRouter for rankings |
| `OPENROUTER_APP_NAME` | No | App attribution title |
| `OPENROUTER_APP_CATEGORIES` | No | App attribution categories |
| `OPENROUTER_MODEL_CACHE_TTL_SECONDS` | No | How long to cache `/v1/models` metadata (default: 300) |
| `SHOW_COST_EMBEDS` | No | Show token/cost embeds on responses (default: `true`; accepts `true/1/yes`) |
| `LOG_FORMAT` | No | `text` (default) or `json` for structured JSON-lines output |

`validate_required_config()` raises `RuntimeError` at startup for missing/blank `BOT_TOKEN` or `OPENROUTER_API_KEY`.

## Gotchas

- Uses **`py-cord`** (not `discord.py`). The slash-command API differs; don't mix docs between the two.
- `GUILD_IDS` empty → commands register globally (up to 1-hour propagation delay). Set it to a test guild ID during development for instant updates.
- Unlike the other AI bots in this family, discord-openrouter does **not** ship a `pricing.yaml`. Pricing is fetched dynamically from OpenRouter's `/v1/models` endpoint and cached per `OPENROUTER_MODEL_CACHE_TTL_SECONDS`.

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
        ├── embeds.py
        ├── image.py
        ├── models.py
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
- `tests/test_package_import.py` is the package import smoke test.
- Runtime state pruning is covered in `tests/test_openrouter_state.py`.
- Retry-loop semantics are covered in `tests/test_openrouter_client.py`.
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

- Chat uses the official `openrouter` Python SDK. Image, video, TTS/STT, and model listing go through raw `httpx` calls wrapped by `_request_with_retries` in `client.py` (exponential backoff + jitter on 429/500/502/503/504, respects `Retry-After` header).
- Pricing is fetched **dynamically** from OpenRouter's `/v1/models` endpoint — there is no local `pricing.yaml` in this bot. Metadata is cached per `OPENROUTER_MODEL_CACHE_TTL_SECONDS`. See https://github.com/pydantic/genai-prices/blob/main/prices/providers/openrouter.yml for a third-party cross-reference of all 500+ OpenRouter model prices.
- Conversation state is pruned on a 15-minute `tasks.loop`. `CONVERSATION_TTL`, `MAX_ACTIVE_CONVERSATIONS`, `VIEW_STATE_TTL`, and `DAILY_COST_RETENTION_DAYS` live in `cogs/openrouter/state.py`.
- Every slash command enters via `cog_before_invoke` which binds a fresh request id via `discord_openrouter.logging_setup.bind_request_id`. `on_message` does the same for follow-up messages.
- `LOG_FORMAT=json` switches log output to JSON lines suitable for log aggregators; leave unset for human-readable text mode.
- **Async file I/O**: blocking `open()` and `pathlib` methods (`read_bytes`, `write_bytes`, `unlink`, etc.) inside `async def` freeze the Discord event loop and stall every concurrent slash command. Wrap them with `asyncio.to_thread(...)` so the I/O runs on a worker thread. Enforced by `ruff` (`ASYNC230`/`ASYNC240`).
