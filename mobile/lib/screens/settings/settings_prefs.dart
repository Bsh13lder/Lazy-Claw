import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/reminder_lead.dart';

// ── Secure-storage keys ────────────────────────────────────────────────────

const String _kSyncInterval = 'settings.sync_interval_minutes';
const String _kWipeOnLogout = 'settings.wipe_on_logout';
const String _kNotifyChatReply = 'settings.notify_chat_reply';
const String _kNotifyTaskDone = 'settings.notify_task_done';
const String _kNotifyApprovals = 'settings.notify_approvals';
const String _kReminderLeadDefault = 'settings.reminder_lead_default';
const String _kDefaultReminderMinutes = 'settings.default_reminder_minutes';

// Server-notification feed cursor + dedup ring + cached delivery channel.
const String _kNotificationsSince = 'notifications.since';
const String _kNotificationsSeenIds = 'notifications.seen_ids';
const String _kNotificationChannel = 'notifications.channel';

/// The lead applied to a new/edited task that GAINS a due time without an
/// explicit reminder. "30 min before" by default.
const ReminderLead kDefaultReminderLead = ReminderLead.min30;

/// Time-of-day (minutes-from-midnight) at which a DATE-ONLY task ("due
/// tomorrow", no clock time) fires its local reminder. 540 = 09:00.
const int kDefaultReminderMinutes = 540;

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

  // ── Default reminder lead-time ───────────────────────────────────────────

  /// Stored as `none` (no reminder) or the lead's whole-minute count. An absent
  /// / unparseable value falls back to [kDefaultReminderLead].
  static Future<ReminderLead> loadReminderLeadDefault() async {
    final raw = await _storage.read(key: _kReminderLeadDefault);
    return reminderLeadFromStored(raw);
  }

  static Future<void> saveReminderLeadDefault(ReminderLead v) =>
      _storage.write(key: _kReminderLeadDefault, value: reminderLeadToStored(v));

  // ── Default date-only reminder time-of-day ────────────────────────────────

  /// Minutes-from-midnight at which DATE-ONLY tasks remind. Absent / invalid →
  /// [kDefaultReminderMinutes] (09:00).
  static Future<int> loadDefaultReminderMinutes() async {
    final raw = await _storage.read(key: _kDefaultReminderMinutes);
    return defaultReminderMinutesFromStored(raw);
  }

  static Future<void> saveDefaultReminderMinutes(int minutes) =>
      _storage.write(
        key: _kDefaultReminderMinutes,
        value: clampReminderMinutes(minutes).toString(),
      );

  // ── Server-notification feed cursor ──────────────────────────────────────

  /// The last server `now` we caught up to (ISO timestamp). Null = never
  /// pulled, so the first pass requests the full window.
  static Future<String?> loadNotificationsSince() =>
      _storage.read(key: _kNotificationsSince);

  static Future<void> saveNotificationsSince(String v) =>
      _storage.write(key: _kNotificationsSince, value: v);

  /// Bounded ring of recently-shown notification ids (newline-joined) used to
  /// dedup across an inclusive `since` boundary. Empty when none stored.
  static Future<List<String>> loadNotificationsSeenIds() async {
    final raw = await _storage.read(key: _kNotificationsSeenIds);
    if (raw == null || raw.isEmpty) return const [];
    return raw.split('\n').where((e) => e.isNotEmpty).toList();
  }

  static Future<void> saveNotificationsSeenIds(List<String> ids) =>
      _storage.write(key: _kNotificationsSeenIds, value: ids.join('\n'));

  // ── Cached delivery channel (for instant Settings render) ────────────────

  /// Locally-cached delivery-channel wire value. Optional optimisation so the
  /// segmented control can render before the server round-trip completes; the
  /// server remains the source of truth.
  static Future<String?> loadNotificationChannelCache() =>
      _storage.read(key: _kNotificationChannel);

  static Future<void> saveNotificationChannelCache(String wire) =>
      _storage.write(key: _kNotificationChannel, value: wire);
}

/// Serialise a default-lead pref: `none` or whole minutes (`0` = at time).
String reminderLeadToStored(ReminderLead v) =>
    v.isNone ? 'none' : v.lead!.inMinutes.toString();

/// Parse a stored default-lead pref, falling back to [kDefaultReminderLead].
ReminderLead reminderLeadFromStored(String? raw) {
  if (raw == null) return kDefaultReminderLead;
  if (raw == 'none') return ReminderLead.none;
  final mins = int.tryParse(raw);
  if (mins == null || mins < 0) return kDefaultReminderLead;
  return ReminderLead(Duration(minutes: mins));
}

/// Clamp a minutes-from-midnight value into a valid day (0..1439).
int clampReminderMinutes(int minutes) =>
    minutes < 0 ? 0 : (minutes > 1439 ? 1439 : minutes);

/// Parse a stored default-reminder-time pref, falling back to
/// [kDefaultReminderMinutes] (09:00) and clamping into a valid day.
int defaultReminderMinutesFromStored(String? raw) {
  final mins = int.tryParse(raw ?? '');
  if (mins == null) return kDefaultReminderMinutes;
  return clampReminderMinutes(mins);
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

  /// Default reminder lead applied when a task gains a due time without an
  /// explicit reminder. Defaults to [kDefaultReminderLead].
  final ReminderLead reminderLeadDefault;

  /// Minutes-from-midnight at which DATE-ONLY tasks (no clock time) remind.
  /// Defaults to [kDefaultReminderMinutes] (09:00).
  final int defaultReminderMinutes;

  const SettingsPrefsData({
    required this.syncInterval,
    required this.wipeOnLogout,
    required this.notifyChatReply,
    required this.notifyTaskDone,
    required this.notifyApprovals,
    this.reminderLeadDefault = kDefaultReminderLead,
    this.defaultReminderMinutes = kDefaultReminderMinutes,
  });

  SettingsPrefsData copyWith({
    SyncInterval? syncInterval,
    bool? wipeOnLogout,
    bool? notifyChatReply,
    bool? notifyTaskDone,
    bool? notifyApprovals,
    ReminderLead? reminderLeadDefault,
    int? defaultReminderMinutes,
  }) =>
      SettingsPrefsData(
        syncInterval: syncInterval ?? this.syncInterval,
        wipeOnLogout: wipeOnLogout ?? this.wipeOnLogout,
        notifyChatReply: notifyChatReply ?? this.notifyChatReply,
        notifyTaskDone: notifyTaskDone ?? this.notifyTaskDone,
        notifyApprovals: notifyApprovals ?? this.notifyApprovals,
        reminderLeadDefault: reminderLeadDefault ?? this.reminderLeadDefault,
        defaultReminderMinutes:
            defaultReminderMinutes ?? this.defaultReminderMinutes,
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
      SettingsPrefs.loadReminderLeadDefault(),
      SettingsPrefs.loadDefaultReminderMinutes(),
    ]);
    return SettingsPrefsData(
      syncInterval: results[0] as SyncInterval,
      wipeOnLogout: results[1] as bool,
      notifyChatReply: results[2] as bool,
      notifyTaskDone: results[3] as bool,
      notifyApprovals: results[4] as bool,
      reminderLeadDefault: results[5] as ReminderLead,
      defaultReminderMinutes: results[6] as int,
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

  Future<void> setReminderLeadDefault(ReminderLead v) async {
    await SettingsPrefs.saveReminderLeadDefault(v);
    state = AsyncData(
      (state.valueOrNull ?? await _load()).copyWith(reminderLeadDefault: v),
    );
  }

  Future<void> setDefaultReminderMinutes(int minutes) async {
    final clamped = clampReminderMinutes(minutes);
    await SettingsPrefs.saveDefaultReminderMinutes(clamped);
    state = AsyncData(
      (state.valueOrNull ?? await _load())
          .copyWith(defaultReminderMinutes: clamped),
    );
  }
}
