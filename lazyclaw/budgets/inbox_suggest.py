"""Suggest a project for an unassigned (Inbox/General) expense.

Mirrors lazyclaw/tasks/smart_intake.py: ROLE_WORKER model, hard timeout,
strict-JSON prompt, never raises — every failure returns an empty suggestion.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

_ALLOWED_CONFIDENCE = {"high", "medium", "low", "none"}


@dataclass(frozen=True)
class ExpenseSuggestion:
    project_name: str | None
    confidence: str  # high | medium | low | none
    reason: str | None
    source: str  # llm | none


def _empty() -> ExpenseSuggestion:
    return ExpenseSuggestion(None, "none", None, "none")


async def _worker_chat(config: Config, user_id: str, prompt: str) -> dict:
    # Isolated for test monkeypatching — lazy imports like smart_intake.py:155.
    #
    # NOTE (adaptation): EcoRouter.chat() returns an `LLMResponse` dataclass
    # (lazyclaw/llm/providers/base.py) with a `.content` STRING ATTRIBUTE —
    # NOT a dict. tasks/smart_intake.py:183 confirms this via `resp.content`.
    # We normalize it into `{"content": ...}` here so the public contract of
    # `_worker_chat` (and the monkeypatched fakes in tests) stays a plain dict.
    from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    eco = EcoRouter(config, LLMRouter(config))
    resp = await eco.chat(
        messages=[
            LLMMessage(role="system", content="You output JSON only."),
            LLMMessage(role="user", content=prompt),
        ],
        user_id=user_id,
        role=ROLE_WORKER,
    )
    return {"content": resp.content or ""}


async def suggest_expense_project(
    config: Config,
    user_id: str,
    *,
    description: str | None,
    vendor: str | None,
    amount: float,
    currency: str,
    timeout_s: float = 3.0,
) -> ExpenseSuggestion:
    """Never raises. Suggests only EXISTING project names (never General)."""
    try:
        from lazyclaw.budgets import store

        projects = await store.list_projects(config, user_id, status="active")
        names = [p["name"] for p in projects if p.get("name_key") != "general"]
        if not names or not (description or vendor):
            return _empty()
        # Cap ONCE: the same capped list is used for both the prompt context
        # AND the validation set below, so an LLM reply can never resolve to
        # a project name that was never shown to it (candidate/validation
        # mismatch bug — a name outside the first 20 must be rejected).
        candidates = names[:20]

        recents = await store.list_all_expenses(config, user_id)
        by_project: dict[str, list[str]] = {}
        for e in recents[:60]:
            pn = e.get("project_name")
            d = (e.get("description") or e.get("vendor") or "").strip()
            if pn and d and pn.casefold() != "general":
                by_project.setdefault(pn, [])
                if len(by_project[pn]) < 3:
                    by_project[pn].append(d[:60])

        context = "\n".join(
            f"- {n}: {', '.join(by_project.get(n, [])) or '(no expenses yet)'}"
            for n in candidates
        )
        prompt = (
            "An expense needs to be filed into one of the user's existing projects.\n"
            f"Expense: {amount} {currency} — {(description or '')[:200]}"
            + (f" (vendor: {vendor[:100]})" if vendor else "") + "\n"
            f"Projects (with recent expense examples):\n{context}\n\n"
            "Pick the best-matching project NAME from the list above, or null if none fits.\n"
            'Reply with STRICT JSON only, no prose, no fence:\n'
            '{"project_name": "<exact name from list or null>", '
            '"confidence": "high|medium|low", "reason": "<max 20 words>"}'
        )

        raw = await asyncio.wait_for(
            _worker_chat(config, user_id, prompt), timeout=timeout_s,
        )
        content = (raw.get("content") or "").strip().strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
        data = json.loads(content)
        if not isinstance(data, dict):
            return _empty()

        name = data.get("project_name")
        by_fold = {n.casefold(): n for n in candidates}
        resolved = by_fold.get(str(name).casefold()) if name else None
        if resolved is None:
            return _empty()

        conf = data.get("confidence")
        if conf not in _ALLOWED_CONFIDENCE:
            conf = "low"
        reason = (str(data.get("reason") or "")[:200]) or None
        # PII-free trace (booleans/enums only, like smart_intake.py:207).
        logger.debug("inbox_suggest: matched=%s confidence=%s", bool(resolved), conf)
        return ExpenseSuggestion(resolved, conf, reason, "llm")
    except (asyncio.TimeoutError, json.JSONDecodeError):
        return _empty()
    except Exception:
        logger.debug("inbox_suggest failed", exc_info=True)
        return _empty()


async def suggest_for_expenses(
    config: Config,
    user_id: str,
    expenses: list[dict],
    projects: list[dict],
) -> list[dict]:
    """Bounded-concurrency batch fan-out over ``suggest_expense_project``.

    WHY: the default HYBRID-mode worker is local Ollama, which serves chat
    calls SERIALLY. An unbounded ``asyncio.gather`` over up to 10 inbox
    items fires 10 concurrent calls, each racing its own independent 3s
    ``asyncio.wait_for`` — calls 2..10 sit queued behind the first and burn
    their whole clock before the worker ever gets to them, so they all come
    back empty. Capping live concurrency at 2 (a semaphore) turns the batch
    into ceil(N/2) serial waves instead; a longer 6s per-call timeout for
    batches >2 gives each wave room to actually complete on the serial
    worker (worst case ~ceil(10/2)*6s = 30s for a full 10-item batch).

    Shared by the ``/api/budgets/inbox/suggestions`` route and
    ``AutoAssignInboxSkill`` so the concurrency bound lives in one place.
    """
    id_by_name = {p["name"]: p["id"] for p in projects}
    timeout_s = 6.0 if len(expenses) > 2 else 3.0
    semaphore = asyncio.Semaphore(2)

    async def one(e: dict) -> dict:
        # Acquire INSIDE the per-item coroutine (not around the gather) so
        # asyncio.gather still schedules and collects every item — the
        # semaphore only throttles how many are actually IN FLIGHT at once.
        async with semaphore:
            s = await suggest_expense_project(
                config, user_id,
                description=e.get("description"), vendor=e.get("vendor"),
                amount=float(e.get("amount") or 0),
                currency=e.get("currency") or "EUR",
                timeout_s=timeout_s,
            )
        return {
            "expense_id": e["id"],
            "project_id": id_by_name.get(s.project_name) if s.project_name else None,
            "project_name": s.project_name,
            "confidence": s.confidence,
            "reason": s.reason,
        }

    return list(await asyncio.gather(*(one(e) for e in expenses)))
