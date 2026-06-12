# Sheets Next-Level Pass — sync · links · tags · power editing

**Date:** 2026-06-12
**Branch:** `feat/sheets-next-level`
**Status:** Approved (user, 2026-06-12)

## Problem

User-reported issues with the Sheets feature, especially on the Flutter mobile app:

1. **Sync is unreliable** — mobile autosaves the whole workbook snapshot with no
   version check. A stale phone cache + one cell edit silently overwrites newer
   web/agent edits. Web has the same blind-write pattern. No refetch when
   returning to an open sheet.
2. **Hyperlinks don't show** — web mounts only `UniverSheetsCorePreset` (the
   hyperlink preset was never added; Docs got it, Sheets didn't). The mobile
   grid reads only `v`/`f`/`s` and ignores Univer's hyperlink resource.
3. **No link converter** — no way to turn bare URLs or `[text](url)` markdown
   into real clickable links.
4. **No tags** — documents (sheets/docs/pdf) can't be tagged or filtered.
5. **Manual control is minimal** — the mobile grid has no formatting, no
   multi-cell selection, no row/column operations, no copy/paste, no sort, no
   freeze, no undo.

Goal: web + mobile Sheets approach Google Sheets functionality, license-clean.

## User decisions (2026-06-12)

- Scope tier: "Everything Google has", decomposed into phases (charts/pivots
  deferred — Univer Pro is commercial and would poison the MIT license).
- Tags: **document-level** (not cell-level), chips + selector + filter, web +
  mobile, all three kinds (sheets/docs/pdf).
- Link converter: **auto + manual + bulk** (auto-convert on entry, insert-link
  dialog, convert-all action).
- Sync: **safe + fresh-on-focus** (optimistic concurrency + conflict banner +
  refetch on focus). No live co-editing.
- Mobile editor: **native grid upgrade** (no WebView Univer).

## Design

### 1. Sync safety + fresh-on-focus

**Server** (`sheets/store.py`, `docs/store.py`, routes):

- `PUT /api/{sheets,docs}/{id}` accepts optional `base_updated_at` (string,
  the `updated_at` the client loaded). If present and it does not match the
  row's current `updated_at` → **409** with body
  `{detail: "conflict", current: {id, name, payload, updated_at}}`.
- Requests without `base_updated_at` keep today's last-write-wins (agent
  skills and old clients unaffected).
- `name` becomes optional in the PUT body — when omitted, the stored name is
  kept (fixes mobile autosave re-sending a stale name forever and clobbering
  renames made elsewhere).
- Save/update responses include the new `updated_at` so clients can re-base.

**Mobile** (`sheet_editor_screen.dart`, `documents_repository.dart`):

- Repository `getPayload`/`save` carry `updated_at`; the editor tracks the
  base version and re-bases after every successful save/recalc/AI edit.
- On 409: banner "Sheet changed on the server" with **Reload** (replace local
  state with `current`) and **Keep mine** (re-PUT without `base_updated_at`).
- Refetch on screen focus/resume and after AI edits (route-aware revalidate).

**Web** (`Sheets.tsx`, `Docs.tsx` API layer):

- Send `base_updated_at` on autosave; on 409 show a confirm — reload via the
  existing `reloadToken` remount, or overwrite.

### 2. Hyperlinks + link converter

**Web:** mount `@univerjs/preset-sheets-hyper-link` (+ locale, Apache-2.0,
same `^0.24.0` family) in `Sheets.tsx`. Links render, click, and get Univer's
native insert/edit popover.

**Server** (`sheets/snapshot.py` + routes + skills):

- Univer sheet links live in the workbook `resources` array under
  `SHEET_HYPER_LINK_PLUGIN`: JSON `{[subUnitId]: [{id, row, column, payload}]}`
  where `payload` is the URL. Add pure helpers:
  - `get_sheet_links(payload) -> dict`
  - `set_cell_link(payload, sheet_id, row, col, url, display=None)` — upserts
    the resource entry and (when `display` given) the cell text.
  - `remove_cell_link(payload, sheet_id, row, col)`
  - `convert_urls_to_links(payload) -> (payload, count)` — scans all cells for
    bare URLs (`https?://…`, trailing punctuation trimmed) and markdown
    `[text](url)`; markdown cells get their text replaced by the display text
    and a link added; bare-URL cells keep the URL as text and gain a link.
    Already-linked cells are skipped.
- `POST /api/sheets/{id}/links/convert` → runs the converter, saves, returns
  `{ok, converted, snapshot, updated_at}`.
- Agent skills: new `convert_sheet_links(sheet_id)`; `set_cells` learns to
  auto-detect `[text](url)` values and write a real link.

**Mobile:**

- `univer_parse.dart`: parse + mutate the hyperlink resource
  (`UniverSheet.linkAt(row, col)`, `setLink`, `removeLink`,
  `convertUrlsToLinks` for the local auto-convert case); keep resource sheet
  ids in sync with the active sheet.
- Grid renders link cells accent-colored + underlined.
- Selecting a link cell shows a link chip under the formula bar with **Open**
  (url_launcher) / **Edit** / **Remove**.
- Toolbar insert-link dialog (display text + URL) writing into the selection.
- Committing a bare URL or `[text](url)` in the formula bar auto-converts.
- Overflow-menu action "Convert URLs to links" calls the server endpoint.
- New dep: `url_launcher`.

### 3. Document-level tags

**Server:** encrypted `tags` column (JSON list of plain strings, encrypted at
rest like `name`) on `sheets`, `docs`, `pdf_files`. Idempotent
`ALTER TABLE … ADD COLUMN` migration on startup (existing pattern). List
endpoints return decrypted `tags`; `PUT /api/{kind}/{id}` accepts `tags`;
PDFs (no generic PUT) get `PATCH /api/pdf/{id}` accepting `{name?, tags?}`.

**Web:** tag chips on rows in the Docs workspace lists; a tag filter bar
(chips of all tags in the list, multi-select AND filter); tag editor (add /
remove) on the open document header.

**Mobile:** tag chips on list cards; tag-selector bottom sheet (reuse the
tasks `chip_edit` pattern); horizontal filter chip row above the list.

### 4. Web — full free-preset surface

Mount remaining free Univer presets in `Sheets.tsx`: **filter, sort,
conditional formatting, data validation, find & replace, notes** (+ locales).
All Apache-2.0 `@univerjs/preset-sheets-*@^0.24.0`. The Univer chunk is
already lazy + excluded from PWA precache; growth only hits the Sheets tab.

License discipline: **no Univer Pro** (charts, pivot, collab, print) — they
are commercial and banned. Charts/pivots become a later, custom, MIT-clean
spec.

### 5. Mobile native grid power-editing

Each step independently shippable, in order:

1. **Selection model** — tap selects; drag the selection handle to extend to
   a range; tap row/column header selects the row/column; selection state is
   `(anchorRow, anchorCol, focusRow, focusCol)`.
2. **Formatting toolbar** — bold / italic / underline / strikethrough, text
   color + fill color palettes, horizontal alignment, number formats
   (auto, number, percent, currency, date), wrap toggle. Requires
   `univer_parse.dart` to learn the workbook `styles` registry: resolve a
   cell's effective style for rendering; on write, create-or-dedupe a style
   entry (hash of style dict → id) and point the cell's `s` at it. Render in
   the grid: weight/style/decoration/color/fill/alignment; number formats
   applied via a small formatter (`pattern` subset: `0`, `0.00`, `0%`,
   `#,##0.00`, currency symbol, `yyyy-mm-dd`).
3. **Row/col operations** — long-press a header → context menu: insert
   above/below (left/right), delete, clear, set column width. Workbook
   mutations shift `cellData`, styles references, links resource rows/cols,
   and `rowData`/`columnData` consistently.
4. **Copy/paste** — in-app range clipboard; system clipboard interop as TSV
   (paste from Google Sheets works; copy puts TSV out).
5. **Sort + freeze** — sort selected range or column asc/desc (values+links
   move together; server recalc after when formulas present); freeze first
   row/col toggle persisted to the worksheet `freeze` field, rendered as
   pinned header row/col.
6. **Undo/redo** — bounded local stack (last 50 `UniverSheet` snapshots).

### Error handling

- All new endpoints validate input shape and return structured errors; 409
  carries the full current snapshot so clients never need a second fetch.
- Mobile network failures keep current behavior (best-effort autosave,
  retry on next edit) but conflicts are never silently dropped.
- The converter never throws on malformed cells — skips and counts.

### Testing

- **Server (pytest):** 409 conflict paths (stale base, fresh base, missing
  base = LWW), name-optional PUT, tags CRUD + encryption round-trip, link
  helpers (set/remove/idempotence), converter edge cases (markdown, trailing
  punctuation, already-linked, non-string cells).
- **Mobile (flutter test):** pure-Dart tests for styles registry
  resolve/dedupe, link resource parse/mutate, row/col shift correctness,
  TSV serialize/parse, sort, number formatter; widget tests for selection +
  toolbar apply + conflict banner; repo tests with fake transports throwing
  production `DioException(error: ApiError)` shapes (sync-engine lesson).
- **Web:** `tsc` + `vite build` green; manual verify of presets.

### Out of scope (later specs)

Charts, pivot tables, live co-editing, mobile Docs editor parity, offline
*editing* of sheets (cache stays instant-paint/view when offline), cell-level
tags.

### Delivery

Bump `mobile/pubspec.yaml` version, `scripts/build-mobile-apk.sh`,
`make rebuild` for server+web, commit per conventional commits.
