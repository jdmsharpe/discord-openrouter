<h1 align="center">Discord OpenRouter Bot</h1>

<div align="center">

![Hits](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fgithub.com%2Fjdmsharpe%2Fdiscord-openrouter%2F&label=discord-openrouter&icon=github&color=%23198754&message=&style=flat&tz=UTC)
[![Version](https://img.shields.io/github/v/tag/jdmsharpe/discord-openrouter?sort=semver&label=version)](https://github.com/jdmsharpe/discord-openrouter/tags)
[![License](https://img.shields.io/github/license/jdmsharpe/discord-openrouter?label=license)](./LICENSE)
[![CI](https://github.com/jdmsharpe/discord-openrouter/actions/workflows/main.yml/badge.svg)](https://github.com/jdmsharpe/discord-openrouter/actions/workflows/main.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/jsgreen152/discord-openrouter?logo=docker&logoColor=white)](https://hub.docker.com/r/jsgreen152/discord-openrouter)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Codecov](https://codecov.io/gh/jdmsharpe/discord-openrouter/branch/main/graph/badge.svg)](https://app.codecov.io/gh/jdmsharpe/discord-openrouter)

</div>

## Overview

A Discord bot built on Pycord 2.0 that integrates OpenRouter's API, providing a unified interface for stateful multi-turn chat, dynamic model switching, and extensive multimodal inputs. It supports image, video, and audio generation, advanced reasoning preservation, and interactive conversation tools without ever losing your thread. Every request goes to OpenRouter's REST API directly over `httpx`; the bot does not depend on the OpenRouter Python SDK.

## Features

- **Multi-turn Conversations:** Persistent conversation history with interactive button controls (regenerate, pause/resume, stop) and explicit context compression to help long conversations fit smaller context windows.
- **Multiple OpenRouter Models:** Seamlessly discover, query, and switch models on the fly using OpenRouter's expansive catalog. The catalog is fetched with `output_modalities=all`, so image, speech, transcription, video, embeddings, and rerank models are listed alongside the text models. Save per-channel defaults or rely on global fallbacks.
- **Multimodal Input:** Supports text, images, PDFs, audio, video, and general file inputs using OpenRouter's normalized multimodal API. Features dedicated PDF parsing controls (`cloudflare-ai`, `mistral-ocr`, `native`).
- **Advanced Tool Calling:** Built-in support for OpenRouter's server tools (`openrouter:web_search`, `openrouter:datetime`). Turn tools on or off mid-conversation via an interactive dropdown.
- **Reasoning Configuration:** Customizable reasoning effort levels for supported models. Automatically preserves `reasoning_details` in assistant messages so models can continue their chain-of-thought across turns.
- **Rich Embeds & Usage Tracking:** Each response ends with a one-line cost summary: the request cost (as reported, or an `est.`-prefixed local estimate), input and output tokens with their cached and thinking counts, web searches, and your total for the day. Web search citations are surfaced cleanly via a Sources embed.
- **Media Generation:**
  - **Images:** High-quality image generation and remixing/editing using models that advertise image output.
  - **Video:** Asynchronous text-to-video and image-to-video generation via OpenRouter's `/videos` API.
  - **Text-to-Speech:** Convert text into spoken audio files.
  - **Speech-to-Text:** Transcribe uploaded audio files using audio-input models.

## Commands

### `/openrouter chat`

Start a conversation with an OpenRouter model.

- Features tool enablement mid-conversation via a dropdown.
- Supports Anthropic-style prompt caching explicitly via `prompt_cache_ttl` (`5m` or `1h`).
- Includes tuning options like `temperature`, `top_p`, `max_tokens`, `reasoning_effort`, and `pdf_engine`.
- Rejects models that don't advertise text output (image-only, speech, transcription, video, embeddings, rerank) before any request is sent, pointing you to the matching `/openrouter-media` or `/openrouter-tools` command where one exists.

### `/openrouter-media image`

Generate a new image or remix an uploaded one.

- **Options:** Customizable `aspect_ratio` (includes standard and extended options like `1:8`) and `image_size`.
- Attach an existing Discord image to remix or edit it.

### `/openrouter-media video`

Generate a video from a text prompt, with an optional reference image.

- **Options:** Customizable `aspect_ratio`, `resolution`, `size` (exact dimensions like `1280x720`), `duration`, `generate_audio`, and `seed`.
- Polled asynchronously server-side until complete, then downloaded and attached to the channel.

### `/openrouter-tools tts`

Convert text into speech audio.

- **Options:** Customizable `voice`, `instructions`, and `response_format` (`mp3`, `wav`, `flac`, `opus`).
- Text input is capped at `4096` characters per request.

### `/openrouter-tools stt`

Generate text from an uploaded audio file.

- **Options:** Optional `instructions` to guide the transcription.
- Supports standard formats like `mp3`, `mp4`, `wav`, `webm`, `flac`, etc. Limit: 20 MiB.

### Utility Commands

- **`/openrouter switch_model`:** Switch the active thread's model, save a per-channel default, or both (`scope=conversation`, `channel`, `both`). For the default `chat` modality it rejects models that don't advertise text output before changing anything, with the same error and command hints as `/openrouter chat`.
- **`/openrouter models`:** Search the OpenRouter catalog natively with optional `input_modality` (`text`, `image`, `audio`, `video`, `file`) and `output_modality` (`text`, `image`, `audio`, `speech`, `video`, `embeddings`, `transcription`, `rerank`) filters. `audio` also matches the `speech` label OpenRouter gives dedicated TTS models; `speech` narrows to just those.
- **`/openrouter current_model`:** View the active conversation model, saved channel default, and global fallback.
- **`/openrouter check_permissions`:** Check if the bot has the necessary permissions in the current channel.

## Setup & Installation

### Prerequisites

- Python 3.11+
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
| `OPENROUTER_DEFAULT_TEXT_MODEL` | No | Global fallback text model (Default: `deepseek/deepseek-v4-flash`) |
| `OPENROUTER_DEFAULT_IMAGE_MODEL` | No | Default model for `/openrouter-media image` (Default: `google/gemini-3.1-flash-image`) |
| `OPENROUTER_DEFAULT_VIDEO_MODEL` | No | Default model for `/openrouter-media video` (Default: `alibaba/happyhorse-1.1`) |
| `OPENROUTER_DEFAULT_TTS_MODEL` | No | Default model for `/openrouter-tools tts` (Default: `google/gemini-3.1-flash-tts-preview`) |
| `OPENROUTER_DEFAULT_STT_MODEL` | No | Default model for `/openrouter-tools stt` (Default: `openai/gpt-audio`) |
| `OPENROUTER_DEFAULT_PDF_ENGINE` | No | Default engine for PDF attachments: `cloudflare-ai`, `mistral-ocr`, `native` |
| `OPENROUTER_SITE_URL` | No | Optional `HTTP-Referer` sent to OpenRouter |
| `OPENROUTER_APP_NAME` | No | Optional app name sent as `X-OpenRouter-Title` header |
| `OPENROUTER_APP_CATEGORIES` | No | Optional categories sent as `X-OpenRouter-Categories` |
| `OPENROUTER_MODEL_CACHE_TTL_SECONDS` | No | Seconds to cache the model catalog fetched from `/models/user?output_modalities=all` (Default: `300`) |
| `SHOW_COST_EMBEDS` | No | Show usage/cost embeds (Default: `true`) |
| `LOG_FORMAT` | No | `text` (default) for human-readable logs, or `json` for structured JSON-lines output with per-request IDs |

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
To compose this repo into a larger bot, import the namespaced package:

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

## Usage

### Prompt Examples

Try these multimodal and tool-assisted commands:

- **Web Search:** `/openrouter chat web_search:true prompt:What changed in OpenRouter this week?`
- **PDF Analysis:** `/openrouter chat prompt:Summarize this contract attachment:<pdf> pdf_engine:mistral-ocr`
- **Video Analysis:** `/openrouter chat model:<video-model> prompt:What happens in this clip? attachment:<video>`
- **Prompt Caching:** `/openrouter chat model:anthropic/claude-sonnet-4.6 prompt_cache_ttl:1h prompt:Use this rubric...`
- **Video Generation:** `/openrouter-media video prompt:A neon train racing through a rainy cyberpunk city at night`
- **Video from Image:** `/openrouter-media video prompt:Animate this character walking forward attachment:<image>`

### Troubleshooting & Notes

- **Attachment Limits:** The bot currently rejects Discord attachments larger than `20 MiB`.
- **Modality Support:** Although the bot handles normalized payloads, your selected model must actually support the requested input/output types. Use `/openrouter models` to check. Every command validates the resolved model against the catalog up front: `/openrouter chat` requires text output, `/openrouter-media image` image output, `/openrouter-media video` video output, `/openrouter-tools tts` `audio` or `speech` output, and `/openrouter-tools stt` audio input plus text output (dedicated `transcription` models are rejected, since STT runs through chat completions).
- **Costs & Usage:** Each response ends with one line such as `$0.0031 · 2.3k in (512 cached) / 441 out (66 thinking) · 1 search · $0.09 today`. The request cost is OpenRouter's `usage.cost`, shown to four decimals; the daily total is shown in whole cents. For BYOK requests (`usage.is_byok`), `usage.cost` is only OpenRouter's fee, so the bot adds `usage.cost_details.upstream_inference_cost` (the amount your provider key is billed) to both the request cost and the daily total. If `usage.cost` is missing, the bot estimates the cost from the catalog pricing and prefixes it with `est.`. `in` counts every input token including cache reads (`cached`), and `out` counts every output token including reasoning (`thinking`). Web search turns show how many searches OpenRouter ran (`usage.server_tool_use_details.web_search_requests`). Image, video, and speech commands describe the item instead of token counts, for example `$0.0400 · 1 image · 16:9 · $0.12 today`.
- **PDF History:** Assistant annotations are preserved in conversation history, meaning you can ask follow-up questions about the same PDF across multiple turns without re-uploading the document.

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
