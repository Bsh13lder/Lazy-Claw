"""Encrypt/decrypt codec for ``agent_messages.metadata`` (tool-call args).

Closes the one plaintext leak in the encrypted message store (2026-06-10
audit): tool-call ARGUMENTS — including credentials passed to vault/
payment skills — were persisted verbatim in the ``metadata`` column while
``content`` sat encrypted next to them.

Write path encrypts with the user DEK (same ``enc:v1`` format as content).
Read path is tolerant by design: legacy plaintext rows pass through
unchanged (no migration required to function), and undecryptable values
fail soft to ``None`` so tool_calls reconstruction is skipped without
losing the message content.
"""

from __future__ import annotations

import logging

from lazyclaw.crypto.encryption import decrypt, encrypt, is_encrypted

logger = logging.getLogger(__name__)


def encode_tool_metadata(metadata_json: str | None, key: bytes) -> str | None:
    """Encrypt serialized tool-calls JSON for storage. None/empty → None."""
    if not metadata_json:
        return None
    return encrypt(metadata_json, key)


def decode_tool_metadata(raw: str | None, key: bytes) -> str | None:
    """Return plaintext tool-calls JSON from a stored metadata value.

    Legacy plaintext rows (pre-encryption) pass through unchanged.
    Decryption failure returns None — fail-soft, never raises.
    """
    if not raw:
        return None
    if not is_encrypted(raw):
        return raw
    try:
        return decrypt(raw, key)
    except Exception:
        logger.debug("tool metadata decrypt failed — skipping tool_calls", exc_info=True)
        return None
