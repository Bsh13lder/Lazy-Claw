"""Encrypted credential vault.

Stores API keys, tokens, and other secrets encrypted with the user's DEK.
Uses AAD to bind each credential to its user and key name, preventing
ciphertext swapping attacks.
"""

from __future__ import annotations

from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt, encrypt, is_encrypted, user_aad
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session


async def set_credential(config: Config, user_id: str, key: str, value: str) -> None:
    """Store or update an encrypted credential."""
    dek = await get_user_dek(config, user_id)
    aad = user_aad(user_id, f"vault:{key}")
    encrypted_value = encrypt(value, dek, aad)

    async with db_session(config) as db:
        existing = await db.execute(
            "SELECT id FROM credential_vault WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        row = await existing.fetchone()
        if row:
            await db.execute(
                "UPDATE credential_vault SET value = ? WHERE id = ?",
                (encrypted_value, row[0]),
            )
        else:
            await db.execute(
                "INSERT INTO credential_vault (id, user_id, key, value) VALUES (?, ?, ?, ?)",
                (str(uuid4()), user_id, key, encrypted_value),
            )
        await db.commit()


async def get_credential(config: Config, user_id: str, key: str) -> str | None:
    """Get a decrypted credential value. Returns None if not found."""
    dek = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        row = await db.execute(
            "SELECT value FROM credential_vault WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        result = await row.fetchone()

    if not result:
        return None

    value = result[0]
    if not is_encrypted(value):
        return value

    # v2 tokens have AAD; v1 tokens don't (decrypt handles both)
    aad = user_aad(user_id, f"vault:{key}")
    return decrypt(value, dek, aad)


async def delete_credential(config: Config, user_id: str, key: str) -> bool:
    """Delete a credential. Returns True if deleted."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM credential_vault WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_credentials(config: Config, user_id: str) -> list[str]:
    """List credential key names (not values) for a user."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT key FROM credential_vault WHERE user_id = ? ORDER BY key",
            (user_id,),
        )
        results = await rows.fetchall()
    return [r[0] for r in results]
