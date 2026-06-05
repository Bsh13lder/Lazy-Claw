import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/notes_repository.dart';
import 'package:lazyclaw_mobile/models/note.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements NotesTransport {
  String? lastPath;
  Map<String, dynamic>? lastBody;
  Map<String, dynamic>? lastQueryParams;
  String? lastMethod;
  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    lastMethod = 'GET';
    lastPath = path;
    lastQueryParams = queryParams;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'PATCH';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    lastMethod = 'DELETE';
    lastPath = path;
    return response;
  }
}

// ── Fixture ────────────────────────────────────────────────────────────────

Map<String, dynamic> _noteJson({
  String id = 'n1',
  String? title = 'Test note',
  String content = 'Some content',
  bool pinned = false,
}) =>
    {
      'id': id,
      'title': title,
      'content': content,
      'tags': <String>[],
      'importance': 0,
      'pinned': pinned,
      'trace_session_id': null,
      'title_key': null,
      'created_at': '2026-06-05T10:00:00Z',
      'updated_at': '2026-06-05T10:00:00Z',
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('NotesRepository.listNotes', () {
    test('GET /api/lazybrain/notes and parses note list', () async {
      final t = _FakeTransport({
        'notes': [_noteJson(id: 'a1', title: 'Note A'), _noteJson(id: 'a2', title: 'Note B')],
      });
      final repo = NotesRepository(t);
      final notes = await repo.listNotes();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/lazybrain/notes');
      expect(notes, hasLength(2));
      expect(notes[0].id, 'a1');
      expect(notes[1].title, 'Note B');
    });

    test('returns empty list when notes key is missing', () async {
      final t = _FakeTransport({});
      final notes = await NotesRepository(t).listNotes();
      expect(notes, isEmpty);
    });

    test('sends no queryParams when all filters are null', () async {
      final t = _FakeTransport({'notes': []});
      await NotesRepository(t).listNotes();
      expect(t.lastQueryParams, isNull);
    });

    test('passes pinned filter as query param', () async {
      final t = _FakeTransport({'notes': []});
      await NotesRepository(t).listNotes(pinned: true);
      expect(t.lastQueryParams, containsPair('pinned', 'true'));
    });

    test('passes tag filter as query param', () async {
      final t = _FakeTransport({'notes': []});
      await NotesRepository(t).listNotes(tag: 'work');
      expect(t.lastQueryParams, containsPair('tag', 'work'));
    });

    test('passes limit as query param', () async {
      final t = _FakeTransport({'notes': []});
      await NotesRepository(t).listNotes(limit: 50);
      expect(t.lastQueryParams, containsPair('limit', '50'));
    });
  });

  group('NotesRepository.getNote', () {
    test('GET /api/lazybrain/notes/{id}', () async {
      final t = _FakeTransport(_noteJson(id: 'n42', title: 'Specific note'));
      final note = await NotesRepository(t).getNote('n42');
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/lazybrain/notes/n42');
      expect(note.id, 'n42');
      expect(note.title, 'Specific note');
    });
  });

  group('NotesRepository.createNote', () {
    test('POST /api/lazybrain/notes with title and content', () async {
      final t = _FakeTransport(_noteJson(id: 'new1', title: 'New note'));
      final note = await NotesRepository(t).createNote(
        title: 'New note',
        content: 'Some markdown',
      );
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/lazybrain/notes');
      expect(t.lastBody, containsPair('title', 'New note'));
      expect(t.lastBody, containsPair('content', 'Some markdown'));
      expect(note, isA<Note>());
      expect(note.id, 'new1');
    });

    test('POST without title sends only content', () async {
      final t = _FakeTransport(_noteJson(id: 'notitle', title: null));
      final note = await NotesRepository(t).createNote(content: 'Just content');
      expect(t.lastBody!.containsKey('title'), isFalse);
      expect(t.lastBody, containsPair('content', 'Just content'));
      expect(note.id, 'notitle');
    });
  });

  group('NotesRepository.updateNote', () {
    test('PATCH /api/lazybrain/notes/{id} with title and content', () async {
      final t = _FakeTransport(_noteJson(id: 'u1', title: 'Updated'));
      final note = await NotesRepository(t).updateNote(
        'u1',
        title: 'Updated',
        content: 'New content',
      );
      expect(t.lastMethod, 'PATCH');
      expect(t.lastPath, '/api/lazybrain/notes/u1');
      expect(t.lastBody, containsPair('title', 'Updated'));
      expect(t.lastBody, containsPair('content', 'New content'));
      expect(note.id, 'u1');
    });

    test('PATCH sends only fields that are provided', () async {
      final t = _FakeTransport(_noteJson(id: 'u2'));
      await NotesRepository(t).updateNote('u2', title: 'Only title');
      expect(t.lastBody, containsPair('title', 'Only title'));
      expect(t.lastBody!.containsKey('content'), isFalse);
    });
  });

  group('NotesRepository.deleteNote', () {
    test('DELETE /api/lazybrain/notes/{id}', () async {
      final t = _FakeTransport({'status': 'deleted', 'id': 'del1'});
      await NotesRepository(t).deleteNote('del1');
      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/lazybrain/notes/del1');
    });
  });

  group('NotesRepository.search', () {
    test('GET /api/lazybrain/search with query param', () async {
      final t = _FakeTransport({
        'query': 'hello',
        'results': [_noteJson(id: 's1', title: 'Search result')],
      });
      final notes = await NotesRepository(t).search('hello');
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/lazybrain/search');
      expect(t.lastQueryParams, containsPair('q', 'hello'));
      expect(notes, hasLength(1));
      expect(notes[0].id, 's1');
    });

    test('returns empty list when results key is missing', () async {
      final t = _FakeTransport({'query': 'nothing'});
      final notes = await NotesRepository(t).search('nothing');
      expect(notes, isEmpty);
    });
  });
}
