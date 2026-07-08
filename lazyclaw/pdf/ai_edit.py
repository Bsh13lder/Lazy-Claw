"""PDF strategy for the in-editor AI specialist.

PDFs can't be reflow-edited, so the specialist drives the *manage* ops. To feel
as seamless as the Docs/Sheets ✨ (which edit the open document in place), the
single-output ops — ``add_text`` / ``fill_form`` / ``rotate`` / ``merge`` —
replace the OPEN PDF in place: same id, same name, the viewer just reloads. The
pre-edit bytes are stashed as a hidden recoverable version (see
:func:`lazyclaw.pdf.store.archive_and_replace`) so the sidebar never accumulates
``foo - signed - filled.pdf`` clutter. ``generate`` (a brand-new document from
text) and ``split`` (one PDF → many) inherently create NEW files. On success the
specialist returns ``new_id`` — equal to the open ``doc_id`` for an in-place
edit, or a fresh id for generate/split — so the viewer knows whether to reload
or switch. Plan shape:

    {"op": "add_text", "items": [{"page": 1, "x": 72, "y": 700, "text": "Signed"}]}
    {"op": "fill_form", "values": {"Name": "Ada"}, "flatten": false}
    {"op": "rotate", "degrees": 90, "pages": [1]}
    {"op": "split", "ranges": [[1, 2], [3, 3]]}
    {"op": "generate", "text": "…", "title": "Letter"}
    {"op": "merge", "pdf_ids": ["<id or name>", "…"]}
"""

from __future__ import annotations

from typing import Any

from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.pdf import ops
from lazyclaw.pdf.store import archive_and_replace, get_pdf, list_pdfs, save_pdf

PLAN_SHAPE = (
    '{"op": "add_text"|"fill_form"|"rotate"|"split"|"generate"|"merge", ...args}'
)

_SYSTEM = (
    "You manage ONE PDF. Read the PDF SUMMARY and the INSTRUCTION, then reply "
    "with ONLY a JSON object — no prose, no code fence — of this shape:\n"
    f"{PLAN_SHAPE}\n"
    "Ops:\n"
    "- add_text: stamp text/signature. items=[{page(1-based), x, y, text, "
    "size?, font?}]; coords are PDF points from the bottom-left (72pt = 1in).\n"
    "- fill_form: values={field: value} for AcroForm fields (see FORM FIELDS); "
    "optional flatten=true to bake them in.\n"
    "- rotate: degrees (90/180/270), optional pages=[1-based].\n"
    "- split: ranges=[[start,end],…] 1-based inclusive; omit to split per page.\n"
    "- generate: a brand-new PDF from text (+ optional title).\n"
    "- merge: pdf_ids=[ids or names] in order (the OTHER PDFS list helps).\n"
    "PDFs can't be reflow text-edited — pick the closest manage op."
)

_MAX_TEXT = 2500


async def load(config: Any, user_id: str, doc_id: str) -> dict[str, Any] | None:
    pdf = await get_pdf(config, user_id, doc_id)
    if not pdf:
        return None
    fields: dict[str, Any] = {}
    text = ""
    try:
        fields = ops.get_form_fields(pdf["bytes"]) or {}
    except ops.PdfError:
        fields = {}
    try:
        text = ops.extract_text(pdf["bytes"]) or ""
    except ops.PdfError:
        text = ""
    others = [r for r in await list_pdfs(config, user_id) if r["id"] != doc_id]
    return {
        "name": pdf["name"],
        "bytes": pdf["bytes"],
        "pages": pdf.get("pages"),
        "fields": fields,
        "text": text,
        "others": others,
    }


def build_messages(ctx: dict[str, Any], instruction: str) -> list[LLMMessage]:
    text = ctx["text"][:_MAX_TEXT]
    fields = ", ".join(sorted(ctx["fields"])) or "(none)"
    others = "; ".join(f"{r['name']} (id {r['id']})" for r in ctx["others"]) or "(none)"
    user = (
        f"PDF SUMMARY: {ctx['name']} — {ctx.get('pages') or '?'} page(s).\n"
        f"FORM FIELDS: {fields}\n"
        f"OTHER PDFS (for merge): {others}\n"
        f"TEXT (truncated):\n{text or '(no extractable text)'}\n\n"
        f"INSTRUCTION:\n{instruction}"
    )
    return [
        LLMMessage(role="system", content=_SYSTEM),
        LLMMessage(role="user", content=user),
    ]


def _base_name(name: str) -> str:
    return name.rsplit(".pdf", 1)[0] or name


async def _resolve_ref(config: Any, user_id: str, ref: str, rows: list[dict]) -> str | None:
    ref = str(ref).strip()
    for r in rows:
        if r["id"] == ref:
            return r["id"]
    low = ref.lower()
    for r in rows:
        if r["name"].strip().lower() == low or low in r["name"].lower():
            return r["id"]
    return None


def is_empty_plan(plan: dict[str, Any]) -> bool:
    """True if the plan's op payload is empty/missing for the chosen op.

    PDF ops are op-dependent — ``add_text`` needs ``items``, ``fill_form``
    needs ``values``, etc. ``rotate`` and ``split`` are inherently non-empty
    (they default sensibly), so they are never treated as no-ops. Mirrors the
    per-op payload guards in :func:`apply` so a no-op plan triggers a
    corrective retry instead of dying there.
    """
    if not isinstance(plan, dict):
        return True
    op = plan.get("op")
    if op == "add_text":
        items = plan.get("items")
        return not (isinstance(items, list) and items)
    if op == "fill_form":
        values = plan.get("values")
        return not (isinstance(values, dict) and values)
    if op == "generate":
        text = plan.get("text")
        return not (isinstance(text, str) and text.strip())
    if op == "merge":
        refs = plan.get("pdf_ids")
        return not (isinstance(refs, list) and refs)
    if op in ("rotate", "split"):
        return False  # both default to a sensible whole-document op
    # Unknown / missing op → no usable plan.
    return True


async def apply(
    config: Any, user_id: str, doc_id: str, ctx: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    op = plan.get("op")
    data = ctx["bytes"]
    base = _base_name(ctx["name"])

    if op == "add_text":
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("add_text needs a non-empty 'items' list")
        out = ops.overlay_text(data, items)
        return await _replace(config, user_id, doc_id, ctx, out, f"Stamped {len(items)} item(s)")

    if op == "fill_form":
        values = plan.get("values")
        if not isinstance(values, dict) or not values:
            raise ValueError("fill_form needs a non-empty 'values' object")
        out = ops.fill_form(data, values)
        if plan.get("flatten"):
            out = ops.flatten(out)
        return await _replace(config, user_id, doc_id, ctx, out, f"Filled {len(values)} field(s)")

    if op == "rotate":
        degrees = int(plan.get("degrees", 90))
        pages = plan.get("pages") if isinstance(plan.get("pages"), list) else None
        out = ops.rotate(data, degrees, pages)
        return await _replace(config, user_id, doc_id, ctx, out, f"Rotated {degrees}°")

    if op == "split":
        raw = plan.get("ranges")
        ranges = None
        if isinstance(raw, list) and raw:
            ranges = [(int(r[0]), int(r[1])) for r in raw]
        parts = ops.split(data, ranges)
        first_id = None
        for i, part in enumerate(parts, start=1):
            row = await save_pdf(config, user_id, f"{base} - part {i}.pdf", part)
            first_id = first_id or row["id"]
        return {"summary": f"Split into {len(parts)} PDF(s).", "snapshot": None, "new_id": first_id}

    if op == "generate":
        text = plan.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("generate needs 'text'")
        out = ops.generate_from_text(text, title=plan.get("title"))
        nm = (plan.get("title") or "document").strip() or "document"
        row = await save_pdf(config, user_id, f"{nm}.pdf", out)
        return _ok("Generated PDF", row)

    if op == "merge":
        refs = plan.get("pdf_ids")
        if not isinstance(refs, list) or not refs:
            raise ValueError("merge needs a 'pdf_ids' list")
        rows = await list_pdfs(config, user_id)
        parts = [data]
        for ref in refs:
            rid = await _resolve_ref(config, user_id, ref, rows)
            if not rid:
                raise ValueError(f"merge: no PDF matching '{ref}'")
            other = await get_pdf(config, user_id, rid)
            if other:
                parts.append(other["bytes"])
        out = ops.merge(parts)
        return await _replace(config, user_id, doc_id, ctx, out, f"Merged {len(parts)} PDFs")

    raise ValueError(f"unknown pdf op: {op!r}")


async def _replace(
    config: Any,
    user_id: str,
    doc_id: str,
    ctx: dict[str, Any],
    out: bytes,
    summary: str,
) -> dict[str, Any]:
    """Overwrite the OPEN PDF in place (keeping its name), archiving the prior
    bytes as a recoverable version. Returns ``new_id == doc_id`` so the viewer
    reloads the same file instead of switching to a fork."""
    row = await archive_and_replace(config, user_id, doc_id, out, name=ctx["name"])
    return {
        "summary": f"{summary} → {row['name']}.",
        "snapshot": None,
        "new_id": doc_id,
    }


def _ok(summary: str, row: dict) -> dict[str, Any]:
    """For ops that legitimately create a NEW file (generate / split)."""
    return {
        "summary": f"{summary} → {row['name']}.",
        "snapshot": None,
        "new_id": row["id"],
    }
