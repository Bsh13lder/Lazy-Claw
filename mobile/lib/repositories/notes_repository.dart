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

class NotesRepository {
  final NotesTransport _t;
  NotesRepository(this._t);

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
  Future<Note> createNote({
    String? title,
    required String content,
    List<String>? tags,
    int? importance,
    bool? pinned,
  }) async {
    final body = <String, dynamic>{'content': content};
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
