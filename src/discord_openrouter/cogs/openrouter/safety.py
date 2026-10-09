"""Pseudonymous per-user identifiers for OpenRouter requests.

OpenRouter uses the `user` field to attribute abuse to one end user instead of
to the whole account. The value is an HMAC-SHA256 of the Discord user ID, so
OpenRouter can tell users apart without learning who they are.

The HMAC key is `SAFETY_IDENTIFIER_SECRET` when set, otherwise a key derived
from `BOT_TOKEN`. It is never derived from `OPENROUTER_API_KEY`: OpenRouter
holds that key and could rebuild the mapping by hashing known Discord user IDs.
An unkeyed hash would allow the same, because Discord user IDs are enumerable.
"""

from __future__ import annotations

import hashlib
import hmac

from ...config.auth import BOT_TOKEN, SAFETY_IDENTIFIER_SECRET

# Domain-separation label for the key derived from BOT_TOKEN, so the derived
# key is not the bot token itself and cannot be reused for another purpose.
SAFETY_IDENTIFIER_KEY_LABEL = b"safety-identifier-v1"


def derive_safety_identifier_key(secret: str | None, bot_token: str | None) -> bytes | None:
    """Return the HMAC key, or None when neither a secret nor a bot token is set."""
    if secret:
        return secret.encode("utf-8")
    if bot_token:
        return hmac.new(
            bot_token.encode("utf-8"), SAFETY_IDENTIFIER_KEY_LABEL, hashlib.sha256
        ).digest()
    return None


SAFETY_IDENTIFIER_KEY: bytes | None = derive_safety_identifier_key(
    SAFETY_IDENTIFIER_SECRET, BOT_TOKEN
)


def build_safety_identifier(user_id: int) -> str | None:
    """Return the 64-character hex HMAC-SHA256 of a Discord user ID.

    Returns None when no key is configured, so the request omits `user`
    rather than sending an unkeyed value.
    """
    key = SAFETY_IDENTIFIER_KEY
    if key is None:
        return None
    return hmac.new(key, str(user_id).encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = [
    "SAFETY_IDENTIFIER_KEY",
    "SAFETY_IDENTIFIER_KEY_LABEL",
    "build_safety_identifier",
    "derive_safety_identifier_key",
]
