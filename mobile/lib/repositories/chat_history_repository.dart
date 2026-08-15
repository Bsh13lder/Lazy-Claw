import '../chat/chat_message.dart';
import '../chat/turn_merge.dart';
import '../core/api/api_client.dart';

// ── Model ──────────────────────────────────────────────────────────────────

/// Lightweight descriptor for a chat session from `GET /api/chat/sessions`.
///
/// The mobile app only cares about the PRIMARY session — the cross-channel
/// (Telegram / CLI / web) shared thread — so it can replay the conversation
/// the user last saw on another surface.
class ChatSessionInfo {
  final String id;
  final String title;
  final bool isPrimary;
  final int messageCount;
  final String createdAt;

  const ChatSessionInfo({
    required this.id,
    required this.title,
    required this.isPrimary,
    required this.messageCount,
    required this.createdAt,
  });

  factory ChatSessionInfo.fromJson(Map<String, dynamic> json) => ChatSessionInfo(
        id: json['id']?.toString() ?? '',
        title: json['title']?.toString() ?? 'Chat',
        isPrimary: json['is_primary'] == true || json['is_primary'] == 1,
        messageCount: (json['message_count'] as num?)?.toInt() ?? 0,
        createdAt: json['created_at']?.toString() ?? '',
      );
}

/// Maps one decrypted `/api/chat/.../messages` row to a renderable
/// [ChatMessage], or `null` for rows that shouldn't render as a bubble
/// (bare `tool` / `system` rows, or empty assistant rows with no tools).
///
/// Assistant `tool_calls` entries follow the 2026-08 contract
/// `{id, name, display, arguments, result?, status}` with
/// status ∈ {"done","unknown"} — mapped chips are NEVER running (a history
/// chip has no live result stream, so a running one would spin forever).
/// Legacy rows (`args` key, result-with-no-status) still map gracefully.
/// Parses the server's `created_at` into a UTC [DateTime]. SQLite emits
/// `"2026-08-13 15:31:09"` (UTC, no suffix); some rows carry full ISO
/// with offsets. Tolerant: unparseable → null (legacy rows render
/// without a timestamp rather than a wrong one).
DateTime? parseServerTime(String? raw) {
  final s = (raw ?? '').trim();
  if (s.isEmpty) return null;
  var normalized = s.replaceFirst(' ', 'T');
  final hasZone = normalized.endsWith('Z') ||
      RegExp(r'[+-]\d{2}:?\d{2}$').hasMatch(normalized);
  if (!hasZone) normalized = '${normalized}Z';
  return DateTime.tryParse(normalized)?.toUtc();
}

ChatMessage? mapApiMessage(Map<String, dynamic> json) {
  final role = json['role']?.toString() ?? '';
  final content = json['content']?.toString() ?? '';
  // Server row identity: id drives delta-merge dedup; kind marks special
  // rows — 'notification' on assistant rows (bell treatment) and 'cron' on
  // user rows ([JOB:...] instruction rows rendered as a system pill). Both
  // are optional — old rows without them still render as plain bubbles.
  final id = json['id']?.toString();
  final kind = json['kind']?.toString();
  final createdAt = parseServerTime(json['created_at']?.toString());

  if (role == 'user') {
    return ChatMessage(
      role: 'user',
      content: content,
      id: id,
      kind: kind,
      createdAt: createdAt,
    );
  }

  if (role == 'assistant') {
    final rawCalls = (json['tool_calls'] as List?) ?? const [];
    final activities = <ToolActivity>[];
    for (final t in rawCalls) {
      if (t is! Map) continue;
      final tm = Map<String, dynamic>.from(t);
      final rawArgs = tm['arguments'] ?? tm['args'];
      final result = tm['result'];
      activities.add(ToolActivity(
        name: tm['name']?.toString() ?? 'tool',
        displayName: _optString(tm['display']),
        toolCallId: _optString(tm['id']),
        args: rawArgs is Map
            ? Map<String, dynamic>.from(rawArgs)
            : const {},
        resultPreview: result?.toString(),
        status: _historyToolStatus(tm['status'], hasResult: result != null),
      ));
    }
    // A row with neither text nor tools is noise — skip it.
    if (content.isEmpty && activities.isEmpty) return null;
    return ChatMessage(
      role: 'assistant',
      content: content,
      id: id,
      kind: kind,
      createdAt: createdAt,
      streaming: false,
      toolActivities: activities,
    );
  }

  // tool / system rows don't render as standalone bubbles.
  return null;
}

/// Status for a history chip: 'done' → done; a legacy row that carries a
/// result but no status also counts as done (the call clearly finished).
/// EVERYTHING else — 'unknown', absent, or even a bogus 'running' — maps to
/// unknown, because a history chip must never spin.
ToolStatus _historyToolStatus(dynamic raw, {required bool hasResult}) {
  final s = raw?.toString();
  if (s == 'done') return ToolStatus.done;
  if (s == null && hasResult) return ToolStatus.done;
  return ToolStatus.unknown;
}

/// Optional string: null / blank degrade to null, non-strings stringify.
String? _optString(dynamic v) {
  final s = v?.toString().trim();
  return (s == null || s.isEmpty) ? null : s;
}

// ── Transport seam ─────────────────────────────────────────────────────────

abstract class ChatHistoryTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });
}

class DioChatHistoryTransport implements ChatHistoryTransport {
  final ApiClient _client;
  DioChatHistoryTransport(this._client);

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
}

// ── Repository ─────────────────────────────────────────────────────────────

/// Loads prior conversation history so the chat screen isn't empty on open.
///
/// The server decrypts message content before returning it, so the client
/// receives plaintext JSON. History loading is best-effort: callers should
/// treat a thrown error as "no history" and let the live socket carry on.
class ChatHistoryRepository {
  final ChatHistoryTransport _t;
  ChatHistoryRepository(this._t);

  Future<List<ChatSessionInfo>> listSessions() async {
    final json = await _t.getJson('/api/chat/sessions');
    return ((json['sessions'] as List?) ?? const [])
        .map((e) => ChatSessionInfo.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList(growable: false);
  }

  /// Returns the primary session's most-recent messages mapped to
  /// [ChatMessage]s, **oldest-first** (ready to seed the chat reducer).
  /// Empty when the user has no history yet.
  ///
  /// Rows of one batch-persisted agent turn are collapsed into a single
  /// bubble by [mergeTurnRows] BEFORE they leave the repository — this is the
  /// only history source, so the seed path and every delta-merge tail fetch
  /// are merged identically (the reducer must never see the raw interim rows,
  /// or a re-fetch would insert them as duplicates).
  Future<List<ChatMessage>> loadPrimaryHistory({int limit = 50}) async {
    final sessions = await listSessions();
    if (sessions.isEmpty) return const [];

    final primary = sessions.firstWhere(
      (s) => s.isPrimary,
      orElse: () => sessions.first,
    );

    final json = await _t.getJson(
      '/api/chat/sessions/${primary.id}/messages',
      queryParams: {'limit': limit.toString()},
    );

    final raw = (json['messages'] as List?) ?? const [];
    final out = <ChatMessage>[];
    for (final e in raw) {
      final m = mapApiMessage(Map<String, dynamic>.from(e as Map));
      if (m != null) out.add(m);
    }
    return mergeTurnRows(out);
  }
}
