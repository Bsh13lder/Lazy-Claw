"""Retention maintenance for the LazyBrain note store.

The store is ~93.5% auto-captured notes (2,676 of 2,863 — audit 2026-08-14).
Three writers produce that flood, none of them user-initiated:

* ``lazybrain/auto_capture.py`` — fires on EVERY user message.
* ``skills/builtin/browser_actions/read_open.py`` + ``browser/site_memory.py``
  — one ``#visit #site-memory`` note per landed URL.
* ``tasks/store.py`` — one mirror note per task create + per status transition.

Retrieval quality is a signal-to-noise problem, so this module walks the
noise back out of the *default* recall surfaces without destroying anything.

Why ``archived = 1`` and not ``deleted_at``
-------------------------------------------
The ``notes.archived`` column (added in the Phase 2 migration,
``db/connection.py:181``) is already honoured by every default reader —
``store.list_notes``, ``store.search_notes`` (via ``list_notes``),
``store.list_memory_notes``, and all three ``graph.py`` queries — yet NO
writer has ever set it (2026-08-14 audit finding #7: the isolation is a
documented no-op). That makes it the ideal mechanism here:

* **Reversible** — one ``UPDATE ... SET archived = 0`` restores everything.
* **Invisible to retrieval, not to the user** — ``include_archived=True``
  still surfaces the rows on demand (``recall`` skill, graph toggle).
* **Zero decrypt cost** — every predicate below is a plaintext column
  (``tags`` JSON, ``importance``, ``pinned``, ``memory_type``,
  ``created_at``, ``archived``, ``deleted_at``), so the whole pass is a
  single SQL ``UPDATE`` per rule. No DEK derivation, no per-note round trip.

Deliberate omission: ``updated_at`` is NOT bumped. ``store.get_note_changes``
feeds the mobile offline-sync delta off ``updated_at`` and does not filter
archived rows — touching it would push thousands of archived notes down to
every client on the next pull. Archiving is a server-side retrieval concern,
not a content change.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain.memory_types import AUTO_INJECT_TYPES

logger = logging.getLogger(__name__)


# ─── Retention policy constants ───────────────────────────────────────────

#: Rule A — ``#auto`` notes must be at least this old before archiving.
STALE_AUTO_DAYS: int = 30

#: Rule A — only low-signal notes are archived. ``importance`` defaults to
#: 5 (``schema.sql``), so this is "default or below".
STALE_AUTO_MAX_IMPORTANCE: int = 5

#: Rule B — per-URL browsing breadcrumbs age out much faster; they have no
#: long-term value once the browsing session is over.
VISIT_NOTE_DAYS: int = 14

#: Tag written by every auto-capture / mirror writer listed in the module
#: docstring.
AUTO_TAG: str = "auto"

#: Tags identifying per-URL browser visit notes (``read_open.py:507``,
#: ``site_memory.py:171``).
VISIT_TAGS: tuple[str, ...] = ("visit", "site-memory")


def _cutoff(days: int, *, now: datetime | None = None) -> str:
    """Timestamp ``days`` before ``now``, in the store's own text format.

    ``store._now()`` writes ``"%Y-%m-%d %H:%M:%S.%f"``; older rows written
    by the schema default (``datetime('now')``) lack the fractional part.
    Both are zero-padded fixed-width through the seconds field, so a plain
    lexicographic ``<`` against this string is correct for either shape and
    stays index-friendly on ``idx_notes_user_created``.
    """
    reference = now or datetime.now(timezone.utc)
    return (reference - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.%f")


def _tag_like(tag: str) -> str:
    """LIKE pattern matching one tag inside the plaintext ``tags`` JSON array.

    ``store._dump_tags`` lowercases, strips ``#`` and JSON-encodes, so a tag
    always appears as the exact token ``"auto"``. The surrounding quotes are
    what make this a whole-token match — ``%"auto"%`` cannot hit
    ``"auto-generated"``.
    """
    return f'%"{tag}"%'


# ─── Shared guards ────────────────────────────────────────────────────────
#
# Applied by BOTH rules. Each one is an explicit "keep" signal:
#   deleted_at IS NULL   — never touch tombstones (idempotence + sync safety)
#   archived = 0         — never re-archive (makes the pass idempotent)
#   pinned = 0           — pinning is a deliberate user keep-forever action
#
# The NULL-tolerant shapes matter: ``archived`` and ``pinned`` are NULL on
# rows written before their respective migrations, and in SQLite
# ``NULL = 0`` is NULL, which WHERE treats as false — without the guard
# those rows would be silently unreachable.
_KEEP_GUARDS: tuple[str, ...] = (
    "deleted_at IS NULL",
    "(archived IS NULL OR archived = 0)",
    "(pinned IS NULL OR pinned = 0)",
)


def _stale_auto_clauses(
    cutoff: str, max_importance: int
) -> tuple[str, list]:
    """Rule A — stale, low-importance, untyped ``#auto`` noise.

    A note is archived only when ALL of these hold:

    1. tagged ``auto``
    2. ``created_at`` older than the cutoff (default 30 days)
    3. ``importance <= 5`` (NULL counts as low)
    4. not pinned
    5. ``memory_type`` NOT in :data:`AUTO_INJECT_TYPES` — durable typed
       memories (``user``/``feedback``/``project``/``reference``/``fact``)
       are never archived by this rule, no matter how old or noisy. NULL
       ``memory_type`` (pre-backfill rows) IS archivable: ``memory_types.
       is_auto_inject_type`` already fails closed on None, so those rows
       are not in the injected pool either.
    6. not already archived / soft-deleted

    KNOWN REACH LIMIT (deliberate, conservative): condition 5 is a strong
    filter because ``fact`` — the DEFAULT classification in
    ``memory_types.infer_memory_type`` — is itself inside
    ``AUTO_INJECT_TYPES``. Checked against the real writers, that means
    auto-capture ``til``/``price``/``deadline``/``command``/``idea`` cards
    (→ ``fact``), ``decision`` cards and task mirrors (→ ``project``),
    ``contact`` cards (→ ``reference``), lesson cards, expense mirrors and
    background-research mirrors (→ ``fact``) are ALL protected. What Rule A
    actually reaches today is the ``session-log`` family (``session-end`` /
    ``daily-log`` / ``journal`` shapes from ``memory/layers.py``), ``other``,
    and pre-backfill NULL rows. Rule B below is what clears the browsing
    breadcrumbs. Widening this rule (e.g. exempting only an explicitly
    classified ``fact`` from the default fallback) is a policy decision that
    changes what the cached system prompt can see — make it deliberately,
    not as a side effect.
    """
    types = sorted(AUTO_INJECT_TYPES)
    placeholders = ", ".join("?" for _ in types)
    clauses = [
        "user_id = ?",
        *_KEEP_GUARDS,
        "tags LIKE ?",
        "created_at < ?",
        "(importance IS NULL OR importance <= ?)",
        f"(memory_type IS NULL OR memory_type NOT IN ({placeholders}))",
    ]
    params: list = [
        # user_id is bound by the caller (kept first for readability).
        _tag_like(AUTO_TAG),
        cutoff,
        max_importance,
        *types,
    ]
    return " AND ".join(clauses), params


def _visit_note_clauses(cutoff: str) -> tuple[str, list]:
    """Rule B — per-URL browsing breadcrumbs older than the cutoff.

    Archived regardless of ``importance`` AND regardless of ``memory_type``.
    The typed guard is deliberately absent here: visit notes carry tags
    (``visit``/``site-memory``/``domain/x``) that match none of the typed
    allowlists in ``memory_types.py``, so they always classify as the
    DEFAULT type ``fact`` — which is inside ``AUTO_INJECT_TYPES``. Applying
    the Rule A typed guard would make this rule a total no-op.

    The ``pinned`` guard is retained: a pinned visit note is an explicit
    user keep signal and outranks the retention policy.
    """
    tag_clause = " OR ".join("tags LIKE ?" for _ in VISIT_TAGS)
    clauses = [
        "user_id = ?",
        *_KEEP_GUARDS,
        f"({tag_clause})",
        "created_at < ?",
    ]
    params: list = [*(_tag_like(t) for t in VISIT_TAGS), cutoff]
    return " AND ".join(clauses), params


async def archive_stale_auto_notes(
    config: Config,
    user_id: str,
    *,
    stale_auto_days: int = STALE_AUTO_DAYS,
    visit_days: int = VISIT_NOTE_DAYS,
    max_importance: int = STALE_AUTO_MAX_IMPORTANCE,
    now: datetime | None = None,
) -> int:
    """Archive stale auto-captured noise for ``user_id``. Returns the count.

    Two SQL-level rules (see :func:`_stale_auto_clauses` and
    :func:`_visit_note_clauses` for the exact predicates). Both are scoped
    by ``user_id`` — no cross-user reach — and both skip rows that are
    already archived, which makes a second run in the same window a no-op.

    Never raises: a maintenance sweep must not be able to take down its
    caller (the heartbeat tick). Failures are logged and reported as 0.

    ``now`` is injectable purely so tests can pin the cutoff boundary.
    """
    stale_cutoff = _cutoff(stale_auto_days, now=now)
    visit_cutoff = _cutoff(visit_days, now=now)

    stale_where, stale_params = _stale_auto_clauses(stale_cutoff, max_importance)
    visit_where, visit_params = _visit_note_clauses(visit_cutoff)

    archived = 0
    try:
        async with db_session(config) as db:
            for where, params in (
                (stale_where, stale_params),
                (visit_where, visit_params),
            ):
                cursor = await db.execute(
                    f"UPDATE notes SET archived = 1 WHERE {where}",
                    (user_id, *params),
                )
                archived += cursor.rowcount or 0
            await db.commit()
    except Exception:
        logger.warning(
            "lazybrain archive sweep failed for user %s", user_id, exc_info=True,
        )
        return 0

    if archived:
        logger.info(
            "lazybrain archive sweep: user=%s archived=%d "
            "(auto>%dd/imp<=%d, visit>%dd)",
            user_id, archived, stale_auto_days, max_importance, visit_days,
        )
    return archived
