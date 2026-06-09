import '../chat/chat_message.dart';
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
/// Assistant `tool_calls` become [ToolActivity] chips — a finished tool call
/// carries its `result`, so [ToolActivity.resultPreview] is set (renders as a
/// "done" chip) rather than left null (which would imply still-running).
ChatMessage? mapApiMessage(Map<String, dynamic> json) {
  final role = json['role']?.toString() ?? '';
  final content = json['content']?.toString() ?? '';

  if (role == 'user') {
    return ChatMessage(role: 'user', content: content);
  }

  if (role == 'assistant') {
    final rawCalls = (json['tool_calls'] as List?) ?? const [];
    final activities = <ToolActivity>[];
    for (final t in rawCalls) {
      if (t is! Map) continue;
      final tm = Map<String, dynamic>.from(t);
      final result = tm['result'];
      activities.add(ToolActivity(
        name: tm['name']?.toString() ?? 'tool',
        args: tm['args'] is Map
            ? Map<String, dynamic>.from(tm['args'] as Map)
            : const {},
        resultPreview: result?.toString(),
      ));
    }
    // A row with neither text nor tools is noise — skip it.
    if (content.isEmpty && activities.isEmpty) return null;
    return ChatMessage(
      role: 'assistant',
      content: content,
      streaming: false,
      toolActivities: activities,
    );
  }

  // tool / system rows don't render as standalone bubbles.
  return null;
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
    return out;
  }
}
