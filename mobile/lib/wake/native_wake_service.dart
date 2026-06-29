/// Android implementation of [WakeService]: drives the native Kotlin Vosk
/// foreground service ([WakeWordService]) over the `lazyclaw/wake` MethodChannel.
/// Detection + the lock-screen wake happen natively; this is just start/stop +
/// the MIUI permission deep-links.
library;

import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:speech_to_text/speech_to_text.dart';

import 'miui_permissions.dart';
import 'wake_service.dart';
import 'wake_settings.dart';

class NativeWakeService implements WakeService {
  static const _ch = MethodChannel('lazyclaw/wake');

  @override
  Future<bool> start() async {
    // Vosk's AudioRecord needs RECORD_AUDIO. speech_to_text requests it on
    // initialize(); reuse that instead of adding a permission dependency. Best
    // effort — the assistant has usually already granted it.
    try {
      await SpeechToText().initialize(onError: (_) {}, onStatus: (_) {});
    } catch (_) {/* fall through — native side surfaces a mic error if denied */}
    try {
      return (await _ch.invokeMethod<bool>('start')) ?? false;
    } on PlatformException {
      return false;
    }
  }

  @override
  Future<void> stop() async {
    try {
      await _ch.invokeMethod('stop');
    } on PlatformException {/* already stopped */}
  }

  @override
  Future<bool> isRunning() async {
    try {
      return (await _ch.invokeMethod<bool>('isRunning')) ?? false;
    } on PlatformException {
      return false;
    }
  }

  /// Opens an OEM settings page (MIUI autostart / battery / background pop-up).
  /// The native side falls back to the app-details page if the component is
  /// absent on this MIUI/HyperOS build.
  Future<void> launchMiui(MiuiSettingTarget t) async {
    try {
      await _ch.invokeMethod('launchIntent', {
        'action': t.action,
        'package': t.package,
        'component': t.component,
        'extras': t.extras,
      });
    } on PlatformException {/* non-fatal */}
  }

  /// `Build.MANUFACTURER` (lowercased), e.g. "xiaomi". Drives HyperOS detection.
  static Future<String> deviceManufacturer() async {
    try {
      return ((await _ch.invokeMethod<String>('deviceManufacturer')) ?? '')
          .toLowerCase();
    } on PlatformException {
      return '';
    }
  }

  /// RECORD_AUDIO granted? The wake word is silently dead without it.
  static Future<bool> micGranted() async {
    try {
      return (await _ch.invokeMethod<bool>('micGranted')) ?? false;
    } on PlatformException {
      return false;
    }
  }

  /// True unless Android 14+ has revoked USE_FULL_SCREEN_INTENT (then a wake
  /// can't surface over the lock screen until the user grants it).
  static Future<bool> canFullScreenIntent() async {
    try {
      return (await _ch.invokeMethod<bool>('canFullScreenIntent')) ?? true;
    } on PlatformException {
      return true;
    }
  }

  /// Deep-links the user to grant the full-screen-intent permission (API 34+).
  static Future<void> requestFullScreenIntent() async {
    try {
      await _ch.invokeMethod('requestFullScreenIntent');
    } on PlatformException {/* non-fatal */}
  }

  /// Whether the 40 MB Vosk model has finished downloading + unzipping.
  static Future<bool> modelReady() async {
    try {
      return (await _ch.invokeMethod<bool>('modelReady')) ?? false;
    } on PlatformException {
      return false;
    }
  }
}

final wakeServiceProvider = Provider<WakeService>((_) => NativeWakeService());

/// The opt-in "Hey Lazy hotword" toggle, re-arming the service on app start when
/// it was left enabled.
final wakeEnabledProvider =
    StateNotifierProvider<WakeEnabledController, bool>((ref) {
  final c = WakeEnabledController(ref.watch(wakeServiceProvider),
      const SecureWakeStore());
  c.restoreAndRearm();
  return c;
});
