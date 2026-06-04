"""In-editor AI "Document Specialist".

One focused, synchronous turn that edits a single open document (sheet / doc /
PDF) from a natural-language instruction — the engine behind the ✨ AI popover in
the web editors. Deliberately bypasses the chat / background / consolidator path
(and its Telegram-leak failure modes): it runs entirely request→response and
returns the fresh document so the editor can reload in place.

Flow: validate → load the doc → ask the LLM for a strict-JSON *edit plan*
(text only, no tool protocol, so it works across every ECO mode) → parse → apply
deterministically via the per-kind strategy (:mod:`lazyclaw.{docs,sheets,pdf}.ai_edit`).
If the worker model returns unparseable JSON we retry once on the stronger brain
model before giving up with a friendly error.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from lazyclaw.docs import ai_edit as docs_ai
from lazyclaw.llm.eco_router import ROLE_BRAIN, ROLE_WORKER
from lazyclaw.pdf import ai_edit as pdf_ai
from lazyclaw.sheets import ai_edit as sheets_ai

logger = logging.getLogger(__name__)

# kind → strategy module (each exposes load / build_messages / apply).
_STRATEGIES: dict[str, Any] = {
    "docs": docs_ai,
    "sheets": sheets_ai,
    "pdf": pdf_ai,
}

_MAX_INSTRUCTION_CHARS = 2000


@dataclass(frozen=True)
class SpecialistResult:
    """Outcome of one specialist turn.

    ``snapshot`` carries the fresh Univer payload for sheets/docs (so the editor
    reloads); ``new_id`` carries the id of the new file for PDF ops. Exactly one
    of them is set on success, both None on a no-op kind.
    """

    ok: bool
    summary: str = ""
    snapshot: dict[str, Any] | None = None
    new_id: str | None = None
    error: str | None = None


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of an LLM reply (tolerates fences/prefix)."""
    if not isinstance(text, str):
        return None
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _ask_for_plan(eco_router, messages, user_id: str) -> dict[str, Any] | None:
    """Ask the worker for a JSON plan; retry once on the brain if it won't parse."""
    for role in (ROLE_WORKER, ROLE_BRAIN):
        try:
            resp = await eco_router.chat(messages, user_id, role=role)
        except Exception as exc:  # noqa: BLE001 — surface as a friendly error
            logger.warning("doc_specialist LLM call failed (%s): %s", role, exc)
            continue
        if getattr(resp, "model", "") == "error":
            logger.warning("doc_specialist LLM returned error sentinel: %s", resp.content)
            continue
        plan = _extract_json(getattr(resp, "content", "") or "")
        if plan is not None:
            return plan
        logger.info("doc_specialist: %s reply was not valid JSON — retrying higher", role)
    return None


async def run_doc_specialist(
    config: Any,
    eco_router: Any,
    user_id: str,
    kind: str,
    doc_id: str,
    instruction: str,
) -> SpecialistResult:
    """Run one specialist edit turn. Never raises — returns a SpecialistResult."""
    strategy = _STRATEGIES.get(kind)
    if strategy is None:
        return SpecialistResult(ok=False, error=f"Unknown document kind '{kind}'.")

    if not isinstance(instruction, str) or not instruction.strip():
        return SpecialistResult(ok=False, error="Tell the AI what to change.")
    instruction = instruction.strip()[:_MAX_INSTRUCTION_CHARS]

    try:
        ctx = await strategy.load(config, user_id, doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_specialist load failed")
        return SpecialistResult(ok=False, error=f"Could not open the document: {exc}")
    if ctx is None:
        return SpecialistResult(ok=False, error="Document not found.")

    messages = strategy.build_messages(ctx, instruction)
    plan = await _ask_for_plan(eco_router, messages, user_id)
    if plan is None:
        return SpecialistResult(
            ok=False,
            error="The AI couldn't turn that into an edit. Try rephrasing it more concretely.",
        )

    try:
        result = await strategy.apply(config, user_id, doc_id, ctx, plan)
    except ValueError as exc:
        return SpecialistResult(ok=False, error=f"Couldn't apply that: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_specialist apply failed")
        return SpecialistResult(ok=False, error=f"Edit failed: {exc}")

    return SpecialistResult(
        ok=True,
        summary=result.get("summary", "Done."),
        snapshot=result.get("snapshot"),
        new_id=result.get("new_id"),
    )


# ── Convenience wiring for the HTTP routes ──────────────────────────────

_DEFAULT_ROUTER: Any = None


def get_default_router(config: Any) -> Any:
    """Return a process-cached EcoRouter (built lazily, same as other callers)."""
    global _DEFAULT_ROUTER
    if _DEFAULT_ROUTER is None:
        from lazyclaw.llm.eco_router import EcoRouter
        from lazyclaw.llm.router import LLMRouter

        _DEFAULT_ROUTER = EcoRouter(config, LLMRouter(config))
    return _DEFAULT_ROUTER


async def ai_edit_document(
    config: Any, user_id: str, kind: str, doc_id: str, instruction: str
) -> SpecialistResult:
    """Route-facing entry point: build the default router and run one turn."""
    return await run_doc_specialist(
        config, get_default_router(config), user_id, kind, doc_id, instruction
    )
