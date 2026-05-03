"""Worker-LLM suggester for new tasks: deadline + project (category).

Called synchronously from ``AddTaskSkill`` BEFORE the row is inserted, so
the user's confirmation reply shows the inferred project + suggested
deadline. Replaces the old fire-and-forget ``_smart_enrich`` path.

Pattern mirrors ``lazyclaw/lazybrain/metadata_suggest.py``:
- ROLE_WORKER (cheap, local Ollama Gemma 4 E2B by default)
- STRICT JSON output, parsed leniently
- Graceful fallback when Ollama is down (returns all-None values)
- Hard 3s timeout so add_task never blocks the user

The suggester uses the user's RECENT tasks as context so it lands new
tasks in existing category buckets instead of inventing new ones.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from lazyclaw.config import Config

logger = logging.getLogger(__name__)


# Bug / error / breakage keywords. When the title hits, the prompt nudges
# the model to prefer an existing code/project category (e.g. "lazyclaw")
# over a generic "tech" bucket. EN + ES so the Madrid user gets both.
_BUG_HINT_RE = re.compile(
    r"\b(bug|error|fix|broken|doesn'?t\s+work|"
    r"crash(?:e[sd])?|regression|fails?|"
    r"503|500|404|exception|stacktrace|stack\s+trace|"
    r"roto|fallo|falla|arregla(?:r)?)\b",
    re.IGNORECASE,
)

# Time-sensitivity keywords that mean "ask the user for a deadline" when
# the LLM's confidence is low. EN + ES.
_TIME_SENSITIVE_RE = re.compile(
    r"\b(call|llamar|llamada|"
    r"meeting|reunión|reunion|"
    r"appointment|cita|"
    r"submit|enviar|entregar|"
    r"deadline|plazo|fecha\s+límite|fecha\s+limite|"
    r"interview|entrevista|"
    r"asap|urgent|urgente|"
    r"by\s+(?:tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)|"
    r"before\s+\w+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntakeSuggestion:
    """Immutable result of a smart-intake call.

    Any field can be ``None`` when the LLM was unavailable or unsure.
    Callers must treat each field independently — partial results are
    expected and useful (e.g. category set but no deadline).
    """
    deadline_iso: str | None
    category: str | None
    confidence: str  # "high" | "medium" | "low" | "none"
    reason: str
    needs_user_clarification: bool
    source: str  # "llm" | "none"


_PROMPT = """You help organize a personal task list. Today is {today} ({tz}).

A new task is being added:
- Title: {title}
- Description: {description}
- Priority: {priority}

The user already has these category buckets in active use: {existing_cats}
Recent active tasks (most recent first):
{recent_tasks}

Your job: infer two things from the title + description.

1. **deadline_iso**: ISO datetime in the user's timezone ({tz}) for when this
   task should be done by. Only set this when the title gives strong cues
   (a named day, "tomorrow", "ASAP", a duration like "in 2 hours", a
   contextual deadline like a court hearing or flight). If the task is
   open-ended ("read book", "research X"), leave it null.

2. **category**: pick ONE of the user's existing categories above when the
   new task belongs to the same project. ONLY invent a new category when
   none of the existing buckets fit. {bug_hint}

3. **confidence**: "high" when the title is unambiguous; "medium" when
   plausible; "low" when it's a guess.

Output STRICT JSON, no prose, no fence:
{{"deadline_iso": "2026-05-08T10:00:00+02:00" or null,
  "category": "lazyclaw" or null,
  "confidence": "high" or "medium" or "low",
  "reason": "short why-this"}}"""


async def suggest_intake(
    config: Config,
    user_id: str,
    title: str,
    description: str | None = None,
    priority: str = "medium",
    timeout_s: float = 3.0,
) -> IntakeSuggestion:
    """Return a deadline + category suggestion for a new task.

    Never raises. Returns an all-None ``IntakeSuggestion`` on any failure
    (Ollama down, parse error, timeout) so ``AddTaskSkill`` can proceed
    without enrichment exactly as it did before this module existed.
    """
    title = (title or "").strip()
    if not title:
        return _empty_suggestion()

    recent_tasks, existing_cats = await _gather_recent_context(config, user_id)
    needs_clarif_hint = bool(_TIME_SENSITIVE_RE.search(title)) or priority in {
        "high", "urgent",
    }

    today = datetime.now(timezone.utc).date().isoformat()
    bug_hint = ""
    if _BUG_HINT_RE.search(title):
        bug_hint = (
            "This task looks like a bug/error report — strongly prefer the "
            "existing code-project category (e.g. an app or repo name from "
            "the recent tasks) over generic 'tech' or 'work'."
        )

    prompt = _PROMPT.format(
        today=today,
        tz="Europe/Madrid",
        title=title[:200],
        description=(description or "(none)")[:400],
        priority=priority,
        existing_cats=", ".join(sorted(existing_cats)) or "(none yet)",
        recent_tasks=_format_recent(recent_tasks),
        bug_hint=bug_hint,
    )

    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter
    except Exception as exc:  # pragma: no cover — import-only failure
        logger.debug("smart_intake imports failed: %s", exc)
        return _empty_suggestion(needs_clarif=needs_clarif_hint)

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await asyncio.wait_for(
            eco.chat(
                messages=[
                    LLMMessage(role="system", content="You output JSON only."),
                    LLMMessage(role="user", content=prompt),
                ],
                user_id=user_id,
                role=ROLE_WORKER,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.debug("smart_intake worker LLM timed out after %.1fs", timeout_s)
        return _empty_suggestion(needs_clarif=needs_clarif_hint)
    except Exception as exc:
        logger.debug("smart_intake worker LLM failed: %s", exc)
        return _empty_suggestion(needs_clarif=needs_clarif_hint)

    raw = (resp.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("smart_intake JSON parse failed: %s", raw[:200])
        return _empty_suggestion(needs_clarif=needs_clarif_hint)

    if not isinstance(data, dict):
        return _empty_suggestion(needs_clarif=needs_clarif_hint)

    deadline_iso = _validate_deadline(data.get("deadline_iso"))
    category = _validate_category(data.get("category"))
    confidence = (str(data.get("confidence") or "low")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    reason = str(data.get("reason") or "")[:200]

    needs_clarif = (
        deadline_iso is None
        and needs_clarif_hint
    )

    return IntakeSuggestion(
        deadline_iso=deadline_iso,
        category=category,
        confidence=confidence if (deadline_iso or category) else "none",
        reason=reason,
        needs_user_clarification=needs_clarif,
        source="llm" if (deadline_iso or category) else "none",
    )


# ── Helpers ────────────────────────────────────────────────────────────


def _empty_suggestion(*, needs_clarif: bool = False) -> IntakeSuggestion:
    return IntakeSuggestion(
        deadline_iso=None,
        category=None,
        confidence="none",
        reason="",
        needs_user_clarification=needs_clarif,
        source="none",
    )


async def _gather_recent_context(
    config: Config, user_id: str,
) -> tuple[list[dict], set[str]]:
    """Return (recent_tasks_list, existing_categories_set).

    Pulls up to 30 active+recent tasks. Categories are extracted from those
    rows so the suggester sees the user's actual buckets, not a hardcoded
    list. Failure → ([], set()) so the suggester can still try with no
    context.
    """
    try:
        from lazyclaw.tasks.store import list_tasks
        all_tasks = await list_tasks(
            config, user_id, status="all", owner="user",
        )
    except Exception:
        logger.debug("smart_intake list_tasks failed", exc_info=True)
        return [], set()

    recent = all_tasks[:30]
    cats = {
        (t.get("category") or "").strip()
        for t in all_tasks
        if t.get("category")
    }
    cats.discard("")
    return recent, cats


def _format_recent(tasks: list[dict]) -> str:
    """Render recent tasks as a compact bullet list for the prompt."""
    if not tasks:
        return "(no recent tasks)"
    lines: list[str] = []
    for t in tasks[:10]:
        title = (t.get("title") or "(untitled)").strip()[:80]
        cat = t.get("category") or "—"
        lines.append(f"- [{cat}] {title}")
    return "\n".join(lines)


def _validate_deadline(value: Any) -> str | None:
    """Accept null or a parseable ISO datetime; reject anything else."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt <= datetime.now(timezone.utc):
        return None
    return dt.isoformat()


def _validate_category(value: Any) -> str | None:
    """Accept a short non-empty lowercase token; reject anything else."""
    if not value or not isinstance(value, str):
        return None
    cat = value.strip().lower()
    if not cat or len(cat) > 40:
        return None
    # Disallow free-form spaces so categories stay tag-like.
    cat = re.sub(r"\s+", "-", cat)
    return cat
