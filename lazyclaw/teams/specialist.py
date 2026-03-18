"""Specialist definitions — config dataclass, built-in specialists, DB CRUD.

Each specialist has a name, system prompt, and a list of allowed skill names
that filter the main SkillRegistry. Built-in specialists are always available;
users can create custom specialists stored encrypted in the specialists table.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpecialistConfig:
    """Immutable specialist definition."""

    name: str
    display_name: str
    system_prompt: str
    allowed_skills: tuple[str, ...]
    preferred_model: str | None = None
    is_builtin: bool = False


# ── Built-in specialists ──────────────────────────────────────────────

BROWSER_SPECIALIST = SpecialistConfig(
    name="browser_specialist",
    display_name="Browser Specialist",
    system_prompt=(
        "You are a browser automation specialist. Your expertise is navigating websites, "
        "reading page content, filling forms, and extracting information from web pages. "
        "Focus on completing the browsing task efficiently. Report what you found clearly "
        "and concisely. If a page requires login, use saved site credentials."
    ),
    allowed_skills=("browse_web", "read_page", "save_site_login"),
    preferred_model="gpt-5-mini",
    is_builtin=True,
)

CODE_SPECIALIST = SpecialistConfig(
    name="code_specialist",
    display_name="Code Specialist",
    system_prompt=(
        "You are a code and skill development specialist. Your expertise is writing Python code, "
        "creating new skills, debugging logic, and performing calculations. Focus on producing "
        "clean, working code. Explain your approach briefly, then deliver the implementation."
    ),
    allowed_skills=("calculate", "create_skill", "list_skills", "delete_skill"),
    preferred_model="gpt-5-mini",
    is_builtin=True,
)

RESEARCH_SPECIALIST = SpecialistConfig(
    name="research_specialist",
    display_name="Research Specialist",
    system_prompt=(
        "You are a research and information gathering specialist. Your expertise is searching "
        "the web, reading local files, listing directories, and synthesizing findings into "
        "clear summaries. For local files use read_file/list_directory, for web use web_search/"
        "read_page. Be thorough but concise. Cite sources when possible."
    ),
    allowed_skills=(
        "web_search", "read_page", "read_file", "list_directory", "run_command",
    ),
    preferred_model="gpt-5-mini",
    is_builtin=True,
)

MEMORY_SPECIALIST = SpecialistConfig(
    name="memory_specialist",
    display_name="Memory Specialist",
    system_prompt=(
        "You are a memory and knowledge management specialist. Your expertise is recalling "
        "stored facts, saving important information, and checking credentials in the vault. "
        "Be precise about what you find. Clearly distinguish between stored facts and "
        "inferences. Report what is and isn't in memory."
    ),
    allowed_skills=("memory_save", "memory_recall", "vault_list"),
    preferred_model="gpt-5-mini",
    is_builtin=True,
)

BUILTIN_SPECIALISTS = (
    BROWSER_SPECIALIST,
    CODE_SPECIALIST,
    RESEARCH_SPECIALIST,
    MEMORY_SPECIALIST,
)


def get_defaults() -> list[SpecialistConfig]:
    """Return the 4 built-in specialist configs."""
    return list(BUILTIN_SPECIALISTS)


# ── User-defined specialist CRUD ──────────────────────────────────────


async def save_specialist(
    config: Config, user_id: str, specialist: SpecialistConfig
) -> str:
    """Save a custom specialist to the DB. Returns the record ID."""
    if specialist.is_builtin:
        raise ValueError("Cannot save a built-in specialist")

    # Check for name collision with built-ins
    builtin_names = {s.name for s in BUILTIN_SPECIALISTS}
    if specialist.name in builtin_names:
        raise ValueError(f"Name '{specialist.name}' conflicts with a built-in specialist")

    key = derive_server_key(config.server_secret, user_id)
    record_id = str(uuid4())

    encrypted_name = encrypt(specialist.name, key)
    encrypted_display = encrypt(specialist.display_name, key)
    encrypted_prompt = encrypt(specialist.system_prompt, key)
    skills_json = json.dumps(list(specialist.allowed_skills))

    async with db_session(config) as db:
        # Upsert: delete existing with same name, then insert
        existing = await db.execute(
            "SELECT id FROM specialists WHERE user_id = ? AND name = ?",
            (user_id, encrypted_name),
        )
        row = await existing.fetchone()
        if row:
            record_id = row[0]
            await db.execute(
                "UPDATE specialists SET display_name = ?, system_prompt = ?, "
                "allowed_skills = ?, preferred_model = ? WHERE id = ?",
                (encrypted_display, encrypted_prompt, skills_json,
                 specialist.preferred_model, record_id),
            )
        else:
            await db.execute(
                "INSERT INTO specialists "
                "(id, user_id, name, display_name, system_prompt, allowed_skills, "
                "preferred_model, is_builtin) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (record_id, user_id, encrypted_name, encrypted_display,
                 encrypted_prompt, skills_json, specialist.preferred_model),
            )
        await db.commit()

    logger.info("Saved specialist '%s' for user %s", specialist.name, user_id)
    return record_id


async def load_specialists(config: Config, user_id: str) -> list[SpecialistConfig]:
    """Load all specialists: built-in + user-defined (decrypted)."""
    result = list(BUILTIN_SPECIALISTS)
    key = derive_server_key(config.server_secret, user_id)

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT name, display_name, system_prompt, allowed_skills, "
            "preferred_model FROM specialists WHERE user_id = ? AND is_builtin = 0",
            (user_id,),
        )
        user_rows = await rows.fetchall()

    for name_enc, display_enc, prompt_enc, skills_json, pref_model in user_rows:
        try:
            name = decrypt(name_enc, key) if name_enc.startswith("enc:") else name_enc
            display = decrypt(display_enc, key) if display_enc.startswith("enc:") else display_enc
            prompt = decrypt(prompt_enc, key) if prompt_enc.startswith("enc:") else prompt_enc
            skills = tuple(json.loads(skills_json))

            result.append(SpecialistConfig(
                name=name,
                display_name=display,
                system_prompt=prompt,
                allowed_skills=skills,
                preferred_model=pref_model,
                is_builtin=False,
            ))
        except Exception as exc:
            logger.warning("Failed to load specialist: %s", exc)

    return result


async def get_specialist(
    config: Config, user_id: str, name: str
) -> SpecialistConfig | None:
    """Get a single specialist by name."""
    # Check built-ins first
    for s in BUILTIN_SPECIALISTS:
        if s.name == name:
            return s

    # Check user-defined
    all_specs = await load_specialists(config, user_id)
    for s in all_specs:
        if s.name == name:
            return s
    return None


async def delete_specialist(config: Config, user_id: str, name: str) -> bool:
    """Delete a custom specialist. Returns True if deleted."""
    # Prevent deleting built-ins
    if any(s.name == name for s in BUILTIN_SPECIALISTS):
        raise ValueError("Cannot delete a built-in specialist")

    key = derive_server_key(config.server_secret, user_id)

    # Find the record by decrypting names
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name FROM specialists WHERE user_id = ? AND is_builtin = 0",
            (user_id,),
        )
        all_rows = await rows.fetchall()

    target_id = None
    for row_id, name_enc in all_rows:
        decrypted = decrypt(name_enc, key) if name_enc.startswith("enc:") else name_enc
        if decrypted == name:
            target_id = row_id
            break

    if not target_id:
        return False

    async with db_session(config) as db:
        await db.execute("DELETE FROM specialists WHERE id = ?", (target_id,))
        await db.commit()

    logger.info("Deleted specialist '%s' for user %s", name, user_id)
    return True
