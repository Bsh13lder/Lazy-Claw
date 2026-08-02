# Sync Recovery, Calendar/Widget, Parser Hardening, Subtask Expenses — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** (P1) Recover dated/recurring tasks missing from the phone, (P2) make calendar+widget show them properly, (P3) stop the quick-add parser mangling titles and teach it real dates, (P4) allow expenses on subtasks.

**Architecture:** Mobile-first Flutter + a small Python backend addition for P4. P1 is a local-DB migration + sync-cursor hardening. P3 is a parser refactor then additive matchers. P4 adds one plaintext `subtask_id` column beside the existing `task_id` on `project_expenses`, so every existing aggregation keeps working untouched.

**Evidence base (binding — do not re-derive):** `docs/superpowers/specs/2026-08-03-diagnosis.md` — written by the discovery pass, contains the file:line map and the live-DB evidence for every claim below.

## Global Constraints

- Backend deploys need `make rebuild` (baked image). NEVER run a bare `pytest tests/` while the container is up — only the targeted files named per task.
- Mobile gates: `flutter analyze` (baseline 65, zero new) + the task's test files. `test/screens/expenses_range_filter_test.dart` has ONE documented pre-existing failure; `test/screens/home_screen_test.dart` is LOAD-FLAKY (passes in isolation) — if it fails, re-run it alone before reporting.
- Conventional commits, `(mobile)` scope where mobile-only, NO AI attribution.
- Immutability; house style; widget tests use plain callbacks/fakes (no real sqflite), DAO tests use `sqflite_common_ffi`.
- Encryption: `subtask_id` is a plaintext id (no user content) exactly like `task_id` — SUM()/GROUP BY must keep working in SQL.
- **P4 hard invariant:** `subtask_id IS NOT NULL` implies `task_id IS NOT NULL`. A subtask expense ALWAYS also carries its parent task id, so every existing per-task/per-project total includes it with zero changes to aggregation code.
- **P4 delete policy (user decision):** deleting a subtask DEMOTES its expenses (`subtask_id = NULL`), never deletes them. Money is never lost.

---

### Task 1: Tasks sync-cursor recovery (P1 — ships first, fixes the reported bug)

**Files:**
- Modify: `mobile/lib/local/app_db.dart` (`kAppDbVersion` 12→13 + migration branch; mirror the v9→v10 `budgets` rewind at ~:363-366)
- Modify: `mobile/lib/sync/task_sync.dart` (cursor advance, ~:499-501 / :526-534)
- Test: `mobile/test/local/app_db_migration_v13_test.dart` (new), extend `mobile/test/sync/` cursor coverage

**Interfaces:**
- v13 migration deletes the `sync_state` row for entity `'task'` (`kTaskEntity`, `task_dao.dart:20`) → next `getCursor()` returns null → the following sync pulls a FULL snapshot. One-time, idempotent.
- `TaskSync` cursor advance gains an **overlap window**: instead of storing `changes.now` verbatim, store `now - 2s` (parse, subtract, re-serialise; fall back to the raw string when unparseable). Re-delivery is idempotent (`upsertFromServer` + LWW), so a small overlap can only cause a harmless re-apply, while the current zero-overlap advance can permanently orphan a row committed between the server's `now_iso` stamp and its SELECT.

- [ ] **Step 1: failing tests.** (a) migration: build a v12-shaped DB with a `sync_state` row `('task','2026-07-28T00:00:00Z')` + a budgets row, run `migrateAppDb(db,12,13)`, assert the task row is GONE and the budgets row SURVIVES, and `kAppDbVersion >= 13`; fresh-install schema still creates `sync_state`. (b) cursor: after a pull whose `changes.now` is `2026-08-03T10:00:00.000Z`, the stored cursor is `2026-08-03T09:59:58.000Z` (2s overlap), and an unparseable `now` is stored verbatim. Run → FAIL.
- [ ] **Step 2: implement** both. Keep the migration comment explicit about WHY (a cursor ahead of an undelivered row orphans it forever because the server filters `updated_at > since`).
- [ ] **Step 3:** `flutter test test/local/ test/sync/` + `flutter analyze` → PASS.
- [ ] **Step 4: commit** `fix(mobile): rewind the tasks sync cursor (v13) and stop advancing it into a gap`

---

### Task 2: Widget shows upcoming work (P2)

**Files:**
- Modify: `mobile/lib/core/home_widget_tasks.dart` (tier selection ~:105-171)
- Test: extend `mobile/test/core/home_widget_tasks_test.dart`

**Interfaces:**
- Today `relevantWidgetTasks` returns ONLY the `dueNow` tier when non-empty (overdue+today, oldest first), so 3 due-now tasks hide everything else and `widgetMoreLabel(3)` returns `''` — no hint at all.
- New behaviour: build the list as `dueNow` first, then FILL the remaining rows from the upcoming-dated tier (soonest first), then undated. Keep the row cap. `widgetMoreLabel` must count everything NOT shown (dated + undated) so a hidden Aug-4/Aug-5 task always produces a `+N more`.
- Preserve the existing blank-due-pill behaviour for undated rows (`TasksWidget.kt:132` hides an empty pill).

- [ ] **Step 1: failing tests** — with 3 due-now + 2 future-dated + 4 undated and a 3-row cap: rows are the 3 due-now AND the label is `+6 more`; with 1 due-now + 2 future: rows = due-now then the 2 soonest future, label `''`; with 0 dated: unchanged undated behaviour (existing tests stay green). Run → FAIL.
- [ ] **Step 2: implement.** [ ] **Step 3:** `flutter test test/core/` + analyze → PASS.
- [ ] **Step 4: commit** `fix(mobile): widget fills empty rows with upcoming tasks and always hints hidden ones`

---

### Task 3: Calendar correctness — local days, recurrence ghosts, visible all-done (P2)

**Files:**
- Modify: `mobile/lib/screens/tasks/task_calendar_utils.dart` (day keying ~:18-34)
- Modify: `mobile/lib/core/home_widget_tasks.dart` (:110/:138/:152) and `mobile/lib/screens/tasks_screen.dart` (`_groupTasks` :131-133) — same `.toLocal()` idiom
- Modify: `mobile/lib/screens/tasks/task_calendar_view.dart` (`_AllDoneBadge` ~:348-368)
- Test: extend `mobile/test/screens/task_calendar_utils_test.dart`; new `mobile/test/screens/task_calendar_recurrence_test.dart`

**Interfaces:**
- **3a. Local-day keying:** every `DateTime.parse(raw)` used to derive a calendar/bucket DAY must be followed by `.toLocal()` before reading `.year/.month/.day` (matches `due_date.dart:26-31`, which already does). Add a `debugPrint` on the silent `catch (_) { continue; }` parse-failure path so a malformed due date is diagnosable instead of vanishing.
- **3b. Recurrence ghosts (the "recurring doesn't show" half of the report):** new pure function in `task_calendar_utils.dart`:
  `Map<DateTime, List<Task>> expandRecurringForRange(List<Task> tasks, DateTime rangeStart, DateTime rangeEnd)` — for each task with a non-empty `recurring` cron, project its NEXT occurrences across the visible range and add them to the day map as GHOST entries. Reuse the existing cron helper the repeat picker uses (`mobile/lib/core/recurrence.dart` — `recurrenceFromCron` + whatever next-occurrence helper exists; if none exists, implement day-stepping for the supported shapes only: daily, weekly-on-weekday(s), monthly-on-day, yearly — NOT arbitrary cron). Ghosts are display-only: they must be visually distinguished (hollow/outlined dot vs solid) and MUST NOT be tappable-as-real or counted in "done" math; the real materialised occurrence always wins for its own day.
  Cap the projection at the visible month range and at 60 generated ghosts per task (guard against a pathological cron).
- **3c. All-done badge:** raise `_AllDoneBadge` visibility — solid `AppColors.success` fill (not 18% alpha) at the same size as `_TaskDot` so a fully-completed day reads as a real marker, not blank space.

- [ ] **Step 1: failing tests** — (a) a `2026-08-04T00:00:00+02:00` due date keys to Aug 4 (not Aug 3) under a non-UTC TZ; malformed date logs and is skipped; (b) `expandRecurringForRange` on `0 8 * * *` across a 7-day range yields 7 ghost days; on `0 9 * * 1` yields only Mondays; a real materialised occurrence on a ghost day does not duplicate; the 60-ghost cap holds; (c) widget/list `.toLocal()` parity test. Run → FAIL.
- [ ] **Step 2-3: implement + run** `flutter test test/screens/ test/core/` + analyze → PASS.
- [ ] **Step 4: commit** `fix(mobile): local-day bucketing, recurring ghosts on the calendar, visible all-done marker`

---

### Task 4: Parser G0 — inert refactor (P3)

**Files:** `mobile/lib/core/smart_add_parser.dart` → split into `mobile/lib/core/smart_add/{dates,times,priority,recurrence_patterns,project}.dart` + a shared `_Collector`.

**Interfaces:** `_collect` (currently a 293-line function, file is 716 lines) becomes a library-private `Collector { final String input; final DateTime ref, today; final List<Raw> raws; void scan(RegExp, Raw? Function(RegExpMatch,int)); }`; each family exposes `void collectX(Collector c)`. `smart_add_parser.dart` keeps only `ParsedTask`, `SmartToken`, `SmartTokenKind`, `parseSmartAdd`, `Raw`, `_resolveOverlaps`, `_weekdayDate`, `_safeDate`, `removeProjectToken`.
ALSO: add a `rank` tiebreak to `_resolveOverlaps` (sort is `start ASC, length DESC` today and Dart's sort is NOT stable → identical spans resolve nondeterministically). Ranks: `range 100 > explicitDate 90 > relativeDate 80 > recurrence 70 > time 50 > priority 30 > project 20`. DELETE the false comment at ~:440-442 ("Listed first so its longer span wins" — resolution is by sort, call order is irrelevant).
ALSO: add a shared test helper `expectWellFormedSpans(input, parsed)` asserting spans are ascending, non-overlapping, in-bounds — the controller silently DROPS malformed spans (`smart_add_controller.dart:52`) so nothing catches this today.

- [ ] **Steps:** ZERO regex text may change. Run the full existing parser suite (89 tests) before and after — identical results prove it inert. `flutter test test/core/ test/screens/` + analyze. Commit `refactor(mobile): split the smart-add collector; deterministic overlap resolution`

---

### Task 5: Parser G1+G2 — stop lying, stop eating words (P3)

**Files:** the new `smart_add/*.dart` families + `mobile/test/core/smart_add_parser_test.dart`

**G1 — remove 13 confirmed false positives (adds ZERO new match surface):**
1. DELETE bare `p1..p4` priority (`_priorityBare`) — `my p1 project` → urgent today.
2. `_priorityBangs` → `(^|\s)(!{2,3})(?=\s|$)` — a single `!` currently sets medium, which IS the default, so it is pure downside.
3. `_weekdayWord`: `sat|sun|wed` require a cue (`on|by|due|this|next|coming|every|before|until|til|from`, or an adjacent clock token, or sole input); `mon|tue|thu|fri` + full names stay bare. Confirmed today: `sat down with the team`→Aug 8; `fix the sat nav`→Aug 8 AND deletes "sat"; `sun is out`→Aug 9; `wed the bride`→Aug 5.
4. `_mdDate`: require ≥1 two-digit component + negative lookbehind `(?<!\b(?:chapter|page|part|section|step|round|ratio|version|v|split|half)\s)`. Kills `split 1/2`, `chapter 3/4`, `ratio 16/9`; PRESERVES `report 6/10` (pinned test) and `12/31`. Comment that this is deliberately variable-length lookbehind (V8-backed, works in Dart) so nobody "simplifies" it.
5. NEW `day after tomorrow` / `overmorrow` — today it matches plain `tomorrow` (WRONG day) and strands "day after".

**G2 — cue absorption (title quality):**
6. NEW `_cuedWeekday` `(^|\s)(?:by|due|before|until|til|this|coming)\s+(<weekday>)(?=\s|$)`. **Ship WITHOUT `on|from`** (high-frequency English: `turn on monday` would eat the cue) — hold those for a follow-up.
7. `_clock12`/`_clock24`: prepend **NON-CAPTURING** `(?:at\s+)?`. CRITICAL: capturing would shift every payload group index and produce silently wrong times.
8. `_timeOfDay`: prepend non-capturing `(?:(?:this|tomorrow|tmrw|tmr|tom)\s+)?`.
9. Move `tonight` out of the date family into the time family → `hour:20, timeDate: today`, reusing the exact payload shape `_inNHours` already uses. `tn/tod/tdy` stay date-only.

**Six existing assertions break intentionally — edit them, don't discover them:** `water plants sat`→ rewrite as `water plants on sat`; `cook tonight` and the "tonight stays date-only" test INVERT to `T20:00:00`; the three `bare priority codes` tests become near-misses.

- [ ] TDD each group; run the full parser suite + `flutter test test/screens/` (add_task sheet tests consume the parser) + analyze. TWO commits (G1, then G2).

---

### Task 6: Parser G3+G4 — vocabulary + absolute dates (P3)

**G3 (mechanical siblings, near-zero risk):** `tues|thur|thurs|weds` into the weekday map + all three alternations; `eom`/`eoy` beside `eod`/`eow`; `in N minutes` (order AFTER any month matcher so `in 3 months` isn't eaten); `+Nw`/`+Nm`/`in N months`; `midday`.

**G4 (the only genuinely missing capability — there is NO way to type an absolute date today except ISO or US M/D):**
- `_monthDay` `(^|\s)(<month>)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?(?=\s|$)` and `_dayMonth` `(^|\s)(\d{1,2})(?:st|nd|rd|th)?\s+(<month>)(?:,?\s+(\d{4}))?(?=\s|$)`, `<month>` = `jan(?:uary)?|...|dec(?:ember)?`. Year defaults to the next occurrence ≥ today. Route through `_safeDate` (reject impossible dates, never clamp). Give the two matchers DIFFERENT ranks (Task 4's tiebreak) — `5 june 6` can overlap.
- Anti-patterns that MUST NOT match: `Marching band practice`, `may need to call back`, `a march to the sea`, `summon the may queen` (the required `\s+\d` is what saves these — test all four).
- `D-M-YYYY` / `D.M.YYYY` / `D/M/YYYY` (4-digit year disambiguates ordering).
- In `_mdDate`'s callback: try BOTH orderings, accept the valid one; when both valid keep M/D. This alone rescues `15/03` and `25/12` for a European user with no setting and no change to `report 6/10`. (A `date_order` setting is explicitly OUT of scope.)

- [ ] TDD; full parser suite + analyze. TWO commits (G3, then G4).

---

### Task 7: Backend — `subtask_id` on expenses + demote cascade (P4)

**Files:**
- Modify: `lazyclaw/db/schema.sql` (`project_expenses`, beside `task_id` ~:497) + `lazyclaw/db/connection.py` (guarded ALTER, same tuple-list pattern as `deleted_at`/`is_favorite` ~:282/:310)
- Modify: `lazyclaw/budgets/store.py` (`EXPENSE_COLUMNS` :55-64; `create_expense` :748+; `update_expense` :892+; `list_expenses` filter :827-843)
- Modify: `lazyclaw/tasks/store.py` (`set_steps` — extend the existing comment-orphan prune with the expense DEMOTE) and `delete_task`
- Modify: `lazyclaw/gateway/routes/budgets.py` (create body :86, patch body :97, list filter :315-319)
- Test: `tests/budgets/test_subtask_expenses.py` (new) + extend `tests/tasks/test_comment_orphan_cascade.py`

**Interfaces:**
- `subtask_id TEXT` nullable plaintext, added to `EXPENSE_COLUMNS` (NOT encrypted).
- **Invariant enforced in `create_expense`/`update_expense`:** a `subtask_id` without a `task_id` is rejected with a clear `ValueError` (route → 400). Additionally validate the `subtask_id` exists in that task's current steps at write time (decode via `tasks.store.decode_steps`) — reject otherwise.
- `list_expenses(..., subtask_id=...)` exact-match filter, mirroring `task_id`.
- **Demote cascade in `set_steps`:** when steps are dropped, `UPDATE project_expenses SET subtask_id = NULL, updated_at = ? WHERE user_id = ? AND task_id = ? AND subtask_id NOT IN (<surviving>)`. NEVER delete. Same demote in `delete_task` for that task's expenses (this also closes the pre-existing dangling-`task_id` hole — set BOTH to NULL there? NO: keep `task_id` semantics unchanged, only NULL the `subtask_id`, and note the pre-existing dangling-task_id hole in the report rather than widening scope).
- **Respawn:** nothing to classify — the column lives on `project_expenses`, not `tasks`; a subtask expense stays pinned to the completed occurrence (correct: the money was spent on it). Add a dedicated test asserting exactly that (the `_RESPAWN_*` disposition guard does NOT cover this column).

- [ ] TDD. Targeted runs only: `python3 -m pytest tests/budgets/test_subtask_expenses.py tests/tasks/test_comment_orphan_cascade.py tests/tasks/test_task_comments.py -v`. Commit `feat: expenses can attach to a subtask (demote, never delete, on subtask removal)`

---

### Task 8: Mobile — subtask expense picker + rollup display (P4)

**Files:**
- Modify: `mobile/lib/models/expense.dart` (`subtaskId` field + parse/serialize/copyWith — note `copyWith` currently CANNOT clear `taskId`; add explicit clear flags for both)
- Modify: `mobile/lib/local/app_db.dart` (v14: `expense_cache.subtask_id`), `mobile/lib/local/budgets_dao.dart` (mapping + outbox payloads), `mobile/lib/repositories/budgets_repository.dart`
- Modify: `mobile/lib/screens/expenses/expense_detail_sheet.dart` (subtask picker under the existing task picker — enabled only when a task is selected; "No subtask" clears it)
- Modify: `mobile/lib/screens/tasks/subtask_editor.dart` (show a small money chip on a subtask row that has expenses) + `mobile/lib/screens/tasks/task_detail_sheet.dart` (pass per-subtask totals)
- Test: new `mobile/test/local/app_db_migration_v14_test.dart`, extend expense-sheet + subtask-editor widget tests

**Interfaces:**
- Picker mirrors the existing task picker's shape; the subtask list comes from the selected task's parsed `steps`; changing the task RESETS `subtaskId` to null.
- Money chip on the subtask row is display-only (like the 💬 comment badge added earlier — reuse that row-affordance pattern), gated on a `Map<String,double> expenseTotals` param defaulting to `const {}` so existing call sites compile unchanged.
- **NOTE the working-tree WIP:** the main checkout has uncommitted edits to `mobile/lib/models/task_project_link.dart` and `mobile/lib/screens/expenses/expense_detail_sheet.dart`. Do NOT revert or absorb them; work around them and report if they conflict.

- [ ] TDD per piece; `flutter test test/local/ test/screens/` + analyze. Commit `feat(mobile): attach an expense to a subtask + per-subtask totals`

---

### Task 9: Final verification, docs, version bump

- [ ] Backend: `python3 -m pytest tests/tasks/ tests/budgets/ tests/test_task_comment_routes.py -v` (container DOWN or targeted only).
- [ ] Mobile: `flutter analyze` (65 baseline) + `flutter test` — expect ONLY `expenses_range_filter_test.dart`; if `home_screen_test.dart` fails, re-run it ALONE (documented load-flaky) before reporting.
- [ ] `mobile/pubspec.yaml` → `1.25.0+126` AND `mobile/lib/core/constants/app_constants.dart` (`kAppVersion`/`kAppBuild`) in the SAME commit; then run `flutter test test/core/app_version_constants_test.dart`.
- [ ] DOCS.md: one tight subsection per phase. TODO.md: Phase 27 summary + tick what closed.
- [ ] Commit `chore(mobile): bump to 1.25.0+126; phase 27 notes`
