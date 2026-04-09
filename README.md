# Discord OpenRouter Bot

![Hits](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fgithub.com%2Fjdmsharpe%2Fdiscord-openrouter%2F&label=discord-openrouter&icon=github&color=%23198754&message=&style=flat&tz=UTC)
[![Version](https://img.shields.io/github/v/tag/jdmsharpe/discord-openrouter?sort=semver&label=version)](https://github.com/jdmsharpe/discord-openrouter/tags)
[![License](https://img.shields.io/github/license/jdmsharpe/discord-openrouter?label=license)](./LICENSE)
[![CI](https://github.com/jdmsharpe/discord-openrouter/actions/workflows/main.yml/badge.svg)](https://github.com/jdmsharpe/discord-openrouter/actions/workflows/main.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)

## Overview

A Discord bot built on Pycord 2.0 that integrates OpenRouter through the official OpenRouter Python SDK. It provides stateful, multi-turn chat in Discord, supports multimodal inputs plus image and audio generation, preserves reasoning blocks for supported models, and lets you switch models on the fly without losing the conversation.

## Features

- **Multi-turn Conversations:** Start a chat with `/openrouter chat`, then continue talking in the same channel.
- **Dynamic Model Switching:** Switch the active conversation model or save a per-channel default with `/openrouter switch_model`.
- **Model Discovery:** Query available models from your OpenRouter account with `/openrouter models`.
- **Multimodal Input:** `/openrouter chat` supports Discord image, PDF, audio, video, and general file inputs using OpenRouter's normalized multimodal API.
- **Image Generation:** `/openrouter image` generates or remixes images with models that advertise image output.
- **Text-to-Speech:** `/openrouter tts` generates spoken audio with models that advertise audio output.
- **Speech-to-Text:** `/openrouter stt` transcribes uploaded audio with models that advertise audio input and text output.
- **Reasoning Preservation:** Stores `reasoning_details` in assistant messages so supported reasoning models can continue their chain of thought across turns.
- **Interactive Controls:** Regenerate, pause/resume, or stop a conversation using buttons under each response.
- **Usage Tracking:** Shows prompt/completion token usage and reported cost when pricing metadata is available.

## Commands

### `/openrouter chat`
Start a conversation with an OpenRouter model.

- **Core Inputs:** `prompt`, optional `persona`, optional `model`, and optional `attachment`.
- **Tuning Options:** `temperature`, `top_p`, `max_tokens`, and `reasoning_effort`.
- **Default Resolution:** If no model is supplied, the bot uses the channel default if one has been saved, otherwise `OPENROUTER_DEFAULT_TEXT_MODEL`.
- **Attachment Types:** the slash command accepts one optional attachment. After the conversation starts, normal follow-up messages in the channel can include multiple attachments.
- **Normalization:** images are sent as `image_url`, PDFs as `file`, audio as `input_audio`, video as `video_url`, and other files as `file` payloads using OpenRouter's multimodal content types.
- **Prompt Ordering:** text is sent before attachments, which matches OpenRouter's recommendation for multimodal prompts.

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
- **PDF parser controls:** OpenRouter supports explicit PDF parser plugins and engine selection, but this bot does not currently expose `plugins.file-parser` configuration in commands or environment variables.
- **Video support:** `/openrouter chat` can forward video attachments through OpenRouter's chat completions API, but the project does not currently expose a dedicated video analysis command or OpenRouter's asynchronous video generation API.
- **Generated images:** `/openrouter image` downloads returned image payloads and re-uploads them as Discord files. In regular chat, image outputs are summarized in embeds rather than re-attached as files.

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
| `OPENROUTER_DEFAULT_TTS_MODEL` | No | Default model for `/openrouter tts` (default: `openai/gpt-audio`) |
| `OPENROUTER_DEFAULT_STT_MODEL` | No | Default model for `/openrouter stt` (default: `openai/gpt-audio`) |
| `OPENROUTER_SITE_URL` | No | Optional `HTTP-Referer` sent to OpenRouter |
| `OPENROUTER_APP_NAME` | No | Optional app name sent as the OpenRouter title header |
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
5. Use `/openrouter models query:minimax` when you want help finding the exact model slug.
6. Use `/openrouter image prompt:...` to generate or remix images with OpenRouter image-capable models.
7. Use `/openrouter tts input:...` to generate spoken audio with OpenRouter audio-capable models.
8. Use `/openrouter stt attachment:...` to transcribe uploaded audio with OpenRouter audio-input models.

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
