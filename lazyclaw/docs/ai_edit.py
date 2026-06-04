"""Docs strategy for the in-editor AI specialist.

The specialist asks the LLM for a strict-JSON *edit plan* (not a tool call, so
it works across every ECO mode) and applies it deterministically here. A plan:

    {
      "mode": "append" | "replace",
      "paragraphs": [
        {"runs": [{"text": "Visit "}, {"text": "my site", "url": "https://…"}]},
        "plain paragraph, may contain [markdown](https://…) links"
      ]
    }

``paragraphs`` entries may be a ``{"runs": [...]}`` object, a plain string (with
optional inline markdown links), or a bare run list — all are normalised. This
is the single home for "add text with a link" from the web UI.
"""

from __future__ import annotations

from typing import Any

from lazyclaw.docs import snapshot as D
from lazyclaw.docs.store import get_doc, save_doc
from lazyclaw.llm.providers.base import LLMMessage

# Used in the system prompt and as a contract reference for tests.
PLAN_SHAPE = (
    '{"mode": "append"|"replace", "paragraphs": [ '
    '{"runs": [{"text": "plain "}, {"text": "linked words", "url": "https://…"}]} ]}'
)

_SYSTEM = (
    "You edit ONE word-processor document. Read the CURRENT DOCUMENT and the "
    "INSTRUCTION, then reply with ONLY a JSON object — no prose, no code fence — "
    "of this exact shape:\n"
    f"{PLAN_SHAPE}\n"
    "Rules:\n"
    "- mode 'append' adds the paragraphs at the end; 'replace' replaces the "
    "whole document body. Default to 'append' unless the user clearly wants a "
    "full rewrite.\n"
    "- To add a clickable link, give a run with a 'url'. A paragraph may also be "
    "a plain string containing markdown links like [label](https://example.com).\n"
    "- Keep it minimal: only the paragraphs the instruction asks for.\n"
    "- Write the actual content the user wants; never echo these instructions."
)

_MAX_DOC_CHARS = 6000


async def load(config: Any, user_id: str, doc_id: str) -> dict[str, Any] | None:
    """Load the doc (name + decrypted Univer payload), or None if missing."""
    return await get_doc(config, user_id, doc_id)


def build_messages(ctx: dict[str, Any], instruction: str) -> list[LLMMessage]:
    """Build the system+user messages embedding the current document text."""
    current = D.get_text(ctx["payload"])
    if len(current) > _MAX_DOC_CHARS:
        current = current[:_MAX_DOC_CHARS] + "\n…(truncated)"
    user = (
        f"CURRENT DOCUMENT (plain text):\n{current or '(empty)'}\n\n"
        f"INSTRUCTION:\n{instruction}"
    )
    return [
        LLMMessage(role="system", content=_SYSTEM),
        LLMMessage(role="user", content=user),
    ]


def _normalize_runs(runs: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(runs, list):
        return out
    for r in runs:
        if isinstance(r, dict) and isinstance(r.get("text"), str):
            run: dict[str, Any] = {"text": r["text"]}
            if r.get("url"):
                run["url"] = str(r["url"])
            out.append(run)
        elif isinstance(r, str):
            out.append({"text": r})
    return out


def _normalize_paragraphs(paragraphs: Any) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    if not isinstance(paragraphs, list):
        return out
    for p in paragraphs:
        if isinstance(p, str):
            out.append(D.runs_from_markdown(p))
        elif isinstance(p, dict):
            if isinstance(p.get("runs"), list):
                out.append(_normalize_runs(p["runs"]))
            elif isinstance(p.get("text"), str):
                out.append(D.runs_from_markdown(p["text"]))
            else:
                out.append([])
        elif isinstance(p, list):
            out.append(_normalize_runs(p))
        else:
            out.append([])
    return out


async def apply(
    config: Any, user_id: str, doc_id: str, ctx: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Apply a docs edit plan, persist, and return the fresh snapshot."""
    paragraphs = _normalize_paragraphs(plan.get("paragraphs"))
    if not paragraphs:
        raise ValueError("plan needs a non-empty 'paragraphs' list")
    mode = "replace" if plan.get("mode") == "replace" else "append"

    snap = ctx["payload"]
    if mode == "replace":
        updated = {**snap, "body": D.build_body_with_runs(paragraphs)}
        verb = "Replaced the document with"
    else:
        updated = snap
        for runs in paragraphs:
            updated = D.append_paragraph_with_runs(updated, runs)
        verb = "Added"

    await save_doc(config, user_id, ctx["name"], updated, doc_id=doc_id)
    fresh = await get_doc(config, user_id, doc_id)
    n = len(paragraphs)
    return {
        "summary": f"{verb} {n} paragraph(s).",
        "snapshot": fresh["payload"] if fresh else updated,
        "new_id": None,
    }
