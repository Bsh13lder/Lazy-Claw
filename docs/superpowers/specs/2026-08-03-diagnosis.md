# Diagnosis: missing dated/recurring tasks; expense-on-subtask feasibility

**Date:** 2026-08-03. Produced by a 4-agent discovery pass (calendar / widget / expenses probes + adversarial verifier). Every claim below carries a file:line from the live tree at main @ dd1b2b2, and the calendar/widget probes queried the live prod DB read-only.

## Probe: calendar

## 1. Full trace: tasks list → rendered day cell

**Source list** — `mobile/lib/screens/tasks_screen.dart:563` `final visibleTasks = filterByOwner(state.tasks, _ownerFilter)` → `:626` `_buildCalendarBody(state, visibleTasks, projects)` → `:700` `TaskCalendarView(tasks: visibleTasks, ...)`.
- `state.tasks` comes from `mobile/lib/providers/tasks_provider.dart:450` `_dao.list()`, which is `mobile/lib/local/task_dao.dart:100-106`: `where: 'deleted = 0'`, `orderBy: 'created_at DESC, id ASC'` — **no status filter, no limit**.
- `filterByOwner` (`mobile/lib/screens/tasks/task_owner_filter.dart:54-63`) returns the list **unchanged** for the default `TaskOwnerFilter.all` (`tasks_screen.dart:173`). No search-query filter and no "hide done" filter exists on the calendar path at all.
- **Done tasks ARE passed to the calendar** and ARE plotted (see §4 below). Verified in DB: all 10 open tasks are `owner='user'`, so the owner filter is not implicated.

**Keying** — `mobile/lib/screens/tasks/task_calendar_utils.dart:18-34`:
```
:21-22  final raw = task.dueDate; if (raw == null || raw.isEmpty) continue;   // undated → dropped
:24-28  try { parsed = DateTime.parse(raw); } catch (_) { continue; }          // unparseable → SILENTLY dropped
:29     final key = DateTime(parsed.year, parsed.month, parsed.day);           // ← NO .toLocal()
:33     return out.map((day, tasks) => MapEntry(day, sortDoneLast(tasks)));    // reorder only
```
**Lookup** — `mobile/lib/screens/tasks/task_calendar_view.dart:50` builds the map once per build; `:53-54` `grouped[DateTime(day.year, day.month, day.day)]`.
- table_calendar 3.2.0 hands `eventLoader` **UTC** grid days (`~/.pub-cache/.../table_calendar-3.2.0/lib/src/shared/utils.dart:45-46` `normalizeDate → DateTime.utc(...)`). Line 54 re-constructs a *local* `DateTime` from those components, which is what makes the map hit. I probed this: `grouped[DateTime(d.year,d.month,d.day)] → hit`, `grouped[utcDay] → null` (Dart's `DateTime.==` compares `isUtc` too). **Fragile but currently correct** — anyone "simplifying" line 54 to `grouped[day]` silently blanks the entire calendar.
- Marker render: `task_calendar_view.dart:149-155` `markerBuilder`. `markersMaxCount: 0` at `:146` does **not** suppress it — `table_calendar.dart:651-694` calls `calendarBuilders.markerBuilder` first and only falls back to the `markersMaxCount` Row when the builder returns null.

## 2. Regression check on 63bd69e / 83e4099 — BOTH INNOCENT

- `git show 63bd69e -- mobile/lib/screens/tasks/task_calendar_utils.dart`: the only change is `+import 'task_sort.dart'` and `-return out;` → `+return out.map((day, tasks) => MapEntry(day, sortDoneLast(tasks)));`. `sortDoneLast` (`mobile/lib/screens/tasks/task_sort.dart:7-10`) is a **total** partition — `[for t if !t.isDone] + [for t if t.isDone]` — every element lands in exactly one branch. No drop, no filter, keys untouched.
- `git show 83e4099 -- .../task_calendar_view.dart`: **one line**, `+key: ValueKey('calendar-task-${dayTasks[i].id}')` at `task_calendar_view.dart:197`. Render-identity only.
- `flutter test test/screens/task_calendar_utils_test.dart test/screens/tasks_calendar_smoke_test.dart test/screens/task_sort_test.dart` → **28 passed**.

## 3. Recurring tasks are NOT excluded anywhere

`grep -rn recurring` across `tasks_screen.dart`, `task_calendar_view.dart`, `task_calendar_utils.dart`, `task_project_grouping.dart` yields exactly one hit: `tasks_screen.dart:394` (create-time pass-through). The calendar plots whatever row exists; a respawned occurrence is just a new row with a new id.

## 4. Confirmed calendar defects

**D1 — missing `.toLocal()` (real, latent).** `task_calendar_utils.dart:25/29` reads `.year/.month/.day` off a possibly-UTC `DateTime`. Probe run under `TZ=Europe/Madrid`:
```
'2026-08-04T00:00:00+02:00' → isUtc=true → KEY 2026-08-03   (correct: 2026-08-04)
'2026-08-03T22:00:00+00:00' → isUtc=true → KEY 2026-08-03   (correct: 2026-08-04)
'2026-08-04T00:00:00'       → isUtc=false→ KEY 2026-08-04   ✓
'2026-08-05'                → isUtc=false→ KEY 2026-08-05   ✓
```
This shape **is** emitted by the server: `lazyclaw/tasks/store.py:552` `due_out = due_local.astimezone(timezone.utc).isoformat()` whenever a recurring template's `due_date` was tz-aware. It also **contradicts `mobile/lib/core/due_date.dart:31`**, which *does* call `.toLocal()` — so the row's time chip and the calendar bucket can disagree by a day on the same task.

**D2 — silent drop.** `task_calendar_utils.dart:26-27` swallows every parse failure with a bare `continue` and no `debugPrint`. Any malformed/`''`/whitespace `due_date` vanishes from the calendar with zero diagnostics.

**D3 — "all-done day" marker is near-invisible.** `task_calendar_view.dart:258-263` short-circuits to `_AllDoneBadge` (`:348-368`): a **13×13** circle at `AppColors.success.withValues(alpha: 0.18)` with a **9px** check — instead of colored dots. In the live DB every dated day before 2026-08-02 is **100% done** (see §5), so the user's entire calendar history renders as faint translucent badges and reads as empty.

## 5. Server data is HEALTHY — the drop is not in the grouping code

`sqlite3 "file:data/lazyclaw.db?mode=ro"` (read-only, user `blck` = `a7ac3e09-…`):
```
310f2d7b  user  2026-08-02          cron 0 9 * * 1   todo
82f6d048  user  2026-08-03                           todo
9d0a948c  user  2026-08-03T08:00:00 cron 0 8 * * *   todo
eaea94bd  user  2026-08-04T00:00:00 cron 0 23 * * *  todo
1c4af205  user  2026-08-05                           todo
+ 5 undated open tasks
```
All naive-local or date-only → **all five bucket correctly** through `groupTasksByDay` (probe above). `due_date` is plaintext (0 rows `like 'enc:%'`), `updated_at` is uniformly `+00:00`-aware (100/100 rows) so the delta cursor compare in `lazyclaw/tasks/store.py:get_task_changes` is lexically consistent, and `TASK_SELECT = ", ".join(TASK_COLUMNS)` (`store.py:100`) so the `deleted_at` index used at tombstone-classification time is aligned (no live-row-as-tombstone bug).

Per-day dated totals (`n | done`): `08-05 1|0`, `08-04 1|0`, `08-03 3|1`, `08-02 4|3`, `08-01 1|1`, `07-31 2|2`, `07-30 2|2`, `07-29 5|5`, `07-28 6|6`, `07-27 6|6`, `07-26 5|5`, `07-25 6|6`.

## 6. Shared calendar ↔ widget input (why the two fail together)

The widget reads the **same** local cache: `tasks_provider.dart:456` `updateTasksWidget(tasks)` off `_dao.list()`, and `background_sync.dart:99` `updateTasksWidget(await TaskDao(db).list())`. `home_widget_tasks.dart:105-116` classifies a task as *undated* exactly when `DateTime.tryParse(dueDate)` is null. So calendar + widget both lose a task **iff** its cached `due_date` is null / empty / unparseable, **or** the row is absent from `task_cache` — while the List view still shows it (`tasks_screen.dart:125-136` dumps undated/unparseable into "Upcoming"). That asymmetry is the exact signature the user described.

Client write paths were audited and all preserve `due_date`: `task_dao.dart:245/275` (create), `:309-314/345-346/396` (the `''` clear sentinel correctly stores NULL locally and rides the outbox verbatim), `:185` `_rowFromTask` on server upsert, `:864` `_taskFromRow`. No path nulls a due date accidentally.

### Conclusion
**Not reproducible from the calendar code with the current data — here is the ranked hypothesis set and what was ruled out.**

RULED OUT (hard evidence): (a) commits 63bd69e and 83e4099 — the first only wraps each bucket in `sortDoneLast`, a total partition that drops nothing (`task_sort.dart:7-10`); the second adds one `ValueKey` (`task_calendar_view.dart:197`); 28 tests green. (b) Any status/done/search/recurring filter on the calendar path — none exists; `tasks_screen.dart:563` passes the full owner-`all` list and `task_dao.dart:100-106` has no status filter. (c) Map-key mismatch against table_calendar's UTC grid days — `task_calendar_view.dart:54` re-derives a local key from components; probed as a hit. (d) `markersMaxCount: 0` suppressing the custom builder — `table_calendar.dart:651-654` calls `markerBuilder` unconditionally. (e) Server-side data loss — the five open dated/recurring rows exist with well-formed naive/date-only `due_date`, plaintext, with consistent `updated_at` for the delta cursor.

**H1 (most likely, calendar-specific, ~45%): the tasks ARE plotted but read as absent, because every fully-completed day collapses to the near-invisible `_AllDoneBadge`** — `task_calendar_view.dart:258-263` + `:348-368`, a 13×13 circle at 18% alpha with a 9px glyph. In the live DB *every* dated day before 2026-08-02 is 100% done, so the whole calendar history has zero colored dots. A recurring task is completed each cycle, so its past occurrences never show a dot — precisely "recurring tasks do not show in the calendar". Confirm by: open Calendar and tap a past day (e.g. 2026-07-28) — if the day list underneath shows 6 tasks while the cell looked blank, this is it.

**H2 (~30%): the rows/`due_date` never reached the phone's `task_cache`.** Calendar and widget share `TaskDao.list()` (§6), and both drop a task exactly when `due_date` is null/empty/unparseable or the row is missing — while the List view still shows it under "Upcoming". Server data is healthy and every client write path preserves `due_date`, so the suspect is the pull landing (or the app not reaching the gateway at all). Confirm by: `adb logcat | grep TaskSync.pull` (it logs `fetching changes since cursor=…` and `applied — merged=N`), or pull `task_cache` off the device and check `select id, due_date from task_cache where deleted=0`. If `due_date` is populated there, H2 is dead and H1/H3 stand.

**H3 (confirmed defect, currently latent, ~15% as the user's actual complaint): the missing `.toLocal()` at `task_calendar_utils.dart:25/29`** puts any tz-aware `due_date` on its **UTC** calendar day — one day early for any Madrid due between 00:00 and 02:00. `lazyclaw/tasks/store.py:552` emits exactly that shape when a recurring template's due was tz-aware, and it contradicts `mobile/lib/core/due_date.dart:31` which does `.toLocal()`, so the row chip and the calendar cell can disagree by a day on the same task. Today's rows are all naive so it is not firing yet — but it is a real bug and should be fixed regardless: `final local = parsed.toLocal(); final key = DateTime(local.year, local.month, local.day);` (and mirror it in `tasks_screen.dart:94` / `:131` and `home_widget_tasks.dart:110` so all three surfaces agree).

Secondary, worth fixing while in there: `task_calendar_utils.dart:26-27` swallows parse failures with a bare `continue` — add a `debugPrint` so a malformed `due_date` is diagnosable instead of invisible.

## Probe: widget

## 1. What the widget selects and pushes

`/Users/blckit/Desktop/Code_Projects/lazyclaw/mobile/lib/core/home_widget_tasks.dart`

- **Selection** — `relevantWidgetTasks` (:96-122) partitions open tasks into 3 buckets and returns **exactly one tier**:
  - `if (t.isDone) continue;` (:104) — `isDone` is `status == 'done'` (`lib/models/task.dart:64`).
  - `_dueInstant(t.dueDate)` null → `undated` (:105-109); `dueDay.isAfter(today)` → `upcoming` (:111-112); else (overdue **or** today) → `dueNow` (:114).
  - Return: `if (dueNow.isNotEmpty) return dueNow..sort(byDue); if (upcoming.isNotEmpty) return upcoming..sort(byDue); return undated;` (:119-121).
  - `byDue` is **ascending** (`_dueInstant(a).compareTo(_dueInstant(b))`, :117-118).
- **Cap / write** — `updateTasksWidget` (:34-62) takes the first 3 (`kTasksWidgetRowCount = 3`, :26) and writes plaintext: `task_count` int (:38), `task_<i>_title` / `task_<i>_due` for i=0..2 (:42-43), `task_more` = `widgetMoreLabel(tier.length)` (:47-49), `task_stamp` = `HH:mm` of the write (:52-54), then `HomeWidget.updateWidget(name:'TasksWidget', androidName:'TasksWidget')` (:55-58). Whole body is wrapped in `catch (_) {}` (:59-61) — **any failure is silent**.
- `dueDate` is rendered by `widgetDueLabel` (:135-147); recurring is **not** referenced anywhere in this file — `recurring`/`recurUntil` play no role in selection or labelling.

## 2. Who calls `updateTasksWidget`, and when

Only two call sites (plus two `clearTasksWidget`):
- `lib/providers/tasks_provider.dart:456` — inside `_refreshFromCache` (:449-457), which every mutation funnels through: `load` (:184), `addTask` (:252), `updateTask` (:296), `rescheduleMany` (:332), `completeTask` (:418), `deleteTask` (:437), `addComment`/`deleteComment`, and `_syncThenRefresh` (:467). So **yes — adding a dated/recurring task in-app does repaint the widget**, provided `tasksProvider` is alive.
- `lib/sync/background_sync.dart:99` — headless WorkManager pass, `kTaskSyncInterval = Duration(minutes: 30)` (:33).
- `lib/providers/auth_provider.dart:83` (`handle401`) and `:152` (`logout`) call `clearTasksWidget()`, which zeroes `task_count` and blanks every row (`home_widget_tasks.dart:66-82`). **A single 401 from any request wipes the widget to its empty state until the next refresh.**
- There is **no app-resume hook** for the widget: `lib/main.dart:246-248` and `lib/sync/foreground_sync.dart:48-49` handle `AppLifecycleState.resumed`, but neither touches the widget directly (it only repaints if the resume-triggered sync reaches `_refreshFromCache`).

## 3. Android side — no key mismatch

- Provider: `android/app/src/main/kotlin/com/lazyclaw/lazyclaw_mobile/TasksWidget.kt:32` extends `HomeWidgetProvider`; keys read are `task_count` (:176 / :101), `task_<i>_title` (:124), `task_<i>_due` (:126), `task_more` (:177 / :138), `task_stamp` (:178 / :84) — **identical to what Dart writes**.
- Prefs file matches: plugin writes/reads `HomeWidgetPreferences` (`~/.pub-cache/hosted/pub.dev/home_widget-0.9.2/.../HomeWidgetPlugin.kt:270`, used at :52 and :302; `HomeWidgetProvider.kt` passes `HomeWidgetPlugin.getData(context)`).
- Class resolution matches: plugin does `Class.forName("${context.packageName}.$className")` (`HomeWidgetPlugin.kt:107`); `applicationId = "com.lazyclaw.lazyclaw_mobile"` (`android/app/build.gradle.kts:38`) == the Kotlin package (`TasksWidget.kt:1`). Receiver is declared with an `APPWIDGET_UPDATE` filter (`AndroidManifest.xml`, `.TasksWidget` block).
- Layout ids all exist (`res/layout/tasks_widget.xml:68,84,93,109,125,134,150,166,175,192,204`).
- **Conclusion: the Android side is sound. If the widget is empty, `task_count` in prefs is 0 or the rows genuinely hold the wrong tasks.**

## 4. Does the pipeline exclude FUTURE due dates? — YES, categorically

`home_widget_tasks.dart:119-121`:
```dart
if (dueNow.isNotEmpty) return dueNow..sort(byDue);
if (upcoming.isNotEmpty) return upcoming..sort(byDue);
return undated;
```
**Any single overdue-or-today task suppresses every future-dated task from the widget entirely.** This is deliberate and codified: `test/core/home_widget_tasks_test.dart:41-50` — *"a 'Today' widget NEVER shows future tasks when something is due now"* asserts the `2026-06-20` task is dropped.

Worse, within `dueNow` the sort is **oldest-first**: `test/core/home_widget_tasks_test.dart:52-60` asserts `['overdue', 'today_am', 'today_pm']`. With ≥3 stale overdue tasks, the 3 rows are permanently occupied by the *oldest* items, and today's task / today's recurring occurrence never appear. The `+N more` footer even hides the scale of it — `widgetMoreLabel(tier.length)` (:47-49, :168-171) counts only that tier.

Note the inverse: the widget **prefers** dated tasks over undated ones. There is **no path in this file where a dated task loses to an undated task**. So if the user sees undated tasks on the widget but not dated ones, the dated ones are either (a) not in the local cache, (b) `status == 'done'`, or (c) starved by ≥3 older overdue items.

## 5. Recent regressions — none in the widget

`git log` on `mobile/lib/core/home_widget_tasks.dart` + `mobile/android`: last widget-touching commit is **`5d9baaf` (2026-06-10)**, "Tasks widget rows show each task's full date AND time". Nothing in the last 25 commits (`dd1b2b2`…`3512881`) touched the widget Dart, Kotlin, or XML. `mobile/android` appears only via `c4102ca` (MainActivity, voice) and `12d7b2c` (build.gradle ABI split). **The widget code is not the regression.**

## Additional latent defect: UTC-vs-local day maths

`_dueInstant` (:176-179) and `widgetDueLabel` (:138) call `DateTime.tryParse(due)` **without `.toLocal()`**, then read `.year/.month/.day` (:110, :153). `lib/core/due_date.dart:26-31` documents exactly this hazard and *does* apply `.toLocal()` in `dueTimeParts`. Consequence for a tz-aware `dueDate`: the day-word comes from UTC components while the clock time comes from local — one label can read `Today · 1:30 AM` for a task that is tomorrow locally, and tier assignment shifts by a day near midnight. Mobile-authored dues are naive-local (`composeDueDate`, `due_date.dart:78-85`) so this is latent, but server/agent-authored dues can be aware (backend only guarantees `due_date` is *not* UTC-normalised — `lazyclaw/tasks/store.py:450-451` — while `store.py:661-662` derives `due_date = reminder_at[:10]` from the **UTC-normalised** reminder, which already yields the previous calendar day for late-evening local times).

## How to confirm on-device

1. `adb shell run-as com.lazyclaw.lazyclaw_mobile cat /data/data/com.lazyclaw.lazyclaw_mobile/shared_prefs/HomeWidgetPreferences.xml`
   - `task_stamp` blank or hours old → the pipeline never ran (staleness, or the `handle401` wipe at `auth_provider.dart:83`).
   - `task_count=3` with three stale overdue titles → row starvation (`:119` + ascending `byDue`).
   - `task_count=0` while the app shows open tasks → upstream cache/sync, not the widget.
2. Cross-check the in-app Tasks list: if the dated/recurring tasks are missing there too, the widget is only mirroring an empty/stale `task_cache`, and the fault is in `TaskSync.pull` (`lib/sync/task_sync.dart:461-478`, cursor-based `/api/tasks/changes?since=`) — the same cursor-advance class of bug already recorded for budgets.

### Conclusion
The widget pipeline contains no filter that drops dated or recurring tasks — it strictly PREFERS them (dated tiers beat undated at home_widget_tasks.dart:119-121), and `recurring` is never read at all. So the widget cannot be the origin of "dated tasks don't show" unless one of these two widget-specific mechanisms is in play:

(1) MOST LIKELY WIDGET-SPECIFIC CAUSE — overdue starvation of the 3 rows. `relevantWidgetTasks` returns ONLY the `dueNow` tier whenever any overdue-or-today task exists (home_widget_tasks.dart:119), and sorts it OLDEST-FIRST (`byDue` ascending, :117-118). With ≥3 stale overdue tasks, rows 0-2 are permanently held by the oldest items; today's tasks and today's recurring occurrence never surface, and every FUTURE-dated task is suppressed outright (asserted by test/core/home_widget_tasks_test.dart:41-50 and :52-60). The `+N more` footer counts only that same tier (:47-49), masking the scale.

(2) SNAPSHOT STALENESS — the row selection is computed only when Dart runs `updateTasksWidget` (tasks_provider.dart:456 on mutation, background_sync.dart:99 every ~30 min). The Android 30-min self-heal repaint (tasks_widget_info.xml `updatePeriodMillis=1800000`, TasksWidget.kt:33-67) merely re-reads the frozen prefs; only the header date is recomputed at paint time (TasksWidget.kt:75-79). So after midnight a task that just became "due today" does not enter the widget until the app is opened or WorkManager fires — and on HyperOS that background job is routinely killed. A single 401 anywhere also zeroes the snapshot (auth_provider.dart:83 → clearTasksWidget).

However, given the user reports the same symptom IN THE APP, the dominant root cause is almost certainly UPSTREAM of this file: the dated/recurring tasks are absent (or `status=='done'`) in `task_cache`. Both the widget and the in-app list read the identical `TaskDao.list()` (task_dao.dart:100-107, `where: 'deleted = 0'`, no status/date filter) — the widget is just mirroring the cache. Prime suspect upstream: the cursor-based pull `TaskSync.pull` → `/api/tasks/changes?since=<cursor>` (task_sync.dart:461-478), which is how server-minted recurring respawns (new ids, created by the backend on complete) are the ONLY way those rows can reach the phone. Ruled out along the way: key mismatch (Dart keys == TasksWidget.kt keys == HomeWidgetPreferences), class-name resolution (applicationId == Kotlin package), missing manifest receiver, missing layout ids, a recent widget regression (widget code untouched since 5d9baaf, 2026-06-10), and dropped due_date/recurring on local create (task_dao.dart:245-248, 275-278 persist both).

## Probe: expenses

## 1. EXPENSE MODEL (backend)

**Table `project_expenses`** — `lazyclaw/db/schema.sql:493-509`:
```
id, user_id, project_id (NOT NULL → projects.id), task_id (nullable, "optional FK to tasks(id)"),
amount REAL (plaintext), currency, description*, vendor*, notes*, spent_at,
status ('posted'|'void'), recurring_expense_id, lazybrain_note_id, created_at, updated_at
```
Indexes: `idx_project_expenses_project(user_id, project_id, status)` at `schema.sql:511-512`; **`idx_project_expenses_task(user_id, task_id)` at `schema.sql:514-515`**.

Two more columns arrive by guarded ALTER, not in schema.sql: `deleted_at` (`lazyclaw/db/connection.py:282`) and `is_favorite` (`connection.py:310`).

- Column list the store SELECTs/decodes: `lazyclaw/budgets/store.py:55-64` (`EXPENSE_COLUMNS`), joined into `EXPENSE_SELECT` at `store.py:81`.
- **Encrypted fields: only `description`, `vendor`, `notes`** — `store.py:30` (`ENCRYPTED_EXPENSE_FIELDS`). `amount`, `project_id`, `task_id`, `status`, `spent_at` are plaintext so `SUM()` works in SQL.
- Row→dict: `store.py:157-164` (`_row_to_dict`) + `store.py:176-182` (`_expense_to_dict`, coerces `is_favorite` to a JSON bool).

**Link fields:**
- `project_id` — required. `create_expense` 404s if the project is missing (`store.py:770-772`).
- `task_id` — optional, `store.py:758` kwarg → INSERT at `store.py:802-812`. **It is never validated**: no check that the task exists, that it belongs to the same project, or that it isn't soft-deleted. Same in `update_expense` (`store.py:892-944` builds `SET col = ?` from whatever keys arrive). Only side-effect of `task_id` is a cosmetic title on the LazyBrain note via `_resolve_task_title` (`store.py:1454-1466`, swallows every error).
- Query filter: `list_expenses(..., task_id=...)` exact equality, `store.py:827-843`.
- Delete is a soft tombstone (`store.py:949-962`); the delta feed re-emits `EXPENSE_SELECT` verbatim at `store.py:1010` inside `get_budget_changes` (`store.py:964+`).

**Routes** — `lazyclaw/gateway/routes/budgets.py`: `task_id` on create body `:86`, patch body `:97`, recurring body `:115`; list filter `:315-319`; create `:340`; recurring `:454`. PATCH uses `model_dump(exclude_unset=True)` (`:363`) so an explicit `"task_id": null` clears the link while an omitted key leaves it alone.

## 2. EXPENSE↔TASK on mobile

- `mobile/lib/models/expense.dart:7` `final String? taskId`; parse `:63`, serialize `:82`, `copyWith` `:100,117`. Note `copyWith` uses `taskId ?? this.taskId` — **cannot clear** a link.
- Local cache column: `mobile/lib/local/app_db.dart:173` (`task_id TEXT` in `expense_cache`, schema block `:170-189`). DB version `kAppDbVersion = 12` at `app_db.dart:51`; migrations in `migrateAppDb` at `app_db.dart:271+` (established pattern: `PRAGMA table_info` → conditional `ALTER`, e.g. `:336-344`).
- Row mapping: `mobile/lib/local/budgets_dao.dart:1341-1358` (`_expenseFromRow`, `task_id` at `:1344`) and `:1360-1380` (`_rowFromExpense`, `:1363`).
- **Create does NOT carry a task link**: `budgets_dao.dart:583-636` (`applyLocalExpenseCreate`) builds its outbox payload at `:620-628` with only `id/project_id/amount/description/currency/spent_at/vendor` — no `task_id`. The repo's `createExpense` (`mobile/lib/repositories/budgets_repository.dart:201-226`) likewise has no `taskId` param. A task link is only ever established by a follow-up PATCH.
- **Update uses explicit null-vs-absent**: `budgets_dao.dart:647-712` — `taskId` + `taskIdSet` pair documented at `:647-663`, applied at `:682` (`if (taskIdSet) 'task_id': taskId`). Provider passthrough at `mobile/lib/providers/budgets_provider.dart:369-394`.
- Callers: detail sheet always saves `taskIdSet: true` (`expense_detail_sheet.dart:92-93`); bulk assign clears on project move (`mobile/lib/screens/expenses_screen.dart:734-746`, `:833-834`).
- Sync: outbox replay `mobile/lib/sync/budgets_sync.dart:240-260`; server merge `:559-560`, `:762+` (LWW).
- Picker source: `tasksForProject(all, p)` at `expense_detail_sheet.dart:123-128` and `bulk_assign.dart:143`, matching `Task.category` against `Project.nameKey` (`mobile/lib/models/task_project_link.dart:17-25`).

### UNCOMMITTED working-tree changes (`git status`, HEAD = dd1b2b2)

Both are a single coherent WIP on the **expense task picker's stale-selection handling** (plus unrelated dirt in CLAUDE.md, Dockerfile, freelance_specialist.md, mcp-upwork/profile.py, tests/teams/…, and untracked `mobile/CLAUDE.md`).

**`mobile/lib/models/task_project_link.dart` (+17/-8)** — `tasksForProject` gains `{bool includeCompleted = false}` and now filters `&& (includeCompleted || !t.isDone)`, i.e. **done tasks disappear from both expense task pickers by default**. Doc comment updated to name the bulk-assign sheet too.

**`mobile/lib/screens/expenses/expense_detail_sheet.dart` (+45/-...)** — adds an `onStaleSelection` VoidCallback to both `_ProjectPicker` (`:364+`) and `_TaskPicker` (`:462+`). In `_ProjectPicker.build` the `hasSelected` guard is hoisted ABOVE the `projects.isEmpty` early-return so it fires even when the list is empty; both pickers now schedule `WidgetsBinding.instance.addPostFrameCallback((_) => onStaleSelection?.call())` when `selectedId != null && !hasSelected`. The sheet wires both back to `setState(() => _projectId/_taskId = null)` (`:191-217`), so a Save can never resubmit an id the UI stopped rendering.

Matching test diffs: `mobile/test/models/task_project_link_test.dart` (+30, "excludes done tasks by default" / "includes done tasks when includeCompleted is true") and `mobile/test/screens/expense_detail_sheet_test.dart` (+63, asserts `taskId: null` + `taskIdSet: true` reach the update stub and that `(no task)` renders).

**Direct relevance to this feature**: the new filter is exactly the trap a subtask picker inherits — a *done* subtask (or a respawned one) would vanish from the picker, trip `onStaleSelection`, and **silently null the link on next Save**.

## 3. SUBTASK IDENTITY — the respawn trap

- Storage: `tasks.steps` is an **encrypted JSON array** — column added at `lazyclaw/db/connection.py:128-129`; listed in `ENCRYPTED_FIELDS` and `_JSON_LIST_ENCRYPTED_FIELDS` at `lazyclaw/tasks/store.py:21,27`. There is **no subtasks table** — a subtask id has no row, no FK, no index.
- Canonical shape + id minting: `tasks/store.py:313-347` (`_normalize_steps`); server mints `f"s{i}-{uuid4().hex[:6]}"` (`:327`, `:335`), mobile mints `s-<uuid v4>` (`mobile/lib/models/subtask.dart`, `newSubtaskId()`). Ids ARE preserved when passed in (`:335` `str(raw.get("id") or ...)`).

**RESPAWN RE-MINTS EVERY ID** — `tasks/store.py:1598-1602`:
```python
next_steps = [
    {"title": step["title"], "done": False}
    for step in decode_steps(task.get("steps"))
    if step.get("title")
] or None
```
`id` is deliberately dropped, so `create_task(steps=next_steps)` (`:1636`) → `_normalize_steps` mints fresh ids ("Fresh step ids … keep each occurrence's per-step toggles independent", `:1596-1597`). The new occurrence is also a **new task row with a new `id`** (`_RESPAWN_RESET_COLUMNS` includes `"id"`, `store.py:412-413`).

**Implication for an expense keyed on subtask_id**: identical to the comments verdict. Comments are classified **RESET** — `"comments", # the thread belongs to the occurrence (progress_log precedent)` at `tasks/store.py:425`. Since the respawn creates a *new task id*, an existing expense's `task_id` still points at the completed occurrence and never follows the series; a `subtask_id` on that same expense points at a step id that no longer exists anywhere. So a subtask-linked expense is **automatically "reset" by construction** — it stays attached to the historical occurrence, which is the correct ledger semantics (money was spent on *that* occurrence). No carry logic is needed or wanted; what IS needed is that the parent link survives so the expense doesn't become invisible. `allocated_budget` by contrast is CARRIED (`_RESPAWN_CARRY_COLUMNS`, `store.py:382-386`), because it's a template setting, not a fact.

Guard tests that will fail loudly if you add a task column without a decision: `tests/tasks/test_recurring_carry_forward.py:160` (`test_respawn_carry_columns_are_real_columns`), `:175` (`test_every_task_column_has_a_respawn_disposition`), `:206` (no-overlap). **These only cover `tasks` columns — a new `project_expenses.subtask_id` gets no such guard for free.**

**Deleted subtask → established precedent (cascade, not orphan):**
- Server: `set_steps` prunes orphaned comments in the SAME UPDATE — `tasks/store.py:1970-2026`; `surviving_ids` at `:1997`, prune at `:1999-2002`, single-write rationale at `:1978-1985`. Validation on write: `add_comment` rejects an unknown `subtask_id` (`:2288-2291`).
- Mobile mirror: `mobile/lib/local/task_dao.dart:316-336` prunes the same way inside `applyLocalUpdate`, and only when a `steps` value is present in the patch.
- Test: `tests/tasks/test_comment_orphan_cascade.py:32-59`.

**But `delete_task` does NOT cascade to expenses** — `tasks/store.py:1800-1845` only nulls the reminder job and stamps `deleted_at`; nothing touches `project_expenses.task_id`. That dangling link is precisely why the mobile `_TaskPicker` needed the stale-selection guard now sitting in the working tree. A `subtask_id` would inherit the same dangling behavior *twice over* (deleted subtask AND deleted parent task).

Note the asymmetry: comments live INSIDE the task row so pruning them is free and lossless (invisible data). **An expense is money.** Silently deleting an expense because its subtask was renamed away would be data loss; the cascade for expenses must be *demote to parent task* (null the `subtask_id`, keep `task_id`), not delete.

## 4. AGGREGATION — where totals are computed

**Backend has exactly one rollup, and it is project-scoped:**
- `_spent_by_project` — `lazyclaw/budgets/store.py:502-513`: `SELECT project_id, SUM(amount) … WHERE status='posted' AND deleted_at IS NULL GROUP BY project_id`. Consumed by `list_projects` (`:515-540`) to attach `spent`/`remaining`.
- **There is no per-task SUM anywhere in Python.** `grep allocated_budget` across `lazyclaw/` hits only the column definition (`tasks/store.py:77,385,742,777`), the migration (`db/connection.py:266`) and the route body field (`gateway/routes/tasks.py:109`).

**Per-task totals are computed client-side, web only:**
- `web/src/components/budgets/TaskExpensePanel.tsx:59-60` — `api.listExpenses(proj.id, taskId)` (→ `?task_id=` at `web/src/api.ts:913`, which hits the exact-equality filter at `budgets/store.py:841-843`).
- `TaskExpensePanel.tsx:119` `const taskTotal = expenses.reduce((s, e) => s + (e.amount||0), 0)`; again at `:260` feeding the allocated-vs-spent bar (`:319-364`, pct `:362`, remaining `:364`). Allocation writes at `:343`/`:354`.
- Web `Expense` type: `web/src/api.ts:835-852`; patchable keys incl. `task_id` at `:854-857`.

**Mobile has no per-task expense rollup at all.** `mobile/lib/screens/tasks/task_detail_sheet.dart` has only an *allocated budget* text field (`:54`, `:148-150`, `:347-358`, `:625-631`) — no spent bar, no expense list. `expense_row.dart` doesn't render a task label either.

**What must change if an expense attaches to a subtask:** if (and only if) the expense **keeps `task_id` populated alongside a new `subtask_id`**, the answer is *nothing* — `_spent_by_project` (`store.py:502-513`) already ignores both, and `list_expenses(task_id=…)` (`:841-843`) still matches, so the web panel's reduce at `TaskExpensePanel.tsx:119,260` keeps rolling subtask expenses into the parent task automatically. If instead you overload `task_id` to hold a subtask id (or move the link), **every one of those breaks silently**: the parent-task filter stops matching, the web task total drops those rows to zero, and the project SUM would still be right — producing a "project shows €300, its tasks show €0" split that reads as a sync bug.

## 5. UI SURFACES NEEDING WORK

- **Expense detail sheet** — `mobile/lib/screens/expenses/expense_detail_sheet.dart`: `_TaskPicker` at `:462-540` ("(no task)" sentinel at `:523`); a third `_SubtaskPicker` slots in after `:207-217`, sourced from `parseSubtasks(selectedTask.steps)` (`mobile/lib/models/subtask.dart`). Must gain the same `onStaleSelection` reset the working tree just added (`:210-217`), plus a cascade reset when `_taskId` changes (mirroring `:191-193` which already nulls `_taskId` on project change). Save path `:86-95` needs a `subtaskId`/`subtaskIdSet` pair.
- **Bulk assign** — `mobile/lib/screens/expenses/bulk_assign.dart:130-240` pops `(projectId, taskId)`; would need a 3-tuple, and `expenses_screen.dart:734-746` updated.
- **Create path** — `budgets_dao.dart:583-636` + `budgets_repository.dart:201-226` currently drop `task_id` entirely on create; a subtask link added at add-expense time needs both threaded (and `add_expense_sheet.dart` has no task picker today).
- **Subtask editor rows** — `mobile/lib/screens/tasks/subtask_editor.dart:15-43`: the `commentCounts` + `onOpenComments` pair (`:20-22`, `:27-43`) is the exact template for a per-subtask money badge / "add expense" affordance. Wiring lives at `task_detail_sheet.dart:867-899`, with counts derived at `:543` (`live.taskComments.where((c) => c.subtaskId == s.id).length`) — note it keys off the SAVED task, not the in-sheet working list, so an unsaved subtask can't be targeted (`:38-42` explains why). Same constraint applies to expenses.
- **Task detail sheet totals** — `task_detail_sheet.dart:625-631` only has the allocated-budget input; a "spent vs allocated" bar (the web `_AllocationBlock`, `TaskExpensePanel.tsx:319-364`) doesn't exist on mobile and would be the natural place to show `parent total (incl. N subtask expenses)`.
- **Web** — `TaskExpensePanel.tsx:280-300` renders the per-task expense list; would need a subtask grouping, and `web/src/api.ts:835-857` needs the field added to `Expense` + `ExpensePatch`.

## Plumbing checklist for a new `project_expenses.subtask_id` column
`schema.sql:493-509` (CREATE) → `db/connection.py` guarded-ALTER list (pattern at `:282`/`:310`) → `budgets/store.py:55-64` `EXPENSE_COLUMNS` (position matters — `_row_to_dict` at `:157-164` is index-based) → `create_expense` kwarg+INSERT (`:748-812`) → `list_expenses` filter (`:827-843`) → routes bodies `budgets.py:86,97` → web `api.ts:835-857` → mobile `expense.dart:7/63/82/100/117`, `app_db.dart:170-189` + `kAppDbVersion` bump at `:51` + a `migrateAppDb` branch at `:271+`, `budgets_dao.dart:1344/1363` + the `taskIdSet` null-vs-absent twin at `:665-682`, `budgets_provider.dart:374`, `budgets_repository.dart:201-226`. Delta feed needs nothing (`store.py:1010` re-uses `EXPENSE_SELECT`).

### Conclusion
## RECOMMENDED SHAPE: additive `subtask_id` column on `project_expenses`, ALWAYS alongside a populated `task_id`

Add one nullable plaintext column `subtask_id TEXT` next to `task_id` (`lazyclaw/db/schema.sql:497`), with the **hard invariant: `subtask_id IS NOT NULL` implies `task_id IS NOT NULL`** — enforced in `create_expense`/`update_expense` (`lazyclaw/budgets/store.py:748`, `:892`) by rejecting/nulling a bare `subtask_id`. Store it plaintext like `task_id`; a `s-<uuid>` string carries no user content (titles stay in the encrypted `tasks.steps` blob), and plaintext is what keeps `SUM()` and `GROUP BY` in SQL.

**Why this shape wins, evidence-first:**

1. **Aggregation becomes a no-op.** `_spent_by_project` groups only by `project_id` (`store.py:502-513`) and the per-task filter is `task_id = ?` (`store.py:841-843`). With `task_id` still populated, the web panel's `expenses.reduce(...)` (`web/src/components/budgets/TaskExpensePanel.tsx:119,260`) keeps counting subtask expenses in the parent total with zero code change. Every alternative (overloading `task_id`, moving the link, a polymorphic `target_type`/`target_id` pair) breaks that filter and silently zeroes task totals while project totals stay right — the worst failure shape, because it reads as a sync bug rather than a schema bug.

2. **The respawn trap is neutralised by construction, not by carry logic.** The respawn re-mints step ids (`lazyclaw/tasks/store.py:1598-1602`) AND mints a new task row id (`_RESPAWN_RESET_COLUMNS` contains `"id"`, `:412-413`). So an expense with `(task_id=old, subtask_id=old-step)` simply stays attached to the completed occurrence — which is correct: the money was spent on that occurrence, exactly the reasoning that put `comments` and `progress_log` in `_RESPAWN_RESET_COLUMNS` (`:424-425`). Do **not** attempt to carry expenses forward and do **not** add `subtask_id` to any respawn set — there is nothing to decide because the column lives on `project_expenses`, not `tasks`. Note the corollary: the existing per-task guard tests (`tests/tasks/test_recurring_carry_forward.py:175`) will NOT protect this column, so add a dedicated respawn test asserting a subtask-linked expense stays pinned to the old occurrence and does not leak into the new one's totals.

3. **Orphan cascade must DEMOTE, not delete.** The established precedent is `set_steps`' comment prune (`tasks/store.py:1997-2002` server, `mobile/lib/local/task_dao.dart:316-336` client, test `tests/tasks/test_comment_orphan_cascade.py:32`). Copying it literally would *delete money on a subtask rename* — unacceptable. Instead, when `set_steps` drops a step, `UPDATE project_expenses SET subtask_id = NULL WHERE subtask_id NOT IN (surviving) AND task_id = ?`. The expense demotes to a plain task-level expense, stays in every total, and nothing is lost. Same for `delete_task` (`tasks/store.py:1800-1845`), which today cascades to *nothing* and already leaves dangling `task_id`s — that pre-existing hole is what forced the `onStaleSelection` guard now sitting uncommitted in `expense_detail_sheet.dart`. Fixing that hole (null both link columns on task delete) is a cheap prerequisite that also lets the picker guard stay a belt-and-braces instead of load-bearing.

4. **UI stale-selection semantics are already solved and must be reused, not re-invented.** The uncommitted working-tree change (`task_project_link.dart` `includeCompleted` filter + `_ProjectPicker`/`_TaskPicker.onStaleSelection`) is the exact defence a `_SubtaskPicker` needs — with one delta: a *done* subtask must remain selectable/visible for an existing link, otherwise the new `!t.isDone` filter shape (`task_project_link.dart:23-25`) would trip `onStaleSelection` and **silently null a live money link on the next Save**. Land the subtask picker with `includeCompleted: true` semantics for the currently-linked item, or the feature ships with a data-loss path on day one.

**Alternatives rejected:**
- *Overload `task_id` to hold either kind of id* — kills the parent-task filter (`store.py:841-843`), splits web totals, and makes `idx_project_expenses_task` (`schema.sql:514-515`) meaningless. Worst option.
- *Polymorphic `(target_kind, target_id)`* — forces every existing reader to branch (`list_expenses`, `TaskExpensePanel`, mobile DAO), migrates live rows, and buys nothing: subtasks are the only new target.
- *Promote subtasks to a real `subtasks` table with stable ids* — the "correct" long-term fix (would also give comments a real FK and let the respawn carry ids deliberately), but it means decrypting and rewriting every `tasks.steps` blob, a mobile schema rewrite, and a rework of the comment cascade. Out of proportion for this request; the additive column does not block it later.
- *Expense-side JSON `{subtask_id}` in `notes`* — unqueryable, encrypted (`store.py:30`), and would need a decrypt loop to aggregate. Violates the plaintext-for-queries rule the whole module is built on (`store.py:1-8`).

**Sequencing:** (1) null-out cascades in `delete_task` + `set_steps` for the existing `task_id` hole; (2) additive column end-to-end per the plumbing checklist; (3) mobile `_SubtaskPicker` + `subtaskIdSet` null-vs-absent pair; (4) parent-total display showing "incl. N subtask expenses"; (5) subtask-editor money badge last (it depends on saved-task ids, same constraint as `subtask_editor.dart:38-42`).

## Adversarial verification

### Corrections to the probes
- CONFIRMED (calendar report): table_calendar 3.2.0 hands eventLoader UTC grid days - calendar_core.dart:262-266 builds them with DateTime.utc(...), consumed at table_calendar.dart:652. task_calendar_view.dart:54 re-derives a LOCAL key from those components, so the map lookup does hit. Their fragility warning is correct and deserves a comment + regression test.
- CONFIRMED (calendar report): markersMaxCount:0 does not suppress the custom builder - table_calendar.dart:651-654 calls calendarBuilders.markerBuilder first. Also confirmed: sortDoneLast is a total partition (task_sort.dart:7-10); filterByOwner(...,all) returns the list unchanged (task_owner_filter.dart:56-57); the '' clear sentinel really does store NULL locally (task_dao.dart:394-396); TASK_SELECT == join(TASK_COLUMNS) with deleted_at at index 34, updated_at at 33 (no tombstone-misclassification bug). 42 tests green across task_calendar_utils_test, tasks_calendar_smoke_test, home_widget_tasks_test.
- WRONG RANKING (calendar H1, 45%): the near-invisible _AllDoneBadge cannot explain the report. Three of the five open dated server rows are status=todo (310f2d7b due 2026-08-02, 82f6d048 due 2026-08-03, 9d0a948c due 2026-08-03T08:00), so Aug 2/3/4/5 all render solid 6px filled dots (_TaskDot._size=6, task_calendar_view.dart:321), never badges. The badge only fires on fully-completed PAST days. Demote to cosmetic secondary.
- MIS-FRAMED (both reports): the missing .toLocal() can never HIDE a task - it only relocates it by one calendar day. And it is not calendar-specific: the identical raw-parse idiom is in home_widget_tasks.dart:110/138/152 AND in the List view's _groupTasks (tasks_screen.dart:131-133), so it cannot produce a calendar-vs-list asymmetry. It is also latent right now: every open row in the live DB is naive or date-only (store.py:552 only emits UTC-aware when the ORIGINAL due was aware).
- SELF-CONTRADICTION (calendar report): it proved the server data is healthy and every client write path preserves due_date, then ranked a rendering hypothesis first. Healthy server + empty phone surfaces IS the evidence for the cache/pull cause - that should have been #1, not H2 at 30%.
- INCOMPLETE (widget report): its own strongest lemma is stated but not turned into the diagnostic. Since no path lets a dated task lose to an undated one (home_widget_tasks.dart:119-121), the widget rows showing BLANK due pills is proof the cache holds ZERO open dated rows. That is the cheapest on-device discriminator and it was never named.
- FACTUALLY WRONG (widget report): '+N more footer masks the scale'. With the live data dueNow has exactly 3 items, so widgetMoreLabel(3) returns '' (home_widget_tasks.dart:168-171) and TasksWidget.kt:139-140 hides the footer entirely. There is no footer to mask anything - the two future-dated tasks are simply invisible with zero hint. Worse than reported.
- MISSED BY BOTH: 'recurring' is WRITE-ONLY on the client. grep over mobile/lib shows every hit is a create/update payload field (tasks_provider.dart:249/295, tasks_repository.dart:169, add_task_sheet.dart:269/294, home_screen.dart:117, tasks_screen.dart:394) - no read path expands it. home_widget_tasks.dart and task_calendar_utils.dart never reference it. The server materializes exactly ONE occurrence at a time (store.py:1571-1633), so a '0 8 * * *' task occupies exactly one cell in a 30-day month. Both wrote 'recurring is not filtered anywhere' and stopped; the absence of expansion IS the defect from the user's viewpoint.
- MISSED BY BOTH: commit a68db21 (2026-08-02 20:30) shows home_screen.dart quick-add silently DROPPED recurring/recurUntil before that build (home_screen.dart:117-118 added). Any task added from the Home card on an older APK has recurring=NULL server-side and never respawns - a real historical contributor to 'recurring tasks do not show', already fixed but only one commit before HEAD (cb024f3 = 1.24.2+125).
- MISSED BY BOTH: there is NO cursor self-heal for tasks. task_sync.dart:499-501 advances the cursor unconditionally to changes.now, and app_db.dart has a one-time rewind branch for the 'budgets' cursor (v9->v10, lines 363-366) precisely for this bug class - but no equivalent for entity 'task' (kTaskEntity='task', task_dao.dart:20). Once the tasks cursor gets ahead of an undelivered row, that row is orphaned permanently.

### Ranked root causes
- #1 (TOP, ~60%) SHARED UPSTREAM - the dated/recurring rows are ABSENT from the phone's local task_cache; calendar and widget are faithfully rendering an empty *dated* set. (a) All three surfaces are pure functions of TaskDao.list(): tasks_provider.dart:450 + :456 (updateTasksWidget off the same list), background_sync.dart:99, home_screen.dart:127; none of them filters out a dated or recurring task. (b) The live server open-task set splits EXACTLY along the reported line: 5 dated rows all updated_at >= 2026-08-01T19:24 (310f2d7b 2026-08-02 cron '0 9 * * 1'; 82f6d048 2026-08-03; 9d0a948c 2026-08-03T08:00 cron '0 8 * * *'; eaea94bd 2026-08-04T00:00 cron '0 23 * * *'; 1c4af205 2026-08-05) vs 5 undated rows all updated_at <= 2026-07-25T14:31. A pull cursor stuck anywhere in that 7-day gap produces exactly 'dated + recurring invisible, undated visible'. (c) With zero dated rows: home_widget_tasks.dart:119-121 falls through to the `undated` tier so the widget paints 3 titles with BLANK due pills (TasksWidget.kt:132 hides an empty pill); groupTasksByDay returns {} so every calendar cell is bare and the panel reads 'Nothing due this day' (task_calendar_view.dart:186-191); the List still shows all 5 undated under 'Upcoming' (tasks_screen.dart:126-129). That triple is the reported signature, and only a cache-content cause produces it. (d) No recovery exists: task_sync.dart:499-501 advances the cursor unconditionally, and app_db.dart has a rewind for 'budgets' (:363-366) but none for 'task'.
- #2 (~20%) WIDGET-ONLY row starvation by the overdue/today tier. home_widget_tasks.dart:119-121 returns ONLY dueNow when non-empty, sorted OLDEST-first (byDue ascending, :117-118). With the live server data on 2026-08-03, dueNow = {310f2d7b overdue, 82f6d048 today, 9d0a948c today} = exactly 3, which occupies all 3 rows; widgetMoreLabel(3) returns '' so there is not even a '+N more' hint that Aug 4 and Aug 5 exist. Codified by test/core/home_widget_tasks_test.dart:41-50 and :52-60. Explains 'the RIGHT dated tasks don't show', not 'no dated tasks show'.
- #3 (~10%) NO RECURRENCE EXPANSION ON THE CLIENT. `recurring` is write-only in mobile/lib (create/update payload only); the server materializes one occurrence at a time on completion (store.py:1571-1633, _next_occurrence_fields at :472-576). So a daily recurring task appears on exactly ONE day of a 30-day calendar grid and, once its single occurrence is completed, on ZERO days until the respawn syncs. A user expecting a repeating task to be visible on every recurrence day reports 'recurring tasks do not show in the calendar' and is literally correct. Design gap, not a regression - but it is the shared data-model cause for the recurring half of the report.
- #4 (~5%) STALE WIDGET SNAPSHOT. The tier is computed in Dart at write time and frozen in HomeWidgetPreferences; the 30-min Android repaint (tasks_widget_info.xml updatePeriodMillis, TasksWidget.kt:33-67) only re-reads frozen prefs and recomputes the header date (:75-79). After midnight a task that just became due-today does not enter the widget until the app is opened or WorkManager fires - routinely killed on HyperOS. A single 401 anywhere zeroes it outright (auth_provider.dart:75-84 -> clearTasksWidget). Explains widget staleness, not the calendar.
- #5 (~3%) MISSING .toLocal() in day derivation - task_calendar_utils.dart:25/29, home_widget_tasks.dart:110/152, tasks_screen.dart:131-133. All three read .year/.month/.day off a possibly-UTC DateTime, contradicting due_date.dart:26-31 which does call .toLocal(). Real defect, but it only MISPLACES a task by a day, never hides it, and it is currently latent (all live open rows are naive or date-only; store.py:545-552 emits UTC-aware only when the original due was already aware). Fix it, but it is not this bug.
- #6 (~2%) ALL-DONE BADGE INVISIBILITY - task_calendar_view.dart:258-263 short-circuits to _AllDoneBadge (:348-368), a 13x13 circle at 18% alpha with a 9px glyph. Every dated day before 2026-08-02 is 100% done in the live DB, so calendar history reads as empty. Cosmetic; cannot cover Aug 2-5, which carry solid 6px dots.
- RULED OUT (hard evidence): commits 63bd69e and 83e4099 (sortDoneLast is total; the second adds one ValueKey); any status/done/search/owner/recurring filter on the calendar or widget path; map-key mismatch against table_calendar's UTC grid days; markersMaxCount:0 suppressing the builder; Android key/class/manifest/layout mismatch; a task_cache schema migration losing due_date (app_db.dart:269-400 are all additive ALTERs); the '' clear sentinel leaking into the cache column; TASK_COLUMNS/TASK_SELECT misalignment causing live rows to be sent as tombstones; server-side data loss (all 5 dated rows present, plaintext, well-formed, updated_at uniformly +00:00).

### Top fix
TOP CANDIDATE: the phone's task_cache is missing the dated/recurring rows because the tasks pull cursor is ahead of them, and there is no self-heal.

EXACT REPRODUCTION (offline, deterministic, no device needed):
1. Open the app DB and delete every task_cache row with due_date IS NOT NULL, leaving only the undated ones (this is exactly the state a stalled/over-advanced cursor produces, since GET /api/tasks/changes filters `updated_at > since` - lazyclaw/tasks/store.py:2353-2361).
2. Tasks -> Calendar: every day cell is bare and the selected-day panel reads "Nothing due this day" (task_calendar_view.dart:186-191), because groupTasksByDay returns {} (task_calendar_utils.dart:18-34).
3. Home-screen widget: relevantWidgetTasks falls through to the `undated` tier (home_widget_tasks.dart:121); updateTasksWidget writes 3 titles with due == '' (:41-43); TasksWidget.kt:132 hides the empty due pill -> three tasks, zero dates.
4. Tasks -> List: all of them still render under "Upcoming" (tasks_screen.dart:126-129).
That is the reported triple, produced purely by cache content - no calendar or widget code involved.

ON-DEVICE CONFIRMATION (two commands):
- `adb logcat -s flutter | grep -E "TaskSync\.(pull|push)"` -> task_sync.dart:459 logs `fetching changes since cursor=<X>` and :488-491 logs `applied - merged=N`. If <X> falls between 2026-07-25T14:31 and 2026-08-01T19:24 and merged=0, confirmed.
- Look at the widget: rows with BLANK due pills prove the cache holds zero open dated rows (no path lets a dated task lose to an undated one - home_widget_tasks.dart:119-121).

CODE CHANGE:

(A) mobile/lib/local/app_db.dart - bump kAppDbVersion to 13 and add the one-time tasks-cursor rewind that only 'budgets' has today (:363-366):

  // v12 -> v13: one-time rewind of the 'task' sync cursor. A cursor that ever
  // got ahead of an undelivered row orphans it forever (the server filters
  // `updated_at > since`), which is how server-minted recurring respawns and
  // agent/web edits go permanently missing from the phone while local-only
  // rows keep showing. Deleting the row makes the next getCursor() return null
  // -> the following sync pulls a FULL snapshot. Mirrors the v9 -> v10
  // 'budgets' rewind.
  if (oldVersion < 13) {
    await db.delete('sync_state', where: 'entity = ?', whereArgs: ['task']);
  }

(entity constant is kTaskEntity = 'task', task_dao.dart:20.)

(B) mobile/lib/sync/task_sync.dart:526-534 - stop advancing the cursor to the bare server clock. A row committed with an `updated_at` earlier than store.py:2351's now_iso but after the SELECT at :2355 is skipped forever. Clamp with a small overlap window (re-delivery is idempotent - upsertFromServer + LWW at :625-650):

  String? _resolveCursor(TaskChanges changes) {
    final raw = changes.now.isNotEmpty ? changes.now : _maxObservedUpdatedAt(changes);
    if (raw == null || raw.isEmpty) return null;
    final t = DateTime.tryParse(raw);
    if (t == null) return raw;
    // Overlap window: never advance past the newest row we could have raced.
    return t.toUtc().subtract(const Duration(minutes: 2)).toIso8601String();
  }

CHEAP GUARDS WORTH SHIPPING IN THE SAME PASS:
- task_calendar_utils.dart:26-27 - the bare `continue` on parse failure swallows a malformed due_date with zero diagnostics; add a debugPrint.
- Add .toLocal() to all three day derivations (task_calendar_utils.dart:25/29, home_widget_tasks.dart:110/152, tasks_screen.dart:131-133) so they agree with due_date.dart:31.
- home_widget_tasks.dart:119-121 - stop hard-cutting the tier: fill rows from dueNow then TOP UP from upcoming, and compute widgetMoreLabel over dueNow.length + upcoming.length so future-dated work is at least counted.
- Add a regression test pinning task_calendar_view.dart:54 (grouped[DateTime(day.year, day.month, day.day)]) - "simplifying" it to grouped[day] silently blanks the whole calendar, since table_calendar hands UTC days and Dart's DateTime.== compares isUtc.