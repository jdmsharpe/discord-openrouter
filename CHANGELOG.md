# Changelog

## v1.0.0

### feat
- wrap every `httpx` call in `cogs/openrouter/client.py` with `_request_with_retries`: exponential backoff + jitter on 429/500/502/503/504, honors `Retry-After` header, `MAX_API_ATTEMPTS=5` across video POST, video GET, file download, and models fetch sites (`aea3684`)
- add structured logging in `src/discord_openrouter/logging_setup.py` with `REQUEST_ID` `ContextVar`, `bind_request_id()`, and `configure_logging()`; `cog_before_invoke` and `on_message` bind fresh 8-char hex ids; set `LOG_FORMAT=json` for JSON-lines output (`aea3684`)
- add channel-default model lookup across all modalities (image, video, tts, stt) (`5412933`)
- add modality option to `/switch_model` and expand `/current_model` to cover all modalities (`51d4020`)
- rewrite `build_current_model_embed` to show defaults for every modality (`1bb7235`)
- migrate `channel_model_defaults` to 3-tuple `(channel, user, modality)` key (`6d99301`)
- add `Modality` type aliases and `MODALITY_CHOICES` (`de169b3`)

### fix
- annotate `global_defaults` as `dict[str, str | None]` for type correctness (`44dc2fa`)

### chore
- bump project version to `1.0.0` (first stable release)
- update default image model to `google/gemini-3-pro-image-preview` and default video model to `bytedance/seedance-2.0` in `config/auth.py` (`aea3684`)
- canonical pre-commit hook at `.githooks/pre-commit`: `ruff format` (auto-applied + re-staged), `ruff check` (blocking), `pyright` (warning-only), `pytest --collect-only` (warning-only smoke); byte-identical across all 6 discord-* repos (`aea3684`)
- retain dynamic pricing via OpenRouter `/v1/models` endpoint (no local `pricing.yaml`); cross-reference available at https://github.com/pydantic/genai-prices/blob/main/prices/providers/openrouter.yml (`aea3684`)

### test
- add 12 new tests (8 for `logging_setup`, plus retry-loop unit tests in `test_openrouter_client.py`); suite total is 83 passing (`aea3684`)

### docs
- create `.claude/CLAUDE.md` (previously missing in this family); documents architecture, environment variables, package layout, and provider notes (`aea3684`)
- refresh `.env.example` and `README.md` with new env vars including `LOG_FORMAT` (`aea3684`)
- add multi-modality model switching design spec and implementation plan (`8f815de`, `6d74d4d`)

### compare
- [`0.1.0...v1.0.0`](https://github.com/jdmsharpe/discord-openrouter/commits/v1.0.0) *(no prior git tag exists; use the commits link)*
