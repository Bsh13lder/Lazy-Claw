import '../core/api/api_client.dart';
import '../models/note.dart';

/// Testable seam — mirrors the TasksTransport pattern.
abstract class NotesTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> deleteJson(String path);
}

class DioNotesTransport implements NotesTransport {
  final ApiClient _client;
  DioNotesTransport(this._client);

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
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.patch<Map<String, dynamic>>(
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

/// A server note paired with its authoritative `updated_at` — the timestamp
/// last-write-wins compares against. The [Note] model already surfaces
/// `updatedAt`, but the sync engine reads the explicit server value here so the
/// merge logic never depends on whichever field the API happened to echo.
class ServerNote {
  final Note note;
  final String? updatedAt;
  const ServerNote(this.note, this.updatedAt);
}

/// One server-side delta page from `GET /api/lazybrain/notes/changes`.
class NoteChanges {
  /// Notes created/updated server-side since the cursor (with their server
  /// `updated_at` for LWW).
  final List<ServerNote> notes;

  /// Ids the server soft-deleted since the cursor.
  final List<String> deleted;

  /// Server "now" timestamp — becomes the next cursor (avoids clock skew).
  final String now;

  const NoteChanges({
    required this.notes,
    required this.deleted,
    required this.now,
  });
}

class NotesRepository {
  final NotesTransport _t;
  NotesRepository(this._t);

  /// Pull the delta since [since] (ISO timestamp, null = full snapshot).
  /// Maps `GET /api/lazybrain/notes/changes?since=<iso>` → {notes, deleted, now}.
  Future<NoteChanges> fetchChanges({String? since}) async {
    final json = await _t.getJson(
      '/api/lazybrain/notes/changes',
      queryParams: since == null ? null : {'since': since},
    );
    final rawNotes = json['notes'] as List? ?? const [];
    final rawDeleted = json['deleted'] as List? ?? const [];
    return NoteChanges(
      notes: rawNotes.map((e) {
        final map = Map<String, dynamic>.from(e as Map);
        final updatedAt = map['updated_at']?.toString();
        return ServerNote(Note.fromJson(map), updatedAt);
      }).toList(),
      deleted: rawDeleted.map((e) => e.toString()).toList(),
      now: (json['now'] ?? '').toString(),
    );
  }

  /// Fetch notes. Optionally filter by tag / pinned status / limit.
  Future<List<Note>> listNotes({
    String? tag,
    bool? pinned,
    int? limit,
    int? offset,
  }) async {
    final params = <String, dynamic>{};
    if (tag != null) params['tag'] = tag;
    if (pinned == true) params['pinned'] = 'true';
    if (limit != null) params['limit'] = limit.toString();
    if (offset != null) params['offset'] = offset.toString();

    final json = await _t.getJson(
      '/api/lazybrain/notes',
      queryParams: params.isEmpty ? null : params,
    );
    final rawList = json['notes'] as List? ?? [];
    return rawList
        .map((e) => Note.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Fetch a single note by [id].
  Future<Note> getNote(String id) async {
    final json = await _t.getJson('/api/lazybrain/notes/$id');
    return Note.fromJson(json);
  }

  /// Create a note. [content] is required; [title] is optional.
  ///
  /// Pass [id] to send a client-minted UUID — `POST /api/lazybrain/notes`
  /// accepts it, making the create idempotent on outbox replay.
  Future<Note> createNote({
    String? id,
    String? title,
    required String content,
    List<String>? tags,
    int? importance,
    bool? pinned,
  }) async {
    final body = <String, dynamic>{'content': content};
    if (id != null) body['id'] = id;
    if (title != null) body['title'] = title;
    if (tags != null) body['tags'] = tags;
    if (importance != null) body['importance'] = importance;
    if (pinned != null) body['pinned'] = pinned;

    final json = await _t.postJson('/api/lazybrain/notes', body);
    return Note.fromJson(json);
  }

  /// Update an existing note. Only the fields that are non-null are sent.
  Future<Note> updateNote(
    String id, {
    String? title,
    String? content,
    List<String>? tags,
    int? importance,
    bool? pinned,
  }) async {
    final body = <String, dynamic>{};
    if (title != null) body['title'] = title;
    if (content != null) body['content'] = content;
    if (tags != null) body['tags'] = tags;
    if (importance != null) body['importance'] = importance;
    if (pinned != null) body['pinned'] = pinned;

    final json = await _t.patchJson('/api/lazybrain/notes/$id', body);
    return Note.fromJson(json);
  }

  /// Patch a note via PATCH /api/lazybrain/notes/{id} with a pre-built
  /// snake_case field map. Used by the sync engine, which carries the outbox
  /// payload (already snake_case) rather than named arguments.
  Future<void> updateNotePatch(String id, Map<String, dynamic> patch) async {
    await _t.patchJson('/api/lazybrain/notes/$id', patch);
  }

  /// Delete a note by [id].
  Future<void> deleteNote(String id) async {
    await _t.deleteJson('/api/lazybrain/notes/$id');
  }

  /// Full-text search. Returns matching notes.
  Future<List<Note>> search(String query, {String? tag, int limit = 20}) async {
    final params = <String, dynamic>{'q': query, 'limit': limit.toString()};
    if (tag != null) params['tag'] = tag;

    final json = await _t.getJson(
      '/api/lazybrain/search',
      queryParams: params,
    );
    final rawList = json['results'] as List? ?? [];
    return rawList
        .map((e) => Note.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }
}
