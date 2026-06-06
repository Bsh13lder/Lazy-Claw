# Mobile Connection Robustness, Self-Update & Offline-First Hardening — Phase 1

- **Date:** 2026-06-06
- **Status:** Draft for review
- **Branch:** `feat/flutter-mobile`
- **Scope:** Flutter app (`mobile/`) only. **No backend changes** (in-app self-update reuses the existing `/api/mobile/{apk,version}` endpoints).

---

## 1. Context

This is **Phase 1 of a 10-phase Flutter polish initiative**:

| Phase | Theme | Status |
|---|---|---|
| **1 — Connection / robustness + in-app self-update** | Kill the "Notes keeps loading / loses connection" bug; offline-first screens always render; foreground 30-min resync; **finish the in-app APK self-updater** so every later phase delivers as a one-tap update. | **This spec** |
| 2 — Tasks polish | Advanced hybrid smart-add (multi-intent: task/expense/time/project), full task detail/edit panel, project CRUD, calendar view + per-project colors. | Banked |
| 3 — Notes → Tasks | Move Notes into the Tasks tab (segmented `Tasks \| Notes`); "what is this note about" smart preview; in-note navigation. | Banked |
| 4 — Documents tab | Freed Notes slot becomes Documents (Sheets/Docs/PDF), **native** Flutter editing + ✨ AI. | Banked |
| 5 — Android widgets & quick actions | Home-screen widgets (quick-capture, today list, budget) + app-icon long-press quick actions, Todoist-style. | Banked |
| 6 — Premium polish & trust | Biometric app lock · haptics + entrance/success animations · sync-transparency/conflict review · command palette. | Banked |
| 7 — Voice | STT quick-add + push-to-talk voice chat (backend whisper already runs) + optional spoken replies. | Banked |
| 8 — Agent-on-the-go | Browser live-view + 1-tap checkpoint approve/reject · live goal progress · Upwork job intake (✅/⏭) · session replay viewer. | Banked |
| 9 — OS capture + daily habit | Android share-target ("Share to LazyClaw" → task/note/agent) · app shortcuts (with P5) · morning briefing + daily journal. | Banked |
| 10 — Native push (big bet) | Replace Telegram-only push with FCM or self-hosted ntfy/UnifiedPush — contentless wake-ping + pull over WS + 1-tap actions; unlocks watcher/bg-task alerts on the phone. | Banked |

Each phase gets its own spec → plan → build. Order after P1 is flexible — the cheap **Phase 6** wins (haptics/biometric) can sprinkle in alongside other phases, and Phase 5's app-icon quick actions overlap Phase 9's capture work (build once, share). The **live agent-activity streaming timeline** in chat (a Phase-8/chat enhancement) rides on the existing WS frames and can fold into whichever chat-touching phase lands first.

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
6. **In-app self-update works end-to-end** — the app detects a newer published APK, downloads it with progress, verifies the `sha256`, and launches the installer — so every later phase ships as a one-tap update from the phone.

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

### 6.8 In-app self-update — finish the half-built updater
The backend already serves `GET /api/mobile/version` (`{version, build, sha256, ...}`) and `GET /api/mobile/apk`; the app already has `core/version_check.dart` (`isUpdateAvailable`) + a Settings "Check for update" button that only shows a SnackBar. Missing: the **download + install** half and a **trustworthy version source**.

- **Packages:** add **`ota_update` 7.1.0 (MIT)** — streams the APK download with progress, verifies the published **`sha256`** (`sha256checksum:`), then fires the system installer — and **`package_info_plus`** to read the *real* running `version`/`buildNumber`.
- **Kill the hardcoded-version footgun:** today `core/constants/app_constants.dart` (`kAppVersion`/`kAppBuild`) is hand-synced with `pubspec.yaml` and the build script never touches it → false "you're up to date". Replace those reads with `PackageInfo.fromPlatform()` so **`pubspec.yaml` is the single source of truth**.
- **Android plumbing** (`mobile/android/app/src/main/AndroidManifest.xml` + `res/xml/filepaths.xml`): add `REQUEST_INSTALL_PACKAGES`, the `ota_update` `FileProvider` + result-receiver, and the file-paths resource. First install triggers Android's one-time "Install unknown apps" grant (expected).
- **UX:** keep the Settings "Check for update" entry but, on update-available, show an `LzDialog`/`LzBanner` (`v1.7.1 (13) → v1.8.0 (14)` + **Update**) wired to an `LzProgressBar` from `OtaEvent` progress; add **one** lightweight post-login startup check that shows a non-blocking banner if a newer build exists (no auto-download).
- **⚠️ Signing-key discipline (carry into the plan, not code):** release APKs are currently signed with the **debug key**. Android refuses a self-update signed by a different key (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`). Self-update therefore works **only if every build uses the same signing key** — fine while always building on this Mac, but a **stable release keystore** is the durable fix and is a prerequisite for relying on OTA across machines/CI.
- **iOS:** not applicable (Apple forbids sideload self-update) — Android-only, not a regression.

### 6.9 Release / delivery convention (applies to EVERY shippable phase)
On finishing any phase: **bump `pubspec.yaml` `version:`** (name and/or `+build`) → run `scripts/build-mobile-apk.sh` (publishes `mobile/dist/app-release.apk` + `version.json` with the new version + sha256) → the running app's self-updater (§6.8) detects the higher build and offers the one-tap update. This is the standing "bump the APK version so I can update from the phone" requirement.

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
7. **self-update** — `isUpdateAvailable` already has tests; add: version source reads real `PackageInfo` values; the update flow surfaces a banner only when the server build is strictly higher; sha256 is passed through to the updater. (The actual install intent is device-tested, not unit-tested.)

Coverage target: **80%+** on touched files (per repo testing rules).

---

## 10. Files touched

- `mobile/lib/local/app_db.dart` — `AppDbResult`, retry + in-memory fallback, `resetAppDb()`.
- `mobile/lib/main.dart` — always-override boot, `dbHealthProvider`, scheduler registration.
- `mobile/lib/providers/notes_provider.dart`, `tasks_provider.dart`, `budgets_provider.dart` — `load()` robustness, reachability init.
- `mobile/lib/screens/notes_screen.dart`, `tasks_screen.dart`, and the Money/Budgets screen *(exact path confirmed at implementation)* — error-state + banner wiring.
- `mobile/lib/ui/` — `LzErrorState`, `LzBanner.degraded()`.
- `mobile/lib/sync/foreground_sync.dart` — **new** foreground scheduler + lifecycle sync.
- **Self-update:** `mobile/pubspec.yaml` (`ota_update`, `package_info_plus`), `mobile/lib/core/constants/app_constants.dart` (drop hardcoded version → `PackageInfo`), `mobile/lib/screens/settings_screen.dart` (update dialog + progress), `mobile/lib/main.dart` (post-login update check), `mobile/android/app/src/main/AndroidManifest.xml` + `mobile/android/app/src/main/res/xml/filepaths.xml` (install permission + FileProvider), small new `mobile/lib/core/self_update.dart` service.
- `mobile/test/...` — the tests above.

No files outside `mobile/`. (Self-update reuses the existing `lazyclaw/gateway/routes/mobile.py` endpoints — no backend edit.)

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
- **Phase 6 — Premium polish & trust.** Researched 2026-06-06; all small effort, big perceived-quality jump.
  - **Biometric app lock** (`local_auth`): Face/fingerprint gate on launch + resume-from-background before releasing the SQLCipher key. Table-stakes for an E2E-encrypted app holding chat history, the credential vault, and finances.
  - **Haptics + animations** (`HapticFeedback` built-in + `flutter_animate`, a Flutter Favorite): tactile feedback on send/complete/swipe + entrance/success motion. The motion tokens (`AppMotion`) already exist but nothing fires them yet — cheapest path to a "Things 3 / Linear" feel. Can sprinkle in during other phases.
  - **Sync transparency / conflict review:** the engine already logs conflicts (never drops them) into a `conflicts` table with a `conflicts_sheet.dart` stub — make it legible (mine-vs-server diff cards + keep-mine/keep-server, retry/dead-letter visibility). Turns an invisible safety net into a trust feature.
  - **Command palette:** ⌘K-style fuzzy quick-switcher (custom `LzBottomSheet` + fuzzy filter for design coherence, or the `command_palette` pkg) to jump to any screen / fire a quick action / ask the agent.
- **Phase 7 — Voice.** Backend whisper STT already runs (`lazyclaw/audio/stt.py` + host Metal bridge on :18790; `/api/audio/transcribe`). Mobile is the missing half: a mic button in the chat composer + on quick-add sheets. Two tiers — (a) on-device `speech_to_text` for instant short quick-add, (b) `record` → upload to server whisper for accuracy (with a live waveform). Optional spoken replies via `flutter_tts`. Voice is the headline "wow" for an agent app.
- **Phase 8 — Agent-on-the-go.** The differentiator; most endpoints already exist (mostly small mobile UI).
  - **Browser live-view + 1-tap checkpoints:** `/api/browser/frame` + `/api/browser/checkpoint/*` — render the live frame, approve/reject risky actions from the phone. Supervise agent work while away from the desk (highest-leverage interaction).
  - **Live goal progress:** poll `/api/goals/{goal_id}` — watch DRAFTING→EXECUTING→DONE with last-action + progress.
  - **Upwork job intake (✅/⏭):** surface pending contracts → `/api/goals/code-task` intake on accept (mirrors the Telegram 1-tap-accept).
  - **Session replay viewer:** `/api/replay/traces` + share tokens — review/share proof-of-work runs.
  - **Live agent-activity timeline** (chat enhancement): upgrade the static tool-chips into a flowing "thinking → calling browser → drafting" feed off the existing WS frames (`flutter_animate`, no new transport).
- **Phase 9 — OS capture + daily habit.**
  - **Android share-target** (`receive_sharing_intent`): "Share to LazyClaw" from any app → quick sheet routes text/URL/image to a task, a note, or the agent ("summarize this URL"). Needs `SEND`/`SEND_MULTIPLE` intent filters. Turns the whole OS into a capture funnel.
  - **App-icon quick actions:** shared with Phase 5 (build once).
  - **Daily habit:** morning briefing (`/api/lazybrain/morning-briefing`) + daily journal (`/api/lazybrain/journal/{iso_date}`) — both endpoints ready; small editor/card UI. Pairs into a brief→work→journal loop.
- **Phase 10 — Native push (big bet).** Replace today's Telegram-only push so the app stands alone. Display + 1-tap action buttons via the already-present `flutter_local_notifications` (`AndroidNotificationAction`); the missing piece is a wake transport + a backend device-token registry + per-event routing.
  - **Privacy-first pattern:** send a **contentless wake-ping** and let the app pull the E2E payload over the existing WS/HTTPS (the Signal pattern) — no plaintext transits the push provider.
  - **Transport choice (decide when speccing):** `firebase_messaging` (most reliable/battery-efficient, but routes a ping through Google + needs a Firebase project) **vs** self-hosted **ntfy/UnifiedPush** (the backend already speaks ntfy; keeps everything on the user's server — aligns with the self-hosted ethos). Lean ntfy/UnifiedPush to stay off Google.
  - Unlocks watcher alerts, background-task-done, reminders, and approval requests on the phone. **Large effort**; HyperOS battery/autostart caveats apply (same MIUI helper as P5).
