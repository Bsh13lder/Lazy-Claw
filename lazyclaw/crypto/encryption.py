from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

FIXED_SALT = b"lazyclaw-server-key-v1"


def derive_key(password: str, salt: bytes, iterations: int = 100_000) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


def derive_server_key(server_secret: str, user_id: str) -> bytes:
    return derive_key(server_secret + user_id, FIXED_SALT, 100_000)


def encrypt(plaintext: str, key: bytes) -> str:
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    b64_nonce = base64.b64encode(nonce).decode("ascii")
    b64_ciphertext = base64.b64encode(ciphertext).decode("ascii")
    return f"enc:v1:{b64_nonce}:{b64_ciphertext}"


def decrypt(token: str, key: bytes) -> str:
    parts = token.split(":", 3)
    if len(parts) != 4 or parts[0] != "enc" or parts[1] != "v1":
        raise ValueError("Invalid encrypted token format")
    nonce = base64.b64decode(parts[2])
    ciphertext = base64.b64decode(parts[3])
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def is_encrypted(value: str) -> bool:
    return value.startswith("enc:v1:")


def encrypt_field(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    return encrypt(value, key)


def decrypt_field(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    return decrypt(value, key)
