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
| `SAFETY_IDENTIFIER_SECRET` | No | HMAC key for the per-user `user` identifier (default: derived from `BOT_TOKEN`); must not equal `OPENROUTER_API_KEY` |

`validate_required_config()` raises `RuntimeError` at startup for missing/blank `BOT_TOKEN` or `OPENROUTER_API_KEY`, and when `SAFETY_IDENTIFIER_SECRET` equals `OPENROUTER_API_KEY` (the message does not include either value).

## Gotchas

- Uses **`py-cord`** (not `discord.py`). The slash-command API differs; don't mix docs between the two.
- `GUILD_IDS` must list at least one guild ID; empty or unset registers the commands **nowhere** — not globally, not per-guild (`_parse_guild_ids("")` returns an empty list, not `None`, and py-cord only registers a command globally when `guild_ids is None`). Set it to a test guild ID during development for instant updates.
- Unlike the other AI bots in this family, discord-openrouter does **not** ship a `pricing.yaml`. Pricing is fetched dynamically from OpenRouter's `/v1/models/user` endpoint (falling back to `/v1/models`) and cached per `OPENROUTER_MODEL_CACHE_TTL_SECONDS`.
- **An account that requires Zero Data Retention** (openrouter.ai/settings/privacy) changes what the key can use: `/models/user` leaves out every model without a ZDR endpoint, and a request for one fails with "0 endpoints out of 1 requested are available matching your guardrail restrictions and data policy ... ZDR violation (account settings)". On 2026-11-06 the key in the local `.env` had it on (the keyed catalog had 416 entries, against 652 on 2026-10-15), which excluded the image (`google/gemini-nano-banana-2.1`), video (`alibaba/happyhorse-1.1`) and STT (`openai/gpt-audio`) defaults; text and TTS defaults were unaffected. `get_model` returns None for such a model, and the image and video pre-flight checks treat None as a pass, so the error only appears when the request is sent. Do not fall back to the keyless `/models` list for this: it would offer models the key cannot call.
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
    ├── cost_line.py                 # One-line cost formatter, byte-identical across the discord-* AI bots (change all five together)
    ├── logging_setup.py             # Structured logging + request-id ContextVar
    ├── stickers.py                  # Sticker-to-text labels for follow-ups, byte-identical across the discord-* AI bots (change all five together)
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
        ├── embed_delivery.py        # Discord embed batching (6000-char/10-embed caps) + plain-text fallback; byte-identical across the five AI bots
        ├── embeds.py
        ├── image.py
        ├── safety.py                # Keyed per-user `user` identifier (HMAC-SHA256)
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
- `tests/conftest.py` sets `safety.SAFETY_IDENTIFIER_KEY` to a fixed test key for every test (autouse), so a `BOT_TOKEN` or `SAFETY_IDENTIFIER_SECRET` in a local `.env` cannot change request-body assertions; expect `user=build_safety_identifier(<id>)`, never the raw ID.
- `tests/fixtures/openrouter_models_mixed.json` holds one real catalog entry per output-modality shape (captured keyless from `/api/v1/models?output_modalities=all`); `tests/conftest.py` exposes it as the session fixtures `mixed_modality_catalog` (raw dicts) and `mixed_modality_models` (parsed `ModelInfo` keyed by id). Use it whenever a test needs a realistic image/speech/transcription/video/embeddings/rerank entry instead of a hand-written `ModelInfo`. The speech entry is `google/gemini-3.8-flash-tts` (captured 2026-10-08, replacing the 2026-08-28 `gemini-3.1-flash-tts-preview` entry) and keeps its `supported_voices` list, since `parse_model_info` reads it. The image+text entry is `google/gemini-nano-banana-2.1` (captured 2026-10-15, replacing the 2026-08-28 `google/gemini-3.1-flash-image` entry). The `_IMAGE_CHAT_RESPONSE` body in `tests/test_openrouter_client.py` is still the `google/gemini-3.1-flash-image` response captured on 2026-09-22; it is a recorded response shape, not the default model.
- New tests and patches should target real owners under `discord_openrouter...`.
- Examples:
  - `discord_openrouter.cogs.openrouter.client.OpenRouterClient`
  - `discord_openrouter.cogs.openrouter.client._request_with_retries`
  - `discord_openrouter.cogs.openrouter.state.prune_runtime_state`
  - `discord_openrouter.cogs.openrouter.views.ButtonView`

## Validation Commands

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pyright src/
uv run pytest -q
```

- The repo pre-commit hook under `.githooks/pre-commit` runs `ruff format` (auto-applied + re-staged), then `ruff check` (blocking), then `pyright` and `pytest --collect-only` as warning-only smoke tests. Resolve tools from `.venv/bin` or `.venv/Scripts` first, then `PATH`.

## Provider Notes

Probe results, dates, token counts and the history behind each bold-titled bullet are in [provider-history.md](provider-history.md) under the same title. Read the matching entry before changing that area; add new evidence there and keep each bullet here to the rule, the constants to keep aligned and the guarding tests.

- **Raw httpx client:** every OpenRouter call is a raw `httpx` request in `client.py`; the bot does not depend on the `openrouter` Python SDK. Chat, image and STT (`create_chat_completion`, `POST /chat/completions`), TTS for `speech` models (`create_audio_speech`, `POST /audio/speech`), video and model listing go through `_request_with_retries` (exponential backoff + jitter on 429/500/502/503/504, honours `Retry-After`, and retries transport errors including read timeouts). Streamed chat-audio TTS (`_stream_audio_completion`) has no retry wrapper and decodes the base64 audio group by group (`_decode_base64_chunks`), because a chunk can end in `=` padding or stop inside a 4-character group. The generation lookup (`get_generation`, `GET /generation`) also skips the retry wrapper (see TTS routing). `create_chat_completion` returns the parsed JSON body unchanged, so `message.annotations`, `message.images`, `usage.server_tool_use_details`, `usage.completion_tokens_details.image_tokens` and `usage.cost` reach the callers; it raises `OpenRouterApiError` with the API's error message on a non-2xx status and on an HTTP 200 body that carries `error` without `choices`. Guards: `tests/test_openrouter_client.py::test_create_chat_completion_posts_payload_with_attribution_headers`, `::test_create_chat_completion_maps_4xx_to_api_error`, `::test_create_chat_completion_raises_on_error_body_with_http_200`.
- **Why the chat path does not use the openrouter SDK:** the SDK converted each response through typed models that drop fields the bot reads (`message.annotations` never arrived, so the Sources embed never appeared), and it was several times slower than a raw request with the same body. Do not reintroduce it for chat. Guards: `::test_create_chat_completion_returns_web_search_annotations_and_tool_counts` and `::test_create_chat_completion_returns_image_tokens_and_images` (`tests/test_openrouter_client.py`).
- **Server-tool counts:** the usage object carries `server_tool_use_details` (`web_search_requests`, `tool_calls_requested`, `tool_calls_executed`). `extract_usage` (`util.py`) reads it and reads `server_tool_use` only when that key is absent; `extract_web_search_requests` takes `web_search_requests`, which sets the "N searches" count and the `search` cost row in the usage embed. Guards: `tests/test_util.py::test_extract_usage_reads_server_tool_use_details` / `::test_extract_usage_prefers_server_tool_use_details_over_server_tool_use`, `tests/test_embeds.py::test_append_usage_embed_counts_searches_from_server_tool_use_details`.
- **Reasoning:** `_build_reasoning_config` builds `{"effort": <value>}` and nothing else, and the raw request sends it as built (`::test_create_chat_completion_sends_exactly_the_effort_reasoning_object` pins the body for every choice). `REASONING_EFFORT_CHOICES` are `minimal`, `low`, `medium`, `high`, `xhigh`, `max` and `none` (7 of 25). The bot does not check an effort against the catalog's per-model `reasoning.supported_efforts`; an unsupported value comes back as the API's error. The removed `reasoning_max_tokens` / `exclude_reasoning` options could now be reinstated, since the raw request sends any field; that would be a separate change.
- Attribution headers: every request built by `_request_headers` sends `Authorization` and `Content-Type` plus `HTTP-Referer` (`OPENROUTER_SITE_URL`), `X-OpenRouter-Title` (`OPENROUTER_APP_NAME`) and `X-OpenRouter-Categories` (`OPENROUTER_APP_CATEGORIES`), each of the last three only when set. Chat requests carry them like every other call. Pinned by `::test_create_chat_completion_posts_payload_with_attribution_headers` (set), `::test_create_chat_completion_omits_categories_when_unset` (absent) and `::test_request_headers_use_documented_openrouter_names`.
- Conversation history: `sanitize_assistant_message` keeps `annotations`, and chat responses now include them, so an assistant turn that used web search or PDF parsing is stored with its annotations and sends them back to OpenRouter on the next turn (the SDK path dropped them from the response). Checked live on 2026-09-22: a follow-up turn whose history held a web-search reply stored by `sanitize_assistant_message` (5 annotations plus `reasoning`) was accepted and answered normally.
- TTS model validation in `speech.py` accepts either `audio` or `speech` in `output_modalities` (`TTS_OUTPUT_MODALITIES`): the live catalog labels dedicated TTS models `speech` and only multimodal audio chat models (gpt-audio, lyria) `audio`, so an `audio`-only check rejected every real TTS model once its metadata resolved (fixed in v1.6.1). Covered by `tests/test_speech.py::TestRunTtsCommand::test_accepts_model_advertising_speech_output` and `::test_rejects_when_model_does_not_advertise_audio_output`.
- **TTS routing:** `speech.py::_uses_speech_endpoint` sends a model whose catalog `output_modalities` contain `speech` and not `audio` to `POST /audio/speech` (`OpenRouterClient.create_audio_speech`); every other model, including an unresolved one, uses the streamed chat-completions path (`create_speech`). `/chat/completions` returns 400 for `speech` models.
  - Request body `{model, input, response_format, voice?, instructions?, user?, session_id?}`; `response_format` is `mp3` or `pcm` (default `pcm`), and `instructions` is sent as its own field, not prepended to the text.
  - Gemini TTS requires an explicit voice, so a blank `voice` option sends `ModelInfo.supported_voices[0]` (catalog `supported_voices`, parsed by `parse_model_info`); a model with no catalog voices gets no `voice` field.
  - Gemini TTS accepts only `pcm`. `create_audio_speech` treats a 400 whose message contains `response_format` as a format rejection (`SpeechFormatRejectedError`), repeats the request once with `pcm`, and remembers the model in `_pcm_only_speech_models` so later requests ask for `pcm` directly.
  - The response body is raw audio with no usage or cost, only an `X-Generation-Id` header. `_speech_audio_file` wraps PCM (`audio/pcm;rate=...;channels=...`, 16-bit little-endian) in a WAV header built from the Content-Type parameters, byte-swaps `audio/L16` (big-endian) first, sends `audio/mpeg`, `audio/wav`, `audio/ogg` / `audio/opus` (`.ogg`), `audio/aac` and `audio/flac` unchanged with their own extension, and treats any other or missing Content-Type as the requested format, so audio that has been paid for is not discarded.
  - Format option on `speech` models: `mp3` requests `mp3`; `wav` / `flac` / `opus` request `pcm` and deliver `.wav`. The embed's `Response Format` shows the delivered format and adds `(requested <choice>)` only when the user picked a different format: py-cord passes the option's `mp3` default when it is left out, so `cog.py::_option_was_given` checks the interaction data and the callback passes `response_format=None` in that case. On the chat path the choice is sent as `audio.format` unchanged, except for `openai/*` models (see Chat-audio TTS path).
  - Cost: `get_generation` waits 1 s and then sends one `GET /generation?id=` (5 s timeout, no retry), up to 4 times (`GENERATION_LOOKUP_INTERVAL_SECONDS` / `_ATTEMPTS`), inside a 6 s `asyncio.timeout` (`GENERATION_LOOKUP_TIMEOUT_SECONDS`); a 404 moves to the next attempt, and any other status, a transport error or the 6 s cap ends the lookup with no record. The cost is `total_cost` (+ `upstream_inference_cost` for BYOK) and the token counts are `tokens_prompt` / `tokens_completion` (`util.extract_generation_usage`). The record usually appears 38–125 s after the request, so the cost line usually starts with `cost unavailable` and the daily total is not changed. Keeping the 4 s lookup is a deliberate decision; showing the cost would need a background lookup that edits the reply later.
  - Guards: `tests/test_speech.py::TestUsesSpeechEndpoint`, `::TestSpeechAudioFile`, `::TestRunTtsCommandSpeechEndpoint`, `::TestRunTtsCommandChatAudio`; `tests/test_openrouter_client.py::test_create_audio_speech_*`, `::test_get_generation_*`, `::test_create_speech_keeps_the_streamed_chat_audio_payload`.
- **Chat-audio TTS path:** `speech.py::_synthesize_with_chat_audio` serves models with `audio` output.
  - Voice: the user's voice, else the model's first catalog voice, else `CHAT_AUDIO_DEFAULT_VOICES` by id prefix (`openai/` → `alloy`), else none. OpenAI audio models require `audio.voice`, and the catalog lists no voices for them.
  - Format: ids starting with a `CHAT_AUDIO_PCM16_MODEL_PREFIXES` entry (`openai/`) always request `pcm16` (OpenAI accepts no other format when streaming), and `_pcm_to_wav` wraps the stream as 24 kHz mono 16-bit. Other chat-audio models (Lyria) keep the chosen format, and their payload is unchanged (`audio: {"format": "mp3"}`, no voice).
  - Read-aloud prompt: ids starting with a `CHAT_AUDIO_READ_ALOUD_MODEL_PREFIXES` entry (`openai/`) get `create_speech(read_aloud=True)`: the system message `READ_ALOUD_SYSTEM_PROMPT` (with `Delivery instructions: <instructions>` appended when given) and a user message `Read this text aloud exactly as written:` followed by the text between `"""` lines. Without it the model answers the text as a conversation turn. Lyria generates music from the prompt, so it keeps the single user message with the instructions prepended.
  - Guard: `tests/test_speech.py::TestRunTtsCommandChatAudio`.
- **Per-user `user` identifier:** `cogs/openrouter/safety.py::build_safety_identifier(user_id)` returns `HMAC-SHA256(key, str(user_id)).hexdigest()` (64 lowercase hex characters, not truncated). The key is `SAFETY_IDENTIFIER_SECRET` when set, otherwise `HMAC-SHA256(BOT_TOKEN, b"safety-identifier-v1")` (`derive_safety_identifier_key`, computed once at import into `SAFETY_IDENTIFIER_KEY`); with neither it returns `None` and the client builders omit `user`. Never use `OPENROUTER_API_KEY` as key material; `validate_required_config` refuses a secret equal to it. The construction and label are shared with discord-openai, discord-grok and discord-claude; change all four together. It is sent as `user` on `POST /chat/completions` for chat (`chat.py::_run_conversation_turn`, which serves `/openrouter chat`, follow-up messages and regenerate), image (`image.py`) and STT (`speech.py::run_stt_command`), on `POST /audio/speech` (`_synthesize_with_speech_endpoint`) and on the streamed chat-audio request (`_synthesize_with_chat_audio`); `create_video_generation` sends neither `user` nor `session_id`. `session_id` carries the slash interaction id (`str(conversation_id)` for chat; `image:` / `tts:` / `stt:<interaction id>` otherwise; cut to 128 characters), never the user ID. 64 characters fit every documented limit (256). Local `COST |` log lines print the raw user ID and never the identifier. Changing the secret, or `BOT_TOKEN` when no secret is set, gives every user a new identifier. Guards: `tests/test_safety_identifier.py` and the `SAFETY_IDENTIFIER_SECRET` cases in `tests/test_config_auth.py`.
- **Provider error text:** for upstream failures OpenRouter's `error.message` is only "Provider returned error" and the provider's response is in `error.metadata.raw`, usually a JSON string; a dict is read directly and a list uses its first element. `client._message_from_error_payload` (used by `_extract_error_message` and the streamed `_extract_error_message_from_bytes`) appends the provider's `error.message`, or the raw text cut to 500 characters. Guards: `tests/test_openrouter_client.py::test_error_message_includes_the_provider_message_from_metadata_raw`, `::test_streamed_error_message_reads_metadata_raw`, `::test_error_message_reads_dict_and_list_metadata_raw`.
- **TTS upload size:** `run_tts_command` compares the file with `attachments.upload_limit_bytes(ctx)` (also used by video): `ctx.interaction.attachment_size_limit` (py-cord 2.7+), then `ctx.guild.filesize_limit`, then `attachments.DEFAULT_UPLOAD_LIMIT_BYTES` (10,485,760). A larger WAV is split by `_split_wav` on whole frames into parts that each fit with their own header, sent as `speech-part-<i>-of-<N>.wav`, one part per follow-up message, with the embeds on the first; a larger file in any other format gets an error embed instead of an upload. Gemini TTS PCM is 48,000 bytes per second, so the 4,096-character maximum can exceed 10 MiB. Guards: `tests/test_speech.py::TestSplitWav` and `::TestRunTtsCommandUploadLimit`.
- **Video job status:** `video.py::_poll_until_complete` stops at every terminal status in OpenRouter's `VideoGenerationResponse.status` enum (`VIDEO_TERMINAL_STATUSES`: `completed`, `failed`, `cancelled`, `expired`). `failed` raises the API's `error` text (or "Video generation failed."); `cancelled` and `expired` raise "The video job ended with status `<status>`; no video was returned." followed by the API's `error` text when there is one, and the command shows it as an error embed. Guards: `tests/test_video.py::TestPollUntilComplete::test_cancelled_or_expired_job_ends_polling_with_the_status` / `::test_cancelled_or_expired_submit_status_ends_without_polling` / `::test_cancelled_job_error_includes_the_api_error_text`, `::TestRunVideoCommand::test_cancelled_or_expired_job_shows_an_error_naming_the_status`.
- **Video download links:** `video.py::_build_download_fields`. The bot downloads each finished video from `unsigned_urls` with the API key (`download_file_bytes` sends `_request_headers()` to `openrouter.ai/` URLs) and compares each file with `attachments.upload_limit_bytes(ctx)`. Files within the limit go out in one direct `ctx.followup.send(embeds=..., files=...)`, not through `send_embed_batches`, whose plain-text retry would re-send `BytesIO` objects that have already been read and so upload 0-byte files (`embed_delivery.py` is byte-identical across the five AI bots and was left unchanged). When a file is over the limit, Discord rejects the upload, or no download succeeded, the reply lists links through `send_embed_batches`:
  - OpenRouter's signed `urls`, followed by a `Link access` field: anyone who can see the message can download the video until `<t:expires_at:f>` (`<t:expires_at:R>`); without a numeric `expires_at`, the field says the links work until OpenRouter deletes its stored copy. Posting signed links shares the video with everyone who can read the channel until expiry (a deliberate decision); the signed URL path also contains the OpenRouter account's user ID and the job ID.
  - Only when `urls` is absent or empty: `unsigned_urls`, with a `Link access` field saying they need an OpenRouter API key in the `Authorization` header.
  - Links are cut at `VIDEO_DOWNLOAD_LINK_LIMIT` (10) and packed into fields of at most `EMBED_FIELD_VALUE_LIMIT` (1,024) characters (`Downloads`, then `Downloads (continued)`); a signed link (about 920 characters as a Markdown link) gets its own field. `_add_download_fields` adds fields to the video embed until the next one would pass `DISCORD_EMBED_TOTAL_LIMIT` (6,000 characters, counted by `count_embed_chars`) and puts the rest in new embeds before the cost embed. A single link over 1,024 characters makes Discord reject the embed, and `send_embed_batches` then sends the reply as plain text.
  - Guards: `tests/test_video.py::TestBuildDownloadFields`, `::TestAddDownloadFields`, `::TestRunVideoCommand::test_rejected_upload_posts_download_links_through_the_real_embed_sender` / `::test_video_above_the_upload_limit_is_linked_without_an_upload` / `::test_video_at_the_upload_limit_is_uploaded` / `::test_ten_long_signed_links_keep_every_embed_and_message_within_discord_limits`.
- **Default image model:** `google/gemini-nano-banana-2.1`, the named replacement for the deprecated `google/gemini-3.1-flash-image` (discord-gemini uses the same default). Catalog: `image` + `text` output, `prompt` $1.50/M, `completion` $7.50/M, `image_output` $30/M tokens. OpenRouter lists a single endpoint for it (Google AI Studio), so there is no other provider to route to when AI Studio rate-limits the model; such requests return HTTP 429 "temporarily rate-limited upstream", and `model:google/gemini-3.1-flash-image` is the workaround. Every request on the day of the switch returned 429, so the image path with this model has not been verified live.
- Default TTS model is `google/gemini-3.8-flash-tts` since 2026-10-08 (catalog: `speech` output, 30 voices, no `expiration_date`). Google shuts down the previous default `google/gemini-3.1-flash-tts-preview` on 2026-11-17; `google/gemini-3.8-flash-lite-tts` is the other replacement in the catalog.
- **Model catalog covers every output modality:** both listing calls send `output_modalities=all` (`MODEL_LIST_PARAMS`), so image-only, `speech`, `transcription`, `video`, `embeddings` and `rerank` entries reach `get_model` (see Gotchas). Every entry parses through `parse_model_info` unchanged (media entries price `prompt` / `completion` as `"0"`; a `context_length` of 0 renders as `ctx unknown`). `MODEL_OUTPUT_MODALITY_CHOICES` offers one `/openrouter models` filter per label (8 of 25), and `audio` also matches `speech` via `OUTPUT_MODALITY_FILTER_ALIASES` (`client.py`). `/openrouter chat` and `/openrouter switch_model` (only for `modality=chat`) run `validate_model_output_modalities` (`chat.py`) to reject non-text-output models before the request or any state write, because a fuzzy `model` query can resolve to a TTS or image-only model. `/openrouter-tools stt` requires `text` output, so dedicated `transcription` models get a friendly error (the STT path uses chat completions, not a transcription endpoint). Guards: `tests/test_util.py::test_parse_model_info_handles_every_live_output_modality`, `tests/test_openrouter_client.py::test_fetch_models_primary_requests_all_modalities` / `::test_list_models_output_filters_cover_every_live_modality`, `tests/test_chat.py::test_run_chat_command_rejects_non_text_output_model_before_request`, `tests/test_openrouter_cog.py::TestModels::test_every_modality_filter_choice_matches_a_live_catalog_entry` / `::TestSwitchModel::test_chat_scope_conversation_rejects_non_text_output_model`, and the live-catalog cases in `tests/test_image.py`, `tests/test_video.py` and `tests/test_speech.py`.
- Pricing is fetched **dynamically** from OpenRouter's `/v1/models/user` endpoint, falling back to `/v1/models` on 404/405/422 (both with `output_modalities=all`) — there is no local `pricing.yaml` in this bot. Metadata is cached per `OPENROUTER_MODEL_CACHE_TTL_SECONDS`. See [genai-prices/openrouter.yml](https://github.com/pydantic/genai-prices/blob/main/prices/providers/openrouter.yml) for a third-party cross-reference of all 500+ OpenRouter model prices.
- Conversation state is pruned on a 15-minute `tasks.loop`. `CONVERSATION_TTL`, `MAX_ACTIVE_CONVERSATIONS`, `VIEW_STATE_TTL`, and `DAILY_COST_RETENTION_DAYS` live in `cogs/openrouter/state.py`.
- Every slash command enters via `cog_before_invoke` which binds a fresh request id via `discord_openrouter.logging_setup.bind_request_id`. `on_message` does the same for follow-up messages.
- `LOG_FORMAT=json` switches log output to JSON lines suitable for log aggregators; leave unset for human-readable text mode.
- **Async file I/O**: blocking `open()` and `pathlib` methods (`read_bytes`, `write_bytes`, `unlink`, etc.) inside `async def` freeze the Discord event loop and stall every concurrent slash command. Wrap them with `asyncio.to_thread(...)` so the I/O runs on a worker thread. Enforced by `ruff` (`ASYNC230`/`ASYNC240`).
- **Stickers are sent as text:** `handle_new_message_in_conversation` (`chat.py`) sends each sticker in `message.stickers` as a `[Sticker: <name>]` line after the message text, built by `text_with_stickers` in `stickers.py` (byte-identical in the five AI bots; change all five together). The sticker image is not sent. A follow-up with nothing to send is ignored and the conversation stays open. Guards: `tests/test_stickers.py` and the sticker follow-up tests in `tests/test_chat.py`.
