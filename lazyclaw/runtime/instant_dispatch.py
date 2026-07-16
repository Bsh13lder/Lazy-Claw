"""Instant dispatch — call a single skill directly when the user message is a
1:1 mapping to a known intent.

Skips the full brain LLM round-trip (build_context + compress_history +
load_user_skills + tool selection + TAOR iterations) for messages where the
matched skill's output is itself a complete user-facing answer.

Conservative by design: false positives erode trust faster than false negatives
add latency. Every pattern is anchored to the WHOLE stripped+lowered message,
and only intents where the skill's textual output IS the reply belong here.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstantRoute:
    pattern: re.Pattern[str]
    skill_name: str
    params_builder: Callable[[re.Match[str]], dict]
    description: str  # debug-only, never shown to the user


def _no_params(_m: re.Match[str]) -> dict:
    return {}


# Heartbeat / cron prefixes that wrap the real instruction. They appear
# verbatim at the start of the message (e.g. ``[JOB:survival_message_check]
# check my upwork inbox``) and must be stripped BEFORE pattern matching
# so cron-driven turns benefit from the same fast path as user-typed ones.
# Only known prefixes are stripped — anything else falls through unchanged
# so an unknown ``[XYZ]`` token doesn't accidentally enable a match the
# brain should have moderated.
_CRON_PREFIX_RE = re.compile(
    r"^\s*\[(?:JOB|REMINDER|TASK_REMINDER|WATCHER|MCP_WATCHER)"
    r"(?::[^\]]*)?\]\s*",
)


# Every pattern matches the WHOLE stripped+lowered message. Trailing
# punctuation is tolerated (chat clients append `.` / `?` casually). New
# patterns must be added with the same anchoring discipline.
INSTANT_ROUTES: tuple[InstantRoute, ...] = (
    # ── Upwork inbox sweep ─────────────────────────────────────────────
    # Most-cited slow path: the skill is fully autonomous (deterministic
    # categoriser + escalation push) so its return value is the complete
    # answer. Going through the brain costs one ``search_tools`` ping-pong
    # + one tool_use round-trip before anything happens.
    InstantRoute(
        pattern=re.compile(
            r"^(?:please\s+|can\s+you\s+)?"
            r"(?:check|sweep|scan|read|sync|refresh)\s+"
            r"(?:my\s+|the\s+)?upwork\s+"
            r"(?:inbox|dms?|messages?|chats?|convos?|conversations?)"
            r"(?:\s+now)?[\s.!?]*$",
        ),
        skill_name="upwork_inbox_check",
        params_builder=_no_params,
        description="check upwork inbox",
    ),
    # ── Upwork contract poll ──────────────────────────────────────────
    # Matches the canonical cron instruction ("poll my upwork contracts
    # and intake any new ones") AND user-typed variants ("check my upwork
    # contracts", "any new contracts on upwork"). Skill output is itself
    # a complete Telegram-ready message, so brain round-trip is wasted.
    InstantRoute(
        pattern=re.compile(
            r"^(?:please\s+|can\s+you\s+)?"
            r"(?:poll|check|scan|sync|fetch|list|show|see)\s+"
            r"(?:me\s+)?(?:my\s+|the\s+|any\s+)?"
            r"(?:new\s+)?upwork\s+contracts?"
            r"(?:\s+and\s+intake\s+(?:any\s+)?new\s+ones)?"
            r"(?:\s+now)?[\s.!?]*$",
        ),
        skill_name="upwork_contract_poll",
        params_builder=_no_params,
        description="poll upwork contracts",
    ),
    # ── Upwork last conversation ──────────────────────────────────────
    # Catches "tell me what's in the last conversation" / "show latest
    # upwork message" / "what did <name> say". The brain-routed path
    # for this on 2026-05-16 cost 2m33s + 4 wasted upwork_get_messages
    # calls; the dedicated skill runs in <10s deterministically.
    # Conservative: requires the "upwork" anchor OR a "conversation/
    # message" anchor with no competing inbox-sweep verb.
    InstantRoute(
        pattern=re.compile(
            r"^(?:please\s+|can\s+you\s+|tell\s+me\s+|show\s+(?:me\s+)?)?"
            r"(?:what['s\s]+(?:in|the)\s+)?"
            r"(?:the\s+|my\s+|that\s+)?"
            r"(?:last|latest|recent|most\s+recent)\s+"
            r"(?:upwork\s+)?"
            r"(?:conversation|message|chat|thread|convo|dm)s?"
            r"(?:\s+on\s+upwork)?"
            r"[\s.!?]*$",
        ),
        skill_name="upwork_last_conversation",
        params_builder=_no_params,
        description="upwork last conversation",
    ),
    # ── Manual contract intake by URL ─────────────────────────────────
    # ``intake contract <url>`` or ``intake upwork contract <url>`` —
    # bypasses the poll/dedup path so the user can re-onboard a contract
    # that was previously marked seen (e.g. after fixing a bad answer).
    InstantRoute(
        pattern=re.compile(
            r"^(?:please\s+|can\s+you\s+)?"
            r"intake\s+(?:the\s+|my\s+|this\s+|upwork\s+)?"
            r"contract\s+"
            r"(?P<url>https?://\S+?)"
            r"[\s.!?]*$",
        ),
        skill_name="new_contract_intake",
        params_builder=lambda m: {"contract_url": m.group("url")},
        description="manual contract intake by URL",
    ),
    # ── Cron jobs / reminders listing ──────────────────────────────────
    # ListJobsSkill takes no params and emits a complete formatted table.
    InstantRoute(
        pattern=re.compile(
            r"^(?:please\s+|can\s+you\s+)?"
            r"(?:show|list|get|view|see|display)\s+"
            r"(?:me\s+)?(?:my\s+|the\s+|all\s+)?"
            r"(?:active\s+|scheduled\s+|cron\s+|current\s+)?"
            r"(?:jobs?|reminders?)"
            r"[\s.!?]*$",
        ),
        skill_name="list_jobs",
        params_builder=_no_params,
        description="list jobs / reminders",
    ),
    # ── User tasks listing ─────────────────────────────────────────────
    # Default params produce the "active user tasks" table — what users
    # mean ~99% of the time when they type "show tasks".
    InstantRoute(
        pattern=re.compile(
            r"^(?:please\s+|can\s+you\s+)?"
            r"(?:show|list|view|see|display)\s+"
            r"(?:me\s+)?(?:my\s+|the\s+|all\s+)?"
            r"(?:active\s+|open\s+|pending\s+|current\s+)?"
            r"tasks?"
            r"[\s.!?]*$",
        ),
        skill_name="list_tasks",
        params_builder=_no_params,
        description="list tasks",
    ),
    # ── Contacts browse ────────────────────────────────────────────────
    # ListContactsSkill is read_only=True and defaults to "allow". No
    # filter args needed for the bare "show contacts" intent.
    InstantRoute(
        pattern=re.compile(
            r"^(?:please\s+|can\s+you\s+)?"
            r"(?:show|list|view|see|display)\s+"
            r"(?:me\s+)?(?:my\s+|the\s+|all\s+)?"
            r"contacts?"
            r"[\s.!?]*$",
        ),
        skill_name="list_contacts",
        params_builder=_no_params,
        description="list contacts",
    ),
)


@dataclass(frozen=True)
class InstantDispatchResult:
    output: str
    skill_name: str
    elapsed_ms: int
    route_description: str


async def try_instant_dispatch(
    message: str,
    registry,
    user_id: str,
) -> InstantDispatchResult | None:
    """Try every route. Returns the dispatch result on hit, None on miss.

    Never raises — any failure (missing skill, skill exception, registry
    None) is logged and converted to None so the caller falls back to the
    standard brain path.
    """
    if registry is None or not message or not user_id:
        return None
    logger.debug(
        "[dispatch] instant_dispatch check: user=%s message_len=%d",
        user_id[:8] if user_id else "", len(message),
    )
    # Strip a single known cron/heartbeat prefix so ``[JOB:foo] check upwork
    # inbox`` lights up the same route as ``check upwork inbox``. Unknown
    # bracketed prefixes are left in place — they have to be handled by the
    # brain because we don't know what they mean.
    _prefix_stripped = _CRON_PREFIX_RE.sub("", message.strip(), count=1)
    stripped = _prefix_stripped.strip().lower()
    if not stripped:
        return None
    for route in INSTANT_ROUTES:
        m = route.pattern.match(stripped)
        if m is None:
            continue
        skill = registry.get(route.skill_name)
        if skill is None:
            logger.debug(
                "instant_dispatch matched %s but %s not in registry — falling through",
                route.description, route.skill_name,
            )
            return None
        start = time.monotonic()
        try:
            params = route.params_builder(m)
            output = await skill.execute(user_id, params)
        except Exception as exc:
            logger.warning(
                "instant_dispatch %s raised (%s: %s) for user=%s — "
                "falling back to brain path",
                route.skill_name, type(exc).__name__, exc,
                user_id[:8] if user_id else "", exc_info=True,
            )
            return None
        if not isinstance(output, str):
            logger.warning(
                "instant_dispatch %s returned non-str (%s) — falling back",
                route.skill_name, type(output).__name__,
            )
            return None
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "instant_dispatch HIT route=%s skill=%s elapsed=%dms output_len=%d",
            route.description, route.skill_name, elapsed_ms, len(output),
        )
        return InstantDispatchResult(
            output=output,
            skill_name=route.skill_name,
            elapsed_ms=elapsed_ms,
            route_description=route.description,
        )
    logger.debug(
        "[dispatch] instant_dispatch MISS — no route matched, falling back to brain path",
    )
    return None
