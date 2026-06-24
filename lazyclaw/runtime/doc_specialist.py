"""In-editor AI "Document Specialist".

One focused, synchronous turn that edits a single open document (sheet / doc /
PDF) from a natural-language instruction — the engine behind the ✨ AI popover in
the web editors. Deliberately bypasses the chat / background / consolidator path
(and its Telegram-leak failure modes): it runs entirely request→response and
returns the fresh document so the editor can reload in place.

Flow: validate → load the doc → ask the LLM for a strict-JSON *edit plan*
(text only, no tool protocol, so it works across every ECO mode) → parse → apply
deterministically via the per-kind strategy (:mod:`lazyclaw.{docs,sheets,pdf}.ai_edit`).

Model: always the user's configured **worker** (ECO ``ROLE_WORKER``, resolved
dynamically from settings — MiniMax, Claude, OpenAI, local, whatever the user
set). Nothing is hardcoded and there is no "stronger model" escalation: the
document AI is exactly the worker the user picked.

Robustness (provider-independent):
  * A reply that won't parse OR a structurally-present-but-EMPTY plan
    (``{"op":"fill_form","values":{}}``, ``{"edits":[]}``, …) is treated the
    same — a non-plan.
  * If ``strategy.apply`` rejects a plan with ``ValueError`` (empty/invalid),
    the specialist asks the SAME worker AGAIN with a corrective follow-up that
    quotes the rejection reason — up to a small cap of apply attempts.

Every rejection/retry is logged (model, attempt, reason) so a future
"AI edit did nothing" report is debuggable straight from data/lazyclaw.log.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from lazyclaw.docs import ai_edit as docs_ai
from lazyclaw.llm.eco_router import ROLE_WORKER
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

# Total times we ask the worker + try to apply before giving up. The first
# attempt is a plain ask; later attempts add a corrective hint quoting why the
# previous plan was rejected. Always the same configured worker model.
_MAX_APPLY_ATTEMPTS = 2


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


def _is_usable_plan(strategy: Any, plan: dict[str, Any] | None) -> bool:
    """True if `plan` parsed AND is not a per-kind no-op.

    A structurally-valid-but-empty plan (``{"edits": []}``,
    ``{"op":"fill_form","values":{}}``) is treated like a parse miss so the
    caller keeps looking instead of letting it die in ``strategy.apply``.
    """
    if plan is None:
        return False
    is_empty = getattr(strategy, "is_empty_plan", None)
    if callable(is_empty):
        try:
            return not is_empty(plan)
        except Exception:  # noqa: BLE001 — defensive; treat odd plans as usable
            logger.warning("doc_specialist is_empty_plan check raised", exc_info=True)
            return True
    return True


async def _ask_for_plan(
    eco_router: Any,
    strategy: Any,
    messages: list[Any],
    user_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Ask the user's configured worker for a usable JSON plan.

    The model is whatever ``ROLE_WORKER`` resolves to in the user's ECO
    settings (any provider) — nothing is hardcoded. Returns
    ``(plan, model_used)``; ``plan`` is None when nothing usable came back
    (won't parse OR an empty/no-op plan).
    """
    model_used: str | None = ROLE_WORKER
    try:
        resp = await eco_router.chat(messages, user_id, role=ROLE_WORKER)
    except Exception as exc:  # noqa: BLE001 — surface as a friendly error
        logger.warning("doc_specialist worker call failed: %s", exc)
        return None, model_used
    model_used = getattr(resp, "model", None) or model_used
    if getattr(resp, "model", "") == "error":
        logger.warning("doc_specialist worker returned error sentinel: %s", resp.content)
        return None, model_used
    plan = _extract_json(getattr(resp, "content", "") or "")
    if plan is None:
        logger.info("doc_specialist: worker reply was not valid JSON")
        return None, model_used
    if not _is_usable_plan(strategy, plan):
        logger.info(
            "doc_specialist: worker returned an empty/no-op plan (op=%r)",
            plan.get("op"),
        )
        return None, model_used
    return plan, model_used


def _corrective_message(strategy: Any, reason: str) -> Any:
    """A follow-up user message telling the model exactly why it was rejected."""
    from lazyclaw.llm.providers.base import LLMMessage

    shape = getattr(strategy, "PLAN_SHAPE", "") or ""
    text = (
        "Your previous plan was REJECTED: "
        f"{reason}\n"
        "The edits/values/blocks were empty or invalid. You MUST reply with a "
        "JSON plan that POPULATES them with concrete content that actually "
        "performs the requested edit — no empty objects or lists, no prose, no "
        "code fence."
    )
    if shape:
        text += f"\nRequired shape:\n{shape}"
    return LLMMessage(role="user", content=text)


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

    base_messages = strategy.build_messages(ctx, instruction)
    last_reason: str | None = None

    for attempt in range(1, _MAX_APPLY_ATTEMPTS + 1):
        # Always the configured worker. The first attempt is a plain ask; later
        # attempts append a corrective hint quoting why the last plan failed.
        if attempt == 1:
            messages = base_messages
        else:
            messages = list(base_messages) + [
                _corrective_message(strategy, last_reason or "the plan was empty")
            ]
            logger.info(
                "doc_specialist[%s] corrective retry %d/%d (reason=%s)",
                kind, attempt, _MAX_APPLY_ATTEMPTS, last_reason,
            )

        plan, model_used = await _ask_for_plan(
            eco_router, strategy, messages, user_id
        )
        if plan is None:
            last_reason = "the AI did not return a usable, non-empty plan"
            logger.info("doc_specialist[%s] attempt %d: no usable plan", kind, attempt)
            continue

        try:
            result = await strategy.apply(config, user_id, doc_id, ctx, plan)
        except ValueError as exc:
            last_reason = str(exc)
            logger.warning(
                "doc_specialist[%s] attempt %d rejected by apply (model=%s): %s",
                kind, attempt, model_used, exc,
            )
            continue
        except Exception as exc:  # noqa: BLE001 — fail loudly + diagnosably
            logger.exception("doc_specialist[%s] apply failed (model=%s)", kind, model_used)
            return SpecialistResult(ok=False, error=f"Edit failed: {exc}")

        logger.info(
            "doc_specialist[%s] applied on attempt %d (model=%s): %s",
            kind, attempt, model_used, result.get("summary", "done"),
        )
        return SpecialistResult(
            ok=True,
            summary=result.get("summary", "Done."),
            snapshot=result.get("snapshot"),
            new_id=result.get("new_id"),
        )

    detail = f" ({last_reason})" if last_reason else ""
    logger.warning(
        "doc_specialist[%s] gave up after %d attempts%s",
        kind, _MAX_APPLY_ATTEMPTS, detail,
    )
    return SpecialistResult(
        ok=False,
        error=(
            "The AI couldn't turn that into a concrete edit. Try rephrasing it "
            f"more specifically.{detail}"
        ),
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
