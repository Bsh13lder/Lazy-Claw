// Repository for the unified Inbox feature.
//
// Uses the same Transport-abstraction pattern as TasksRepository,
// NotesRepository, etc. — InboxTransport is a testable seam so tests
// can inject a fake without spinning up Dio/HTTP.
//
// ApiClient.get  → queryParams (NOT queryParameters), fromJson optional.
// ApiClient.post → data, fromJson optional.

import '../core/api/api_client.dart';
import 'inbox_models.dart';

// ── Transport abstraction (testable seam) ─────────────────────────────────────

abstract class InboxTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });

  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  );
}

class DioInboxTransport implements InboxTransport {
  final ApiClient _client;
  DioInboxTransport(this._client);

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

// ── Repository ────────────────────────────────────────────────────────────────

class InboxRepository {
  final InboxTransport _t;
  InboxRepository(this._t);

  /// Fetch all inbox threads, optionally filtered by [channel]
  /// (e.g. `'whatsapp'`, `'telegram'`, `'email'`).
  ///
  /// Maps `GET /api/inbox/threads?channel=` → `{threads, count}`.
  Future<List<InboxThread>> fetchThreads({String? channel}) async {
    final json = await _t.getJson(
      '/api/inbox/threads',
      queryParams: channel == null ? null : {'channel': channel},
    );
    final raw = json['threads'] as List? ?? const [];
    return raw
        .map((e) => InboxThread.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Fetch all messages for [threadId].
  ///
  /// Maps `GET /api/inbox/threads/{id}/messages` → `{messages, thread}`.
  Future<List<InboxMessage>> fetchMessages(String threadId) async {
    final json = await _t.getJson('/api/inbox/threads/$threadId/messages');
    final raw = json['messages'] as List? ?? const [];
    return raw
        .map((e) => InboxMessage.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Mark a thread as read.
  ///
  /// Maps `POST /api/inbox/threads/{id}/read`.
  Future<void> markRead(String threadId) =>
      _t.postJson('/api/inbox/threads/$threadId/read', {});

  /// Send a reply on [threadId].
  ///
  /// [mode] can be `'direct'` (send immediately) or `'suggest'`
  /// (queue as AI suggestion for human review).
  ///
  /// Maps `POST /api/inbox/threads/{id}/reply {text, mode}`.
  Future<Map<String, dynamic>> reply(
    String threadId,
    String text, {
    String mode = 'direct',
  }) =>
      _t.postJson(
        '/api/inbox/threads/$threadId/reply',
        {'text': text, 'mode': mode},
      );
}
