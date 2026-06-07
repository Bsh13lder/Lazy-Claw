import '../core/api/api_client.dart';

// ── Notification delivery channel ───────────────────────────────────────────

/// Where server-originated notifications (watchers, background-job done,
/// escalations, …) are delivered. Mirrors the backend contract:
/// `channel ∈ "telegram" | "app" | "both"` (default "telegram").
enum NotificationChannel {
  telegram('telegram', 'Telegram'),
  app('app', 'App'),
  both('both', 'Both');

  const NotificationChannel(this.wire, this.label);

  /// The exact string the backend stores / expects.
  final String wire;

  /// Human-facing label for the segmented control.
  final String label;

  /// Parse a wire value, defaulting to [telegram] for anything unknown/absent
  /// (matches the backend default and fails safe — never silently enables
  /// in-app delivery the user didn't pick).
  static NotificationChannel fromWire(String? raw) {
    for (final c in NotificationChannel.values) {
      if (c.wire == raw) return c;
    }
    return NotificationChannel.telegram;
  }
}

// ── Feed models ─────────────────────────────────────────────────────────────

/// One server-originated notification from `GET /api/notifications`.
class ServerNotification {
  final String id;
  final String kind;
  final String title;
  final String body;
  final String createdAt;

  const ServerNotification({
    required this.id,
    required this.kind,
    required this.title,
    required this.body,
    required this.createdAt,
  });

  factory ServerNotification.fromJson(Map<String, dynamic> json) =>
      ServerNotification(
        id: (json['id'] ?? '').toString(),
        kind: (json['kind'] ?? '').toString(),
        title: (json['title'] ?? '').toString(),
        body: (json['body'] ?? '').toString(),
        createdAt: (json['created_at'] ?? '').toString(),
      );
}

/// One delta page from `GET /api/notifications?since=<iso>`:
/// the new notifications plus the server `now` to use as the next cursor.
class NotificationFeed {
  /// Notifications the server emitted since the cursor (newest-relevant first
  /// or chronological — order is preserved as the server returns it).
  final List<ServerNotification> notifications;

  /// Server "now" timestamp — becomes the next `since` cursor (avoids relying
  /// on client clocks / individual `created_at` values).
  final String now;

  const NotificationFeed({required this.notifications, required this.now});
}

// ── Transport seam (testable) ───────────────────────────────────────────────

/// Minimal I/O seam so [NotificationsRepository] is unit-testable without a
/// live Dio instance. Mirrors the [SettingsTransport] / [NotesTransport]
/// pattern.
abstract class NotificationsTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
}

class DioNotificationsTransport implements NotificationsTransport {
  final ApiClient _client;
  DioNotificationsTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) =>
      _client.get<Map<String, dynamic>>(
        path,
        queryParams: queryParams,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.post<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

// ── Repository ──────────────────────────────────────────────────────────────

/// Reads the server notification feed and the delivery-channel preference.
///
/// The feed contract is envelope-free (`{notifications, now}`); the channel
/// contract is `{channel}`. Both parsers are tolerant of an optional
/// `{success, data}` wrapper so the app keeps working if the backend later
/// standardises on the project's envelope.
class NotificationsRepository {
  final NotificationsTransport _t;
  NotificationsRepository(this._t);

  // ── Feed ──────────────────────────────────────────────────────────────────

  /// Pull notifications emitted since [since] (ISO timestamp; null = first
  /// pull / full window). Maps `GET /api/notifications?since=<iso>` →
  /// `{notifications, now}`.
  Future<NotificationFeed> fetchSince({String? since}) async {
    final json = await _t.getJson(
      '/api/notifications',
      queryParams: (since == null || since.isEmpty) ? null : {'since': since},
    );
    final body = _unwrap(json);
    final raw = body['notifications'] as List? ?? const [];
    return NotificationFeed(
      notifications: raw
          .map((e) =>
              ServerNotification.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList(),
      now: (body['now'] ?? '').toString(),
    );
  }

  // ── Channel preference ──────────────────────────────────────────────────

  /// Read the current delivery channel. `GET /api/settings/notifications`.
  Future<NotificationChannel> getChannel() async {
    final json = await _t.getJson('/api/settings/notifications');
    return NotificationChannel.fromWire(_channelOf(json));
  }

  /// Set the delivery channel. `POST /api/settings/notifications {channel}`.
  /// Returns the channel the server confirms (so a server-side normalisation
  /// wins over the requested value).
  Future<NotificationChannel> setChannel(NotificationChannel channel) async {
    final json = await _t
        .postJson('/api/settings/notifications', {'channel': channel.wire});
    // Some backends echo only `{ok:true}` on write — fall back to the
    // requested channel when the response carries no channel field.
    final confirmed = _channelOf(json);
    return confirmed == null
        ? channel
        : NotificationChannel.fromWire(confirmed);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  /// Unwrap an optional `{success, data}` envelope, returning the inner map
  /// when present, else the top-level map unchanged.
  Map<String, dynamic> _unwrap(Map<String, dynamic> json) {
    final data = json['data'];
    if (data is Map) return Map<String, dynamic>.from(data);
    return json;
  }

  /// Extract the `channel` string from either the top level or a `data`
  /// envelope. Returns null when absent.
  String? _channelOf(Map<String, dynamic> json) {
    final top = json['channel'];
    if (top is String) return top;
    final data = json['data'];
    if (data is Map && data['channel'] is String) {
      return data['channel'] as String;
    }
    return null;
  }
}
