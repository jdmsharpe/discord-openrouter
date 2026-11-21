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

A Discord bot for the OpenRouter API, built on Pycord 2.0. It provides chat with any OpenRouter model, image and video generation, text-to-speech and speech-to-text through slash commands.

## Features

- **Chat:** Multi-turn conversations with any OpenRouter model that outputs text. Messages you send in the channel continue the conversation, and context compression helps long conversations fit smaller context windows.
- **Model catalog:** Search the catalog, switch a conversation's model, and save per-channel defaults. The catalog lists image, speech, transcription, video, embedding and rerank models alongside text models.
- **Attachments:** Text, images, PDFs, audio, video and other files. PDFs can use the `cloudflare-ai`, `mistral-ocr` or `native` parser.
- **Reasoning:** Reasoning effort from `none` to `max` on models that support it. Reasoning details are kept across turns.
- **Built-in tools:** OpenRouter's `web_search` and `datetime` server tools.
- **Citations:** Web search citations appear in a Sources embed; pages the model only consulted are left out.
- **Media generation:**
  - **Images:** Generation and editing with any model that outputs images.
  - **Video:** Text-to-video and image-to-video through OpenRouter's `/videos` API.
  - **Text-to-speech:** Dedicated TTS models and audio chat models.
  - **Speech-to-text:** Transcription with audio-input models.
- **Cost line:** Each reply ends with a line such as `$0.0031 · 2.3k in (512 cached) / 441 out (66 thinking) · 1 search · $0.09 today`: the cost OpenRouter reports (prefixed `est.` when the bot estimates it from catalog prices), input tokens (cached in parentheses), output tokens (thinking in parentheses), web searches and your total for the day. BYOK requests include what your provider key is billed. Set `SHOW_COST_EMBEDS=false` to hide it.

## Commands

### `/openrouter chat`

Start a conversation with an OpenRouter model.

- **`prompt` (required):** Your first message.
- **`persona`:** System prompt (default: "You are a helpful assistant.").
- **`model`:** Model slug (default: the channel's saved model, then `OPENROUTER_DEFAULT_TEXT_MODEL`). A model without text output is refused before the request, with a pointer to the matching media or tools command.
- **`pdf_engine`:** PDF parser: `cloudflare-ai`, `mistral-ocr` or `native`.
- **`attachment`:** An image, PDF, audio, video or other file.
- **`temperature` / `top_p` / `max_tokens`:** Sampling and output limit.
- **`context_compression`:** Override OpenRouter's context compression.
- **`prompt_cache_ttl`:** Anthropic prompt caching TTL (`5m` or `1h`).
- **`reasoning_effort`:** Reasoning effort on models that support it.
- **`web_search` / `datetime`:** Enable OpenRouter server tools (default: off).

### `/openrouter switch_model`

Switch the conversation's model, save it as the channel default, or both (`scope`: `conversation`, `channel` or `both`). `modality` picks which command's model to change (default: `chat`).

### `/openrouter models`

Search the OpenRouter catalog, with optional `input_modality` and `output_modality` filters.

### `/openrouter current_model`

Show the conversation's model, the channel default and the global fallback.

### `/openrouter-media image`

Generate an image from a prompt, or edit an attached image.

- **`model`:** Image model slug (default: `OPENROUTER_DEFAULT_IMAGE_MODEL`).
- **`aspect_ratio` / `image_size`:** Standard and extended ratios such as `1:8`, and an image size hint.
- **`attachment`:** An image to edit or remix.

### `/openrouter-media video`

Generate a video from a prompt, with an optional reference image.

- **`model`:** Video model slug (default: `OPENROUTER_DEFAULT_VIDEO_MODEL`).
- **`aspect_ratio` / `resolution` / `size` / `duration`:** `size` takes exact dimensions such as `1280x720`.
- **`generate_audio` / `seed`:** Request audio, and set a seed for repeatable results.
- **`attachment`:** A reference image.
- The video is attached when generation finishes. A video too large to attach is linked instead; anyone who can see the message can download it until the link expires.

### `/openrouter-tools tts`

Convert text to speech (up to 4096 characters).

- **`model`:** TTS model slug (default: `OPENROUTER_DEFAULT_TTS_MODEL`). Dedicated TTS models use OpenRouter's `/audio/speech` endpoint; audio chat models (`openai/gpt-audio`, `openai/gpt-audio-mini`, Google Lyria) use chat completions.
- **`voice`:** Voice name. Blank uses the model's first listed voice (`alloy` on OpenAI audio models).
- **`instructions`:** Delivery instructions.
- **`response_format`:** `mp3` (default), `wav`, `flac` or `opus`. Dedicated TTS models return MP3 or WAV and OpenAI audio models always WAV; the reply shows the voice and format you got.
- A WAV file over the upload limit is split into playable parts; an MP3 file over the limit returns an error.

### `/openrouter-tools stt`

Transcribe an audio file (up to 20 MiB; mp3, mp4, m4a, wav, ogg, flac and other common types).

- **`model`:** STT model slug (default: `OPENROUTER_DEFAULT_STT_MODEL`).
- **`instructions`:** Transcription instructions (default: an accurate verbatim transcript).

### `/openrouter check_permissions`

Check if the bot has the necessary permissions in the current channel.

## Setup & Installation

### Prerequisites

- Python 3.11+
- Discord Bot Token
- OpenRouter API Key

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/jdmsharpe/discord-openrouter.git
   cd discord-openrouter
   ```

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
| `OPENROUTER_DEFAULT_IMAGE_MODEL` | No | Default model for `/openrouter-media image` (Default: `google/gemini-nano-banana-2.1`) |
| `OPENROUTER_DEFAULT_VIDEO_MODEL` | No | Default model for `/openrouter-media video` (Default: `alibaba/happyhorse-1.1`) |
| `OPENROUTER_DEFAULT_TTS_MODEL` | No | Default model for `/openrouter-tools tts` (Default: `google/gemini-3.8-flash-tts`) |
| `OPENROUTER_DEFAULT_STT_MODEL` | No | Default model for `/openrouter-tools stt` (Default: `openai/gpt-audio`) |
| `OPENROUTER_DEFAULT_PDF_ENGINE` | No | Default engine for PDF attachments: `cloudflare-ai`, `mistral-ocr`, `native` |
| `OPENROUTER_SITE_URL` | No | Optional `HTTP-Referer` sent to OpenRouter |
| `OPENROUTER_APP_NAME` | No | Optional app name sent as `X-OpenRouter-Title` header |
| `OPENROUTER_APP_CATEGORIES` | No | Up to 2 recognized categories (for example `general-chat,image-gen`) sent as `X-OpenRouter-Categories`; others are dropped |
| `OPENROUTER_MODEL_CACHE_TTL_SECONDS` | No | Seconds to cache the model catalog fetched from `/models/user?output_modalities=all` (Default: `300`) |
| `SHOW_COST_EMBEDS` | No | Show usage/cost embeds (Default: `true`) |
| `LOG_FORMAT` | No | `text` (default) for human-readable logs, or `json` for structured JSON-lines output with per-request IDs |
| `SAFETY_IDENTIFIER_SECRET` | No | HMAC key for the per-user `user` identifier sent to OpenRouter (Default: a key derived from `BOT_TOKEN`) |

#### User Identifier

Chat (including follow-ups and regenerations), image, text-to-speech and speech-to-text requests carry a per-user identifier in OpenRouter's `user` field so OpenRouter can attribute a policy violation to one Discord user instead of the whole bot.

- The identifier is an HMAC-SHA256 of the Discord user ID, keyed with `SAFETY_IDENTIFIER_SECRET` or, when that is unset, a key derived from `BOT_TOKEN`. OpenRouter never receives the user ID or the key, and the bot refuses to start when `SAFETY_IDENTIFIER_SECRET` equals `OPENROUTER_API_KEY`.
- Changing the key (or the bot token, when no secret is set) gives every user a new identifier. To keep identifiers stable across token changes, set the secret to a long random string, for example `python -c "import secrets; print(secrets.token_hex(32))"`.
- discord-claude, discord-grok, discord-openai and discord-openrouter compute it the same way, so a user gets the same identifier from each bot that shares the key.
- Video requests carry none.

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

1. Run `/openrouter chat` to start a conversation.
2. Send messages in the same channel to continue it.
3. Use the controls under each reply:
   - 🔄 Regenerate the last response
   - ⏯️ Pause or resume the conversation
   - ⏹️ End the conversation
   - **Tools** menu: turn tools on or off mid-conversation

### Prompt Examples

- **Web Search:** `/openrouter chat web_search:true prompt:What changed in OpenRouter this week?`
- **PDF Analysis:** `/openrouter chat prompt:Summarize this contract attachment:<pdf> pdf_engine:mistral-ocr`
- **Video Analysis:** `/openrouter chat model:<video-model> prompt:What happens in this clip? attachment:<video>`
- **Prompt Caching:** `/openrouter chat model:anthropic/claude-sonnet-4.6 prompt_cache_ttl:1h prompt:Use this rubric...`
- **Video Generation:** `/openrouter-media video prompt:A neon train racing through a rainy cyberpunk city at night`
- **Video from Image:** `/openrouter-media video prompt:Animate this character walking forward attachment:<image>`

### Troubleshooting

- **Attachment limit:** Discord attachments over 20 MiB are rejected.
- **Model support:** Each command checks the model against the catalog before the request: `/openrouter chat` needs text output, `/openrouter-media image` image output, `/openrouter-media video` video output, `/openrouter-tools tts` audio or speech output, and `/openrouter-tools stt` audio input and text output. Use `/openrouter models` to find a matching model.
- **PDF follow-ups:** Conversation history keeps PDF annotations, so you can ask follow-up questions about a PDF without uploading it again.
- **Speech costs:** OpenRouter usually publishes a speech request's cost about a minute after the reply, so the cost line often reads `cost unavailable` and the request is not added to the daily total.

## Development

### Testing

Tests use `pytest` with `pytest-asyncio` (`asyncio_mode = "auto"`). All tests are mocked (no real API calls).

```bash
# Install developer tooling if you have not already
python -m pip install -e ".[dev]"

# Run tests locally
python -m pytest -q

# Run tests in Docker
docker build --build-arg PYTHON_VERSION=3.11 -f Dockerfile.test -t discord-openrouter-test:3.11 .
docker run --rm discord-openrouter-test:3.11 python -m pytest -q

# Run linting and type checks in Docker
docker run --rm discord-openrouter-test:3.11 sh -lc 'ruff check src tests && ruff format --check src tests && pyright'
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
