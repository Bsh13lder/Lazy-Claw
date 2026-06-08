# Phase 1 — Connection Robustness + In-App Self-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **TDD: write the test first, watch it fail, implement, watch it pass, commit.**

**Goal:** Make the mobile app never get stuck on an infinite loading screen (cache-first always, in-memory DB fallback + banner on DB failure, foreground 30-min resync) and finish the half-built in-app APK self-updater so every later phase ships as a one-tap update.

**Architecture:** Approach B from the spec — `openAppDb` gains a resilient wrapper that retries then falls back to an ephemeral `:memory:` DB, so the Riverpod provider graph always has a working `Database` and the `StateError` crash can't happen. A `dbHealthProvider` drives a degraded banner. Provider `load()` becomes crash-safe. Self-update finishes via `ota_update` + `package_info_plus`.

**Tech Stack:** Flutter 3.41 / Dart 3.11, Riverpod, `sqflite_sqlcipher`, `ota_update` 7.1.0 (MIT), `package_info_plus`. Tests: `flutter test` (+ `fake_async` for timers, in-memory sqflite_common_ffi for DB).

**Spec:** `docs/superpowers/specs/2026-06-06-mobile-connection-robustness-design.md`

---

## Shared contracts (LOCK THESE — every task references them)

**In `mobile/lib/local/app_db.dart`:**
```dart
enum DbHealthStatus { ok, degraded }

class DbHealth {
  final DbHealthStatus status;
  final Object? error;
  const DbHealth.ok() : status = DbHealthStatus.ok, error = null;
  const DbHealth.degraded(this.error) : status = DbHealthStatus.degraded;
  bool get isDegraded => status == DbHealthStatus.degraded;
}

class AppDbResult {
  final Database db;
  final DbHealth health;
  const AppDbResult(this.db, this.health);
}

/// Resilient open: retry the encrypted file DB, then fall back to in-memory.
Future<AppDbResult> openAppDbWithFallback({
  FlutterSecureStorage? storage,
  String? pathOverride,
  int retries = 2,
  Future<Database> Function()? openImpl,      // test seam (defaults to openAppDb)
  Future<Database> Function()? openInMemory,  // test seam (defaults to :memory:)
}) async { ... }

/// Wipe the corrupt DB file + key so a fresh one can be minted. Caller re-opens.
Future<void> resetAppDb({FlutterSecureStorage? storage, String? pathOverride}) async { ... }
```

**In `mobile/lib/providers/tasks_provider.dart`** (next to `appDatabaseProvider`):
```dart
/// DB health, OVERRIDDEN in main() with the real AppDbResult.health.
final dbHealthProvider = StateProvider<DbHealth>((ref) => const DbHealth.ok());
```

**In `mobile/lib/sync/foreground_sync.dart` (new):**
```dart
class ForegroundSyncScheduler with WidgetsBindingObserver {
  ForegroundSyncScheduler({required Future<void> Function() onSync,
      Duration interval = const Duration(minutes: 30)});
  void start();      // add observer + arm periodic timer
  void dispose();    // remove observer + cancel timer
}
```

**In `mobile/lib/core/self_update.dart` (new):**
```dart
class UpdateInfo {
  final String version; final int build; final String? sha256; final String apkPath;
  const UpdateInfo({required this.version, required this.build, this.sha256, required this.apkPath});
}
abstract class SelfUpdateGateway {            // testable seam over Dio + PackageInfo
  Future<Map<String, dynamic>> fetchVersion();
  Future<({String version, int build})> currentVersion();
}
class SelfUpdateService {
  SelfUpdateService(this._gw);
  Future<UpdateInfo?> checkForUpdate();       // null when up-to-date / unreachable
  Stream<dynamic> startInstall(UpdateInfo info); // wraps OtaUpdate().execute(...)
}
```

---

## Task ordering (dependency-aware)

- **Wave A (parallel, independent new/disjoint files):** Task 1 (app_db resilience), Task 2 (foreground_sync), Task 3 (UI widgets), Task 6 (self-update subsystem).
- **Wave B (after A — touch shared providers/boot/screens):** Task 4 (provider robustness + reachability + dbHealthProvider), Task 5 (main.dart boot wiring), Task 7 (screen error-state/banner wiring), Task 8 (verify + APK bump).

Agents in Wave A own DISJOINT files and can run in parallel worktrees. Wave B integrates.

---

### Task 1: DB resilience — `AppDbResult` + `openAppDbWithFallback` + `resetAppDb`

**Files:**
- Modify: `mobile/lib/local/app_db.dart`
- Test: `mobile/test/local/app_db_fallback_test.dart`

- [ ] **Step 1 — failing test** (`mobile/test/local/app_db_fallback_test.dart`):
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  test('falls back to in-memory + degraded when file open keeps failing', () async {
    var calls = 0;
    final result = await openAppDbWithFallback(
      retries: 2,
      openImpl: () async { calls++; throw StateError('keychain locked'); },
      openInMemory: () async {
        final db = await databaseFactory.openDatabase(inMemoryDatabasePath);
        await createAppDbSchema(db);
        return db;
      },
    );
    expect(calls, 3);                       // 1 + 2 retries
    expect(result.health.isDegraded, true);
    expect(result.health.error, isA<StateError>());
    expect(await result.db.query('note_cache'), isEmpty); // schema present, usable
  });

  test('returns ok health when file open succeeds first try', () async {
    final result = await openAppDbWithFallback(
      openImpl: () async {
        final db = await databaseFactory.openDatabase(inMemoryDatabasePath);
        await createAppDbSchema(db);
        return db;
      },
    );
    expect(result.health.isDegraded, false);
  });
}
```

- [ ] **Step 2 — run, expect FAIL** (`openAppDbWithFallback`/`AppDbResult` undefined):
  `cd mobile && flutter test test/local/app_db_fallback_test.dart`

- [ ] **Step 3 — implement** in `app_db.dart` (append after `openAppDb`). Use the `DbHealth`/`AppDbResult` contract above. `openImpl` defaults to `() => openAppDb(storage: storage, pathOverride: pathOverride)`; `openInMemory` defaults to opening `inMemoryDatabasePath` + `createAppDbSchema`. Loop `attempt` `0..retries`, `print`/`debugPrint` each failure (no silent swallow), short `Future.delayed(Duration(milliseconds: 150))` between tries, capture `lastError`; on exhaustion call `openInMemory` and return `AppDbResult(mem, DbHealth.degraded(lastError))`. Add `resetAppDb` per contract (`deleteDatabase(dbPath)` in try/catch + `store.delete(key: kDbKeyName)`).

- [ ] **Step 4 — run, expect PASS.**

- [ ] **Step 5 — commit:**
```bash
git add mobile/lib/local/app_db.dart mobile/test/local/app_db_fallback_test.dart
git commit -m "feat(mobile): resilient DB open with in-memory fallback + resetAppDb"
```

---

### Task 2: Foreground 30-min resync scheduler

**Files:**
- Create: `mobile/lib/sync/foreground_sync.dart`
- Test: `mobile/test/sync/foreground_sync_test.dart`

- [ ] **Step 1 — failing test** using `fake_async`:
```dart
import 'package:fake_async/fake_async.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/sync/foreground_sync.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('fires onSync every interval while running', () {
    fakeAsync((async) {
      var n = 0;
      final s = ForegroundSyncScheduler(
        onSync: () async => n++, interval: const Duration(minutes: 30));
      s.start();
      async.elapse(const Duration(minutes: 91));
      expect(n, 3);
      s.dispose();
    });
  });

  test('syncs on resume and cancels timer on pause', () {
    fakeAsync((async) {
      var n = 0;
      final s = ForegroundSyncScheduler(onSync: () async => n++);
      s.start();
      s.didChangeAppLifecycleState(AppLifecycleState.paused);
      async.elapse(const Duration(minutes: 60));
      expect(n, 0);                               // paused → no periodic fire
      s.didChangeAppLifecycleState(AppLifecycleState.resumed);
      expect(n, 1);                               // resume triggers immediate sync
      s.dispose();
    });
  });
}
```

- [ ] **Step 2 — run, expect FAIL.** `cd mobile && flutter test test/sync/foreground_sync_test.dart`
- [ ] **Step 3 — implement** per the contract: `Timer.periodic(interval, (_) => onSync())` in `_arm()`; `start()` adds the observer + arms; `didChangeAppLifecycleState` → on `resumed` call `onSync()` + re-arm, on any other state cancel the timer; `dispose()` removes observer + cancels.
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:**
```bash
git add mobile/lib/sync/foreground_sync.dart mobile/test/sync/foreground_sync_test.dart
git commit -m "feat(mobile): foreground 30-min resync + sync-on-resume scheduler"
```

---

### Task 3: UI — `LzErrorState` + `LzBanner.degraded()`

**Files (READ these first to match the kit's token usage exactly):**
- Read: `mobile/lib/ui/` (tokens `AppColors/AppText/AppSpacing/AppRadii`, existing `LzBanner`, `LzEmptyState`, the barrel/export file).
- Create: `mobile/lib/ui/components/lz_error_state.dart`
- Modify: the existing `LzBanner` component (add a `degraded` factory) + the ui barrel export.
- Test: `mobile/test/ui/lz_error_state_test.dart`

- [ ] **Step 1 — failing widget test:** pump `LzErrorState(message: 'Something broke', onRetry: () => tapped = true)` inside a `MaterialApp`; expect the message text is found and tapping the Retry button flips `tapped`. Pump `LzBanner.degraded(onRetry: ..., onReset: ...)`; expect both actions present.
- [ ] **Step 2 — run, expect FAIL.** `cd mobile && flutter test test/ui/lz_error_state_test.dart`
- [ ] **Step 3 — implement** `LzErrorState` (icon + message + a `LzButton`/existing primary button labelled "Retry", consuming ONLY kit tokens — no hard-coded colors/sizes/text styles, mirror `LzEmptyState`'s structure). Add `LzBanner.degraded({required VoidCallback onRetry, required VoidCallback onReset})` factory next to the existing `LzBanner.offline()`, with text "Local storage unavailable — showing live data from server" + Retry + Reset affordances. Export `LzErrorState` from the ui barrel.
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:**
```bash
git add mobile/lib/ui/ mobile/test/ui/lz_error_state_test.dart
git commit -m "feat(mobile): LzErrorState + LzBanner.degraded() kit widgets"
```

---

### Task 4: Provider robustness + reachability + `dbHealthProvider`

**Files:**
- Modify: `mobile/lib/providers/tasks_provider.dart` (add `dbHealthProvider`; fix `_ReachableNotifier`; crash-safe `load()`).
- Modify: `mobile/lib/providers/notes_provider.dart` (crash-safe `load()`).
- Modify: `mobile/lib/providers/budgets_provider.dart` (crash-safe `load()` — READ it first to match its state shape/method names).
- Test: `mobile/test/providers/load_robustness_test.dart`, `mobile/test/providers/reachable_optimistic_test.dart`

- [ ] **Step 1 — failing tests:**
  - `load()` clears `isLoading` and sets `error` when the DAO read throws. Build a `TasksNotifier`/`NotesNotifier` with a fake DAO whose `list()` throws; call `load()`; assert `state.isLoading == false` and `state.error != null`. (Construct the notifier directly with fakes — they take `(dao, sync)`.)
  - reachability optimistic default: a freshly built `_ReachableNotifier` exposes `state == true` before any probe resolves. (Expose via `reachableProvider` with a fake `Reachability` whose `start()` never completes; read initial state.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:**
  - Add `dbHealthProvider` (StateProvider) per the contract, right under `appDatabaseProvider`.
  - `_ReachableNotifier`: change `super(_reach.value)` → `super(true)` (optimistic; also prevents the boot-time false→true spurious sync). Keep the stream sub + `start()`.
  - In BOTH `TasksNotifier.load()` and `NotesNotifier.load()` (and budgets' equivalent), wrap the cache read:
    ```dart
    Future<void> load() async {
      state = state.copyWith(isLoading: true, error: null);
      try {
        await _refreshFromCache(loading: false);
      } catch (e) {
        state = state.copyWith(isLoading: false, error: e.toString());
      } finally {
        if (state.isLoading) state = state.copyWith(isLoading: false);
      }
      unawaited(_syncThenRefresh());
    }
    ```
- [ ] **Step 4 — run, expect PASS** (+ `flutter test test/providers/`).
- [ ] **Step 5 — commit:**
```bash
git add mobile/lib/providers/ mobile/test/providers/
git commit -m "feat(mobile): crash-safe load(), optimistic reachability, dbHealthProvider"
```

---

### Task 5: Boot wiring — `main.dart`

**Files:**
- Modify: `mobile/lib/main.dart`
- Test: covered by widget smoke test in Task 7 + manual device test (boot path is integration-level).

- [ ] **Step 1 — implement** (replace the swallow block lines 19–24 + override block 29–35):
```dart
final result = await openAppDbWithFallback();   // ALWAYS returns a usable db
await registerBackgroundSync();
runApp(ProviderScope(
  overrides: [
    baseUrlProvider.overrideWith((ref) => baseUrl),
    appDatabaseProvider.overrideWithValue(result.db),
    dbHealthProvider.overrideWith((ref) => result.health),
  ],
  child: const LazyClawApp(),
));
```
  In `_LazyClawAppState`: create a `ForegroundSyncScheduler` in `initState` (onSync triggers `tasksProvider`, `notesProvider`, `budgetsProvider` `syncNow()` via `ref.read`), `start()` it, `dispose()` it in `dispose`. After `checkSession()`, fire a post-login `SelfUpdateService.checkForUpdate()` and stash the result into an `updateAvailableProvider` (StateProvider<UpdateInfo?>) — non-blocking, see Task 6.
- [ ] **Step 2 — run** `cd mobile && flutter analyze` — expect no errors from main.dart.
- [ ] **Step 3 — commit:**
```bash
git add mobile/lib/main.dart
git commit -m "feat(mobile): always-on DB boot + foreground sync + startup update check"
```

---

### Task 6: Finish in-app self-update

**Files:**
- Modify: `mobile/pubspec.yaml` — add `ota_update: ^7.1.0`, `package_info_plus: ^10.1.0`.
- Modify: `mobile/lib/core/constants/app_constants.dart` — drop the hand-synced `kAppVersion`/`kAppBuild` as the source of truth (keep as fallback only); read real values via `package_info_plus`.
- Create: `mobile/lib/core/self_update.dart` — `SelfUpdateService` + `UpdateInfo` + `updateAvailableProvider` (StateProvider<UpdateInfo?>) per contract. `checkForUpdate()` GETs `/api/mobile/version`, reads `PackageInfo.fromPlatform()`, reuses the existing `isUpdateAvailable(...)` from `core/version_check.dart`, returns `UpdateInfo` (apkPath `'/api/mobile/apk'`, pass `sha256` through) or null.
- Modify: `mobile/lib/screens/settings_screen.dart` — replace the "update available" SnackBar in `_checkForUpdate()` with an `LzDialog`/`LzBanner` showing `current → latest` + an **Update** button that drives `OtaUpdate().execute(baseUrl + apkPath, sha256checksum: info.sha256)` and renders progress on an `LzProgressBar`.
- Modify: `mobile/android/app/src/main/AndroidManifest.xml` — add `<uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES"/>`, the `ota_update` `FileProvider` (`sk.fourq.otaupdate.OtaUpdateFileProvider`, authority `${applicationId}.ota_update_provider`) + `InstallResultReceiver`.
- Create: `mobile/android/app/src/main/res/xml/filepaths.xml` — `<files-path name="internal_apk_storage" path="ota_update/"/>`.
- Test: `mobile/test/core/self_update_test.dart`

- [ ] **Step 1 — failing test:** with a fake `SelfUpdateGateway` (server `{version:'1.8.0', build:14}`, current `(1.7.1, 13)`), `checkForUpdate()` returns a non-null `UpdateInfo(version:'1.8.0', build:14)`; with server build == current, returns null; with `fetchVersion` throwing, returns null (no crash). (Keep existing `version_check_test.dart` green.)
- [ ] **Step 2 — run, expect FAIL.** `cd mobile && flutter test test/core/self_update_test.dart`
- [ ] **Step 3 — implement** the service + provider + the `PackageInfo`-backed version source. Then wire the Settings dialog + manifest/filepaths (compile-level; install path is device-tested).
- [ ] **Step 4 — run, expect PASS** + `cd mobile && flutter pub get && flutter analyze`.
- [ ] **Step 5 — commit:**
```bash
git add mobile/pubspec.yaml mobile/pubspec.lock mobile/lib/core/self_update.dart \
  mobile/lib/core/constants/app_constants.dart mobile/lib/core/version_check.dart \
  mobile/lib/screens/settings_screen.dart mobile/android/app/src/main/AndroidManifest.xml \
  mobile/android/app/src/main/res/xml/filepaths.xml mobile/test/core/self_update_test.dart
git commit -m "feat(mobile): finish in-app self-update (ota_update + package_info_plus + install flow)"
```

---

### Task 7: Screen wiring — error-state + degraded banner

**Files (READ each first):**
- Modify: `mobile/lib/screens/notes_screen.dart` (render precedence: items → error → empty; add degraded banner from `dbHealthProvider`).
- Modify: `mobile/lib/screens/tasks_screen.dart` (same).
- Modify: the Money/Budgets screen (same — find it under `mobile/lib/screens/`).
- Test: `mobile/test/screens/error_state_render_test.dart`

- [ ] **Step 1 — failing widget test:** pump `NotesScreen` (or its body) with an overridden `notesProvider` whose state is `isLoading:false, notes:[], error:'boom'` → expect `LzErrorState` + Retry present, no infinite skeleton. Override `dbHealthProvider` to `DbHealth.degraded(...)` → expect the degraded banner present.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** the render precedence on each screen body: cached items → `error != null && items.isEmpty` ? `LzErrorState(message: error, onRetry: load)` : empty state. Watch `dbHealthProvider`; when degraded, show `LzBanner.degraded(onRetry: <re-open via a small reopen action>, onReset: <confirm → resetAppDb → restart providers>)` near the existing offline banner.
- [ ] **Step 4 — run, expect PASS** + `cd mobile && flutter test`.
- [ ] **Step 5 — commit:**
```bash
git add mobile/lib/screens/ mobile/test/screens/error_state_render_test.dart
git commit -m "feat(mobile): error-state + degraded banner on offline-first screens"
```

---

### Task 8: Verify + version bump + APK

- [ ] **Step 1 — full suite green:** `cd mobile && flutter analyze && flutter test` → expect 0 analyze errors, all tests pass (note: pre-existing unrelated chat test files may already be modified in the tree; scope concern only if they fail for reasons unrelated to Phase 1).
- [ ] **Step 2 — bump version:** in `mobile/pubspec.yaml` bump `version:` (e.g. `1.7.1+13` → `1.8.0+14`). (PackageInfo now reads this; `app_constants` fallback updated to match if still used.)
- [ ] **Step 3 — build APK:** `bash scripts/build-mobile-apk.sh` → produces `mobile/dist/app-release.apk` + `version.json` (build 14, fresh sha256).
- [ ] **Step 4 — commit:**
```bash
git add mobile/pubspec.yaml
git commit -m "chore(mobile): bump to 1.8.0+14 (Phase 1 connection fix + self-update)"
```
- [ ] **Step 5 — device-test checklist (Mi 15, manual):**
  - Existing install → Settings → Check for update → see `1.7.1(13) → 1.8.0(14)` → Update → APK downloads w/ progress → installer opens (first time: grant "Install unknown apps") → app relaunches on 1.8.0. *(Requires the new APK to be signed with the SAME key as the installed one — currently the debug key; build on this Mac.)*
  - Open Notes immediately after launch → list renders from cache, **no infinite spinner**.
  - Airplane-mode launch → Notes/Tasks/Budgets show cached data + offline banner, never a stuck skeleton.
  - Leave app open 30 min (or shorten interval to test) → background resync runs; reopen from background → sync-on-resume fires.
  - (Degraded path is hard to force on-device; covered by Task 1 unit test.)

---

## Self-review notes
- **Spec coverage:** Goals 1–5 → Tasks 1,4,5,7 + 2; Goal 6 (self-update) → Task 6. Files-touched list matches Tasks 1–7.
- **Type consistency:** `AppDbResult.health: DbHealth` used identically in Task 1 (def), Task 5 (`result.health`), `dbHealthProvider<DbHealth>` (Task 4 def, Task 5 override, Task 7 read). `UpdateInfo`/`updateAvailableProvider` defined in Task 6, consumed in Task 5.
- **Parallelism:** Wave A tasks (1,2,3,6) own disjoint files → safe to fan in worktrees. Wave B (4,5,7,8) integrates and must run after A.
