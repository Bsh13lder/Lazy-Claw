"""One-shot migration: personal_memory + daily_logs + markdown layers → LazyBrain notes.

Invoked via ``python -m lazyclaw.cli_migrate_lazybrain [--user-id X | --all]``.

Safe by default:
- No source data is deleted (pass ``--purge-source`` explicitly once verified).
- Writes ``{data}/lazybrain_migration_<ts>.json`` with a ``{source → note_id}``
  map so rollback is just replaying the file.
- Idempotent via import tag — re-running skips rows already tagged
  ``#imported/<source>``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from lazyclaw.config import Config, load_config
from lazyclaw.crypto.encryption import decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.lazybrain import store as lb_store
from lazyclaw.memory.layers import MemoryLayer, _memory_path, read_memory

console = Console()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _list_users(config: Config) -> list[str]:
    async with db_session(config) as db:
        rows = await db.execute("SELECT id FROM users")
        return [r[0] for r in await rows.fetchall()]


async def _already_imported(
    config: Config, user_id: str, source_tag: str
) -> bool:
    notes = await lb_store.list_notes(
        config, user_id, tag=source_tag, limit=1
    )
    return len(notes) > 0


async def _purge_imported(
    config: Config, user_id: str, source_tag: str, *, dry_run: bool
) -> int:
    """Delete every note carrying ``source_tag`` for ``user_id``. Returns
    the count touched. Used by ``--force-reimport`` so a second import
    pass after a schema fix doesn't pile rows on top of stale ones.

    Plain DELETE on the encrypted ``notes`` table — re-import will
    re-create from the source store, so losing the encrypted body here
    is intentional (the source is the canonical copy until the user
    runs ``--purge-source``).
    """
    notes = await lb_store.list_notes(
        config, user_id, tag=source_tag, limit=10000,
    )
    if not notes:
        return 0
    if dry_run:
        return len(notes)
    ids = [n["id"] for n in notes]
    placeholders = ",".join("?" * len(ids))
    async with db_session(config) as db:
        await db.execute(
            f"DELETE FROM note_links WHERE user_id = ? "
            f"AND (from_note_id IN ({placeholders}) "
            f"OR to_note_id IN ({placeholders}))",
            (user_id, *ids, *ids),
        )
        await db.execute(
            f"DELETE FROM notes WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *ids),
        )
        await db.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Migrators — each returns {source_id → new_note_id}
# ---------------------------------------------------------------------------

async def migrate_personal_memory(
    config: Config, user_id: str, *, dry_run: bool, force_reimport: bool = False,
) -> dict[str, str]:
    """personal_memory rows → LazyBrain notes tagged #imported/personal."""
    if force_reimport:
        purged = await _purge_imported(
            config, user_id, "imported/personal", dry_run=dry_run,
        )
        if purged:
            console.print(
                f"[yellow]--force-reimport: purged {purged} imported/personal "
                f"notes for user {user_id}[/yellow]"
            )
    elif await _already_imported(config, user_id, "imported/personal"):
        return {}
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, memory_type, content, importance FROM personal_memory "
            "WHERE user_id = ? ORDER BY importance DESC",
            (user_id,),
        )
        records = await rows.fetchall()

    mapping: dict[str, str] = {}
    for src_id, mem_type, enc_content, importance in records:
        try:
            content = decrypt_field(enc_content, dek, fallback="")
            if not content:
                continue
            if dry_run:
                mapping[src_id] = "<dry-run>"
                continue
            kind = mem_type or "fact"
            owner_tag = "owner/user" if kind == "fact" else "owner/agent"
            note = await lb_store.save_note(
                config,
                user_id,
                content=content,
                title=f"{kind}: {content[:60]}",
                tags=["imported/personal", kind, owner_tag],
                importance=importance or 5,
            )
            mapping[src_id] = note["id"]
        except Exception:
            logger.exception("Failed to migrate personal_memory %s", src_id)
    return mapping


async def migrate_daily_logs(
    config: Config, user_id: str, *, dry_run: bool, force_reimport: bool = False,
) -> dict[str, str]:
    """daily_logs rows → LazyBrain notes tagged #imported/daily and #journal/YYYY-MM-DD."""
    if force_reimport:
        purged = await _purge_imported(
            config, user_id, "imported/daily", dry_run=dry_run,
        )
        if purged:
            console.print(
                f"[yellow]--force-reimport: purged {purged} imported/daily "
                f"notes for user {user_id}[/yellow]"
            )
    elif await _already_imported(config, user_id, "imported/daily"):
        return {}
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, date, summary, key_events FROM daily_logs "
            "WHERE user_id = ? ORDER BY date DESC",
            (user_id,),
        )
        records = await rows.fetchall()

    mapping: dict[str, str] = {}
    for src_id, date, enc_summary, enc_events in records:
        try:
            summary = decrypt_field(enc_summary, dek, fallback="")
            events = decrypt_field(enc_events, dek, fallback="")
            body_parts: list[str] = []
            if summary:
                body_parts.append(summary)
            if events:
                body_parts.append(f"\n**Key events**\n{events}")
            if not body_parts:
                continue
            body = "\n".join(body_parts)
            if dry_run:
                mapping[src_id] = "<dry-run>"
                continue
            note = await lb_store.save_note(
                config,
                user_id,
                content=body,
                title=f"Journal — {date}",
                tags=["imported/daily", f"journal/{date}", "owner/user"],
                importance=4,
            )
            mapping[src_id] = note["id"]
        except Exception:
            logger.exception("Failed to migrate daily_log %s", src_id)
    return mapping


async def migrate_tasks(
    config: Config, user_id: str, *, dry_run: bool, force_reimport: bool = False,
) -> dict[str, str]:
    """tasks rows → LazyBrain notes tagged #imported/tasks #task #owner/{owner}."""
    if force_reimport:
        purged = await _purge_imported(
            config, user_id, "imported/tasks", dry_run=dry_run,
        )
        if purged:
            console.print(
                f"[yellow]--force-reimport: purged {purged} imported/tasks "
                f"notes for user {user_id}[/yellow]"
            )
    elif await _already_imported(config, user_id, "imported/tasks"):
        return {}
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, title, description, category, priority, status, owner, "
            "due_date, reminder_at, created_at "
            "FROM tasks WHERE user_id = ?",
            (user_id,),
        )
        records = await rows.fetchall()

    importance_map = {"urgent": 9, "high": 7, "medium": 5, "low": 3}
    mapping: dict[str, str] = {}
    for row in records:
        try:
            (src_id, enc_title, enc_desc, enc_cat, prio,
             status, owner, due_date, reminder_at, created_at) = row
            title = decrypt_field(enc_title, dek, fallback="") or "(no title)"
            desc = decrypt_field(enc_desc, dek, fallback="") or ""
            cat = decrypt_field(enc_cat, dek, fallback="") or ""

            body_lines = [f"**Task:** {title}"]
            if desc:
                body_lines.append(desc)
            meta = [f"priority `{prio}`", f"status `{status}`"]
            if due_date:
                meta.append(f"due `{due_date}`")
            if reminder_at:
                meta.append(f"reminder `{reminder_at}`")
            if cat:
                meta.append(f"category `{cat}`")
            body_lines.append("— " + " · ".join(meta))
            body = "\n\n".join(body_lines)

            tags = [
                "imported/tasks", "task",
                f"priority/{prio}", f"status/{status}",
                f"owner/{owner if owner == 'user' else 'agent'}",
            ]
            if cat:
                tags.append(f"category/{cat}")

            if dry_run:
                mapping[src_id] = "<dry-run>"
                continue
            note = await lb_store.save_note(
                config,
                user_id,
                content=body,
                title=f"Task: {title}",
                tags=tags,
                importance=importance_map.get(prio, 5),
            )
            mapping[src_id] = note["id"]
        except Exception:
            logger.exception("Failed to migrate task")
    return mapping


async def migrate_site_memory(
    config: Config, user_id: str, *, dry_run: bool, force_reimport: bool = False,
) -> dict[str, str]:
    """site_memory rows → LazyBrain notes tagged #imported/site-memory #owner/agent."""
    if force_reimport:
        purged = await _purge_imported(
            config, user_id, "imported/site-memory", dry_run=dry_run,
        )
        if purged:
            console.print(
                f"[yellow]--force-reimport: purged {purged} imported/site-memory "
                f"notes for user {user_id}[/yellow]"
            )
    elif await _already_imported(config, user_id, "imported/site-memory"):
        return {}
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, domain, memory_type, title, content, "
            "success_count, fail_count, last_used "
            "FROM site_memory WHERE user_id = ?",
            (user_id,),
        )
        records = await rows.fetchall()

    mapping: dict[str, str] = {}
    for src_id, domain, mem_type, enc_title, enc_content, ok, fail, last_used in records:
        try:
            title = decrypt_field(enc_title, dek, fallback="") or "(no title)"
            content = decrypt_field(enc_content, dek, fallback="") or ""
            body = (
                f"**Site knowledge:** {title}\n\n"
                f"Domain: `{domain}` — type `{mem_type}` · "
                f"{ok} success / {fail} fail"
                f"{f' · last used {last_used}' if last_used else ''}\n\n"
                f"```\n{content[:1500]}\n```"
            )
            tags = [
                "imported/site-memory", "site-memory", "owner/agent",
                f"site/{domain}", f"kind/{mem_type}",
            ]
            if dry_run:
                mapping[src_id] = "<dry-run>"
                continue
            note = await lb_store.save_note(
                config,
                user_id,
                content=body,
                title=f"Site: {domain} — {title[:40]}",
                tags=tags,
                importance=4,
            )
            mapping[src_id] = note["id"]
        except Exception:
            logger.exception("Failed to migrate site_memory")
    return mapping


async def migrate_markdown_layers(
    config: Config, user_id: str, *, dry_run: bool, force_reimport: bool = False,
) -> dict[str, str]:
    """5-layer markdown files (lazyclaw/memory/layers.py) → notes tagged #imported/layer."""
    if force_reimport:
        purged = await _purge_imported(
            config, user_id, "imported/layer", dry_run=dry_run,
        )
        if purged:
            console.print(
                f"[yellow]--force-reimport: purged {purged} imported/layer "
                f"notes for user {user_id}[/yellow]"
            )
    elif await _already_imported(config, user_id, "imported/layer"):
        return {}
    mapping: dict[str, str] = {}

    for layer in (MemoryLayer.USER, MemoryLayer.GLOBAL):
        try:
            scope_id = user_id if layer == MemoryLayer.USER else "global"
            body = read_memory(config, layer, scope_id, max_lines=0)
        except Exception:
            body = ""
        if not body or not body.strip():
            continue

        source_key = str(_memory_path(config, layer, scope_id))
        if dry_run:
            mapping[source_key] = "<dry-run>"
            continue
        try:
            note = await lb_store.save_note(
                config,
                user_id,
                content=body,
                title=f"Layer import — {layer.value}",
                tags=["imported/layer", f"layer/{layer.value}", "owner/agent"],
                importance=6,
            )
            mapping[source_key] = note["id"]
        except Exception:
            logger.exception("Failed to migrate layer %s for user %s", layer, user_id)

    return mapping


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def migrate_user(
    config: Config, user_id: str, *, dry_run: bool, force_reimport: bool = False,
) -> dict[str, dict[str, str]]:
    return {
        "personal_memory": await migrate_personal_memory(
            config, user_id, dry_run=dry_run, force_reimport=force_reimport,
        ),
        "daily_logs": await migrate_daily_logs(
            config, user_id, dry_run=dry_run, force_reimport=force_reimport,
        ),
        "tasks": await migrate_tasks(
            config, user_id, dry_run=dry_run, force_reimport=force_reimport,
        ),
        "site_memory": await migrate_site_memory(
            config, user_id, dry_run=dry_run, force_reimport=force_reimport,
        ),
        "markdown_layers": await migrate_markdown_layers(
            config, user_id, dry_run=dry_run, force_reimport=force_reimport,
        ),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

@click.command()
@click.option("--user-id", default=None, help="Migrate one specific user.")
@click.option("--all", "all_users", is_flag=True, help="Migrate every user.")
@click.option("--dry-run", is_flag=True, help="Report but don't write.")
@click.option(
    "--purge-source",
    is_flag=True,
    help="After migration, delete the source rows/files. Requires confirmation.",
)
@click.option(
    "--force-reimport",
    is_flag=True,
    help=(
        "Delete existing #imported/<source> notes and re-import from the source "
        "tables. Use after a schema fix or when retrying a partial import."
    ),
)
def main(
    user_id: str | None,
    all_users: bool,
    dry_run: bool,
    purge_source: bool,
    force_reimport: bool,
) -> None:
    """Migrate existing memory stores into LazyBrain."""
    if not user_id and not all_users:
        console.print("[red]Pass --user-id <id> or --all.[/red]")
        sys.exit(1)
    if purge_source and not click.confirm(
        "Really delete source rows after migration?", default=False
    ):
        sys.exit(1)

    async def _run() -> None:
        config = load_config()
        await init_db(config)

        targets = [user_id] if user_id else await _list_users(config)
        report: dict[str, dict[str, dict[str, str]]] = {}
        for uid in targets:
            try:
                report[uid] = await migrate_user(
                    config, uid, dry_run=dry_run, force_reimport=force_reimport,
                )
            except Exception as exc:
                logger.exception("Migration failed for %s", uid)
                report[uid] = {"error": {"message": str(exc)}}  # type: ignore[dict-item]

        # Render summary
        table = Table(title="LazyBrain migration")
        table.add_column("User", style="cyan")
        table.add_column("memory", justify="right")
        table.add_column("daily_logs", justify="right")
        table.add_column("tasks", justify="right")
        table.add_column("site_memory", justify="right")
        table.add_column("layers", justify="right")
        for uid, buckets in report.items():
            if "error" in buckets:
                table.add_row(uid, "—", "—", "—", "—", f"[red]{buckets['error'].get('message')[:40]}[/red]")
                continue
            table.add_row(
                uid,
                str(len(buckets.get("personal_memory") or {})),
                str(len(buckets.get("daily_logs") or {})),
                str(len(buckets.get("tasks") or {})),
                str(len(buckets.get("site_memory") or {})),
                str(len(buckets.get("markdown_layers") or {})),
            )
        console.print(table)

        if not dry_run:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out = Path(config.data_dir) / f"lazybrain_migration_{ts}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, default=str))
            console.print(f"\n📄 Mapping written to [cyan]{out}[/cyan]")

        if purge_source and not dry_run:
            console.print(
                "[yellow]--purge-source requested — manual SQL still required."
                " See plan Phase 18.4 for the destructive-delete helpers.[/yellow]"
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
