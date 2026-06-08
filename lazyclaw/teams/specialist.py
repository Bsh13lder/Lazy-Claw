"""Specialist definitions — config dataclass, built-in specialists, DB CRUD.

Each specialist has a name, system prompt, and a list of allowed skill names
that filter the main SkillRegistry. Built-in specialists are always available;
users can create custom specialists stored encrypted in the specialists table.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import decrypt, decrypt_field, encrypt
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


# ── Code Specialist workspace ─────────────────────────────────────────
#
# Every Code Specialist run claims its own folder under WORKSPACE_ROOT so
# the user can find the generated code on disk and the Web UI can link
# to it. Layout:
#
#   /workspace/<project_tag-or-untagged>/<goal_id-or-adhoc>/<task_id>/
#
# Bind-mounted from `~/Desktop/lazyclaw-workspace` on the host (see
# docker-compose.yml), so generated files appear directly on the user's
# Desktop and survive `docker compose down`. Per-task isolation means
# parallel Code Specialist runs never trample each other.
WORKSPACE_ROOT = os.environ.get("LAZYCLAW_WORKSPACE_ROOT", "/workspace")

# Path components are user-derivable (project_tag in particular). Sanitize
# aggressively so a malicious tag can't escape the mount or land on hidden
# files. Keep slugs short — long tags break `ls` output but don't break
# functionality; truncating prevents path-length errors on exFAT-mounted
# Windows hosts.
_SLUG_MAX = 64
_SLUG_SUB = re.compile(r"[^a-z0-9_-]+")
_LEADING_DOT_DASH = re.compile(r"^[._-]+")


def _slugify_for_path(value: str | None, fallback: str) -> str:
    """Lowercase + ASCII-safe slug for use as a path component.

    Strips path-traversal characters (``..``, leading ``/``), collapses
    runs of unsafe chars to ``_``, truncates to ``_SLUG_MAX``. Empty
    input yields ``fallback`` so callers always get a non-empty segment.
    """
    if not value:
        return fallback
    s = value.strip().lower().replace(":", "_").replace("/", "_")
    s = _SLUG_SUB.sub("_", s)
    s = _LEADING_DOT_DASH.sub("", s)
    s = s.strip("_-") or fallback
    return s[:_SLUG_MAX]


def code_workspace_dir(
    *,
    task_id: str,
    project_tag: str | None = None,
    goal_id: str | None = None,
    root: str = WORKSPACE_ROOT,
) -> str:
    """Resolve the persistent workspace path for a Code Specialist run.

    Pure function — does NOT create the directory; callers do that
    explicitly via ``os.makedirs(..., exist_ok=True)`` so tests can
    exercise resolution without filesystem side effects.
    """
    tag = _slugify_for_path(project_tag, "untagged")
    goal = _slugify_for_path(goal_id, "adhoc")
    tid = _slugify_for_path(task_id, "task")
    return os.path.join(root, tag, goal, tid)


@dataclass(frozen=True)
class SpecialistConfig:
    """Immutable specialist definition."""

    name: str
    display_name: str
    system_prompt: str
    allowed_skills: tuple[str, ...]
    preferred_model: str | None = None
    is_builtin: bool = False
    # When True, the runner unions every connected mcp-scraper tool into
    # the specialist's allowed set at execution time. Scraper tool names
    # are dynamic (`mcp_<server_uuid>_<toolname>`) so we can't enumerate
    # them in `allowed_skills`. Without this, browser/research specialists
    # fall back to opening Chrome for read-only contact-data work that
    # `extract_entities` would solve in one JS-rendered call.
    include_scraper: bool = False


# ── Built-in specialists ─────────────────────────────────────
#
# Definitions live as declarative `.md` files in `specialists/`
# (frontmatter + markdown body), loaded at import time. See ADR-0005 and
# `specialist_loader.py`. The named handles below stay because other modules
# import them directly (delegate, runner, executor, code_goal_executor).
from lazyclaw.teams.specialist_loader import load_builtin_specialists

BUILTIN_SPECIALISTS: tuple[SpecialistConfig, ...] = tuple(load_builtin_specialists())

_BUILTIN_BY_NAME = {s.name: s for s in BUILTIN_SPECIALISTS}
BROWSER_SPECIALIST = _BUILTIN_BY_NAME["browser_specialist"]
CODE_SPECIALIST = _BUILTIN_BY_NAME["code_specialist"]
RESEARCH_SPECIALIST = _BUILTIN_BY_NAME["research_specialist"]


def get_defaults() -> list[SpecialistConfig]:
    """Return the 3 built-in specialist configs."""
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

    key = await get_user_dek(config, user_id)
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
    key = await get_user_dek(config, user_id)

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

    key = await get_user_dek(config, user_id)

    # Find the record by decrypting names
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name FROM specialists WHERE user_id = ? AND is_builtin = 0",
            (user_id,),
        )
        all_rows = await rows.fetchall()

    target_id = None
    for row_id, name_enc in all_rows:
        decrypted = decrypt_field(name_enc, key)
        if decrypted == name:
            target_id = row_id
            break

    if not target_id:
        return False

    async with db_session(config) as db:
        await db.execute("DELETE FROM specialists WHERE id = ? AND user_id = ?", (target_id, user_id))
        await db.commit()

    logger.info("Deleted specialist '%s' for user %s", name, user_id)
    return True
