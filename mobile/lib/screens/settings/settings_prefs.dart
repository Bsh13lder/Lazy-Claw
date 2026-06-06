import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

// ── Secure-storage keys ────────────────────────────────────────────────────

const String _kSyncInterval = 'settings.sync_interval_minutes';
const String _kWipeOnLogout = 'settings.wipe_on_logout';
const String _kNotifyChatReply = 'settings.notify_chat_reply';
const String _kNotifyTaskDone = 'settings.notify_task_done';
const String _kNotifyApprovals = 'settings.notify_approvals';

// ── Sync interval ───────────────────────────────────────────────────────────

/// Allowed background-sync intervals in minutes. 0 = off.
enum SyncInterval {
  off(0, 'Off'),
  min15(15, '15 min'),
  min30(30, '30 min'),
  hour1(60, '1 hour');

  const SyncInterval(this.minutes, this.label);
  final int minutes;
  final String label;

  static SyncInterval fromMinutes(int v) {
    return SyncInterval.values.firstWhere(
      (e) => e.minutes == v,
      orElse: () => SyncInterval.min30,
    );
  }
}

// ── Prefs store ─────────────────────────────────────────────────────────────

/// Thin wrapper around [FlutterSecureStorage] for Settings-specific prefs.
///
/// All methods are static and side-effect free on the caller — they read/write
/// isolated keys so they cannot interfere with [ServerConfig] or the DB key.
class SettingsPrefs {
  SettingsPrefs._();

  static const FlutterSecureStorage _storage = FlutterSecureStorage();

  // ── Sync interval ──────────────────────────────────────────────────────────

  static Future<SyncInterval> loadSyncInterval() async {
    final raw = await _storage.read(key: _kSyncInterval);
    final minutes = int.tryParse(raw ?? '') ?? 30;
    return SyncInterval.fromMinutes(minutes);
  }

  static Future<void> saveSyncInterval(SyncInterval v) =>
      _storage.write(key: _kSyncInterval, value: v.minutes.toString());

  // ── Wipe on logout ─────────────────────────────────────────────────────────

  static Future<bool> loadWipeOnLogout() async {
    final raw = await _storage.read(key: _kWipeOnLogout);
    return raw == 'true';
  }

  static Future<void> saveWipeOnLogout({required bool v}) =>
      _storage.write(key: _kWipeOnLogout, value: v.toString());

  // ── Notification toggles ───────────────────────────────────────────────────

  static Future<bool> _loadNotify(String key) async {
    final raw = await _storage.read(key: key);
    // Default ON for all notification kinds.
    return raw != 'false';
  }

  static Future<void> _saveNotify(String key, {required bool v}) =>
      _storage.write(key: key, value: v.toString());

  static Future<bool> loadNotifyChatReply() => _loadNotify(_kNotifyChatReply);
  static Future<bool> loadNotifyTaskDone() => _loadNotify(_kNotifyTaskDone);
  static Future<bool> loadNotifyApprovals() => _loadNotify(_kNotifyApprovals);

  static Future<void> saveNotifyChatReply({required bool v}) =>
      _saveNotify(_kNotifyChatReply, v: v);
  static Future<void> saveNotifyTaskDone({required bool v}) =>
      _saveNotify(_kNotifyTaskDone, v: v);
  static Future<void> saveNotifyApprovals({required bool v}) =>
      _saveNotify(_kNotifyApprovals, v: v);
}

// ── Riverpod providers ───────────────────────────────────────────────────────

/// Async provider for all Settings prefs loaded from secure storage.
final settingsPrefsProvider =
    AsyncNotifierProvider<SettingsPrefsNotifier, SettingsPrefsData>(
  SettingsPrefsNotifier.new,
);

/// Immutable snapshot of all local settings prefs.
class SettingsPrefsData {
  final SyncInterval syncInterval;
  final bool wipeOnLogout;
  final bool notifyChatReply;
  final bool notifyTaskDone;
  final bool notifyApprovals;

  const SettingsPrefsData({
    required this.syncInterval,
    required this.wipeOnLogout,
    required this.notifyChatReply,
    required this.notifyTaskDone,
    required this.notifyApprovals,
  });

  SettingsPrefsData copyWith({
    SyncInterval? syncInterval,
    bool? wipeOnLogout,
    bool? notifyChatReply,
    bool? notifyTaskDone,
    bool? notifyApprovals,
  }) =>
      SettingsPrefsData(
        syncInterval: syncInterval ?? this.syncInterval,
        wipeOnLogout: wipeOnLogout ?? this.wipeOnLogout,
        notifyChatReply: notifyChatReply ?? this.notifyChatReply,
        notifyTaskDone: notifyTaskDone ?? this.notifyTaskDone,
        notifyApprovals: notifyApprovals ?? this.notifyApprovals,
      );
}

class SettingsPrefsNotifier extends AsyncNotifier<SettingsPrefsData> {
  @override
  Future<SettingsPrefsData> build() => _load();

  Future<SettingsPrefsData> _load() async {
    final results = await Future.wait([
      SettingsPrefs.loadSyncInterval(),
      SettingsPrefs.loadWipeOnLogout(),
      SettingsPrefs.loadNotifyChatReply(),
      SettingsPrefs.loadNotifyTaskDone(),
      SettingsPrefs.loadNotifyApprovals(),
    ]);
    return SettingsPrefsData(
      syncInterval: results[0] as SyncInterval,
      wipeOnLogout: results[1] as bool,
      notifyChatReply: results[2] as bool,
      notifyTaskDone: results[3] as bool,
      notifyApprovals: results[4] as bool,
    );
  }

  Future<void> setSyncInterval(SyncInterval v) async {
    await SettingsPrefs.saveSyncInterval(v);
    state = AsyncData(
      (state.valueOrNull ?? await _load()).copyWith(syncInterval: v),
    );
  }

  Future<void> setWipeOnLogout({required bool v}) async {
    await SettingsPrefs.saveWipeOnLogout(v: v);
    state = AsyncData(
      (state.valueOrNull ?? await _load()).copyWith(wipeOnLogout: v),
    );
  }

  Future<void> setNotifyChatReply({required bool v}) async {
    await SettingsPrefs.saveNotifyChatReply(v: v);
    state = AsyncData(
      (state.valueOrNull ?? await _load()).copyWith(notifyChatReply: v),
    );
  }

  Future<void> setNotifyTaskDone({required bool v}) async {
    await SettingsPrefs.saveNotifyTaskDone(v: v);
    state = AsyncData(
      (state.valueOrNull ?? await _load()).copyWith(notifyTaskDone: v),
    );
  }

  Future<void> setNotifyApprovals({required bool v}) async {
    await SettingsPrefs.saveNotifyApprovals(v: v);
    state = AsyncData(
      (state.valueOrNull ?? await _load()).copyWith(notifyApprovals: v),
    );
  }
}
