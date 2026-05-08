"""Real LLM critic — audits the brain's final reply before it ships.

Runs ONE structured prompt that performs four checks against the reply:

  1. Tool failures the reply ignored (regex stamps from result_verifier)
  2. Hallucinated facts (numbers/names/addresses/links not in the trace)
  3. Whether the reply actually answers the user's question
  4. Subagent results consolidation (did the reply reference each one)

Loop policy:
  - Pass → ship as-is.
  - Fail → inject `[CRITIC FEEDBACK: <fix_hint>]` and ask the BRAIN to
    rewrite text-only. Re-check. Up to ``max_loops`` total attempts.
  - After ``max_loops`` failures → ship the last draft with a small
    `_Note: critic still flagged: <top issue>_` footer so the user sees
    something is off. Never infinite-loops, always terminates.

Gated upstream by effort (HIGH | MAX) and ``critic_mode is True``. The
critic itself never enters subagent recursion (top-level only).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lazyclaw.llm.providers.base import LLMMessage

if TYPE_CHECKING:
    from lazyclaw.llm.eco_router import EcoRouter

logger = logging.getLogger(__name__)


# Keep prompts terse — critic must be cheap and stable.
_CRITIC_SYSTEM = (
    "You are a strict, brief reply auditor. You run four checks against an "
    "assistant's reply and return JSON only — no prose, no markdown.\n\n"
    "CHECK 1 — Tool failures: did the reply claim success while a tool "
    "returned `→ FAILED:` / HTTP 4xx / 5xx / 'error' / 'traceback'?\n"
    "CHECK 2 — Hallucinated facts: numbers, names, addresses, prices, or "
    "links in the reply that do NOT appear anywhere in the tool trace? "
    "If the reply paraphrases a fact that IS in the trace, that's fine.\n"
    "CHECK 3 — Answered question: does the reply actually address what the "
    "USER asked, or drift off-topic / dodge the question?\n"
    "CHECK 4 — Subagent consolidation: when the trace contains multiple "
    "subagent results, did the reply reference each (or explain why it "
    "skipped one)?\n\n"
    "Output JSON exactly in this shape:\n"
    "{\"pass\": <bool>, \"issues\": [<string>, ...], "
    "\"fix_hint\": <string|null>}\n\n"
    "Rules — be terse:\n"
    "- Each issue ≤ 15 words. fix_hint ≤ 30 words.\n"
    "- If pass=true, issues MUST be []. fix_hint MUST be null.\n"
    "- If pass=false, fix_hint MUST be set with a concrete one-line fix.\n"
    "- No commentary outside the JSON object."
)


_FOOTER_TEMPLATE = "\n\n_Note: critic still flagged: {issue}_"

# Trace truncation — keep critic cost bounded on long sessions.
_MAX_TRACE_CHARS = 6000
_MAX_REPLY_CHARS = 4000


@dataclass(frozen=True)
class CriticResult:
    """Outcome of run_critic. Immutable.

    Attributes:
        final_reply: The reply to ship to the user (rewritten if critic
            forced a retry, original on pass, original + footer on
            max-loop exhaustion).
        passed: True if the critic ever returned pass=true. False if all
            attempts failed and the loop exhausted.
        loops_used: Number of critic LLM calls actually made (1..max).
        last_issues: Issues from the most recent critic verdict.
        rewrite_count: Number of brain-rewrite calls (loops_used - 1
            when fail-then-rewrite, 0 when first pass succeeded).
    """
    final_reply: str
    passed: bool
    loops_used: int
    last_issues: tuple[str, ...] = field(default_factory=tuple)
    rewrite_count: int = 0


def build_tool_trace(
    tool_names: list[str],
    tool_results: list[str],
) -> str:
    """Render parallel tool name + result lists into a compact trace.

    Pure function. Called by agent.py before invoking run_critic.
    Truncates at ``_MAX_TRACE_CHARS`` to keep critic latency bounded.
    """
    if not tool_results:
        return "(no tools were called)"

    lines: list[str] = []
    for i, result in enumerate(tool_results):
        name = tool_names[i] if i < len(tool_names) else f"tool_{i}"
        # Each entry capped so a single huge result can't eat the budget.
        snippet = result if len(result) <= 800 else result[:800] + " …(truncated)"
        lines.append(f"- {name} → {snippet}")
    trace = "\n".join(lines)
    if len(trace) > _MAX_TRACE_CHARS:
        trace = trace[:_MAX_TRACE_CHARS] + "\n…(trace truncated)"
    return trace


def _build_audit_user_message(
    user_message: str,
    reply: str,
    tool_trace: str,
) -> str:
    """Build the user-role message handed to the critic LLM."""
    short_reply = reply if len(reply) <= _MAX_REPLY_CHARS else reply[:_MAX_REPLY_CHARS] + " …(truncated)"
    return (
        f"USER asked:\n{user_message}\n\n"
        f"TOOLS RUN:\n{tool_trace}\n\n"
        f"ASSISTANT replied:\n{short_reply}\n\n"
        "Return the JSON verdict now."
    )


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_verdict(raw: str) -> tuple[bool, list[str], str | None]:
    """Best-effort parse of the critic's JSON output.

    On parse failure returns ``(True, [], None)`` — failing OPEN. A
    misbehaving critic must never block a reply that might actually be
    fine; the user's brain reply ships and we log the parse failure.
    """
    if not raw or not raw.strip():
        logger.warning("Critic returned empty output — failing open")
        return True, [], None

    text = raw.strip()
    # Strip code fences if model wrapped JSON in ```json … ```
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = text.rstrip("`").rstrip()

    match = _JSON_BLOCK_RE.search(text)
    if not match:
        logger.warning("Critic output had no JSON block — failing open: %.120s", text)
        return True, [], None

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("Critic JSON parse failed (%s) — failing open", exc)
        return True, [], None

    passed = bool(data.get("pass", True))
    raw_issues = data.get("issues") or []
    if not isinstance(raw_issues, list):
        raw_issues = [str(raw_issues)]
    issues = [str(x).strip() for x in raw_issues if str(x).strip()]

    fix_hint = data.get("fix_hint")
    if fix_hint is not None:
        fix_hint = str(fix_hint).strip() or None

    # Coerce inconsistent verdicts into a stable shape.
    if passed:
        return True, [], None
    if not fix_hint:
        # Failed verdict with no hint — synthesise one from issues.
        fix_hint = "; ".join(issues)[:200] or "Reply did not pass audit."
    return False, issues, fix_hint


async def _audit_once(
    eco_router: EcoRouter,
    user_id: str,
    user_message: str,
    reply: str,
    tool_trace: str,
    model_id: str | None,
) -> tuple[bool, list[str], str | None]:
    """Single critic LLM call. Returns (passed, issues, fix_hint)."""
    audit_messages = [
        LLMMessage(role="system", content=_CRITIC_SYSTEM),
        LLMMessage(
            role="user",
            content=_build_audit_user_message(user_message, reply, tool_trace),
        ),
    ]

    # Picked model overrides routing; otherwise fall back to ROLE_WORKER
    # (the worker tier is the right cost class for a small audit task).
    from lazyclaw.llm.eco_router import ROLE_WORKER

    try:
        if model_id:
            response = await eco_router.chat(
                audit_messages,
                user_id=user_id,
                model=model_id,
                role="critic",  # Anything not BRAIN/WORKER bypasses routing
                temperature=0.0,
            )
        else:
            response = await eco_router.chat(
                audit_messages,
                user_id=user_id,
                role=ROLE_WORKER,
                temperature=0.0,
            )
    except Exception as exc:
        # Network blip, auth issue, etc. — fail open so we never block a
        # reply on a flaky critic.
        logger.warning("Critic call failed (%s) — failing open", exc)
        return True, [], None

    return _parse_verdict(response.content or "")


async def _ask_brain_to_rewrite(
    eco_router: EcoRouter,
    user_id: str,
    base_messages: list[LLMMessage],
    last_reply: str,
    fix_hint: str,
) -> str | None:
    """Inject critic feedback and get a text-only rewrite from the brain.

    Returns the new reply string, or None on failure (caller keeps the
    previous reply).

    Strips any trailing assistant draft and any trailing TAOR
    self-verification / prior critic-feedback directives so the rewrite
    context is clean — then re-injects (assistant: last_reply, user:
    critic feedback) as the canonical pair.
    """
    from lazyclaw.llm.eco_router import ROLE_BRAIN

    cleaned: list[LLMMessage] = list(base_messages)
    while cleaned:
        last = cleaned[-1]
        if last.role == "assistant":
            cleaned.pop()
            continue
        if last.role == "user" and last.content and (
            "[SELF-VERIFICATION FAILED" in last.content
            or "[CRITIC FEEDBACK:" in last.content
        ):
            cleaned.pop()
            continue
        break

    rewrite_messages = cleaned + [
        LLMMessage(role="assistant", content=last_reply),
        LLMMessage(
            role="user",
            content=(
                f"[CRITIC FEEDBACK: {fix_hint}] Rewrite your previous reply "
                "to address this feedback. Respond with text only — do NOT "
                "call any tools. Be concise."
            ),
        ),
    ]
    try:
        response = await eco_router.chat(
            rewrite_messages, user_id=user_id, role=ROLE_BRAIN,
        )
    except Exception as exc:
        logger.warning("Critic rewrite call failed (%s) — keeping previous reply", exc)
        return None

    new_reply = (response.content or "").strip()
    if not new_reply:
        return None
    return new_reply


async def run_critic(
    *,
    user_message: str,
    reply: str,
    tool_trace: str,
    base_messages: list[LLMMessage],
    eco_router: EcoRouter,
    user_id: str,
    model_id: str | None = None,
    max_loops: int = 3,
) -> CriticResult:
    """Audit ``reply`` and loop the brain until the critic passes or we hit
    ``max_loops``.

    ``base_messages`` is the conversation history (system + user + tool
    calls/results) up to but NOT including the assistant reply being
    audited. It's used as context for the rewrite call.

    The critic itself is stateless — each audit gets a fresh
    (system_prompt, audit_user_message) pair so verdicts don't drift
    across loops.
    """
    if max_loops < 1:
        max_loops = 1

    current_reply = reply
    last_issues: list[str] = []
    rewrite_count = 0

    for attempt in range(1, max_loops + 1):
        passed, issues, fix_hint = await _audit_once(
            eco_router=eco_router,
            user_id=user_id,
            user_message=user_message,
            reply=current_reply,
            tool_trace=tool_trace,
            model_id=model_id,
        )
        last_issues = issues
        logger.info(
            "Critic attempt %d/%d: passed=%s issues=%d",
            attempt, max_loops, passed, len(issues),
        )

        if passed:
            return CriticResult(
                final_reply=current_reply,
                passed=True,
                loops_used=attempt,
                last_issues=tuple(issues),
                rewrite_count=rewrite_count,
            )

        # Last attempt failed — ship with footer note.
        if attempt == max_loops:
            top = issues[0] if issues else (fix_hint or "audit failed")
            footer = _FOOTER_TEMPLATE.format(issue=top[:120])
            return CriticResult(
                final_reply=current_reply + footer,
                passed=False,
                loops_used=attempt,
                last_issues=tuple(issues),
                rewrite_count=rewrite_count,
            )

        # Otherwise rewrite and re-check.
        new_reply = await _ask_brain_to_rewrite(
            eco_router=eco_router,
            user_id=user_id,
            base_messages=base_messages,
            last_reply=current_reply,
            fix_hint=fix_hint or "; ".join(issues)[:200],
        )
        if new_reply is None:
            # Rewrite call failed — break early, ship current with footer.
            top = issues[0] if issues else "audit failed"
            footer = _FOOTER_TEMPLATE.format(issue=top[:120])
            return CriticResult(
                final_reply=current_reply + footer,
                passed=False,
                loops_used=attempt,
                last_issues=tuple(issues),
                rewrite_count=rewrite_count,
            )
        current_reply = new_reply
        rewrite_count += 1

    # Defensive fallback — loop should always return inside the body.
    return CriticResult(
        final_reply=current_reply,
        passed=False,
        loops_used=max_loops,
        last_issues=tuple(last_issues),
        rewrite_count=rewrite_count,
    )
