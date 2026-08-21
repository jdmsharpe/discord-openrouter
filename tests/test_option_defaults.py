"""Guard against an option advertising a default the code no longer uses.

A slash-command option's description is the ONLY place the Discord UI tells a
user what happens when they skip that option, and this family of bots states it
as a trailing ``(default: X)`` clause. When a "promote the new default" commit
changes the Python default and misses the description, the bot goes on telling
every user something false -- forever, and silently, because nothing else in the
suite compares the two. Four such defects were found across this fleet on
2026-08-20, each traceable to exactly that kind of commit.

This file has three parts, and it needs all three:

* a fixed matcher table exercising the acceptance rule directly, so the rule
  itself is always under test no matter what discovery turns up;
* an exact-count discovery invariant, so a partial collapse of the discovery
  walk (py-cord moving where options hang off subcommands, say) fails loudly
  instead of quietly shrinking the guard toward nothing;
* the parametrized guard over every in-scope option this cog declares.

Scope is deliberately narrow: an option counts only if it carries static
``choices``, a non-``None`` Python default, a ``(default: X)`` clause, AND a
default that resolves to one of its own choices. An option defaulting to
``None`` resolves downstream (env config, channel default, per-conversation
state) where introspection cannot see the real value, so asserting over those
yields dozens of false alarms -- and a noisy guard gets muted, which is worse
than no guard at all. An option whose default matches none of its own choices
has no display name to compare against; it is counted as unassertable and
reported by the discovery invariant rather than silently passed.

The acceptance rule is tight on purpose, and it has been tightened twice:

* an early pass accepted "the display name starts with the claimed text", which
  let a "Foo 1" -> "Foo 1.5" promotion pass while the description still said
  "Foo 1" -- precisely the drift this guard exists to catch -- and it matched the
  raw default value without checking the value was non-empty, where
  ``"" in claimed`` is always True and accepted arbitrary wrong text;
* plain substring containment then left the mirror-image hole open: "Claude
  Opus 5" is contained in a claim of "Claude Opus 5.1", so promoting 5 to 5.1
  with a stale description still passed.

Every match is therefore anchored with :data:`NOT_EXTENDED`, a lookahead that
rejects a continuation into a longer identifier (a word character, a hyphen, or
a dot followed by a digit) while still allowing ordinary sentence punctuation,
which real descriptions use: "(default: Claude Opus 5. warning: Opus is
expensive!)". Both holes are pinned shut by the matcher table below.
"""

from __future__ import annotations

import importlib
import re

import discord
import pytest

# The cog whose SlashCommandGroup attributes carry every slash command.
COG_MODULE = "discord_openrouter.cogs.openrouter.cog"
COG_CLASS = "OpenRouterCog"

# The house convention for stating a default inside an option description.
DEFAULT_CLAIM_RE = re.compile(r"\(default:\s*([^)]+)\)", re.IGNORECASE)

# A match must not be *extended* by the claim: no word character, no hyphen, and
# no dot-then-digit may follow it, because all three mean the claim actually
# names a longer identifier ("Claude Opus 5.1", "gpt-image-2-mini"). Ordinary
# sentence punctuation -- ". ", ";", "!", ")" -- is still allowed through, since
# real descriptions read "(default: Claude Opus 5. warning: Opus is expensive!)".
NOT_EXTENDED = r"(?![\w-])(?!\.\d)(?!\s+\w)"

# The recorded shape of this cog's option surface, asserted EXACTLY rather than
# as a floor: a ">= N" bound in a repo whose real count IS N can only ever catch
# a total collapse, which makes it no stronger than "did discovery find
# anything". UPDATE THESE TWO NUMBERS DELIBERATELY whenever you add or remove a
# choice-backed option that states a default. A mismatch means either a real
# change to the bot's option surface or a regression in the discovery walk
# below -- both deserve a human look, so never nudge them to make a red run go
# green without deciding which one happened.
EXPECTED_ASSERTABLE_OPTIONS = 1
EXPECTED_UNASSERTABLE_OPTIONS = 0


def _accepts(display_name: str, raw_value: object, claimed: str) -> bool:
    """Accept every legitimate way a description spells the default it falls back to.

    A description may name the choice outright, use the display name with its
    trailing parenthetical trimmed ("Grok Imagine Video 1.5" for "... (Preview)"),
    continue past that stem into prose ("Deep Research; Max for best reports"),
    or use the raw option value ("1:1" where the display name is "Square (1:1)").

    Nothing else is accepted. A claim that is merely a PREFIX of the display name
    is a mismatch ("Foo 1" against a "Foo 1.5" default), and so is a claim that
    EXTENDS it ("Claude Opus 5.1" against a "Claude Opus 5" default) -- both are
    the stale-description drift this file exists to catch, which is why every
    branch is anchored with :data:`NOT_EXTENDED`. The raw-value branch is
    additionally guarded on a non-empty value, since ``"" in claim`` is always
    True and would wave through arbitrary wrong text.
    """
    name = (display_name or "").strip().lower()
    value = str(raw_value or "").strip().lower()
    claim = (claimed or "").strip().lower()
    if not name or not claim:
        return False
    stem = re.sub(r"\s*\(.*", "", name).strip()
    if re.search(re.escape(name) + NOT_EXTENDED, claim):
        return True
    if stem and claim == stem:
        return True
    if stem and re.match(re.escape(stem) + NOT_EXTENDED, claim):
        return True
    # Same anchored raw-value branch as above, inlined only because SIM103 asks
    # for it; the non-empty ``value`` guard is what keeps "" from matching.
    return bool(value and re.search(re.escape(value) + NOT_EXTENDED, claim))


# Proven acceptance cases for the rule itself, kept identical across the fleet.
# This table runs no matter what discovery finds, so the rule can never go
# untested. "prefix-superset-drift", "superset-drift" and "empty-value" are the
# holes review found in earlier passes and they must keep coming out False;
# "sentence-punctuation" is the case that stops anyone closing them by
# over-anchoring the rule into rejecting real descriptions.
MATCHER_CASES = [
    (
        "Gemini 3.7 Flash",
        "gemini-3.7-flash",
        "Gemini 3.7 Flash Pro",
        False,
        "space-extended superset drift: the claim names a longer, different model",
    ),
    pytest.param(
        "GPT Image 2", "gpt-image-2", "GPT Image 1.5", False, "real drift", id="real-drift"
    ),
    pytest.param(
        "GPT Image 1.5",
        "gpt-image-1.5",
        "GPT Image 1",
        False,
        "prefix-superset drift (v3 hole)",
        id="prefix-superset-drift",
    ),
    pytest.param(
        "Claude Opus 5",
        "claude-opus-5",
        "Claude Opus 5.1",
        False,
        "SUPERSET drift (v4 hole)",
        id="superset-drift",
    ),
    pytest.param(
        "Claude Opus 5",
        "claude-opus-5",
        "Claude Opus 5. warning: Opus is expensive!",
        True,
        "sentence punctuation after name",
        id="sentence-punctuation",
    ),
    pytest.param(
        "Grok Imagine Video 1.5 (Preview)",
        "grok-imagine-video-1.5-preview",
        "Grok Imagine Video 1.5",
        True,
        "trailing parenthetical trimmed",
        id="parenthetical-trimmed",
    ),
    pytest.param(
        "Deep Research (Apr 2026)",
        "deep-research-preview-04-2026",
        "Deep Research; Max for best reports",
        True,
        "prose after the stem",
        id="prose-after-stem",
    ),
    pytest.param(
        "Square (1:1)", "1:1", "1:1", True, "description uses the raw value", id="raw-value"
    ),
    pytest.param("Kore (Firm)", "Kore", "Kore", True, "value spelling", id="value-spelling"),
    pytest.param(
        "Gemini 3.7 Flash",
        "gemini-3.7-flash",
        "Gemini 3.6 Flash",
        False,
        "real drift",
        id="real-drift-minor-version",
    ),
    pytest.param(
        "Anything",
        "",
        "total nonsense",
        False,
        "empty value must not vacuously accept",
        id="empty-value",
    ),
    pytest.param(
        "Gemini 3.1 Flash Preview TTS",
        "gemini-3.1-flash-tts-preview",
        "Gemini 2.5 Flash Preview TTS",
        False,
        "real drift",
        id="real-drift-generation",
    ),
]


def _discover_default_claims() -> tuple[list[tuple[str, str, object, str]], list[str]]:
    """Return ``(assertable, unassertable)`` for the cog's stated option defaults.

    ``assertable`` holds ``(label, claimed, actual, choice_name)`` for every
    option in scope; ``unassertable`` holds the labels of options that state a
    default which resolves to none of their own choices, so there is no display
    name to compare the description against.

    The walk goes ``vars()`` of the cog class -> ``SlashCommandGroup`` attributes
    -> subcommands -> options, so nothing is hand-enumerated here and future
    commands are covered for free.
    """
    cog_class = getattr(importlib.import_module(COG_MODULE), COG_CLASS)
    assertable: list[tuple[str, str, object, str]] = []
    unassertable: list[str] = []
    for group in vars(cog_class).values():
        if not isinstance(group, discord.SlashCommandGroup):
            continue
        for subcommand in group.subcommands:
            for opt in getattr(subcommand, "options", []):
                choices = getattr(opt, "choices", None) or []
                actual = getattr(opt, "default", None)
                if not choices or actual is None:
                    continue
                claim = DEFAULT_CLAIM_RE.search(getattr(opt, "description", "") or "")
                if claim is None:
                    continue
                label = f"{group.name} {subcommand.name} --{opt.name}"
                choice_name = next((c.name for c in choices if c.value == actual), None)
                if choice_name is None:
                    unassertable.append(label)
                    continue
                assertable.append((label, claim.group(1).strip(), actual, choice_name))
    return assertable, unassertable


_DEFAULT_CLAIMS, _UNASSERTABLE = _discover_default_claims()


@pytest.mark.parametrize(("display_name", "raw_value", "claimed", "expected", "why"), MATCHER_CASES)
def test_claim_matcher_accepts_only_real_matches(
    display_name: str, raw_value: object, claimed: str, expected: bool, why: str
) -> None:
    """Pin the acceptance rule to its known-good verdicts.

    Without this the rule could be loosened -- as it twice was -- and every
    per-option case below would keep passing while the guard quietly stopped
    catching drift.
    """
    verdict = _accepts(display_name, raw_value, claimed)
    assert verdict is expected, (
        f"'(default: {claimed})' against the {display_name!r} choice ({raw_value!r}) should be "
        f"{'accepted' if expected else 'rejected'} -- {why} -- but the matcher "
        f"{'accepted' if verdict else 'rejected'} it."
    )


def test_discovered_option_counts_match_the_recorded_surface() -> None:
    """Fail loudly if the discovery walk stops finding the options it used to.

    This is an EXACT equality, not a floor, and that is the point: a floor set to
    the repo's real count can only catch a total collapse, and a floor of zero
    can never fail at all. Equality also catches the opposite direction -- a new
    choice-backed option with a stated default arriving without anyone updating
    the recorded numbers, which is the moment to check the new description too.

    When this fails, decide which happened: a real change to the bot's options
    (update ``EXPECTED_ASSERTABLE_OPTIONS`` /
    ``EXPECTED_UNASSERTABLE_OPTIONS`` in the same commit) or a regression in the
    walk (py-cord moved where options hang off subcommands, or the cog stopped
    exposing SlashCommandGroup attributes -- fix the wiring, not the numbers).
    """
    assert (len(_DEFAULT_CLAIMS), len(_UNASSERTABLE)) == (
        EXPECTED_ASSERTABLE_OPTIONS,
        EXPECTED_UNASSERTABLE_OPTIONS,
    ), (
        f"Discovery found {len(_DEFAULT_CLAIMS)} assertable and {len(_UNASSERTABLE)} unassertable "
        f"choice-backed option(s) with a stated default on {COG_MODULE}.{COG_CLASS}, but this "
        f"file records {EXPECTED_ASSERTABLE_OPTIONS} and {EXPECTED_UNASSERTABLE_OPTIONS}. "
        f"Assertable: {', '.join(label for label, _, _, _ in _DEFAULT_CLAIMS) or 'none'}. "
        f"Unassertable (default resolves to none of the option's own choices, so there is no "
        f"display name to compare against): {', '.join(_UNASSERTABLE) or 'none'}."
    )


@pytest.mark.parametrize(
    ("label", "claimed", "actual", "choice_name"),
    _DEFAULT_CLAIMS,
    ids=[label for label, _, _, _ in _DEFAULT_CLAIMS],
)
def test_stated_default_matches_real_default(
    label: str, claimed: str, actual: object, choice_name: str
) -> None:
    assert _accepts(choice_name, actual, claimed), (
        f"/{label} advertises '(default: {claimed})' but the option actually "
        f"defaults to {actual!r}, the {choice_name!r} choice. Discord renders "
        "that description verbatim, so every user is being shown a default the "
        "bot does not use. Update the description to name the real default -- "
        "or restore the default the description promises."
    )
