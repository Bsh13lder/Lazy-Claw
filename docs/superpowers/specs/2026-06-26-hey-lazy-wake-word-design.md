# Hey Lazy Wake Word — Design Spec

**Date:** 2026-06-26
**Status:** Approved (design), pending implementation plan
**Area:** `mobile/` (Flutter Android client)

## Goal

Add a true always-on **"Hey Lazy" hotword** to the LazyClaw Flutter app — hands-free
activation like "Hey Google". The phrase is detected **fully on-device** (offline,
audio never leaves the phone), **even when the screen is off / the phone is locked**.
On detection it launches the existing voice-assistant pipeline. The manual **✨ button**
stays exactly as-is — both paths coexist (user choice: "always on and background
together, also with button, how Google does it").

## Non-goals (deferred — YAGNI)

- Sensitivity slider / tunable threshold UI (ship a sane fixed threshold first)
- "Answer without lighting the screen" (audio-only while locked) — phase 2
- Multi-language wake word / user-custom phrase
- iOS support (Android-first; iOS background-mic is far more restricted — separate effort)

## Decisions locked during brainstorming

- **Scope:** always-on incl. screen-off, **plus** the existing button. Opt-in (off by default).
- **Engine:** **Vosk** (open-source, Apache-2.0, offline) — fits LazyClaw's MIT ethos; no
  third-party account. Trade-off accepted: slightly more CPU/false-triggers than Porcupine,
  mitigated by a grammar-limited recognizer + a voice-activity gate.

## Architecture

Small, single-purpose units under `mobile/lib/wake/`, each testable in isolation:

1. **`wake_word_detector.dart` — `WakeWordDetector`**
   Wraps Vosk. Continuous, **grammar-limited** recognizer (`["hey lazy", "[unk]"]`) so the
   model only matches the wake phrase — everything else collapses to `[unk]`, cutting CPU and
   false-positives. Emits a `Stream<WakeEvent>`. Depends on an abstract `VoskRecognizer` seam
   so unit tests feed canned recognition results without real audio.

2. **`wake_service.dart` — `WakeService`**
   Controls the Android **foreground microphone service** (via `flutter_foreground_task`).
   Starts/stops the service, owns the persistent **"Lazy is listening"** notification (Android
   requires it), and hosts the main-isolate Vosk detector for the service's lifetime.
   **Boot caveat:** Android 14/15 forbid starting a `microphone` foreground service from
   `BOOT_COMPLETED` (`ForegroundServiceStartNotAllowedException`) — so there is **no** silent
   auto-resume after reboot. Instead, on first foreground after a reboot, if the hotword is
   enabled-but-not-running, we re-arm it (and/or post a "tap to resume Hey Lazy" notification).

3. **`wake_settings.dart` — providers**
   Persisted opt-in toggle (`flutter_secure_storage`, mirrors `assistant_backend_mode`
   pattern). Exposes enabled state; toggling on/off starts/stops `WakeService`. Off by default.

4. **`miui_permissions.dart` — MIUI setup helper**
   Detects MIUI/Xiaomi and deep-links the user to the three settings MIUI needs (below),
   tracking which are granted. Pure intent construction is unit-testable.

5. **Wake → Assistant bridge**
   On a `WakeEvent`: play a chime → post a **full-screen-intent notification** (the BAL-compliant
   way to surface UI from a service; direct `startActivity` from a background service is blocked on
   Android 12–14). When locked/screen-off the system launches the assistant Activity over the lock
   screen; `MainActivity` sets `setShowWhenLocked(true)` + `setTurnScreenOn(true)` (API 27+) to turn
   the screen on and render over the keyguard. Then navigate to `/assistant` and auto-start
   listening — the existing `LazyAssistantController` pipeline is reused unchanged. On **Android 14+**
   `USE_FULL_SCREEN_INTENT` is restricted (assistants are not auto-granted): check
   `canUseFullScreenIntent()` and route the user to `ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT`;
   fallback if denied is `SYSTEM_ALERT_WINDOW` + direct `startActivity`.

### Data flow

```
mic audio ──▶ VAD gate (skip silence) ──▶ Vosk recognizer (grammar: "hey lazy" | [unk])
   ──▶ "hey lazy" match ≥ threshold ──▶ debounce ──▶ WakeService emits WakeEvent
   ──▶ chime + wake screen + show assistant over lock screen
   ──▶ existing pipeline: speech_to_text ▶ AssistantRouter ▶ on-device LLM / cloud ▶ flutter_tts
```

### Native (Android)

- **Manifest permissions:** `RECORD_AUDIO`, `FOREGROUND_SERVICE`,
  `FOREGROUND_SERVICE_MICROPHONE` (API 34+), `POST_NOTIFICATIONS` (API 33+), `WAKE_LOCK`,
  `RECEIVE_BOOT_COMPLETED`, `USE_FULL_SCREEN_INTENT`.
- **Foreground service:** `android:foregroundServiceType="microphone"`; persistent
  low-priority notification.
- **MainActivity:** `setShowWhenLocked(true)` + `setTurnScreenOn(true)` in `onCreate` (API 27+),
  optional `KeyguardManager.requestDismissKeyguard` — so the assistant appears over the lock screen
  and the screen turns on (no screen wake-lock needed).
- **Lock-screen surfacing:** high-importance, `CATEGORY_CALL`, `setFullScreenIntent(pending, true)`
  notification with a `FLAG_IMMUTABLE` PendingIntent — NOT `startActivity` from the service.
- **No boot receiver for the mic service** (Android 14/15 block it). Re-arm on next foreground.

## Packages (confirmed via research 2026-06-26)

- **`vosk_flutter` 0.3.48** (Apache-2.0, official Alpha Cephei binding). Grammar is first-class:
  `createRecognizer(model:, sampleRate: 16000, grammar: ['hey lazy', '[unk]'])`; mic capture via
  `initSpeechService(recognizer)` → `SpeechService` (native `AudioRecord` on its own thread, **no
  Activity required**). Stale (last release 2023, pins Dart `<3.0.0`) → if version-solve fights the
  toolchain, fall back to **`vosk_flutter_2` 1.0.5** (same code, Dart `>=3.1.2`, Android-only).
- **`flutter_foreground_task` 9.2.2** (MIT, active) — the service *shell*: `microphone`
  service type, `eventAction: nothing()` (stay alive, no ticks), `allowWakeLock: true`
  (PARTIAL_WAKE_LOCK for screen-off CPU), `wakeUpScreen()`, `setOnLockScreenVisibility(true)`,
  `launchApp('/assistant')`, `requestIgnoreBatteryOptimization()` /
  `openIgnoreBatteryOptimizationSettings()`.
- Existing: `speech_to_text`, `flutter_tts`, `go_router`, `flutter_riverpod`,
  `flutter_secure_storage`. Chime via `SystemSound` or a tiny bundled asset.

### Isolate decision (load-bearing)

Run **Vosk on the MAIN isolate** under a `flutter_foreground_task` `microphone` service that keeps
the process + engine alive. Do **NOT** run Vosk in the foreground-task background isolate —
background-isolate Dart plugin registration is unreliable (`vosk_flutter` #21,
flutter/flutter #98591). `SpeechService`'s capture is native and Activity-independent, so it keeps
listening with the screen off as long as the service holds the process. **Documented fallback** if
the main isolate is paused while backgrounded: native Kotlin `AudioRecord` + Vosk JNI inside the
service, bridging wake events to Dart via the plugin's comms port.

## Model delivery

Vosk small EN model (~40 MB). The APK is already ~177 MB — do **not** bundle the model.
**Download-on-first-enable** into the app files dir (same pattern as the on-device LLM model),
verify a checksum, persist the path. Fully offline after first download.

## Error handling

- Mic permission denied → toggle refuses to enable; surface the system prompt.
- **MIUI kills the service** → on next app open, if enabled-but-not-running, re-prompt the MIUI
  setup helper. (Foreground service + boot receiver minimize this.)
- Vosk model missing/corrupt → re-download with progress + checksum verify.
- Wake while server unreachable → on-device queries still answer; a cloud query speaks an honest
  "can't reach the server right now" (ties into the ongoing connectivity work).
- False triggers → grammar + confidence threshold + debounce window.
- Battery → VAD gate + small model + single recognizer thread; document expected drain.

## Testing

- **`WakeWordDetector`** (unit): feed canned recognition frames through the fake `VoskRecognizer`
  seam — assert a `WakeEvent` fires on "hey lazy", not on other phrases; verify threshold + debounce.
- **`WakeSettings`** (unit): toggle persistence + that enabling/disabling calls a fake
  `WakeService` start/stop.
- **`MiuiPermissions`** (unit): MIUI detection + correct intent construction (no real launch).
- **On-device (manual, over USB + logcat):** service survives backgrounding & screen-off; wake
  fires; screen wakes and assistant shows over lock screen; boot-restart; MIUI-kill recovery.

## Rollout

- Opt-in Settings toggle **"Hey Lazy hotword (always listening)"**, **off by default**.
- Version bump; build + `adb install -r` over USB; verify on the physical device (logcat) that
  the service stays alive, the wake fires, and the screen wakes.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| MIUI aggressively kills background services | Foreground service + MIUI permission helper (Autostart `AutoStartManagementActivity`, no-battery-restriction `HiddenAppsConfigActivity`, background-popup `PermissionsEditorActivity`), each wrapped try/catch → app-details fallback |
| Vosk in a background Dart isolate is unreliable (#21) | Run Vosk on the **main isolate**; FG service only keeps the process alive. Native Kotlin Vosk-in-service is the fallback if the main isolate pauses while backgrounded |
| No silent listening after reboot (Android 14/15 block mic FG service from BOOT_COMPLETED) | Re-arm on next foreground / "tap to resume" notification — documented, not a bug |
| Continuous ASR battery drain | Grammar-limited recognizer (`['hey lazy','[unk]']`) + small model + 16 kHz mono; document measured drain |
| Background activity-start blocked (Android 10+) + `USE_FULL_SCREEN_INTENT` restricted (Android 14) | Full-screen-intent notification; `canUseFullScreenIntent()` check + `ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT` deep-link; `SYSTEM_ALERT_WINDOW` + direct `startActivity` fallback; MIUI "display pop-up while in background" |
| Model size vs APK bloat | Download `vosk-model-small-en-us-0.15` (40 MB) on first enable, not bundled |
