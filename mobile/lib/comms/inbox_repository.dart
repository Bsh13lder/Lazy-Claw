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

  /// Fetch a binary payload (message media bytes).
  Future<List<int>> getBytes(String path);

  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  );

  Future<Map<String, dynamic>> deleteJson(String path);
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

  @override
  Future<List<int>> getBytes(String path) async {
    final response = await _client.downloadFile(path);
    return (response.data as List<int>?) ?? const <int>[];
  }

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.put<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> deleteJson(String path) =>
      _client.delete<Map<String, dynamic>>(
        path,
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
  /// [mode] can be `'direct'` (send immediately) or `'ai'`
  /// (hand the thread to an AI conversation — server Phase E).
  /// These are the only values the server's `Literal` accepts.
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

  /// Download the media bytes behind a message (voice note, photo, file).
  ///
  /// Maps `GET /api/inbox/threads/{id}/media/{messageId}`.
  Future<List<int>> fetchMedia(String threadId, String messageId) =>
      _t.getBytes('/api/inbox/threads/$threadId/media/$messageId');

  /// Read the standing instruction for [channel] (null when unset).
  ///
  /// Maps `GET /api/inbox/channels/{channel}/instruction`.
  Future<String?> getChannelInstruction(String channel) async {
    final json =
        await _t.getJson('/api/inbox/channels/$channel/instruction');
    return json['instruction'] as String?;
  }

  /// Set the standing instruction for [channel] — the agent executes it on
  /// every new message batch and reports the result via notifications.
  ///
  /// Maps `PUT /api/inbox/channels/{channel}/instruction`.
  Future<Map<String, dynamic>> setChannelInstruction(
    String channel,
    String instruction,
  ) =>
      _t.putJson(
        '/api/inbox/channels/$channel/instruction',
        {'instruction': instruction},
      );

  /// Clear the standing instruction for [channel].
  ///
  /// Maps `DELETE /api/inbox/channels/{channel}/instruction`.
  Future<void> clearChannelInstruction(String channel) =>
      _t.deleteJson('/api/inbox/channels/$channel/instruction');

  /// Read this thread's per-contact instruction (null when unset).
  ///
  /// Rides the messages endpoint — its `thread` envelope carries the
  /// decrypted `instruction` field.
  Future<String?> getThreadInstruction(String threadId) async {
    final json = await _t.getJson('/api/inbox/threads/$threadId/messages');
    final thread = json['thread'];
    if (thread is Map) return thread['instruction'] as String?;
    return null;
  }

  /// Set the per-contact standing instruction — the agent executes it as a
  /// real turn whenever THIS contact writes.
  ///
  /// Maps `PUT /api/inbox/threads/{id}/instruction`.
  Future<Map<String, dynamic>> setThreadInstruction(
    String threadId,
    String instruction,
  ) =>
      _t.putJson(
        '/api/inbox/threads/$threadId/instruction',
        {'instruction': instruction},
      );

  /// Clear the per-contact standing instruction.
  ///
  /// Maps `DELETE /api/inbox/threads/{id}/instruction`.
  Future<void> clearThreadInstruction(String threadId) =>
      _t.deleteJson('/api/inbox/threads/$threadId/instruction');

  /// Name this thread's contact — saves the name on the thread, attaches the
  /// handle(s) to the unified contact store, and creates a [[Name]] LazyBrain
  /// page.
  ///
  /// Maps `PUT /api/inbox/threads/{id}/contact`.
  Future<Map<String, dynamic>> nameContact(String threadId, String name) =>
      _t.putJson('/api/inbox/threads/$threadId/contact', {'name': name});
}
