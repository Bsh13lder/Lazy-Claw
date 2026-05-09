"""Bare-NL task router — local-only, zero brain LLM calls.

The user types things like:
  - "snooze upwork 2h"
  - "postpone dentist Friday 10am"
  - "complete the upwork application"
  - "cancel dentist"
  - "when is the upwork due?"
  - "set priority high on the dentist task"
  - "clear deadline on upwork"

Plus Spanish equivalents (Madrid user). All of these are handled by
existing skills (``RescheduleTaskSkill`` / ``CompleteTaskSkill`` /
``DeleteTaskSkill`` / ``AskAboutTaskSkill``) but routing them through
the brain LLM costs a roundtrip and sometimes the brain picks the
wrong skill or the wrong fields.

This router intercepts at message ingress: if the message starts with a
known task verb AND we can unambiguously resolve the task, the action
runs locally and we return a reply. Otherwise we return ``None`` and
the caller (Telegram message handler) lets the brain take it.

Disambiguation rule (per user choice 2026-05-08): when 2+ tasks fuzzy-
match the name, fall through to the brain. The brain has more context
to disambiguate, and we don't dead-end the user with a numbered list
they have to acknowledge.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ── Verb regex — message-start anchor so we don't false-positive
# mid-conversation. EN + ES (the Madrid user types both).
# Captures: (verb_kind, captured groups depending on regex).
# Each regex must have a single named group ``rest`` carrying everything
# AFTER the verb so the dispatcher can split task name from phrase.

_RESCHEDULE_RE = re.compile(
    r"^\s*(?:snooze|delay|defer|postpone|push|move|reschedule|"
    r"aplaza(?:r)?|posponer|mueve|mover|cambiar)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)

_CLEAR_DEADLINE_RE = re.compile(
    r"^\s*(?:clear|remove|wipe|elimina(?:r)?|borra(?:r)?)\s+"
    r"(?:(?:the|el|la)\s+)?(?:deadline|due\s+date|reminder|recordatorio|fecha)"
    r"(?:\s+(?:on|of|de|del|para)\s+(?P<rest>.+))?$",
    re.IGNORECASE,
)

_PRIORITY_RE = re.compile(
    r"^\s*(?:set\s+)?priority\s+(?P<level>urgent|high|medium|low|"
    r"urgente|alta|media|baja)\s+(?:on|of|de|del|para)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)

_COMPLETE_RE = re.compile(
    r"^\s*(?:complete|finish|mark\s+(?:as\s+)?done|"
    r"terminar|terminado|acabar|acabado|completar|completado|"
    r"hecho)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)

_DELETE_RE = re.compile(
    r"^\s*(?:cancel|delete|remove|"
    r"cancela(?:r)?|elimina(?:r)?|borra(?:r)?|"
    r"quita(?:r)?)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)

_WHENISDUE_RE = re.compile(
    r"^\s*(?:when(?:'?s)?\s+is\s+|when'?s\s+|"
    r"what(?:'?s)?\s+(?:the\s+)?(?:deadline|due\s+date)\s+(?:on|of|for)\s+|"
    r"cu[aá]ndo\s+es\s+|cu[aá]ndo\s+vence\s+)"
    r"(?P<rest>.+?)(?:\s+due)?\??\s*$",
    re.IGNORECASE,
)

# Progress capture verbs — bare NL like "started X", "working on X",
# "stuck on X". The verb maps to a kind; the rest of the message after
# the task name is the optional free-text note. Each verb has its own
# regex so we can tag the kind without a second pass.
_PROGRESS_VERBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(
        r"^\s*(?:started|starting|empezar|empez[aá]ndo|"
        r"empec[eé](?:\s+a)?)\s+(?P<rest>.+)$",
        re.IGNORECASE,
    ), "started"),
    (re.compile(
        r"^\s*(?:working\s+on|trabajando\s+en|trabajo\s+en|"
        r"on\s+it[:\s]+|in\s+the\s+middle\s+of)\s+(?P<rest>.+)$",
        re.IGNORECASE,
    ), "working"),
    (re.compile(
        r"^\s*(?:halfway\s+(?:through\s+)?|midway\s+through\s+|"
        r"progress\s+on\s+|progreso\s+(?:en\s+)?|avance\s+(?:en\s+)?|"
        r"update\s+on\s+)(?P<rest>.+)$",
        re.IGNORECASE,
    ), "progress"),
    (re.compile(
        r"^\s*(?:stuck\s+(?:on|with)\s+|atorado\s+(?:en\s+|con\s+)?|"
        r"atrapado\s+(?:en\s+|con\s+)?|atascado\s+(?:en\s+|con\s+)?)"
        r"(?P<rest>.+)$",
        re.IGNORECASE,
    ), "stuck"),
    (re.compile(
        r"^\s*(?:blocked\s+(?:on|by)\s+|bloqueado\s+(?:en\s+|por\s+)?)"
        r"(?P<rest>.+)$",
        re.IGNORECASE,
    ), "blocked"),
    (re.compile(
        r"^\s*(?:paused?|pausa(?:r)?|pausado|stop\s+working\s+on)\s+"
        r"(?P<rest>.+)$",
        re.IGNORECASE,
    ), "paused"),
    (re.compile(
        r"^\s*(?:resumed?|resuming|retomar|retomado|back\s+on|"
        r"back\s+to)\s+(?P<rest>.+)$",
        re.IGNORECASE,
    ), "resumed"),
)

# Drop "the / la / el" prefixes from a candidate task name so the user
# can say "complete the upwork task" and we strip the article noise
# before fuzzy-matching.
_ARTICLE_RE = re.compile(
    r"^\s*(?:the|la|el|los|las)\s+", re.IGNORECASE,
)

_PRIORITY_MAP = {
    "urgent": "urgent", "urgente": "urgent",
    "high": "high", "alta": "high",
    "medium": "medium", "media": "medium",
    "low": "low", "baja": "low",
}

# Common trailing noise words — strip them when isolating the task name.
_NOISE_TRAILING_RE = re.compile(
    r"\b(?:task|tarea|job|item)\s*$",
    re.IGNORECASE,
)


def _strip_articles(text: str) -> str:
    cleaned = _ARTICLE_RE.sub("", text)
    cleaned = _NOISE_TRAILING_RE.sub("", cleaned).strip()
    return cleaned


# ── Fuzzy-match — multi-result version ────────────────────────────────


def _all_fuzzy_matches(tasks: list[dict], name: str) -> list[dict]:
    """Return ALL tasks whose title contains the name (or vice versa).

    Different from ``_fuzzy_match_task`` (which returns the first hit) —
    here we want to count, so we can fall through to the brain on
    ambiguous matches per the user-chosen behaviour.
    """
    needle = (name or "").lower().strip()
    if not needle:
        return []
    out: list[dict] = []
    # Exact match shortcuts ambiguity.
    for t in tasks:
        if (t.get("title") or "").lower() == needle:
            return [t]
    seen_ids: set[str] = set()
    for t in tasks:
        title = (t.get("title") or "").lower()
        if needle in title or title in needle:
            tid = str(t.get("id") or "")
            if tid not in seen_ids:
                seen_ids.add(tid)
                out.append(t)
    return out


# ── Reschedule-phrase splitter ─────────────────────────────────────────
#
# For "snooze upwork 2h" / "postpone dentist Friday 10am" the rest text
# is "<task name> <time phrase>". We don't know where the boundary is.
#
# Strategy: try progressively longer prefixes of `rest` as the task name
# and keep the longest one that fuzzy-matches a single task. The phrase
# is whatever's left.


def _strict_contains_matches(tasks: list[dict], needle: str) -> list[dict]:
    """Strict ``needle in title`` matches — used for prefix probing.

    Distinct from ``_all_fuzzy_matches`` which is bidirectional. For
    prefix splitting we want only "is this prefix actually a substring
    of a task title?" — bidirectional would accept any prefix shorter
    than a title as "matched" once the prefix grows past the title's
    own length.
    """
    needle = (needle or "").lower().strip()
    if not needle:
        return []
    return [
        t for t in tasks
        if needle in (t.get("title") or "").lower()
    ]


def _split_name_and_phrase(rest: str, tasks: list[dict]) -> tuple[str, str, list[dict]]:
    """Find the longest prefix of ``rest`` that's a substring of exactly
    one task title.

    Returns (name, phrase, matches). When ``matches`` is empty or has
    >1 entries the caller should fall through to the brain.

    Strict prefix probing: we keep extending the prefix only as long as
    it remains a substring of at least one title. The moment the prefix
    grows past what any title contains, we stop and return the last
    successful prefix's match-set.
    """
    rest = _strip_articles(rest.strip())
    tokens = rest.split()
    if not tokens:
        return "", "", []

    best_match: list[dict] | None = None
    best_idx = 0
    for i in range(1, len(tokens) + 1):
        candidate = " ".join(tokens[:i])
        candidate_clean = _strip_articles(candidate)
        if not candidate_clean:
            continue
        matches = _strict_contains_matches(tasks, candidate_clean)
        if not matches:
            # Prefix grew past any title — stop and use the last hit.
            break
        if len(matches) == 1:
            best_match = matches
            best_idx = i
            # Keep going only if a longer prefix is still strict-
            # contained in this same title (handles "dentist" vs
            # "dentist appointment" both narrowing to one task).
            continue
        # Multiple strict matches at this length. Save as a fallback
        # so the caller can see ambiguity, but keep extending.
        if best_match is None:
            best_match = matches
            best_idx = i

    if best_match is None or best_idx == 0:
        whole = _strip_articles(rest)
        # Fall back to bidirectional fuzzy here — gives the caller a
        # final shot at recognizing tasks the user named loosely.
        return whole, "", _all_fuzzy_matches(tasks, whole)

    name = " ".join(tokens[:best_idx])
    phrase = " ".join(tokens[best_idx:]).strip()
    return name, phrase, best_match


# ── Top-level entry point ─────────────────────────────────────────────


async def try_route_task_nl(
    config, user_id: str, message: str,
) -> str | None:
    """Try to handle ``message`` as a bare-NL task command.

    Returns the reply text on success, or ``None`` to indicate the
    caller should fall through to the brain. Never raises — any error
    in the local path falls through.
    """
    if not message or not message.strip():
        return None
    msg = message.strip()

    # Cheap pre-check: does it START with a verb-class word? Saves the
    # cost of a list_tasks call when the message is clearly chat.
    if not _looks_like_task_verb(msg):
        return None

    try:
        from lazyclaw.tasks.store import list_tasks
        tasks = await list_tasks(
            config, user_id, status="all", owner="user",
        )
    except Exception:
        logger.debug("task_nl_router list_tasks failed", exc_info=True)
        return None
    if not tasks:
        return None

    # Try each verb regex in order. First match wins.
    try:
        if (m := _PRIORITY_RE.match(msg)):
            return await _handle_priority(
                config, user_id, m.group("level"), m.group("rest"), tasks,
            )
        if (m := _CLEAR_DEADLINE_RE.match(msg)):
            return await _handle_clear_deadline(
                config, user_id, m.group("rest"), tasks,
            )
        if (m := _RESCHEDULE_RE.match(msg)):
            return await _handle_reschedule(
                config, user_id, m.group("rest"), msg, tasks,
            )
        if (m := _COMPLETE_RE.match(msg)):
            return await _handle_complete(
                config, user_id, m.group("rest"), tasks,
            )
        if (m := _DELETE_RE.match(msg)):
            return await _handle_delete(
                config, user_id, m.group("rest"), tasks,
            )
        if (m := _WHENISDUE_RE.match(msg)):
            return await _handle_whenisdue(
                config, user_id, m.group("rest"), tasks,
            )
        # Progress verbs — try each kind in order, first match wins.
        for pattern, kind in _PROGRESS_VERBS:
            if (m := pattern.match(msg)):
                return await _handle_progress(
                    config, user_id, kind, m.group("rest"), tasks,
                )
    except Exception:
        logger.debug("task_nl_router dispatch failed", exc_info=True)
        return None
    return None


def _looks_like_task_verb(message: str) -> bool:
    """Cheapest possible filter — are we starting with a known verb?

    Lets routine chat ("hey what's up") skip the list_tasks call
    entirely.
    """
    head = message[:32].lower()
    triggers = (
        "snooze ", "delay ", "defer ", "postpone ", "push ", "move ",
        "reschedule ",
        "aplaza", "posponer", "mueve", "mover",
        "clear ", "remove ", "wipe ", "elimina", "borra",
        "set priority ", "priority ",
        "complete ", "finish ", "mark ", "terminar", "hecho ", "acabar",
        "cancel ", "delete ", "cancela", "quita",
        "when is ", "when's ", "whens ", "what's the deadline",
        "cuando es ", "cuándo es ", "cuando vence", "cuándo vence",
        # Progress verbs (Tier 1)
        "started ", "starting ", "empezar ", "empez", "empec",
        "working on ", "trabajando ", "trabajo en ", "on it",
        "in the middle of ",
        "halfway ", "midway ", "progress on ", "progreso ",
        "avance ", "update on ",
        "stuck ", "atorado ", "atrapado ", "atascado ",
        "blocked ", "bloqueado ",
        "pause ", "paused ", "pausa", "stop working on ",
        "resume ", "resumed ", "resuming ", "retomar", "retomado",
        "back on ", "back to ",
    )
    return any(head.startswith(t) for t in triggers)


# ── Dispatchers ───────────────────────────────────────────────────────


async def _handle_reschedule(
    config, user_id: str, rest: str, full_msg: str, tasks: list[dict],
) -> str | None:
    name, phrase, matches = _split_name_and_phrase(rest, tasks)
    if len(matches) != 1:
        return None  # ambiguous or zero — let brain handle
    if not phrase:
        return None  # "snooze upwork" with no duration → brain
    # Rebuild a phrase the skill recognizes. The first word of the user's
    # original message was the verb (snooze/postpone/etc); we prefix it
    # back so the skill's regex catches "snooze 2h" / "postpone Friday".
    verb = full_msg.split(maxsplit=1)[0].lower()
    skill_phrase = f"{verb} {phrase}" if verb in ("snooze", "delay", "aplaza", "aplazar") else phrase

    from lazyclaw.skills.builtin.task_reschedule import RescheduleTaskSkill
    skill = RescheduleTaskSkill(config=config)
    return await skill.execute(
        user_id, {"task_name": name, "phrase": skill_phrase},
    )


async def _handle_clear_deadline(
    config, user_id: str, rest: str | None, tasks: list[dict],
) -> str | None:
    if not rest:
        return None  # "clear deadline" with no target → brain
    name = _strip_articles(rest.strip())
    matches = _all_fuzzy_matches(tasks, name)
    if len(matches) != 1:
        return None
    from lazyclaw.skills.builtin.task_reschedule import RescheduleTaskSkill
    skill = RescheduleTaskSkill(config=config)
    return await skill.execute(
        user_id, {"task_name": name, "phrase": "clear deadline"},
    )


async def _handle_priority(
    config, user_id: str, level: str, rest: str, tasks: list[dict],
) -> str | None:
    name = _strip_articles(rest.strip())
    matches = _all_fuzzy_matches(tasks, name)
    if len(matches) != 1:
        return None
    pri = _PRIORITY_MAP.get(level.lower())
    if not pri:
        return None
    from lazyclaw.skills.builtin.task_reschedule import RescheduleTaskSkill
    skill = RescheduleTaskSkill(config=config)
    return await skill.execute(
        user_id, {"task_name": name, "phrase": f"priority {pri}"},
    )


async def _handle_complete(
    config, user_id: str, rest: str, tasks: list[dict],
) -> str | None:
    name = _strip_articles(rest.strip())
    matches = _all_fuzzy_matches(tasks, name)
    if len(matches) != 1:
        return None
    target = matches[0]
    from lazyclaw.tasks.store import complete_task
    ok = await complete_task(config, user_id, target["id"])
    if not ok:
        return None
    return f"✅ Done: {target.get('title') or 'task'}"


async def _handle_delete(
    config, user_id: str, rest: str, tasks: list[dict],
) -> str | None:
    name = _strip_articles(rest.strip())
    matches = _all_fuzzy_matches(tasks, name)
    if len(matches) != 1:
        return None
    target = matches[0]
    from lazyclaw.tasks.store import delete_task
    ok = await delete_task(config, user_id, target["id"])
    if not ok:
        return None
    return f"🗑️ Deleted: {target.get('title') or 'task'}"


async def _handle_whenisdue(
    config, user_id: str, rest: str, tasks: list[dict],
) -> str | None:
    name = _strip_articles(rest.strip())
    matches = _all_fuzzy_matches(tasks, name)
    if len(matches) != 1:
        return None
    from lazyclaw.skills.builtin.task_reschedule import AskAboutTaskSkill
    skill = AskAboutTaskSkill(config=config)
    return await skill.execute(user_id, {"task_name": name})


async def _handle_progress(
    config, user_id: str, kind: str, rest: str, tasks: list[dict],
) -> str | None:
    """Progress capture: '<verb> <task name> [free text]'.

    Splits the trailing text into task-name + free-form note using the
    same prefix-probing strategy as reschedule. The free-form remainder
    becomes the entry's ``text`` field (e.g. "section 2 done").
    """
    name, note, matches = _split_name_and_phrase(rest, tasks)
    if len(matches) != 1:
        return None
    target = matches[0]

    # Status side-effects: certain progress verbs flip task.status so
    # the brain doesn't have to. "started" → in_progress (only if it
    # was todo); "paused" → in_progress stays as-is but we record the
    # state; "resumed" → ensures back to in_progress. "stuck"/"blocked"
    # don't flip status; the user knows what they're doing.
    from lazyclaw.tasks.store import (
        append_progress_entry, clear_nudge_sent, update_task,
    )

    if kind == "started" and target.get("status") == "todo":
        try:
            await update_task(
                config, user_id, target["id"], status="in_progress",
            )
        except Exception:
            logger.debug("progress: status flip to in_progress failed", exc_info=True)
    elif kind == "resumed" and target.get("status") in {"todo", "paused"}:
        try:
            await update_task(
                config, user_id, target["id"], status="in_progress",
            )
        except Exception:
            logger.debug("progress: status flip on resume failed", exc_info=True)

    entry = await append_progress_entry(
        config, user_id, target["id"],
        kind=kind, text=note, source="nl",
    )
    if entry is None:
        return None

    # Any user signal clears the stale-nudge state machine.
    await clear_nudge_sent(config, user_id, target["id"])

    icon = {
        "started": "🚀", "working": "🟡", "progress": "📊",
        "stuck": "🔴", "blocked": "🚧",
        "paused": "⏸️", "resumed": "▶️",
    }.get(kind, "📝")
    title = target.get("title") or "task"
    if note:
        return f"{icon} {title} — {kind}: {note}"
    return f"{icon} {title} — {kind}"
