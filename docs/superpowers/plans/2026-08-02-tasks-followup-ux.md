# Tasks Follow-up UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project selection (chip + `/`-suggestions + create-if-missing) in Add Task, Add-link on subtask comments with splice clamp, a Settings Guide, and multiline subtask editing.

**Architecture:** Pure mobile/Flutter pass on top of `558072b`+. Reuses the existing project picker (`chip_edit.dart`), `AddProjectSheet`, offline-first `createProject`, the smart-add token parser, and the `_HelpStep` help-dialog pattern. No backend changes.

**Tech Stack:** Flutter (Riverpod), existing LazyClaw UI kit.

**Spec:** `docs/superpowers/specs/2026-08-02-tasks-followup-ux-design.md` — its Current-state facts section carries the file:line map; every fact there is binding context.

## Global Constraints

- Mobile-only; NO server/backend edits; NO web edits.
- Tasks link to projects by case-insensitive NAME (never id); `''` stays the clear sentinel, null = untouched.
- All project creation goes through `BudgetsNotifier.createProject` (offline-first outbox) — never a direct repository/API call.
- Comment cap `kMaxCommentChars` (2000, `models/comment.dart`) must never be exceedable client-side; refusals are LOUD (snackbar), never silent.
- Widget tests: plain callbacks/fakes, no real sqflite DB; provider tests may use the established in-memory ffi harness.
- Test gates per task: the task's test files, then the touched dir suite, then `flutter analyze` (baseline 65, zero new). One documented pre-existing failure exists: `test/screens/expenses_range_filter_test.dart`.
- Conventional commits, `(mobile)` scope, NO AI attribution.
- Follow existing house style (LzSection/LzListTile/LzBottomSheet/AppText/AppColors; immutable state updates).

---

### Task 1: Picker infrastructure — public `ProjectChip` + `allowCreate` on the picker

**Files:**
- Modify: `mobile/lib/screens/tasks/chip_edit.dart` (`ProjectPickResult` ~line 131, `showProjectPicker` ~line 142)
- Modify: `mobile/lib/screens/tasks/task_detail_sheet.dart` (delete private `_ProjectChip` ~line 1074-1135; use the public one)
- Test: `mobile/test/screens/project_picker_create_test.dart` (new)

**Interfaces:**
- Produces: `class ProjectChip` in `chip_edit.dart` — byte-equivalent move of `_ProjectChip` (same constructor params, same `Key('task-detail-project')` default overridable via a `fieldKey` param so the Add sheet can use `Key('add-task-project')`).
- Produces: `ProjectPickResult` gains `final bool createNew` (default false) + `const ProjectPickResult.createNew()` factory/named ctor; `showProjectPicker` gains `bool allowCreate = false` → when true renders a trailing `LzListTile` "＋ New project" (key `project-pick-create`) popping `ProjectPickResult.createNew()`.
- Detail sheet behavior unchanged (it passes `allowCreate: false` implicitly).

**Steps:**
- [ ] Write failing widget test: pump `showProjectPicker(allowCreate: true)` with 2 projects → "＋ New project" tile present; tapping pops a result with `createNew == true`; with `allowCreate: false` (default) the tile is absent; existing selection rows still pop the right category. Run → FAIL.
- [ ] Implement the `ProjectPickResult.createNew` flag + tile; extract `ProjectChip` (pure move + `fieldKey` param); update `task_detail_sheet.dart` to construct `ProjectChip` with its original key. Run new test + `flutter test test/screens/` (pre-existing failure only) + analyze → PASS.
- [ ] Commit: `refactor(mobile): public ProjectChip + create-new affordance in project picker`

---

### Task 2: `BudgetsNotifier.ensureProject`

**Files:**
- Modify: `mobile/lib/providers/budgets_provider.dart` (beside `createProject` ~line 142)
- Test: `mobile/test/providers/budgets_ensure_project_test.dart` (new; reuse the harness of the existing budgets provider tests under `mobile/test/providers/` — if none exists there, model on how `tasks_provider_test.dart` bootstraps its in-memory DAO)

**Interfaces:**
- Produces: `Future<void> ensureProject(String name)` on `BudgetsNotifier` — trims the name; no-op on empty; case-insensitive compare against `state.projects[*].name` (use `.toLowerCase()` on both sides, matching `_ProjectChip`'s precedent); when missing, awaits `createProject(trimmedName)`.

**Steps:**
- [ ] Write failing tests: (a) unknown name → a project with that name appears in state and an outbox create op is queued; (b) existing name in different case (`"GROCERIES"` vs stored `"Groceries"`) → no new project, no op; (c) empty/whitespace name → no-op. Run → FAIL.
- [ ] Implement. Run tests + analyze → PASS.
- [ ] Commit: `feat(mobile): BudgetsNotifier.ensureProject (case-insensitive get-or-create)`

---

### Task 3: Add Task sheet — projects param, PROJECT chip, effective category, auto-create

**Files:**
- Modify: `mobile/lib/screens/tasks/add_task_sheet.dart` (sheet + `showAddTaskSheet` ~line 808-831 + `_AddTaskResult` ~line 751)
- Modify: `mobile/lib/screens/tasks_screen.dart` (`_openAddSheet` ~line 367-390)
- Modify: `mobile/lib/screens/home_screen.dart` (add-task call ~line 91-107)
- Test: `mobile/test/screens/add_task_project_test.dart` (new)

**Interfaces:**
- Produces: `showAddTaskSheet(context, {projects: List<Project> = const []})`; sheet state `String? _category`, `bool _categoryTouched`; a "PROJECT" `_SectionLabel` + `ProjectChip` (key `add-task-project`) between the Priority and Due-date sections. Chip tap → `showProjectPicker(context, projects: widget.projects, allowCreate: true)`; a `createNew` result opens the existing `AddProjectSheet` (import from `screens/expenses/add_expense_sheet.dart`), whose `onSubmit` calls `ref.read(budgetsProvider.notifier).createProject(...)` and then sets `_category = name; _categoryTouched = true` — which requires the sheet to become a `ConsumerStatefulWidget` if it is not already (check; convert minimally if needed, or thread a `onCreateProject` callback from the caller if conversion is invasive — prefer whichever is the SMALLER diff and say which you chose).
- Effective category in `_submit()`: `_categoryTouched ? _category : parsed.project` → into `_AddTaskResult.category` (unchanged field). The chip's display also reflects a live-parsed token when `!_categoryTouched` (so typing `/gro` shows on the chip) — read the current parse in build.
- Callers: both pass `projects: ref.read(budgetsProvider).projects`; `home_screen` first fires the same best-effort lazy load `tasks_screen._ensureBudgetsLoaded` does (replicate the 3-line guard locally); both call `await ref.read(budgetsProvider.notifier).ensureProject(result.category!)` before `addTask(...)` whenever `result.category` is non-null/non-empty.

**Steps:**
- [ ] Write failing widget tests: (a) sheet with projects shows the PROJECT chip defaulting "No project"; (b) picking a project from the picker updates the chip; (c) with title `"buy paint /Groceries"` and NO manual pick, submit returns `category == 'Groceries'`... careful: the token regex is case-sensitive on content match — assert the raw token value; (d) manual pick beats token: pick "Casa" then type `/Groceries` → submit returns `'Casa'`. Run → FAIL.
- [ ] Implement sheet changes. Run new tests → PASS.
- [ ] Wire both callers (+ `ensureProject` await). `flutter test test/screens/ && flutter analyze` → PASS (pre-existing failure only).
- [ ] Commit: `feat(mobile): project chip + create-if-missing in Add Task`

---

### Task 4: `/` suggestion strip

**Files:**
- Modify: `mobile/lib/screens/tasks/add_task_sheet.dart` (strip widget + wiring under the title field ~line 273-281)
- Test: `mobile/test/screens/add_task_suggestions_test.dart` (new)

**Interfaces:**
- Produces: private `_ProjectSuggestionStrip` rendered under the title `LzTextField` when the live parse (`_onTitleChanged` state) has a project token AND the title focus node has focus (add a `FocusNode` to the title field if it lacks one). Rows (max 4): case-insensitive substring matches over `widget.projects` names — `ProjectColorDot` + name, key `project-suggest-<name>`; plus a trailing `Create project '<token>'` row (key `project-suggest-create`) when no case-insensitive EXACT match exists.
- Tap a match: remove the token (prefix char + token, collapse the leftover double space) from the title controller, set `_category = <exact project name>; _categoryTouched = true`.
- Tap create: `ref.read(budgetsProvider.notifier).createProject(token)` (or the Task-3 callback route if that was chosen), then same token-removal + `_category = token`.
- Token removal helper is a pure function `String removeProjectToken(String title)` (exported from `smart_add_parser.dart` or local to the sheet — put it where the token regex lives so they can't drift) with its own unit test.

**Steps:**
- [ ] Write failing tests: strip hidden with no token; typing `/gro` (2 matching projects "Groceries", "Grow lights", 1 non-match) shows exactly those 2 rows + no create row when an exact match exists for the full token? — careful: `gro` has no exact match → create row DOES show alongside matches; tapping "Groceries" clears the token from the title text and later submit carries `Groceries`; `removeProjectToken('buy paint /gro now') == 'buy paint now'`. Run → FAIL.
- [ ] Implement. Run tests + `flutter test test/screens/` + analyze → PASS.
- [ ] Commit: `feat(mobile): live project suggestions for /token in Add Task`

---

### Task 5: Subtask composer Add-link + splice clamp

**Files:**
- Modify: `mobile/lib/screens/tasks/task_comments_section.dart` (`showSubtaskCommentsSheet` ~line 69-85, `_SubtaskCommentsSheetBody` ~line 99-146, `_addLink` ~line 279-295)
- Modify: `mobile/lib/screens/tasks/task_detail_sheet.dart` (subtask-sheet call site ~line 885-897)
- Test: extend `mobile/test/screens/task_comments_section_test.dart`

**Interfaces:**
- Produces: `showSubtaskCommentsSheet(..., {Future<String?> Function()? onAddLink})` threaded to `_CommentsBody`; call site passes `onAddLink: () => showAddLinkDialog(context)`.
- `_addLink` clamp: compute `nextText`; if `nextText.length > kMaxCommentChars` → `ScaffoldMessenger.maybeOf(context)?.showSnackBar(const SnackBar(content: Text('Comment limit is 2000 characters.')))` and return without touching `_ctrl`.

**Steps:**
- [ ] Write failing tests: (a) subtask sheet body built WITH onAddLink shows the add-link icon (find `Icons.add_link`); (b) splice clamp: seed the composer near 2000 chars, invoke add-link with a stubbed `onAddLink` returning a link that would overflow → field text unchanged + snackbar text found; a non-overflowing insert still splices at the cursor. Run → FAIL.
- [ ] Implement both. Run the comments test file + `flutter test test/screens/` + analyze → PASS.
- [ ] Commit: `fix(mobile): add-link on subtask comments + clamp cursor-splice to 2000 chars`

---

### Task 6: Multiline subtask edit + Settings Guide

**Files:**
- Modify: `mobile/lib/screens/tasks/subtask_editor.dart` (edit TextField ~line 265-278)
- Modify: `mobile/lib/screens/settings_screen.dart` (new section before About ~line 545; reuse `_HelpStep` ~line 2182)
- Test: extend a subtask-editor test + `mobile/test/screens/settings_guide_test.dart` (new)

**Interfaces:**
- Subtask edit TextField: add `maxLines: null` (keep `textInputAction: TextInputAction.done` and the existing commit-on-done/unfocus behavior).
- Settings: `LzSection('Guide')` with one `LzListTile` "Tips & shortcuts" (key `settings-guide-tile`, `Icons.lightbulb_outline`) → `_showGuide()` opening the same dialog shape as `_showWidgetHelp` with these 6 `_HelpStep` rows verbatim:
  1. "Type /project or #project while adding a task — matching projects appear; no match offers Create."
  2. "Or tap the PROJECT chip in Add Task to pick or create one manually."
  3. "Links: paste a URL or use the Add-link button in notes and comments — [text](url) renders tappable."
  4. "Tap 💬 on a subtask for its own comment thread. Long-press any comment to delete it."
  5. "The eye icon in Projects view hides completed tasks; section headers collapse and remember their state."
  6. "Tap a task's notes preview to edit; links inside stay tappable."

**Steps:**
- [ ] Write failing tests: (a) subtask edit mode: enter edit on a long title → the TextField's `maxLines` is null (assert via `tester.widget<TextField>`); (b) settings: the Guide tile exists and tapping it shows a dialog containing "Tips & shortcuts" content (find one step's text). The settings screen may need its standard test harness — reuse whatever existing settings tests use; if none can pump the full screen cheaply, extract `_showGuide`'s dialog body into a testable widget and test THAT + the tile's presence via a scoped pump. Run → FAIL.
- [ ] Implement. Run tests + `flutter test test/screens/` + analyze → PASS.
- [ ] Commit: `feat(mobile): settings Guide tips + multiline subtask editing`

---

### Task 7: Final verification + version bump

**Files:**
- Modify: `mobile/pubspec.yaml` (1.24.0+123 → 1.24.1+124) AND `mobile/lib/core/` version constants (`kAppVersion = '1.24.1'`, `kAppBuild = 124` — the file `test/core/app_version_constants_test.dart` guards; bump BOTH in the same commit, then run that test)
- Modify: `TODO.md` (tick the two Phase 24 follow-ups this pass closes: add-link splice clamp + counter... only the splice one — verify which apply and tick only those; add a Phase 25 line for this pass)

**Steps:**
- [ ] Full `cd mobile && flutter analyze` (baseline 65, zero new) `&& flutter test` — expect ONLY the documented pre-existing `expenses_range_filter_test.dart` failure.
- [ ] Run `flutter test test/core/app_version_constants_test.dart` explicitly AFTER the version edits.
- [ ] Commit: `chore(mobile): bump to 1.24.1+124; tick phase-24 splice follow-up`

Deployment note for the controller (NOT this task's implementer): after merge — APK build via `scripts/build-mobile-apk.sh` for OTA; no `make rebuild` needed (zero backend changes).
