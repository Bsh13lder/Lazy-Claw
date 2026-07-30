"""Budget storage: encrypted CRUD for projects, expenses, recurring rules.

Pattern cloned from ``pipeline/store.py`` (encrypted fields + plaintext amount)
and ``tasks/store.py`` (fire-and-forget LazyBrain mirroring). A project is keyed
by ``name_key`` (casefolded name) which matches ``tasks.category`` so tasks
auto-associate without a migration. Expenses roll up via a plaintext
``SUM(amount)`` — no decrypt loop.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENCRYPTED_PROJECT_FIELDS = frozenset({"name", "description"})
ENCRYPTED_EXPENSE_FIELDS = frozenset({"description", "vendor", "notes"})
ENCRYPTED_RECUR_FIELDS = frozenset({"description", "vendor"})
ENCRYPTED_BUDGET_ENTRY_FIELDS = frozenset({"source"})

PROJECT_COLUMNS = [
    "id", "user_id", "name", "name_key", "budget", "currency",
    "status", "description",
    # Per-project color (feat/flutter-mobile) — optional hex string like
    # "#4F8AF4" (or NULL) so the mobile calendar can color-code projects +
    # tasks. Plaintext like status/budget (not sensitive).
    "color",
    # Per-project favorite flag (feat/flutter-mobile) — INTEGER 0/1, plaintext.
    # Pins a project into the mobile Home "Favorites" section. Default 0.
    "is_favorite",
    # Project time frame (feat/task-timeframes 2026-07-30) — plaintext
    # YYYY-MM-DD strings (or NULL) so clients can show "due Aug 12" on a
    # project. Same lenient-normalize profile as color.
    "start_date",
    "due_date",
    "lazybrain_note_id", "created_at", "updated_at",
    # Offline-sync column (feat/flutter-mobile) — soft-delete tombstone.
    # NULL = live; non-NULL = deleted. Exposed via /api/budgets/changes.
    "deleted_at",
]

EXPENSE_COLUMNS = [
    "id", "user_id", "project_id", "task_id", "amount", "currency",
    "description", "vendor", "notes", "spent_at", "status",
    "recurring_expense_id", "lazybrain_note_id", "created_at", "updated_at",
    # Per-expense favorite flag (star) — plaintext INTEGER 0/1, default 0,
    # serialized to clients as a JSON bool. Powers the "starred only" overview.
    "is_favorite",
    # Offline-sync column (feat/flutter-mobile) — soft-delete tombstone.
    "deleted_at",
]

RECUR_COLUMNS = [
    "id", "user_id", "project_id", "task_id", "amount", "currency",
    "description", "vendor", "cron_expression", "job_id", "next_run",
    "last_charged_at", "status", "created_at", "updated_at",
]

BUDGET_ENTRY_COLUMNS = [
    "id", "user_id", "project_id", "amount", "currency", "source",
    "kind", "created_at",
    # Offline-sync primitives — updated_at drives the /changes delta feed;
    # deleted_at is the soft-delete tombstone (NULL = live).
    "updated_at", "deleted_at",
]

PROJECT_SELECT = ", ".join(PROJECT_COLUMNS)
EXPENSE_SELECT = ", ".join(EXPENSE_COLUMNS)
RECUR_SELECT = ", ".join(RECUR_COLUMNS)
BUDGET_ENTRY_SELECT = ", ".join(BUDGET_ENTRY_COLUMNS)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _name_key(name: str) -> str:
    """Casefold + collapse whitespace so a project resolves consistently and
    matches ``tasks.category`` regardless of casing."""
    return " ".join((name or "").strip().casefold().split())


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _clean_color(value: str | None) -> str | None:
    """Normalize a project color to a 6-digit hex string like ``"#4F8AF4"``.

    Lenient by design: anything that isn't a valid ``#RRGGBB`` string (bad
    shape, empty, non-string) is cleared to ``None`` rather than raising — a
    bad color must never 500 a project write. The original case is preserved.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _HEX_COLOR_RE.match(candidate) else None


def _clean_project_date(value: str | None) -> str | None:
    """Normalize a project start/due date to ``YYYY-MM-DD`` (or ``None``).

    Lenient like ``_clean_color``: anything that isn't a parseable ISO date is
    cleared to ``None`` rather than raising — a bad date must never 500 a
    project write. A full datetime is truncated to its date part.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    from datetime import datetime as _dt

    try:
        return _dt.fromisoformat(candidate).date().isoformat()
    except (ValueError, TypeError):
        return None


def _clean_favorite(value) -> int:
    """Normalize a favorite flag to a stored INTEGER 0/1.

    Lenient like ``_clean_color``: any truthy value → 1, anything else → 0, so a
    bad payload can never 500 a project write. Stored as int (not bool) so the
    column stays a plain SQLite INTEGER like ``status``/``budget``.
    """
    return 1 if value else 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enc(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    return encrypt(value, key)


def _row_to_dict(row, columns: list[str], encrypted: frozenset[str], key: bytes) -> dict:
    result: dict = {}
    for i, col in enumerate(columns):
        value = row[i]
        if col in encrypted:
            value = decrypt_field(value, key)
        result[col] = value
    return result


def _project_to_dict(row, key: bytes) -> dict:
    result = _row_to_dict(row, PROJECT_COLUMNS, ENCRYPTED_PROJECT_FIELDS, key)
    # Serialize the stored INTEGER 0/1 as a JSON boolean so the web/mobile
    # clients get a real ``true``/``false`` (NULL on a pre-migration row → False).
    if "is_favorite" in result:
        result["is_favorite"] = bool(result["is_favorite"])
    return result


def _expense_to_dict(row, key: bytes) -> dict:
    result = _row_to_dict(row, EXPENSE_COLUMNS, ENCRYPTED_EXPENSE_FIELDS, key)
    # Serialize the stored INTEGER 0/1 as a JSON boolean (NULL on a pre-migration
    # row → False), exactly like ``_project_to_dict``.
    if "is_favorite" in result:
        result["is_favorite"] = bool(result["is_favorite"])
    return result


def _recur_to_dict(row, key: bytes) -> dict:
    return _row_to_dict(row, RECUR_COLUMNS, ENCRYPTED_RECUR_FIELDS, key)


def _budget_entry_to_dict(row, key: bytes) -> dict:
    return _row_to_dict(row, BUDGET_ENTRY_COLUMNS, ENCRYPTED_BUDGET_ENTRY_FIELDS, key)


# ---------------------------------------------------------------------------
# LazyBrain mirroring (fire-and-forget — an expense must never fail on PKM)
# ---------------------------------------------------------------------------


def _project_note_title(name: str) -> str:
    return f"{name} Project"


async def _ensure_project_note(
    config: Config, user_id: str, name: str, budget: float, currency: str,
    description: str | None = None,
) -> str | None:
    """Create the ``<Name> Project`` note that wikilinks resolve to. Returns
    the note id, or None on failure (never raises).

    The note body leads with the project's ``description`` (the human-readable
    "what this project is") when present, so the wikilink target is a real page
    — not just a budget line. Tasks and expenses wikilink back here.
    """
    try:
        from lazyclaw.lazybrain import store as lb_store

        lines = [f"# {name} Project", ""]
        if description and description.strip():
            lines += [description.strip(), ""]
        lines += [
            f"Budget: {budget} {currency}",
            "",
            "Tasks and expenses tagged with this project link here.",
        ]
        note = await lb_store.save_note(
            config,
            user_id,
            content="\n".join(lines),
            title=_project_note_title(name),
            tags=["project", f"project/{_name_key(name)}", "kind/budget"],
            importance=7,
            memory_type="project",
        )
        return note["id"]
    except Exception:
        logger.warning(
            "lazybrain project note mirror failed for %s (user=%s)",
            name, user_id, exc_info=True,
        )
        return None


async def _write_expense_note(
    config: Config,
    user_id: str,
    *,
    project_name: str,
    amount: float,
    currency: str,
    description: str | None,
    vendor: str | None,
    spent_at: str | None,
    task_title: str | None,
) -> str | None:
    """Mirror an expense to a LazyBrain note wikilinking the project. Returns
    the note id, or None on failure (never raises)."""
    try:
        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store

        lines = [f"**Expense:** {amount} {currency}"]
        if description:
            lines[0] += f" — {description}"
        if vendor:
            lines.append(f"Vendor: {vendor}")
        lines.append(f"Project: [[{_project_note_title(project_name)}]]")
        if task_title:
            lines.append(f"Task: {task_title}")
        if spent_at:
            lines.append(f"_spent {spent_at}_")
        body = "\n".join(lines)

        title_label = description or vendor or f"{amount} {currency}"
        note = await lb_store.save_note(
            config,
            user_id,
            content=body,
            title=f"Expense: {title_label} ({project_name})",
            tags=["expense", "auto", "kind/expense", f"project/{_name_key(project_name)}"],
            importance=4,
            memory_type="project",
        )
        lb_events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"], source="expense",
        )
        return note["id"]
    except Exception:
        logger.warning(
            "lazybrain expense note mirror failed (user=%s project=%s)",
            user_id, project_name, exc_info=True,
        )
        return None


async def _delete_note(config: Config, user_id: str, note_id: str | None) -> None:
    if not note_id:
        return
    try:
        from lazyclaw.lazybrain import store as lb_store

        await lb_store.delete_note(config, user_id, note_id)
    except Exception:
        logger.debug("lazybrain note delete skipped (%s)", note_id, exc_info=True)


# ---------------------------------------------------------------------------
# Projects CRUD
# ---------------------------------------------------------------------------


async def _merge_into_existing_project(
    config: Config,
    user_id: str,
    existing: dict,
    *,
    name: str,
    budget: float,
    currency: str,
    description: str | None,
    color: str | None,
    is_favorite: bool | None,
    start_date: str | None = None,
    due_date: str | None = None,
) -> dict:
    """Idempotent upsert into an already-existing project (resolved by its
    ``name_key``). Only explicitly-provided, non-empty fields are applied so a
    replay of an offline create never clobbers a richer server row with empty
    defaults (e.g. an offline ``budget=0`` must not zero a web-set budget).
    Returns the merged project dict."""
    updates: dict = {}
    if budget:
        updates["budget"] = budget
    if currency and currency != existing.get("currency"):
        updates["currency"] = currency
    if description is not None:
        updates["description"] = description
    if color is not None:
        updates["color"] = color
    if is_favorite is not None:
        updates["is_favorite"] = is_favorite
    if start_date is not None:
        updates["start_date"] = start_date
    if due_date is not None:
        updates["due_date"] = due_date
    if not existing.get("lazybrain_note_id"):
        note_id = await _ensure_project_note(
            config, user_id, name,
            budget or existing.get("budget") or 0.0, currency,
            description=(
                description if description is not None
                else existing.get("description")
            ),
        )
        if note_id:
            updates["lazybrain_note_id"] = note_id
    if updates:
        await update_project(config, user_id, existing["id"], **updates)
        return {**existing, **updates}
    return existing


async def create_project(
    config: Config,
    user_id: str,
    name: str,
    *,
    budget: float = 0.0,
    currency: str = "EUR",
    description: str | None = None,
    color: str | None = None,
    is_favorite: bool | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Create or upsert a project by ``name_key``. Idempotent against
    category-typo duplicates (case/whitespace). If the project already exists,
    its budget/currency/description/color/is_favorite are updated when
    explicitly provided. Creates the ``<Name> Project`` LazyBrain note on first
    insert.

    ``color``: optional ``#RRGGBB`` hex string (or None) for calendar
    color-coding. Invalid values are cleared to None (never raises).

    ``is_favorite``: optional bool pinning the project into the mobile Home
    "Favorites" section. ``None`` means "leave as-is" on an upsert; a fresh
    insert defaults to un-favorited.

    ``project_id``: optional client-minted id for offline-first idempotent
    replay. When provided, the server uses it as the project id. A second POST
    with the same id returns the existing project without duplicating it.
    """
    if not name or not name.strip():
        raise ValueError("project name required")

    key = await get_user_dek(config, user_id)
    name_key = _name_key(name)
    color = _clean_color(color)
    start_date = _clean_project_date(start_date)
    due_date = _clean_project_date(due_date)

    # Idempotent replay: if the client-minted id already exists for this user,
    # return the existing row without inserting a duplicate.
    if project_id:
        async with db_session(config) as db:
            cur = await db.execute(
                f"SELECT {PROJECT_SELECT} FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            )
            existing_by_id = await cur.fetchone()
        if existing_by_id is not None:
            return _project_to_dict(existing_by_id, key)

    # Name-key merge (idempotent). Runs even when a client id was supplied but
    # no row owns that id yet: a project whose casefolded name already exists
    # under a DIFFERENT id (e.g. created on web) must merge into that row, not
    # hit the UNIQUE(user_id, name_key) index on INSERT and 500 forever — the
    # bug that stranded offline mobile projects + all their expenses.
    existing = await get_project_by_name(config, user_id, name)
    if existing:
        return await _merge_into_existing_project(
            config, user_id, existing,
            name=name, budget=budget, currency=currency,
            description=description, color=color, is_favorite=is_favorite,
            start_date=start_date, due_date=due_date,
        )

    project_id = project_id or str(uuid4())
    now = _now()
    note_id = await _ensure_project_note(
        config, user_id, name, budget, currency, description=description,
    )

    favorite_int = _clean_favorite(is_favorite)
    try:
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO projects "
                "(id, user_id, name, name_key, budget, currency, status, "
                "description, color, is_favorite, start_date, due_date, "
                "lazybrain_note_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id, user_id, encrypt(name, key), name_key,
                    budget, currency, _enc(description, key), color, favorite_int,
                    start_date, due_date,
                    note_id, now, now,
                ),
            )
            await db.commit()
    except sqlite3.IntegrityError:
        # Belt-and-suspenders: a concurrent create won the name_key race
        # between the pre-check above and this INSERT. Re-resolve by name (user
        # isolation preserved by get_project_by_name) and merge instead of
        # surfacing a 500.
        collided = await get_project_by_name(config, user_id, name)
        if collided is not None:
            return await _merge_into_existing_project(
                config, user_id, collided,
                name=name, budget=budget, currency=currency,
                description=description, color=color, is_favorite=is_favorite,
                start_date=start_date, due_date=due_date,
            )
        raise

    logger.debug("Created project %s (%s) for user %s", project_id, name_key, user_id)
    return {
        "id": project_id, "user_id": user_id, "name": name, "name_key": name_key,
        "budget": budget, "currency": currency, "status": "active",
        "description": description, "color": color,
        "is_favorite": bool(favorite_int),
        "start_date": start_date, "due_date": due_date,
        "lazybrain_note_id": note_id,
        "created_at": now, "updated_at": now, "deleted_at": None,
    }


async def get_project(config: Config, user_id: str, project_id: str) -> dict | None:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {PROJECT_SELECT} FROM projects "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (project_id, user_id),
        )
        row = await cursor.fetchone()
    return _project_to_dict(row, key) if row else None


async def get_project_by_name(config: Config, user_id: str, name: str) -> dict | None:
    """Resolve a project by its ``name_key`` (used by auto-detect + NL skills)."""
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {PROJECT_SELECT} FROM projects "
            "WHERE user_id = ? AND name_key = ? AND deleted_at IS NULL",
            (user_id, _name_key(name)),
        )
        row = await cursor.fetchone()
    return _project_to_dict(row, key) if row else None


async def _spent_by_project(config: Config, user_id: str) -> dict[str, float]:
    """Single grouped plaintext SUM — no decrypt. {project_id: spent}."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT project_id, COALESCE(SUM(amount), 0) FROM project_expenses "
            "WHERE user_id = ? AND status = 'posted' AND deleted_at IS NULL "
            "GROUP BY project_id",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return {r[0]: float(r[1] or 0) for r in rows}


async def list_projects(
    config: Config, user_id: str, *, status: str | None = None
) -> list[dict]:
    """List projects with rolled-up ``spent`` + ``remaining`` (plaintext SUM)."""
    key = await get_user_dek(config, user_id)
    where = "user_id = ? AND deleted_at IS NULL"
    params: list = [user_id]
    if status:
        where += " AND status = ?"
        params.append(status)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {PROJECT_SELECT} FROM projects "
            f"WHERE {where} ORDER BY updated_at DESC",
            params,
        )
        rows = await cursor.fetchall()

    spent_map = await _spent_by_project(config, user_id)
    projects: list[dict] = []
    for row in rows:
        proj = _project_to_dict(row, key)
        spent = spent_map.get(proj["id"], 0.0)
        budget = float(proj.get("budget") or 0.0)
        projects.append({**proj, "spent": spent, "remaining": budget - spent})
    return projects


async def set_budget(
    config: Config, user_id: str, project_id: str,
    budget: float, currency: str | None = None,
) -> bool:
    """Set the total project budget. Records an audit ledger entry capturing
    the delta + the before→after values, so Edit-budget changes are tracked
    just like top-ups (visible in the project Log; rolls back on entry delete)."""
    project = await get_project(config, user_id, project_id)
    if project is None:
        return False
    old = float(project.get("budget") or 0.0)
    new = float(budget)
    fields: dict = {"budget": new}
    if currency:
        fields["currency"] = currency
    ok = await update_project(config, user_id, project_id, **fields)
    if not ok:
        return False

    delta = new - old
    if delta != 0:
        eff_currency = currency or project.get("currency") or "EUR"
        # `_fmt` keeps trailing zeros clean for the human-readable audit note.
        def _fmt(v: float) -> str:
            return f"{v:.2f}".rstrip("0").rstrip(".")
        source = f"edit: {_fmt(old)} → {_fmt(new)} {eff_currency}"
        try:
            await _insert_budget_entry(
                config, user_id, project_id,
                amount=delta, source=source, currency=eff_currency, kind="edit",
            )
        except Exception:
            logger.warning(
                "set_budget audit entry failed for project %s",
                project_id, exc_info=True,
            )
    return True


async def _insert_budget_entry(
    config: Config, user_id: str, project_id: str, *,
    amount: float, source: str | None, currency: str, kind: str,
    entry_id: str | None = None,
) -> dict:
    """Low-level ledger row insert. Caller is responsible for any
    project.budget adjustment — keep ``add_budget_entry`` as the public
    bump-and-record API for top-ups.

    ``entry_id``: optional client-minted id for offline-first idempotent
    replay. When omitted a fresh uuid4 is minted."""
    key = await get_user_dek(config, user_id)
    entry_id = entry_id or str(uuid4())
    now = _now()
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO budget_entries "
            "(id, user_id, project_id, amount, currency, source, kind, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, user_id, project_id, amount, currency,
             _enc(source, key), kind, now, now),
        )
        await db.commit()
    return {
        "id": entry_id, "user_id": user_id, "project_id": project_id,
        "amount": amount, "currency": currency, "source": source,
        "kind": kind, "created_at": now, "updated_at": now, "deleted_at": None,
    }


async def update_project(
    config: Config, user_id: str, project_id: str, **fields
) -> bool:
    """Update project fields. Recomputes ``name_key`` + renames the LazyBrain
    note when ``name`` changes so wikilinks re-resolve via the new title.

    A ``color`` field is normalized to ``#RRGGBB`` (invalid → NULL) so a bad
    color clears instead of 500-ing the update. An ``is_favorite`` field is
    normalized to a stored INTEGER 0/1."""
    if not fields:
        return False

    # Normalize color without mutating the caller's dict (immutable update).
    if "color" in fields:
        fields = {**fields, "color": _clean_color(fields["color"])}
    # Normalize the favorite flag to a stored INTEGER 0/1 (immutable update).
    if "is_favorite" in fields:
        fields = {**fields, "is_favorite": _clean_favorite(fields["is_favorite"])}
    # Normalize time-frame dates (immutable update); invalid/empty clears.
    if "start_date" in fields:
        fields = {**fields, "start_date": _clean_project_date(fields["start_date"])}
    if "due_date" in fields:
        fields = {**fields, "due_date": _clean_project_date(fields["due_date"])}

    key = await get_user_dek(config, user_id)
    set_clauses: list[str] = []
    params: list = []

    new_name = fields.get("name")
    # Capture the old name BEFORE the UPDATE so we can re-point tasks.category
    # (the join key) when the project is renamed.
    old_name: str | None = None
    if new_name:
        prior = await get_project(config, user_id, project_id)
        old_name = prior.get("name") if prior else None
    for col, value in fields.items():
        if col in ENCRYPTED_PROJECT_FIELDS and value is not None:
            value = encrypt(value, key)
        set_clauses.append(f"{col} = ?")
        params.append(value)

    if new_name:
        set_clauses.append("name_key = ?")
        params.append(_name_key(new_name))

    set_clauses.append("updated_at = ?")
    params.append(_now())
    params.extend([project_id, user_id])

    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE projects SET {', '.join(set_clauses)} "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
        updated = result.rowcount > 0

    if updated and new_name:
        # Rename the project note title so [[New Name Project]] resolves.
        proj = await get_project(config, user_id, project_id)
        note_id = proj.get("lazybrain_note_id") if proj else None
        if note_id:
            try:
                from lazyclaw.lazybrain import store as lb_store

                await lb_store.update_note(
                    config, user_id, note_id,
                    title=_project_note_title(new_name),
                )
            except Exception:
                logger.debug("project note rename skipped", exc_info=True)
        # Re-point every task that referenced the old project name so the
        # tasks.category ⇄ projects.name_key join survives the rename. Without
        # this, renamed projects orphan all their tasks.
        if old_name and old_name.strip().casefold() != new_name.strip().casefold():
            try:
                from lazyclaw.tasks import store as task_store

                await task_store.rename_category(
                    config, user_id, old_name, new_name,
                )
            except Exception:
                logger.debug("task category re-point skipped", exc_info=True)
    return updated


async def delete_project(
    config: Config, user_id: str, project_id: str, *, cascade: bool = False
) -> bool:
    """Soft-delete a project. Sets ``deleted_at`` on the project row instead of
    removing it so offline clients can learn of the delete via /changes.

    Refuses (returns False) if the project has live expenses unless
    ``cascade=True`` — then soft-deletes all live expenses too. Recurring rules
    and budget entries are left as-is (they carry no user-visible content in the
    mobile client and cascade-delete there is handled server-side on compaction).
    """
    # Only look at live expenses for the guard + cascade.
    expenses = await list_expenses(config, user_id, project_id=project_id, status=None)
    if expenses and not cascade:
        return False

    now = _now()

    # Verify the project exists (and is live) before doing anything.
    proj = await get_project(config, user_id, project_id)
    if proj is None:
        return False

    async with db_session(config) as db:
        if cascade and expenses:
            # Soft-delete all live expenses atomically with the project.
            await db.execute(
                "UPDATE project_expenses SET deleted_at = ? "
                "WHERE project_id = ? AND user_id = ? AND deleted_at IS NULL",
                (now, project_id, user_id),
            )
        result = await db.execute(
            "UPDATE projects SET deleted_at = ? "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (now, project_id, user_id),
        )
        await db.commit()
        deleted = result.rowcount > 0

    return deleted


# ---------------------------------------------------------------------------
# Expenses CRUD
# ---------------------------------------------------------------------------


async def create_expense(
    config: Config,
    user_id: str,
    project_id: str,
    *,
    amount: float,
    currency: str | None = None,
    description: str | None = None,
    vendor: str | None = None,
    notes: str | None = None,
    task_id: str | None = None,
    spent_at: str | None = None,
    recurring_expense_id: str | None = None,
    expense_id: str | None = None,
) -> dict:
    """Log an expense against a project (optionally a task). Mirrors a
    LazyBrain note wikilinking ``[[<Name> Project]]``.

    ``expense_id``: optional client-minted id for offline-first idempotent
    replay. A second call with the same id returns the existing row without
    inserting a duplicate.
    """
    project = await get_project(config, user_id, project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")

    key = await get_user_dek(config, user_id)

    # Idempotent replay: if the client-minted id already exists, return it.
    expense_id = expense_id or str(uuid4())
    async with db_session(config) as db:
        cur = await db.execute(
            f"SELECT {EXPENSE_SELECT} FROM project_expenses "
            "WHERE id = ? AND user_id = ?",
            (expense_id, user_id),
        )
        existing_row = await cur.fetchone()
    if existing_row is not None:
        return _expense_to_dict(existing_row, key)

    now = _now()
    currency = currency or project.get("currency") or "EUR"
    spent_at = spent_at or now[:10]

    task_title = await _resolve_task_title(config, user_id, task_id)
    note_id = await _write_expense_note(
        config, user_id,
        project_name=project["name"], amount=amount, currency=currency,
        description=description, vendor=vendor, spent_at=spent_at,
        task_title=task_title,
    )

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO project_expenses "
            "(id, user_id, project_id, task_id, amount, currency, description, "
            "vendor, notes, spent_at, status, recurring_expense_id, "
            "lazybrain_note_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'posted', ?, ?, ?, ?)",
            (
                expense_id, user_id, project_id, task_id, amount, currency,
                _enc(description, key), _enc(vendor, key), _enc(notes, key),
                spent_at, recurring_expense_id, note_id, now, now,
            ),
        )
        await db.commit()

    logger.debug("Created expense %s on project %s", expense_id, project_id)
    return {
        "id": expense_id, "user_id": user_id, "project_id": project_id,
        "task_id": task_id, "amount": amount, "currency": currency,
        "description": description, "vendor": vendor, "notes": notes,
        "spent_at": spent_at, "status": "posted",
        "recurring_expense_id": recurring_expense_id,
        "lazybrain_note_id": note_id, "created_at": now, "updated_at": now,
        "is_favorite": False, "deleted_at": None,
    }


async def list_expenses(
    config: Config,
    user_id: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    status: str | None = "posted",
) -> list[dict]:
    key = await get_user_dek(config, user_id)
    where = "user_id = ? AND deleted_at IS NULL"
    params: list = [user_id]
    if project_id:
        where += " AND project_id = ?"
        params.append(project_id)
    if task_id:
        where += " AND task_id = ?"
        params.append(task_id)
    if status:
        where += " AND status = ?"
        params.append(status)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {EXPENSE_SELECT} FROM project_expenses "
            f"WHERE {where} ORDER BY spent_at DESC, created_at DESC",
            params,
        )
        rows = await cursor.fetchall()
    return [_expense_to_dict(r, key) for r in rows]


async def list_all_expenses(
    config: Config,
    user_id: str,
    *,
    status: str | None = "posted",
) -> list[dict]:
    """Every expense across ALL of the user's projects, newest-first, each
    enriched with the (decrypted) ``project_name`` of its project. Powers the
    global Expenses view. User-scoped; ``status`` filters posted/void."""
    key = await get_user_dek(config, user_id)
    where = "user_id = ? AND deleted_at IS NULL"
    params: list = [user_id]
    if status:
        where += " AND status = ?"
        params.append(status)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {EXPENSE_SELECT} FROM project_expenses "
            f"WHERE {where} ORDER BY spent_at DESC, created_at DESC",
            params,
        )
        rows = await cursor.fetchall()

    expenses = [_expense_to_dict(r, key) for r in rows]
    if not expenses:
        return []

    # Attach each expense's project name (decrypted once per project) so the
    # cross-project view can group/label without a second round-trip.
    name_by_id = {p["id"]: p["name"] for p in await list_projects(config, user_id)}
    return [{**e, "project_name": name_by_id.get(e["project_id"])} for e in expenses]


async def update_expense(
    config: Config, user_id: str, expense_id: str, **fields
) -> bool:
    if not fields:
        return False
    # Normalize the star flag to a stored INTEGER 0/1 (a stray truthy payload
    # can never write a non-0/1), exactly like ``update_project``.
    if "is_favorite" in fields:
        fields = {**fields, "is_favorite": _clean_favorite(fields["is_favorite"])}
    key = await get_user_dek(config, user_id)
    set_clauses: list[str] = []
    params: list = []
    for col, value in fields.items():
        if col in ENCRYPTED_EXPENSE_FIELDS and value is not None:
            value = encrypt(value, key)
        set_clauses.append(f"{col} = ?")
        params.append(value)
    set_clauses.append("updated_at = ?")
    params.append(_now())
    params.extend([expense_id, user_id])

    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE project_expenses SET {', '.join(set_clauses)} "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
        moved = result.rowcount > 0

    # Re-point the LazyBrain note if the project changed (best-effort, never fails).
    if moved and "project_id" in fields:
        try:
            rows = await list_expenses(config, user_id, project_id=fields["project_id"], status=None)
            exp = next((r for r in rows if r["id"] == expense_id), None)
            proj = await get_project(config, user_id, fields["project_id"])
            if exp and proj:
                await _delete_note(config, user_id, exp.get("lazybrain_note_id"))
                note_id = await _write_expense_note(
                    config, user_id,
                    project_name=proj["name"], amount=float(exp["amount"] or 0),
                    currency=exp.get("currency") or proj.get("currency") or "EUR",
                    description=exp.get("description"), vendor=exp.get("vendor"),
                    spent_at=exp.get("spent_at"), task_title=None,
                )
                async with db_session(config) as db:
                    await db.execute(
                        "UPDATE project_expenses SET lazybrain_note_id = ? WHERE id = ? AND user_id = ?",
                        (note_id, expense_id, user_id),
                    )
                    await db.commit()
        except Exception:
            logger.warning("expense note re-point failed (%s)", expense_id, exc_info=True)

    return moved


async def delete_expense(config: Config, user_id: str, expense_id: str) -> bool:
    """Soft-delete an expense by setting ``deleted_at``. The row is preserved so
    offline clients can learn of the delete via /api/budgets/changes."""
    now = _now()
    async with db_session(config) as db:
        result = await db.execute(
            "UPDATE project_expenses SET deleted_at = ? "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (now, expense_id, user_id),
        )
        await db.commit()
        deleted = result.rowcount > 0
    return deleted


async def get_budget_changes(
    config: Config,
    user_id: str,
    *,
    since: str | None = None,
) -> dict:
    """Delta feed for offline-first clients.

    Returns:
    - ``projects``: live (non-deleted) projects with ``updated_at > since``
    - ``expenses``: live (non-deleted) expenses with ``updated_at > since``
    - ``budget_entries``: live (non-deleted) top-ups with ``updated_at > since``
    - ``deleted_projects``: ids of projects soft-deleted after ``since``
    - ``deleted_expenses``: ids of expenses soft-deleted after ``since``
    - ``deleted_budget_entries``: ids of top-ups soft-deleted after ``since``
    - ``now``: server ISO timestamp — pass this as ``since`` next time

    When ``since`` is omitted, returns ALL live rows + ALL tombstones (full
    sync). Clients should persist ``now`` and send it on the next pull.
    """
    key = await get_user_dek(config, user_id)
    now_ts = _now()

    if since:
        # Live projects updated after since.
        async with db_session(config) as db:
            cur = await db.execute(
                f"SELECT {PROJECT_SELECT} FROM projects "
                "WHERE user_id = ? AND deleted_at IS NULL AND updated_at > ? "
                "ORDER BY updated_at DESC",
                (user_id, since),
            )
            proj_rows = await cur.fetchall()

        # Deleted projects whose deleted_at > since.
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id FROM projects "
                "WHERE user_id = ? AND deleted_at IS NOT NULL AND deleted_at > ?",
                (user_id, since),
            )
            deleted_proj_ids = [r[0] for r in await cur.fetchall()]

        # Live expenses updated after since.
        async with db_session(config) as db:
            cur = await db.execute(
                f"SELECT {EXPENSE_SELECT} FROM project_expenses "
                "WHERE user_id = ? AND deleted_at IS NULL AND updated_at > ? "
                "ORDER BY updated_at DESC",
                (user_id, since),
            )
            exp_rows = await cur.fetchall()

        # Deleted expenses whose deleted_at > since.
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id FROM project_expenses "
                "WHERE user_id = ? AND deleted_at IS NOT NULL AND deleted_at > ?",
                (user_id, since),
            )
            deleted_exp_ids = [r[0] for r in await cur.fetchall()]

        # Live budget entries (top-ups) updated after since.
        async with db_session(config) as db:
            cur = await db.execute(
                f"SELECT {BUDGET_ENTRY_SELECT} FROM budget_entries "
                "WHERE user_id = ? AND deleted_at IS NULL AND updated_at > ? "
                "ORDER BY updated_at DESC",
                (user_id, since),
            )
            entry_rows = await cur.fetchall()

        # Deleted budget entries whose deleted_at > since.
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id FROM budget_entries "
                "WHERE user_id = ? AND deleted_at IS NOT NULL AND deleted_at > ?",
                (user_id, since),
            )
            deleted_entry_ids = [r[0] for r in await cur.fetchall()]
    else:
        # Full sync: all live rows + all tombstones.
        async with db_session(config) as db:
            cur = await db.execute(
                f"SELECT {PROJECT_SELECT} FROM projects "
                "WHERE user_id = ? AND deleted_at IS NULL "
                "ORDER BY updated_at DESC",
                (user_id,),
            )
            proj_rows = await cur.fetchall()

        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id FROM projects "
                "WHERE user_id = ? AND deleted_at IS NOT NULL",
                (user_id,),
            )
            deleted_proj_ids = [r[0] for r in await cur.fetchall()]

        async with db_session(config) as db:
            cur = await db.execute(
                f"SELECT {EXPENSE_SELECT} FROM project_expenses "
                "WHERE user_id = ? AND deleted_at IS NULL "
                "ORDER BY updated_at DESC",
                (user_id,),
            )
            exp_rows = await cur.fetchall()

        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id FROM project_expenses "
                "WHERE user_id = ? AND deleted_at IS NOT NULL",
                (user_id,),
            )
            deleted_exp_ids = [r[0] for r in await cur.fetchall()]

        async with db_session(config) as db:
            cur = await db.execute(
                f"SELECT {BUDGET_ENTRY_SELECT} FROM budget_entries "
                "WHERE user_id = ? AND deleted_at IS NULL "
                "ORDER BY updated_at DESC",
                (user_id,),
            )
            entry_rows = await cur.fetchall()

        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id FROM budget_entries "
                "WHERE user_id = ? AND deleted_at IS NOT NULL",
                (user_id,),
            )
            deleted_entry_ids = [r[0] for r in await cur.fetchall()]

    return {
        "projects": [_project_to_dict(r, key) for r in proj_rows],
        "expenses": [_expense_to_dict(r, key) for r in exp_rows],
        "budget_entries": [_budget_entry_to_dict(r, key) for r in entry_rows],
        "deleted_projects": deleted_proj_ids,
        "deleted_expenses": deleted_exp_ids,
        "deleted_budget_entries": deleted_entry_ids,
        "now": now_ts,
    }


async def spending_report(
    config: Config, user_id: str, *, project_id: str | None = None
) -> dict:
    """Per-project budget/spent/remaining + totals. No FX — totals assume each
    project's base currency; mixed-currency projects are flagged, not converted."""
    projects = await list_projects(config, user_id, status="active")
    if project_id:
        projects = [p for p in projects if p["id"] == project_id]

    report_projects: list[dict] = []
    total_budget = 0.0
    total_spent = 0.0
    for proj in projects:
        expenses = await list_expenses(config, user_id, project_id=proj["id"])
        currencies = {e.get("currency") for e in expenses if e.get("currency")}
        currencies.discard(proj.get("currency"))
        report_projects.append({
            "id": proj["id"],
            "name": proj["name"],
            "currency": proj.get("currency"),
            "budget": proj.get("budget"),
            "spent": proj.get("spent"),
            "remaining": proj.get("remaining"),
            "expense_count": len(expenses),
            "mixed_currency": bool(currencies),
        })
        total_budget += float(proj.get("budget") or 0.0)
        total_spent += float(proj.get("spent") or 0.0)

    return {
        "currency": "EUR",
        "projects": report_projects,
        "total_budget": total_budget,
        "total_spent": total_spent,
        "total_remaining": total_budget - total_spent,
    }


# ---------------------------------------------------------------------------
# Budget entries (credit side — top-ups with a source)
# ---------------------------------------------------------------------------


async def add_budget_entry(
    config: Config,
    user_id: str,
    project_id: str,
    *,
    amount: float,
    source: str | None = None,
    currency: str | None = None,
    kind: str = "credit",
    entry_id: str | None = None,
) -> dict:
    """Add money to a project's budget, recording WHERE it came from. Bumps
    ``projects.budget`` by ``amount`` and writes a ledger row (shown in the
    project Log next to expenses). ``source`` is the first comment — the
    answer to "where is this budget from?".

    ``entry_id``: optional client-minted id for offline-first idempotent
    replay. A retried POST with the same id returns the existing row WITHOUT
    re-bumping the project budget (mirrors ``create_expense``)."""
    project = await get_project(config, user_id, project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")

    # Idempotent replay: if the client-minted id already exists, return it
    # without inserting a duplicate or re-bumping the budget.
    if entry_id:
        existing = await get_budget_entry(config, user_id, entry_id)
        if existing is not None:
            return {**existing, "new_budget": float(project.get("budget") or 0.0)}

    currency = currency or project.get("currency") or "EUR"
    entry = await _insert_budget_entry(
        config, user_id, project_id,
        amount=amount, source=source, currency=currency, kind=kind,
        entry_id=entry_id,
    )
    new_budget = float(project.get("budget") or 0.0) + amount
    await update_project(config, user_id, project_id, budget=new_budget)
    logger.debug("Added budget %s to project %s (kind=%s)", amount, project_id, kind)
    return {**entry, "new_budget": new_budget}


async def list_budget_entries(
    config: Config, user_id: str, project_id: str
) -> list[dict]:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {BUDGET_ENTRY_SELECT} FROM budget_entries "
            "WHERE user_id = ? AND project_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (user_id, project_id),
        )
        rows = await cursor.fetchall()
    return [_budget_entry_to_dict(r, key) for r in rows]


async def get_budget_entry(
    config: Config, user_id: str, entry_id: str
) -> dict | None:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cur = await db.execute(
            f"SELECT {BUDGET_ENTRY_SELECT} FROM budget_entries "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (entry_id, user_id),
        )
        row = await cur.fetchone()
    return _budget_entry_to_dict(row, key) if row else None


async def update_budget_entry(
    config: Config, user_id: str, entry_id: str, *,
    amount: float | None = None,
    source: str | None = None,
    currency: str | None = None,
) -> bool:
    """Edit a ledger entry. Adjusts ``project.budget`` by the AMOUNT DELTA so
    the project total stays consistent (entry was +200, edit to +150 →
    project.budget -= 50). ``source`` and ``currency`` update in place."""
    existing = await get_budget_entry(config, user_id, entry_id)
    if existing is None:
        return False

    key = await get_user_dek(config, user_id)
    new_amount = float(amount) if amount is not None else float(existing["amount"])
    new_currency = currency or existing.get("currency") or "EUR"
    # source: None means "leave alone"; empty string means "clear it".
    new_source = source if source is not None else existing.get("source")
    delta = new_amount - float(existing["amount"])
    now = _now()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE budget_entries "
            "SET amount = ?, currency = ?, source = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (new_amount, new_currency, _enc(new_source, key), now,
             entry_id, user_id),
        )
        await db.commit()

    if delta != 0:
        project = await get_project(config, user_id, existing["project_id"])
        if project is not None:
            new_budget = float(project.get("budget") or 0.0) + delta
            await update_project(
                config, user_id, existing["project_id"], budget=new_budget,
            )
    return True


async def delete_budget_entry(
    config: Config, user_id: str, entry_id: str
) -> bool:
    """Soft-delete a ledger entry (set ``deleted_at``) and roll back its effect
    on ``project.budget``. The row is preserved so offline clients learn of the
    delete via /api/budgets/changes instead of it silently vanishing. Mirrors
    ``delete_expense``'s soft-delete pattern."""
    existing = await get_budget_entry(config, user_id, entry_id)
    if existing is None:
        return False

    now = _now()
    async with db_session(config) as db:
        result = await db.execute(
            "UPDATE budget_entries SET deleted_at = ? "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (now, entry_id, user_id),
        )
        await db.commit()
        if result.rowcount == 0:
            return False

    project = await get_project(config, user_id, existing["project_id"])
    if project is not None:
        new_budget = float(project.get("budget") or 0.0) - float(existing["amount"])
        await update_project(
            config, user_id, existing["project_id"], budget=new_budget,
        )
    return True


# ---------------------------------------------------------------------------
# Recurring expenses (cron-driven auto-charge)
# ---------------------------------------------------------------------------


async def list_recurring(
    config: Config, user_id: str, *, project_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    key = await get_user_dek(config, user_id)
    where = "user_id = ?"
    params: list = [user_id]
    if project_id:
        where += " AND project_id = ?"
        params.append(project_id)
    if status:
        where += " AND status = ?"
        params.append(status)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {RECUR_SELECT} FROM recurring_expenses "
            f"WHERE {where} ORDER BY created_at DESC",
            params,
        )
        rows = await cursor.fetchall()
    return [_recur_to_dict(r, key) for r in rows]


async def get_recurring(
    config: Config, user_id: str, recurring_id: str
) -> dict | None:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {RECUR_SELECT} FROM recurring_expenses "
            "WHERE id = ? AND user_id = ?",
            (recurring_id, user_id),
        )
        row = await cursor.fetchone()
    return _recur_to_dict(row, key) if row else None


async def create_recurring_expense(
    config: Config,
    user_id: str,
    project_id: str,
    *,
    amount: float,
    cron_expression: str,
    currency: str | None = None,
    description: str | None = None,
    vendor: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Register a recurring expense rule + its agent_jobs cron scheduler. The
    heartbeat daemon materializes a posted expense on each due tick."""
    from lazyclaw.heartbeat import cron as cron_mod
    from lazyclaw.heartbeat import orchestrator

    if not cron_mod.is_valid(cron_expression):
        raise ValueError(f"invalid cron expression: {cron_expression!r}")

    project = await get_project(config, user_id, project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")

    key = await get_user_dek(config, user_id)
    recurring_id = str(uuid4())
    now = _now()
    currency = currency or project.get("currency") or "EUR"
    next_run = cron_mod.calculate_next_run(cron_expression, user_id=user_id)

    job_id = await orchestrator.create_job(
        config,
        user_id,
        name=f"Recurring expense: {description or amount} ({project['name']})",
        instruction=f"[EXPENSE:{recurring_id}]",
        job_type="cron",
        cron_expression=cron_expression,
    )

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO recurring_expenses "
            "(id, user_id, project_id, task_id, amount, currency, description, "
            "vendor, cron_expression, job_id, next_run, last_charged_at, "
            "status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'active', ?, ?)",
            (
                recurring_id, user_id, project_id, task_id, amount, currency,
                _enc(description, key), _enc(vendor, key), cron_expression,
                job_id, next_run, now, now,
            ),
        )
        await db.commit()

    logger.debug("Created recurring expense %s (job %s)", recurring_id, job_id)
    return {
        "id": recurring_id, "user_id": user_id, "project_id": project_id,
        "task_id": task_id, "amount": amount, "currency": currency,
        "description": description, "vendor": vendor,
        "cron_expression": cron_expression, "job_id": job_id,
        "next_run": next_run, "last_charged_at": None, "status": "active",
        "created_at": now, "updated_at": now,
    }


async def materialize_recurring_expense(
    config: Config, user_id: str, recurring_id: str
) -> dict | None:
    """Daemon-side handler: insert this period's expense, guarding against a
    double-charge if the daemon double-fires. Returns the new expense dict
    (with ``_project_name`` for the push), or None when nothing was charged."""
    from datetime import datetime as _dt

    from lazyclaw.heartbeat import cron as cron_mod

    rule = await get_recurring(config, user_id, recurring_id)
    if rule is None or rule.get("status") != "active":
        return None

    # Dedup guard: after charging at a scheduled fire-time, the next run is in
    # the future — only charge again once now passes it.
    last_charged = rule.get("last_charged_at")
    if last_charged:
        try:
            base = _dt.fromisoformat(last_charged)
            next_after = cron_mod.get_next_run(
                rule["cron_expression"], after=base, user_id=user_id,
            )
            if next_after > _dt.now(timezone.utc):
                return None
        except Exception:
            logger.debug("recurring dedup check skipped", exc_info=True)

    expense = await create_expense(
        config, user_id, rule["project_id"],
        amount=rule["amount"], currency=rule.get("currency"),
        description=rule.get("description"), vendor=rule.get("vendor"),
        task_id=rule.get("task_id"), recurring_expense_id=recurring_id,
    )

    now = _now()
    async with db_session(config) as db:
        await db.execute(
            "UPDATE recurring_expenses SET last_charged_at = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (now, now, recurring_id, user_id),
        )
        await db.commit()

    project = await get_project(config, user_id, rule["project_id"])
    return {**expense, "_project_name": project["name"] if project else "project"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _resolve_task_title(
    config: Config, user_id: str, task_id: str | None
) -> str | None:
    if not task_id:
        return None
    try:
        from lazyclaw.tasks.store import get_task

        task = await get_task(config, user_id, task_id)
        return task.get("title") if task else None
    except Exception:
        logger.debug("expense task-title resolve skipped", exc_info=True)
        return None


async def _cancel_recurring_job(
    config: Config, user_id: str, job_id: str | None
) -> None:
    if not job_id:
        return
    try:
        from lazyclaw.heartbeat import orchestrator

        await orchestrator.delete_job(config, user_id, job_id)
    except Exception:
        logger.debug("recurring job cancel skipped (%s)", job_id, exc_info=True)
