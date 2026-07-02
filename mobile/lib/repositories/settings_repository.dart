import '../core/api/api_client.dart';
import '../core/reminder_offset.dart';

// ── Models ─────────────────────────────────────────────────────────────────

/// Snapshot of the ECO mode returned by `GET /api/settings/eco`.
class EcoSettings {
  final String mode;
  final List<String> modes;
  final String brain;
  final String worker;
  final String fallback;

  const EcoSettings({
    required this.mode,
    required this.modes,
    required this.brain,
    required this.worker,
    required this.fallback,
  });

  factory EcoSettings.fromJson(Map<String, dynamic> json) => EcoSettings(
        mode: json['mode'] as String,
        modes: List<String>.from(json['modes'] as List),
        brain: json['brain'] as String? ?? '',
        worker: json['worker'] as String? ?? '',
        fallback: json['fallback'] as String? ?? '',
      );

  EcoSettings copyWith({String? mode}) => EcoSettings(
        mode: mode ?? this.mode,
        modes: modes,
        brain: brain,
        worker: worker,
        fallback: fallback,
      );
}

// ── Operating mode (general.agent_mode) ──────────────────────────────────────

/// The four operating-mode options for `general.agent_mode` (ADR-0005 §D).
///
/// Maps the stored value → the label shown to the user. Insertion order is the
/// display order (Ask → Plan → Action → Execute). Stored VALUES are unchanged:
/// 'chat'→Ask, 'plan'→Plan, 'ask'→Action, 'auto'→Execute.
const Map<String, String> kAgentModeLabels = {
  'chat': 'Ask',
  'plan': 'Plan',
  'ask': 'Action',
  'auto': 'Execute',
};

/// Default operating mode when the server has none stored yet.
const String kDefaultAgentMode = 'ask';

/// The operating-mode slice of a user's general settings.
///
/// We model only [agentMode] (not the whole general-settings dict) since that's
/// the only field this client reads/writes. Unknown / missing values coerce to
/// [kDefaultAgentMode] so a not-yet-migrated backend can't break the picker.
class GeneralSettings {
  final String agentMode;

  /// "Hey Lazy" privacy flags. Default OFF / on-device-first (CLAUDE.md):
  /// process-on-device default false, confirm-cloud default true.
  final bool assistantProcessDataOnDevice;
  final bool assistantConfirmCloudRequests;
  final bool assistantAlwaysListening;

  /// The server's default reminder OFFSET set (normalised wire strings), or
  /// `null` when the backend didn't include `reminder_offsets` (not migrated /
  /// never set). `null` is DISTINCT from an empty list (server stores none):
  /// callers must not clobber a local selection when this is `null`.
  final List<String>? reminderOffsets;

  const GeneralSettings({
    required this.agentMode,
    this.assistantProcessDataOnDevice = false,
    this.assistantConfirmCloudRequests = true,
    this.assistantAlwaysListening = false,
    this.reminderOffsets,
  });

  factory GeneralSettings.fromJson(Map<String, dynamic> json) => GeneralSettings(
        agentMode: coerceAgentMode(json['agent_mode']),
        assistantProcessDataOnDevice: json['process_data_on_device'] == true,
        assistantConfirmCloudRequests: json['confirm_cloud_requests'] != false,
        assistantAlwaysListening: json['assistant_always_listening'] == true,
        reminderOffsets: parseServerReminderOffsets(json['reminder_offsets']),
      );

  GeneralSettings copyWith({
    String? agentMode,
    bool? assistantProcessDataOnDevice,
    bool? assistantConfirmCloudRequests,
    bool? assistantAlwaysListening,
    List<String>? reminderOffsets,
  }) =>
      GeneralSettings(
        agentMode: agentMode ?? this.agentMode,
        assistantProcessDataOnDevice:
            assistantProcessDataOnDevice ?? this.assistantProcessDataOnDevice,
        assistantConfirmCloudRequests:
            assistantConfirmCloudRequests ?? this.assistantConfirmCloudRequests,
        assistantAlwaysListening:
            assistantAlwaysListening ?? this.assistantAlwaysListening,
        reminderOffsets: reminderOffsets ?? this.reminderOffsets,
      );

  /// Normalizes any input to a known mode value, falling back to the default.
  static String coerceAgentMode(dynamic v) {
    final s = v?.toString().toLowerCase().trim();
    return (s != null && kAgentModeLabels.containsKey(s))
        ? s
        : kDefaultAgentMode;
  }

  /// Parse the server's `reminder_offsets` value → a normalised list, or `null`
  /// when the key is absent / not a list. An empty list stays an empty list.
  static List<String>? parseServerReminderOffsets(dynamic v) {
    if (v is List) {
      return normalizeReminderOffsets(v.map((e) => e.toString()));
    }
    return null;
  }
}

/// Snapshot of the permissions settings returned by
/// `GET /api/permissions/settings`.
class PermissionsSettings {
  /// Maps category name → level ('allow' | 'ask' | 'deny').
  final Map<String, String> categoryDefaults;

  /// Per-skill overrides (skill_name → level).
  final Map<String, String> skillOverrides;

  final int autoApproveTimeout;
  final bool requireApprovalForHeartbeat;

  const PermissionsSettings({
    required this.categoryDefaults,
    required this.skillOverrides,
    required this.autoApproveTimeout,
    required this.requireApprovalForHeartbeat,
  });

  factory PermissionsSettings.fromJson(Map<String, dynamic> json) {
    final catRaw = json['category_defaults'] as Map? ?? {};
    final overRaw = json['skill_overrides'] as Map? ?? {};
    return PermissionsSettings(
      categoryDefaults: Map<String, String>.fromEntries(
        catRaw.entries.map((e) => MapEntry(e.key as String, e.value as String)),
      ),
      skillOverrides: Map<String, String>.fromEntries(
        overRaw.entries
            .map((e) => MapEntry(e.key as String, e.value as String)),
      ),
      autoApproveTimeout: json['auto_approve_timeout'] as int? ?? 300,
      requireApprovalForHeartbeat:
          json['require_approval_for_heartbeat'] as bool? ?? true,
    );
  }

  PermissionsSettings withCategory(String category, String level) =>
      PermissionsSettings(
        categoryDefaults: {
          ...categoryDefaults,
          category: level,
        },
        skillOverrides: skillOverrides,
        autoApproveTimeout: autoApproveTimeout,
        requireApprovalForHeartbeat: requireApprovalForHeartbeat,
      );
}

// ── Transport seam (testable) ──────────────────────────────────────────────

/// Minimal I/O seam that makes [SettingsRepository] unit-testable without a
/// live Dio instance. Mirrors the [AuthTransport] / [BudgetsTransport] pattern.
abstract class SettingsTransport {
  Future<Map<String, dynamic>> getJson(String path);
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body);
}

class DioSettingsTransport implements SettingsTransport {
  final ApiClient _client;
  DioSettingsTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(String path) =>
      _client.get<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) =>
      _client.post<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> patchJson(
          String path, Map<String, dynamic> body) =>
      _client.patch<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

// ── Repository ─────────────────────────────────────────────────────────────

/// Repository for ECO mode and permissions settings.
///
/// All methods unwrap the `{success, data}` envelope and throw a
/// [SettingsException] when `success` is false.
class SettingsRepository {
  final SettingsTransport _t;
  SettingsRepository(this._t);

  // ── ECO ──────────────────────────────────────────────────────────────────

  /// Returns the current ECO mode and available modes from the server.
  Future<EcoSettings> getEco() async {
    final json = await _t.getJson('/api/settings/eco');
    _assertSuccess(json);
    return EcoSettings.fromJson(json['data'] as Map<String, dynamic>);
  }

  /// Sets the ECO mode and returns the updated settings.
  ///
  /// Throws [SettingsException] if [mode] is not in the server's list.
  Future<EcoSettings> setEco(String mode) async {
    final json = await _t.postJson('/api/settings/eco', {'mode': mode});
    _assertSuccess(json);
    return EcoSettings.fromJson(json['data'] as Map<String, dynamic>);
  }

  // ── Operating mode (general.agent_mode) ───────────────────────────────────

  /// Returns the current operating mode from `GET /api/settings/general`.
  ///
  /// Uses the same `{success, data}` envelope as the ECO endpoints. A backend
  /// that doesn't yet emit `agent_mode` simply yields [kDefaultAgentMode].
  Future<GeneralSettings> getGeneral() async {
    final json = await _t.getJson('/api/settings/general');
    _assertSuccess(json);
    return GeneralSettings.fromJson(
        Map<String, dynamic>.from(json['data'] as Map));
  }

  /// Sets the operating mode via `PATCH /api/settings/general` with
  /// `{ agent_mode: mode }` and returns the updated settings.
  ///
  /// Honors the server's echoed `agent_mode`; if the backend hasn't started
  /// returning it yet, falls back to the requested [mode] so the UI reflects
  /// the user's choice instead of snapping back to the default.
  Future<GeneralSettings> setAgentMode(String mode) async {
    final json =
        await _t.patchJson('/api/settings/general', {'agent_mode': mode});
    _assertSuccess(json);
    final data = Map<String, dynamic>.from(json['data'] as Map);
    data.putIfAbsent('agent_mode', () => mode);
    return GeneralSettings.fromJson(data);
  }

  /// PATCHes only the provided "Hey Lazy" privacy flags onto
  /// `/api/settings/general` and returns the updated settings. Omitted
  /// arguments are not sent (the server merges into its general dict).
  Future<GeneralSettings> setAssistantFlags({
    bool? processDataOnDevice,
    bool? confirmCloudRequests,
    bool? alwaysListening,
  }) async {
    final body = <String, dynamic>{};
    if (processDataOnDevice != null) {
      body['process_data_on_device'] = processDataOnDevice;
    }
    if (confirmCloudRequests != null) {
      body['confirm_cloud_requests'] = confirmCloudRequests;
    }
    if (alwaysListening != null) {
      body['assistant_always_listening'] = alwaysListening;
    }
    final json = await _t.patchJson('/api/settings/general', body);
    _assertSuccess(json);
    return GeneralSettings.fromJson(
        Map<String, dynamic>.from(json['data'] as Map));
  }

  /// PATCHes the default reminder OFFSET set via `PATCH /api/settings/general`
  /// with `{ reminder_offsets: [...] }` and returns the updated settings. The
  /// list is normalised before sending. If the backend doesn't echo
  /// `reminder_offsets` yet, falls back to the sent list so the UI reflects the
  /// user's choice (mirrors [setAgentMode]).
  Future<GeneralSettings> setReminderOffsets(List<String> offsets) async {
    final normalised = normalizeReminderOffsets(offsets);
    final json = await _t.patchJson(
      '/api/settings/general',
      {'reminder_offsets': normalised},
    );
    _assertSuccess(json);
    final data = Map<String, dynamic>.from(json['data'] as Map);
    data.putIfAbsent('reminder_offsets', () => normalised);
    return GeneralSettings.fromJson(data);
  }

  // ── Permissions ───────────────────────────────────────────────────────────

  /// Returns the current permissions settings from the server.
  Future<PermissionsSettings> getPermissions() async {
    final json = await _t.getJson('/api/permissions/settings');
    _assertSuccess(json);
    return PermissionsSettings.fromJson(
        json['data'] as Map<String, dynamic>);
  }

  /// Sets a single category permission level and returns the updated settings.
  Future<PermissionsSettings> setCategoryPermission(
      String category, String level) async {
    final json = await _t.patchJson('/api/permissions/settings', {
      'category_defaults': {category: level},
    });
    _assertSuccess(json);
    return PermissionsSettings.fromJson(
        json['data'] as Map<String, dynamic>);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  void _assertSuccess(Map<String, dynamic> json) {
    if (json['success'] == true) return;
    final msg = json['error']?.toString() ?? 'Unknown server error';
    throw SettingsException(msg);
  }
}

/// Thrown when the server returns `{success: false}`.
class SettingsException implements Exception {
  final String message;
  const SettingsException(this.message);

  @override
  String toString() => 'SettingsException: $message';
}
