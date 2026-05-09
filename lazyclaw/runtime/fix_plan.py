"""Plan-mode fix flow — propose, ask, then execute.

When the user says "fix / upgrade / refactor / rebuild …" we don't want
the agent to just plough ahead. We want it to (1) do quick context
research, (2) propose a step list + clarifying questions, (3) wait for
the user to OK or refine, (4) only then execute.

Why a dedicated module instead of letting the brain decide? The brain
*can* propose plans, but in practice it tends to just start editing.
A small upstream guard that intercepts fix-intent messages, builds a
plan via a single bounded LLM call, and gates the execution behind
``request_plan_approval`` keeps the experience consistent across
channels.

This module deliberately does NOT execute the plan — it only produces
the plan + asks for approval. The caller (agent.py turn handler) decides
what to do with the approved plan: usually, prepend it to the user's
message as a "## Approved plan\\n..." block so the brain sees the
locked-in steps and follows them.

Bypass valves:
- Message starts with ``!`` — explicit "just do it".
- ``priority='urgent'`` task with due_date inside next hour — fire-and-fix.
- Same plan slug auto-approved within last 24h — re-use without re-asking.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ── Intent detection ───────────────────────────────────────────────────


# Verbs that indicate "I want you to *change* something nontrivial".
# Matched as whole words. EN + ES so the Madrid user gets both.
_FIX_VERBS = (
    "fix", "fixes", "fixed",
    "upgrade", "upgrades", "upgraded",
    "refactor", "refactors", "refactored",
    "redesign",
    "rebuild", "rebuilds", "rebuilt",
    "improve", "improves", "improved",
    "optimise", "optimize",
    "repair", "repairs", "repaired",
    "overhaul", "overhauls",
    # Spanish equivalents
    "arregla", "arreglar", "arreglado",
    "mejora", "mejorar", "mejorado",
    "rediseña", "rediseñar",
    "rehacer", "rehago",
    "reparar",
)

_FIX_INTENT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in _FIX_VERBS) + r")\b",
    re.IGNORECASE,
)

# Explicit-bypass prefixes — user says "just do it".
_BYPASS_PREFIXES = ("!", "just do", "go ahead", "ya hazlo", "hazlo ya")

# Known plan slugs auto-approved within window. Module-level dict, keyed
# by (user_id, slug). 24h TTL.
_RECENT_APPROVALS: dict[tuple[str, str], float] = {}
_RECENT_TTL_S = 24 * 60 * 60


def detect_fix_intent(message: str) -> bool:
    """Cheap intent check — no LLM call.

    Returns True when the message contains a fix-class verb AND isn't
    explicitly bypassed. False positives are recoverable (the user can
    always tap Cancel or prefix ``!``); false negatives mean we silently
    skip plan-mode, which is fine for routine "add task" / question
    requests.
    """
    if not message:
        return False
    stripped = message.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    for prefix in _BYPASS_PREFIXES:
        if lowered.startswith(prefix):
            return False
    return bool(_FIX_INTENT_RE.search(lowered))


def _slug_for(message: str) -> str:
    """Stable short slug for de-dup keys.

    Hashes the lowercased message so semantically identical re-asks
    collapse onto the same key. Token-based normalization (drop stop
    words) would be slightly better but the 24h TTL already keeps the
    cache small.
    """
    h = hashlib.sha256(message.strip().lower().encode("utf-8")).digest()
    return h[:6].hex()


def is_recently_approved(user_id: str, message: str) -> bool:
    """Return True if the same plan slug was approved in the last 24h."""
    slug = _slug_for(message)
    now = time.time()
    expiry = _RECENT_APPROVALS.get((user_id, slug))
    if expiry is None:
        return False
    if now >= expiry:
        _RECENT_APPROVALS.pop((user_id, slug), None)
        return False
    return True


def mark_approved(user_id: str, message: str) -> None:
    """Stamp this plan slug as approved so re-asks within 24h skip the gate."""
    _RECENT_APPROVALS[(user_id, message and _slug_for(message))] = (
        time.time() + _RECENT_TTL_S
    )


# ── Plan synthesis ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class FixPlan:
    """Structured plan returned by the brain LLM.

    All fields are independent — partial plans are still useful. ``steps``
    being non-empty is the minimum bar for showing the plan card.
    """
    summary: str
    steps: list[str]
    questions: list[str]
    risks: list[str]
    confidence: str  # "high" | "medium" | "low"
    research: str = ""

    def to_markdown(self) -> str:
        """Render as a Markdown card for Telegram / Web UI / CLI."""
        lines: list[str] = []
        if self.summary:
            lines.append(self.summary)
            lines.append("")
        if self.steps:
            lines.append("**Plan:**")
            for i, step in enumerate(self.steps, 1):
                lines.append(f"  {i}. {step}")
            lines.append("")
        if self.questions:
            lines.append("**Questions:**")
            for q in self.questions:
                lines.append(f"  • {q}")
            lines.append("")
        if self.risks:
            lines.append("**Risks:**")
            for r in self.risks:
                lines.append(f"  • {r}")
            lines.append("")
        lines.append(f"_Confidence: {self.confidence}_")
        return "\n".join(lines).strip()


_PLAN_PROMPT = """You are a senior engineer. The user typed a fix/upgrade
request. Use the research findings (if any) to propose a SHORT plan
before any code changes happen.

User request:
{message}

{research}

Output STRICT JSON, no prose, no fence:
{{
  "summary": "1-2 sentence restatement of what they want",
  "steps": ["3-7 concrete steps", "..."],
  "questions": ["any clarifying question that would change the plan", "..."],
  "risks": ["specific risks worth flagging", "..."],
  "confidence": "high" or "medium" or "low"
}}

Rules:
- Steps must be specific enough that a junior engineer could execute them.
- If you would need to make a non-obvious decision, ASK in `questions`.
- Empty arrays are fine — only include items that actually apply.
- "high" confidence only when the request is unambiguous and you found
  enough research to ground the plan.
"""


async def build_fix_plan(
    config,
    user_id: str,
    message: str,
    *,
    timeout_s: float = 8.0,
) -> FixPlan | None:
    """Synthesize a plan for a fix-intent message.

    Returns ``None`` when the LLM is unavailable or the response can't
    be parsed — the caller should fall back to executing the request as
    today (no gate). Never raises.
    """
    from lazyclaw.runtime.plan_research import gather_plan_research

    # Pre-flight research (4 lanes in parallel, hard 2s/lane). Cheap
    # NOOP when there's nothing to surface.
    try:
        research = await gather_plan_research(config, user_id, message)
    except Exception:
        logger.debug("fix_plan research gather failed", exc_info=True)
        research = ""

    research_block = research or "## Research findings\n_(none surfaced)_"
    prompt = _PLAN_PROMPT.format(message=message[:2000], research=research_block)

    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_BRAIN
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter
    except Exception as exc:  # pragma: no cover — import-only failure
        logger.debug("fix_plan imports failed: %s", exc)
        return None

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await asyncio.wait_for(
            eco.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content="You output STRICT JSON only. No prose.",
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                user_id=user_id,
                role=ROLE_BRAIN,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.debug("fix_plan brain LLM timed out after %.1fs", timeout_s)
        return None
    except Exception as exc:
        logger.debug("fix_plan brain LLM failed: %s", exc)
        return None

    raw = (resp.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("fix_plan JSON parse failed: %.200s", raw)
        return None
    if not isinstance(data, dict):
        return None

    return FixPlan(
        summary=str(data.get("summary") or "")[:500],
        steps=[str(s) for s in (data.get("steps") or []) if s][:10],
        questions=[str(q) for q in (data.get("questions") or []) if q][:5],
        risks=[str(r) for r in (data.get("risks") or []) if r][:5],
        confidence=(
            (str(data.get("confidence") or "low")).strip().lower()
            if str(data.get("confidence") or "low").strip().lower()
            in {"high", "medium", "low"}
            else "low"
        ),
        research=research,
    )


# ── Bypass + gating ────────────────────────────────────────────────────


async def should_enter_plan_mode(
    config, user_id: str, message: str,
) -> bool:
    """Combine all the bypass valves into a single decision.

    Returns True when the message should be routed through the plan-mode
    gate. False when any bypass valve fires: explicit "!" prefix,
    recently-approved slug, or globally disabled (``users.auto_plan=0``).
    """
    if not detect_fix_intent(message):
        return False
    if is_recently_approved(user_id, message):
        return False

    # Per-user global toggle — same column the existing plan_checkpoint
    # respects. When 0, plan-mode is fully disabled.
    try:
        from lazyclaw.db.connection import db_session
        async with db_session(config) as db:
            cursor = await db.execute(
                "SELECT auto_plan FROM users WHERE id = ?", (user_id,),
            )
            row = await cursor.fetchone()
        if row is not None and not int(row[0] or 0):
            return False
    except Exception:
        logger.debug("auto_plan lookup failed", exc_info=True)
        # Default: assume on. Don't bypass plan-mode just because settings
        # lookup choked.

    return True


async def gate_with_plan(
    config, user_id: str, message: str,
) -> tuple[bool, FixPlan | None, str | None]:
    """Run the full plan → approve → execute gating sequence.

    Returns ``(approved, plan, reason)``:

    - ``approved=True``, ``plan`` set: caller should prepend the plan to
      the message before invoking the agent.
    - ``approved=True``, ``plan`` None: plan synthesis failed — fall
      through to direct execution.
    - ``approved=False``: user rejected; ``reason`` carries their reply
      (so the agent can acknowledge instead of silently dropping).

    Caller responsibility: only invoke this when ``should_enter_plan_mode``
    returned True. We don't re-check here so the caller can short-circuit
    cheaply on the hot path.
    """
    plan = await build_fix_plan(config, user_id, message)
    if plan is None or not plan.steps:
        # No plan to gate — let the agent take over.
        logger.info(
            "fix_plan: synthesis failed or empty for user %s — bypassing gate",
            user_id,
        )
        return True, None, None

    try:
        from lazyclaw.runtime import plan_checkpoint
        decision = await plan_checkpoint.request_plan_approval(
            user_id,
            plan_text=plan.to_markdown(),
            steps=plan.steps,
        )
    except Exception as exc:
        logger.warning("plan_checkpoint failed for user %s: %s", user_id, exc)
        return True, plan, None

    if decision.approved:
        mark_approved(user_id, message)
        return True, plan, decision.reason
    return False, plan, decision.reason or "user rejected"
