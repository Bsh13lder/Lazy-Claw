"""Saved browser templates — reusable agent recipes.

Each template captures everything the agent needs to repeat a multi-step
browsing flow: setup URLs to open in tabs, a playbook with site-specific
instructions, named checkpoints the user must approve, and an optional
watch_extractor for zero-token slot polling.

Government-appointment use case:
- name: "Cita Previa Spain"
- setup_urls: ["https://sede.administracionespublicas.gob.es/..."]
- playbook: "Use NIE from vault. Pick first available slot in Madrid."
- checkpoints: ["Pick date", "Confirm booking"]
- watch_url + watch_extractor: poll for available slots without LLM cost
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

ENCRYPTED_FIELDS = frozenset({"system_prompt", "playbook"})

TEMPLATE_COLUMNS = [
    "id", "user_id", "name", "icon",
    "system_prompt", "setup_urls", "checkpoints", "playbook",
    "page_reader_mode",
    "watch_url", "watch_extractor", "watch_condition", "watch_job_id",
    "created_at", "updated_at",
]
SELECT_COLS = ", ".join(TEMPLATE_COLUMNS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encrypt(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    return encrypt(value, key)


def _row_to_dict(row, key: bytes) -> dict:
    out: dict = {}
    for i, col in enumerate(TEMPLATE_COLUMNS):
        v = row[i]
        if col in ENCRYPTED_FIELDS:
            v = decrypt_field(v, key)
        elif col in ("setup_urls", "checkpoints"):
            try:
                v = json.loads(v) if v else []
            except (json.JSONDecodeError, TypeError):
                v = []
        out[col] = v
    return out


# ── CRUD ──────────────────────────────────────────────────────────────────


async def create_template(
    config: Config,
    user_id: str,
    name: str,
    *,
    icon: str | None = None,
    system_prompt: str | None = None,
    setup_urls: list[str] | None = None,
    checkpoints: list[str] | None = None,
    playbook: str | None = None,
    page_reader_mode: str = "auto",
    watch_url: str | None = None,
    watch_extractor: str | None = None,
    watch_condition: str | None = None,
) -> dict:
    if not name or not name.strip():
        raise ValueError("Template name required")
    key = await get_user_dek(config, user_id)
    tpl_id = str(uuid4())
    now = _now()
    async with db_session(config) as db:
        await db.execute(
            f"INSERT INTO browser_templates ({SELECT_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tpl_id, user_id, name.strip(), icon,
                _encrypt(system_prompt, key),
                json.dumps(setup_urls or []),
                json.dumps(checkpoints or []),
                _encrypt(playbook, key),
                page_reader_mode,
                watch_url, watch_extractor, watch_condition, None,
                now, now,
            ),
        )
        await db.commit()
    logger.info("Created browser template %s ('%s') for user %s", tpl_id, name, user_id)
    return await get_template(config, user_id, tpl_id) or {}


async def get_template(config: Config, user_id: str, tpl_id: str) -> dict | None:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {SELECT_COLS} FROM browser_templates WHERE id = ? AND user_id = ?",
            (tpl_id, user_id),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row, key)


async def get_template_by_name(
    config: Config, user_id: str, name: str,
) -> dict | None:
    if not name:
        return None
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {SELECT_COLS} FROM browser_templates "
            "WHERE user_id = ? AND lower(name) = lower(?) LIMIT 1",
            (user_id, name.strip()),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row, key)


async def list_templates(config: Config, user_id: str) -> list[dict]:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {SELECT_COLS} FROM browser_templates "
            "WHERE user_id = ? ORDER BY name",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r, key) for r in rows]


async def update_template(
    config: Config, user_id: str, tpl_id: str, **fields,
) -> dict | None:
    if not fields:
        return await get_template(config, user_id, tpl_id)
    key = await get_user_dek(config, user_id)
    sets: list[str] = []
    values: list = []
    for col, val in fields.items():
        if col not in TEMPLATE_COLUMNS or col in ("id", "user_id", "created_at"):
            continue
        if col in ENCRYPTED_FIELDS:
            val = _encrypt(val, key) if val is not None else None
        elif col in ("setup_urls", "checkpoints"):
            val = json.dumps(val or [])
        sets.append(f"{col} = ?")
        values.append(val)
    if not sets:
        return await get_template(config, user_id, tpl_id)
    sets.append("updated_at = ?")
    values.append(_now())
    values.extend([tpl_id, user_id])
    async with db_session(config) as db:
        await db.execute(
            f"UPDATE browser_templates SET {', '.join(sets)} "
            "WHERE id = ? AND user_id = ?",
            values,
        )
        await db.commit()
    return await get_template(config, user_id, tpl_id)


async def delete_template(config: Config, user_id: str, tpl_id: str) -> bool:
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM browser_templates WHERE id = ? AND user_id = ?",
            (tpl_id, user_id),
        )
        await db.commit()
        return (cursor.rowcount or 0) > 0


# ── Hydration helper ──────────────────────────────────────────────────────


async def seed_examples(config: Config, user_id: str) -> list[dict]:
    """Install bundled example templates if the user has no templates yet.

    Idempotent — skips templates that already exist by name.
    Returns the list of templates created.
    """
    from pathlib import Path
    seed_path = Path(__file__).parent / "templates_seed.json"
    if not seed_path.exists():
        return []
    try:
        seeds = json.loads(seed_path.read_text())
    except Exception:
        logger.warning("Could not parse templates_seed.json", exc_info=True)
        return []
    created: list[dict] = []
    for s in seeds:
        existing = await get_template_by_name(config, user_id, s.get("name", ""))
        if existing is not None:
            continue
        try:
            tpl = await create_template(
                config, user_id,
                name=s["name"],
                icon=s.get("icon"),
                playbook=s.get("playbook"),
                setup_urls=s.get("setup_urls"),
                checkpoints=s.get("checkpoints"),
                watch_url=s.get("watch_url"),
                watch_extractor=s.get("watch_extractor"),
                watch_condition=s.get("watch_condition"),
                page_reader_mode=s.get("page_reader_mode", "auto"),
            )
            created.append(tpl)
        except Exception:
            logger.warning("Failed to seed template '%s'", s.get("name"), exc_info=True)
    return created


def build_run_instruction(template: dict, user_input: str | None) -> str:
    """Compose the agent prompt from a template + the user's request.

    Layout:
      [TEMPLATE: name]
      <playbook>
      Setup URLs: ...
      Checkpoints to confirm: ...
      [USER REQUEST]
      <user_input>
    """
    parts: list[str] = []
    parts.append(f"[TEMPLATE: {template['name']}]")
    if template.get("playbook"):
        parts.append(template["playbook"].strip())
    if template.get("setup_urls"):
        urls = ", ".join(template["setup_urls"])
        parts.append(f"Setup URLs to open first: {urls}")
    if template.get("checkpoints"):
        names = ", ".join(template["checkpoints"])
        parts.append(
            "Before each of these steps, call the request_user_approval skill "
            f"with the matching name: {names}."
        )
    if user_input and user_input.strip():
        parts.append("[USER REQUEST]")
        parts.append(user_input.strip())
    else:
        parts.append("Run the template flow above.")
    return "\n\n".join(parts)
