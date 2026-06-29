# Tasks UX Pass — Smart Fast Reschedule + Persistent Lists

**Date:** 2026-06-25
**Branch:** `feat/tasks-reschedule-and-lists`
**Status:** Approved (user, 2026-06-25)

Two independent Tasks-domain improvements that ship together.

---

## Feature A — Smart Fast Reschedule

### Problem

Overdue tasks already group into their own section on **both** web
(`web/src/pages/Tasks.tsx` → `groupByDueBucket()` "Overdue" group) and mobile
(`mobile/lib/screens/tasks_screen.dart` → `_groupTasks()` overdue section). What's
missing is a *fast* way to clear them:

- **Mobile** has **no quick-reschedule flow at all** — only a bare tap-the-chip
  date picker and Today/Tomorrow/Pick chips in the detail sheet, with no smart
  time default (the time picker seeds to 9am only when opened manually).
- **Web** has `TaskRescheduleInput.tsx` (quick picks `+1h / +3h / tom / +1w`,
  free-form NL, "smart" LLM mode) but its day-level picks default to **9am**, and
  there's no one-tap date grid.

User wants: tap a date → it lands on that day **at 10:00 AM** ("boom"), unless a
custom time is chosen.

### The core rule (identical on web + mobile)

Rescheduling a task to a target day sets:

- `due_date` = that day, **date-only `YYYY-MM-DD`** — keeps backend bucket/overdue
  queries correct (they compare `due_date <= today_str` lexically; a datetime in
  this column would break the comparison).
- `reminder_at` = that day **at the chosen time, default `10:00` local** (ISO
  datetime) — this carries the "10am" and is what fires the nag.

Tapping a date applies immediately at 10am. A "Time: 10:00 AM · change" row lets
the user override the time *before* tapping a date; untouched ⇒ 10am.

### Quick-date math (shared helper, unit-tested both platforms)

Computed in the **user's local time**:

- **Tomorrow** = today + 1 day.
- **This weekend** = the coming Saturday. If today *is* Saturday → today; if
  Sunday → next Saturday (6 days out).
- **Next week** = the coming Monday. If today *is* Monday → next Monday (7 days
  out), else the next Monday strictly after today.
- **Pick a date…** = calendar picker.

Each resolves to `{ day: "YYYY-MM-DD", reminderAt: "YYYY-MM-DDT10:00:00" }`
(time overridable).

### Why deterministic PATCH, not the NL endpoint

Quick-date chips compute the date **client-side** and call `PATCH /api/tasks/{id}`
with `{ due_date, reminder_at }` rather than posting a phrase to
`POST /api/tasks/{id}/reschedule`. Reasons:

- Deterministic — no dependency on `nl_time.parse`'s vocabulary ("this weekend",
  "next Monday" parsing is unverified).
- Identical semantics on web and mobile.
- Works offline on mobile through the outbox (the NL endpoint is online-only).

The backend already does the right thing on a `reminder_at` PATCH
(`tasks/store.py:update_task`): deletes the old reminder job, creates a fresh one,
recomputes `reminder_offset_minutes`, and resets nag escalation
(`nag_count = 0`, `nag_fired_at = NULL`). **No backend change required.** The
existing `/reschedule` NL endpoint stays for typed free-form input.

### Web

- Extend `web/src/components/tasks/TaskRescheduleInput.tsx` with a quick-date chip
  row (Tomorrow · This weekend · Next week · Pick a date…). These compute the date
  client-side and PATCH `{ due_date, reminder_at }` via the existing `api.ts`
  task-update function.
- Shift the existing day-level NL quick picks' default time **9am → 10am**.
- New pure helper `web/src/components/tasks/rescheduleDates.ts` (the date math).
- `formatDueChip(dueDate, reminderAt)` already reads `reminder_at`, so cards show
  "Tomorrow 10:00 AM" with no extra work.

### Mobile (the main effort)

- **New** `mobile/lib/screens/tasks/reschedule_sheet.dart` — an `LzBottomSheet`
  ("Reschedule") containing:
  - a time row: "Time: 10:00 AM · change" → `showThemedTimePicker` (seed 10:00),
  - the four quick-date chips (`LzChip`/`LzListTile`). A single tap applies the
    pick with the current time (default 10am) and closes the sheet.
  - "Pick a date…" → `showThemedDatePicker`, then applies at the chosen time.
- **New** pure helper `mobile/lib/core/reschedule_dates.dart` (Tomorrow / This
  weekend / Next week math + `composeAt(day, time)`), mirroring the TS helper.
- Writes via `tasksProvider.notifier.updateTask(id, dueDate: <day>,
  reminderAt: <day>T<time>)` → optimistic cache + outbox + best-effort sync, and
  reschedules the local notification (`_reminders?.scheduleForTask`). Works
  offline.
- **Entry points:** tapping the due-chip on an **overdue** `task_row.dart` card
  opens this sheet (today it opens the bare date picker); plus a "Reschedule"
  button in `task_detail_sheet.dart`.
- **Display tweak:** the mobile card due-chip shows the `reminder_at` time when
  `dueDate` is date-only, so "10:00 AM" is visible. Small helper in
  `mobile/lib/core/due_date.dart`.

### Testing (Feature A)

- `rescheduleDates` pure helper unit-tested on both platforms (Dart `test/`, TS):
  Tomorrow / Weekend / Next-week resolve to the correct day at 10:00, including
  the today-is-Saturday and today-is-Monday edge cases.
- Mobile: a flow test that tapping a chip enqueues an outbox PATCH with the
  expected `due_date` (date-only) + `reminder_at` (day @ 10:00).

### Feature A.2 — Dedicated Overdue view + Reschedule-all (added 2026-06-26)

User opted overdue-rework back in. Two additions on top of A:

**Mobile — dedicated Overdue view (full control):**
- Add **Overdue** as a first-class view segment in `tasks_screen.dart`, a peer of
  the existing List · Calendar · Projects view modes (not just the inline section).
- The view shows only overdue tasks (dueDate's day < today, not done), respecting
  the active owner filter (All · Mine · AI), reusing the normal task rows so every
  per-task control still works: tap-the-due-chip → reschedule sheet, complete,
  open detail, swipe.
- A header bar: "N overdue · **Reschedule all →**". Empty state when none.
- **Reschedule-all** = the same reschedule sheet, generalized to apply to a *set*
  of task ids: pick a date once (default Tomorrow 10am, overridable time) → applies
  `due_date` + `reminder_at` to every overdue task at once.
- Generalize `reschedule_sheet.dart` to accept `List<String> taskIds` + an optional
  heading (single-task callers pass `[task.id]`). New provider method
  `TasksNotifier.rescheduleMany(ids, {dueDate, reminderAt})` — batched local
  updates + one refresh + per-task notification reschedule + one sync (avoids N
  refresh/sync churn). Backend unchanged (bulk = N PATCHes through the outbox).

**Web — parity:**
- Add a **Reschedule all** control to the existing Overdue group header in
  `Tasks.tsx`: a small quick-date menu (Tomorrow · This weekend · Next week · Pick)
  reusing `rescheduleDates.ts`; on choice, PATCH `due_date` + `reminder_at` for
  every overdue task in the group (loop the existing `updateTask`).

**Testing (A.2):** mobile — `rescheduleMany` applies the target to all ids and
leaves non-overdue untouched; the Overdue segment filters correctly. Web — typecheck.

---

## Feature B — Persistent Lists (grocery / home)

### Problem (current behavior, verified)

When the user tells the AI "add milk to my grocery list":

- `add_task` (`skills/builtin/task_manager.py:252`) **always creates a new task**.
  No fuzzy-find, no dedup, no append — it only checks the title is non-empty.
- Three such messages → **three separate tasks** ("milk", "eggs", "butter"), and
  on the 3rd, the materialize threshold (`tasks/store.py`
  `_PROJECT_MATERIALIZE_THRESHOLD = 3`) silently spins up a **"Grocery" project**.
  Result: task spam + an accidental project.

The right model already half-exists but isn't reachable from the agent:

- A task carries an encrypted `steps[]` (checkable sub-items) — that *is* a list.
  Web/mobile already render and toggle subtasks.
- SOUL.md already says "Enumerated items → `steps`, not the description."
- `_fuzzy_match_task()` (`task_manager.py:210`) finds a task by partial name.
- Store has `set_steps()` (replace-all) and `toggle_step()` — but **neither is an
  agent skill**, and there is no `append_step`.

### Gaps

1. No append-to-list agent skill (find-or-create list task, append item to steps).
2. No find-or-create for a persistent named list.
3. No SOUL.md routing so "add X to my grocery list" appends instead of `add_task`.
4. (Minor) smart-intake has no list-intent awareness — out of scope; the routing
   rule + skill are sufficient.

### Design

**New store helper** — `tasks/store.py:append_steps(config, user_id, task_id, items)`:

- Fetch the task, parse current steps.
- Append each item as a new `{id, title, done: false}` step, **skipping** any whose
  title already exists (case-insensitive, trimmed) — dedup.
- Persist via the existing `set_steps()` path (encrypted JSON). Immutable: builds a
  new list, returns the normalized result. Returns `None` if the task is missing.

**New agent skill** — `add_to_list` (in a new `skills/builtin/list_manager.py`):

- Params: `list` (string, e.g. "Grocery" / "Home" / "Shopping"), `items`
  (array of short strings).
- Behavior:
  1. **Ensure the project** named `list` exists — reuse `create_project` so the
     list lives in **one private project immediately** (no waiting for the 3-task
     threshold).
  2. **Find-or-create the single list task** in that project: fuzzy-match an open
     (`status in (todo, in_progress)`) task whose title matches the list name
     (e.g. "Grocery list" / "Grocery for this week"); if none, create exactly one
     task titled `"<List> list"` with `category=<list>`.
  3. **Append** the items as subtasks via `append_steps` (deduped).
  4. Return short: `"Added milk, eggs to Grocery — 7 items."` (or, on full dedup,
     `"Already on the Grocery list: milk."`).
- Category `lists` (or reuse `task`) → default **ALLOW**.

**Companion skill** — `check_off_list_item` (same module):

- Params: `list`, `item`. Fuzzy-find the list task, fuzzy-match the step by title,
  flip it done via the existing `toggle_step()` store function. "got the milk" →
  ticks milk off. *Remove-item is deferred.*

**SOUL.md routing rule** (under "Task Manager — Personal Second Brain"):

- "add X to my grocery/shopping/home list", "we're out of Y", "put Z on the list"
  → `add_to_list(list=<name>, items=[...])`, **never** `add_task` per item; dedup,
  never spawn one task per item.
- "got/bought/have X" against a known list → `check_off_list_item`.

**No UI change.** Typing subtasks into the Grocery task already works on web/mobile
(`PUT /api/tasks/{id}/steps`); this fixes only the AI path.

### Testing (Feature B)

- `append_steps`: appends new items, dedups existing (case-insensitive), no-op when
  all duplicates, returns `None` for a missing task, leaves other fields untouched.
- `add_to_list`: first call creates exactly one project + one list task with the
  items as steps; second call (different items) appends to the *same* task —
  asserts **no second task and no second project** are created; dedup honored.
- `check_off_list_item`: flips the matching step's `done` flag; unknown item →
  clear message, no mutation.

---

## Files touched (summary)

**Backend**
- `lazyclaw/tasks/store.py` — add `append_steps()`. (Feature A needs nothing here.)
- `lazyclaw/skills/builtin/list_manager.py` — **new**: `add_to_list`,
  `check_off_list_item`.
- skill registry wiring (`skills/registry.py` or equivalent) for the two skills.
- `personality/SOUL.md` — routing rules for lists.
- Tests under `tests/` for `append_steps`, `add_to_list`, `check_off_list_item`.

**Web**
- `web/src/components/tasks/TaskRescheduleInput.tsx` — quick-date chips, 10am
  default.
- `web/src/components/tasks/rescheduleDates.ts` — **new** date helper (+ test).

**Mobile**
- `mobile/lib/screens/tasks/reschedule_sheet.dart` — **new** bottom sheet.
- `mobile/lib/core/reschedule_dates.dart` — **new** date helper (+ test).
- `mobile/lib/screens/tasks/task_row.dart` — overdue due-chip opens the sheet.
- `mobile/lib/screens/tasks/task_detail_sheet.dart` — "Reschedule" button.
- `mobile/lib/core/due_date.dart` — show `reminder_at` time on date-only cards.

---

## Out of scope (per user)

- ~~Overdue-view restructuring~~ — **now in scope** as Feature A.2 (dedicated
  Overdue view segment + Reschedule-all, both platforms).
- New backend `overdue` list bucket — not needed (clients already group overdue;
  bulk reschedule is N client-side PATCHes).
- List features: weekly auto-reset of "this week's" list, and list item **removal**
  — deferred follow-ups.
- Smart-intake list-intent detection — the routing rule + skill cover it.
