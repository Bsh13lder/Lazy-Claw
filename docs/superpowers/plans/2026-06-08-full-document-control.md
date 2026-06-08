# Full Document Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make agents write properly-formatted documents (real lists/headings/bold) everywhere, and give the Flutter mobile app full native Sheets + Docs editors (manual control, formula helper, formatting).

**Architecture:** The Univer `IDocumentData`/`IWorkbookData` snapshot is the single shared contract across web-Univer, the backend agent, and the native Flutter editors. Phase A enriches that snapshot model (the foundation + the actual "ugly formatting" fix, shippable on its own). Phase B builds the native Sheets editor (+ a server recalc endpoint). Phase C builds the native Docs editor (flutter_quill ↔ Univer converter).

**Tech Stack:** Python (FastAPI, pytest, python-docx, xlcalculator), Dart/Flutter (Riverpod, Dio, flutter_quill, pluto_grid candidate), Univer `@univerjs/core` 0.24.

**Spec:** `docs/superpowers/specs/2026-06-08-full-document-control-design.md`

---

## Conventions for this plan

- Run Python tests from repo root: `pytest <path> -v`.
- Run Dart tests from `mobile/`: `cd mobile && flutter test <path>`.
- Each task is TDD: failing test → run-fails → implement → run-passes → commit.
- Commits are scoped to the files in the task (per the pre-commit-auto-stage lesson, `git add` only the named files).
- The working tree currently holds unrelated WIP — every commit step lists exact paths; never `git add -A`.

---

# PHASE A — Backend rich formatting (foundation + ugly-fix)

Ships value immediately: after Phase A the web Univer editor and `.docx` export render real headings, numbered lists, bullet lists, and bold/italic that the agent produces.

## Task A0: Verify exact Univer field shapes (spike — no test, records constants)

**Files:**
- Read: `web/node_modules/@univerjs/core/lib/types/types/interfaces/i-document-data.d.ts`
- Read: `web/node_modules/@univerjs/core/lib/cjs/index.js` (enum values for `PresetListType`, `NamedStyleType`)
- Create: `docs/superpowers/plans/A0-univer-field-notes.md` (scratch notes consumed by later tasks)

- [ ] **Step 1: Extract the real field names/values.** Grep the un-minified declarations and the cjs enum bodies:

```bash
cd /Users/blckit/Desktop/Code_Projects/lazyclaw
# Paragraph + bullet + paragraphStyle + textstyle field names
sed -n '1,200p' web/node_modules/@univerjs/core/lib/types/types/interfaces/i-document-data.d.ts
# Enum literal values
grep -nE 'NamedStyleType|HEADING_1|NORMAL_TEXT|PresetListType|BULLET_LIST"|ORDER_LIST"|BooleanNumber' web/node_modules/@univerjs/core/lib/cjs/index.js | head -60
```

- [ ] **Step 2: Confirm a real render.** Open the web app's Docs editor, create a doc, manually make a numbered list + a heading + bold text, save, then read the persisted snapshot via `GET /api/docs/{id}` (browser devtools or `curl` with the session cookie). Copy the exact `body.paragraphs[*].paragraphStyle`, `body.paragraphs[*].bullet`, and `body.textRuns[*].ts` JSON into `A0-univer-field-notes.md`. **This captured-from-Univer JSON is the authoritative shape** the Python builders and Dart serializers must reproduce.

- [ ] **Step 3: Record the resolved constants** in `A0-univer-field-notes.md`:
  - Heading: how Univer marks H1/H2/H3 (`paragraphStyle.namedStyleType` value vs `paragraphStyle.headingId`).
  - Bullet list: `paragraph.bullet` exact keys (`listType`, `listId`, `nestingLevel`) + whether a `body.lists`/`customLists` definition is required, and how consecutive ordered items share numbering (same `listId`?).
  - Run styling: bold/italic/underline keys inside `textRuns[*].ts` (`bl`, `it`, `ul` — confirm underline is `{"s": 1}` object vs `1`).

- [ ] **Step 4: Commit the notes.**

```bash
git add docs/superpowers/plans/A0-univer-field-notes.md
git commit -m "docs: capture verified Univer paragraph/bullet/run field shapes"
```

> Later tasks reference these as the "verified shapes". Where this plan shows a concrete shape below, it is the *expected* shape — if A0 finds it differs, use A0's captured JSON and adjust the assertion + builder together.

---

## Task A1: `docs/markdown_blocks.py` — markdown → structured blocks

A **block** is the unit Phase A introduces: `{"type": "heading"|"paragraph"|"bullet"|"number", "level": int, "runs": [run, ...]}` where a **run** is `{"text": str, "bold"?: bool, "italic"?: bool, "underline"?: bool, "url"?: str}`. This module is pure (no Univer shape knowledge — that lives in `snapshot.py`).

**Files:**
- Create: `lazyclaw/docs/markdown_blocks.py`
- Test: `tests/docs/test_markdown_blocks.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/docs/test_markdown_blocks.py
from lazyclaw.docs.markdown_blocks import parse_blocks, runs_from_inline


def test_heading_levels():
    blocks = parse_blocks("# Title\n## Sub\n### Small")
    assert [b["type"] for b in blocks] == ["heading", "heading", "heading"]
    assert [b["level"] for b in blocks] == [1, 2, 3]
    assert blocks[0]["runs"] == [{"text": "Title"}]


def test_bullet_and_number_lists():
    blocks = parse_blocks("- one\n- two\n1. first\n2. second")
    assert [b["type"] for b in blocks] == ["bullet", "bullet", "number", "number"]
    assert blocks[2]["runs"] == [{"text": "first"}]


def test_inline_bold_italic_link():
    runs = runs_from_inline("plain **bold** and *italic* and [site](https://x.io)")
    assert {"text": "bold", "bold": True} in runs
    assert {"text": "italic", "italic": True} in runs
    assert {"text": "site", "url": "https://x.io"} in runs


def test_plain_paragraph_passthrough():
    blocks = parse_blocks("just a line")
    assert blocks == [{"type": "paragraph", "level": 0, "runs": [{"text": "just a line"}]}]


def test_malformed_never_raises():
    # Unbalanced markers degrade to plain text, never raise.
    blocks = parse_blocks("**oops and [bad](nope")
    assert blocks[0]["type"] == "paragraph"
    assert "oops" in blocks[0]["runs"][0]["text"]
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `pytest tests/docs/test_markdown_blocks.py -v`
Expected: FAIL — `ModuleNotFoundError: lazyclaw.docs.markdown_blocks`

- [ ] **Step 3: Write minimal implementation.**

```python
# lazyclaw/docs/markdown_blocks.py
"""Parse a small, safe subset of Markdown into structured document *blocks*.

A block is ``{"type": "heading"|"paragraph"|"bullet"|"number", "level": int,
"runs": [run, ...]}``; a run is ``{"text": str, "bold"?, "italic"?,
"underline"?, "url"?}``. This module is pure text→dict shaping with no Univer
knowledge — :mod:`lazyclaw.docs.snapshot` turns blocks into the Univer wire
form. Malformed markup degrades to plain text and NEVER raises.
"""

from __future__ import annotations

import re
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+[.)]\s+(.*)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)|_([^_]+)_")


def runs_from_inline(text: str) -> list[dict[str, Any]]:
    """Split one line into styled runs (bold/italic/link). Plain on failure."""
    text = str(text or "")
    # Tokenise by scanning for the earliest of: link, bold, italic.
    runs: list[dict[str, Any]] = []
    pos = 0
    while pos < len(text):
        nxt = _earliest_match(text, pos)
        if nxt is None:
            runs.append({"text": text[pos:]})
            break
        kind, m = nxt
        if m.start() > pos:
            runs.append({"text": text[pos:m.start()]})
        if kind == "link":
            runs.append({"text": m.group(1), "url": m.group(2)})
        elif kind == "bold":
            runs.append({"text": m.group(1), "bold": True})
        else:  # italic
            runs.append({"text": m.group(1) or m.group(2), "italic": True})
        pos = m.end()
    merged = [r for r in runs if r.get("text") != ""]
    return merged or [{"text": text}]


def _earliest_match(text: str, pos: int):
    candidates = []
    for kind, rx in (("link", _LINK_RE), ("bold", _BOLD_RE), ("italic", _ITALIC_RE)):
        m = rx.search(text, pos)
        if m:
            candidates.append((m.start(), kind, m))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    _, kind, m = candidates[0]
    return kind, m


def parse_blocks(text: str) -> list[dict[str, Any]]:
    """Parse multi-line markdown into a list of blocks (one per line)."""
    blocks: list[dict[str, Any]] = []
    for raw in str(text or "").split("\n"):
        line = raw.rstrip("\r")
        h = _HEADING_RE.match(line)
        if h:
            blocks.append({"type": "heading", "level": len(h.group(1)),
                           "runs": runs_from_inline(h.group(2))})
            continue
        b = _BULLET_RE.match(line)
        if b:
            blocks.append({"type": "bullet", "level": 0,
                           "runs": runs_from_inline(b.group(1))})
            continue
        n = _NUMBER_RE.match(line)
        if n:
            blocks.append({"type": "number", "level": 0,
                           "runs": runs_from_inline(n.group(1))})
            continue
        blocks.append({"type": "paragraph", "level": 0,
                       "runs": runs_from_inline(line)})
    return blocks or [{"type": "paragraph", "level": 0, "runs": [{"text": ""}]}]
```

- [ ] **Step 4: Run test to verify it passes.**

Run: `pytest tests/docs/test_markdown_blocks.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit.**

```bash
git add lazyclaw/docs/markdown_blocks.py tests/docs/test_markdown_blocks.py
git commit -m "feat(docs): markdown→blocks parser (headings, lists, bold/italic/links)"
```

---

## Task A2: extend `docs/snapshot.py` — emit + read styled runs, headings, lists

Add `build_body_with_blocks(blocks)` (the new rich builder) and a reader `get_blocks(snap)`. Keep `build_body_with_runs` working (delegate to the block builder with all-paragraph blocks) so existing callers/tests are untouched.

**Files:**
- Modify: `lazyclaw/docs/snapshot.py` (add block builder + reader; extend run emission with `ts`)
- Test: `tests/test_docs_snapshot_blocks.py` (new file; existing `tests/test_docs_snapshot*.py` must still pass)

- [ ] **Step 1: Write the failing test** (use the shapes verified in A0 — adjust literals to match `A0-univer-field-notes.md` if they differ):

```python
# tests/test_docs_snapshot_blocks.py
from lazyclaw.docs import snapshot as D


def _para(snap, i):
    return snap["body"]["paragraphs"][i]


def test_heading_block_sets_paragraph_style():
    blocks = [{"type": "heading", "level": 1, "runs": [{"text": "Title"}]}]
    body = D.build_body_with_blocks(blocks)
    # heading marker present (namedStyleType OR headingId — per A0 notes)
    assert "paragraphStyle" in body["paragraphs"][0]


def test_bullet_block_sets_bullet():
    blocks = [{"type": "bullet", "level": 0, "runs": [{"text": "x"}]}]
    body = D.build_body_with_blocks(blocks)
    assert "bullet" in body["paragraphs"][0]
    assert body["paragraphs"][0]["bullet"]["listType"] == "BULLET_LIST"


def test_consecutive_number_items_share_list_id():
    blocks = [
        {"type": "number", "level": 0, "runs": [{"text": "a"}]},
        {"type": "number", "level": 0, "runs": [{"text": "b"}]},
    ]
    body = D.build_body_with_blocks(blocks)
    lid0 = body["paragraphs"][0]["bullet"]["listId"]
    lid1 = body["paragraphs"][1]["bullet"]["listId"]
    assert lid0 == lid1  # same run → continuous 1,2 numbering


def test_bold_run_emits_textrun_style():
    blocks = [{"type": "paragraph", "level": 0,
               "runs": [{"text": "hi ", }, {"text": "bold", "bold": True}]}]
    body = D.build_body_with_blocks(blocks)
    runs = body["textRuns"]
    assert any(tr.get("ts", {}).get("bl") for tr in runs)


def test_blocks_roundtrip_text():
    blocks = [{"type": "number", "level": 0, "runs": [{"text": "first"}]}]
    body = D.build_body_with_blocks(blocks)
    snap = {"id": "doc-1", "documentStyle": {}, "body": body}
    out = D.get_blocks(snap)
    assert out[0]["type"] == "number"
    assert out[0]["runs"][0]["text"] == "first"


def test_build_body_with_runs_still_works():
    body = D.build_body_with_runs([[{"text": "plain"}]])
    assert body["dataStream"].startswith("plain")
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `pytest tests/test_docs_snapshot_blocks.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'build_body_with_blocks'`

- [ ] **Step 3: Write minimal implementation.** Add to `lazyclaw/docs/snapshot.py` (after `build_body_with_runs`). Use the A0-verified constants — the values below are the expected Univer 0.24 shapes:

```python
# ───────────────────────── rich blocks (styles + lists) ─────────────
# Univer NamedStyleType heading values (verify in A0; expected 0.24):
_HEADING_NAMED_STYLE = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}
# PresetListType literals (verify in A0):
_LIST_TYPE = {"bullet": "BULLET_LIST", "number": "ORDER_LIST"}


def _run_style(run: dict[str, Any]) -> dict[str, Any]:
    """Map a run's bold/italic/underline flags to a Univer ``ts`` style dict."""
    ts: dict[str, Any] = {}
    if run.get("bold"):
        ts["bl"] = 1
    if run.get("italic"):
        ts["it"] = 1
    if run.get("underline"):
        ts["ul"] = {"s": 1}
    return ts


def build_body_with_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a Univer ``body`` from structured blocks (headings/lists/styles).

    Emits ``paragraphs[*].paragraphStyle`` for headings, ``paragraphs[*].bullet``
    for list items (consecutive same-type items share a ``listId`` so ordered
    lists number continuously), ``textRuns`` for bold/italic/underline runs, and
    hyperlink ``customRanges`` (reusing the sentinel convention). Empty input →
    one empty paragraph.
    """
    if not blocks:
        blocks = [{"type": "paragraph", "level": 0, "runs": [{"text": ""}]}]

    stream_parts: list[str] = []
    para_meta: list[dict[str, Any]] = []
    custom_ranges: list[dict[str, Any]] = []
    text_runs: list[dict[str, Any]] = []
    cursor = 0
    link_counter = 0
    list_counter = 0
    prev_list_kind: str | None = None
    cur_list_id: str | None = None

    for block in blocks:
        btype = block.get("type", "paragraph")
        runs = block.get("runs") or []
        for run in runs:
            if not isinstance(run, dict):
                continue
            text = _clean_run_text(run.get("text", ""))
            url = run.get("url")
            run_start = cursor
            if url:
                stream_parts.append(CUSTOM_RANGE_START)
                cursor += 1
                stream_parts.append(text)
                text_start = cursor
                cursor += len(text)
                end_token = cursor
                stream_parts.append(CUSTOM_RANGE_END)
                cursor += 1
                custom_ranges.append({
                    "startIndex": run_start, "endIndex": end_token,
                    "rangeId": f"link-{link_counter}", "rangeType": _HYPERLINK,
                    "properties": {"url": str(url)},
                })
                link_counter += 1
                style = _run_style(run)
                if style and text:
                    text_runs.append({"st": text_start, "ed": text_start + len(text), "ts": style})
            else:
                stream_parts.append(text)
                cursor += len(text)
                style = _run_style(run)
                if style and text:
                    text_runs.append({"st": run_start, "ed": run_start + len(text), "ts": style})

        stream_parts.append(PARAGRAPH_BREAK)
        meta: dict[str, Any] = {"startIndex": cursor}
        if btype == "heading":
            level = int(block.get("level") or 1)
            named = _HEADING_NAMED_STYLE.get(level, "HEADING_1")
            meta["paragraphStyle"] = {"namedStyleType": named}
        elif btype in ("bullet", "number"):
            if prev_list_kind != btype:
                cur_list_id = f"list-{list_counter}"
                list_counter += 1
            prev_list_kind = btype
            meta["bullet"] = {
                "listType": _LIST_TYPE[btype],
                "listId": cur_list_id,
                "nestingLevel": int(block.get("level") or 0),
            }
        if btype not in ("bullet", "number"):
            prev_list_kind = None
        para_meta.append(meta)
        cursor += 1

    data_stream = "".join(stream_parts) + SECTION_BREAK
    return {
        "dataStream": data_stream,
        "paragraphs": para_meta,
        "textRuns": text_runs,
        "customRanges": custom_ranges,
        "sectionBreaks": [{"startIndex": len(data_stream) - 1}],
    }


def get_blocks(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Inverse of :func:`build_body_with_blocks`: paragraphs → blocks.

    Reconstructs type (heading/bullet/number/paragraph) from ``paragraphStyle``
    / ``bullet`` metadata and runs (with bold/italic/underline/url) from
    ``textRuns`` + ``customRanges``. Robust to missing metadata (→ paragraph).
    """
    body = snap.get("body") if isinstance(snap, dict) else None
    if not isinstance(body, dict):
        return []
    paras_meta = body.get("paragraphs") or []
    run_lists = get_paragraph_runs(snap)  # plain text + hyperlink runs per para
    styled = _styled_runs_by_index(body)
    blocks: list[dict[str, Any]] = []
    for i, runs in enumerate(run_lists):
        meta = paras_meta[i] if i < len(paras_meta) and isinstance(paras_meta[i], dict) else {}
        btype, level = _block_type_from_meta(meta)
        merged = _apply_styles_to_runs(runs, styled, meta_index=i, body=body)
        blocks.append({"type": btype, "level": level, "runs": merged})
    return blocks


def _block_type_from_meta(meta: dict[str, Any]) -> tuple[str, int]:
    bullet = meta.get("bullet")
    if isinstance(bullet, dict):
        lt = str(bullet.get("listType", ""))
        kind = "number" if lt.startswith("ORDER") else "bullet"
        return kind, int(bullet.get("nestingLevel") or 0)
    ps = meta.get("paragraphStyle")
    if isinstance(ps, dict):
        named = str(ps.get("namedStyleType", ""))
        for lvl, name in _HEADING_NAMED_STYLE.items():
            if named == name:
                return "heading", lvl
    return "paragraph", 0
```

Plus two small read helpers `_styled_runs_by_index` / `_apply_styles_to_runs` that overlay `textRuns[*].ts` (bold/italic/underline) onto the text runs returned by `get_paragraph_runs`, keyed by absolute char index. Implement them to map any run span overlapping a `ts` with `bl`/`it`/`ul` to `bold`/`italic`/`underline` flags. (Round-trip is exercised by `test_blocks_roundtrip_text`; extend that test to assert a bold flag survives.)

- [ ] **Step 4: Run tests (new + existing snapshot tests).**

Run: `pytest tests/test_docs_snapshot_blocks.py tests/ -k "docs_snapshot or snapshot" -v`
Expected: PASS (new file) and no regressions in existing snapshot tests.

- [ ] **Step 5: Commit.**

```bash
git add lazyclaw/docs/snapshot.py tests/test_docs_snapshot_blocks.py
git commit -m "feat(docs): snapshot supports headings, bullet/numbered lists, bold/italic runs"
```

---

## Task A3: extend `docs/ai_edit.py` — structured plan shape + prompt

The doc specialist now emits blocks. Extend `PLAN_SHAPE`, `_SYSTEM`, and `_normalize_paragraphs` to accept block dicts and markdown strings (parsed via `markdown_blocks`), routing through `build_body_with_blocks`.

**Files:**
- Modify: `lazyclaw/docs/ai_edit.py`
- Test: `tests/test_docs_ai_edit_blocks.py` (new)

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_docs_ai_edit_blocks.py
from lazyclaw.docs import ai_edit


def test_normalize_accepts_block_dicts():
    blocks = ai_edit._normalize_blocks([
        {"type": "heading", "level": 2, "text": "Steps"},
        {"type": "number", "text": "first"},
        {"type": "number", "text": "second"},
    ])
    assert blocks[0]["type"] == "heading" and blocks[0]["level"] == 2
    assert [b["type"] for b in blocks[1:]] == ["number", "number"]


def test_normalize_markdown_string_becomes_blocks():
    blocks = ai_edit._normalize_blocks(["# Title", "- a", "- b"])
    assert blocks[0]["type"] == "heading"
    assert blocks[1]["type"] == "bullet" and blocks[2]["type"] == "bullet"


def test_plan_shape_mentions_lists():
    assert "number" in ai_edit.PLAN_SHAPE or "bullet" in ai_edit.PLAN_SHAPE
```

- [ ] **Step 2: Run — fails** (`_normalize_blocks` undefined).

Run: `pytest tests/test_docs_ai_edit_blocks.py -v`

- [ ] **Step 3: Implement.** In `lazyclaw/docs/ai_edit.py`: add `from lazyclaw.docs.markdown_blocks import parse_blocks, runs_from_inline`. Replace `PLAN_SHAPE`/`_SYSTEM` and add `_normalize_blocks`:

```python
PLAN_SHAPE = (
    '{"mode": "append"|"replace", "blocks": [ '
    '{"type": "heading"|"paragraph"|"bullet"|"number", "level": 1, '
    '"text": "content with **bold**, *italic*, [link](https://…)"} ]}'
)

_SYSTEM = (
    "You edit ONE word-processor document. Read the CURRENT DOCUMENT and the "
    "INSTRUCTION, then reply with ONLY a JSON object — no prose, no code fence — "
    f"of this exact shape:\n{PLAN_SHAPE}\n"
    "Rules:\n"
    "- mode 'append' adds blocks at the end; 'replace' replaces the whole body. "
    "Default to 'append' unless the user clearly wants a full rewrite.\n"
    "- Use 'number' for an ORDERED sequence of steps, 'bullet' for an unordered "
    "list, 'heading' (level 1-3) for section titles, 'paragraph' for prose.\n"
    "- Put inline emphasis in the text with **bold**, *italic*, and links as "
    "[label](https://example.com). Never write a literal '1.' or '- ' — use the "
    "block 'type' instead.\n"
    "- Keep it minimal: only the blocks the instruction asks for.\n"
    "- Write the actual content the user wants; never echo these instructions."
)


def _normalize_blocks(items: Any) -> list[dict[str, Any]]:
    """Normalise plan 'blocks' (or legacy 'paragraphs') into block dicts."""
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, str):
            out.extend(parse_blocks(it))
        elif isinstance(it, dict):
            btype = it.get("type")
            if btype in ("heading", "paragraph", "bullet", "number"):
                runs = it["runs"] if isinstance(it.get("runs"), list) else \
                    runs_from_inline(str(it.get("text", "")))
                out.append({"type": btype, "level": int(it.get("level") or 0),
                            "runs": runs})
            elif isinstance(it.get("runs"), list):  # legacy {"runs":[...]}
                out.append({"type": "paragraph", "level": 0, "runs": it["runs"]})
            elif isinstance(it.get("text"), str):
                out.extend(parse_blocks(it["text"]))
    return out
```

Update `apply()` to read `plan.get("blocks")` (falling back to `plan.get("paragraphs")` for legacy), call `_normalize_blocks`, and build via `D.build_body_with_blocks` for replace / a new `D.append_blocks` for append. Add `append_blocks(snap, blocks)` to `snapshot.py` mirroring `append_paragraph_with_runs` but block-aware (read existing blocks via `get_blocks`, concat, rebuild). Keep the summary line (`f"{verb} {n} block(s)."`).

- [ ] **Step 4: Run — passes.**

Run: `pytest tests/test_docs_ai_edit_blocks.py -v`

- [ ] **Step 5: Commit.**

```bash
git add lazyclaw/docs/ai_edit.py lazyclaw/docs/snapshot.py tests/test_docs_ai_edit_blocks.py
git commit -m "feat(docs): AI edit plan emits structured blocks (lists/headings/emphasis)"
```

---

## Task A4: extend `docs/docx_io.py` — export/import real lists, headings, bold

**Files:**
- Modify: `lazyclaw/docs/docx_io.py`
- Test: `tests/test_docx_io_formatting.py` (new)

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_docx_io_formatting.py
import io
from docx import Document
from lazyclaw.docs import snapshot as D
from lazyclaw.docs.docx_io import snapshot_to_docx, docx_to_snapshot


def _snap(blocks):
    return {"id": "doc-1", "documentStyle": {}, "body": D.build_body_with_blocks(blocks)}


def test_numbered_list_exports_as_list_number_style():
    snap = _snap([
        {"type": "number", "level": 0, "runs": [{"text": "first"}]},
        {"type": "number", "level": 0, "runs": [{"text": "second"}]},
    ])
    doc = Document(io.BytesIO(snapshot_to_docx(snap)))
    styles = [p.style.name for p in doc.paragraphs if p.text]
    assert all("List Number" in s for s in styles)


def test_heading_exports_as_heading_style():
    snap = _snap([{"type": "heading", "level": 1, "runs": [{"text": "Title"}]}])
    doc = Document(io.BytesIO(snapshot_to_docx(snap)))
    assert any("Heading 1" in p.style.name for p in doc.paragraphs if p.text)


def test_bold_run_exports_bold():
    snap = _snap([{"type": "paragraph", "level": 0,
                   "runs": [{"text": "x", "bold": True}]}])
    doc = Document(io.BytesIO(snapshot_to_docx(snap)))
    assert any(r.bold for p in doc.paragraphs for r in p.runs)


def test_docx_list_roundtrips_to_number_block():
    snap = _snap([{"type": "number", "level": 0, "runs": [{"text": "a"}]}])
    back = docx_to_snapshot(snapshot_to_docx(snap), "RT")
    assert D.get_blocks(back)[0]["type"] == "number"
```

- [ ] **Step 2: Run — fails.**

Run: `pytest tests/test_docx_io_formatting.py -v`

- [ ] **Step 3: Implement.** Rewrite `snapshot_to_docx` to walk `D.get_blocks(snap)` instead of `get_paragraph_runs`, mapping block type → python-docx style:

```python
def snapshot_to_docx(snap: dict[str, Any]) -> bytes:
    document = Document()
    for block in D.get_blocks(snap):
        btype = block.get("type", "paragraph")
        runs = block.get("runs") or []
        if btype == "heading":
            level = int(block.get("level") or 1)
            para = document.add_heading("", level=level)
        elif btype == "number":
            para = document.add_paragraph(style="List Number")
        elif btype == "bullet":
            para = document.add_paragraph(style="List Bullet")
        else:
            para = document.add_paragraph()
        for run in runs:
            url = run.get("url")
            if url:
                _add_hyperlink(para, run.get("text", ""), str(url))
            else:
                r = para.add_run(run.get("text", ""))
                if run.get("bold"):
                    r.bold = True
                if run.get("italic"):
                    r.italic = True
                if run.get("underline"):
                    r.underline = True
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()
```

For `docx_to_snapshot`: read each paragraph's `style.name` → block type (`List Number`→number, `List Bullet`→bullet, `Heading N`→heading level N, else paragraph), read run-level `bold`/`italic`/`underline`, and build via `D.build_body_with_blocks`. Replace the current `get_paragraph_runs`/`build_body_with_runs` path. Guard unknown styles → paragraph (graceful).

- [ ] **Step 4: Run — passes** (and existing docx tests still pass).

Run: `pytest tests/test_docx_io_formatting.py tests/ -k docx -v`

- [ ] **Step 5: Commit.**

```bash
git add lazyclaw/docs/docx_io.py tests/test_docx_io_formatting.py
git commit -m "feat(docs): docx export/import real lists, headings, bold/italic"
```

---

## Task A5: extend `skills/builtin/docs.py` — markdown-aware append/set

**Files:**
- Read first: `lazyclaw/skills/builtin/docs.py` (find `AppendToDocSkill`, `SetDocContentSkill`)
- Modify: `lazyclaw/skills/builtin/docs.py`
- Test: `tests/test_docs_skills_markdown.py` (new)

- [ ] **Step 1: Write the failing test.** (Mirror the existing skill-test pattern in `tests/` — locate an existing docs-skill test and copy its fixture/`execute` invocation style.) Assert that appending markdown `"1. a\n2. b"` produces two `number` blocks (not literal "1." text):

```python
# tests/test_docs_skills_markdown.py
import pytest
from lazyclaw.docs import snapshot as D
# Reuse whatever in-memory config/store fixture the existing docs-skill tests use.

@pytest.mark.asyncio
async def test_append_markdown_numbered_list(docs_test_ctx):  # fixture from existing suite
    skill, config, user_id, doc_id = docs_test_ctx
    await skill.execute({"doc_id": doc_id, "markdown": "1. a\n2. b"},
                        config=config, user_id=user_id)
    snap = (await _load(config, user_id, doc_id))["payload"]
    blocks = D.get_blocks(snap)
    types = [b["type"] for b in blocks if b["runs"] and b["runs"][0]["text"]]
    assert types[-2:] == ["number", "number"]
```

> If the existing docs-skill tests don't expose a reusable fixture, add a minimal local fixture that uses the same in-memory store setup they use; do NOT invent a new store API.

- [ ] **Step 2: Run — fails.**

Run: `pytest tests/test_docs_skills_markdown.py -v`

- [ ] **Step 3: Implement.** Add an optional `markdown` param to `AppendToDocSkill`/`SetDocContentSkill` schema. When present, parse via `markdown_blocks.parse_blocks` and apply via `D.append_blocks` / `D.build_body_with_blocks`. Keep the existing plain-text/runs params working (back-compat). Update the skill `description` to tell the agent: "pass `markdown` with `#` headings, `-`/`1.` lists, `**bold**` — never hand-format lists as plain text."

- [ ] **Step 4: Run — passes.**

Run: `pytest tests/test_docs_skills_markdown.py tests/ -k "docs_skill or builtin_docs" -v`

- [ ] **Step 5: Commit.**

```bash
git add lazyclaw/skills/builtin/docs.py tests/test_docs_skills_markdown.py
git commit -m "feat(docs): doc skills accept markdown (real lists/headings/emphasis)"
```

---

## Task A6: SOUL.md formatting guidance (prompt-first lever)

**Files:**
- Modify: `personality/SOUL.md`

- [ ] **Step 1: Add guidance.** Find the document/skills section of `personality/SOUL.md` and add a concise rule:

> When writing into a Sheet/Doc, format properly: use a NUMBERED list for ordered steps, a BULLET list for unordered items, `#` headings for sections, and `**bold**` for emphasis — pass these as markdown/blocks to the doc skills. NEVER emit a literal "1." or "- " as plain paragraph text.

- [ ] **Step 2: Verify length.** `wc -c personality/SOUL.md` — ensure no file-size rule is breached (SOUL.md has no hard cap but keep the addition tight).

- [ ] **Step 3: Commit.**

```bash
git add personality/SOUL.md
git commit -m "docs(soul): teach proper list/heading formatting in documents"
```

---

## Task A7: Phase A verification (manual, no commit)

- [ ] **Step 1:** `make rebuild` (or restart the server) so the new code loads.
- [ ] **Step 2:** In the web Docs editor, open the ✨ AI box and instruct: *"Add a numbered list of 3 setup steps and a bold heading 'Setup'."* Confirm the editor renders a real heading + a real numbered list (1. 2. 3.), not literal text.
- [ ] **Step 3:** Export that doc to `.docx`, open it (Word/LibreOffice/Google Docs), confirm the list is a real Word numbered list and the heading uses Heading style.
- [ ] **Step 4:** Run the whole docs test surface: `pytest tests/ -k "docs or docx or markdown_blocks" -v` — all green.

**Phase A is shippable here.** The reported "ugly formatting" bug is fixed on web + export.

---

# PHASE B — Native mobile Sheets editor

## Task B1: backend `POST /api/sheets/{id}/recalc`

**Files:**
- Modify: `lazyclaw/gateway/routes/sheets.py`
- Test: `tests/test_sheets_recalc_route.py` (new)

- [ ] **Step 1: Write the failing test.** (Mirror the existing route-test style — find a test that hits `/api/sheets` with the app's `TestClient`/auth fixture and reuse it.)

```python
# tests/test_sheets_recalc_route.py
# Uses the existing FastAPI test client + auth fixture pattern from the suite.
def test_recalc_returns_computed_values(client, auth_headers):
    # create a sheet, PUT a snapshot with A1=2, A2=3, A3==SUM(A1:A2)
    ...
    res = client.post(f"/api/sheets/{sid}/recalc", json={"payload": snap},
                      headers=auth_headers)
    assert res.status_code == 200
    out = res.json()["snapshot"]
    a3 = out["sheets"][sheet_id]["cellData"]["2"]["0"]["v"]
    assert a3 == 5
```

- [ ] **Step 2: Run — fails** (404, route missing).

Run: `pytest tests/test_sheets_recalc_route.py -v`

- [ ] **Step 3: Implement.** Add to `lazyclaw/gateway/routes/sheets.py`:

```python
from lazyclaw.sheets.recalc import recalc as _recalc_snapshot


class RecalcBody(BaseModel):
    payload: dict[str, Any]


@router.post("/{sheet_id}/recalc")
async def recalc_sheet_route(
    sheet_id: str,
    body: RecalcBody,
    user: User = Depends(get_current_user),
):
    """Recompute formulas server-side for a client-edited snapshot.

    The native mobile grid has no in-browser formula engine, so after a manual
    formula edit it posts the snapshot here; we run xlcalculator and return the
    snapshot with computed ``v`` values filled. Never 500s — unsupported
    formulas keep their prior value (see sheets/recalc.recalc)."""
    snapshot = _recalc_snapshot(body.payload)
    return {"ok": True, "snapshot": snapshot}
```

- [ ] **Step 4: Run — passes.**

Run: `pytest tests/test_sheets_recalc_route.py -v`

- [ ] **Step 5: Commit.**

```bash
git add lazyclaw/gateway/routes/sheets.py tests/test_sheets_recalc_route.py
git commit -m "feat(sheets): POST /api/sheets/{id}/recalc for native-client formula edits"
```

---

## Task B2: Dart full read/write `UniverSheet` model

Extend `univer_parse.dart` from a read-only `SheetGrid` into a mutable `UniverSheet` (cells with value + formula + style, multi-sheet, immutable copy-on-edit). **First fix the existing bug:** `_customRangeStart`/`_customRangeEnd` constants are empty strings (the ``/`` chars were lost) — restore them.

**Files:**
- Modify: `mobile/lib/screens/documents/univer_parse.dart`
- Test: `mobile/test/screens/documents/univer_sheet_test.dart` (new)

- [ ] **Step 1: Write the failing test.**

```dart
// mobile/test/screens/documents/univer_sheet_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

void main() {
  test('reads value, formula, multi-sheet', () {
    final wb = {
      'sheetOrder': ['s1', 's2'],
      'sheets': {
        's1': {'name': 'One', 'cellData': {'0': {'0': {'v': 10}, '1': {'f': '=A1*2', 'v': 20}}}},
        's2': {'name': 'Two', 'cellData': {}},
      },
    };
    final sheet = UniverSheet.fromWorkbook(wb);
    expect(sheet.sheetNames, ['One', 'Two']);
    expect(sheet.cellAt(0, 0).display, '10');
    expect(sheet.cellAt(0, 1).formula, '=A1*2');
  });

  test('setCell returns a new workbook (immutable) with the edit', () {
    final wb = {'sheetOrder': ['s1'], 'sheets': {'s1': {'name': 'One', 'cellData': {}}}};
    final sheet = UniverSheet.fromWorkbook(wb);
    final next = sheet.setCell(0, 0, value: '42');
    expect(next.cellAt(0, 0).display, '42');
    expect(sheet.cellAt(0, 0).display, ''); // original untouched
    expect(next.toWorkbook()['sheets']['s1']['cellData']['0']['0']['v'], anyOf('42', 42));
  });
}
```

- [ ] **Step 2: Run — fails.**

Run: `cd mobile && flutter test test/screens/documents/univer_sheet_test.dart`

- [ ] **Step 3: Implement.** Add to `univer_parse.dart`: restore the sentinel constants (`const String _customRangeStart = ''; const String _customRangeEnd = '';`), then add `UniverCell` (value/formula/style + `display`), `UniverSheet` with `fromWorkbook(map)`, `sheetNames`, `activeIndex`, `withActiveIndex(i)`, `cellAt(r,c)`, `setCell(r,c,{value,formula})` returning a NEW `UniverSheet` (deep-copy the workbook map, mirror `_apply_cell` from `sheets/snapshot.py`: formula wins, leading `=` enforced, value coerced), `usedBounds()`, and `toWorkbook()`. Keep `parseSheetGrid`/`SheetGrid` for any read-only callers.

- [ ] **Step 4: Run — passes.**

Run: `cd mobile && flutter test test/screens/documents/univer_sheet_test.dart`

- [ ] **Step 5: Commit.**

```bash
git add mobile/lib/screens/documents/univer_parse.dart mobile/test/screens/documents/univer_sheet_test.dart
git commit -m "feat(mobile): mutable UniverSheet model (value/formula/style, multi-sheet)"
```

---

## Task B3: repository `recalc()` + `save()`

**Files:**
- Modify: `mobile/lib/repositories/documents_repository.dart` (+ `DocumentsTransport` already has `postJson`/`putJson`? add `putJson` if absent)
- Test: `mobile/test/repositories/documents_repository_recalc_test.dart` (new)

- [ ] **Step 1: Write the failing test** — fake transport returns a recalced snapshot; assert `recalc()` posts to `/api/sheets/{id}/recalc` and returns the snapshot. **The fake must throw the production `DioException(error: ApiError)` shape on its error path** (per the sync-engine lesson).

- [ ] **Step 2: Run — fails.**

Run: `cd mobile && flutter test test/repositories/documents_repository_recalc_test.dart`

- [ ] **Step 3: Implement.** Add to `DocumentsRepository`:

```dart
/// Server-side formula recompute for a client-edited [snapshot].
Future<Map<String, dynamic>> recalc(String id, Map<String, dynamic> snapshot) async {
  final json = await _t.postJson('/api/sheets/$id/recalc', {'payload': snapshot});
  final snap = json['snapshot'];
  return snap is Map ? Map<String, dynamic>.from(snap) : snapshot;
}

/// Persist an edited sheet/doc snapshot. Maps PUT /api/<kind>/<id>.
Future<void> save(DocKind kind, String id, String name, Map<String, dynamic> payload) async {
  await _t.putJson('/api/${kind.api}/$id', {'name': name, 'payload': payload});
}
```

Add `putJson` to `DocumentsTransport` + `DioDocumentsTransport` (using `_client.put`) if not present.

- [ ] **Step 4: Run — passes.**

- [ ] **Step 5: Commit.**

```bash
git add mobile/lib/repositories/documents_repository.dart mobile/test/repositories/documents_repository_recalc_test.dart
git commit -m "feat(mobile): documents repo recalc() + save()"
```

---

## Task B4: formula catalog asset + helper filter

**Files:**
- Create: `mobile/assets/sheets/formula_catalog.json` (name + signature + 1-line help for the common functions)
- Modify: `mobile/pubspec.yaml` (add `assets: - assets/sheets/formula_catalog.json`)
- Create: `mobile/lib/screens/documents/formula_helper.dart` (load + filter logic)
- Test: `mobile/test/screens/documents/formula_helper_test.dart`

- [ ] **Step 1: Write the failing test.**

```dart
// filters by case-insensitive prefix after '='
final fns = [FormulaFn('SUM', 'SUM(range)', 'Adds numbers'),
             FormulaFn('AVERAGE', 'AVERAGE(range)', 'Mean'),
             FormulaFn('IF', 'IF(cond,a,b)', 'Branch')];
expect(filterFormulas(fns, '=su').map((f) => f.name), ['SUM']);
expect(filterFormulas(fns, '=SUM(a1,av').last.name, 'AVERAGE'); // helps inside args
expect(filterFormulas(fns, 'plain text'), isEmpty); // no '=' → no helper
```

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement.** `FormulaFn` model + `filterFormulas(List<FormulaFn>, String input)` (returns matches when the current token after `=`/`(`/`,` is a prefix of a function name; empty when no leading `=`). Seed `formula_catalog.json` with the functions xlcalculator reliably supports (SUM, AVERAGE, COUNT, COUNTA, MIN, MAX, IF, ROUND, ABS, SUMIF, COUNTIF, VLOOKUP, CONCATENATE, LEFT, RIGHT, MID, LEN, TODAY, NOW). Load via `rootBundle.loadString`.

- [ ] **Step 4: Run — passes.**

- [ ] **Step 5: Commit.**

```bash
git add mobile/assets/sheets/formula_catalog.json mobile/pubspec.yaml mobile/lib/screens/documents/formula_helper.dart mobile/test/screens/documents/formula_helper_test.dart
git commit -m "feat(mobile): formula helper catalog + prefix filter"
```

---

## Task B5: `sheet_editor_screen.dart` — editable grid widget

This is the UI integration task. Pure logic is already tested (B2/B4); here we assemble the widget. **Spike first:** evaluate `pluto_grid` (MIT) vs a custom `Table`/`CustomScrollView`. Decision rule: use `pluto_grid` only if it cleanly supports (a) tap-to-edit returning the raw string, (b) a custom overlay for the formula helper, and (c) rendering Univer cell styles; otherwise build a custom grid. Record the decision in a one-line comment at the top of the file.

**Files:**
- Create: `mobile/lib/screens/documents/sheet_editor_screen.dart`
- (If pluto chosen) Modify: `mobile/pubspec.yaml` (`pluto_grid`)
- Test: `mobile/test/screens/documents/sheet_editor_smoke_test.dart` (widget smoke test)

- [ ] **Step 1: Write the failing widget smoke test** — pump `SheetEditorScreen` with an injected fake repo + a 3×3 workbook; expect the grid shows `A`/`B`/`C` headers, the cell values, a formula bar, and a multi-sheet tab bar when >1 sheet.

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement the widget:**
  - Top: editable name + Save badge + ✨ AI button (reuse the existing `doc_ai_box.dart`).
  - Formula bar row: shows selected cell's formula/value; editing here commits to that cell.
  - Body: `InteractiveViewer` (pinch-zoom) wrapping the grid; **fit-to-width** by computing column width = `max(minCol, viewportWidth / visibleCols)`; **frozen** header row + header col; both-axis scroll for overflow; tap a cell → select (highlight) → tap again/edit icon → inline `TextField`; commit → `sheet.setCell(...)`, mark dirty.
  - If the committed value starts with `=`, show the **formula helper** sheet (filtered `FormulaFn` list) below the field; on commit call `repo.recalc(id, sheet.toWorkbook())` and apply returned values.
  - Formatting toolbar (bold/italic/align/number-format) updates the selected cell's `s` style (store in workbook `styles` per Univer; minimal: bold via a style id).
  - Multi-sheet `TabBar` driven by `sheet.sheetNames`; switching calls `sheet.withActiveIndex(i)`.
  - Debounced (800ms) `repo.save(DocKind.sheets, id, name, sheet.toWorkbook())`.

- [ ] **Step 4: Run — passes** (`flutter test` + `flutter analyze` clean).

- [ ] **Step 5: Commit.**

```bash
git add mobile/lib/screens/documents/sheet_editor_screen.dart mobile/test/screens/documents/sheet_editor_smoke_test.dart mobile/pubspec.yaml
git commit -m "feat(mobile): native editable Sheets grid (fit/zoom, formula bar+helper, multi-sheet)"
```

---

## Task B6: route Documents tab → Sheets editor

**Files:**
- Read first: `mobile/lib/screens/documents/documents_screen.dart`, `mobile/lib/screens/documents/sheet_viewer_screen.dart`
- Modify: `mobile/lib/screens/documents/documents_screen.dart` (open the editor instead of the read-only viewer for sheets)

- [ ] **Step 1:** Change the sheets tap-through to push `SheetEditorScreen` (keep `sheet_viewer_screen.dart` only if something else uses it; otherwise delete it and its test in a later cleanup).
- [ ] **Step 2:** `cd mobile && flutter analyze && flutter test` — green.
- [ ] **Step 3: Commit.**

```bash
git add mobile/lib/screens/documents/documents_screen.dart
git commit -m "feat(mobile): open native Sheets editor from Documents tab"
```

---

# PHASE C — Native mobile Docs editor

## Task C1: add `flutter_quill`

**Files:**
- Modify: `mobile/pubspec.yaml`

- [ ] **Step 1:** Add under dependencies (MIT — license-compliant per CLAUDE.md):

```yaml
  # Documents tab: native rich-text editor (MIT). Bold/italic/underline,
  # headings, bullet & numbered lists, links — round-tripped to the Univer
  # IDocumentData snapshot via univer_quill.dart.
  flutter_quill: ^10.8.5
```

- [ ] **Step 2:** `cd mobile && flutter pub get` — resolves cleanly (if a version conflict surfaces, pin to the latest that resolves and note it).
- [ ] **Step 3: Commit.**

```bash
git add mobile/pubspec.yaml mobile/pubspec.lock
git commit -m "build(mobile): add flutter_quill for native Docs editing"
```

---

## Task C2: `univer_quill.dart` — Univer IDocumentData ↔ Quill Delta

The crux of Phase C. Pure converter (no widgets) so it's fully unit-tested.

**Files:**
- Create: `mobile/lib/screens/documents/univer_quill.dart`
- Test: `mobile/test/screens/documents/univer_quill_test.dart`

- [ ] **Step 1: Write the failing round-trip test corpus.**

```dart
// For each fixture: univer → delta → univer preserves type + text + attributes.
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_quill.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart'; // shared consts

void main() {
  test('plain paragraph round-trips', () {
    final uni = univerDocFromBlocks([Block.paragraph('hello')]);
    final delta = deltaFromUniver(uni);
    final back = univerFromDelta(delta);
    expect(blocksOf(back).first.text, 'hello');
  });

  test('numbered list round-trips as ordered', () {
    final uni = univerDocFromBlocks([Block.number('a'), Block.number('b')]);
    final delta = deltaFromUniver(uni);
    expect(delta.toList().any((op) => op.attributes?['list'] == 'ordered'), isTrue);
    final back = univerFromDelta(delta);
    expect(blocksOf(back).map((b) => b.type), ['number', 'number']);
  });

  test('heading + bold + link round-trip', () {
    final uni = univerDocFromBlocks([
      Block.heading('Title', 1),
      Block.paragraph('see ', boldWord: 'this', link: 'https://x.io'),
    ]);
    final back = univerFromDelta(deltaFromUniver(uni));
    final b = blocksOf(back);
    expect(b.first.type, 'heading');
    // bold + link attributes survived
  });
}
```

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement** the converter. Map, in both directions, using the A0-verified Univer shapes and the same sentinel/`customRanges` hyperlink convention as `snapshot.py`:
  - Univer `paragraphStyle.namedStyleType` ↔ Quill block attribute `header: 1|2|3`.
  - Univer `bullet.listType` (ORDER_LIST/BULLET_LIST) ↔ Quill `list: ordered|bullet`.
  - Univer `textRuns[*].ts` (`bl`/`it`/`ul`) ↔ Quill inline attributes `bold`/`italic`/`underline`.
  - Univer hyperlink `customRanges` ↔ Quill `link` attribute.
  Provide `Block` helpers + `blocksOf` mirroring the Python `get_blocks` block dicts so the test corpus reads cleanly. Keep parsing tolerant (unknown → plain).

- [ ] **Step 4: Run — passes.**

- [ ] **Step 5: Commit.**

```bash
git add mobile/lib/screens/documents/univer_quill.dart mobile/test/screens/documents/univer_quill_test.dart
git commit -m "feat(mobile): Univer IDocumentData ↔ Quill Delta converter (round-trip)"
```

---

## Task C3: `doc_editor_screen.dart` — Quill rich-text editor

**Files:**
- Create: `mobile/lib/screens/documents/doc_editor_screen.dart`
- Test: `mobile/test/screens/documents/doc_editor_smoke_test.dart`

- [ ] **Step 1: Write the failing widget smoke test** — pump with a fake repo + a doc payload containing a heading + numbered list; expect the Quill toolbar + the rendered text present.

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement:** load payload → `univerFromDelta`? no — `deltaFromUniver(payload)` → `QuillController(document: Document.fromDelta(...))`. Render `QuillSimpleToolbar` (bold/italic/underline, H1/H2/H3, ordered/bullet list, link, undo/redo) + `QuillEditor`. On change (debounced 800ms): `univerFromDelta(controller.document.toDelta())` → `repo.save(DocKind.docs, id, name, snapshot)`. Top bar: editable name + Save badge + ✨ AI button (reuse `doc_ai_box.dart`; on AI result, rebuild the controller from the returned snapshot).

- [ ] **Step 4: Run — passes** (`flutter test` + `flutter analyze`).

- [ ] **Step 5: Commit.**

```bash
git add mobile/lib/screens/documents/doc_editor_screen.dart mobile/test/screens/documents/doc_editor_smoke_test.dart
git commit -m "feat(mobile): native rich-text Docs editor (Quill, lists/headings/links)"
```

---

## Task C4: route Documents tab → Docs editor

**Files:**
- Modify: `mobile/lib/screens/documents/documents_screen.dart`

- [ ] **Step 1:** Change the docs tap-through to push `DocEditorScreen` (replacing the read-only `doc_viewer_screen.dart`).
- [ ] **Step 2:** `cd mobile && flutter analyze && flutter test` — green.
- [ ] **Step 3: Commit.**

```bash
git add mobile/lib/screens/documents/documents_screen.dart
git commit -m "feat(mobile): open native Docs editor from Documents tab"
```

---

## Task C5: full verification + version bump (manual)

- [ ] **Step 1:** `cd mobile && flutter analyze && flutter test` — entire suite green.
- [ ] **Step 2:** Build + sideload to the Mi 15 device: `scripts/build-mobile-apk.sh`; open a sheet → edit a cell + a formula → confirm recalc; open a doc → bold/heading/numbered list → confirm save + reopen persists.
- [ ] **Step 3:** Bump `mobile/pubspec.yaml` version (e.g. `1.10.0+29`).
- [ ] **Step 4: Commit.**

```bash
git add mobile/pubspec.yaml
git commit -m "chore(mobile): bump version (native Sheets + Docs editors)"
```

---

## Self-Review Notes

- **Spec coverage:** ① snapshot enrichment → A1/A2; ② backend formatting → A3/A4/A5/A6; ③ mobile Sheets (recalc + grid + formula helper + multi-sheet + fit/zoom) → B1–B6; ④ mobile Docs (quill + converter) → C1–C4. PDF unchanged (non-goal) ✓. Online-first (non-goal: offline) ✓ — recalc/save are network calls, no SQLCipher.
- **Type consistency:** block dict shape `{type, level, runs}` and run shape `{text, bold?, italic?, underline?, url?}` are identical across `markdown_blocks.py`, `snapshot.build_body_with_blocks`/`get_blocks`, `ai_edit._normalize_blocks`, `docx_io`, and the Dart `Block`/converter. `build_body_with_blocks` / `get_blocks` / `append_blocks` names are consistent A2↔A3↔A4.
- **A0 dependency:** Tasks A2, A4, C2 depend on the field shapes captured in A0 — if A0 finds a different shape, adjust the literal in the test + the builder together.
- **Risk:** flutter_quill ↔ Univer fidelity (C2 round-trip corpus is the guard); grid library choice (B5 spike); exact Univer enum values (A0 gate before any builder).
