# Discord OpenRouter Bot

![Hits](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fgithub.com%2Fjdmsharpe%2Fdiscord-openrouter%2F&label=discord-openrouter&icon=github&color=%23198754&message=&style=flat&tz=UTC)
[![Version](https://img.shields.io/github/v/tag/jdmsharpe/discord-openrouter?sort=semver&label=version)](https://github.com/jdmsharpe/discord-openrouter/tags)
[![License](https://img.shields.io/github/license/jdmsharpe/discord-openrouter?label=license)](./LICENSE)
[![CI](https://github.com/jdmsharpe/discord-openrouter/actions/workflows/main.yml/badge.svg)](https://github.com/jdmsharpe/discord-openrouter/actions/workflows/main.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)

## Overview

A Discord bot built on Pycord 2.0 that integrates OpenRouter through the official OpenRouter Python SDK, providing a unified interface for stateful multi-turn chat, dynamic model switching, and extensive multimodal inputs. All functionality is cleanly grouped under the `/openrouter` namespace, supporting image, video, and audio generation, advanced reasoning preservation, and interactive conversation tools without ever losing your thread.

## Features

- **Multi-turn Conversations:** Persistent conversation history with interactive button controls (regenerate, pause/resume, stop) and explicit context compression to help long conversations fit smaller context windows.
- **Multiple OpenRouter Models:** Seamlessly discover, query, and switch models on the fly using OpenRouter's expansive catalog. Save per-channel defaults or rely on global fallbacks.
- **Multimodal Input:** Supports text, images, PDFs, audio, video, and general file inputs using OpenRouter's normalized multimodal API. Features dedicated PDF parsing controls (`cloudflare-ai`, `mistral-ocr`, `native`).
- **Advanced Tool Calling:** Built-in support for OpenRouter's server tools (`openrouter:web_search`, `openrouter:datetime`). Turn tools on or off mid-conversation via an interactive dropdown.
- **Reasoning Configuration:** Customizable reasoning effort levels and token budgets for supported models. Automatically preserves `reasoning_details` in assistant messages so models can continue their chain-of-thought across turns.
- **Rich Embeds & Usage Tracking:** Responses include cache reads/writes, prompt/completion tokens, and exact reported costs (or an `est.`-prefixed local fallback). Web search citations are surfaced cleanly via a Sources embed.
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
- Includes tuning options like `temperature`, `top_p`, `max_tokens`, `exclude_reasoning`, and `pdf_engine`.

### `/openrouter image`

Generate a new image or remix an uploaded one.

- **Options:** Customizable `aspect_ratio` (includes standard and extended options like `1:8`) and `image_size`.
- Attach an existing Discord image to remix or edit it.

### `/openrouter video`

Generate a video from a text prompt, with an optional reference image.

- **Options:** Customizable `aspect_ratio`, `resolution`, `size` (exact dimensions like `1280x720`), `duration`, `generate_audio`, and `seed`.
- Polled asynchronously server-side until complete, then downloaded and attached to the channel.

### `/openrouter tts`

Convert text into speech audio.

- **Options:** Customizable `voice`, `instructions`, and `response_format` (`mp3`, `wav`, `flac`, `opus`).
- Text input is capped at `4096` characters per request.

### `/openrouter stt`

Generate text from an uploaded audio file.

- **Options:** Optional `instructions` to guide the transcription.
- Supports standard formats like `mp3`, `mp4`, `wav`, `webm`, `flac`, etc. Limit: 20 MiB.

### Utility Commands

- **`/openrouter switch_model`:** Switch the active thread's model, save a per-channel default, or both (`scope=conversation`, `channel`, `both`).
- **`/openrouter models`:** Search the OpenRouter catalog natively with optional `input_modality` and `output_modality` filters.
- **`/openrouter current_model`:** View the active conversation model, saved channel default, and global fallback.
- **`/openrouter check_permissions`:** Check if the bot has the necessary permissions in the current channel.

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
| `OPENROUTER_DEFAULT_TEXT_MODEL` | No | Global fallback text model (Default: `minimax/minimax-m2.7`) |
| `OPENROUTER_DEFAULT_IMAGE_MODEL` | No | Default model for `/openrouter image` (Default: `google/gemini-3.1-flash-image-preview`) |
| `OPENROUTER_DEFAULT_VIDEO_MODEL` | No | Default model for `/openrouter video` (Default: `google/veo-3.1`) |
| `OPENROUTER_DEFAULT_TTS_MODEL` | No | Default model for `/openrouter tts` (Default: `openai/gpt-audio`) |
| `OPENROUTER_DEFAULT_STT_MODEL` | No | Default model for `/openrouter stt` (Default: `openai/gpt-audio`) |
| `OPENROUTER_DEFAULT_PDF_ENGINE` | No | Default engine for PDF attachments: `cloudflare-ai`, `mistral-ocr`, `native` |
| `OPENROUTER_SITE_URL` | No | Optional `HTTP-Referer` sent to OpenRouter |
| `OPENROUTER_APP_NAME` | No | Optional app name sent as `X-OpenRouter-Title` header |
| `OPENROUTER_APP_CATEGORIES` | No | Optional categories sent as `X-OpenRouter-Categories` |
| `OPENROUTER_MODEL_CACHE_TTL_SECONDS` | No | Seconds to cache model metadata (Default: `300`) |
| `SHOW_COST_EMBEDS` | No | Show usage/cost embeds (Default: `true`) |

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
- **Prompt Caching:** `/openrouter chat model:anthropic/claude-sonnet-4.5 prompt_cache_ttl:1h prompt:Use this rubric...`
- **Video Generation:** `/openrouter video prompt:A neon train racing through a rainy cyberpunk city at night`
- **Video from Image:** `/openrouter video prompt:Animate this character walking forward attachment:<image>`

### Troubleshooting & Notes

- **Attachment Limits:** The bot currently rejects Discord attachments larger than `20 MiB`.
- **Modality Support:** Although the bot handles normalized payloads, your selected model must actually support the requested input/output types. Use `/openrouter models` to check.
- **Costs & Usage:** If OpenRouter returns `usage.cost`, the exact amount is displayed. If missing, the bot estimates it based on local pricing data and marks it with an `est.` prefix. Cache reads (`cached_tokens`) and writes (`cache_write_tokens`) are also displayed when reported.
- **PDF History:** Assistant annotations are preserved in conversation history, meaning you can ask follow-up questions about the same PDF across multiple turns without re-uploading the document.
- **Hidden Reasoning:** If a model supports hidden reasoning, you can pass `exclude_reasoning:true` in `/openrouter chat` to keep the model's thinking internal and out of the Discord response.

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
