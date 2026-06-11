import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/repositories/lazybrain_repository.dart';

// ── Canned server payloads ────────────────────────────────────────────────────
//
// Shapes copied from the real backend:
//   lazyclaw/gateway/routes/lazybrain.py + lazyclaw/lazybrain/store.py
//
// Note dict (store.list_notes): id, title, content, tags, importance, pinned,
// trace_session_id, title_key, memory_type, created_at, updated_at.

Map<String, dynamic> _noteJson({
  String id = 'n1',
  String? title = 'Journal 2026-06-10',
  String content = '# Heading\nbody text',
  List<String> tags = const ['journal', 'owner/user'],
  bool pinned = false,
}) =>
    {
      'id': id,
      'title': title,
      'content': content,
      'tags': tags,
      'importance': 5,
      'pinned': pinned,
      'trace_session_id': null,
      'title_key': title?.toLowerCase(),
      'memory_type': 'session-log',
      'created_at': '2026-06-10 08:00:00.000000',
      'updated_at': '2026-06-10 09:00:00.000000',
    };

// ── Fake transport ────────────────────────────────────────────────────────────

class _FakeTransport implements LazyBrainTransport {
  String? lastPath;
  String? lastMethod;
  Map<String, dynamic>? lastQueryParams;
  Map<String, dynamic>? lastBody;
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
}

/// Fake transport that always throws the production error shape: [ApiError]
/// is what [DioLazyBrainTransport] propagates after the Dio _ErrorInterceptor
/// rejects a non-2xx response (see test/comms/inbox_repository_test.dart).
class _ErrorTransport implements LazyBrainTransport {
  final ApiError error;
  _ErrorTransport(this.error);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) =>
      Future.error(error);

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      Future.error(error);
}

void main() {
  // ── Model tests ────────────────────────────────────────────────────────────

  test('BrainTag.fromJson parses server shape', () {
    final t = BrainTag.fromJson({'tag': 'journal', 'count': 14});
    expect(t.tag, 'journal');
    expect(t.count, 14);
  });

  test('BrainTag.fromJson defaults on malformed payload', () {
    final t = BrainTag.fromJson({});
    expect(t.tag, '');
    expect(t.count, 0);
  });

  test('SemanticHit.fromJson parses note fields plus _score', () {
    final hit = SemanticHit.fromJson({
      ..._noteJson(id: 'hit1', title: 'Browser canvas'),
      '_score': 0.8123,
    });
    expect(hit.note.id, 'hit1');
    expect(hit.note.title, 'Browser canvas');
    expect(hit.score, closeTo(0.8123, 1e-9));
  });

  test('SemanticHit.fromJson tolerates missing _score (bm25/substring path)',
      () {
    final hit = SemanticHit.fromJson(_noteJson(id: 'hit2'));
    expect(hit.note.id, 'hit2');
    expect(hit.score, isNull);
  });

  test('AskResult.fromJson parses full server shape', () {
    final r = AskResult.fromJson({
      'question': 'what is the plan?',
      'answer': 'The plan is X [[Note]].',
      'sources': ['Note', 'Other note'],
      'source_count': 2,
      'retrieval_source': 'hybrid+graph',
    });
    expect(r.question, 'what is the plan?');
    expect(r.answer, 'The plan is X [[Note]].');
    expect(r.sources, ['Note', 'Other note']);
    expect(r.sourceCount, 2);
    expect(r.retrievalSource, 'hybrid+graph');
  });

  test('AskResult.fromJson tolerates missing retrieval_source + sources', () {
    final r = AskResult.fromJson({
      'question': 'q',
      'answer': '',
      'source_count': 0,
    });
    expect(r.answer, '');
    expect(r.sources, isEmpty);
    expect(r.sourceCount, 0);
    expect(r.retrievalSource, isNull);
  });

  // ── fetchJournal ───────────────────────────────────────────────────────────

  group('LazyBrainRepository.fetchJournal', () {
    test('calls GET /api/lazybrain/journal with limit and parses notes',
        () async {
      final transport = _FakeTransport({
        'notes': [
          _noteJson(id: 'j1', title: 'Journal 2026-06-10'),
          _noteJson(id: 'j2', title: 'Journal 2026-06-09'),
        ],
      });
      final repo = LazyBrainRepository(transport);
      final notes = await repo.fetchJournal(limit: 14);
      expect(transport.lastMethod, 'GET');
      expect(transport.lastPath, '/api/lazybrain/journal');
      expect(transport.lastQueryParams, {'limit': 14});
      expect(notes.length, 2);
      expect(notes.first.id, 'j1');
      expect(notes.first.title, 'Journal 2026-06-10');
      expect(notes.first.tags, contains('journal'));
    });

    test('handles empty journal', () async {
      final transport = _FakeTransport({'notes': []});
      final repo = LazyBrainRepository(transport);
      expect(await repo.fetchJournal(), isEmpty);
    });

    test('propagates ApiError', () {
      final repo = LazyBrainRepository(_ErrorTransport(ApiError(500, 'boom')));
      expect(() => repo.fetchJournal(), throwsA(isA<ApiError>()));
    });
  });

  // ── fetchTags ──────────────────────────────────────────────────────────────

  group('LazyBrainRepository.fetchTags', () {
    test('calls GET /api/lazybrain/tags and parses counts', () async {
      final transport = _FakeTransport({
        'tags': [
          {'tag': 'journal', 'count': 14},
          {'tag': 'owner/user', 'count': 7},
        ],
      });
      final repo = LazyBrainRepository(transport);
      final tags = await repo.fetchTags();
      expect(transport.lastMethod, 'GET');
      expect(transport.lastPath, '/api/lazybrain/tags');
      expect(tags.length, 2);
      expect(tags.first.tag, 'journal');
      expect(tags.first.count, 14);
    });

    test('handles empty tags', () async {
      final transport = _FakeTransport({'tags': []});
      final repo = LazyBrainRepository(transport);
      expect(await repo.fetchTags(), isEmpty);
    });

    test('propagates ApiError', () {
      final repo = LazyBrainRepository(_ErrorTransport(ApiError(502, 'bad')));
      expect(() => repo.fetchTags(), throwsA(isA<ApiError>()));
    });
  });

  // ── fetchPinned ────────────────────────────────────────────────────────────

  group('LazyBrainRepository.fetchPinned', () {
    test('calls GET /api/lazybrain/notes?pinned=true', () async {
      final transport = _FakeTransport({
        'notes': [_noteJson(id: 'p1', title: 'Pinned idea', pinned: true)],
      });
      final repo = LazyBrainRepository(transport);
      final notes = await repo.fetchPinned();
      expect(transport.lastMethod, 'GET');
      expect(transport.lastPath, '/api/lazybrain/notes');
      expect(transport.lastQueryParams, {'pinned': 'true'});
      expect(notes.single.id, 'p1');
      expect(notes.single.pinned, isTrue);
    });

    test('propagates ApiError', () {
      final repo = LazyBrainRepository(_ErrorTransport(ApiError(500, 'down')));
      expect(() => repo.fetchPinned(), throwsA(isA<ApiError>()));
    });
  });

  // ── fetchNotesByTag ────────────────────────────────────────────────────────

  group('LazyBrainRepository.fetchNotesByTag', () {
    test('calls GET /api/lazybrain/notes?tag=<tag>', () async {
      final transport = _FakeTransport({
        'notes': [
          _noteJson(id: 't1', tags: ['topic/browser']),
        ],
      });
      final repo = LazyBrainRepository(transport);
      final notes = await repo.fetchNotesByTag('topic/browser');
      expect(transport.lastMethod, 'GET');
      expect(transport.lastPath, '/api/lazybrain/notes');
      expect(transport.lastQueryParams, {'tag': 'topic/browser'});
      expect(notes.single.id, 't1');
    });

    test('rejects empty tag without hitting the network', () {
      final transport = _FakeTransport({'notes': []});
      final repo = LazyBrainRepository(transport);
      expect(() => repo.fetchNotesByTag('  '), throwsArgumentError);
      expect(transport.lastPath, isNull);
    });

    test('propagates ApiError', () {
      final repo = LazyBrainRepository(_ErrorTransport(ApiError(500, 'x')));
      expect(() => repo.fetchNotesByTag('journal'), throwsA(isA<ApiError>()));
    });
  });

  // ── semanticSearch ─────────────────────────────────────────────────────────

  group('LazyBrainRepository.semanticSearch', () {
    test('POSTs query + k and parses results with source', () async {
      final transport = _FakeTransport({
        'query': 'browser cadence',
        'results': [
          {..._noteJson(id: 's1', title: 'Cadence'), '_score': 0.91},
          _noteJson(id: 's2', title: 'Profiles'),
        ],
        'source': 'hybrid',
      });
      final repo = LazyBrainRepository(transport);
      final result = await repo.semanticSearch('browser cadence', k: 5);
      expect(transport.lastMethod, 'POST');
      expect(transport.lastPath, '/api/lazybrain/semantic-search');
      expect(transport.lastBody, {'query': 'browser cadence', 'k': 5});
      expect(result.query, 'browser cadence');
      expect(result.source, 'hybrid');
      expect(result.hits.length, 2);
      expect(result.hits.first.score, closeTo(0.91, 1e-9));
      expect(result.hits.last.score, isNull);
    });

    test('handles empty results (source=empty)', () async {
      final transport = _FakeTransport({
        'query': 'nothing',
        'results': [],
        'source': 'empty',
      });
      final repo = LazyBrainRepository(transport);
      final result = await repo.semanticSearch('nothing');
      expect(result.hits, isEmpty);
      expect(result.source, 'empty');
    });

    test('rejects blank query without hitting the network', () {
      final transport = _FakeTransport({});
      final repo = LazyBrainRepository(transport);
      expect(() => repo.semanticSearch('   '), throwsArgumentError);
      expect(transport.lastPath, isNull);
    });

    test('propagates ApiError', () {
      final repo = LazyBrainRepository(_ErrorTransport(ApiError(503, 'llm')));
      expect(() => repo.semanticSearch('q'), throwsA(isA<ApiError>()));
    });
  });

  // ── ask ────────────────────────────────────────────────────────────────────

  group('LazyBrainRepository.ask', () {
    test('POSTs question + k and parses the answer', () async {
      final transport = _FakeTransport({
        'question': 'what did I decide about budgets?',
        'answer': 'You chose last-write-wins [[Budgets]].',
        'sources': ['Budgets'],
        'source_count': 1,
        'retrieval_source': 'hybrid',
      });
      final repo = LazyBrainRepository(transport);
      final result = await repo.ask('what did I decide about budgets?', k: 8);
      expect(transport.lastMethod, 'POST');
      expect(transport.lastPath, '/api/lazybrain/ask');
      expect(
        transport.lastBody,
        {'question': 'what did I decide about budgets?', 'k': 8},
      );
      expect(result.answer, 'You chose last-write-wins [[Budgets]].');
      expect(result.sources, ['Budgets']);
      expect(result.sourceCount, 1);
    });

    test('rejects blank question without hitting the network', () {
      final transport = _FakeTransport({});
      final repo = LazyBrainRepository(transport);
      expect(() => repo.ask(''), throwsArgumentError);
      expect(transport.lastPath, isNull);
    });

    test('propagates ApiError', () {
      final repo = LazyBrainRepository(_ErrorTransport(ApiError(500, 'err')));
      expect(() => repo.ask('q'), throwsA(isA<ApiError>()));
    });
  });
}
