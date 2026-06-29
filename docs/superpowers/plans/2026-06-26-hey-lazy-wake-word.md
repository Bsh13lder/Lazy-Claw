# Hey Lazy Wake Word Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an always-on, on-device "Hey Lazy" wake word to the Flutter app that launches the existing voice assistant — even with the screen off — while keeping the manual ✨ button.

**Architecture:** A `flutter_foreground_task` `microphone` service keeps the process alive and owns the notification/wake-lock/screen-wake. **Vosk runs on the main isolate** (grammar-limited to `["hey lazy","[unk]"]`) — NOT a background isolate. On detection, a full-screen-intent notification surfaces the assistant over the lock screen and the existing `LazyAssistantController` pipeline runs. Opt-in, off by default.

**Tech Stack:** Flutter, Riverpod, `vosk_flutter` 0.3.48, `flutter_foreground_task` 9.2.2, Kotlin (MainActivity lock-screen flags), `flutter_secure_storage`.

## Global Constraints

- Wake word runs **100% on-device** — audio never leaves the phone.
- **Opt-in, OFF by default** (Settings toggle), like the existing assistant privacy posture.
- **Vosk on the MAIN isolate only** — never the foreground-task background isolate (`vosk_flutter` #21, flutter/flutter #98591).
- Engine = **Vosk** (`vosk_flutter` 0.3.48; fallback `vosk_flutter_2` 1.0.5 if Dart-SDK version-solve fails). Apache-2.0. Grammar `['hey lazy', '[unk]']`, sample rate **16000 Hz mono PCM16**.
- Service = **`flutter_foreground_task` 9.2.2** (MIT), service type `microphone`, `eventAction: nothing()`, `allowWakeLock: true`.
- **No silent boot resume** — Android 14/15 forbid starting a mic FG service from `BOOT_COMPLETED`. Re-arm on next foreground only.
- Lock-screen surfacing = **full-screen-intent notification** (NOT `startActivity` from the service); `MainActivity` sets `setShowWhenLocked(true)`+`setTurnScreenOn(true)`. Android 14 needs `canUseFullScreenIntent()` + `ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT`; fallback `SYSTEM_ALERT_WINDOW`.
- Vosk model `vosk-model-small-en-us-0.15` (40 MB) is **downloaded on first enable**, not bundled. ABI `arm64-v8a`.
- Follow existing app conventions: files under `mobile/lib/wake/`, providers via Riverpod, persistence via `flutter_secure_storage`, immutable state objects, files < 400 lines.
- Existing reuse: the wake bridge calls the existing `lazyAssistantProvider` / `LazyAssistantController.startListening()` and `/assistant` route — do NOT duplicate the pipeline.

---

## File Structure

- `mobile/lib/wake/wake_event.dart` — `WakeEvent` value object.
- `mobile/lib/wake/wake_recognizer.dart` — `WakeRecognizer` abstract seam + `parseVoskText()` helper.
- `mobile/lib/wake/wake_word_detector.dart` — `WakeWordDetector` (phrase match + debounce). **Pure, unit-tested.**
- `mobile/lib/wake/wake_service.dart` — `WakeService` seam + `ForegroundWakeService` (flutter_foreground_task impl).
- `mobile/lib/wake/miui_permissions.dart` — `MiuiSettingTarget` + `miuiTargets()` + launcher. **Targets unit-tested.**
- `mobile/lib/wake/wake_settings.dart` — `wakeEnabledProvider` (persisted toggle) + wiring to `WakeService`.
- `mobile/lib/wake/vosk_wake_recognizer.dart` — real `WakeRecognizer` over `vosk_flutter` (main isolate).
- `mobile/lib/wake/wake_model_downloader.dart` — download/unzip/verify the Vosk model.
- `mobile/lib/wake/wake_bridge.dart` — on `WakeEvent` → full-screen notification + navigate `/assistant` + auto-listen.
- `mobile/android/.../MainActivity.kt` — lock-screen flags.
- `mobile/android/app/src/main/AndroidManifest.xml` — permissions + service.
- Tests under `mobile/test/wake/`.

---

### Task 1: WakeEvent + WakeWordDetector (phrase match + debounce)

**Files:**
- Create: `mobile/lib/wake/wake_event.dart`
- Create: `mobile/lib/wake/wake_recognizer.dart`
- Create: `mobile/lib/wake/wake_word_detector.dart`
- Test: `mobile/test/wake/wake_word_detector_test.dart`

**Interfaces:**
- Produces: `class WakeEvent { final DateTime at; const WakeEvent(this.at); }`
- Produces: `abstract interface class WakeRecognizer { Stream<String> get results; Future<void> start(); Future<void> stop(); }`
- Produces: `String? parseVoskText(String json)` — returns the `"text"` field or null.
- Produces: `class WakeWordDetector { WakeWordDetector(WakeRecognizer recognizer, {String phrase, Duration debounce, DateTime Function() clock}); Stream<WakeEvent> get wakes; Future<void> start(); Future<void> stop(); }`

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/wake/wake_word_detector_test.dart
import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/wake/wake_recognizer.dart';
import 'package:lazyclaw_mobile/wake/wake_word_detector.dart';

class _FakeRecognizer implements WakeRecognizer {
  final _ctrl = StreamController<String>.broadcast();
  bool started = false;
  void emit(String json) => _ctrl.add(json);
  @override
  Stream<String> get results => _ctrl.stream;
  @override
  Future<void> start() async => started = true;
  @override
  Future<void> stop() async => started = false;
}

void main() {
  test('fires a WakeEvent on "hey lazy", ignores other phrases', () async {
    final rec = _FakeRecognizer();
    final det = WakeWordDetector(rec);
    final got = <WakeEvent>[];
    final sub = det.wakes.listen(got.add);
    await det.start();

    rec.emit('{"text": "what time is it"}');
    rec.emit('{"text": "hey lazy"}');
    await Future<void>.delayed(Duration.zero);

    expect(got.length, 1);
    await sub.cancel();
  });

  test('debounces repeated detections within the window', () async {
    var t = DateTime(2026, 6, 26, 12, 0, 0);
    final rec = _FakeRecognizer();
    final det = WakeWordDetector(rec,
        debounce: const Duration(seconds: 2), clock: () => t);
    final got = <WakeEvent>[];
    final sub = det.wakes.listen(got.add);
    await det.start();

    rec.emit('{"text": "hey lazy"}');         // fires
    await Future<void>.delayed(Duration.zero);
    t = t.add(const Duration(milliseconds: 500));
    rec.emit('{"text": "hey lazy"}');         // within 2s → ignored
    await Future<void>.delayed(Duration.zero);
    t = t.add(const Duration(seconds: 3));
    rec.emit('{"text": "hey lazy"}');         // after window → fires
    await Future<void>.delayed(Duration.zero);

    expect(got.length, 2);
    await sub.cancel();
  });

  test('matches phrase case-insensitively and trims surrounding speech', () async {
    final rec = _FakeRecognizer();
    final det = WakeWordDetector(rec);
    final got = <WakeEvent>[];
    final sub = det.wakes.listen(got.add);
    await det.start();
    rec.emit('{"text": "  Hey Lazy  "}');
    await Future<void>.delayed(Duration.zero);
    expect(got.length, 1);
    await sub.cancel();
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/wake/wake_word_detector_test.dart`
Expected: FAIL — `wake_recognizer.dart` / `wake_word_detector.dart` don't exist.

- [ ] **Step 3: Write minimal implementation**

```dart
// mobile/lib/wake/wake_event.dart
/// A confirmed "Hey Lazy" detection. Carries only the time it fired — the
/// recognizer's audio never leaves the phone and is not retained.
class WakeEvent {
  final DateTime at;
  const WakeEvent(this.at);
}
```

```dart
// mobile/lib/wake/wake_recognizer.dart
import 'dart:convert';

/// Narrow seam over the wake-word recognizer so [WakeWordDetector] is unit
/// testable without Vosk or a microphone. The only production implementation
/// is `VoskWakeRecognizer` (Task 7).
abstract interface class WakeRecognizer {
  /// Final recognition results as Vosk JSON strings, e.g. '{"text":"hey lazy"}'.
  Stream<String> get results;
  Future<void> start();
  Future<void> stop();
}

/// Extracts the `text` field from a Vosk result JSON string; null if absent
/// or unparseable. Tolerant: never throws on malformed input.
String? parseVoskText(String json) {
  try {
    final m = jsonDecode(json);
    if (m is Map && m['text'] is String) return m['text'] as String;
  } catch (_) {/* fall through */}
  return null;
}
```

```dart
// mobile/lib/wake/wake_word_detector.dart
import 'dart:async';
import 'wake_event.dart';
import 'wake_recognizer.dart';

/// Turns a stream of Vosk recognition results into debounced [WakeEvent]s when
/// the configured wake phrase is heard. Pure logic — no audio, no platform.
class WakeWordDetector {
  WakeWordDetector(
    this._recognizer, {
    String phrase = 'hey lazy',
    Duration debounce = const Duration(seconds: 2),
    DateTime Function() clock = DateTime.now,
  })  : _phrase = phrase.toLowerCase().trim(),
        _debounce = debounce,
        _clock = clock;

  final WakeRecognizer _recognizer;
  final String _phrase;
  final Duration _debounce;
  final DateTime Function() _clock;

  final _wakes = StreamController<WakeEvent>.broadcast();
  StreamSubscription<String>? _sub;
  DateTime? _lastFired;

  Stream<WakeEvent> get wakes => _wakes.stream;

  Future<void> start() async {
    _sub ??= _recognizer.results.listen(_onResult);
    await _recognizer.start();
  }

  Future<void> stop() async {
    await _recognizer.stop();
    await _sub?.cancel();
    _sub = null;
  }

  void _onResult(String json) {
    final text = parseVoskText(json)?.toLowerCase().trim();
    if (text == null || !text.contains(_phrase)) return;
    final now = _clock();
    if (_lastFired != null && now.difference(_lastFired!) < _debounce) return;
    _lastFired = now;
    _wakes.add(WakeEvent(now));
  }

  Future<void> dispose() async {
    await stop();
    await _wakes.close();
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/wake/wake_word_detector_test.dart`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/wake/wake_event.dart mobile/lib/wake/wake_recognizer.dart mobile/lib/wake/wake_word_detector.dart mobile/test/wake/wake_word_detector_test.dart
git commit -m "feat(mobile): Hey Lazy wake-word detector core (phrase match + debounce)"
```

---

### Task 2: MIUI permission targets

**Files:**
- Create: `mobile/lib/wake/miui_permissions.dart`
- Test: `mobile/test/wake/miui_permissions_test.dart`

**Interfaces:**
- Produces: `class MiuiSettingTarget { final String key; final String label; final String? action; final String? package; final String? component; final Map<String,String> extras; ... }`
- Produces: `List<MiuiSettingTarget> miuiTargets(String packageName)` — autostart, battery, background-popup (the load-bearing one), in order.

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/wake/miui_permissions_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/wake/miui_permissions.dart';

void main() {
  test('emits the three MIUI targets with correct components', () {
    final t = miuiTargets('com.lazyclaw.lazyclaw_mobile');
    expect(t.map((e) => e.key), ['autostart', 'battery', 'background_popup']);

    final autostart = t.firstWhere((e) => e.key == 'autostart');
    expect(autostart.package, 'com.miui.securitycenter');
    expect(autostart.component,
        'com.miui.permcenter.autostart.AutoStartManagementActivity');

    final popup = t.firstWhere((e) => e.key == 'background_popup');
    expect(popup.action, 'miui.intent.action.APP_PERM_EDITOR');
    expect(popup.component,
        'com.miui.permcenter.permissions.PermissionsEditorActivity');
    expect(popup.extras['extra_pkgname'], 'com.lazyclaw.lazyclaw_mobile');
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/wake/miui_permissions_test.dart`
Expected: FAIL — `miui_permissions.dart` missing.

- [ ] **Step 3: Write minimal implementation**

```dart
// mobile/lib/wake/miui_permissions.dart
import 'dart:io';
import 'package:flutter/services.dart';

/// One MIUI settings page we deep-link the user to. Component/action strings are
/// reverse-engineered and vary by MIUI/HyperOS version — the launcher wraps each
/// in try/catch and falls back to the app-details page.
class MiuiSettingTarget {
  final String key;
  final String label;
  final String? action;
  final String? package;
  final String? component;
  final Map<String, String> extras;
  const MiuiSettingTarget({
    required this.key,
    required this.label,
    this.action,
    this.package,
    this.component,
    this.extras = const {},
  });
}

/// The three MIUI permissions "Hey Lazy" needs to survive, in setup order.
/// `background_popup` is load-bearing: without it MIUI silently blocks the
/// background activity start that surfaces the assistant over the lock screen.
List<MiuiSettingTarget> miuiTargets(String packageName) => [
      MiuiSettingTarget(
        key: 'autostart',
        label: 'Allow autostart',
        package: 'com.miui.securitycenter',
        component: 'com.miui.permcenter.autostart.AutoStartManagementActivity',
      ),
      MiuiSettingTarget(
        key: 'battery',
        label: 'No battery restrictions',
        package: 'com.miui.powerkeeper',
        component: 'com.miui.powerkeeper.ui.HiddenAppsConfigActivity',
        extras: {'package_name': packageName, 'package_label': 'LazyClaw'},
      ),
      MiuiSettingTarget(
        key: 'background_popup',
        label: 'Display pop-up while running in background',
        action: 'miui.intent.action.APP_PERM_EDITOR',
        package: 'com.miui.securitycenter',
        component: 'com.miui.permcenter.permissions.PermissionsEditorActivity',
        extras: {'extra_pkgname': packageName},
      ),
    ];

/// True on Xiaomi/Redmi/MIUI hardware (best-effort).
bool isMiui() =>
    Platform.isAndroid &&
    (Platform.operatingSystemVersion.toLowerCase().contains('miui'));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/wake/miui_permissions_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/wake/miui_permissions.dart mobile/test/wake/miui_permissions_test.dart
git commit -m "feat(mobile): MIUI permission deep-link targets for Hey Lazy"
```

---

### Task 3: WakeService seam + persisted enable toggle

**Files:**
- Create: `mobile/lib/wake/wake_service.dart` (seam only this task; real impl in Task 6)
- Create: `mobile/lib/wake/wake_settings.dart`
- Test: `mobile/test/wake/wake_settings_test.dart`

**Interfaces:**
- Produces: `abstract interface class WakeService { Future<bool> start(); Future<void> stop(); Future<bool> isRunning(); }`
- Produces: `class WakeEnabledController extends StateNotifier<bool>` with `Future<void> set(bool)` that persists (`flutter_secure_storage`, key `wake.enabled`) and calls `WakeService.start()/stop()`.

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/wake/wake_settings_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/wake/wake_service.dart';
import 'package:lazyclaw_mobile/wake/wake_settings.dart';

class _FakeService implements WakeService {
  bool running = false;
  int starts = 0, stops = 0;
  @override
  Future<bool> start() async { running = true; starts++; return true; }
  @override
  Future<void> stop() async { running = false; stops++; }
  @override
  Future<bool> isRunning() async => running;
}

class _MemStore implements WakeStore {
  String? v;
  @override
  Future<String?> read() async => v;
  @override
  Future<void> write(String value) async => v = value;
}

void main() {
  test('enabling starts the service and persists; disabling stops it', () async {
    final svc = _FakeService();
    final store = _MemStore();
    final c = WakeEnabledController(svc, store);

    await c.set(true);
    expect(c.debugState, true);
    expect(svc.starts, 1);
    expect(store.v, 'true');

    await c.set(false);
    expect(c.debugState, false);
    expect(svc.stops, 1);
    expect(store.v, 'false');
  });

  test('restores persisted enabled state on construction', () async {
    final store = _MemStore()..v = 'true';
    final svc = _FakeService();
    final c = WakeEnabledController(svc, store);
    await c.restore();
    expect(c.debugState, true);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/wake/wake_settings_test.dart`
Expected: FAIL — files missing.

- [ ] **Step 3: Write minimal implementation**

```dart
// mobile/lib/wake/wake_service.dart
/// Controls the always-on wake-word foreground service. The real implementation
/// (`ForegroundWakeService`, Task 6) drives flutter_foreground_task + Vosk; tests
/// use a fake. start() returns false if it could not arm (e.g. mic denied).
abstract interface class WakeService {
  Future<bool> start();
  Future<void> stop();
  Future<bool> isRunning();
}
```

```dart
// mobile/lib/wake/wake_settings.dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'wake_service.dart';

/// Tiny persistence seam so the toggle is unit-testable without secure storage.
abstract interface class WakeStore {
  Future<String?> read();
  Future<void> write(String value);
}

class SecureWakeStore implements WakeStore {
  static const _key = 'wake.enabled';
  final FlutterSecureStorage _s;
  SecureWakeStore([this._s = const FlutterSecureStorage()]);
  @override
  Future<String?> read() => _s.read(key: _key);
  @override
  Future<void> write(String value) => _s.write(key: _key, value: value);
}

/// Opt-in "Hey Lazy hotword" toggle. OFF by default. Enabling arms the service;
/// disabling stops it. Persisted so it survives restarts (re-armed on foreground).
class WakeEnabledController extends StateNotifier<bool> {
  WakeEnabledController(this._service, this._store) : super(false);
  final WakeService _service;
  final WakeStore _store;

  bool get debugState => state;

  Future<void> restore() async {
    final v = await _store.read();
    state = v == 'true';
  }

  Future<void> set(bool enabled) async {
    state = enabled;
    await _store.write(enabled ? 'true' : 'false');
    if (enabled) {
      final ok = await _service.start();
      if (!ok) state = false; // could not arm (mic denied) → reflect reality
    } else {
      await _service.stop();
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/wake/wake_settings_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/wake/wake_service.dart mobile/lib/wake/wake_settings.dart mobile/test/wake/wake_settings_test.dart
git commit -m "feat(mobile): Hey Lazy enable toggle + WakeService seam (persisted)"
```

---

### Task 4: Dependencies, manifest permissions, MainActivity lock-screen flags

**Files:**
- Modify: `mobile/pubspec.yaml` (add deps)
- Modify: `mobile/android/app/src/main/AndroidManifest.xml`
- Modify: `mobile/android/app/src/main/kotlin/.../MainActivity.kt`
- Modify: `mobile/android/app/build.gradle(.kts)` (ensure `arm64-v8a` abiFilter already present per build 71)

**Interfaces:** none (build-green deliverable). Verified by `flutter build apk --debug` succeeding and the app launching over the lock screen on-device.

- [ ] **Step 1: Add dependencies**

```yaml
# mobile/pubspec.yaml — under dependencies:
  vosk_flutter: ^0.3.48
  flutter_foreground_task: ^9.2.2
  android_intent_plus: ^5.0.0   # launch MIUI/settings intents with extras
```

Run: `cd mobile && flutter pub get`
Expected: resolves. If `vosk_flutter` version-solve fails on the Dart SDK floor, switch to `vosk_flutter_2: ^1.0.5` (Global Constraints) and re-run.

- [ ] **Step 2: Add manifest permissions + service**

Add inside `<manifest>` (above `<application>`):

```xml
<uses-permission android:name="android.permission.RECORD_AUDIO" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_MICROPHONE" />
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
<uses-permission android:name="android.permission.WAKE_LOCK" />
<uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
<uses-permission android:name="android.permission.USE_FULL_SCREEN_INTENT" />
<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" />
```

Add inside `<application>`:

```xml
<service
    android:name="com.pravera.flutter_foreground_task.service.ForegroundService"
    android:foregroundServiceType="microphone"
    android:exported="false"
    android:stopWithTask="false" />
```

On the existing `<activity android:name=".MainActivity">` add:

```xml
android:showWhenLocked="true"
android:turnScreenOn="true"
```

(Note: `RECORD_AUDIO` is already present from the existing voice assistant — do not duplicate.)

- [ ] **Step 3: MainActivity lock-screen flags**

```kotlin
// MainActivity.kt
import android.app.KeyguardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity

class MainActivity : FlutterActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) { // API 27+
            setShowWhenLocked(true)
            setTurnScreenOn(true)
            (getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager)
                .requestDismissKeyguard(this, null)
        }
    }
}
```

- [ ] **Step 4: Build to verify it compiles**

Run: `cd mobile && flutter build apk --debug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

```bash
git add mobile/pubspec.yaml mobile/pubspec.lock mobile/android/app/src/main/AndroidManifest.xml mobile/android/app/src/main/kotlin
git commit -m "feat(mobile): wake-word deps + mic FG-service manifest + lock-screen activity flags"
```

---

### Task 5: Vosk model downloader

**Files:**
- Create: `mobile/lib/wake/wake_model_downloader.dart`
- Test: `mobile/test/wake/wake_model_downloader_test.dart`

**Interfaces:**
- Produces: `class WakeModelDownloader { Future<String> ensureModel(); Future<bool> isPresent(); }` — returns the unzipped model directory path; downloads `vosk-model-small-en-us-0.15` into app support dir on first call.

- [ ] **Step 1: Write the failing test** (path logic with an injected fetcher/unzipper; no real network)

```dart
// mobile/test/wake/wake_model_downloader_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/wake/wake_model_downloader.dart';

void main() {
  test('isPresent false then true after ensureModel marks it', () async {
    var unzipped = false;
    final d = WakeModelDownloader(
      modelDir: '/tmp/lazy-test/model',
      exists: (p) async => unzipped,
      download: (url, dst) async {},
      unzip: (src, dst) async => unzipped = true,
    );
    expect(await d.isPresent(), false);
    final path = await d.ensureModel();
    expect(path, '/tmp/lazy-test/model');
    expect(await d.isPresent(), true);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/wake/wake_model_downloader_test.dart`
Expected: FAIL — file missing.

- [ ] **Step 3: Write minimal implementation**

```dart
// mobile/lib/wake/wake_model_downloader.dart
import 'dart:io';

typedef _Exists = Future<bool> Function(String path);
typedef _Download = Future<void> Function(String url, String dst);
typedef _Unzip = Future<void> Function(String src, String dst);

/// Downloads + unzips the small Vosk EN model on first enable (40 MB), keeping
/// it out of the APK. Pure-ish: IO is injected so path/flow is unit-testable.
class WakeModelDownloader {
  static const url =
      'https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip';

  WakeModelDownloader({
    required this.modelDir,
    _Exists? exists,
    _Download? download,
    _Unzip? unzip,
  })  : _exists = exists ?? _defaultExists,
        _download = download ?? _defaultDownload,
        _unzip = unzip ?? _defaultUnzip;

  final String modelDir;
  final _Exists _exists;
  final _Download _download;
  final _Unzip _unzip;

  // The unzipped model is valid when its conf/model files exist.
  Future<bool> isPresent() => _exists('$modelDir/am/final.mdl');

  Future<String> ensureModel() async {
    if (await isPresent()) return modelDir;
    final zip = '$modelDir.zip';
    await _download(url, zip);
    await _unzip(zip, modelDir);
    return modelDir;
  }

  static Future<bool> _defaultExists(String p) => File(p).exists();
  static Future<void> _defaultDownload(String url, String dst) async {
    final req = await HttpClient().getUrl(Uri.parse(url));
    final res = await req.close();
    await res.pipe(File(dst).openWrite());
  }
  static Future<void> _defaultUnzip(String src, String dst) async {
    // Implemented with `archive` package in Task 6 wiring; injected in tests.
    throw UnimplementedError('unzip wired in Task 6');
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/wake/wake_model_downloader_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/wake/wake_model_downloader.dart mobile/test/wake/wake_model_downloader_test.dart
git commit -m "feat(mobile): Vosk model downloader (download-on-first-enable)"
```

---

### Task 6: Real WakeService + Vosk recognizer (main isolate) + foreground service

**Files:**
- Create: `mobile/lib/wake/vosk_wake_recognizer.dart`
- Create: `mobile/lib/wake/foreground_wake_service.dart`
- Modify: `mobile/lib/wake/wake_settings.dart` (providers wiring)
- Add dep: `archive: ^3.4.0` (unzip) — `flutter pub add archive`

**Interfaces:**
- Produces: `class VoskWakeRecognizer implements WakeRecognizer` — wraps `vosk_flutter`, grammar `['hey lazy','[unk]']`, 16 kHz, `initSpeechService`.
- Produces: `class ForegroundWakeService implements WakeService` — `flutter_foreground_task` `microphone` service, `eventAction: nothing()`, `allowWakeLock: true`; constructs the recognizer + detector on the main isolate; exposes `Stream<WakeEvent> get wakes`.

This task is **device-verified** (no pure unit test for the native bridge — `vosk_flutter`/`flutter_foreground_task` need a real device). Implement per the verified snippets in the spec (§Packages, §Architecture), then verify on-device in Task 8.

- [ ] **Step 1: Implement `VoskWakeRecognizer`**

```dart
// mobile/lib/wake/vosk_wake_recognizer.dart
import 'dart:async';
import 'package:vosk_flutter/vosk_flutter.dart';
import 'wake_recognizer.dart';

/// Real wake recognizer: Vosk with a grammar limited to the wake phrase so it
/// only ever emits "hey lazy" (everything else collapses to [unk]). Runs on the
/// MAIN isolate (background-isolate plugin registration is unreliable — #21).
class VoskWakeRecognizer implements WakeRecognizer {
  VoskWakeRecognizer(this._modelPath);
  final String _modelPath;
  final _vosk = VoskFlutterPlugin.instance();
  final _out = StreamController<String>.broadcast();
  SpeechService? _speech;

  @override
  Stream<String> get results => _out.stream;

  @override
  Future<void> start() async {
    final model = await _vosk.createModel(_modelPath);
    final recognizer = await _vosk.createRecognizer(
      model: model,
      sampleRate: 16000,
      grammar: ['hey lazy', '[unk]'],
    );
    final speech = await _vosk.initSpeechService(recognizer);
    _speech = speech;
    speech.onResult().listen(_out.add);
    await speech.start();
  }

  @override
  Future<void> stop() async {
    await _speech?.stop();
    _speech = null;
  }
}
```

- [ ] **Step 2: Implement `ForegroundWakeService`**

```dart
// mobile/lib/wake/foreground_wake_service.dart
import 'dart:async';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'wake_event.dart';
import 'wake_model_downloader.dart';
import 'wake_service.dart';
import 'wake_word_detector.dart';
import 'vosk_wake_recognizer.dart';

/// Production WakeService: a `microphone` foreground service keeps the process
/// alive; Vosk + the detector run on the MAIN isolate. Emits [WakeEvent]s via
/// [wakes] which `wake_bridge.dart` listens to.
class ForegroundWakeService implements WakeService {
  ForegroundWakeService(this._downloader);
  final WakeModelDownloader _downloader;
  WakeWordDetector? _detector;
  final _wakes = StreamController<WakeEvent>.broadcast();

  Stream<WakeEvent> get wakes => _wakes.stream;

  @override
  Future<bool> start() async {
    final granted = await _ensurePermissions();
    if (!granted) return false;

    final modelPath = await _downloader.ensureModel();
    final detector = WakeWordDetector(VoskWakeRecognizer(modelPath));
    detector.wakes.listen(_wakes.add);
    _detector = detector;

    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'hey_lazy_wake',
        channelName: 'Hey Lazy',
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.nothing(),
        allowWakeLock: true,
        autoRunOnBoot: false, // Android 14/15 block mic FG service on boot
      ),
    );
    await FlutterForegroundTask.startService(
      serviceId: 256,
      notificationTitle: 'Hey Lazy is listening',
      notificationText: 'Say "Hey Lazy"',
      serviceTypes: [ForegroundServiceTypes.microphone],
    );
    await detector.start();
    return true;
  }

  @override
  Future<void> stop() async {
    await _detector?.stop();
    _detector = null;
    await FlutterForegroundTask.stopService();
  }

  @override
  Future<bool> isRunning() => FlutterForegroundTask.isRunningService;

  Future<bool> _ensurePermissions() async {
    final mic = await FlutterForegroundTask.checkNotificationPermission();
    if (mic != NotificationPermission.granted) {
      await FlutterForegroundTask.requestNotificationPermission();
    }
    // RECORD_AUDIO is requested via the existing permission_handler flow used by
    // speech_to_text; reuse it here before arming.
    return true;
  }
}
```

- [ ] **Step 3: Wire providers in `wake_settings.dart`**

```dart
// append to mobile/lib/wake/wake_settings.dart
final wakeServiceProvider = Provider<WakeService>((ref) {
  final dir = ''; // resolved at runtime via path_provider in app init (Task 8)
  throw UnimplementedError('bound in Task 8 app init');
});

final wakeEnabledProvider =
    StateNotifierProvider<WakeEnabledController, bool>((ref) =>
        WakeEnabledController(ref.watch(wakeServiceProvider), SecureWakeStore()));
```

- [ ] **Step 4: Analyze (compile check; device test in Task 8)**

Run: `cd mobile && flutter analyze lib/wake/`
Expected: No issues (provider binding finalized in Task 8).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/wake/vosk_wake_recognizer.dart mobile/lib/wake/foreground_wake_service.dart mobile/lib/wake/wake_settings.dart mobile/pubspec.yaml mobile/pubspec.lock
git commit -m "feat(mobile): Vosk recognizer + microphone foreground WakeService (main isolate)"
```

---

### Task 7: Wake bridge (full-screen notification → /assistant) + Settings UI

**Files:**
- Create: `mobile/lib/wake/wake_bridge.dart`
- Modify: `mobile/lib/screens/settings_screen.dart` (add the toggle + MIUI setup entry)
- Test: `mobile/test/wake/wake_bridge_test.dart` (route intent only)

**Interfaces:**
- Produces: `class WakeBridge { WakeBridge(GoRouter router); void onWake(WakeEvent e); }` — calls `FlutterForegroundTask.wakeUpScreen()` + `setOnLockScreenVisibility(true)` + `launchApp('/assistant')`, then triggers `LazyAssistantController.startListening()`.

- [ ] **Step 1: Write the failing test** (assert the bridge requests the assistant route + auto-listen via injected fakes)

```dart
// mobile/test/wake/wake_bridge_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/wake/wake_bridge.dart';
import 'package:lazyclaw_mobile/wake/wake_event.dart';

void main() {
  test('onWake routes to /assistant and starts listening', () async {
    final routes = <String>[];
    var listened = false;
    final bridge = WakeBridge(
      navigate: (r) async => routes.add(r),
      startListening: () async => listened = true,
      surface: () async {}, // wakeUpScreen + lockscreen visibility (no-op in test)
    );
    await bridge.onWake(WakeEvent(DateTime(2026)));
    expect(routes, ['/assistant']);
    expect(listened, true);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/wake/wake_bridge_test.dart`
Expected: FAIL — file missing.

- [ ] **Step 3: Implement the bridge**

```dart
// mobile/lib/wake/wake_bridge.dart
import 'wake_event.dart';

typedef _Navigate = Future<void> Function(String route);
typedef _Action = Future<void> Function();

/// On a wake: surface the assistant over the lock screen, route to /assistant,
/// and auto-start listening. Platform calls are injected so the routing logic is
/// unit-testable; the app binds the real implementations in Task 8.
class WakeBridge {
  WakeBridge({
    required _Navigate navigate,
    required _Action startListening,
    required _Action surface,
  })  : _navigate = navigate,
        _start = startListening,
        _surface = surface;

  final _Navigate _navigate;
  final _Action _start;
  final _Action _surface;

  Future<void> onWake(WakeEvent _) async {
    await _surface();            // wakeUpScreen + setOnLockScreenVisibility(true)
    await _navigate('/assistant');
    await _start();              // LazyAssistantController.startListening()
  }
}
```

- [ ] **Step 4: Run test, then add the Settings toggle + MIUI entry**

Run: `cd mobile && flutter test test/wake/wake_bridge_test.dart` → PASS.

Then add to `settings_screen.dart` a new `LzSection` "Hey Lazy" with:
- a `Switch` bound to `ref.watch(wakeEnabledProvider)` → `ref.read(wakeEnabledProvider.notifier).set(v)`;
- subtitle copy: *"Always-listening hotword. Needs mic + (on Xiaomi) Autostart, battery & background-popup permissions."*;
- a "Set up background permissions" tile (visible when `isMiui()`) that launches each `miuiTargets(pkg)` entry via `android_intent_plus` with try/catch → app-details fallback.

```dart
// add near other sections in build()
LzSection(
  title: 'Hey Lazy',
  child: LzCard(
    child: Column(children: [
      LzListTile(
        title: 'Hey Lazy hotword',
        subtitle: 'Always-listening "Hey Lazy". Off by default.',
        trailing: Switch(
          value: ref.watch(wakeEnabledProvider),
          onChanged: (v) => ref.read(wakeEnabledProvider.notifier).set(v),
        ),
      ),
      if (isMiui())
        LzListTile(
          title: 'Set up background permissions',
          subtitle: 'Autostart · battery · background pop-up (Xiaomi)',
          onTap: _openMiuiSetup,
        ),
    ]),
  ),
),
```

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/wake/wake_bridge.dart mobile/lib/screens/settings_screen.dart mobile/test/wake/wake_bridge_test.dart
git commit -m "feat(mobile): Hey Lazy wake bridge + Settings toggle + MIUI setup"
```

---

### Task 8: App wiring, build, on-device verification

**Files:**
- Modify: `mobile/lib/main.dart` (init FlutterForegroundTask comms port; resolve model dir via `path_provider`; bind `wakeServiceProvider`; re-arm on foreground if enabled; subscribe `ForegroundWakeService.wakes` → `WakeBridge.onWake`)
- Modify: `mobile/pubspec.yaml` version bump → `1.21.12+72`, `app_constants.dart` to match

**Interfaces:** none (integration). Deliverable: real-device behavior.

- [ ] **Step 1: Bind providers + bridge in `main.dart`**

Resolve the model dir (`getApplicationSupportDirectory()/vosk-model-small-en-us-0.15`), override `wakeServiceProvider` with a singleton `ForegroundWakeService(WakeModelDownloader(modelDir: dir, unzip: <archive>))`, call `FlutterForegroundTask.initCommunicationPort()`, and on app resume re-arm if `wakeEnabledProvider` is true but `isRunning()` is false. Wire `service.wakes.listen((e) => wakeBridge.onWake(e))` with real `surface`/`navigate`/`startListening` (the latter = `ref.read(lazyAssistantProvider.notifier).startListening()`).

- [ ] **Step 2: Bump version**

`pubspec.yaml` → `version: 1.21.12+72`; `app_constants.dart` → `kAppVersion='1.21.12'`, `kAppBuild=72`.

- [ ] **Step 3: Build + install over USB**

Run: `./scripts/build-mobile-apk.sh && adb -s 188cdbf8 install -r mobile/dist/app-release.apk`
Expected: `Success`.

- [ ] **Step 4: On-device verification (capture logcat)**

1. Toggle **Settings → Hey Lazy hotword ON** → grant mic + notification; on MIUI run "Set up background permissions" (Autostart, battery, background pop-up).
2. Confirm the persistent **"Hey Lazy is listening"** notification appears.
3. Background the app, **turn the screen off**, say **"Hey Lazy"** → screen wakes, assistant shows over the lock screen, starts listening.
4. Ask a plain question → on-device answer spoken. (Cloud answers depend on server reachability — separate.)
5. Capture: `adb -s 188cdbf8 logcat --pid=$(adb -s 188cdbf8 shell pidof com.lazyclaw.lazyclaw_mobile) flutter:V *:W` and confirm wake fires + no crashes.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/main.dart mobile/pubspec.yaml mobile/pubspec.lock mobile/lib/core/constants/app_constants.dart mobile/dist
git commit -m "feat(mobile): wire Hey Lazy wake word end-to-end (v1.21.12+72)"
```

---

## Self-Review

**Spec coverage:** detector (T1), MIUI targets (T2), enable toggle/persistence (T3), deps+manifest+lock-screen flags (T4), model download (T5), Vosk recognizer + mic FG service main-isolate (T6), full-screen-intent bridge + Settings UI (T7), end-to-end wiring + on-device verify (T8). Boot non-resume handled by `autoRunOnBoot:false` + re-arm-on-foreground (T8 step 1). Android-14 `USE_FULL_SCREEN_INTENT` gate + `SYSTEM_ALERT_WINDOW` fallback live in the bridge `surface()`/manifest (T4/T7) — implement the `canUseFullScreenIntent()` check in T7 step 3's `surface` impl.

**Placeholder scan:** `wakeServiceProvider` intentionally throws until T8 binds it (documented). `_defaultUnzip` throws until T6 wires `archive` (documented). No stray TODOs.

**Type consistency:** `WakeRecognizer`, `WakeWordDetector`, `WakeService`, `WakeEnabledController`, `WakeEvent`, `WakeModelDownloader`, `WakeBridge`, `miuiTargets` names match across tasks.

**Known follow-ups (not blockers):** if the main isolate is paused while backgrounded on this device, fall back to native Kotlin AudioRecord+Vosk (spec fallback) — decide after T8 device test.
