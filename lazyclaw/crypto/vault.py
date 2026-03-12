from __future__ import annotations
from uuid import uuid4
from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session


async def set_credential(config: Config, user_id: str, key: str, value: str) -> None:
    """Store or update an encrypted credential."""
    enc_key = derive_server_key(config.server_secret, user_id)
    encrypted_value = encrypt(value, enc_key)
    async with db_session(config) as db:
        # Upsert: try update first, insert if not exists
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
    enc_key = derive_server_key(config.server_secret, user_id)
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT value FROM credential_vault WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        result = await row.fetchone()
    if not result:
        return None
    value = result[0]
    return decrypt(value, enc_key) if value.startswith("enc:") else value


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
