# Discord OpenRouter Bot

![Hits](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fgithub.com%2Fjdmsharpe%2Fdiscord-openrouter%2F&label=discord-openrouter&icon=github&color=%23198754&message=&style=flat&tz=UTC)
[![Version](https://img.shields.io/github/v/tag/jdmsharpe/discord-openrouter?sort=semver&label=version)](https://github.com/jdmsharpe/discord-openrouter/tags)
[![License](https://img.shields.io/github/license/jdmsharpe/discord-openrouter?label=license)](./LICENSE)
[![CI](https://github.com/jdmsharpe/discord-openrouter/actions/workflows/main.yml/badge.svg)](https://github.com/jdmsharpe/discord-openrouter/actions/workflows/main.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)

## Overview

A Discord bot built on Pycord 2.0 that integrates OpenRouter through the official OpenRouter Python SDK. It provides stateful, multi-turn chat in Discord, supports multimodal inputs plus image, video, and audio generation, preserves reasoning blocks for supported models, and lets you switch models on the fly without losing the conversation.

## Features

- **Multi-turn Conversations:** Start a chat with `/openrouter chat`, then continue talking in the same channel.
- **Dynamic Model Switching:** Switch the active conversation model or save a per-channel default with `/openrouter switch_model`.
- **Model Discovery:** Query available models from your OpenRouter account with `/openrouter models`.
- **Multimodal Input:** `/openrouter chat` supports Discord image, PDF, audio, video, and general file inputs using OpenRouter's normalized multimodal API.
- **Image Generation:** `/openrouter image` generates or remixes images with models that advertise image output.
- **Video Generation:** `/openrouter video` generates videos through OpenRouter's asynchronous video API, with optional reference images.
- **Text-to-Speech:** `/openrouter tts` generates spoken audio with models that advertise audio output.
- **Speech-to-Text:** `/openrouter stt` transcribes uploaded audio with models that advertise audio input and text output.
- **Web Search:** `/openrouter chat` can enable OpenRouter's `openrouter:web_search` server tool for current-information questions, with source links surfaced in Discord.
- **Datetime Tooling:** `/openrouter chat` can enable OpenRouter's `openrouter:datetime` server tool for time-sensitive prompts.
- **Context Compression:** `/openrouter chat` can enable OpenRouter's context-compression plugin to help long conversations fit smaller context windows.
- **Reasoning Controls:** Tune reasoning with effort levels, optional reasoning token budgets, and hidden-reasoning mode for supported models.
- **Reasoning Preservation:** Stores `reasoning_details` in assistant messages so supported reasoning models can continue their chain of thought across turns.
- **Interactive Controls:** Regenerate, pause/resume, stop, or toggle conversation tools from the persistent controls under each response.
- **Usage Tracking:** Shows prompt/completion token usage, cache reads and writes, exact reported cost when OpenRouter includes it, and an `est.`-prefixed local fallback when OpenRouter omits `usage.cost`.

## Commands

### `/openrouter chat`
Start a conversation with an OpenRouter model.

- **Core Inputs:** `prompt`, optional `persona`, optional `model`, optional `attachment`, and optional `pdf_engine`.
- **Tuning Options:** `temperature`, `top_p`, `max_tokens`, `context_compression`, `prompt_cache_ttl`, `web_search`, `datetime`, `reasoning_effort`, `reasoning_max_tokens`, and `exclude_reasoning`.
- **Default Resolution:** If no model is supplied, the bot uses the channel default if one has been saved, otherwise `OPENROUTER_DEFAULT_TEXT_MODEL`.
- **Attachment Types:** the slash command accepts one optional attachment. After the conversation starts, normal follow-up messages in the channel can include multiple attachments.
- **Normalization:** images are sent as `image_url`, PDFs as `file`, audio as `input_audio`, video as `video_url`, and other files as `file` payloads using OpenRouter's multimodal content types.
- **Prompt Ordering:** text is sent before attachments, which matches OpenRouter's recommendation for multimodal prompts.
- **PDF Controls:** `pdf_engine` can be set to `cloudflare-ai`, `mistral-ocr`, or `native`. If `OPENROUTER_DEFAULT_PDF_ENGINE` is configured, that value is used for PDF attachments unless you override it in the command.
- **Context Compression:** set `context_compression:true` to request OpenRouter's `context-compression` plugin for the chat turn and subsequent conversation replies. Set `context_compression:false` to explicitly disable OpenRouter's default compression on smaller-context models.
- **Prompt Caching:** `prompt_cache_ttl` requests explicit top-level Anthropic prompt caching for the conversation with `5m` or `1h`. Other supported OpenRouter providers may still cache automatically even when this option is unset.
- **Web Search:** set `web_search:true` when you want OpenRouter to browse for current information. The bot uses OpenRouter's `openrouter:web_search` server tool and explicitly disables the deprecated `web` plugin in request-level overrides to avoid account-default surprises when possible. When the provider returns `url_citation` annotations, the bot adds a Sources embed with linked results.
- **Datetime:** set `datetime:true` when you want the model to have on-demand access to the current date and time through OpenRouter's `openrouter:datetime` server tool.
- **Conversation Tool Dropdown:** after the conversation starts, you can toggle supported tools for later turns from the `Tools` dropdown under the bot response, matching the ongoing-tool behavior used in the sibling Discord bots.
- **Reasoning Notes:** use either `reasoning_effort` or `reasoning_max_tokens` for a request, not both. `exclude_reasoning` keeps model thinking internal when the provider supports hidden reasoning.

### `/openrouter switch_model`
Switch the active conversation model, save a per-channel default, or do both.

- `scope=conversation` updates only the running conversation. If none is active, it falls back to updating your saved channel default.
- `scope=channel` updates only the saved default for you in the current channel.
- `scope=both` updates both the running conversation and the saved channel default.

### `/openrouter image`
Generate a new image or remix an uploaded one.

- **Core Inputs:** `prompt`, optional `model`, optional `attachment`.
- **Image Controls:** optional `aspect_ratio` and `image_size`.
- **Ratio Notes:** includes standard ratios plus extended options like `1:4`, `4:1`, `1:8`, and `8:1`, but support is model-dependent.
- **Editing Mode:** attach an existing Discord image to remix or edit it with the selected model.
- **Model Requirement:** the selected model should advertise `image` in its OpenRouter `output_modalities`.

### `/openrouter video`
Generate a video from a text prompt, with an optional reference image.

- **Core Inputs:** `prompt`, optional `model`, optional `attachment`.
- **Video Controls:** optional `aspect_ratio`, `resolution`, `size`, `duration`, `generate_audio`, and `seed`.
- **Reference Image Mode:** attach an existing Discord image to guide the video generation. This uses OpenRouter's `input_references` support on the `/videos` API.
- **Sizing Notes:** `size` is an exact dimension such as `1280x720`. Use it instead of `resolution` and `aspect_ratio`, not alongside them.
- **Async Workflow:** video generation is polled server-side until OpenRouter reports `completed`, then the bot downloads and re-uploads the generated video when possible.
- **Model Requirement:** the selected model should advertise `video` in its OpenRouter `output_modalities`. If you use a reference image, the model should also advertise `image` input.

### `/openrouter tts`
Convert text into speech audio.

- **Core Inputs:** `input`, optional `model`.
- **Audio Controls:** optional `voice`, optional `instructions`, optional `response_format`.
- **Limits:** text input is capped at `4096` characters per request.
- **Formats:** supported output formats are `mp3`, `wav`, `flac`, and `opus`.
- **Voice Notes:** `voice` is a free-form override because supported voices differ by model/provider. If omitted, the model default is used.
- **Model Requirement:** the selected model should advertise `audio` in its OpenRouter `output_modalities`.

### `/openrouter stt`
Generate text from an uploaded audio file.

- **Core Inputs:** required `attachment`, optional `model`.
- **Transcription Controls:** optional `instructions`.
- **Attachment Notes:** common supported formats include `mp3`, `mp4`, `m4a`, `wav`, `webm`, `ogg`, `flac`, `aiff`, `aac`, `pcm16`, and `pcm24`.
- **Size Limit:** uploaded audio is limited to `20 MiB`.
- **Model Requirement:** the selected model should advertise `audio` in its `input_modalities` and `text` in its `output_modalities`.

### `/openrouter current_model`
Show the active conversation model, the saved channel default, and the global fallback model.

### `/openrouter models`
Search models visible to your OpenRouter API key.

- Queries the user-filtered model catalog when possible.
- Falls back to the public catalog if needed.
- Supports optional `input_modality` and `output_modality` filters.
- Useful for finding exact model IDs like `minimax/minimax-m2.7`.

### `/openrouter check_permissions`
Check whether the bot can read the current channel and message history.

## Multimodal Notes

- **Attachment limit:** this bot currently rejects Discord attachments larger than `20 MiB`.
- **Model compatibility:** OpenRouter accepts the normalized payloads, but the selected model still needs to support the requested input or output modalities. Use `/openrouter models` with `input_modality` and `output_modality` filters when in doubt.
- **PDF follow-ups:** assistant `annotations` are preserved in conversation history, so follow-up questions about the same PDF can continue using OpenRouter's parsed file context across turns.
- **PDF parser controls:** PDF attachments can use the `pdf_engine` slash-command option or `OPENROUTER_DEFAULT_PDF_ENGINE` to request `cloudflare-ai`, `mistral-ocr`, or `native`.
- **Deprecated PDF alias:** if you still use the deprecated OpenRouter engine name `pdf-text`, this bot normalizes it to `cloudflare-ai`.
- **Automatic caching:** many OpenRouter providers already enable prompt caching automatically. OpenRouter also handles provider sticky routing for cached conversations. The bot surfaces cache reads and cache writes in the usage embed when OpenRouter reports them.
- **Prompt caching visibility:** when OpenRouter reports cache activity, the usage embed includes cache reads (`cached_tokens`) and cache writes (`cache_write_tokens`).
- **Cost display:** when OpenRouter returns `usage.cost`, the bot shows that amount directly. When `usage.cost` is missing, the bot falls back to local pricing metadata and prefixes the per-request amount with `est.` so estimated totals are visually distinct from API-reported totals.
- **Web search visibility:** when OpenRouter reports server-tool web usage, the usage embed includes the search count and the response can include a Sources embed from standardized `url_citation` annotations.
- **Reasoning visibility:** reasoning-capable models may return spoilered thinking embeds. Set `exclude_reasoning` on `/openrouter chat` if you want reasoning used internally but omitted from the Discord response when supported.
- **Video support:** `/openrouter chat` can analyze uploaded video attachments through OpenRouter's chat completions API, and `/openrouter video` uses OpenRouter's asynchronous `/videos` API for video generation.
- **Generated images:** `/openrouter image` downloads returned image payloads and re-uploads them as Discord files. In regular chat, generated image outputs are also re-attached as Discord files when the model returns image payloads.

## Setup & Installation

### Prerequisites

- Python 3.10+
- Discord Bot Token
- OpenRouter API Key

### Installation

1. Clone the repository and navigate to the project directory.
2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install the package and its runtime dependencies:

   ```bash
   python -m pip install .
   ```

4. Copy the environment example file:

   ```bash
   cp .env.example .env
   ```

### Contributor Setup

Install development tooling for tests, linting, and type checking:

```bash
python -m pip install -e ".[dev]"
```

### Configuration (`.env`)

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | **Yes** | Your Discord bot token |
| `GUILD_IDS` | **Yes** | Comma-separated Discord server IDs |
| `OPENROUTER_API_KEY` | **Yes** | Your OpenRouter API key |
| `OPENROUTER_DEFAULT_TEXT_MODEL` | No | Global fallback model when neither a conversation nor channel default is set (default: `minimax/minimax-m2.7`) |
| `OPENROUTER_DEFAULT_IMAGE_MODEL` | No | Default model for `/openrouter image` (default: `google/gemini-3.1-flash-image-preview`) |
| `OPENROUTER_DEFAULT_VIDEO_MODEL` | No | Default model for `/openrouter video` (default: `google/veo-3.1`) |
| `OPENROUTER_DEFAULT_TTS_MODEL` | No | Default model for `/openrouter tts` (default: `openai/gpt-audio`) |
| `OPENROUTER_DEFAULT_STT_MODEL` | No | Default model for `/openrouter stt` (default: `openai/gpt-audio`) |
| `OPENROUTER_DEFAULT_PDF_ENGINE` | No | Default PDF parsing engine for `/openrouter chat` PDF attachments. Supported values: `cloudflare-ai`, `mistral-ocr`, `native`. Deprecated `pdf-text` is normalized to `cloudflare-ai`. |
| `OPENROUTER_SITE_URL` | No | Optional `HTTP-Referer` sent to OpenRouter |
| `OPENROUTER_APP_NAME` | No | Optional app name sent as the OpenRouter `X-OpenRouter-Title` header |
| `OPENROUTER_APP_CATEGORIES` | No | Optional comma-separated OpenRouter marketplace categories sent as `X-OpenRouter-Categories` on the bot's direct HTTP requests |
| `OPENROUTER_MODEL_CACHE_TTL_SECONDS` | No | Seconds to cache model metadata before refreshing (default: `300`) |
| `SHOW_COST_EMBEDS` | No | Show usage/cost embeds (default: `true`) |

### Running the Bot

**Locally:**

```bash
python src/bot.py
```

*(Note: `src/bot.py` is a thin launcher that delegates to `discord_openrouter.bot.main`)*

**With Docker:**

```bash
docker compose up -d --build
```

**Using as a Cog:**

```python
from discord_openrouter import OpenRouterCog

bot.add_cog(OpenRouterCog(bot=bot))
```

## Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application and add a bot in the "Bot" section.
3. Enable **Server Members Intent** and **Message Content Intent** under Privileged Gateway Intents.
4. Copy the bot token and add it to your `.env` file.
5. Go to OAuth2 > URL Generator.
6. Select scopes: `bot`, `applications.commands`.
7. Select permissions: `Send Messages`, `Read Message History`, `Use Slash Commands`, `Embed Links`, `Attach Files`.
8. Use the generated URL to invite the bot to your server.

## Usage & Demo

1. Start a conversation with `/openrouter chat`.
2. Keep talking in the same channel to continue that conversation.
3. Use `/openrouter switch_model model:minimax/minimax-m2.7 scope:conversation` to move the active thread to a different model.
4. Use the buttons under each answer to regenerate, pause/resume, or stop the conversation.
5. Use the `Tools` dropdown under a response to turn `web_search` and `datetime` on or off for the rest of that conversation.
6. Use `/openrouter models query:minimax` when you want help finding the exact model slug.
7. Use `/openrouter image prompt:...` to generate or remix images with OpenRouter image-capable models.
8. Use `/openrouter video prompt:...` to generate videos with OpenRouter video-capable models.
9. Use `/openrouter tts input:...` to generate spoken audio with OpenRouter audio-capable models.
10. Use `/openrouter stt attachment:...` to transcribe uploaded audio with OpenRouter audio-input models.
11. Use `/openrouter chat web_search:true prompt:...` when you want current information with linked sources.
12. Use `/openrouter chat datetime:true prompt:...` when the model should be able to check "right now" context.
13. Use `/openrouter chat context_compression:true prompt:...` for long-running threads that may hit smaller context windows.

### Multimodal Examples

- Summarize a PDF with OCR enabled: `/openrouter chat prompt:Summarize this contract attachment:<pdf> pdf_engine:mistral-ocr`
- Ask about an uploaded image: `/openrouter chat prompt:Describe the scene and read any visible text attachment:<image>`
- Analyze a video clip with a video-capable model: `/openrouter chat model:<video-model> prompt:What happens in this clip? attachment:<video>`
- Generate a short video from text: `/openrouter video prompt:A neon train racing through a rainy cyberpunk city at night`
- Generate a guided video from an image: `/openrouter video prompt:Animate this character walking forward attachment:<image>`
- Start a PDF conversation with the default parser from `.env`, then ask follow-up questions in the same channel without re-uploading the document.
- Search the web for up-to-date info: `/openrouter chat web_search:true prompt:What changed in OpenRouter this week?`
- Start a cached Claude conversation: `/openrouter chat model:anthropic/claude-sonnet-4.5 prompt_cache_ttl:1h prompt:Use this rubric for all later answers...`

## Development

### Testing

Tests use `pytest`. The current suite is mocked and covers config parsing, model lookup, state cleanup, and view callbacks.

```bash
# Install developer tooling if you have not already
python -m pip install -e ".[dev]"

# Run tests locally
python -m pytest -q

# Run tests in Docker
docker build --build-arg PYTHON_VERSION=3.13 -f Dockerfile.test -t discord-openrouter-test .
docker run --rm discord-openrouter-test python -m pytest -q

# Run linting and type checks in Docker
docker run --rm discord-openrouter-test sh -lc 'ruff check src tests && ruff format --check src tests && pyright'
```

### Linting & Type Checking

```bash
ruff check src tests
ruff format --check src tests
pyright
```

*Run `git config core.hooksPath .githooks` after cloning to enable the pre-commit hook.*

## License

MIT License - see [LICENSE](LICENSE) for details.
