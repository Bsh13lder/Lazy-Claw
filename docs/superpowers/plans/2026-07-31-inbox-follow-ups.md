# Expense Inbox — Follow-up Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the post-merge follow-ups from the three inbox-phase final reviews: two file-size extractions, mobile UX/robustness hardening, sync-rejection observability, and the pre-existing test flake.

**Architecture:** Zero behavior change in the extractions (compat re-exports keep every import/test working). Hardening items are the exact fixes the final reviews specified. Sync change mirrors the existing create-rejection logging path — the engine's flow itself stays untouched.

**Tech Stack:** Python 3.12 (server skills), Flutter/Dart (mobile), existing test suites.

## Global Constraints

- Repo root `/Users/blckit/Desktop/Code_Projects/lazyclaw`. NEVER run the full pytest suite (live DB). Python: targeted files only. Mobile: `cd mobile && flutter analyze` (60-issue baseline) + targeted `flutter test` dirs.
- Extractions are PURE MOVES: no logic edits, no renames, no formatting churn beyond what the move forces. Every existing import site and test must pass UNCHANGED (compat re-exports where needed).
- Commit style `<type>: <description>`, no AI attribution, explicit `git add` paths.
- Mobile test conventions: recording DAO / fake transports throwing production `ApiError` shapes; no real DB in widget tests.

---

### Task 1: Extract inbox skills from `budget_manager.py` → `budget_inbox.py`

**Files:**
- Create: `lazyclaw/skills/builtin/budget_inbox.py`
- Modify: `lazyclaw/skills/builtin/budget_manager.py`, `lazyclaw/skills/registry.py`

**Interfaces:**
- Produces: `budget_inbox.py` holds `MoveExpenseSkill`, `AutoAssignInboxSkill`, `ListProjectsSkill`, `ListBudgetTopupsSkill` (plus whatever module-level helpers ONLY they use — check `_fmt_money` usage: if shared, import it from budget_manager). `budget_manager.py` ends with compat re-exports: `from lazyclaw.skills.builtin.budget_inbox import MoveExpenseSkill, AutoAssignInboxSkill, ListProjectsSkill, ListBudgetTopupsSkill  # noqa: F401 — compat re-export` so existing imports (tests import from budget_manager) keep working. `registry.py` imports the four from the NEW module.

- [ ] **Step 1:** Read `budget_manager.py` end-to-end; list the four classes' line ranges and every helper they touch. Move them verbatim (docstrings, comments included) into the new module with the imports they need (`GENERAL_PROJECT_NAME` from `lazyclaw.budgets.store`, `_fmt_money` imported from budget_manager or moved if only inbox skills use it — check).
- [ ] **Step 2:** Add the compat re-export block; update registry imports.
- [ ] **Step 3:** Verify — `python3 -m pytest tests/test_budget_inbox_skills.py tests/test_budget_expense_skill.py tests/test_budget_pending.py tests/runtime/test_budget_write_inline_autopromote.py -v` ALL green with ZERO test-file edits; both files now well under control (`wc -l` both — budget_manager should drop to roughly ~740, budget_inbox ~470).
- [ ] **Step 4:** Commit — `refactor: extract inbox skills into budget_inbox.py (pure move, compat re-exports)`

---

### Task 2: Extract bulk-assign UI from `expenses_screen.dart`

**Files:**
- Create: `mobile/lib/screens/expenses/bulk_assign.dart`
- Modify: `mobile/lib/screens/expenses_screen.dart`

**Interfaces:**
- Produces: the private bulk widgets (`_BulkActionBar`, `_BulkAssignSheet`, `_AutoPreviewSheet` — find their actual names) become public-in-file widgets (`BulkActionBar`, …) in `bulk_assign.dart`, taking their dependencies as constructor params/callbacks exactly as they already do. `expenses_screen.dart` imports them. Pure move: no logic edits.

- [ ] **Step 1:** Locate the three widgets + any tiny shared helpers they use; move verbatim (rename leading underscore off the class names only — call sites updated accordingly).
- [ ] **Step 2:** Verify — `cd mobile && flutter analyze` (zero new) + `flutter test test/screens/expenses/ test/models/ test/providers/` green; `wc -l lib/screens/expenses_screen.dart` (expect roughly −500).
- [ ] **Step 3:** Commit — `refactor(mobile): extract bulk-assign UI into expenses/bulk_assign.dart (pure move)`

---

### Task 3: Mobile hardening — bulk-bar visibility + narrow catch

**Files:**
- Modify: `mobile/lib/screens/expenses_screen.dart` (+ `bulk_assign.dart` from Task 2 as needed)

**Interfaces:**
- Produces: (a) the bulk bar becomes screen-pinned — render it bottom-anchored (a `Stack`/`Align` overlay above the scroll view, or `bottomNavigationBar`-adjacent within the tab — pick whichever the existing screen structure makes cleanest) so it is ALWAYS visible in selection mode regardless of scroll position; keep the `AnimatedSwitcher`/`AppMotion` transition. (b) `_openAutoAssign`'s `catch (_)` narrows to `on DioException catch (_)` (which wraps `ApiError`) → "needs connection" message; any OTHER exception rethrows in debug / logs + generic "something went wrong" in release (match the app's existing error-snackbar idiom — grep for how other screens phrase it).

- [ ] **Step 1:** Implement (a); verify by reading the widget tree that the bar cannot be scrolled out of view (it must no longer live inside the scrollable slivers).
- [ ] **Step 2:** Implement (b).
- [ ] **Step 3:** Verify — `flutter analyze` zero new; `flutter test test/screens/expenses/ test/providers/` green.
- [ ] **Step 4:** Commit — `fix(mobile): pin bulk bar on screen + narrow auto-assign error handling`

---

### Task 4: Mobile pickers — guard-trip state reset + status filter + save-path test

**Files:**
- Modify: `mobile/lib/screens/expenses/expense_detail_sheet.dart`, `mobile/lib/models/task_project_link.dart`
- Test: `mobile/test/screens/expense_detail_sheet_test.dart`, `mobile/test/models/task_project_link_test.dart` (extend both)

**Interfaces:**
- Produces: (a) when `_ProjectPicker`/`_TaskPicker`'s stale-id guard trips (selected id no longer in options), the SHEET's state (`_projectId`/`_taskId`) resets to match what renders — via an `onStaleSelection` callback or post-frame state fix, so render and submitted value can never diverge; save-path widget test: expense with a deleted task id → open sheet → Save → recorded `updateExpense` call has `taskId: null, taskIdSet: true`. (b) `tasksForProject` gains `bool includeCompleted = false` (default excludes tasks whose status is done/completed — check the Task model's actual status values with grep) with unit tests; detail-sheet + bulk-assign task pickers use the default.

- [ ] **Step 1:** TDD (a): failing save-path widget test (stub notifier records the call), then implement the reset, green.
- [ ] **Step 2:** TDD (b): failing unit tests (done task excluded by default, included with flag), then implement, green. Guard: an expense currently LINKED to a done task must still render that task's label safely — the stale-guard from (a) treats it as stale and resets to null; assert that interplay in the widget test and note it in the report.
- [ ] **Step 3:** Verify — `flutter analyze` zero new; `flutter test test/screens/ test/models/ test/providers/` green.
- [ ] **Step 4:** Commit — `fix(mobile): picker guard-trip state reset + status-filtered task pickers`

---

### Task 5: Sync — surface drained UPDATE rejections

**Files:**
- Modify: `mobile/lib/sync/budgets_sync.dart` (ONLY the rejection-classification/logging area — the drain flow itself stays byte-identical)
- Test: `mobile/test/` (find the existing budgets_sync test file and extend)

**Interfaces:**
- Consumes: the existing `_classifyPushFailure` 4xx branch that calls `deleteOutboxItem` for a rejected op, and the existing `_logCreateRejected`-style mechanism (find its exact name + where it writes — conflicts table).
- Produces: rejected UPDATE (and DELETE, if the same branch handles it) ops get logged through the SAME conflicts mechanism as creates — entity, id, status code, response detail — never dropped silently. The op is still drained (behavior unchanged); only observability is added. If the conflicts store needs an op-type column it already has one (check) — do NOT migrate schema; if a schema change would be required, log via the existing structure's fields only and note the limitation in the report.

- [ ] **Step 1:** Read the classification branch + the create-rejection logger; write the failing test first: fake transport 404s an update push → assert the conflicts store received a rejection record AND the outbox item was drained AND the push loop continued (production exception shapes!).
- [ ] **Step 2:** Implement, green.
- [ ] **Step 3:** Verify — `flutter analyze` zero new; run the sync test file + `flutter test test/local/ test/providers/`.
- [ ] **Step 4:** Commit — `feat(mobile): log drained update/delete sync rejections to conflicts (was silent)`

---

### Task 6: Fix the pre-existing month-stepper test flake

**Files:**
- Modify: `mobile/test/screens/expenses_range_filter_test.dart` (and production code ONLY if the investigation proves a real date bug)

- [ ] **Step 1:** Run the failing sub-test ("Month range shows the one-tap month stepper with chevrons") 3× and read it + the `_MonthStepper` logic it exercises. Diagnose: date-boundary assumption (e.g. test constructs "now"-relative expenses that fall outside the asserted month near month ends/DST)?
- [ ] **Step 2:** Fix at the root: if the TEST builds boundary-fragile dates, pin them to explicit mid-month dates or inject a fixed clock the way other date tests in the suite do (grep for an existing clock/injection pattern first). If the SCREEN has a real boundary bug, report DONE_WITH_CONCERNS with the diagnosis BEFORE changing production code.
- [ ] **Step 3:** Verify — the whole `test/screens/expenses_range_filter_test.dart` file green 3 consecutive runs; `flutter analyze` zero new.
- [ ] **Step 4:** Commit — `test(mobile): pin month-stepper range test to boundary-safe dates` (adjust to the actual diagnosis)

---

### Task 7: Verification sweep

- [ ] **Step 1:** Server: `python3 -m pytest tests/test_budget_inbox_skills.py tests/test_budget_expense_skill.py tests/test_budget_pending.py tests/test_budgets_routes.py tests/budgets/test_inbox_suggest.py tests/runtime/test_budget_write_inline_autopromote.py -v` — all green.
- [ ] **Step 2:** Mobile: `flutter analyze` (60-issue baseline, zero new) + `flutter test test/providers/ test/repositories/ test/models/ test/local/ test/screens/` — all green (the month-stepper flake now fixed, so screens/ should be fully green).
- [ ] **Step 3:** Registry smoke: the four inbox skills still resolve by name (same check as Phase 1's sweep). Specialist loader boot-check prints no unknown-skill warnings.
- [ ] **Step 4:** `wc -l lazyclaw/skills/builtin/budget_manager.py lazyclaw/skills/builtin/budget_inbox.py mobile/lib/screens/expenses_screen.dart` — report the new sizes.
