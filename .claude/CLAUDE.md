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
- `tests/fixtures/openrouter_models_mixed.json` holds one real catalog entry per output-modality shape (captured keyless from `/api/v1/models?output_modalities=all`); `tests/conftest.py` exposes it as the session fixtures `mixed_modality_catalog` (raw dicts) and `mixed_modality_models` (parsed `ModelInfo` keyed by id). Use it whenever a test needs a realistic image/speech/transcription/video/embeddings/rerank entry instead of a hand-written `ModelInfo`. The speech entry is `google/gemini-3.8-flash-tts` (captured 2026-10-08, replacing the 2026-08-28 `gemini-3.1-flash-tts-preview` entry) and keeps its `supported_voices` list, since `parse_model_info` reads it.
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

- Every OpenRouter call is a raw `httpx` request in `client.py`; the bot does not depend on the `openrouter` Python SDK. Chat, image and STT (`create_chat_completion`, `POST /chat/completions`), TTS for `speech` models (`create_audio_speech`, `POST /audio/speech`), video, and model listing go through `_request_with_retries` (exponential backoff + jitter on 429/500/502/503/504, respects `Retry-After` header; transport errors, read timeouts included, are retried as well). TTS for `audio` output chat models streams over raw `httpx` in `_stream_audio_completion` with no retry wrapper, and decodes the streamed base64 audio group by group (`_decode_base64_chunks`), because a chunk can end in `=` padding or stop inside a 4-character group. The generation lookup (`get_generation`, `GET /generation`) also skips the retry wrapper: one plain request per attempt and a 6 s cap on the whole lookup (see the TTS cost note). `create_chat_completion` returns the parsed JSON body unchanged, so `message.annotations`, `message.images`, `usage.server_tool_use_details`, `usage.completion_tokens_details.image_tokens` and `usage.cost` reach the callers; it raises `OpenRouterApiError` with the API's error message on a non-2xx status and on an HTTP 200 body that carries `error` without `choices`. Pinned by `tests/test_openrouter_client.py::test_create_chat_completion_posts_payload_with_attribution_headers` (exact URL, headers and JSON body), `::test_create_chat_completion_maps_4xx_to_api_error` and `::test_create_chat_completion_raises_on_error_body_with_http_200`.
- Why the chat path no longer uses the `openrouter` SDK (the last locked version was openrouter 1.2.19): the SDK converted each response through typed models that drop fields the bot reads. Checked live on 2026-09-22 with the bot's web-search request (`deepseek/deepseek-v4-flash`, `tools=[{"type": "openrouter:web_search"}]`): a raw POST returned `choices[0].message.annotations` (10 `url_citation` entries) and the SDK result returned none, so `extract_url_citations` got nothing and the Sources embed never appeared. The SDK was also slow: the same `google/gemini-3.1-flash-image` request (`modalities=["image", "text"]`) finished in about 10 s raw (`usage.completion_tokens_details` = `{"reasoning_tokens": 0, "image_tokens": 1120, "audio_tokens": 0}`, `cost` 0.0672035) and was still waiting for response headers after 150 s through the SDK; the web-search request took 75 s through the SDK and 10 s raw. The SDK's request body was the raw body plus `"stream": false`, and a raw request with `"stream": false` also completed in 9 s. Rechecked on 2026-09-22 through the new `OpenRouterClient.create_chat_completion`: the web-search request took 35 s and returned 25 annotations (15 unique citation URLs) with `server_tool_use_details` = 5 web searches; the image request took 10 s and returned one PNG with `image_tokens` 1120 and `cost` 0.0672045. Pinned by `::test_create_chat_completion_returns_web_search_annotations_and_tool_counts` and `::test_create_chat_completion_returns_image_tokens_and_images`.
- Server-tool counts: the live usage object (2026-09-22) carries `server_tool_use_details` = `{"web_search_requests": 2, "tool_calls_requested": 2, "tool_calls_executed": 2}` and has no `server_tool_use` key. `extract_usage` (`util.py`) reads `server_tool_use_details` and reads `server_tool_use` only when that key is absent; `extract_web_search_requests` takes `web_search_requests` from the result, which sets the "N searches" count and the `search` cost row in the usage embed. Pinned by `tests/test_util.py::test_extract_usage_reads_server_tool_use_details` / `::test_extract_usage_prefers_server_tool_use_details_over_server_tool_use` and `tests/test_embeds.py::test_append_usage_embed_counts_searches_from_server_tool_use_details`.
- Reasoning: `_build_reasoning_config` builds `{"effort": <value>}` and nothing else, and the raw request sends it as built; `::test_create_chat_completion_sends_exactly_the_effort_reasoning_object` pins that body for every `REASONING_EFFORT_CHOICES` value. The choices are `minimal`, `low`, `medium`, `high`, `xhigh`, `max` and `none` (7 of 25); `max` was added on 2026-10-08 because the API's `reasoning.effort` enum includes it and the catalog's per-model `reasoning.supported_efforts` lists it for models such as `anthropic/claude-haiku-5.5`. The bot does not check an effort against `supported_efforts`; an unsupported value comes back as the API's error. The `reasoning_max_tokens`/`exclude_reasoning` slash options were removed in v1.6.0 because the SDK's `ChatRequestReasoning` model serialised only `effort` and `summary`, so those settings never reached the wire. The raw request has no such limit, so reinstating them is now possible as a separate change.
- Attribution headers: every request built by `_request_headers` sends `Authorization` and `Content-Type` plus `HTTP-Referer` (`OPENROUTER_SITE_URL`), `X-OpenRouter-Title` (`OPENROUTER_APP_NAME`) and `X-OpenRouter-Categories` (`OPENROUTER_APP_CATEGORIES`), each of the last three only when set. Chat requests carry them like every other call. Pinned by `::test_create_chat_completion_posts_payload_with_attribution_headers` (set), `::test_create_chat_completion_omits_categories_when_unset` (absent) and `::test_request_headers_use_documented_openrouter_names`.
- Conversation history: `sanitize_assistant_message` keeps `annotations`, and chat responses now include them, so an assistant turn that used web search or PDF parsing is stored with its annotations and sends them back to OpenRouter on the next turn (the SDK path dropped them from the response). Checked live on 2026-09-22: a follow-up turn whose history held a web-search reply stored by `sanitize_assistant_message` (5 annotations plus `reasoning`) was accepted and answered normally.
- TTS model validation in `speech.py` accepts either `audio` or `speech` in `output_modalities` (`TTS_OUTPUT_MODALITIES`): the live catalog labels dedicated TTS models `speech` and only multimodal audio chat models (gpt-audio, lyria) `audio`, so an `audio`-only check rejected every real TTS model once its metadata resolved (fixed in v1.6.1). Covered by `tests/test_speech.py::TestRunTtsCommand::test_accepts_model_advertising_speech_output` and `::test_rejects_when_model_does_not_advertise_audio_output`.
- TTS routing (`speech.py::_uses_speech_endpoint`): a model whose catalog `output_modalities` contain `speech` and not `audio` goes to `POST /audio/speech` (`OpenRouterClient.create_audio_speech`); every other model, including an unresolved one, stays on the streamed chat-completions path (`create_speech`). Checked live on 2026-10-08: `/chat/completions` answers HTTP 400 "<model> is a text-to-speech model and cannot be used with the chat/completions endpoint. Use the /api/v1/audio/speech endpoint instead." for `speech` models (the previous default `google/gemini-3.1-flash-tts-preview` and `hexgrad/kokoro-82m` were tried; the catalog lists 32 `speech` models), so `/openrouter-tools tts` failed with its own default model until this routing was added. `/audio/speech` facts from the same day:
  - Request body `{model, input, response_format, voice?, instructions?, user?, session_id?}` (OpenRouter's `SpeechRequest`; `response_format` enum is `mp3`/`pcm`, default `pcm`). `instructions` is sent as its own field, not prepended to the text as on the chat path.
  - Gemini TTS without `voice` -> 400 "An explicit voice is required for this TTS provider."; so a blank `voice` option sends `ModelInfo.supported_voices[0]` (catalog `supported_voices`, parsed by `parse_model_info`; `Zephyr` for both Gemini TTS models). A model with no catalog voices gets no `voice` field.
  - Gemini TTS with `mp3` -> 400 'Gemini TTS only supports response_format="pcm". Got "mp3".'. `create_audio_speech` treats a 400 whose message contains `response_format` as a format rejection (`SpeechFormatRejectedError`), repeats the request once with `pcm`, and remembers the model in `_pcm_only_speech_models` so later requests ask for `pcm` directly. `hexgrad/kokoro-82m` accepted `mp3` (`audio/mpeg`).
  - The body is the raw audio: `Content-Type: audio/pcm;rate=24000;channels=1` (16-bit little-endian) for `pcm`, `audio/mpeg` for `mp3`; there is no usage or cost in the response, only an `X-Generation-Id: gen-tts-...` header. `_speech_audio_file` wraps PCM in a WAV header with the rate and channel count from the Content-Type (stdlib `wave`); `audio/L16` (big-endian) is byte-swapped first. `audio/mpeg`, `audio/wav`, `audio/ogg`/`audio/opus` (`.ogg`), `audio/aac` and `audio/flac` are sent unchanged with their own extension. Any other or missing Content-Type is logged and treated as the format the bot requested (`pcm` -> WAV, `mp3` -> `.mp3`), so audio that has been paid for is not discarded. Checked end to end through `run_tts_command` with the real client: the default model returned a 4.28 s, 24 kHz mono 16-bit WAV for 57 characters.
  - Format option on `speech` models: `mp3` requests `mp3`; `wav`/`flac`/`opus` request `pcm` and deliver `.wav`. The embed's `Response Format` shows the delivered format and adds `(requested <choice>)` only when the user picked a format that differs: py-cord passes the option's `mp3` default when it is left out, so `cog.py::_option_was_given` checks the interaction data and the callback passes `response_format=None` in that case (the option keeps its `mp3` default, so `tests/test_option_defaults.py` still checks the description). On the chat path the choice is sent as `audio.format` unchanged, except for `openai/*` models, which always get `pcm16` (see the chat-audio note below).
  - Cost: `get_generation` waits 1 s and then sends one `GET /generation?id=` (5 s timeout, no retry), up to 4 times (`GENERATION_LOOKUP_INTERVAL_SECONDS`/`_ATTEMPTS`), inside an `asyncio.timeout` of 6 s (`GENERATION_LOOKUP_TIMEOUT_SECONDS`); a 404 moves to the next attempt, and any other status (429 and 5xx included), a transport error or the 6 s cap ends the lookup with no record. It builds the cost from `total_cost` (+ `upstream_inference_cost` for BYOK) and the token counts from `tokens_prompt`/`tokens_completion` (`util.extract_generation_usage`). If the record is not there the cost line starts with `cost unavailable` and the daily total is not changed. Measured on 2026-10-08: the record returned 404 for 38 s (gemini-3.8-flash-tts) in one run and for about 125 s (gemini-3.8-flash-tts and kokoro) in another, so the record usually appears 38–125 s after the request, the line usually reads `cost unavailable`, and the lookup adds about 4 s to every `speech` reply. John chose on 2026-10-08 to keep the 4 s lookup as it is; showing the cost would need a later edit of the reply (a background lookup for about 2 minutes) instead of a longer wait.
  - Pinned by `tests/test_speech.py::TestUsesSpeechEndpoint`, `::TestSpeechAudioFile`, `::TestRunTtsCommandSpeechEndpoint`, `::TestRunTtsCommandChatAudio`, `tests/test_openrouter_client.py::test_create_audio_speech_*`, `::test_get_generation_*` and `::test_create_speech_keeps_the_streamed_chat_audio_payload`.
- Chat-audio TTS path (`speech.py::_synthesize_with_chat_audio`, models with `audio` output). Checked live on 2026-10-08 with the earlier payload (no voice, `mp3`, `stream: true`): `google/lyria-3-clip-preview` returned an MP3 and `usage.cost` 0.04, but `openai/gpt-audio-mini` failed: no `audio.voice` -> provider 400 "Missing required parameter: 'audio.voice'."; `voice: "alloy"` + `mp3` -> "'audio.format' does not support 'mp3' when stream=true. Supported values are: 'pcm16'."; `alloy` + `pcm16` succeeded. The catalog lists no `supported_voices` for the gpt-audio models. Fixed the same day (John's decision):
  - Voice: the user's voice, else the model's first catalog voice, else `CHAT_AUDIO_DEFAULT_VOICES` by id prefix (`openai/` -> `alloy`), else none.
  - Format: models whose id starts with a `CHAT_AUDIO_PCM16_MODEL_PREFIXES` entry (`openai/`) are always asked for `pcm16`, and the streamed audio (which carries no rate or channel information) is wrapped by `_pcm_to_wav` as 24 kHz mono 16-bit, the format OpenAI documents for `pcm16`. Other chat-audio models (Lyria) keep the chosen format; the payload the bot builds for Lyria is unchanged (`audio: {"format": "mp3"}`, no voice), so `pcm16` is not forced on them.
  - Read-aloud prompt: models whose id starts with a `CHAT_AUDIO_READ_ALOUD_MODEL_PREFIXES` entry (`openai/`) get `create_speech(read_aloud=True)`: a system message `READ_ALOUD_SYSTEM_PROMPT` ("Read the user's text aloud exactly as written. Do not answer it, add to it or comment on it.", with `Delivery instructions: <instructions>` appended when the user gave style instructions) and a user message `Read this text aloud exactly as written:` followed by the text between `"""` lines. Lyria generates music from the prompt, so it keeps the single user message with the instructions prepended; the catalog's only other `audio` output models are the two Lyria entries. Checked live on 2026-10-08 with `openai/gpt-audio-mini` and the text "Hello from the OpenRouter bot. This is a short audio test.": the bare text as the user message was answered as a conversation turn ("Hi there! I can hear you loud and clear..."), and so was the system message with the bare text as the user message ("Hello! Loud and clear. I'm picking up your voice..."). The system message plus a labelled or quoted user message, or one user message holding both, each returned a transcript identical to the text. End to end through `run_tts_command` with the quoted form: transcript identical to the text, a 4.1 s 24 kHz mono 16-bit WAV, voice `alloy`, `usage.cost` 0.000289.
  - Pinned by `tests/test_speech.py::TestRunTtsCommandChatAudio`.
- Provider error text: for upstream failures OpenRouter's `error.message` is only "Provider returned error" and the provider's response is in `error.metadata.raw`, usually a JSON string; a dict is read directly and a list (JSON or not) uses its first element. `client._message_from_error_payload` (used by `_extract_error_message` and the streamed `_extract_error_message_from_bytes`) appends the provider's `error.message` (or the raw text, cut to 500 characters), for example "Provider returned error: Missing required parameter: 'audio.voice'." (checked live on 2026-10-08). Pinned by `tests/test_openrouter_client.py::test_error_message_includes_the_provider_message_from_metadata_raw`, `::test_streamed_error_message_reads_metadata_raw` and `::test_error_message_reads_dict_and_list_metadata_raw`.
- TTS upload size: `run_tts_command` compares the file with the limit Discord sends on the interaction (`ctx.interaction.attachment_size_limit`, py-cord 2.7+), then `ctx.guild.filesize_limit`, then `DEFAULT_UPLOAD_LIMIT_BYTES` (10,485,760). A larger WAV is split by `_split_wav` on whole frames into parts that each fit with their own header, sent as `speech-part-<i>-of-<N>.wav`, one part per follow-up message, with the embeds on the first. A larger file in any other format gets an error embed instead of an upload. Gemini TTS PCM is 48,000 bytes per second, so the 4,096-character maximum can exceed 10 MiB. Pinned by `tests/test_speech.py::TestSplitWav` and `::TestRunTtsCommandUploadLimit`.
- Default TTS model is `google/gemini-3.8-flash-tts` since 2026-10-08 (catalog: `speech` output, 30 voices, no `expiration_date`). Google shuts down the previous default `google/gemini-3.1-flash-tts-preview` on 2026-11-17; `google/gemini-3.8-flash-lite-tts` is the other replacement in the catalog.
- The model catalog lists **every** output modality since v1.7.0 (image-only, speech, transcription, video, embeddings, rerank entries are new; image+text models such as the default image model were already in the text-default list): both listing calls send `output_modalities=all` (`MODEL_LIST_PARAMS`; previously only the `/v1/models` fallback did, so a keyed bot saw text-output models only). Checked keyless on 2026-08-28: 540 entries with the parameter vs. 396 without; the extra `architecture.output_modalities` labels are `image` (image-only), `speech` (dedicated TTS), `transcription`, `video`, `embeddings`, and `rerank`, and every entry parses through `parse_model_info` unchanged (media entries price `prompt`/`completion` as `"0"` with per-image keys such as `image_token`/`image_output` that the parser ignores; `context_length` is `0` for many, which the list embed renders as `ctx unknown`). Consequences: `MODEL_OUTPUT_MODALITY_CHOICES` offers one `/openrouter models` filter per label (8 of the 25 allowed); `audio` also matches `speech` via `OUTPUT_MODALITY_FILTER_ALIASES` in `client.py`; `/openrouter chat` and `/openrouter switch_model` run `validate_model_output_modalities` (`chat.py`, un-prefixed because `cog.py` imports it) to reject non-text-output entries before the request or any state write, since a fuzzy `model` query can now resolve to a TTS or image-only model — `switch_model` guards only `modality=chat`, since the other modalities are validated by the command that drives them; the image/video/TTS pre-flight checks now run against real entries for the default models. `/openrouter-tools stt` still requires `text` output, so dedicated `transcription` models get the existing friendly error — the STT path drives chat completions, not a transcription endpoint. Pinned by `tests/test_util.py::test_parse_model_info_handles_every_live_output_modality`, `tests/test_openrouter_client.py::test_fetch_models_primary_requests_all_modalities` / `::test_list_models_output_filters_cover_every_live_modality`, `tests/test_chat.py::test_run_chat_command_rejects_non_text_output_model_before_request`, `tests/test_openrouter_cog.py::TestModels::test_every_modality_filter_choice_matches_a_live_catalog_entry`, `tests/test_openrouter_cog.py::TestSwitchModel::test_chat_scope_conversation_rejects_non_text_output_model`, and the live-catalog cases in `tests/test_image.py`, `tests/test_video.py`, and `tests/test_speech.py`.
- Pricing is fetched **dynamically** from OpenRouter's `/v1/models/user` endpoint, falling back to `/v1/models` on 404/405/422 (both with `output_modalities=all`) — there is no local `pricing.yaml` in this bot. Metadata is cached per `OPENROUTER_MODEL_CACHE_TTL_SECONDS`. See https://github.com/pydantic/genai-prices/blob/main/prices/providers/openrouter.yml for a third-party cross-reference of all 500+ OpenRouter model prices.
- Conversation state is pruned on a 15-minute `tasks.loop`. `CONVERSATION_TTL`, `MAX_ACTIVE_CONVERSATIONS`, `VIEW_STATE_TTL`, and `DAILY_COST_RETENTION_DAYS` live in `cogs/openrouter/state.py`.
- Every slash command enters via `cog_before_invoke` which binds a fresh request id via `discord_openrouter.logging_setup.bind_request_id`. `on_message` does the same for follow-up messages.
- `LOG_FORMAT=json` switches log output to JSON lines suitable for log aggregators; leave unset for human-readable text mode.
- **Async file I/O**: blocking `open()` and `pathlib` methods (`read_bytes`, `write_bytes`, `unlink`, etc.) inside `async def` freeze the Discord event loop and stall every concurrent slash command. Wrap them with `asyncio.to_thread(...)` so the I/O runs on a worker thread. Enforced by `ruff` (`ASYNC230`/`ASYNC240`).
