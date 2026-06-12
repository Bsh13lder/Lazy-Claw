# Sheets Next-Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix sheet sync (optimistic concurrency + fresh-on-focus), add hyperlinks + link converter everywhere, document-level tags, mount the full free Univer preset surface on web, and upgrade the mobile native grid to power-editing (selection, formatting, row/col ops, copy/paste, sort, freeze, undo).

**Architecture:** Server stays the single source of truth (one encrypted Univer snapshot per sheet). Concurrency = compare-and-swap on `updated_at`. Hyperlinks ride Univer's `SHEET_HYPER_LINK_PLUGIN` workbook resource (pure-dict helpers server-side, mirrored in Dart). Tags = plaintext JSON column beside `name` (matches the existing table design — `name` is plaintext for listing). Mobile editing stays native Flutter; all workbook mutations live in `univer_parse.dart` as immutable-copy methods, UI is thin.

**Tech Stack:** FastAPI + aiosqlite (server), React 19 + `@univerjs/preset-sheets-*@^0.24.0` (web), Flutter + Riverpod + Dio (mobile), pytest + flutter_test.

**Spec:** `docs/superpowers/specs/2026-06-12-sheets-next-level-design.md`
**Spec amendment (2026-06-12):** tags are stored PLAINTEXT (JSON list), not encrypted — the sheets/docs/pdf tables already store `name` plaintext for index listing; tags are the same sensitivity class and need the same query path. Everything else unchanged.

---

## Conventions the engineer must know

- Run server tests: `python -m pytest tests/path/test_x.py -x -q 2>&1 | tail -20` — **NEVER pipe to `tail` via `| tail` alone without redirect on this repo? No — the known gotcha is pytest hanging at exit when piped; write to a file if you see a hang: `pytest ... > /tmp/out.txt 2>&1; tail -30 /tmp/out.txt`** (MEMORY lesson 2026-06-10).
- Mobile tests: `cd mobile && flutter test test/<file>.dart 2>&1 | tail -20`.
- Web check: `cd web && npx tsc --noEmit && npm run build 2>&1 | tail -5`.
- Migrations: idempotent `(table, column, ALTER...)` tuples in `lazyclaw/db/connection.py:~40`.
- Immutability: every workbook mutation returns a NEW dict/object (`copy.deepcopy` server, `_deepCopyMap` Dart).
- Commit per task, conventional commits, **no AI attribution**.

---

### Task 1: Server store — tags column + conflict-aware, name-optional save

**Files:**
- Modify: `lazyclaw/db/schema.sql` (sheets/docs/pdf_files tables: add `tags TEXT DEFAULT '[]'`)
- Modify: `lazyclaw/db/connection.py:40` (3 migration tuples)
- Modify: `lazyclaw/sheets/store.py`, `lazyclaw/docs/store.py`, `lazyclaw/pdf/store.py`
- Test: `tests/sheets/test_store_conflict_tags.py` (new)

- [ ] **Step 1: Write failing tests** — `tests/sheets/test_store_conflict_tags.py` with cases:
  - `save_sheet(..., base_updated_at=<stale>)` raises `SheetConflictError` carrying `.current` (the fresh row incl. payload).
  - `save_sheet(..., base_updated_at=<current updated_at>)` succeeds and returns new `updated_at`.
  - `save_sheet` without `base_updated_at` keeps last-write-wins.
  - `save_sheet(name=None)` preserves the stored name.
  - `save_sheet(tags=["a","b"])` round-trips; `list_sheets` returns `tags`; invalid tags (non-list, >32 tags, tag >40 chars) are cleaned.
  Use the existing test fixtures pattern from `tests/sheets/` (look at any existing store test for the config/user fixture; if none, follow `tests/` conftest).

- [ ] **Step 2: Run, verify FAIL** (`ImportError: cannot import name 'SheetConflictError'`).

- [ ] **Step 3: Implement.** In `sheets/store.py`:

```python
class SheetConflictError(Exception):
    """Raised when base_updated_at doesn't match the stored row (CAS failure)."""
    def __init__(self, current: dict[str, Any]):
        super().__init__("sheet was modified by another client")
        self.current = current


_TAGS_MAX = 32
_TAG_LEN_MAX = 40


def _clean_tags(tags: Any) -> list[str]:
    """Validate + normalize a tags payload to a bounded list of short strings."""
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    for t in tags:
        s = str(t).strip()[:_TAG_LEN_MAX]
        if s and s not in out:
            out.append(s)
        if len(out) >= _TAGS_MAX:
            break
    return out
```

`save_sheet` new signature (name optional, CAS, tags):

```python
async def save_sheet(
    config: Config,
    user_id: str,
    name: str | None,
    payload: dict[str, Any],
    sheet_id: str | None = None,
    *,
    base_updated_at: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
```

Update path becomes: read current row (`SELECT name, tags, updated_at`); if missing → insert (as today). If `base_updated_at` is not None and != stored `updated_at` → fetch full current sheet via `get_sheet` and `raise SheetConflictError(current)`. Effective name = `_clean_name(name)` when `name` is not None else stored name; effective tags = `json.dumps(_clean_tags(tags))` when `tags is not None` else stored. Single `UPDATE ... SET name=?, tags=?, payload=?, updated_at=?`. Return row dict including `tags` (parsed list) + new `updated_at`. `list_sheets`/`get_sheet` SELECT and return `tags` (json.loads with `or "[]"` fallback). Mirror the same in `docs/store.py` (`DocConflictError`) — same shape. `pdf/store.py`: add `tags` to list/get + new `update_pdf_meta(config, user_id, pdf_id, name=None, tags=None)` (no payload write, no CAS needed — PDFs are immutable blobs).

Schema: add `tags TEXT DEFAULT '[]'` to the three CREATE TABLEs; migrations:

```python
("sheets", "tags", "ALTER TABLE sheets ADD COLUMN tags TEXT DEFAULT '[]'"),
("docs", "tags", "ALTER TABLE docs ADD COLUMN tags TEXT DEFAULT '[]'"),
("pdf_files", "tags", "ALTER TABLE pdf_files ADD COLUMN tags TEXT DEFAULT '[]'"),
```

- [ ] **Step 4: Run tests → PASS.**
- [ ] **Step 5: Commit** `feat(sheets): conflict-aware name-optional save + document tags in stores`

### Task 2: Routes — 409 conflict, tags, PDF PATCH

**Files:**
- Modify: `lazyclaw/gateway/routes/sheets.py:52-108`, `lazyclaw/gateway/routes/docs.py` (same shape), `lazyclaw/gateway/routes/pdf.py`
- Test: `tests/gateway/test_documents_conflict_tags.py` (new; follow auth-mock pattern from `tests/gateway/test_mobile_settings.py`)

- [ ] **Step 1: Failing tests:** PUT with stale `base_updated_at` → 409, body `{"detail": "conflict", "current": {...}}`; PUT without name keeps name; PUT with `tags` persists; `PATCH /api/pdf/{id}` sets name/tags; list endpoints include `tags`.
- [ ] **Step 2: Verify FAIL.**
- [ ] **Step 3: Implement.** `SaveSheetBody` becomes:

```python
class SaveSheetBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    payload: dict[str, Any]
    base_updated_at: str | None = None
    tags: list[str] | None = None
```

```python
@router.put("/{sheet_id}")
async def save_sheet_route(sheet_id, body, user=Depends(get_current_user)):
    try:
        row = await save_sheet(
            _config, user.id, body.name, body.payload, sheet_id=sheet_id,
            base_updated_at=body.base_updated_at, tags=body.tags,
        )
    except SheetConflictError as exc:
        return JSONResponse(status_code=409,
            content={"detail": "conflict", "current": exc.current})
    return {"sheet": row}
```

Same for docs. PDF: `class PatchPdfBody(BaseModel): name: str | None = None; tags: list[str] | None = None` + `@router.patch("/{pdf_id}")` → `update_pdf_meta`, 404 when missing.

- [ ] **Step 4: Run → PASS.**  - [ ] **Step 5: Commit** `feat(gateway): 409 conflict + tags on sheets/docs, PDF meta PATCH`

### Task 3: `sheets/snapshot.py` — hyperlink helpers + URL/markdown converter

**Files:**
- Modify: `lazyclaw/sheets/snapshot.py`
- Test: `tests/sheets/test_snapshot_links.py` (new)

Univer stores sheet links in the workbook-level `resources` array:

```json
{"resources": [{"name": "SHEET_HYPER_LINK_PLUGIN",
  "data": "{\"<subUnitId>\": [{\"id\": \"l1\", \"row\": 0, \"column\": 1, \"payload\": \"https://x.com\"}]}"}]}
```

(`data` is a JSON *string*; `payload` is the URL; `row`/`column` 0-based; verified against `@univerjs/sheets-hyper-link@0.24` source.)

- [ ] **Step 1: Failing tests** covering: `get_sheet_links` on missing resource → `{}`; `set_cell_link` creates resource + entry and (with `display`) sets cell text; upsert replaces an existing entry at same row/col; `remove_cell_link`; `convert_urls_to_links`:
  - bare `https://example.com` cell → link added, text kept, count 1
  - `See [Docs](https://d.io) here` → text becomes `See Docs here`?? **No** — per spec, a markdown cell's whole value `[Docs](https://d.io)` → text `Docs` + link; mixed-text markdown is converted only when the cell is EXACTLY one markdown link (keep it deterministic)
  - trailing punctuation `https://x.com.` → link `https://x.com`, text untouched
  - already-linked cell skipped; numeric/formula cells skipped; idempotent (running twice → count 0 second time)
- [ ] **Step 2: FAIL.**  - [ ] **Step 3: Implement** (all immutable, deepcopy-out):

```python
_LINK_RESOURCE = "SHEET_HYPER_LINK_PLUGIN"
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_MD_LINK_RE = re.compile(r"^\[([^\]]+)\]\((https?://[^\s)]+)\)$")
_TRAIL_PUNCT = ".,;:!?)"


def _link_data(snap: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    for res in snap.get("resources") or []:
        if res.get("name") == _LINK_RESOURCE:
            try:
                return json.loads(res.get("data") or "{}")
            except json.JSONDecodeError:
                return {}
    return {}


def get_sheet_links(snap, sheet=0) -> list[dict[str, Any]]:
    sid = _resolve_sheet_id(snap, sheet)
    return _link_data(snap).get(sid, [])


def _write_link_data(snap, data) -> None:  # caller owns the copy
    blob = json.dumps(data)
    resources = snap.setdefault("resources", [])
    for res in resources:
        if res.get("name") == _LINK_RESOURCE:
            res["data"] = blob
            return
    resources.append({"name": _LINK_RESOURCE, "data": blob})


def set_cell_link(snap, row, col, url, display=None, sheet=0) -> dict[str, Any]:
    out = copy.deepcopy(snap)
    sid = _resolve_sheet_id(out, sheet)
    data = _link_data(out)
    entries = [e for e in data.get(sid, [])
               if not (e.get("row") == row and e.get("column") == col)]
    entries.append({"id": f"l-{uuid4().hex[:8]}", "row": row, "column": col,
                    "payload": url})
    data[sid] = entries
    _write_link_data(out, data)
    if display is not None:
        _apply_cell(out["sheets"][sid], row, col, display, None, clear=False)
    return out


def remove_cell_link(snap, row, col, sheet=0) -> dict[str, Any]:
    out = copy.deepcopy(snap)
    sid = _resolve_sheet_id(out, sheet)
    data = _link_data(out)
    data[sid] = [e for e in data.get(sid, [])
                 if not (e.get("row") == row and e.get("column") == col)]
    _write_link_data(out, data)
    return out


def convert_urls_to_links(snap) -> tuple[dict[str, Any], int]:
    """Scan ALL sheets; returns (new_snap, converted_count)."""
    out = copy.deepcopy(snap)
    count = 0
    for sid in out.get("sheetOrder") or list((out.get("sheets") or {}).keys()):
        data = _link_data(out)
        linked = {(e.get("row"), e.get("column")) for e in data.get(sid, [])}
        for r, c, cell in list(iter_cells(out, sid)):
            v = cell.get("v")
            if cell.get("f") or not isinstance(v, str) or (r, c) in linked:
                continue
            md = _MD_LINK_RE.match(v.strip())
            if md:
                out = set_cell_link(out, r, c, md.group(2),
                                    display=md.group(1), sheet=sid)
                count += 1
                continue
            m = _URL_RE.search(v)
            if m and m.group(0).rstrip(_TRAIL_PUNCT) == v.strip().rstrip(_TRAIL_PUNCT):
                out = set_cell_link(out, r, c, m.group(0).rstrip(_TRAIL_PUNCT),
                                    sheet=sid)
                count += 1
    return out, count
```

(Add `import json` to snapshot.py imports.)

- [ ] **Step 4: PASS.**  - [ ] **Step 5: Commit** `feat(sheets): hyperlink resource helpers + URL/markdown link converter`

### Task 4: Convert endpoint + agent skills

**Files:**
- Modify: `lazyclaw/gateway/routes/sheets.py` (new route), `lazyclaw/skills/builtin/sheets.py` (find via `grep -rn "set_cells" lazyclaw/skills/builtin/`)
- Test: extend `tests/gateway/test_documents_conflict_tags.py` + `tests/sheets/test_snapshot_links.py`

- [ ] **Step 1: Failing tests:** `POST /api/sheets/{id}/links/convert` → `{ok, converted, snapshot, updated_at}` and persists; agent `set_cells` with value `[Site](https://s.io)` writes display text + link.
- [ ] **Step 2: FAIL.**  - [ ] **Step 3: Implement** route:

```python
@router.post("/{sheet_id}/links/convert")
async def convert_links_route(sheet_id: str, user: User = Depends(get_current_user)):
    sheet = await get_sheet(_config, user.id, sheet_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Sheet not found")
    snap, converted = convert_urls_to_links(sheet["payload"])
    row = await save_sheet(_config, user.id, None, snap, sheet_id=sheet_id)
    return {"ok": True, "converted": converted, "snapshot": snap,
            "updated_at": row["updated_at"]}
```

Skills: in the builtin sheets skill module add `convert_sheet_links(sheet_id)` skill (registry pattern — copy the decorator/registration shape of the adjacent `set_cells` skill) and, inside the existing set-cells execution path, detect `_MD_LINK_RE` string values → route through `set_cell_link(..., display=...)` instead of plain `set_cell`.

- [ ] **Step 4: PASS. Run the full server suite for the touched areas:** `python -m pytest tests/sheets tests/gateway -x -q`.
- [ ] **Step 5: Commit** `feat(sheets): links/convert endpoint + convert_sheet_links agent skill`

### Task 5: Web — presets, conflict handling, tags UI

**Files:**
- Modify: `web/package.json` (add `@univerjs/preset-sheets-hyper-link`, `-filter`, `-sort`, `-conditional-formatting`, `-data-validation`, `-find-replace`, `-note`, all `^0.24.0`)
- Modify: `web/src/pages/Sheets.tsx`, `web/src/pages/Docs.tsx`, `web/src/api.ts:1946-2130`
- Verify: `cd web && npm install && npx tsc --noEmit && npm run build`

- [ ] **Step 1:** `npm install` the 7 presets.
- [ ] **Step 2:** In `Sheets.tsx` import each preset + its en-US locale + its `lib/index.css`, add to `mergeLocales(...)` and `presets: [UniverSheetsCorePreset({container}), UniverSheetsHyperLinkPreset(), UniverSheetsFilterPreset(), UniverSheetsSortPreset(), UniverSheetsConditionalFormattingPreset(), UniverSheetsDataValidationPreset(), UniverSheetsFindReplacePreset(), UniverSheetsNotePreset()]` (exact export names: check each package's `README`/`d.ts` after install — they follow `UniverSheets<Thing>Preset`).
- [ ] **Step 3:** `api.ts`: `SheetMeta` gains `tags?: string[]; updated_at?: string`; `saveSheet(id, name, payload, baseUpdatedAt?, tags?)` posts `{name, payload, base_updated_at, tags}`; add `convertSheetLinks(id)`; same updates for docs + `patchPdf(id, {name?, tags?})`.
- [ ] **Step 4:** `Sheets.tsx` flush: keep `updatedAtRef`; pass it as `base_updated_at`; on response re-base; on a 409 (`request` throws — inspect error payload) show `window.confirm("This sheet changed on the server. Reload the latest version? (Cancel keeps yours and overwrites.)")` → reload = bump `reloadToken`; cancel = re-save without base. Same in `Docs.tsx`.
- [ ] **Step 5:** Tags UI: in the Sheets/Docs/Documents list rows render `tags` as small chips; a filter bar above the list with every distinct tag (click = toggle filter, AND semantics); on the open document header a "🏷" button → prompt-style popover (input + existing chips with ×) that PUTs tags. Keep styling consistent with existing Tailwind classes in those files. Add a "Convert URLs to links" item next to the export controls calling `convertSheetLinks` then bumping `reloadToken`.
- [ ] **Step 6:** `npx tsc --noEmit && npm run build` → green.
- [ ] **Step 7: Commit** `feat(web): full free Univer preset surface, sheet conflict re-base, document tags`

### Task 6: Mobile model — styles registry, links, structure ops (pure Dart)

**Files:**
- Modify: `mobile/lib/screens/documents/univer_parse.dart` (split: keep parse there, create `mobile/lib/screens/documents/univer_model.dart` if file would exceed ~800 lines)
- Test: `mobile/test/univer_model_test.dart` (new)

- [ ] **Step 1: Failing tests** for every API below (construct workbooks as inline maps).
- [ ] **Step 2-3: Implement on `UniverSheet`** (all return NEW instances; `_wb` deep-copied):
  - **Styles:** `CellStyleView resolveStyle(int row, int col)` reading cell `s` (string id → workbook `styles[id]`, or inline map). `CellStyleView` fields: `bool bold, italic, underline, strike; String? color; String? bgColor; int hAlign (0 auto/1 left/2 center/3 right); bool wrap; String? numFmt`. Univer mapping: `bl:1`→bold, `it:1`→italic, `ul:{s:1}`→underline, `st:{s:1}`→strike, `cl:{rgb:"#rrggbb"}`→color, `bg:{rgb}`→bgColor, `ht`→hAlign, `tb:3`→wrap, `n:{pattern}`→numFmt.
  - `UniverSheet applyStyle(SelRange range, Map<String, dynamic> patch)` — for each cell in range, merge patch into the cell's effective style dict, hash result (`jsonEncode` of sorted keys), reuse an existing identical entry in `styles` or insert `"s-<n>"`, point cell `s` at it. Toggling: caller passes explicit values (`{"bl": 1}` or `{"bl": 0}`).
  - **Number format:** `String formatNumber(dynamic v, String? pattern)` top-level fn supporting `0`, `0.00`, `0%`, `#,##0.00`, `"$"#,##0.00`, `yyyy-mm-dd` (value = excel serial or ISO string passthrough). Anything else → `v.toString()`.
  - **Links:** `String? linkAt(int row, int col)`, `setLink(row, col, url, {String? display})`, `removeLink(row, col)`, `(UniverSheet, int) convertUrlsToLinks()` — mirror Task 3 semantics exactly (same regexes), resource key = ACTIVE sheet id.
  - **Structure:** `insertRow(int at)`, `deleteRow(int at)`, `insertCol(int at)`, `deleteCol(int at)` — shift `cellData` keys, link-resource `row`/`column`, and adjust `rowCount`/`columnCount`. (Formula references are NOT rewritten — document this; server recalc handles stale refs as Univer does.) `setColWidth(int col, double w)` → `columnData[col] = {"w": w}`.
  - **Sort:** `sortRange(SelRange range, int byCol, {required bool asc, bool hasHeader = false})` — reorder rows of the range (numbers before strings, case-insensitive), moving each row's cells + links together.
  - **Freeze:** `toggleFreeze()` → worksheet `freeze` = `{"xSplit": 1, "ySplit": 1, "startRow": 1, "startColumn": 1}` or removed; `bool get frozen`.
  - **TSV:** `String rangeToTsv(SelRange)` and `UniverSheet pasteTsv(int row, int col, String tsv)` (splits `\n`/`\t`, coerces numerics).
  - `SelRange` class: `{int r1, c1, r2, c2}` normalized (`r1<=r2`), `contains(r,c)`, `Iterable<(int,int)> cells`.
- [ ] **Step 4:** `flutter test test/univer_model_test.dart` → PASS.
- [ ] **Step 5: Commit** `feat(mobile): univer workbook model — styles, links, row/col ops, sort, freeze, TSV`

### Task 7: Mobile repo/provider — conflict, tags, links, updatedAt

**Files:**
- Modify: `mobile/lib/repositories/documents_repository.dart`
- Test: `mobile/test/documents_repository_test.dart` (extend existing if present, else new with `FakeDocumentsTransport`)

- [ ] **Steps (TDD):**
  - `DocPayload` + `DocMeta` gain `updatedAt` / `tags` (already has updatedAt on meta; add `tags: List<String>` default `[]`; payload gains `updatedAt`).
  - `save(...)` gains `{String? baseUpdatedAt, List<String>? tags, String? name}` (name now optional named param), returns the new `updated_at` string. On HTTP 409, throw `DocConflictException(current: DocPayload)` parsed from `error.response.data["current"]`. **The fake transport must throw the production shape: `DioException(response: Response(statusCode: 409, data: {...}))`** (sync-engine lesson).
  - New: `convertLinks(String id)` → POST, returns `(int converted, Map snapshot, String updatedAt)`; `setTags(DocKind, id, List<String>)` (PUT for sheets/docs, PATCH for pdf).
- [ ] **Run tests → PASS.**
- [ ] **Commit** `feat(mobile): documents repo — 409 conflict surface, tags, link converter`

### Task 8: Mobile editor — selection model + formatting toolbar

**Files:**
- Modify: `mobile/lib/screens/documents/sheet_editor_screen.dart`
- Create: `mobile/lib/screens/documents/sheet_toolbar.dart`, `mobile/lib/screens/documents/sheet_selection.dart`
- Test: `mobile/test/sheet_selection_test.dart`

- [ ] **Selection (`sheet_selection.dart`):** `SheetSelection` immutable state `{int anchorRow, anchorCol, focusRow, focusCol}` with `SelRange get range`; tap cell = collapse to cell; drag on the grid extends focus (GestureDetector `onPanUpdate` translating local position → row/col via known row height/col width — disable InteractiveViewer pan while a drag started ON the selection handle, a small circle drawn at the range's bottom-right corner); tapping a column header letter selects the column (rows 0..maxRow), row number selects the row. Unit-test the pure hit-math (`cellFromOffset(Offset, colW, rowH)`).
- [ ] **Toolbar (`sheet_toolbar.dart`):** horizontal scrollable row pinned above the formula bar using `Lz*` kit + `AppColors` tokens only: **B / I / U / S** toggles, text-color + fill-color (popup with a fixed 10-swatch palette from `AppColors` + none), align L/C/R cycle, wrap toggle, number-format menu (Auto, Number, 2-decimals, Percent, Currency, Date), insert-link, undo, redo. Each button calls `widget.onAction(SheetAction.bold)` etc.; active state derives from `resolveStyle` of the selection anchor.
- [ ] **Wire into `sheet_editor_screen.dart`:** replace `_selRow/_selCol` with `SheetSelection? _sel`; `_commit`/formula bar use the anchor cell; toolbar actions map to `_sheet.applyStyle(_sel.range, patch)` + `_scheduleSave()`; grid cells render via `resolveStyle` (fontWeight/fontStyle/decoration/color/fill/alignment + `formatNumber(v, numFmt)`); undo/redo = `List<UniverSheet> _undoStack/_redoStack` (cap 50, push before every mutation).
- [ ] **Tests:** selection hit-math + a widget test that taps Bold and asserts the committed workbook style. `flutter test` green, `flutter analyze` clean.
- [ ] **Commit** `feat(mobile): sheet range selection + formatting toolbar + undo/redo`

### Task 9: Mobile editor — links UI + auto-convert

**Files:**
- Modify: `mobile/pubspec.yaml` (add `url_launcher: ^6.3.0`), `sheet_editor_screen.dart`, `sheet_toolbar.dart`
- Test: `mobile/test/univer_model_test.dart` (auto-convert commit logic is in the model already; widget test for the chip)

- [ ] Grid: cells with `linkAt(r,c) != null` render `AppColors.accent` + underline.
- [ ] Selecting a linked cell shows a chip row under the formula bar: 🔗 hostname + **Open** (`launchUrl(..., mode: LaunchMode.externalApplication)`) + **Edit** + **Remove**.
- [ ] Toolbar insert-link → `LzBottomSheet` dialog: display text (prefilled from cell), URL field, validates `http(s)://`; writes `setLink(anchor, url, display: text)`.
- [ ] `_commit()`: after setCell, if the raw text is a bare URL or exact `[text](url)` markdown → run the model's single-cell convert (reuse `convertUrlsToLinks` on a temp or add `autoLinkCell(r,c)`); then schedule save.
- [ ] Overflow menu (share PopupMenu): "Convert URLs to links" → `repo.convertLinks(id)` → replace `_sheet` from returned snapshot, re-base `updatedAt`, snack "N links created".
- [ ] `flutter analyze` + tests green. **Commit** `feat(mobile): sheet hyperlinks — render, open, insert, auto + bulk convert`

### Task 10: Mobile editor — row/col ops, copy/paste, sort, freeze

**Files:**
- Modify: `sheet_editor_screen.dart`, `sheet_toolbar.dart`
- Test: covered by Task 6 model tests; add widget smoke test for the header context menu

- [ ] Long-press a column header → `showMenu`: Insert left / Insert right / Delete column / Clear column / Sort A→Z / Sort Z→A / Column width… (slider 60–320). Long-press row gutter → Insert above / Insert below / Delete row / Clear row. All call the Task-6 model methods + `_scheduleSave()` (+ `repo.recalc` when the sheet has any formula — reuse the existing recalc path).
- [ ] Copy/paste buttons in the toolbar overflow: Copy = `Clipboard.setData(rangeToTsv(_sel.range))`; Paste = `Clipboard.getData` → `pasteTsv(anchor)`.
- [ ] Freeze toggle (toolbar pin icon) → `toggleFreeze()`; render frozen first row/col as pinned headers (duplicate row 0 / col 0 painted above the InteractiveViewer when `frozen` — simplest faithful rendering).
- [ ] Tests + analyze green. **Commit** `feat(mobile): sheet row/col ops, TSV copy/paste, sort, freeze`

### Task 11: Mobile — sync safety + fresh-on-focus

**Files:**
- Modify: `sheet_editor_screen.dart`, `mobile/lib/screens/documents/doc_editor_screen.dart` (same save shape)
- Test: `mobile/test/sheet_conflict_test.dart` (widget test with fake repo)

- [ ] Track `String? _baseUpdatedAt` from `getPayload`; every `_save()` passes it and re-bases from the response; `_save()` passes `name: null` (server keeps the stored name — kills the stale-rename clobber).
- [ ] Catch `DocConflictException` in `_save()`: show a persistent `MaterialBanner` "Sheet changed on the server" with **Reload** (adopt `e.current` payload+updatedAt, clear undo stacks) and **Keep mine** (re-save with `baseUpdatedAt: null`).
- [ ] Fresh-on-focus: make the screen `with WidgetsBindingObserver` — on `AppLifecycleState.resumed`, and in `didPopNext` (via the app's existing `RouteObserver` if registered in `app_router.dart`; otherwise add one) → `_load()` IF no unsaved edits pending (`_saveTimer == null`).
- [ ] AI edit + bulk convert already replace the snapshot — also re-base `_baseUpdatedAt` from their responses.
- [ ] Same minimal treatment in `doc_editor_screen.dart` (base tracking + banner; docs editor is simpler).
- [ ] Tests + analyze green. **Commit** `fix(mobile): sheet sync — optimistic concurrency, conflict banner, fresh-on-focus`

### Task 12: Tags UI — mobile list

**Files:**
- Modify: `mobile/lib/screens/documents/documents_list_view.dart`, `mobile/lib/providers/documents_provider.dart`
- Test: `mobile/test/documents_tags_test.dart` (provider filter logic)

- [ ] Cards show tag chips (first 3 + "+n"); card long-press/menu gains "Tags…" → `LzBottomSheet` selector: existing tags as toggle chips + a TextField to add new (reuse the visual pattern of `mobile/lib/screens/tasks/chip_edit.dart`); saves via `repo.setTags`.
- [ ] Filter chip row above the list (all distinct tags across the loaded list, multi-select AND). Pure filter fn unit-tested.
- [ ] Tests + analyze green. **Commit** `feat(mobile): document tags — chips, selector sheet, filter row`

### Task 13: Ship

- [ ] Full test sweep: `python -m pytest tests/sheets tests/gateway -q` (to file if it hangs), `cd web && npx tsc --noEmit && npm run build`, `cd mobile && flutter analyze && flutter test`.
- [ ] Bump `mobile/pubspec.yaml` `version:` minor (`1.18.0+56`).
- [ ] `scripts/build-mobile-apk.sh` → verify `mobile/dist/app-release.apk` + `version.json`.
- [ ] `make rebuild` (server+web Docker).
- [ ] Update `TODO.md` (check items) + `DOCS.md` (new patterns: CAS save, link resource helpers, tags) + CLAUDE.md one-liner in the Documents row if needed.
- [ ] Commit `chore(mobile): v1.18.0+56 — sheets next-level pass` and report.

## Self-Review (done at write time)

- Spec coverage: §1 sync→Tasks 1,2,7,11; §2 links→3,4,5,6,9; §3 tags→1,2,5,7,12; §4 presets→5; §5 grid→6,8,9,10; testing→in every task; delivery→13. Spec's "encrypted tags" amended to plaintext (header note) — matches existing table design.
- Types: `SelRange`/`SheetSelection`/`CellStyleView` defined Task 6/8 before use; `DocConflictException` Task 7 before Task 11; `SheetConflictError` Task 1 before Task 2.
- Known risk: exact Univer preset export names verified at install time (Task 5 Step 2 says how).
