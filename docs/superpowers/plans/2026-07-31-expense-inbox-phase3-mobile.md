# Expense Inbox — Phase 3 (Mobile/Flutter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mobile Inbox treatment: pinned 📥 Inbox chip in the Ledger, task picker in the expense edit sheet, long-press multi-select with bulk assign and AI auto-assign — with NO sync-engine or schema changes.

**Architecture:** Inbox = project with `nameKey == 'general'` (nullable — fall back to `name.toLowerCase() == 'general'` for legacy cached rows). Moves/assigns are ordinary expense `update` outbox ops the sync engine already handles (Phase 1's server fix makes them stick). Suggestions are an online-only repository call. All UI from the Lz* kit only — never hard-code colors/text styles.

**Tech Stack:** Flutter (Riverpod + go_router + Dio), sqflite_sqlcipher local cache, existing outbox/sync engine (UNTOUCHED).

## Global Constraints

- Mobile root: `/Users/blckit/Desktop/Code_Projects/lazyclaw/mobile`. Verify with `flutter analyze` (no new issues) + targeted `flutter test <paths>` — NEVER widget-test against a real DB (sqflite + FakeAsync hangs; use the recording-DAO/offline-transport pattern from `test/providers/update_expense_test.dart:25-60`).
- Fake transports must throw the PRODUCTION exception shape (`ApiError` / `DioException(error: ApiError)`) — green fakes with wrong shapes have masked data-loss bugs before.
- Key anchors (verified 2026-07-30, unchanged since — re-read before editing): `_kUncategorizedFilter`/`_kFavoritesFilter` sentinels `expenses_screen.dart:29,:33`; Ledger filter Row 3 `:1286-1345` (`LzChip` per project, starred first, `onProjectChanged`); `_applyFilter` `:752-766`; `ExpenseRow` `mobile/lib/screens/expenses/expense_row.dart` (Dismissible `:56-88`, project chip `:147-156`, `onTap` prop); detail sheet `expense_detail_sheet.dart` (`_ProjectPicker` `:315-392`, `_save()` `:59-92`); provider `budgets_provider.dart:318-346` (`updateExpense`, no taskId); DAO `budgets_dao.dart:623-674` (`applyLocalExpenseUpdate`, patch map `:644-651` uses null-aware `'project_id': ?projectId` — mirror that syntax); repo `budgets_repository.dart:228-232` (generic PATCH); `LzChip` API `mobile/lib/ui/components/lz_chip.dart:9-27`.
- Server contract (live after `make rebuild`): expense PATCH accepts `project_id` (404 unknown/400 null) + `task_id` (nullable clear); `POST /api/budgets/inbox/suggestions` body `{expense_ids: null|[...]}` → `{suggestions: [{expense_id, project_id|null, project_name|null, confidence, reason}], skipped}` (`[]` = none, null/omitted = all inbox capped 10).
- Commit style `<type>: <description>`, no AI attribution, explicit `git add` paths.

---

### Task 1: Thread `taskId` through provider → DAO → outbox

**Files:**
- Modify: `mobile/lib/providers/budgets_provider.dart` (`updateExpense:318`), `mobile/lib/local/budgets_dao.dart` (`applyLocalExpenseUpdate:623`)
- Test: `mobile/test/providers/update_expense_test.dart` (extend), `mobile/test/local/` (extend the budgets DAO test file if one exists, else add the assertion at the provider seam only)

**Interfaces:**
- Produces: `BudgetsNotifier.updateExpense(String id, {double? amount, String? description, String? vendor, String? projectId, String? taskId, bool taskIdSet = false, String? notes, String? spentAt})` and the same `taskId`/`taskIdSet` pair on DAO `applyLocalExpenseUpdate`. Semantics — **null-vs-absent is load-bearing** (this repo's `feedback_patch_null_vs_absent` lesson): when `taskIdSet` is false the patch map has NO `task_id` key (existing callers — favorite toggles, plain field edits — must never clear a task link); when `taskIdSet` is true the patch map contains `'task_id': taskId` even when `taskId == null` → JSON null on the wire → the server's `exclude_unset` keeps it and CLEARS the link (`gateway/routes/budgets.py` documents `task_id` as explicitly clearable). Cache write updates the `task_id` column (exists, `app_db.dart:170`) only when `taskIdSet`. Sync's `_patchFrom` passes the key through untouched (`budgets_sync.dart:451-454`) — DO NOT touch sync.

- [ ] **Step 1: Write the failing tests** — extend the recording-DAO test with three cases: (a) `updateExpense(id, taskId: 't1', taskIdSet: true)` → recorded DAO call carries `taskId == 't1', taskIdSet == true`; (b) `updateExpense(id, taskId: null, taskIdSet: true)` → recorded call carries the explicit-clear pair; (c) `updateExpense(id, description: 'x')` (no task args) → recorded call has `taskIdSet == false`. If a DAO-level test file exists under `test/local/`, also assert the patch map: case (b) → `'task_id': null` PRESENT; case (c) → no `task_id` key.
- [ ] **Step 2: Run to verify it fails** — `cd mobile && flutter test test/providers/update_expense_test.dart` → compile error (no taskId param) = RED.
- [ ] **Step 3: Implement** — add the `taskId`/`taskIdSet` pair in provider + DAO per the Interfaces semantics (conditional patch-map entry, conditional cache write), leaving `project_id`'s existing handling untouched (project move already nulls the denormalized `project_name` at `:658`).
- [ ] **Step 4: Verify** — `flutter test test/providers/update_expense_test.dart` green; `flutter analyze` no new issues.
- [ ] **Step 5: Commit** — `git add mobile/lib/providers/budgets_provider.dart mobile/lib/local/budgets_dao.dart mobile/test/providers/update_expense_test.dart && git commit -m "feat(mobile): thread taskId through expense update path"` (add the DAO test file too if extended).

---

### Task 2: `InboxSuggestion` model + repository call

**Files:**
- Create: `mobile/lib/models/inbox_suggestion.dart`
- Modify: `mobile/lib/repositories/budgets_repository.dart`
- Test: `mobile/test/repositories/` (new file `inbox_suggestions_test.dart`, mirroring an existing repository test's fake-transport pattern)

**Interfaces:**
- Produces: `class InboxSuggestion { final String expenseId; final String? projectId; final String? projectName; final String confidence; final String? reason; }` with `fromJson`; `Future<({List<InboxSuggestion> suggestions, int skipped})> getInboxSuggestions({List<String>? expenseIds})` on the repository — POST `/api/budgets/inbox/suggestions` body `{'expense_ids': expenseIds}` (null means ALL server-side; never coerce null→[] — `[]` means NONE; this is the exact null-vs-absent class from the project's own lesson log).

- [ ] **Step 1: Failing tests** — fake transport returns canned JSON (one suggestion + `skipped: 2`) → parsed record matches; error case: transport throws `ApiError` → the repository method propagates (callers handle; no swallowing).
- [ ] **Step 2:** RED (missing symbols), then implement model + repo method.
- [ ] **Step 3: Verify** — `flutter test test/repositories/inbox_suggestions_test.dart` green; `flutter analyze` clean.
- [ ] **Step 4: Commit** — `git add mobile/lib/models/inbox_suggestion.dart mobile/lib/repositories/budgets_repository.dart mobile/test/repositories/inbox_suggestions_test.dart && git commit -m "feat(mobile): inbox suggestions model + repository call"`

---

### Task 3: 📥 Inbox chip in Ledger Row 3

**Files:**
- Modify: `mobile/lib/screens/expenses_screen.dart` (Row 3 `:1286-1345` + a helper), `mobile/lib/models/project.dart` (helper)
- Test: `mobile/test/models/` (new or extended file for the helper)

**Interfaces:**
- Produces: `Project.isInbox` getter on the model: `bool get isInbox => (nameKey ?? name.toLowerCase()) == 'general';` (pure, unit-testable). Row 3 renders, right after the `'★ Favorites'` chip: `LzChip(label: '📥 Inbox' or '📥 Inbox (N)', dense: true, color: AppColors.warn, selected: _projectFilter == inbox.id, onTap: () => onProjectChanged(inbox.id))` where `inbox = projects.firstWhereOrNull((p) => p.isInbox)`; the chip renders only when `inbox != null` AND N > 0 (N = count of that project's expenses in the current full expense list — pass it in like `favoriteIds` is passed today). The per-project chip loop `:1318-1329` EXCLUDES the inbox project (no duplicate chip).

- [ ] **Step 1: Failing unit test** for `Project.isInbox` — three cases: `nameKey: 'general'` → true; `nameKey: null, name: 'General'` → true; `nameKey: 'clubbay'` → false.
- [ ] **Step 2:** RED → implement getter → GREEN.
- [ ] **Step 3: Implement the chip** in `_LedgerControls` Row 3 per the Interfaces block: read how `favoriteIds`/`showUncategorized` flow into `_LedgerControls` (`:1146` ctor) and thread the inbox count the same way; selection uses the ordinary project-id filter path (`onProjectChanged(inbox.id)` → `_applyFilter` `:752-766` needs NO changes — it's a plain project filter). Keep the `'Uncategorized'` sentinel behavior untouched.
- [ ] **Step 4: Verify** — `flutter analyze` no new issues; `flutter test test/models/` green. (No widget test — `_LedgerControls` is private and the screen needs a DB; the logic added is the pure helper + declarative chip.)
- [ ] **Step 5: Commit** — `git add mobile/lib/screens/expenses_screen.dart mobile/lib/models/project.dart mobile/test/models/ && git commit -m "feat(mobile): pinned inbox chip in ledger filters"`

---

### Task 4: Task picker in the expense detail sheet

**Files:**
- Modify: `mobile/lib/screens/expenses/expense_detail_sheet.dart`
- Test: `mobile/test/models/` or `mobile/test/core/` (pure helper test)

**Interfaces:**
- Consumes: Task 1's `taskId` param; the tasks provider (`tasks_provider.dart` — already imported by `expenses_screen.dart:9-10`; find the provider exposing the task list).
- Produces: pure helper `List<Task> tasksForProject(List<Task> all, Project p)` filtering by `(t.category ?? '').trim().toLowerCase() == (p.nameKey ?? p.name.toLowerCase())` (the same category↔name_key join the server and agent use) — put it beside the models or in a small util file; the sheet gains a task `DropdownButton` below `_ProjectPicker` (`:164-168`), default "(no task)" (null), initial value = `expense.taskId`, options rebuilt when the project selection changes (and the current selection RESET to null on project change — a task belongs to one project); `_save()` (`:59-92`) passes `taskId: _taskId, taskIdSet: true` through `updateExpense` (always-set: the sheet submits the picker's current value, so "(no task)" is an explicit clear).

- [ ] **Step 1: Failing unit test** for `tasksForProject` — matches by category casefold, ignores tasks of other categories, handles null category and null nameKey.
- [ ] **Step 2:** RED → implement helper → GREEN.
- [ ] **Step 3: Implement the picker** per Interfaces (mirror `_ProjectPicker`'s structure `:315-392` including its stale-id guard idiom `:343-346`); wire `_save()`.
- [ ] **Step 4: Verify** — `flutter analyze`; `flutter test test/providers/update_expense_test.dart test/models/` green.
- [ ] **Step 5: Commit** — `git add mobile/lib/screens/expenses/expense_detail_sheet.dart <helper file> <test file> && git commit -m "feat(mobile): task picker in expense edit sheet"`

---

### Task 5: Multi-select + bulk assign + Auto in the Ledger

**Files:**
- Modify: `mobile/lib/screens/expenses_screen.dart`, `mobile/lib/screens/expenses/expense_row.dart`
- Test: `mobile/test/` pure-helper test for the confident filter

**Interfaces:**
- Consumes: Tasks 1-3 (`updateExpense(projectId:, taskId:)`, `getInboxSuggestions`, inbox chip active state).
- Produces: when the Ledger's project filter is the inbox project — long-press on an expense row enters selection mode: `Set<String> _selected` in `_LedgerTab` state; rows render a leading check indicator instead of the receipt icon and `onTap` toggles membership (Dismissible swipe DISABLED in selection mode); a bulk bar (an `LzCard` pinned above the footer sliver) shows `N selected · [Assign] · [✨ Auto] · [✕ cancel]`. Also: pure helper `List<InboxSuggestion> confidentSuggestions(List<InboxSuggestion> s)` → those with `projectId != null && (confidence == 'high' || confidence == 'medium')`.
  - **Assign** → `LzBottomSheet` with a project picker (exclude inbox project; reuse `_ProjectPicker`'s dropdown idiom) + optional task picker (Task 4 helper) → sequential `updateExpense(id, projectId: target, taskId: t, taskIdSet: true)` over the selection (`taskIdSet: true` even when no task is chosen — a moved expense's old task link belongs to the old project and must clear, same semantics as the agent's move_expense) (offline-capable — each queues an outbox op) → `LzSnackbar`-style feedback "Moved X of N", clear selection, provider refresh.
  - **Auto** → `getInboxSuggestions(_selected.isEmpty ? null : _selected.toList())`; on `ApiError` show a "needs connection" snackbar (feature is online-only); else an `LzBottomSheet` preview listing each suggestion (`description → projectName (confidence)`, "no match" rows dimmed) with an "Apply N confident" button → loop `updateExpense(id, projectId: s.projectId, taskId: null, taskIdSet: true)` over `confidentSuggestions(...)` (same clear-on-move semantics as Assign), show "`skipped` more not analyzed — run again" when non-zero.
- [ ] **Step 1: Failing unit test** for `confidentSuggestions` (high/medium kept, low/none dropped, null-project dropped).
- [ ] **Step 2:** RED → implement helper → GREEN.
- [ ] **Step 3: Implement selection mode + bulk bar + both sheets** per Interfaces. Keep ALL state in `_LedgerTab`'s State — no new providers. Respect `AppMotion` durations for the bar's appearance; Lz kit components only.
- [ ] **Step 4: Verify** — `flutter analyze` no new issues; run `flutter test test/providers/ test/repositories/ test/models/ test/local/` (targeted dirs) — all green.
- [ ] **Step 5: Commit** — `git add mobile/lib/screens/expenses_screen.dart mobile/lib/screens/expenses/expense_row.dart <helper+test files> && git commit -m "feat(mobile): inbox multi-select with bulk assign and AI auto-assign"`

---

### Task 6: Verification sweep

- [ ] **Step 1:** `cd mobile && flutter analyze` — zero NEW issues vs. main's baseline (run on main first if unsure).
- [ ] **Step 2:** `flutter test test/providers/ test/repositories/ test/models/ test/local/` — all green. Do NOT run the full `flutter test` blindly — screen/sheet suites unrelated to this work may be slow; the targeted dirs cover every seam this phase touched.
- [ ] **Step 3:** `grep -n "isInbox\|nameKey" mobile/lib/screens/expenses_screen.dart mobile/lib/screens/expenses/expense_detail_sheet.dart` — inbox detection must go through `Project.isInbox`, never a literal display-name compare in the UI files.
- [ ] **Step 4:** Report done. Notes for the operator: APK build is NOT part of this plan — run `scripts/build-mobile-apk.sh` when ready to ship; watch item from the final Phase 1 review: a mobile outbox `update` op whose target project was deleted server-side now gets a 404 (was a silent drop) — confirm during the next real-device sync that the outbox surfaces it as a rejected op rather than wedging (sync engine already dead-letters on 4xx per its design).
