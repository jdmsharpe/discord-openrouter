# Multi-Modality Model Switching Design

**Date:** 2026-04-11
**Status:** Approved

## Problem

`/switch_model` and `/current_model` only operate on the chat modality. The bot supports image, video, TTS, and STT commands — each with an optional `model` parameter — but there is no way to set a persistent per-channel default for those modalities. Users must specify a model slug on every invocation or accept the global env-var default.

## Goal

Extend `/switch_model` and `/current_model` to support all modalities (chat, image, video, tts, stt), so users can save a per-channel model default for each modality independently.

## Approach

Extend the existing `channel_model_defaults` dict key from a 2-tuple `(channel_id, user_id)` to a 3-tuple `(channel_id, user_id, modality)`. All other structure stays the same — one dict, one lookup pattern, no new cog fields.

## Data Model

### `state.py`

Add two type aliases:

```python
Modality: TypeAlias = str  # "chat" | "image" | "video" | "tts" | "stt"
ModalityModelStore: TypeAlias = dict[tuple[int, int, Modality], str]
```

### `cog.py`

```python
# Before
self.channel_model_defaults: dict[tuple[int, int], str] = {}

# After
self.channel_model_defaults: ModalityModelStore = {}
```

All existing reads/writes to `channel_model_defaults` append `"chat"` as the third key element. No other files touch this dict directly.

## Command Changes

### `command_options.py`

Add a new choices list:

```python
MODALITY_CHOICES = [
    OptionChoice(name="Chat",  value="chat"),
    OptionChoice(name="Image", value="image"),
    OptionChoice(name="Video", value="video"),
    OptionChoice(name="TTS",   value="tts"),
    OptionChoice(name="STT",   value="stt"),
]
```

### `/switch_model`

Add one new option:

```python
@option("modality", description="Which modality to switch. (default: chat)",
        required=False, type=str, choices=MODALITY_CHOICES)
async def switch_model(self, ctx, model: str, scope: str | None = None,
                       modality: str | None = None):
    resolved_modality = modality or "chat"
    ...
```

**Scope logic:**

- `modality == "chat"` → existing behavior: conversation scope updates `active_conversation.settings.model`; channel scope updates `channel_model_defaults[(channel_id, user_id, "chat")]`; `"both"` does both.
- `modality != "chat"` → any scope value is treated as `"channel"` silently. Only `channel_model_defaults[(channel_id, user_id, resolved_modality)]` is written. No error is returned.

Model validation via `get_model()` applies for all modalities unchanged.

### `/current_model`

Reads `channel_model_defaults.get((channel_id, user_id, m))` for each modality `m ∈ {"chat", "image", "video", "tts", "stt"}` and passes the full map to `build_current_model_embed`.

### Modality commands (`image.py`, `video.py`, `speech.py`)

Each command resolves its model with a three-step fallback:

```python
# e.g. image.py
resolved_model = (
    model                                                                 # explicit arg
    or cog.channel_model_defaults.get((channel_id, user_id, "image"))   # channel default
    or OPENROUTER_DEFAULT_IMAGE_MODEL                                     # global env var
)
```

Same pattern for `video` (`"video"`), `tts` (`"tts"`), and `stt` (`"stt"`).

## Embed — `/current_model`

`build_current_model_embed` signature changes to accept:

- `channel_defaults: dict[str, str]` — modality → channel-default model (only set modalities present)
- `global_defaults: dict[str, str | None]` — modality → env-var global default
- `active_model: str | None` — active chat conversation model
- `active_options: str | None` — active chat conversation options description

**Rendered layout:**

```
Chat
  Active conversation: anthropic/claude-3.5-sonnet   ← only when a conversation is active
  Channel default:     openai/gpt-4o                 ← only when set
  Global default:      openai/gpt-4o-mini

Image
  Channel default:     black-forest-labs/flux-1      ← only when set
  Global default:      openai/dall-e-3

Video
  Global default:      runway/gen3                   ← no channel default set, so omitted

TTS
  Global default:      openai/tts-1

STT
  Global default:      openai/whisper-1
```

Rules:
- "Channel default" line is omitted for a modality when no channel default has been set.
- "Active conversation" line is only present for the Chat section, and only when an active conversation exists.
- All five modality sections always appear (so users can see what is switchable).

## Files Changed

| File | Change |
|---|---|
| `state.py` | Add `Modality` and `ModalityModelStore` type aliases |
| `cog.py` | 3-tuple key on `channel_model_defaults`; `modality` option on `/switch_model`; expand `/current_model` reads |
| `command_options.py` | Add `MODALITY_CHOICES` |
| `embeds.py` | Update `build_current_model_embed` signature and rendering |
| `image.py` | Channel-default lookup before env var fallback |
| `video.py` | Channel-default lookup before env var fallback |
| `speech.py` | Channel-default lookup for both `tts` and `stt` before env var fallback |

No new files. No changes to `util.py`, `client.py`, `chat.py`, `views.py`, or tests.

## Out of Scope

- Persisting `channel_model_defaults` across bot restarts (in-memory only, consistent with current behavior)
- Validating that a switched model actually supports the target modality (left to OpenRouter to reject at inference time)
- Per-user global defaults across channels (channel-scoped is sufficient)
