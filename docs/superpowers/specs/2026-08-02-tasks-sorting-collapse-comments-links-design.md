# Tasks Update: Sorting, Collapse, Comments, Links — Design

**Date:** 2026-08-02
**Scope:** Mobile-first (Flutter app + Python backend). Web UI untouched this pass; backend endpoints are built web-ready.
**Status:** Approved by user (design conversation 2026-08-02)

## Goals

1. **Sorting** — completed tasks and subtasks sink to the bottom of their lists.
2. **Collapse** — the mobile task screens remember what's expanded and can hide completed tasks.
3. **Comments** — timestamped comment threads on tasks AND subtasks, authored by the user or the agent.
4. **Links** — URLs and named links (`[text](url)`) render tappable in notes, comments, and subtask titles, with an "Add link" editor function.

## Current-state facts (from 2026-08-02 discovery audit)

- Mobile base task order is `created_at DESC, id ASC` (`mobile/lib/local/task_dao.dart:97-104`); the server orders priority → due_date → created_at (`lazyclaw/tasks/store.py:826-829`). No comparator anywhere consults done-ness.
- Mobile Projects view interleaves done tasks inside buckets (`tasks_project_view.dart:357-380`); subtasks render in raw array order (`subtask_editor.dart:61-68`).
- Projects-view expand state is ephemeral widget state (`tasks_project_view.dart:64-73`); List view collapses only the Done section (`tasks_screen.dart:771-784`).
- No comments exist anywhere. Closest precedent: encrypted `progress_log` JSON column, server-side only (`store.py:2075-2169`), NOT client-readable (not in `ENCRYPTED_FIELDS`, own AAD `_PROGRESS_LOG_AAD`).
- Steps codec strips unknown keys (`_normalize_steps`, `store.py:309-343`); mobile steps writes are lossy for unknown keys (`subtask.dart` serializes `{id,title,done}` only). Recurring respawn re-mints step ids and carries `{title, done:false}` only (`store.py:1585-1591`).
- No linkify anywhere on mobile; notes (description) are invisible outside the edit TextField (`task_detail_sheet.dart:446-454`). `url_launcher ^6.3.0` already a dependency; bare-URL regex precedent in `documents/univer_links.dart:147`.

## Feature A — Done-last sorting (mobile, display-only)

A shared, stable comparator utility (new `mobile/lib/screens/tasks/task_sort.dart`):

- `sortDoneLast(List<Task>)` — stable partition: pending tasks first (preserving their existing relative order), done tasks after (preserving theirs). Statuses rank: `todo`/`in_progress` = pending; `done` (and any terminal status) = bottom.
- `sortSubtasksDoneLast(List<Subtask>)` — same stable partition for checklists.

Applied at render time in:
- Projects view bucket lists (`tasks_project_view.dart`)
- Calendar day lists (`task_calendar_utils.dart` consumers)
- Subtask checklist in the detail sheet AND the inline TaskRow fold (`subtask_editor.dart` callers)

Rules:
- **Display-only.** The stored `steps` array order is never rewritten by sorting; unticking a subtask returns it to its original position. Task rows are likewise only re-ordered in the view layer.
- Ticking re-sorts immediately (state update → rebuild moves the item down).
- List view is untouched (Done is already a separate final section).

## Feature B — Collapse persistence + hide completed (mobile)

- **Projects view:** persist the expanded-bucket set across view switches and app restarts. Storage: SharedPreferences (the app's existing lightweight prefs mechanism), key e.g. `tasks.projects.expanded` (JSON list of bucket names). Default stays all-collapsed.
- **Projects view:** new **"Hide completed"** toggle (persisted, e.g. `tasks.projects.hideCompleted`, default off). When on, done tasks are filtered out of bucket bodies; the `open/total` header badge remains so counts stay visible.
- **List view:** Overdue / Today / Upcoming sections get the same chevron collapse the Done section has; collapsed-state persisted per section (`tasks.list.<section>.collapsed`). Defaults: Overdue/Today/Upcoming expanded, Done collapsed (current behavior).

No backend involvement — purely client-local UI state.

## Feature C — Comment threads on tasks and subtasks

### Data model

New encrypted JSON list column `comments` on the `tasks` row (the proven steps/tags pattern):

```json
[{"id": "c-<uuid>", "ts": "<ISO-8601 UTC>", "author": "user" | "agent", "text": "<= 2000 chars", "subtask_id": "s..." | null}]
```

- `subtask_id: null` → task-level comment; set → thread of that subtask.
- Cap: **500 comments per task** (server rejects beyond; progress_log caps at 200 by silent trim — comments instead fail loudly with a clear error).
- Ordering: append-only, chronological.

### Backend

- `comments` added to `TASK_COLUMNS` and to `ENCRYPTED_FIELDS` / `_JSON_LIST_ENCRYPTED_FIELDS` (`store.py:21/27`) so it decrypts into list/get/changes payloads — deliberately unlike `progress_log`, because clients must read it.
- **Respawn classification (mandatory guard):** `comments` goes into `_RESPAWN_RESET_COLUMNS` — comments stay with the completed occurrence; the fresh occurrence starts clean (same rule as `progress_log`). This also neutralizes the re-minted-step-id problem: old comments' `subtask_id`s stay valid on the old row. The classification test enforces this at CI time.
- Store methods: `add_comment(user_id, task_id, text, author, subtask_id=None) -> comment` (server-mints id + ts, appends under the DB write path atomically, bumps `updated_at`), `delete_comment(user_id, task_id, comment_id) -> bool` (bumps `updated_at`).
- REST: `POST /api/tasks/{id}/comments` (body `{text, subtask_id?}`, author forced to `user`), `DELETE /api/tasks/{id}/comments/{comment_id}`. Steps-style validation: text required, 1–2000 chars, `subtask_id` must exist in the task's current steps if provided. Comments ride the existing `GET /api/tasks/changes` feed inside the task dict — **no new sync cursor** (the budgets-v9 shared-cursor trap is avoided entirely).
- **Agent skill:** new `add_task_comment(task_ref, text, subtask_ref=None)` builtin skill (author=`agent`). On agent-authored comments, drop a **quiet** notification-spine feed entry (`notify()`, no Telegram push).
- `update_task`/PATCH deliberately do NOT accept `comments` (append/delete endpoints only — no full-replace surface to lose data through).

### Mobile

- Local DB: `kAppDbVersion` bump + migration branch adding `task_cache.comments TEXT`; DAO read/write mapping; `Task.comments` raw string + parsed getter (tolerant codec like `parseSubtasks`).
- **Outbox:** new op types `comment_add` / `comment_delete` (replayed as the POST/DELETE endpoints — append semantics, so offline comments from two devices merge instead of last-write-wins). Optimistic UI: client-minted temp id, reconciled from the server response / next pull.
- Sync pull: comments arrive inside the task row via existing task sync — DAO stores verbatim.
- UI: **Comments section** in the task detail sheet (thread list — author label + relative timestamp + text — plus an input row with Send). Each subtask row gets a 💬 icon with count; tapping opens a mini-sheet with that subtask's thread + input. Long-press a comment → delete (confirm).

## Feature D — Tappable links + "Add link"

- New shared widget `mobile/lib/widgets/link_text.dart` (`LinkText`): parses **bare URLs** (regex adapted from `univer_links.dart:147` incl. trailing-punctuation trimming) and **named markdown links** `[text](url)`; renders them as tappable spans (`TapGestureRecognizer` → `launchUrl(..., mode: LaunchMode.externalApplication)`, failure snackbar — pattern from `sheet_link_ui.dart:198-207`). Everything else renders as plain text (no full markdown engine this pass).
- Applied to:
  - **Notes:** the detail sheet gains a **read-only notes preview** (LinkText) shown by default; tapping non-link text switches to the existing edit TextField. Link taps do NOT enter edit mode.
  - **Comments:** comment text renders through LinkText.
  - **Subtask titles:** rendered through LinkText; link taps open the URL, taps elsewhere keep current toggle/edit behavior.
- **"Add link" button** in the Notes editor and the comment input: small dialog (display text + URL, URL validated `https?://`) → inserts `[text](url)` at the cursor.
- Backend: no changes — title/description/steps/comments already accept arbitrary text.

## Error handling

- Comment add/delete failures surface a snackbar and keep the outbox op queued (offline-first retry, standard outbox behavior).
- Tolerant parse on comments JSON (malformed → `[]`, never crash), matching `parseSubtasks`.
- Link launch failure → snackbar ("Could not open link").
- Server: comment endpoints return 404 (task not found), 400 (validation), 409 (cap reached) with clear messages.

## Testing

- **Backend (pytest, container DOWN — prod-DB rule):** store tests for add/delete/cap/subtask_id validation/encryption round-trip; respawn-classification test updated for `comments`; route tests for POST/DELETE; changes-feed includes decrypted comments; agent-skill test.
- **Mobile:** model tests (comment codec, sort comparators — stable partition property), DAO/migration test, outbox replay test, widget tests for detail-sheet comments + LinkText tap spans + collapse persistence (mock SharedPreferences; avoid real sqflite in widget tests — FakeAsync hang gotcha).
- Coverage target per repo rules: 80%+ on new code.

## Out of scope (explicit)

- Web UI changes (endpoints are web-ready; a later pass wires the React side).
- Task-title linkification, drag-to-reorder, server ORDER BY changes, full markdown rendering on mobile, editing comments (add/delete only), carrying comments across recurring occurrences.

## Deployment

- Server: `make rebuild` (image is baked; source not mounted).
- Mobile: version bump + APK build per usual flow.
