# In-editor AI "Document Specialist" + hyperlink support

**Date:** 2026-06-04
**Branch:** feat/claude-agent-sdk
**Status:** Approved design → implementation

## Problem

The Documents workspace (Sheets / Docs / PDF) ships three Univer editors. The
agent can already edit them by natural language, but **only from the main chat**
(Telegram or the chat sidebar). There is no AI control *inside* the editors, so
a user who "doesn't know formulas" has to leave the editor, go to chat, and name
the document. Two concrete gaps:

1. **No in-editor AI box.** `web/src/pages/{Sheets,Docs,Pdf}.tsx` only expose the
   Univer canvas + name field + save/export buttons.
2. **"Text with a link" is unsupported anywhere.** `lazyclaw/docs/snapshot.py`
   stores plain text only — `set_text` resets `textRuns` to `[]`, there is no
   `customRanges` (Univer's hyperlink container), and `docx_io` drops links on
   both import and export. Even via chat the agent cannot produce a real
   clickable link today.

## Goals

- A small **✨ popover** AI box in every editor header (Sheets, Docs, PDF). The
  user types an instruction about the *currently open* document; it edits in
  place and the editor refreshes instantly.
- Full NL control routed **without** touching the fragile chat / background /
  consolidator path (which has documented Telegram-leak and stranded-promise
  failure modes).
- Real **hyperlink** support in Docs (insert "text with a link"), rendered in
  the editor and exported as a true `.docx` hyperlink.

## Non-goals

- Reflow text-editing of existing PDF prose (no permissive tool supports it).
  PDF AI drives the *manage* ops (sign / overlay / fill-form / merge / split /
  rotate / generate) and the viewer reloads the new bytes.
- Rich formatting beyond hyperlinks (bold/italic/headings) — out of scope here.

## Architecture

### 1. Frontend — one reusable popover

`web/src/components/DocAiPopover.tsx`: a ✨ button in the editor header that opens
a small floating box (textarea + Send + one-line status: *thinking… / ✓ summary /
error*). Reused across all three editors via props:

```ts
{ kind: "sheets" | "docs" | "pdf", docId, docName, onApplied(result) }
```

Per editor:

- **Sheets / Docs** — the `/ai` response carries the fresh Univer snapshot. The
  editor's load effect is refactored into a re-callable `loadSnapshot(snap)`; the
  popover calls the existing `flush()` to commit pending edits, **pauses
  autosave** (a ref flag), applies the returned snapshot, then resumes — so the
  agent edit and the user's keystrokes never clobber each other.
- **PDF** — PDF ops create a *new* file (immutable). The response returns
  `new_pdf_id`; the viewer refreshes the file list and selects it (or re-fetches
  `/api/pdf/{id}/raw?t=<ts>` when the op edits in place).

`web/src/api.ts`: `aiEditSheet(id, instruction)`, `aiEditDoc(...)`,
`aiEditPdf(...)`.

Docs editor mount adds `UniverDocsHyperLinkPreset` (the package is installed but
unused today) so hyperlink `customRanges` render as clickable links.

### 2. Backend — scoped synchronous endpoint + "Document Specialist"

New routes (synchronous request/response, **no** background task / Telegram /
consolidator):

```
POST /api/sheets/{id}/ai   {instruction}  -> {ok, summary, snapshot?, error?}
POST /api/docs/{id}/ai     {instruction}  -> {ok, summary, snapshot?, error?}
POST /api/pdf/{id}/ai      {instruction}  -> {ok, summary, new_pdf_id?, error?}
```

`lazyclaw/runtime/doc_specialist.py` orchestrates one focused turn:

1. Validate instruction (non-empty, ≤2000 chars).
2. Dispatch by `kind` to a per-kind strategy (`load → build_messages → apply`).
3. Call `eco_router.chat(messages, user_id, role="worker")` — **text only, no
   tools.** The model returns a strict-JSON **edit plan** (not a tool call, so it
   works across all ECO modes incl. local Gemma). On JSON parse failure, retry
   **once** via `role="brain"` (stronger model). Still bad → friendly error.
4. Validate the plan, apply it deterministically, persist, return a
   `SpecialistResult{ok, summary, snapshot?, new_id?, error?}`.

Rationale for **LLM → JSON plan → deterministic apply** over an LLM tool-calling
loop: reliable on any model (no native tool-use dependency), trivially testable
with a stub router, predictable, and the document content is preloaded so no
read-tool round-trip is needed.

### 3. Per-kind strategies (`{docs,sheets,pdf}/ai_edit.py`)

Each module is small and independently testable, exposing the same shape:

```python
async def load(config, user_id, doc_id) -> ctx | None
def build_messages(ctx, instruction) -> list[LLMMessage]   # system+user, embeds content + JSON schema
async def apply(config, user_id, doc_id, ctx, plan) -> ApplyResult
PLAN_SCHEMA: dict                                          # for validation + the prompt
```

**Sheets plan** — `{ "edits": [ {cell|row+col, value?, formula?} ] }`. Apply =
`sheets.snapshot.set_cells` → `sheets.recalc.recalc` → `store.save_sheet`. Returns
the fresh snapshot.

**Docs plan** — `{ "mode": "append"|"replace", "paragraphs": [ {runs:[{text, url?}]} ] }`.
A run with `url` becomes a hyperlink. Apply builds the body via the new
`snapshot.build_body_with_runs` (append to existing or replace), preserving links,
then `store.save_doc`. Returns the fresh snapshot.

**PDF plan** — `{ "op": "add_text"|"fill_form"|"merge"|"split"|"rotate"|"generate", ...args }`
mapping to `pdf.ops` + `store.save_pdf`. Returns `new_pdf_id`.

### 4. Hyperlink data model (`lazyclaw/docs/snapshot.py`)

Univer hyperlink representation (verified against installed `@univerjs/core@0.24`):

- `dataStream` brackets link text with sentinel tokens
  `CUSTOM_RANGE_START = ""` … `CUSTOM_RANGE_END = ""`.
- `body.customRanges: [{ startIndex: <idx of >, endIndex: <idx of >,
  rangeId, rangeType: 0 /*HYPERLINK*/, properties: { url } }]`.
- `paragraphs[].startIndex` still points at each paragraph's `\r`, counting the
  sentinel chars.

Changes:

- `build_body_with_runs(paragraphs_of_runs)` — builds `dataStream` + `paragraphs`
  + `customRanges` from a list of paragraphs, each a list of `{text, url?}` runs.
- `get_paragraphs` strips the ``/`` sentinels from returned visible
  text (so `read_doc`, previews, and `set_text` stay clean).
- `set_text` preserves any existing `customRanges` only when the rebuilt text is
  unchanged length-wise is **not** attempted — a plain-text rewrite still drops
  links (documented), but `build_body_with_runs` is the link-preserving path.
- `get_paragraph_runs(snap)` — returns, per paragraph, ordered runs `{text, url?}`
  by intersecting `customRanges` with the paragraph span (used by docx export).
- `append_paragraph_with_runs(snap, runs)` — convenience for the append path.

### 5. docx round-trip (`lazyclaw/docs/docx_io.py`)

- Export: walk `get_paragraph_runs`; emit a real `<w:hyperlink>` (oxml + a
  relationship) for runs with a `url`, plain runs otherwise.
- Import: read `<w:hyperlink r:id>` runs, resolve the rel target, and rebuild
  `customRanges` so a docx with links round-trips.

### 6. Chat parity (`append_to_doc` skill)

Extend `AppendToDocSkill` with optional `link_text` + `link_url`, and
auto-convert inline markdown `[text](url)` in `text` → a real link, so the same
capability works from chat, not just the popover.

## Error handling

- Endpoints never 500 on a bad instruction — the specialist catches apply/parse
  errors and returns `{ok:false, error}`; the popover shows it inline.
- Instruction validated (non-empty, length cap); ~60 s ceiling.
- All edits remain `user_id`-scoped (load/save go through the existing scoped
  stores).

## Testing

- `tests/docs/test_snapshot.py` — hyperlink build/read round-trip, sentinel
  index correctness, `get_paragraph_runs` spans, `set_text` still clean.
- `tests/docs/test_docx_io.py` — docx hyperlink export + import round-trip.
- `tests/docs/test_skills.py` — `append_to_doc` link params + markdown convert.
- `tests/{docs,sheets,pdf}/test_ai_edit.py` — apply each plan kind from fixtures.
- `tests/runtime/test_doc_specialist.py` — stub router → plan → apply; parse
  failure → brain retry → friendly error; instruction validation.
- `tests/{sheets,docs,pdf}/test_routes.py` — `/ai` happy path (stub specialist),
  auth, cross-user scoping, error shape.
- Target 80%+ on new Python; `npm run build` green.

## File inventory

New: `docs/ai_edit.py`, `sheets/ai_edit.py`, `pdf/ai_edit.py`,
`runtime/doc_specialist.py`, `web/src/components/DocAiPopover.tsx`, the test
files above.
Edited: `docs/snapshot.py`, `docs/docx_io.py`, `skills/builtin/docs.py`,
`gateway/routes/{sheets,docs,pdf}.py`, `web/src/api.ts`,
`web/src/pages/{Sheets,Docs,Pdf}.tsx`, docs (DOCS.md, CLAUDE.md, README, TODO).
