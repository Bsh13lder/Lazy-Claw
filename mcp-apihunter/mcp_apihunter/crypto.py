"""Self-contained AES-256-GCM encryption for the apihunter manifest.

Deliberately standalone — does NOT import from ``lazyclaw`` so the package
stays installable on its own. The wire format mirrors LazyClaw's
``enc:v2:{nonce}:{ciphertext}`` tokens so the two stay conceptually aligned,
but the key here is derived independently from the server secret + user id
that the bundled-MCP launcher injects into this subprocess's environment.

The manifest never stores credentials (cookies and passwords live in the
browser profile / vault, not here). It stores endpoint structure — which is
still sensitive, so it is always encrypted at rest.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Distinct from LazyClaw's own salts so an apihunter token can never be
# confused with (or swapped for) a LazyClaw DB token even under the same
# server secret. 600K iterations matches the project's OWASP-2024 posture.
_APIHUNTER_SALT = b"apihunter-manifest-key-v1"
_ITERATIONS = 600_000
_NONCE_BYTES = 12  # 96-bit nonce for AES-GCM
_KEY_BYTES = 32    # 256-bit key


def derive_manifest_key(server_secret: str, user_id: str) -> bytes:
    """Derive the per-user 256-bit manifest key.

    Both inputs come from the subprocess environment: ``server_secret`` from
    the inherited ``SERVER_SECRET`` (loaded from ``.env`` by the parent), and
    ``user_id`` from the injected ``LAZYCLAW_USER_ID``. Deterministic, so the
    same user always derives the same key across restarts — no key needs to be
    persisted anywhere.
    """
    if not server_secret:
        raise ValueError("server_secret is required to derive a manifest key")
    if not user_id:
        raise ValueError("user_id is required to derive a manifest key")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=_APIHUNTER_SALT,
        iterations=_ITERATIONS,
    )
    return kdf.derive((server_secret + user_id).encode("utf-8"))


def manifest_aad(user_id: str, site: str) -> bytes:
    """Bind a ciphertext to one user + site so tokens can't be swapped."""
    return f"apihunter:{user_id}:{site}".encode("utf-8")


def encrypt(plaintext: str, key: bytes, aad: bytes) -> str:
    """Encrypt to an ``enc:v2:{b64 nonce}:{b64 ciphertext}`` token."""
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    b64_nonce = base64.b64encode(nonce).decode("ascii")
    b64_ct = base64.b64encode(ciphertext).decode("ascii")
    return f"enc:v2:{b64_nonce}:{b64_ct}"


def decrypt(token: str, key: bytes, aad: bytes) -> str:
    """Decrypt an ``enc:v2`` token. Raises on tampering or wrong key/AAD."""
    parts = token.split(":", 3)
    if len(parts) != 4 or parts[0] != "enc" or parts[1] != "v2":
        raise ValueError("Invalid apihunter encrypted token format")
    nonce = base64.b64decode(parts[2])
    ciphertext = base64.b64decode(parts[3])
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    return plaintext.decode("utf-8")


def is_encrypted(value: str) -> bool:
    """True if ``value`` looks like an apihunter ciphertext token."""
    return value.startswith("enc:v2:")
