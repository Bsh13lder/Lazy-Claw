from __future__ import annotations

import json
import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.skills.instruction import InstructionSkill
from lazyclaw.skills.registry import SkillRegistry
from lazyclaw.skills.sandbox import CodeSkill

logger = logging.getLogger(__name__)


async def create_instruction_skill(
    config: Config,
    user_id: str,
    name: str,
    description: str,
    instruction: str,
) -> str:
    """Create a new instruction skill and store it encrypted in the DB."""
    key = await get_user_dek(config, user_id)
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


async def create_code_skill(
    config: Config,
    user_id: str,
    name: str,
    description: str,
    code: str,
    parameters_schema: dict | None = None,
) -> str:
    """Create a new code skill and store it encrypted in the DB."""
    key = await get_user_dek(config, user_id)
    skill_id = str(uuid4())
    encrypted_name = encrypt(name, key)
    encrypted_code = encrypt(code, key)
    schema_json = json.dumps(parameters_schema) if parameters_schema else None

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO skills (id, user_id, skill_type, name, description, code, parameters_schema) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (skill_id, user_id, "code", encrypted_name, description, encrypted_code, schema_json),
        )
        await db.commit()
    return skill_id


async def get_skill_by_id(config: Config, user_id: str, skill_id: str) -> dict | None:
    """Get a skill by ID, scoped to user."""
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id, name, description, skill_type, instruction, code, parameters_schema "
            "FROM skills WHERE id = ? AND user_id = ?",
            (skill_id, user_id),
        )
        result = await row.fetchone()

    if not result:
        return None

    name = decrypt_field(result[1], key)
    instruction = decrypt_field(result[4], key)
    code = decrypt_field(result[5], key)

    return {
        "id": result[0],
        "name": name,
        "description": result[2],
        "type": result[3],
        "instruction": instruction,
        "code": code,
        "parameters_schema": json.loads(result[6]) if result[6] else None,
    }


async def update_skill(config: Config, user_id: str, skill_id: str, **fields) -> bool:
    """Update skill fields. Encrypts name, instruction, and code."""
    key = await get_user_dek(config, user_id)

    # Map field names to encrypted values
    updates = {}
    for field, value in fields.items():
        if field == "name":
            updates["name"] = encrypt(value, key)
        elif field == "instruction":
            updates["instruction"] = encrypt(value, key)
        elif field == "code":
            updates["code"] = encrypt(value, key)
        elif field == "description":
            updates["description"] = value
        elif field == "parameters_schema":
            updates["parameters_schema"] = json.dumps(value) if isinstance(value, dict) else value

    if not updates:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [skill_id, user_id]

    async with db_session(config) as db:
        cursor = await db.execute(
            f"UPDATE skills SET {set_clause} WHERE id = ? AND user_id = ?",
            values,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_user_skill_by_id(config: Config, user_id: str, skill_id: str) -> bool:
    """Delete a user skill by ID."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM skills WHERE id = ? AND user_id = ?",
            (skill_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_user_skills(config: Config, user_id: str) -> list[dict]:
    """List all skills for a user (decrypted names)."""
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, description, skill_type FROM skills "
            "WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        )
        results = await rows.fetchall()

    skills = []
    for row in results:
        decrypted_name = decrypt_field(row[1], key)
        skills.append({
            "id": row[0],
            "name": decrypted_name,
            "description": row[2],
            "type": row[3],
        })
    return skills


async def delete_user_skill(config: Config, user_id: str, skill_name: str) -> bool:
    """Delete a user skill by name."""
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name FROM skills WHERE user_id = ?",
            (user_id,),
        )
        results = await rows.fetchall()

    # Find the skill by decrypted name
    target_id = None
    for row in results:
        decrypted = decrypt_field(row[1], key)
        if decrypted == skill_name:
            target_id = row[0]
            break

    if not target_id:
        return False

    async with db_session(config) as db:
        await db.execute("DELETE FROM skills WHERE id = ? AND user_id = ?", (target_id, user_id))
        await db.commit()
    return True


async def load_user_skills(
    config: Config,
    user_id: str,
    registry: SkillRegistry,
) -> int:
    """Load all user skills (instruction + code) from DB into the registry. Returns count loaded."""
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT name, description, instruction, skill_type, code, parameters_schema "
            "FROM skills WHERE user_id = ?",
            (user_id,),
        )
        results = await rows.fetchall()

    count = 0
    for row in results:
        enc_name, description, enc_instruction, skill_type, enc_code, params_schema = (
            row[0], row[1], row[2], row[3], row[4], row[5],
        )
        name = decrypt_field(enc_name, key)

        if skill_type == "instruction" and enc_instruction:
            instruction = decrypt_field(enc_instruction, key)
            skill = InstructionSkill(
                skill_name=name,
                skill_description=description,
                instruction=instruction,
            )
        elif skill_type == "code" and enc_code:
            code = decrypt_field(enc_code, key)
            schema = json.loads(params_schema) if params_schema else None
            skill = CodeSkill(
                skill_name=name,
                skill_description=description,
                code=code,
                params_schema=schema,
            )
        else:
            logger.warning("Skipping skill '%s' with unknown type '%s'", name, skill_type)
            continue

        registry.register(skill)
        count += 1
    return count
