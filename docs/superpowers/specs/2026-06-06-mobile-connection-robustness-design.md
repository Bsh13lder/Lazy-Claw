# Mobile Connection Robustness & Offline-First Hardening — Phase 1

- **Date:** 2026-06-06
- **Status:** Draft for review
- **Branch:** `feat/flutter-mobile`
- **Scope:** Flutter app (`mobile/`) only. **No backend changes.**

---

## 1. Context

This is **Phase 1 of a 4-phase Flutter polish initiative**:

| Phase | Theme | Status |
|---|---|---|
| **1 — Connection / robustness** | Kill the "Notes keeps loading / loses connection" bug; make offline-first screens always render; foreground 30-min resync. | **This spec** |
| 2 — Tasks polish | Advanced hybrid smart-add (multi-intent: task/expense/time/project), full task detail/edit panel, project CRUD wired into tasks. | Future |
| 3 — Notes → Tasks | Move Notes into the Tasks tab (segmented `Tasks \| Notes`); "what is this note about" smart preview; in-note navigation. | Future |
| 4 — Documents tab | Freed Notes slot becomes Documents (Sheets/Docs/PDF), **native** Flutter editing + ✨ AI. | Future |
| 5 — Android widgets & quick actions | Home-screen widgets (quick-capture, today list, budget) + app-icon long-press quick actions, Todoist-style. | Future |

Each phase gets its own spec → plan → build. (Phase 5's cheapest wins — the 4 app-icon quick actions + a quick-capture bar — reuse the existing add-sheets and could be pulled earlier as a standalone slice.)

---

## 2. Problem

**Symptoms reported:** opening the **Notes** tab "keeps loading", "loses connection", and an already-open app doesn't refresh on its own.

**Root cause (verified by code exploration):** The "loses connection / infinite loading" is **not** a missing cache. The encrypted local cache (`note_cache`), outbox, last-write-wins sync, **and** a 30-min background resync (`mobile/lib/sync/background_sync.dart:24`, Workmanager) **already exist** and work. The failure is a chain of unhandled error paths:

1. **Silent DB-init failure** — `mobile/lib/main.dart:19-24` swallows any DB-open error:
   ```dart
   try { db = await openAppDb(); } catch (_) { db = null; }
   ```
2. **Conditional provider override** — `mobile/lib/main.dart:32` only overrides `appDatabaseProvider` when `db != null`.
3. **Provider throws on access** — `appDatabaseProvider` (`mobile/lib/providers/tasks_provider.dart:17-22`) throws `StateError` when it was never overridden. The Notes provider chain (`notesProvider → noteSyncProvider → noteDaoProvider → appDatabaseProvider`) evaluates this on first watch and **crashes**. Notes is simply the first offline-first screen that trips the dead provider.
4. **No error UI** — `mobile/lib/screens/notes_screen.dart:161-169` shows the loading skeleton whenever `isLoading && notes.isEmpty`. With the provider crashed, the screen sits on the skeleton **forever** and nothing surfaces the real error.
5. **`isLoading` can stick** — `load()` (`mobile/lib/providers/notes_provider.dart:100-105`) sets `isLoading = true`; if the first cache read throws, `isLoading` is never reset.
6. **Reachability probe not awaited** — `_ReachableNotifier` (`mobile/lib/providers/tasks_provider.dart:56-71`) starts the probe with `unawaited(...)`, so the first paint can see `reachable=false` (false "offline" flash) and then fire a spurious second sync on the `false→true` edge.
7. **No foreground periodic resync** — periodic sync only runs in the background isolate (Workmanager, 30 min). A long-open foreground app only syncs on `load()`, pull-to-refresh, or a reachability flip.

---

## 3. Goals

1. Offline-first screens (**Tasks / Notes / Budgets**) **always render cache-first** — never an infinite skeleton.
2. A DB-open failure **retries**, then falls back to an **ephemeral in-memory DB** plus a **non-blocking banner** (Retry / Reset). The app never blocks.
3. `isLoading` is **always** cleared; a genuine error surfaces an **error + Retry** state instead of a spinner.
4. Reachability **initializes cleanly** — no false "offline" flash, no spurious double-sync.
5. An **open app stays fresh**: foreground **30-min** resync **+ sync-on-resume**.

## 4. Non-goals (deferred to later phases)

- Note summary / "what is this note about" preview → **Phase 3**.
- Smart-add / natural-language parsing → **Phase 2**.
- Documents tab → **Phase 4**.
- Any backend / API change.

---

## 5. Approach decision

Three options were considered for the dead-DB case:

- **A. Online-only repository path.** Each domain notifier gets a parallel "no cache" branch reading straight from the server repo. Literal "online-only", but touches every notifier with a second code path → more code, more failure surface. Rejected (violates minimal-impact).
- **B. Ephemeral in-memory DB fallback + banner. ✅ CHOSEN.** Harden `openAppDb()`: retry the encrypted file DB; on persistent failure open a `:memory:` SQLite DB so the provider chain *always* has a working `Database`. The whole app keeps working unchanged (cache-first reads, optimistic writes, existing sync); the cache is just ephemeral, and the launch sync immediately re-pulls from the server so real data appears ("online-backed"). A non-blocking banner explains the degraded state with Retry / Reset.
- **C. Blocking error/gate screen.** Top-level gate → splash → error screen. Blocks the app; not the chosen UX.

**Why B:** delivers the chosen "fallback + banner, never an infinite spinner" with the **smallest blast radius** — the provider graph keeps working because there is always a `Database`. Functionally identical to A in the degraded case at a fraction of the code.

**Security note:** a `:memory:` SQLite DB never touches disk, so the in-memory fallback introduces **no plaintext-at-rest** exposure. Decrypted user content already lives in app memory while running.

---

## 6. Detailed design

### 6.1 DB resilience — `mobile/lib/local/app_db.dart`
- `openAppDb()` returns a small immutable result instead of a bare `Database`:
  ```dart
  class AppDbResult { final Database db; final bool degraded; final Object? error; }
  ```
- Open flow: try the encrypted file DB → on failure **retry 2×** with a short backoff → on persistent failure open `databaseFactory.openDatabase(inMemoryDatabasePath)` and return `degraded: true, error: <captured>`.
- New `Future<void> resetAppDb()`: delete the DB file + the secure-storage key, then mint a fresh key + DB (used by the banner "Reset" action).
- Every catch **logs** (no silent swallow).

### 6.2 Boot wiring — `mobile/lib/main.dart`
- Always override `appDatabaseProvider` with `result.db` (file **or** in-memory) → the `StateError` crash path can no longer occur in normal boot.
- Expose `dbHealthProvider` (a `StateProvider<DbHealth>` where `DbHealth = ok | degraded(error)`), seeded from `AppDbResult`.
- Register the foreground sync scheduler + lifecycle observer (§6.6).

### 6.3 Provider robustness — `mobile/lib/providers/{notes,tasks,budgets}_provider.dart`
- Wrap `load()` so `isLoading` is **always** cleared (`try { … } finally { isLoading=false }`).
- On a cache-read error: set `error` (and leave `isLoading=false`), keep any cached items already shown.
- Preserve the existing cache-first → `unawaited(_syncThenRefresh())` flow.

### 6.4 UI states — screens + `mobile/lib/ui/`
- Render precedence on each offline-first screen:
  1. cached items present → show the list,
  2. else `error != null` → **`LzErrorState`** with a **Retry** button (calls `load()` / `syncNow()`),
  3. else → existing empty state.
  The infinite skeleton (`isLoading && empty`) only shows during the *first* in-flight cache read, which is near-instant.
- **Degraded banner:** a `LzBanner.degraded()` variant driven by `dbHealthProvider`, with **Retry** (re-run `openAppDb` and re-override) and **Reset** (confirm → `resetAppDb()` → re-pull). Shown on the offline-first screens (or once in the shell).
- New widgets: `LzErrorState` and the `LzBanner.degraded()` factory — consistent with the existing `Lz*` kit; no hard-coded colors/sizes/text styles.

### 6.5 Reachability — `mobile/lib/providers/tasks_provider.dart` (`_ReachableNotifier`)
- Start the probe **eagerly at boot** with an **optimistic default** (assume reachable until the first probe resolves) so the UI never flashes "offline" on first paint and does not fire a spurious `false→true` double-sync. Guard against double-start.

### 6.6 Foreground 30-min resync — new `mobile/lib/sync/foreground_sync.dart`
- `ForegroundSyncScheduler`: a `Timer.periodic(Duration(minutes: 30))` active only while the app is **resumed**, plus a `WidgetsBindingObserver` that triggers `syncNow` for **tasks / notes / budgets** on `AppLifecycleState.resumed`, and cancels the timer when backgrounded.
- Reuses the existing per-domain `syncNow()` — no new sync logic. This complements the existing background Workmanager 30-min job (which covers the app-closed case).

### 6.7 Reset flow
- Banner **Reset** → confirmation dialog → `resetAppDb()` → providers reload → launch sync re-pulls from server. Discards any unsynced outbox items — **only on explicit user tap**, with the dialog stating this.

---

## 7. Data flow (degraded boot)

```
boot → openAppDb() file-open fails (retry ×2 fail)
     → open :memory: DB, degraded=true
     → appDatabaseProvider overridden with in-memory db (no crash)
     → screens render (empty, isLoading cleared)
     → launch sync pulls from server
     → UI shows server data + degraded banner (Retry / Reset)
```

Happy path is unchanged: file DB opens → cache-first render → background sync → 30-min foreground/background resync.

---

## 8. Error handling

- No silent `catch (_) {}` swallows anywhere in the boot/DB/provider path — every catch logs context.
- Cache-read errors → `error` state with Retry, never a crash or endless spinner.
- Sync errors keep cached data (existing, correct behavior — offline is normal).

---

## 9. Testing plan (TDD — tests written first)

Unit / widget tests under `mobile/test/`:

1. **`app_db`** — when the file-DB open throws (mocked factory), `openAppDb()` returns an in-memory DB with `degraded == true` and a captured `error`.
2. **`app_db`** — `resetAppDb()` deletes the file + key and yields a fresh openable DB.
3. **`notes_provider` / `tasks_provider`** — `load()` clears `isLoading` even when the cache read throws, and sets `error`.
4. **reachability** — optimistic default before the first probe; no spurious second sync on the initial `false→true` edge.
5. **`foreground_sync`** — under `fakeAsync`, `syncNow` fires at the 30-min interval; the timer pauses on background and a sync runs on `resumed`.
6. **widget** — empty + `error` renders `LzErrorState` with a working Retry; `degraded` health renders the banner.

Coverage target: **80%+** on touched files (per repo testing rules).

---

## 10. Files touched

- `mobile/lib/local/app_db.dart` — `AppDbResult`, retry + in-memory fallback, `resetAppDb()`.
- `mobile/lib/main.dart` — always-override boot, `dbHealthProvider`, scheduler registration.
- `mobile/lib/providers/notes_provider.dart`, `tasks_provider.dart`, `budgets_provider.dart` — `load()` robustness, reachability init.
- `mobile/lib/screens/notes_screen.dart`, `tasks_screen.dart`, and the Money/Budgets screen *(exact path confirmed at implementation)* — error-state + banner wiring.
- `mobile/lib/ui/` — `LzErrorState`, `LzBanner.degraded()`.
- `mobile/lib/sync/foreground_sync.dart` — **new** foreground scheduler + lifecycle sync.
- `mobile/test/...` — the tests above.

No files outside `mobile/`.

---

## 11. Risks & mitigations

- **In-memory fallback hides the real DB fault** → mitigated by a visible banner + full logging; Reset offers recovery.
- **Foreground timer battery cost** → 30-min interval, cancelled whenever the app is backgrounded.
- **Optimistic reachability** could delay a true "offline" banner by one probe cycle → acceptable; avoids the worse false-offline flash.

---

## 12. Future phases (captured here so nothing is lost)

- **Phase 2 — Tasks (advanced hybrid smart-add).** A *universal* natural-language intake bar that recognizes **multiple intents and routes accordingly**:
  - **task vs expense** — e.g. "spent $20 on groceries" → an expense, not a task;
  - **times / dates / recurrence** — "tomorrow 5pm", "every Monday";
  - **priority** — `!p1` / `!!` style;
  - **project + tags via slash `/project`** (and `#tag` / `@label`).
  Implementation = **client-side Dart parser** for the common patterns (instant, offline, zero token cost) **+ optional ✨ AI** call for messy input only. Plus: full task **detail/edit panel** (description, subtasks/steps, priority, due, reminder, recurring, labels, project) and **project CRUD wired into Tasks**.

  **Calendar view (Phase 2):**
  - A **calendar view** in the Tasks tab (month / week / agenda), switchable with the list view.
  - **Per-project color-coding** — the `Project` entity gains a `color` field; each task renders in its project's color on the calendar (and consistently in the list, "accurate" across views).
  - Project color is **set + editable in project settings** (part of project CRUD), with a **toggle** to show/hide color-coding on the calendar.
  - **Add task from the calendar** — tap a day → smart-add sheet pre-filled with that date.
  - **Auto-refresh on add** — optimistic local insert already reflects instantly via the offline engine; the calendar must re-render without a manual refresh.
  - This is a **visual** design area — when we brainstorm Phase 2 we'll likely use the Visual Companion to compare calendar layouts and the color/picker UX. Needs a calendar package decision (e.g. `table_calendar`) and a backend `projects.color` field (small additive change).
- **Phase 3 — Notes → Tasks + smart preview** (segmented `Tasks | Notes`, "what is this note about" preview, in-note navigation, smart typing).
- **Phase 4 — Documents tab** (native Flutter Sheets/Docs/PDF editing + ✨ AI, in the freed Notes slot).
- **Phase 5 — Android home-screen widgets & quick actions (Todoist-style).** Researched 2026-06-06.
  - **Package:** `home_widget` 0.9.2 (BSD-3 — license-clean). Widget UI is **native** (RemoteViews or Jetpack Glance), *not* Flutter-rendered.
  - **Encrypted-DB pattern (the key insight):** a widget process can't open the SQLCipher DB, BUT the existing headless background-sync isolate (`sync/background_sync.dart`) proves a Dart isolate *can*. So: the widget **displays** from a small **plaintext snapshot** written to `home_widget`'s `HomeWidgetPreferences` (titles + due/done only — the single place data leaves the AES boundary; keep minimal, clear on logout); a widget **quick-add** runs a `@pragma('vm:entry-point')` background callback that opens `openAppDb()` + `TaskDao` + outbox (mirroring `runHeadlessSync`), with an app-launch deep-link fallback for free-text.
  - **Refresh:** call `HomeWidget.updateWidget` on every task write/sync (the `updatePeriodMillis` self-refresh is floored at 30 min and throttled further on HyperOS — don't rely on it).
  - **Styling decision (to make when speccing P5):** plain RemoteViews = "clean/functional"; **Jetpack Glance** (or `renderFlutterWidget`→PNG) = "designer-grade" matching the `Lz*` look. For the premium feel the user wants → lean **Glance**. (Note: RemoteViews can't use the bundled Inter font or the Dart `Lz*` kit — widget styling is re-expressed in XML/Glance.)
  - **App-icon quick actions ("fast menu"):** `quick_actions` plugin (federated; `quick_actions_android` actively maintained). **Max ~4 visible** → recommend **Add task · Add expense · Chat · New note** (reuse existing `showAddTaskSheet` / add-expense sheet; route via `routerProvider.go(...)`). Gotchas: dynamic shortcuts only appear after first launch (re-register each start); cold-start race needs a one-shot `pendingAction` provider replayed once the router mounts; R8 can strip shortcut icon drawables in release builds.
  - **Recommended widget suite (priority):** **P0** Quick-Capture bar (4×1: `+Task / +Expense / +Note / Chat`) · **P1** Today + overdue list (collection widget, per-row complete checkbox → toggle DB + queue sync) · **P2** Budget traffic-light widget (month spend-vs-budget, the differentiator vs Todoist) · P3 Quick-note · P4 Agent pulse (agenda + watcher/job counts).
  - **Mi 15 / HyperOS reality (CRITICAL):** widgets + background quick-add die silently unless **Autostart = on** and **battery = No restrictions**; pin-widget is gated behind a Security-Center toggle. Ship an in-app one-time MIUI setup helper (same pattern as the existing cleartext/LAN-IP helper). Treat `updateWidget`-on-write as the source of truth, not self-refresh.
  - **Effort:** medium for P0+P1 (~2–4 focused days); larger if adopting Glance for premium polish. Main risk = HyperOS background death.
  - This is a **visual** area → use the Visual Companion when speccing P5.
