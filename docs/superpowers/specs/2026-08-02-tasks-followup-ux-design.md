# Tasks Follow-up UX: Project Select, /-Suggestions, Comment Links, Guide, Subtask Edit — Design

**Date:** 2026-08-02
**Scope:** Mobile (Flutter) only. Builds on the tasks pass merged at `558072b`.
**Status:** Approved (design conversation 2026-08-02)

## Goals

1. **Project select in Add Task** — the Add Task sheet gets a PROJECT chip + picker (parity with the detail sheet), including a create-new affordance.
2. **`/project` live suggestions + create-if-missing** — typing `/name` or `#name` in the title shows matching real projects; tapping completes; no match offers "Create project"; an unmatched token at submit auto-creates the project instead of today's silent phantom-tag bucket.
3. **Add-link on subtask comment composer** — thread the existing `onAddLink` through `showSubtaskCommentsSheet`; also clamp the Add-link cursor-splice so it can't exceed `kMaxCommentChars` (2000) — refused with a snackbar, never silently dropped.
4. **Settings Guide** — a new "Guide" tile in Settings opening a static tips dialog (`_HelpStep` pattern): `/`+`#` project typing, `[text](url)` + Add-link, task/subtask comments (💬, long-press delete), hide-completed + collapsible sections.
5. **Subtask edit is multiline** — the inline edit `TextField` gets `maxLines: null` (title wraps while editing; Done still commits). Display mode already wraps — no change there.

## Current-state facts (2026-08-02 discovery)

- Add Task sheet (`add_task_sheet.dart`) has NO project control; the only project path is the smart-add token `[#/]([A-Za-z0-9_-]+)` (`smart_add_parser.dart:548`) → `_AddTaskResult.category`; free-form, unvalidated — typos land in the "Tags" bucket (`splitTasksByGroup`).
- Detail sheet has `_ProjectChip` (private, `task_detail_sheet.dart:1074-1135`) → `showProjectPicker` (`chip_edit.dart:142-198`, shared with `task_row.dart:214`); no create affordance in any picker. Project creation UI: `AddProjectSheet` (`screens/expenses/add_expense_sheet.dart:234`) via `BudgetsNotifier.createProject` (`budgets_provider.dart:142-166`, offline-first through `applyLocalProjectCreate` + outbox).
- Projects list: `ref.watch(budgetsProvider).projects`; tasks_screen lazy-loads via `_ensureBudgetsLoaded` (`tasks_screen.dart:348-352`); `home_screen.dart:91-107` also calls addTask (without recurring params) and does NOT load budgets.
- Tasks link to projects by case-insensitive NAME, not id (`task_detail_sheet.dart:1090`). `''` = clear sentinel; null = untouched.
- No autocomplete machinery app-wide; precedent for an inline suggestion strip: `SheetFormulaHelper` (`sheet_formula_bar.dart:128-159`) — plain bounded ListView under the field, no Overlay.
- Subtask comment composer supports `onAddLink` (`task_comments_section.dart:157/163/246/249/315-322`) but `showSubtaskCommentsSheet` (`:69-85`) and `_SubtaskCommentsSheetBody` (`:99-113`) don't thread it; task-level is wired at `task_detail_sheet.dart:913`.
- `_addLink` splice (`task_comments_section.dart:288-294`) sets `_ctrl.value` directly → bypasses `LengthLimitingTextInputFormatter`; provider clamp then silently drops the comment.
- Settings (`settings_screen.dart:471-551`) has no help surface; precedent = `_showWidgetHelp` (`:410-449`) with `_HelpStep` rows (`:2182-2219`).
- Subtask display titles wrap unbounded (LinkText, no maxLines); EDIT TextField (`subtask_editor.dart:265-278`) has default `maxLines: 1` → long titles unreadable while editing.

## Feature 1 — PROJECT chip in Add Task

- Extract the detail sheet's private `_ProjectChip` into a public `ProjectChip` in `chip_edit.dart` (pure move + rename; detail sheet reuses it — no behavior change there).
- `showAddTaskSheet`/`AddTaskSheet` gain `projects: List<Project>` (default `const []`). Both callers pass `ref.read(budgetsProvider).projects`; `home_screen` additionally fires the same lazy `load()` tasks_screen uses before opening the sheet (best-effort).
- New "PROJECT" section (label + `ProjectChip`) in the sheet between Priority and Due date. State: `String? _category`, `bool _categoryTouched`.
- `showProjectPicker` gains `allowCreate: bool = false` → when true, a trailing "＋ New project" `LzListTile` pops `ProjectPickResult.createNew` (new sentinel flag on the result class). On that result the Add sheet opens the existing `AddProjectSheet`; on submit it calls `BudgetsNotifier.createProject(...)` and sets `_category` to the new name.

## Feature 2 — `/` suggestions + create-if-missing

- **Effective category at submit:** `_categoryTouched ? _category : parsed.project` — a manual chip pick wins over the typed token.
- **Suggestion strip** (`_ProjectSuggestionStrip`, new widget in `add_task_sheet.dart`): rendered directly under the title field whenever the live-parsed title contains a project token AND the title field has focus. Contents: up to 4 case-insensitive substring matches of real project names (each row: `ProjectColorDot` + name), plus — when no exact case-insensitive match exists — a final "Create project '<token>'" row.
  - Tapping a match: REMOVES the token text (incl. its `#`/`/` prefix, collapsing double spaces) from the title controller and sets `_category`/`_categoryTouched` — this sidesteps the token regex's no-spaces limit for names like "Casa Woodwork".
  - Tapping "Create…": creates via `BudgetsNotifier.createProject(name)` (name = raw token), removes the token, sets `_category`.
- **Auto-create at submit:** `BudgetsNotifier` gains `Future<void> ensureProject(String name)` — case-insensitive check against `state.projects`; creates when missing; no-op otherwise. Both addTask call sites await `ensureProject(effectiveCategory)` before `addTask(...)` when a category is set. This converts the phantom-tag path into a real project.

## Feature 3 — Subtask composer Add-link + splice clamp

- Thread `Future<String?> Function()? onAddLink` through `showSubtaskCommentsSheet` → `_SubtaskCommentsSheetBody` → `_CommentsBody`; call site (`task_detail_sheet.dart` subtask-sheet opener) passes `() => showAddLinkDialog(context)`.
- `_addLink` clamp: if `nextText.length > kMaxCommentChars`, do NOT splice — show `SnackBar('Comment limit is 2000 characters.')` and leave the field untouched.

## Feature 4 — Settings Guide

- New `LzSection('Guide')` immediately before About: one `LzListTile` "Tips & shortcuts" (lightbulb icon) → `LzDialog` with 6 `_HelpStep` rows: (1) `/project` or `#project` while adding a task — matches or creates; (2) PROJECT chip to pick manually; (3) `[text](url)` in notes/comments renders tappable — use the Add-link button; (4) 💬 on a subtask opens its thread; long-press a comment to delete; (5) eye icon hides completed tasks; section headers collapse and are remembered; (6) tap a task's notes preview to edit.

## Feature 5 — Multiline subtask edit

- `subtask_editor.dart` edit-mode `TextField`: `maxLines: null` (keep `textInputAction: TextInputAction.done` → Done commits; titles stay newline-free via the existing trim-on-commit).

## Error handling

- `ensureProject`/`createProject` failures surface via the existing BudgetsState error path; addTask proceeds with the category string regardless (worst case = today's tag behavior, never a lost task).
- Splice clamp: explicit snackbar, never silent.

## Testing

Widget/unit tests per feature: picker create-row + `ProjectPickResult.createNew`; `ensureProject` (creates when missing, case-insensitive no-op); Add-sheet chip + effective-category precedence + token-removal-on-tap; suggestion strip matching/create rows; subtask-sheet Add-link button present + splice clamp refusal; Guide tile opens dialog; multiline edit field accepts wrap. Full `flutter test` gate at the end (1 documented pre-existing failure) + `flutter analyze` baseline 65.

## Out of scope

Web UI; server changes (none needed — category remains a name string); Overlay-based autocomplete; project rename/merge.
