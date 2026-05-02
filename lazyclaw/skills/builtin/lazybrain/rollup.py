"""Rollup admin skills — opt-in cascade crons + post-rollup source marking.

Three skills exposed to the agent:

- :class:`EnableWeeklyRollupSkill` — register the Sunday-night cron.
- :class:`EnableMonthlyRollupSkill` — register the 1st-of-month cron.
- :class:`MarkRolledUpSkill` — called by the cron-driven agent immediately
  after creating a rollup, to tag every source with ``#rolled-up`` +
  ``#source-of/<week>``. Sources stay in the DB and remain searchable
  when the agent passes ``include_rolled_up=True``.
"""
from __future__ import annotations

from lazyclaw.lazybrain import events, rollup as rollup_mod, store
from lazyclaw.skills.base import BaseSkill


class EnableWeeklyRollupSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_enable_weekly_rollup"

    @property
    def display_name(self) -> str:
        return "Enable weekly rollup"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Register a weekly heartbeat cron that tells the agent to "
            "summarise the past N days of notes into a [[rollup]] page "
            "every Sunday night. Idempotent — no-op if already registered. "
            "Source notes are kept in the DB and tagged #rolled-up so they "
            "hide from the default sidebar/graph but remain searchable."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cron": {
                    "type": "string",
                    "description": "Override schedule (default: Sunday 22:00 — '0 22 * * 0').",
                },
                "lookback_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 90,
                    "description": "How many days of notes the rollup eats per run. Default 7.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        cron = params.get("cron") or "0 22 * * 0"
        lookback = int(params.get("lookback_days") or 7)
        existing = await rollup_mod.has_weekly_rollup(self._config, user_id)
        if existing:
            return "Weekly rollup is already enabled."
        job_id = await rollup_mod.ensure_weekly_rollup(
            self._config, user_id, cron_expression=cron, lookback_days=lookback
        )
        if not job_id:
            return "❌ Could not register the weekly rollup cron (see logs)."
        return (
            f"✅ Weekly rollup enabled (cron `{cron}`, lookback {lookback} days). "
            f"Job id: {job_id[:8]}."
        )


class EnableMonthlyRollupSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_enable_monthly_rollup"

    @property
    def display_name(self) -> str:
        return "Enable monthly rollup"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Schedule a monthly cron job (1st of month at 22:00 user-local) "
            "that folds the past month's weekly rollups into a single "
            "[[monthly rollup]] note. Cascade: day 1–7 individual notes → "
            "day 8 weekly rollup → day 31 monthly rollup. Idempotent — "
            "call once to register; weeklies remain searchable via "
            "include_rolled_up=True."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cron": {
                    "type": "string",
                    "description": "Override schedule (default: 1st of month 22:00 — '0 22 1 * *').",
                },
                "lookback_days": {
                    "type": "integer",
                    "minimum": 7,
                    "maximum": 180,
                    "description": "How many days of weeklies the cascade eats. Default 31.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        cron = params.get("cron") or "0 22 1 * *"
        lookback = int(params.get("lookback_days") or 31)
        existing = await rollup_mod.has_monthly_rollup(self._config, user_id)
        if existing:
            return "Monthly rollup is already enabled."
        job_id = await rollup_mod.ensure_monthly_rollup(
            self._config, user_id, cron_expression=cron, lookback_days=lookback
        )
        if not job_id:
            return "❌ Could not register the monthly rollup cron (see logs)."
        return (
            f"✅ Monthly rollup enabled (cron `{cron}`, lookback {lookback} days). "
            f"Job id: {job_id[:8]}."
        )


class ListRollupsSkill(BaseSkill):
    """List the user's existing weekly/monthly rollup notes.

    Drives the sidebar Rollups section in the web UI and gives the agent
    a quick way to see which periods already have rollups before deciding
    whether to create a new one.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_list_rollups"

    @property
    def display_name(self) -> str:
        return "List rollups"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "List the user's existing rollup notes (weekly + monthly). "
            "Returns title (which IS the topic phrase), period tag, source "
            "count, and note ID. Used by the sidebar Rollups section and "
            "by the agent to check whether a period already has a rollup."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["weekly", "monthly", "any"],
                    "default": "any",
                    "description": "Filter by rollup tier — 'weekly', 'monthly', or 'any'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        period = (params.get("period") or "any").lower()
        limit = int(params.get("limit") or 50)
        # Filter on the kind/rollup tag — every rollup carries it. The
        # cascade tags hide rollup notes themselves from list_notes (since
        # monthly rollups have rolled-up tagged on the weeklies they
        # subsume), so we deliberately pass include_rolled_up=True here.
        candidates = await store.list_notes(
            self._config,
            user_id,
            tag="kind/rollup",
            include_rolled_up=True,
            limit=max(limit * 2, 100),
        )

        def _matches(note: dict) -> bool:
            tags = [t.lower() for t in (note.get("tags") or [])]
            if period == "weekly":
                return any(t.startswith("rollup/weekly") for t in tags)
            if period == "monthly":
                return any(t.startswith("rollup/monthly") for t in tags)
            return True

        rollups = [n for n in candidates if _matches(n)][:limit]
        if not rollups:
            return f"(no {period if period != 'any' else ''} rollups yet)"

        lines = [f"{len(rollups)} rollup(s):"]
        for n in rollups:
            tags = [t for t in (n.get("tags") or [])]
            period_tag = next(
                (t for t in tags if t.startswith("rollup/weekly/") or t.startswith("rollup/monthly/")),
                "?",
            )
            count_tag = next((t for t in tags if t.startswith("source-count/")), None)
            count = count_tag.split("/", 1)[1] if count_tag else "?"
            lines.append(
                f"• {n['title'] or '(untitled)'} — {period_tag} "
                f"({count} sources) [{n['id'][:8]}]"
            )
        return "\n".join(lines)


class MarkRolledUpSkill(BaseSkill):
    """Tag source notes with #rolled-up + #source-of/<period> after a rollup runs.

    Called by the cron-driven agent as the final step of weekly/monthly
    rollup creation. Originals stay in the DB; the tags hide them from
    the default sidebar/graph view (the web UI's `list_notes` defaults to
    `include_rolled_up=False`). Searches that pass `include_rolled_up=True`
    still find them, so wikilink navigation from the rollup back to its
    sources keeps working.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_mark_rolled_up"

    @property
    def display_name(self) -> str:
        return "Mark notes as rolled-up"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return (
            "After creating a weekly or monthly rollup, mark each source note "
            "as rolled-up so the default sidebar/graph hides it. Originals "
            "stay in DB and remain searchable via "
            "lazybrain_search_notes(include_rolled_up=True). Idempotent — "
            "re-marking a note that already has the tags is a no-op."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "source_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Note IDs to mark as rolled-up.",
                },
                "period_id": {
                    "type": "string",
                    "description": (
                        "ISO period this rollup covers — e.g. 'W23-2026' "
                        "for the weekly tier or 'M2026-05' for monthly."
                    ),
                },
                "rollup_id": {
                    "type": "string",
                    "description": "ID of the rollup note that subsumes these sources.",
                },
            },
            "required": ["source_ids", "period_id", "rollup_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        source_ids = params.get("source_ids") or []
        period_id = (params.get("period_id") or "").strip()
        rollup_id = (params.get("rollup_id") or "").strip()
        if not source_ids or not period_id or not rollup_id:
            return "❌ source_ids, period_id, and rollup_id are all required."

        marked = 0
        skipped = 0
        for source_id in source_ids:
            existing = await store.get_note(self._config, user_id, source_id)
            if not existing:
                skipped += 1
                continue
            existing_tags = list(existing.get("tags") or [])
            new_tags = list(existing_tags)
            for tag in ("rolled-up", f"source-of/{period_id}", f"in-rollup/{rollup_id}"):
                if tag not in new_tags:
                    new_tags.append(tag)
            if new_tags == existing_tags:
                # Already marked — idempotent skip.
                continue
            updated = await store.update_note(
                self._config, user_id, source_id, tags=new_tags
            )
            if updated:
                marked += 1
                events.publish_note_saved(
                    user_id,
                    updated["id"],
                    updated["title"],
                    updated["tags"],
                    source="agent",
                )
            else:
                skipped += 1

        return (
            f"✅ Marked {marked} note{'s' if marked != 1 else ''} as "
            f"rolled-up under {period_id}"
            + (f" ({skipped} skipped)" if skipped else "")
            + "."
        )
