"""Morning review — combines open tasks + recent notes into one digest.

Designed for "where should I start today?" / "what notes do I have?" queries.
The agent calls this when the user asks for a synthesis across the two
surfaces; otherwise it should call ``list_tasks`` or ``search_notes``
individually.

Zero LLM calls — pure data assembly. The agent's brain still composes the
final reply text, but the source material is deterministic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lazyclaw.config import Config
from lazyclaw.lazybrain import store as lb_store
from lazyclaw.lazybrain import timezone_util
from lazyclaw.skills.base import BaseSkill
from lazyclaw.tasks import store as task_store


_NOTE_KIND_TAGS = {"kind/note", "kind/idea", "kind/memory"}


def _today_str(user_id: str) -> str:
    return timezone_util.today_iso(user_id)


def _bucket_for_due(due_date: str | None, today: str) -> str:
    if not due_date:
        return "someday"
    if due_date < today:
        return "overdue"
    if due_date == today:
        return "today"
    # parse safely
    try:
        d = datetime.fromisoformat(due_date).date()
        t = datetime.fromisoformat(today).date()
    except ValueError:
        return "later"
    if (d - t).days <= 7:
        return "this_week"
    return "later"


class MorningReviewSkill(BaseSkill):
    """Bundle open tasks + recent notes for a single review prompt."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "morning_review"

    @property
    def display_name(self) -> str:
        return "Morning review"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return (
            "Combine open tasks (overdue / today / this week) with the user's "
            "recent personal notes (kind/note, kind/idea, kind/memory) into a "
            "single digest. Call this when the user asks 'where should I "
            "start?', 'what's on my plate?', or 'what notes do I have?' — "
            "anything that crosses the task and notes surfaces. Returns "
            "structured JSON-ready data; do NOT call list_tasks AND "
            "search_notes separately when this is a better fit."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Max open tasks to include. Default 12.",
                },
                "note_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Max recent notes to include. Default 10.",
                },
                "since_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "description": "How far back to look for notes. Default 7.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        task_limit = int((params or {}).get("task_limit", 12))
        note_limit = int((params or {}).get("note_limit", 10))
        since_days = int((params or {}).get("since_days", 7))

        today = _today_str(user_id)

        # Open tasks across all buckets — we sort + bucket below.
        open_tasks = await task_store.list_tasks(
            self._config, user_id, status="todo",
        )
        in_progress = await task_store.list_tasks(
            self._config, user_id, status="in_progress",
        )
        all_open = (open_tasks or []) + (in_progress or [])

        buckets: dict[str, list[dict]] = {
            "overdue": [], "today": [], "this_week": [],
            "later": [], "someday": [],
        }
        for t in all_open:
            buckets[_bucket_for_due(t.get("due_date"), today)].append(t)

        # Apply limit to the *combined* view (overdue + today + this_week first).
        priority_order = ["overdue", "today", "this_week", "later", "someday"]
        ordered: list[dict] = []
        for bucket_key in priority_order:
            for t in buckets[bucket_key]:
                t["_bucket"] = bucket_key
                ordered.append(t)
                if len(ordered) >= task_limit:
                    break
            if len(ordered) >= task_limit:
                break

        # Recent notes — pull more than note_limit and filter client-side
        # to "personal note kinds". Cheaper than per-tag round-trips.
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=since_days)
        ).isoformat()
        recent_pool = await lb_store.list_notes(
            self._config, user_id, limit=max(note_limit * 4, 40),
        )
        recent_notes: list[dict] = []
        for n in recent_pool or []:
            tags = set(n.get("tags") or [])
            if not (tags & _NOTE_KIND_TAGS):
                continue
            if (n.get("created_at") or "") < cutoff:
                continue
            recent_notes.append({
                "id": n.get("id"),
                "title": n.get("title") or "(untitled)",
                "tags": list(tags),
                "created_at": n.get("created_at"),
                "snippet": ((n.get("content") or "").strip().splitlines() or [""])[0][:140],
            })
            if len(recent_notes) >= note_limit:
                break

        lines: list[str] = []
        lines.append(f"# Morning review — {today}\n")

        # Tasks block
        n_overdue = len(buckets["overdue"])
        n_today = len(buckets["today"])
        n_week = len(buckets["this_week"])
        lines.append(
            f"## Tasks ({n_overdue} overdue · {n_today} today · {n_week} this week)\n"
        )
        if not ordered:
            lines.append("- (no open tasks)\n")
        else:
            for t in ordered:
                badge = {
                    "overdue": "🔴",
                    "today": "🟠",
                    "this_week": "🟢",
                    "later": "·",
                    "someday": "·",
                }.get(t.get("_bucket"), "·")
                due = f" ({t.get('due_date')})" if t.get("due_date") else ""
                pri = (t.get("priority") or "").lower()
                pri_chip = f" [{pri}]" if pri in ("urgent", "high") else ""
                lines.append(
                    f"- {badge} {t.get('title') or '(untitled)'}{pri_chip}{due}"
                )

        # Notes block
        lines.append(f"\n## Recent notes (last {since_days} days)\n")
        if not recent_notes:
            lines.append("- (no recent notes)\n")
        else:
            for n in recent_notes:
                kind_tag = next(
                    (t for t in n["tags"] if t.startswith("kind/")), "kind/note",
                )
                kind_label = kind_tag.split("/", 1)[1].capitalize()
                lines.append(f"- 📝 _{kind_label}_ — {n['title']}\n  > {n['snippet']}")

        # Suggestion footer — gives the brain a starting point without doing
        # the prioritization itself (that's the agent's call).
        lines.append("")
        if buckets["overdue"]:
            lines.append("**Suggested start:** clear the overdue task above.")
        elif buckets["today"]:
            top = buckets["today"][0]
            lines.append(
                f"**Suggested start:** \"{top.get('title', 'first today task')}\"."
            )
        elif buckets["this_week"]:
            lines.append("**Suggested start:** pick one from this week before they pile up.")
        elif recent_notes:
            lines.append("**Suggested start:** review the most recent note above.")
        else:
            lines.append("**Suggested start:** plate is clear — capture an idea or plan something.")

        return "\n".join(lines)
