from __future__ import annotations

import json
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session
from lazyclaw.skills.instruction import InstructionSkill
from lazyclaw.skills.registry import SkillRegistry


async def create_instruction_skill(
    config: Config,
    user_id: str,
    name: str,
    description: str,
    instruction: str,
) -> str:
    """Create a new instruction skill and store it encrypted in the DB."""
    key = derive_server_key(config.server_secret, user_id)
    skill_id = str(uuid4())
    encrypted_name = encrypt(name, key)
    encrypted_instruction = encrypt(instruction, key)

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO skills (id, user_id, skill_type, name, description, instruction) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (skill_id, user_id, "instruction", encrypted_name, description, encrypted_instruction),
        )
        await db.commit()
    return skill_id


async def list_user_skills(config: Config, user_id: str) -> list[dict]:
    """List all skills for a user (decrypted names)."""
    key = derive_server_key(config.server_secret, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, description, skill_type FROM skills "
            "WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        )
        results = await rows.fetchall()

    skills = []
    for row in results:
        enc_name = row[1]
        decrypted_name = decrypt(enc_name, key) if enc_name.startswith("enc:") else enc_name
        skills.append({
            "id": row[0],
            "name": decrypted_name,
            "description": row[2],
            "type": row[3],
        })
    return skills


async def delete_user_skill(config: Config, user_id: str, skill_name: str) -> bool:
    """Delete a user skill by name."""
    key = derive_server_key(config.server_secret, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name FROM skills WHERE user_id = ?",
            (user_id,),
        )
        results = await rows.fetchall()

    # Find the skill by decrypted name
    target_id = None
    for row in results:
        enc_name = row[1]
        decrypted = decrypt(enc_name, key) if enc_name.startswith("enc:") else enc_name
        if decrypted == skill_name:
            target_id = row[0]
            break

    if not target_id:
        return False

    async with db_session(config) as db:
        await db.execute("DELETE FROM skills WHERE id = ?", (target_id,))
        await db.commit()
    return True


async def load_user_skills(
    config: Config,
    user_id: str,
    registry: SkillRegistry,
) -> int:
    """Load all user instruction skills from DB into the registry. Returns count loaded."""
    key = derive_server_key(config.server_secret, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT name, description, instruction FROM skills "
            "WHERE user_id = ? AND skill_type = 'instruction'",
            (user_id,),
        )
        results = await rows.fetchall()

    count = 0
    for row in results:
        enc_name, description, enc_instruction = row[0], row[1], row[2]
        name = decrypt(enc_name, key) if enc_name.startswith("enc:") else enc_name
        instruction = decrypt(enc_instruction, key) if enc_instruction.startswith("enc:") else enc_instruction

        skill = InstructionSkill(
            skill_name=name,
            skill_description=description,
            instruction=instruction,
        )
        registry.register(skill)
        count += 1
    return count
