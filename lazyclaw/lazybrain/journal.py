"""Daily-journal helpers — thin wrapper over ``store`` that enforces the
``#journal/YYYY-MM-DD`` tagging convention and treats each date as a single
append-only page.

After each append, a fire-and-forget worker-model call regenerates the
title into a short phrase describing what actually happened that day
(e.g. "Shipped LazyBrain redesign, fixed canvas bug"). Falls back to
``Journal — YYYY-MM-DD`` when the LLM is unavailable.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone

from lazyclaw.config import Config
from lazyclaw.lazybrain import store, timezone_util

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Only regenerate the journal title once the page has meaningful content.
_MIN_CHARS_FOR_LLM_TITLE = 120
_MIN_BULLETS_FOR_LLM_TITLE = 2

# Serialize get-then-write windows in ensure_today_journal() +
# append_journal() so two concurrent coroutines for the same user+date
# don't each miss the existence check and create a fresh stub. The DB
# audit on 2026-05-13 found multiple `journal/<date>`-tagged rows per
# day — every one a stub with 0 outgoing edges sitting next to the
# real journal row carrying all the bullets. Within-process locks
# close that race for the single-process default deployment. A cross-
# process collision still needs the cleanup CLI to consolidate (the
# UI hides duplicate stubs by surfacing the longest-content row).
_JOURNAL_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _journal_lock(user_id: str, iso_date: str) -> asyncio.Lock:
    """Per-(user, date) lock for journal mutations. Created lazily — the
    dict size is bounded by the user's active days so it can't grow."""
    key = (user_id, iso_date)
    lock = _JOURNAL_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _JOURNAL_LOCKS[key] = lock
    return lock


def _today_iso(user_id: str | None = None) -> str:
    """ISO date in the user's timezone. Journal pages follow the user's calendar."""
    return timezone_util.today_iso(user_id)


def resolve_date(value: str | None, user_id: str | None = None) -> str:
    """Coerce ``today``/``yesterday``/``YYYY-MM-DD`` into ISO date."""
    if not value or value.lower() == "today":
        return _today_iso(user_id)
    if value.lower() == "yesterday":
        return timezone_util.yesterday_iso(user_id)
    if not _DATE_RE.match(value):
        raise ValueError(f"expected YYYY-MM-DD, got {value!r}")
    return value


def _journal_tag(iso_date: str) -> str:
    return f"journal/{iso_date}"


async def get_journal(
    config: Config, user_id: str, iso_date: str | None = None
) -> dict | None:
    """Return today's (or given date's) journal note, if any."""
    target = resolve_date(iso_date, user_id)
    notes = await store.list_notes(
        config, user_id, tag=_journal_tag(target), limit=1
    )
    return notes[0] if notes else None


async def ensure_today_journal(config: Config, user_id: str) -> dict:
    """Return today's journal note, creating an empty stub if absent.

    Idempotent: safe to call from heartbeat tick + GET handler + skill
    without producing duplicates. The get-then-save window is
    serialized by a per-(user, today) asyncio.Lock so two concurrent
    callers can't each miss the existence check.
    """
    today = timezone_util.today_iso(user_id)
    async with _journal_lock(user_id, today):
        existing = await get_journal(config, user_id, today)
        if existing:
            return existing
        tag = _journal_tag(today)
        return await store.save_note(
            config,
            user_id,
            content=f"# Journal — {today}\n",
            title=f"Journal — {today}",
            tags=[tag, "owner/user"],
        )


async def append_journal(
    config: Config,
    user_id: str,
    content: str,
    iso_date: str | None = None,
) -> dict:
    """Append to (or create) a daily journal note for the given date.

    Kicks off (fire-and-forget) a worker-model call to regenerate the
    title into a descriptive phrase once the page has enough content.

    Race protection: the get-then-write window is serialized by a
    per-(user, target) asyncio.Lock so concurrent task-completion +
    auto-capture writers can't each miss the existence check and
    create a fresh stub.
    """
    target = resolve_date(iso_date, user_id)
    tag = _journal_tag(target)

    timestamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    snippet = f"- {timestamp} — {content.strip()}"

    async with _journal_lock(user_id, target):
        existing = await get_journal(config, user_id, target)
        if existing:
            merged = f"{existing['content'].rstrip()}\n{snippet}"
            updated = await store.update_note(
                config, user_id, existing["id"], content=merged
            )
            result = updated or existing
        else:
            result = await store.save_note(
                config,
                user_id,
                content=f"# Journal — {target}\n\n{snippet}",
                title=f"Journal — {target}",
                tags=[tag, "owner/user"],
            )

    # Async title refresh — doesn't block the caller. Runs on the
    # background task loop; failures are logged, never raised.
    try:
        asyncio.create_task(
            _maybe_refresh_title(config, user_id, result["id"], target)
        )
    except Exception:
        logger.debug("could not schedule journal title refresh", exc_info=True)

    return result


_TITLE_PROMPT = """You are writing a short diary-entry title. Given today's bullet log below,
output a 4-10 word phrase that captures what actually happened. Use the user's language
if you can detect it, otherwise English. Plain text only — no quotes, no markdown, no prefix.

Examples:
- "Shipped LazyBrain redesign, fixed canvas bug"
- "Debugged Postgres migration, dinner with Maria"
- "Ремонт кода ECO router, созвон с клиентом"

Bullets:
{body}

Title:"""


async def _maybe_refresh_title(
    config: Config,
    user_id: str,
    note_id: str,
    iso_target: str,
) -> None:
    """Rewrite the journal title from its accumulated bullets via worker LLM.

    No-op unless the page is past the character / bullet threshold.
    Silent on failure — the journal still works with the static title.
    """
    try:
        note = await store.get_note(config, user_id, note_id)
        if not note:
            return
        body = note.get("content") or ""
        if len(body) < _MIN_CHARS_FOR_LLM_TITLE:
            return
        bullet_count = sum(1 for line in body.splitlines() if line.lstrip().startswith("- "))
        if bullet_count < _MIN_BULLETS_FOR_LLM_TITLE:
            return

        # Skip if the user already customised the title manually.
        current_title = (note.get("title") or "").strip()
        if current_title and not current_title.lower().startswith("journal "):
            return

        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter

        eco = EcoRouter(config, LLMRouter(config))
        messages = [
            LLMMessage(
                role="system",
                content="You write concise, factual diary titles. No quotes, no preamble.",
            ),
            LLMMessage(role="user", content=_TITLE_PROMPT.format(body=body[:2000])),
        ]
        response = await eco.chat(messages, user_id=user_id, role=ROLE_WORKER)
        raw = (response.content or "").strip() if response else ""
        if not raw:
            return
        # Strip obvious decorations
        raw = raw.strip('"').strip("'").strip().rstrip(".")
        raw = raw.splitlines()[0].strip()[:100]
        if len(raw) < 4:
            return
        new_title = f"{iso_target} — {raw}"
        # Preserve the canonical ``Journal — DATE`` name as an alias so inbound
        # ``[[Journal — DATE]]`` backlinks keep resolving after the rename
        # (FIX B — cross-user journal bug 2026-06-01). ``update_note`` also
        # auto-preserves the old title on any title change; passing the alias
        # explicitly makes the guarantee independent of that internal behaviour
        # and survives even if the note was retitled more than once.
        canonical = f"Journal — {iso_target}"
        existing_aliases = list(note.get("aliases") or [])
        if canonical not in existing_aliases:
            existing_aliases.append(canonical)
        await store.update_note(
            config,
            user_id,
            note_id,
            title=new_title,
            aliases=existing_aliases,
        )
        logger.info("Journal title refreshed for %s → %s", iso_target, new_title)
    except Exception:
        logger.debug("journal title refresh failed", exc_info=True)


async def list_journal(
    config: Config,
    user_id: str,
    *,
    limit: int = 14,
) -> list[dict]:
    """Return recent journal pages, newest first."""
    return await store.list_notes(
        config, user_id, tag="journal", limit=limit
    )


# Wikilink targets reject [, ], #, |, newline (see wikilinks._WIKILINK_RE).
# We strip those and cap at 100 chars so the resulting [[link]] always parses.
_WIKILINK_BAD = str.maketrans({"[": " ", "]": " ", "#": " ", "|": " ", "\n": " "})


def _sanitize_wikilink(title: str) -> str:
    cleaned = " ".join(title.translate(_WIKILINK_BAD).split())
    return cleaned[:100]


async def link_note_today(
    config: Config,
    user_id: str,
    *,
    title: str,
    kind: str,
) -> None:
    """Append a `[[Title]] — kind` bullet to today's journal page.

    Why: clicking the today node in the LazyBrain graph should reveal what
    actually happened today. The wikilink reindexer (`store._reindex_links`)
    converts the `[[Title]]` into a `note_links` row → graph edge.
    How to apply: call after any user-visible event whose target note already
    exists (auto_capture save, task completion). Silent on failure.
    """
    safe = _sanitize_wikilink(title)
    if not safe:
        return
    try:
        await append_journal(config, user_id, f"[[{safe}]] — {kind}")
    except Exception:
        logger.debug("link_note_today failed for %r", title, exc_info=True)
