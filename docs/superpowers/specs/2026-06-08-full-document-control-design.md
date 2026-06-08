# Full Document Control — Web Formatting + Native Mobile Editors

**Date:** 2026-06-08
**Status:** Approved (design) → implementation
**Branch:** `feat/flutter-mobile`

## Problem

Two user-reported problems with the LazyClaw document suite (Sheets / Docs / PDF):

1. **The agent writes documents "in an ugly way."** When it wants a numbered list it
   emits literal text `"1. Step one"`; bullets become literal `"- item"`. These render
   as plain text in both the web Univer editor and the exported `.docx`. Root cause:
   `lazyclaw/docs/snapshot.py` deliberately discards all formatting — every paragraph is
   stored as `{"startIndex": n}` with `textRuns: []`, and `docs/ai_edit.py` only knows
   plain text + hyperlinks. There is **no** representation of headings, lists, bold, or
   italic anywhere in the authoring pipeline.

2. **Mobile shows "half the sheet and zero control."** `mobile/lib/screens/documents/`
   has read-only native viewers (no WebView). The sheet viewer uses fixed 104px cells,
   horizontal-scroll-only, renders only the first worksheet, and truncates cell text.
   There is no manual editing — only the ✨ AI box.

The web Sheets/Docs editors themselves are fine (full Univer presets with toolbar +
formula bar). The weakness is purely (a) the *agent's* output quality and (b) the
*mobile* editing experience.

## Goal

1. Agents produce **properly formatted** documents — real numbered/bulleted lists,
   headings, bold/italic — across web, mobile, and `.docx` export.
2. A **native Flutter Sheets editor** with full manual control: editable grid that fits
   the screen + pinch-zoom, frozen headers, multi-sheet tabs, a formatting toolbar, and a
   **formula helper** (function autocomplete). Plus the existing ✨ AI box.
3. A **native Flutter Docs editor** with full rich-text editing (bold/italic/underline,
   headings, bullet & numbered lists, links). Plus the existing ✨ AI box.
4. The **Univer snapshot is the single shared contract** — it round-trips losslessly
   across the web Univer editor, the backend agent, and the native Flutter editors.

## Non-Goals (v1)

- **PDF editing on mobile.** PDF stays view + manage + ✨ AI everywhere (no native PDF
  editing — consistent with web).
- **Offline document editing.** v1 is online-first, matching today's mobile-docs behavior.
  Formula recalculation *requires* the server (no JS/formula engine on the phone). Full
  offline doc sync (SQLCipher + outbox like Tasks/Notes/Budgets) is a future workstream.
- **Charts, conditional formatting, pivot tables, comments, real-time collaboration.**
- **Multi-agent fan-out for the in-editor AI.** The existing synchronous ✨ specialist
  stays; W1 is what makes its output well-formatted. Fan-out is a future enhancement.

## Architecture

Four components. **Component ①** is the load-bearing foundation that every other
component reads and writes through.

### ① Shared foundation — enrich the Univer snapshot model

**Files:** `lazyclaw/docs/snapshot.py` (extend), `lazyclaw/docs/markdown_blocks.py` (new),
mirrored in Dart by `mobile/lib/screens/documents/univer_parse.dart` (extend) and a new
`mobile/lib/screens/documents/univer_quill.dart`.

Univer's `IDocumentData` already supports everything we need (verified against
`web/node_modules/@univerjs/core`):

- `IParagraph.paragraphStyle: IParagraphStyle` — headings via `namedStyleType` /
  `headingId`.
- `IParagraph.bullet: IBullet` — `listType` is a `PresetListType`
  (`ORDER_LIST` / `ORDER_LIST_1..5`, `BULLET_LIST` / `BULLET_LIST_1..5`, `CHECK_LIST`),
  plus `nestingLevel`.
- `textRuns[].ts` (`IStyleBase`) — `bl` (bold), `it` (italic), `ul` (underline).

The change: stop discarding this. Paragraphs carry `paragraphStyle` + optional `bullet`;
text runs carry `bl`/`it`/`ul`. A new block-level markdown parser
(`docs/markdown_blocks.py`) maps:

| Markdown | Univer |
|----------|--------|
| `# / ## / ###` | paragraph with `namedStyleType` heading 1/2/3 |
| `- ` / `* ` | paragraph with `bullet.listType = BULLET_LIST` |
| `1. ` `2. ` … | paragraph with `bullet.listType = ORDER_LIST` |
| `**bold**` | run with `ts.bl = 1` |
| `*italic*` / `_italic_` | run with `ts.it = 1` |
| `[label](url)` | hyperlink customRange (existing path, reused) |

The exact Univer field names (`bl`/`it`/`ul`, `bullet`, `namedStyleType`) are verified
against the un-minified `@univerjs/core` source during implementation — the snapshot
builder and the Dart serializer must emit byte-identical shapes so Univer (web) renders
agent/mobile output and vice-versa.

### ② W1 — Backend rich formatting

- **`lazyclaw/docs/ai_edit.py`** — extend `PLAN_SHAPE` + system prompt so the doc
  specialist can emit structured blocks
  `{"type": "heading"|"paragraph"|"bullet"|"number", "level": n, "runs": [...]}`. The
  plain-markdown-string fallback is kept but is now actually parsed (via `markdown_blocks`)
  into real structures instead of stored verbatim.
- **`lazyclaw/skills/builtin/docs.py`** — `append_to_doc` / `set_doc_content` accept
  markdown or structured blocks (optional `style` / `list_type` params; markdown is the
  primary, lowest-friction path).
- **`lazyclaw/docs/docx_io.py`** — export real Word lists (`style="List Number"` /
  `"List Bullet"`), headings (`add_heading`), and bold/italic runs; import them back
  (`docx → snapshot`) so round-tripping survives.
- **`personality/SOUL.md`** — teach "numbered list for ordered sequences, bullets for
  unordered" (prompt-first lever per CLAUDE.md), now backed by real mechanics.

### ③ W2 — Native mobile Sheets editor

- **Backend:** new `POST /api/sheets/{id}/recalc` in `lazyclaw/gateway/routes/sheets.py`
  → runs the existing pure `recalc(snap)` from `sheets/recalc.py` → returns the recomputed
  snapshot. (Manual formula edits on the phone have no JS engine; the server recalculates.)
- **`mobile/lib/screens/documents/univer_parse.dart`** — extend from read-only into a full
  read/write `UniverSheet` model: cell value, formula, style (bold/align/number-format/bg),
  and **all worksheets** (not just the first).
- **`mobile/lib/screens/documents/sheet_editor_screen.dart`** (new) — editable grid:
  - **Fit-to-width by default + pinch-zoom** (`InteractiveViewer`), **frozen header
    row/col**, both-axis scroll, **multi-sheet tab bar**. This is the "half sheet" fix.
  - Tap cell → select; a formula bar at the top shows the value/formula; tap → inline
    `TextField` edit; commit updates the model.
  - Formatting toolbar: bold, italic, alignment, number format on the selected cell(s).
  - **Formula helper:** typing a leading `=` opens a live-filtered function list (name +
    signature + one-line help). Catalog = the functions `xlcalculator` supports, shipped as
    a shared JSON asset. Tap inserts the function skeleton.
  - On formula commit (and on save): call `/recalc`, apply returned values. Offline →
    show last known value + a "needs recompute" badge.
  - Debounced PUT save (mirrors web's 800ms pattern). ✨ AI box stays (already calls
    `/api/sheets/{id}/ai`).
- **Build-vs-reuse:** evaluate `pluto_grid` (MIT) as a grid accelerator during planning;
  a custom grid is likely required for formula-helper integration + Univer formatting
  fidelity, but `pluto_grid` may cover the base editable-grid mechanics.

### ④ W3 — Native mobile Docs editor

- Add **`flutter_quill`** (MIT) to `mobile/pubspec.yaml`.
- **`mobile/lib/screens/documents/univer_quill.dart`** (new) — bidirectional converter:
  Univer `IDocumentData` ↔ Quill `Delta`. Maps dataStream + paragraphs + `bullet` +
  `paragraphStyle` + `textRuns` + hyperlink customRanges ↔ Delta ops with attributes
  (bold / italic / underline / header / list:ordered|bullet / link).
- **`mobile/lib/screens/documents/doc_editor_screen.dart`** (new) — Quill editor +
  toolbar (bold/italic/underline, H1/H2/H3, bullet/number list, link, undo/redo). Save:
  Delta → Univer snapshot → debounced PUT. ✨ AI box stays (already calls
  `/api/docs/{id}/ai`).

## Data Flow

```
                    ┌──────────────────────────────────────┐
   manual edit ───► │  Dart model (UniverSheet / Quill)     │
                    └───────────────┬──────────────────────┘
                                    │ sheets: POST /recalc
                                    ▼
                    ┌──────────────────────────────────────┐
   web Univer ◄────►│   ENRICHED UNIVER SNAPSHOT (one fmt)  │◄──── ✨ agent (/ai)
                    └───────────────┬──────────────────────┘
                                    │ PUT save  /  export
                                    ▼
                         encrypted blob  /  .docx / .xlsx
```

One format, three producers (web Univer, native Flutter, backend agent), all in agreement.

## Error Handling

- **Dart parse boundary** validates snapshot shape → graceful read-only fallback with a
  message, never a crash.
- **`/recalc`** catches per-cell `xlcalculator` errors → `#ERROR` value, never a 500.
- **docx export** — unknown paragraph style → plain paragraph (graceful degradation).
- **Save** — last-write-wins by `updated_at` (existing pattern) + saved / failed badge.
- **Markdown parser** — malformed input → plain paragraph, never raises.

## Testing (target 80%+)

**Python:**
- `markdown_blocks` round-trip: md → snapshot → md.
- Snapshot carries headings / bullets / ordered lists / bold / italic.
- `docx_io` exports real lists + headings + bold (assert paragraph styles, not literal text).
- `POST /api/sheets/{id}/recalc` endpoint (success + per-cell error).
- `docs/ai_edit` plan with a numbered list produces real `bullet` paragraphs.

**Dart:**
- `univer_parse` read/write round-trip (value + formula + style + multi-sheet).
- **`univer_quill` round-trip corpus** (headings, both list types, links, mixed runs).
- Formula-helper filter logic.
- Grid edit/commit + recalc integration — fake transport throws the **production
  `DioException(error: ApiError)` shape** (per the sync-engine lesson: fakes that don't
  throw the production exception green-light data-loss bugs).

## Risks

| Risk | Mitigation |
|------|------------|
| `flutter_quill` ↔ Univer Delta fidelity (lists/links/headings) | Round-trip test corpus; lock the attribute mapping early |
| Native grid perf on large sheets | Virtualize rows/cols; cap initial render window |
| Exact Univer field names | Verify against un-minified `@univerjs/core` in plan phase before writing builders |
| `pluto_grid` vs custom grid | Spike both early; decide before building the formula helper on top |

## File Inventory

**Backend (Python):**
- `lazyclaw/docs/snapshot.py` (extend) — paragraph styles, bullets, styled runs
- `lazyclaw/docs/markdown_blocks.py` (new) — block-level markdown → Univer structures
- `lazyclaw/docs/ai_edit.py` (extend) — structured plan shape + prompt
- `lazyclaw/docs/docx_io.py` (extend) — real lists/headings/bold export + import
- `lazyclaw/skills/builtin/docs.py` (extend) — markdown/structured append/set
- `lazyclaw/gateway/routes/sheets.py` (extend) — `POST /{id}/recalc`
- `personality/SOUL.md` (extend) — list-formatting guidance

**Mobile (Dart):**
- `mobile/pubspec.yaml` (extend) — `flutter_quill` (+ maybe `pluto_grid`)
- `mobile/lib/screens/documents/univer_parse.dart` (extend) — full read/write sheet model
- `mobile/lib/screens/documents/univer_quill.dart` (new) — Univer ↔ Quill Delta
- `mobile/lib/screens/documents/sheet_editor_screen.dart` (new) — editable grid + formula helper
- `mobile/lib/screens/documents/doc_editor_screen.dart` (new) — Quill rich-text editor
- `mobile/lib/repositories/documents_repository.dart` (extend) — `recalc()` + `save()` calls
- `mobile/lib/screens/documents/documents_screen.dart` (extend) — route viewers → editors
