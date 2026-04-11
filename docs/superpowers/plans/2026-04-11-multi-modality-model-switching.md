# Multi-Modality Model Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `/switch_model` with a `modality` dropdown and `/current_model` to show all modality defaults, so users can save per-channel model preferences for chat, image, video, tts, and stt independently.

**Architecture:** Change `channel_model_defaults` from a 2-tuple key `(channel_id, user_id)` to a 3-tuple `(channel_id, user_id, modality)`. All 5 read/write sites are updated to append `"chat"` or the relevant modality string. Modality commands gain a three-step fallback: explicit arg → channel default → env-var global.

**Tech Stack:** Python 3.10+, py-cord 2.7, pytest 9.0

---

## File Map

| File | Change |
|---|---|
| `src/discord_openrouter/cogs/openrouter/state.py` | Add `Modality` and `ModalityModelStore` type aliases |
| `src/discord_openrouter/cogs/openrouter/command_options.py` | Add `MODALITY_CHOICES` |
| `src/discord_openrouter/cogs/openrouter/cog.py` | 3-tuple key on `channel_model_defaults` (3 sites); `modality` option on `/switch_model`; expand `/current_model` reads |
| `src/discord_openrouter/cogs/openrouter/chat.py` | 3-tuple key on `channel_model_defaults` read (1 site) |
| `src/discord_openrouter/cogs/openrouter/embeds.py` | Rewrite `build_current_model_embed` signature and rendering |
| `src/discord_openrouter/cogs/openrouter/image.py` | Channel-default lookup before env var fallback |
| `src/discord_openrouter/cogs/openrouter/video.py` | Channel-default lookup before env var fallback |
| `src/discord_openrouter/cogs/openrouter/speech.py` | Channel-default lookup for both `tts` and `stt` |
| `tests/test_embeds.py` | New tests for updated `build_current_model_embed` |
| `tests/test_openrouter_state.py` | New test for 3-tuple key modality model store |

---

## Task 1: Add type aliases and `MODALITY_CHOICES`

**Files:**
- Modify: `src/discord_openrouter/cogs/openrouter/state.py`
- Modify: `src/discord_openrouter/cogs/openrouter/command_options.py`
- Test: `tests/test_openrouter_state.py`

- [ ] **Step 1: Write failing test for MODALITY_CHOICES**

Add to `tests/test_openrouter_state.py`:

```python
def test_modality_choices_covers_all_expected_modalities():
    from discord_openrouter.cogs.openrouter.command_options import MODALITY_CHOICES

    values = {choice.value for choice in MODALITY_CHOICES}
    assert values == {"chat", "image", "video", "tts", "stt"}
```

- [ ] **Step 2: Run test to verify it fails**

```
cd C:\Users\jdmsh\OneDrive\Desktop\discord-openrouter
.venv/Scripts/python -m pytest tests/test_openrouter_state.py::test_modality_choices_covers_all_expected_modalities -v
```

Expected: `FAILED` with `ImportError: cannot import name 'MODALITY_CHOICES'`

- [ ] **Step 3: Add `MODALITY_CHOICES` to `command_options.py`**

Add after the existing `MODEL_SCOPE_CHOICES` block (after line 16):

```python
MODALITY_CHOICES = [
    OptionChoice(name="Chat", value="chat"),
    OptionChoice(name="Image", value="image"),
    OptionChoice(name="Video", value="video"),
    OptionChoice(name="TTS", value="tts"),
    OptionChoice(name="STT", value="stt"),
]
```

- [ ] **Step 4: Add type aliases to `state.py`**

Add after the existing `TypeAlias` imports at the top of `state.py` (after line 12, below the existing alias block):

```python
Modality: TypeAlias = str  # "chat" | "image" | "video" | "tts" | "stt"
ModalityModelStore: TypeAlias = dict[tuple[int, int, Modality], str]
```

The `TypeAlias` import is already present via `from typing import Any, TypeAlias`.

- [ ] **Step 5: Run test to verify it passes**

```
.venv/Scripts/python -m pytest tests/test_openrouter_state.py::test_modality_choices_covers_all_expected_modalities -v
```

Expected: `PASSED`

- [ ] **Step 6: Run full test suite to check for regressions**

```
.venv/Scripts/python -m pytest -v
```

Expected: all existing tests pass.

- [ ] **Step 7: Commit**

```
git add src/discord_openrouter/cogs/openrouter/state.py \
        src/discord_openrouter/cogs/openrouter/command_options.py \
        tests/test_openrouter_state.py
git commit -m "feat: add Modality type aliases and MODALITY_CHOICES"
```

---

## Task 2: Migrate `channel_model_defaults` to 3-tuple key

**Files:**
- Modify: `src/discord_openrouter/cogs/openrouter/cog.py` (3 sites)
- Modify: `src/discord_openrouter/cogs/openrouter/chat.py` (1 site)
- Test: `tests/test_openrouter_state.py`

There are exactly 5 access points to `channel_model_defaults`. This task updates them all and adds the import for `ModalityModelStore`.

- [ ] **Step 1: Write failing test for 3-tuple key chat lookup**

Add to `tests/test_openrouter_state.py`:

```python
def test_channel_model_defaults_uses_modality_as_third_key():
    from types import SimpleNamespace
    from discord_openrouter.cogs.openrouter.state import ModalityModelStore

    store: ModalityModelStore = {}
    channel_id, user_id = 100, 42

    store[(channel_id, user_id, "chat")] = "openai/gpt-4o"
    store[(channel_id, user_id, "image")] = "openai/dall-e-3"

    assert store.get((channel_id, user_id, "chat")) == "openai/gpt-4o"
    assert store.get((channel_id, user_id, "image")) == "openai/dall-e-3"
    assert store.get((channel_id, user_id, "video")) is None
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/Scripts/python -m pytest tests/test_openrouter_state.py::test_channel_model_defaults_uses_modality_as_third_key -v
```

Expected: `FAILED` with `ImportError: cannot import name 'ModalityModelStore'`

- [ ] **Step 3: Export `ModalityModelStore` from `state.py`**

`ModalityModelStore` was added in Task 1 but is not yet exported. Verify it's accessible — no `__all__` is defined in `state.py`, so it's already importable. The test import should resolve after the alias was added in Task 1. If the test fails only due to a different reason, re-check Task 1 was committed correctly.

- [ ] **Step 4: Run test to verify it passes**

```
.venv/Scripts/python -m pytest tests/test_openrouter_state.py::test_channel_model_defaults_uses_modality_as_third_key -v
```

Expected: `PASSED` (the test validates dict key semantics, no code change needed beyond Task 1).

- [ ] **Step 5: Update `cog.py` — type annotation (line 73)**

```python
# Before
self.channel_model_defaults: dict[tuple[int, int], str] = {}

# After
self.channel_model_defaults: ModalityModelStore = {}
```

Also add `ModalityModelStore` to the import from `.state`:

```python
# Before (in cog.py imports)
from .state import (
    cleanup_conversation,
    create_button_view,
    find_active_conversation,
    prune_runtime_state,
    stop_conversation,
    strip_previous_view,
)

# After
from .state import (
    ModalityModelStore,
    cleanup_conversation,
    create_button_view,
    find_active_conversation,
    prune_runtime_state,
    stop_conversation,
    strip_previous_view,
)
```

- [ ] **Step 6: Update `cog.py` — `current_model` read (line 336)**

```python
# Before
channel_default = self.channel_model_defaults.get((channel_id, user_id))

# After
channel_default = self.channel_model_defaults.get((channel_id, user_id, "chat"))
```

- [ ] **Step 7: Update `cog.py` — `switch_model` writes (lines 385 and 408)**

Line 385 (inside the `if resolved_scope == "conversation":` fallback branch):

```python
# Before
self.channel_model_defaults[(channel_id, user_id)] = resolved_model

# After
self.channel_model_defaults[(channel_id, user_id, "chat")] = resolved_model
```

Line 408 (channel scope write):

```python
# Before
self.channel_model_defaults[(channel_id, user_id)] = resolved_model

# After
self.channel_model_defaults[(channel_id, user_id, "chat")] = resolved_model
```

- [ ] **Step 8: Update `chat.py` — `_resolve_model_for_request` read (line 502)**

```python
# Before
channel_default = cog.channel_model_defaults.get((channel_id, user_id))

# After
channel_default = cog.channel_model_defaults.get((channel_id, user_id, "chat"))
```

- [ ] **Step 9: Run full test suite**

```
.venv/Scripts/python -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```
git add src/discord_openrouter/cogs/openrouter/cog.py \
        src/discord_openrouter/cogs/openrouter/chat.py \
        tests/test_openrouter_state.py
git commit -m "feat: migrate channel_model_defaults to 3-tuple (channel, user, modality) key"
```

---

## Task 3: Rewrite `build_current_model_embed`

**Files:**
- Modify: `src/discord_openrouter/cogs/openrouter/embeds.py`
- Test: `tests/test_embeds.py`

The current signature is:
```python
def build_current_model_embed(*, active_model, active_options, channel_default, global_default) -> Embed
```

The new signature accepts a dict of channel defaults and a dict of global defaults, one entry per modality.

- [ ] **Step 1: Write failing tests for the new embed**

Add to `tests/test_embeds.py`:

```python
def test_build_current_model_embed_shows_all_modalities_with_no_channel_defaults():
    from discord_openrouter.cogs.openrouter.embeds import build_current_model_embed

    embed = build_current_model_embed(
        active_model=None,
        active_options=None,
        channel_defaults={},
        global_defaults={
            "chat": "openai/gpt-4o-mini",
            "image": "openai/dall-e-3",
            "video": "runway/gen3",
            "tts": "openai/tts-1",
            "stt": "openai/whisper-1",
        },
    )

    desc = embed.description or ""
    assert "Chat" in desc
    assert "Image" in desc
    assert "Video" in desc
    assert "TTS" in desc
    assert "STT" in desc
    # No channel defaults set — "Channel default" line should not appear
    assert "Channel default" not in desc
    assert "openai/gpt-4o-mini" in desc
    assert "openai/dall-e-3" in desc


def test_build_current_model_embed_shows_channel_default_only_when_set():
    from discord_openrouter.cogs.openrouter.embeds import build_current_model_embed

    embed = build_current_model_embed(
        active_model="anthropic/claude-sonnet-4-5",
        active_options="web search",
        channel_defaults={"chat": "openai/gpt-4o", "image": "black-forest-labs/flux-1"},
        global_defaults={
            "chat": "openai/gpt-4o-mini",
            "image": "openai/dall-e-3",
            "video": "runway/gen3",
            "tts": "openai/tts-1",
            "stt": "openai/whisper-1",
        },
    )

    desc = embed.description or ""
    # Chat has active conversation + channel default
    assert "anthropic/claude-sonnet-4-5" in desc
    assert "web search" in desc
    assert "openai/gpt-4o" in desc
    # Image has channel default
    assert "black-forest-labs/flux-1" in desc
    # Video has no channel default — "Channel default" should not appear for video
    # (it appears for chat and image, but not video/tts/stt)
    assert "runway/gen3" in desc
    # Channel default line appears exactly twice (chat and image)
    assert desc.count("Channel default") == 2


def test_build_current_model_embed_omits_active_conversation_line_when_no_active_conversation():
    from discord_openrouter.cogs.openrouter.embeds import build_current_model_embed

    embed = build_current_model_embed(
        active_model=None,
        active_options=None,
        channel_defaults={},
        global_defaults={"chat": "openai/gpt-4o-mini", "image": None, "video": None, "tts": None, "stt": None},
    )

    desc = embed.description or ""
    assert "Active conversation" not in desc
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/Scripts/python -m pytest tests/test_embeds.py::test_build_current_model_embed_shows_all_modalities_with_no_channel_defaults tests/test_embeds.py::test_build_current_model_embed_shows_channel_default_only_when_set tests/test_embeds.py::test_build_current_model_embed_omits_active_conversation_line_when_no_active_conversation -v
```

Expected: `FAILED` with `TypeError` (wrong arguments to `build_current_model_embed`).

- [ ] **Step 3: Rewrite `build_current_model_embed` in `embeds.py`**

Replace the existing `build_current_model_embed` function (lines 232–250) with:

```python
_MODALITY_LABELS: dict[str, str] = {
    "chat": "Chat",
    "image": "Image",
    "video": "Video",
    "tts": "TTS",
    "stt": "STT",
}
_MODALITY_ORDER = ["chat", "image", "video", "tts", "stt"]


def build_current_model_embed(
    *,
    active_model: str | None,
    active_options: str | None,
    channel_defaults: dict[str, str],
    global_defaults: dict[str, str | None],
) -> Embed:
    sections: list[str] = []
    for modality in _MODALITY_ORDER:
        label = _MODALITY_LABELS[modality]
        lines = [f"**{label}**"]
        if modality == "chat" and active_model:
            lines.append(f"  Active conversation: `{active_model}`")
            if active_options:
                lines.append(f"  Active options: {active_options}")
        channel_default = channel_defaults.get(modality)
        if channel_default:
            lines.append(f"  Channel default: `{channel_default}`")
        global_default = global_defaults.get(modality)
        if global_default:
            lines.append(f"  Global default: `{global_default}`")
        sections.append("\n".join(lines))
    return Embed(
        title="Current Model Settings",
        description="\n\n".join(sections),
        color=Colour.blue(),
    )
```

- [ ] **Step 4: Run new tests to verify they pass**

```
.venv/Scripts/python -m pytest tests/test_embeds.py::test_build_current_model_embed_shows_all_modalities_with_no_channel_defaults tests/test_embeds.py::test_build_current_model_embed_shows_channel_default_only_when_set tests/test_embeds.py::test_build_current_model_embed_omits_active_conversation_line_when_no_active_conversation -v
```

Expected: all 3 `PASSED`.

- [ ] **Step 5: Run full test suite**

```
.venv/Scripts/python -m pytest -v
```

Expected: all tests pass. (The old `build_current_model_embed` call in `cog.py` will break at runtime but the existing tests don't call it — we fix `cog.py` in Task 4.)

- [ ] **Step 6: Commit**

```
git add src/discord_openrouter/cogs/openrouter/embeds.py \
        tests/test_embeds.py
git commit -m "feat: rewrite build_current_model_embed to show all modality defaults"
```

---

## Task 4: Add `modality` to `/switch_model` and update `/current_model`

**Files:**
- Modify: `src/discord_openrouter/cogs/openrouter/cog.py`

This task wires up the `modality` option on `/switch_model` and fixes the now-broken `build_current_model_embed` call in `/current_model`.

- [ ] **Step 1: Update `/switch_model` in `cog.py`**

Add `MODALITY_CHOICES` to the import from `.command_options`. Find the existing import block:

```python
from .command_options import (
    IMAGE_ASPECT_RATIO_CHOICES,
    IMAGE_SIZE_CHOICES,
    MODEL_INPUT_MODALITY_CHOICES,
    MODEL_OUTPUT_MODALITY_CHOICES,
    MODEL_SCOPE_CHOICES,
    PDF_ENGINE_CHOICES,
    PROMPT_CACHE_TTL_CHOICES,
    REASONING_EFFORT_CHOICES,
    TTS_FORMAT_CHOICES,
    VIDEO_ASPECT_RATIO_CHOICES,
    VIDEO_RESOLUTION_CHOICES,
)
```

Add `MODALITY_CHOICES,` to that list (keep alphabetical order — add after `MODEL_SCOPE_CHOICES`).

- [ ] **Step 2: Add the `modality` decorator option to `/switch_model`**

Find the `/switch_model` command decorator block. After the existing `scope` option decorator, add:

```python
@option(
    "modality",
    description="Which modality's model to switch. (default: chat)",
    required=False,
    type=str,
    choices=MODALITY_CHOICES,
)
```

Update the function signature to accept `modality: str | None = None`:

```python
# Before
async def switch_model(
    self,
    ctx: ApplicationContext,
    model: str,
    scope: str | None = None,
):

# After
async def switch_model(
    self,
    ctx: ApplicationContext,
    model: str,
    scope: str | None = None,
    modality: str | None = None,
):
```

- [ ] **Step 3: Update `switch_model` body to use `resolved_modality`**

At the top of the `switch_model` body, add:

```python
resolved_modality = modality or "chat"
```

For non-chat modalities the conversation scope silently acts as channel scope. Replace the scope routing block entirely with this clean `if/else` split:

```python
if resolved_modality == "chat":
    if resolved_scope in {"conversation", "both"}:
        if active_conversation is None:
            if resolved_scope == "conversation":
                self.channel_model_defaults[(channel_id, user_id, "chat")] = resolved_model
                lines.append("**Conversation:** no active conversation to update")
                lines.append("**Channel default:** updated (fallback)")
                resolved_scope = "channel"
            else:
                lines.append("**Conversation:** no active conversation to update")
        else:
            active_conversation.settings.model = resolved_model
            if (
                active_conversation.settings.prompt_cache_ttl
                and not prompt_cache_supported_for_model(resolved_model)
            ):
                active_conversation.settings.prompt_cache_ttl = None
                lines.append("**Prompt cache:** cleared (not supported by the new model)")
            active_conversation.touch()
            lines.append("**Conversation:** updated")
            active_options = describe_chat_settings(active_conversation.settings)
            if active_options:
                lines.append(f"**Active options:** {active_options}")

    if resolved_scope in {"channel", "both"} and (
        "**Channel default:** updated (fallback)" not in lines
    ):
        self.channel_model_defaults[(channel_id, user_id, "chat")] = resolved_model
        lines.append("**Channel default:** updated")
else:
    # non-chat modalities: scope is always channel (silently downgraded)
    self.channel_model_defaults[(channel_id, user_id, resolved_modality)] = resolved_model
    lines.append("**Channel default:** updated")
```

- [ ] **Step 4: Update `/current_model` to build full modality defaults and call updated embed**

Replace the body of the `current_model` command:

```python
async def current_model(self, ctx: ApplicationContext):
    user_id = ctx.user.id
    channel_id = ctx.channel.id if ctx.channel is not None else 0
    active_conversation = find_active_conversation(self, channel_id=channel_id, user_id=user_id)

    channel_defaults = {
        modality: self.channel_model_defaults[(channel_id, user_id, modality)]
        for modality in ("chat", "image", "video", "tts", "stt")
        if (channel_id, user_id, modality) in self.channel_model_defaults
    }
    global_defaults = {
        "chat": OPENROUTER_DEFAULT_TEXT_MODEL,
        "image": OPENROUTER_DEFAULT_IMAGE_MODEL,
        "video": OPENROUTER_DEFAULT_VIDEO_MODEL,
        "tts": OPENROUTER_DEFAULT_TTS_MODEL,
        "stt": OPENROUTER_DEFAULT_STT_MODEL,
    }
    embed = build_current_model_embed(
        active_model=active_conversation.settings.model if active_conversation else None,
        active_options=(
            describe_chat_settings(active_conversation.settings)
            if active_conversation is not None
            else None
        ),
        channel_defaults=channel_defaults,
        global_defaults=global_defaults,
    )
    await ctx.respond(embed=embed)
```

Also add the missing config imports. The `cog.py` top already imports `OPENROUTER_DEFAULT_IMAGE_MODEL`, `OPENROUTER_DEFAULT_STT_MODEL`, `OPENROUTER_DEFAULT_TEXT_MODEL`, `OPENROUTER_DEFAULT_TTS_MODEL`, `OPENROUTER_DEFAULT_VIDEO_MODEL` — verify all five are present in the `from ...config import (...)` block. They are already imported per the file read above.

- [ ] **Step 5: Run full test suite**

```
.venv/Scripts/python -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```
git add src/discord_openrouter/cogs/openrouter/cog.py
git commit -m "feat: add modality option to /switch_model and expand /current_model to all modalities"
```

---

## Task 5: Add channel-default lookup to modality commands

**Files:**
- Modify: `src/discord_openrouter/cogs/openrouter/image.py`
- Modify: `src/discord_openrouter/cogs/openrouter/video.py`
- Modify: `src/discord_openrouter/cogs/openrouter/speech.py`

Each command currently resolves its model as:
```python
resolved_model = (model or OPENROUTER_DEFAULT_XYZ_MODEL).strip()
```

It needs to become a three-step fallback that checks the channel default first.

- [ ] **Step 1: Update `image.py` — `run_image_command`**

Find line 36 in `image.py`:

```python
# Before
resolved_model = (model or OPENROUTER_DEFAULT_IMAGE_MODEL).strip()

# After
channel_id = ctx.channel.id if ctx.channel is not None else 0
resolved_model = (
    model
    or cog.channel_model_defaults.get((channel_id, ctx.author.id, "image"))
    or OPENROUTER_DEFAULT_IMAGE_MODEL
).strip()
```

- [ ] **Step 2: Update `video.py` — `run_video_command`**

Find line 38 in `video.py`:

```python
# Before
resolved_model = (model or OPENROUTER_DEFAULT_VIDEO_MODEL).strip()

# After
channel_id = ctx.channel.id if ctx.channel is not None else 0
resolved_model = (
    model
    or cog.channel_model_defaults.get((channel_id, ctx.author.id, "video"))
    or OPENROUTER_DEFAULT_VIDEO_MODEL
).strip()
```

- [ ] **Step 3: Update `speech.py` — `run_tts_command`**

Find line 60 in `speech.py`:

```python
# Before
resolved_model = (model or OPENROUTER_DEFAULT_TTS_MODEL).strip()

# After
channel_id = ctx.channel.id if ctx.channel is not None else 0
resolved_model = (
    model
    or cog.channel_model_defaults.get((channel_id, ctx.author.id, "tts"))
    or OPENROUTER_DEFAULT_TTS_MODEL
).strip()
```

- [ ] **Step 4: Update `speech.py` — `run_stt_command`**

Find line 191 in `speech.py`:

```python
# Before
resolved_model = (model or OPENROUTER_DEFAULT_STT_MODEL).strip()

# After
channel_id = ctx.channel.id if ctx.channel is not None else 0
resolved_model = (
    model
    or cog.channel_model_defaults.get((channel_id, ctx.author.id, "stt"))
    or OPENROUTER_DEFAULT_STT_MODEL
).strip()
```

- [ ] **Step 5: Run full test suite**

```
.venv/Scripts/python -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```
git add src/discord_openrouter/cogs/openrouter/image.py \
        src/discord_openrouter/cogs/openrouter/video.py \
        src/discord_openrouter/cogs/openrouter/speech.py
git commit -m "feat: add channel-default model lookup to image, video, tts, stt commands"
```

---

## Done

At this point:
- `/switch_model modality:image model:black-forest-labs/flux-1` sets the image default for your channel
- `/switch_model modality:chat model:anthropic/claude-opus-4-6 scope:both` works exactly as before
- `/current_model` shows a five-section embed covering all modalities
- `/image`, `/video`, `/tts`, `/stt` all respect the channel default before falling back to the global env-var
