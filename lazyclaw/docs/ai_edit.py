"""Docs strategy for the in-editor AI specialist.

The specialist asks the LLM for a strict-JSON *edit plan* (not a tool call, so
it works across every ECO mode) and applies it deterministically here. A plan:

    {
      "mode": "append" | "replace",
      "blocks": [
        {"type": "heading", "level": 1, "text": "Setup"},
        {"type": "number", "text": "Install **deps**"},
        {"type": "paragraph", "text": "see [the site](https://…)"}
      ]
    }

Each block is ``{"type": heading|paragraph|bullet|number, "level"?: int,
"text": "...with **bold**/*italic*/[links](…)"}`` — or a plain markdown string,
or (legacy) ``{"runs": [...]}``. All forms are normalised into the structured
blocks :mod:`lazyclaw.docs.snapshot` understands. This is the single home for
formatted "write into the doc" edits from the web UI and the agent.
"""

from __future__ import annotations

from typing import Any

from lazyclaw.docs import snapshot as D
from lazyclaw.docs.markdown_blocks import parse_blocks, runs_from_inline
from lazyclaw.docs.store import get_doc, save_doc
from lazyclaw.llm.providers.base import LLMMessage

_BLOCK_TYPES = ("heading", "paragraph", "bullet", "number")

# Used in the system prompt and as a contract reference for tests.
PLAN_SHAPE = (
    '{"mode": "append"|"replace", "blocks": [ '
    '{"type": "heading"|"paragraph"|"bullet"|"number", "level": 1, '
    '"text": "content with **bold**, *italic*, [link](https://…)"} ]}'
)

_SYSTEM = (
    "You edit ONE word-processor document. Read the CURRENT DOCUMENT and the "
    "INSTRUCTION, then reply with ONLY a JSON object — no prose, no code fence — "
    "of this exact shape:\n"
    f"{PLAN_SHAPE}\n"
    "Rules:\n"
    "- mode 'append' adds the blocks at the end; 'replace' replaces the whole "
    "document body. Default to 'append' unless the user clearly wants a full "
    "rewrite.\n"
    "- Use 'number' for an ORDERED sequence of steps, 'bullet' for an unordered "
    "list, 'heading' (level 1-3) for section titles, 'paragraph' for prose.\n"
    "- Put inline emphasis in the text with **bold**, *italic*, and links as "
    "[label](https://example.com). NEVER write a literal '1.' or '- ' as text — "
    "use the block 'type' instead.\n"
    "- Keep it minimal: only the blocks the instruction asks for.\n"
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


def _normalize_blocks(items: Any) -> list[dict[str, Any]]:
    """Normalise plan ``blocks`` (or legacy ``paragraphs``) into block dicts.

    Accepts: typed block dicts ``{"type", "level"?, "text"|"runs"}``, plain
    markdown strings (parsed into blocks), legacy ``{"runs": [...]}`` objects,
    and bare run lists. Anything unrecognised is dropped.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, str):
            out.extend(parse_blocks(it))
        elif isinstance(it, dict):
            btype = it.get("type")
            if btype in _BLOCK_TYPES:
                runs = (
                    _normalize_runs(it["runs"])
                    if isinstance(it.get("runs"), list)
                    else runs_from_inline(str(it.get("text", "")))
                )
                out.append(
                    {"type": btype, "level": int(it.get("level") or 0), "runs": runs}
                )
            elif isinstance(it.get("runs"), list):  # legacy {"runs": [...]}
                out.append(
                    {"type": "paragraph", "level": 0, "runs": _normalize_runs(it["runs"])}
                )
            elif isinstance(it.get("text"), str):
                out.extend(parse_blocks(it["text"]))
        elif isinstance(it, list):  # legacy bare run list
            out.append({"type": "paragraph", "level": 0, "runs": _normalize_runs(it)})
    return out


async def apply(
    config: Any, user_id: str, doc_id: str, ctx: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Apply a docs edit plan, persist, and return the fresh snapshot."""
    items = plan.get("blocks")
    if items is None:
        items = plan.get("paragraphs")  # legacy plan shape
    blocks = _normalize_blocks(items)
    if not blocks:
        raise ValueError("plan needs a non-empty 'blocks' (or 'paragraphs') list")
    mode = "replace" if plan.get("mode") == "replace" else "append"

    snap = ctx["payload"]
    if mode == "replace":
        updated = {**snap, "body": D.build_body_with_blocks(blocks)}
        verb = "Replaced the document with"
    else:
        updated = D.append_blocks(snap, blocks)
        verb = "Added"

    await save_doc(config, user_id, ctx["name"], updated, doc_id=doc_id)
    fresh = await get_doc(config, user_id, doc_id)
    n = len(blocks)
    return {
        "summary": f"{verb} {n} block(s).",
        "snapshot": fresh["payload"] if fresh else updated,
        "new_id": None,
    }
