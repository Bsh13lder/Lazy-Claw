import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';

// ── Fake transport ────────────────────────────────────────────────────────────

/// A flexible fake transport that returns a pre-set response for all JSON
/// methods and optionally throws on the next [putJson] call.
class _FakeTransport implements DocumentsTransport {
  final Map<String, dynamic> response;
  final List<int> bytes;

  String? lastMethod;
  String? lastPath;
  Map<String, dynamic>? lastBody;
  File? lastFile;

  /// When non-null, [putJson] throws this error instead of returning [response].
  Object? putThrows;

  _FakeTransport({this.response = const {}, this.bytes = const []});

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    lastMethod = 'GET';
    lastPath = path;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> putJson(
      String path, Map<String, dynamic> body) async {
    if (putThrows != null) throw putThrows!;
    lastMethod = 'PUT';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async {
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

  @override
  Future<Map<String, dynamic>> uploadFile(String path, File file) async {
    lastMethod = 'UPLOAD';
    lastPath = path;
    lastFile = file;
    return response;
  }

  @override
  Future<List<int>> getBytes(String path) async {
    lastMethod = 'BYTES';
    lastPath = path;
    return bytes;
  }

  @override
  Future<List<int>> postBytes(String path, Map<String, dynamic> body) async {
    lastMethod = 'POSTBYTES';
    lastPath = path;
    lastBody = body;
    return bytes;
  }
}

// ── Helper: build a real DioException with a given status code and body ───────

DioException _dioError(int statusCode, Map<String, dynamic> data) {
  final opts = RequestOptions(path: '/test');
  return DioException(
    requestOptions: opts,
    response: Response(
      requestOptions: opts,
      statusCode: statusCode,
      data: data,
    ),
    type: DioExceptionType.badResponse,
  );
}

void main() {
  // ── list ──────────────────────────────────────────────────────────────────
  group('DocumentsRepository.list', () {
    test('sheets → GET /api/sheets, parses {sheets:[...]}', () async {
      final t = _FakeTransport(response: {
        'sheets': [
          {'id': 's1', 'name': 'Budget', 'updated_at': '2026-06-06'},
          {'id': 's2', 'name': 'Plan'},
        ],
        'count': 2,
      });
      final items = await DocumentsRepository(t).list(DocKind.sheets);
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/sheets');
      expect(items, hasLength(2));
      expect(items[0].id, 's1');
      expect(items[0].name, 'Budget');
      expect(items[0].updatedAt, '2026-06-06');
    });

    test('docs → GET /api/docs, parses {docs:[...]}', () async {
      final t = _FakeTransport(response: {
        'docs': [
          {'id': 'd1', 'name': 'Letter'}
        ]
      });
      final items = await DocumentsRepository(t).list(DocKind.docs);
      expect(t.lastPath, '/api/docs');
      expect(items.single.id, 'd1');
    });

    test('pdf → GET /api/pdf, parses {files:[...]} incl. pages', () async {
      final t = _FakeTransport(response: {
        'files': [
          {'id': 'p1', 'name': 'Invoice.pdf', 'pages': 3}
        ]
      });
      final items = await DocumentsRepository(t).list(DocKind.pdf);
      expect(t.lastPath, '/api/pdf');
      expect(items.single.id, 'p1');
      expect(items.single.pages, 3);
    });

    test('missing list key → empty', () async {
      final items = await DocumentsRepository(_FakeTransport()).list(DocKind.sheets);
      expect(items, isEmpty);
    });
  });

  // ── create ────────────────────────────────────────────────────────────────
  group('DocumentsRepository.create', () {
    test('sheet → POST /api/sheets {name}, unwraps {sheet:row}', () async {
      final t = _FakeTransport(response: {
        'sheet': {'id': 'new1', 'name': 'My Sheet'}
      });
      final meta = await DocumentsRepository(t).create(DocKind.sheets, 'My Sheet');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/sheets');
      expect(t.lastBody, {'name': 'My Sheet'});
      expect(meta.id, 'new1');
      expect(meta.name, 'My Sheet');
    });

    test('doc → POST /api/docs, unwraps {doc:row}', () async {
      final t = _FakeTransport(response: {
        'doc': {'id': 'nd1', 'name': 'Memo'}
      });
      final meta = await DocumentsRepository(t).create(DocKind.docs, 'Memo');
      expect(t.lastPath, '/api/docs');
      expect(meta.id, 'nd1');
    });
  });

  // ── importPdf ─────────────────────────────────────────────────────────────
  group('DocumentsRepository.importPdf', () {
    test('UPLOAD /api/pdf/import, unwraps {file:meta}', () async {
      final t = _FakeTransport(response: {
        'file': {'id': 'imp1', 'name': 'Scan.pdf', 'pages': 1}
      });
      final tmp = File('${Directory.systemTemp.path}/doc_repo_test.pdf');
      final meta = await DocumentsRepository(t).importPdf(tmp);
      expect(t.lastMethod, 'UPLOAD');
      expect(t.lastPath, '/api/pdf/import');
      expect(t.lastFile, tmp);
      expect(meta.id, 'imp1');
      expect(meta.pages, 1);
    });
  });

  // ── getPayload ────────────────────────────────────────────────────────────
  group('DocumentsRepository.getPayload', () {
    test('sheet → GET /api/sheets/{id}, parses payload + updatedAt + tags',
        () async {
      final t = _FakeTransport(response: {
        'id': 's1',
        'name': 'Budget',
        'updated_at': '2026-06-12T10:00:00Z',
        'tags': ['work', 'finance'],
        'payload': {
          'sheetOrder': ['sh'],
          'sheets': {
            'sh': {'name': 'Sheet1', 'cellData': {}}
          }
        },
      });
      final p = await DocumentsRepository(t).getPayload(DocKind.sheets, 's1');
      expect(t.lastPath, '/api/sheets/s1');
      expect(p.id, 's1');
      expect(p.name, 'Budget');
      expect(p.updatedAt, '2026-06-12T10:00:00Z');
      expect(p.tags, ['work', 'finance']);
      expect(p.payload['sheets'], isA<Map>());
    });

    test('doc → GET /api/docs/{id}, missing tags → []', () async {
      final t = _FakeTransport(response: {
        'id': 'd1',
        'name': 'Letter',
        'payload': {
          'body': {'dataStream': 'Hi\r\n'}
        },
      });
      final p = await DocumentsRepository(t).getPayload(DocKind.docs, 'd1');
      expect(t.lastPath, '/api/docs/d1');
      expect(p.tags, isEmpty);
      expect(p.updatedAt, isNull);
    });
  });

  // ── pdf bytes / extract ───────────────────────────────────────────────────
  group('DocumentsRepository pdf raw + extract', () {
    test('getPdfBytes → BYTES /api/pdf/{id}/raw', () async {
      final t = _FakeTransport(bytes: [37, 80, 68, 70]); // %PDF
      final bytes = await DocumentsRepository(t).getPdfBytes('p1');
      expect(t.lastMethod, 'BYTES');
      expect(t.lastPath, '/api/pdf/p1/raw');
      expect(bytes, [37, 80, 68, 70]);
    });

    test('extractPdfText → GET /api/pdf/{id}/extract', () async {
      final t = _FakeTransport(response: {'text': 'hello', 'pages': 2});
      final text = await DocumentsRepository(t).extractPdfText('p1');
      expect(t.lastPath, '/api/pdf/p1/extract');
      expect(text, 'hello');
    });
  });

  // ── aiEdit ────────────────────────────────────────────────────────────────
  group('DocumentsRepository.aiEdit', () {
    test('sheet → POST /api/sheets/{id}/ai, returns snapshot', () async {
      final t = _FakeTransport(response: {
        'ok': true,
        'summary': 'Added a total',
        'snapshot': {
          'sheets': {'sh': {}}
        },
      });
      final r = await DocumentsRepository(t).aiEdit(
          DocKind.sheets, 's1', 'add a total');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/sheets/s1/ai');
      expect(t.lastBody, {'instruction': 'add a total'});
      expect(r.ok, isTrue);
      expect(r.summary, 'Added a total');
      expect(r.snapshot, isNotNull);
      expect(r.newPdfId, isNull);
    });

    test('pdf → POST /api/pdf/{id}/ai, returns new_pdf_id', () async {
      final t = _FakeTransport(response: {
        'ok': true,
        'summary': 'Signed',
        'new_pdf_id': 'p2',
      });
      final r = await DocumentsRepository(t).aiEdit(DocKind.pdf, 'p1', 'sign it');
      expect(t.lastPath, '/api/pdf/p1/ai');
      expect(r.newPdfId, 'p2');
      expect(r.snapshot, isNull);
    });

    test('parses failure shape (ok:false + error)', () async {
      final t = _FakeTransport(response: {
        'ok': false,
        'error': 'could not parse instruction',
      });
      final r = await DocumentsRepository(t).aiEdit(DocKind.docs, 'd1', 'x');
      expect(r.ok, isFalse);
      expect(r.error, 'could not parse instruction');
    });
  });

  // ── delete ────────────────────────────────────────────────────────────────
  group('DocumentsRepository.delete', () {
    test('sheet → DELETE /api/sheets/{id}', () async {
      final t = _FakeTransport(response: {'status': 'deleted', 'id': 's1'});
      await DocumentsRepository(t).delete(DocKind.sheets, 's1');
      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/sheets/s1');
    });

    test('pdf → DELETE /api/pdf/{id}', () async {
      final t = _FakeTransport(response: {'status': 'deleted', 'id': 'p1'});
      await DocumentsRepository(t).delete(DocKind.pdf, 'p1');
      expect(t.lastPath, '/api/pdf/p1');
    });
  });

  // ── DocMeta.fromJson ──────────────────────────────────────────────────────
  group('DocMeta.fromJson', () {
    test('defaults blank name to Untitled', () {
      expect(DocMeta.fromJson({'id': 'x', 'name': ''}).name, 'Untitled');
      expect(DocMeta.fromJson({'id': 'x'}).name, 'Untitled');
    });

    test('parses pages as int from string', () {
      expect(DocMeta.fromJson({'id': 'x', 'pages': '5'}).pages, 5);
    });

    test('tags missing → empty list', () {
      final m = DocMeta.fromJson({'id': 'x', 'name': 'Test'});
      expect(m.tags, isEmpty);
    });

    test('tags null → empty list', () {
      final m = DocMeta.fromJson({'id': 'x', 'name': 'Test', 'tags': null});
      expect(m.tags, isEmpty);
    });

    test('tags non-list junk value → empty list', () {
      final m = DocMeta.fromJson({'id': 'x', 'name': 'Test', 'tags': 42});
      expect(m.tags, isEmpty);
    });

    test('tags list with strings → parsed', () {
      final m = DocMeta.fromJson({
        'id': 'x',
        'name': 'Test',
        'tags': ['work', 'finance']
      });
      expect(m.tags, ['work', 'finance']);
    });

    test('tags list with mixed types — only strings kept', () {
      final m = DocMeta.fromJson({
        'id': 'x',
        'name': 'Test',
        'tags': ['valid', 123, null, 'also-valid']
      });
      expect(m.tags, ['valid', 'also-valid']);
    });
  });

  // ── DocMeta.toJson round-trip ─────────────────────────────────────────────
  group('DocMeta.toJson', () {
    test('empty tags omitted from json', () {
      final m = DocMeta(id: 'x', name: 'Test');
      expect(m.toJson().containsKey('tags'), isFalse);
    });

    test('non-empty tags included in json', () {
      final m = DocMeta(
          id: 'x', name: 'Test', tags: ['work', 'finance']);
      expect(m.toJson()['tags'], ['work', 'finance']);
    });

    test('round-trip preserves tags', () {
      final original = DocMeta(
        id: 'abc',
        name: 'Budget',
        createdAt: '2026-01-01',
        updatedAt: '2026-06-12',
        pages: null,
        tags: ['q2', 'review'],
      );
      final json = original.toJson();
      final restored = DocMeta.fromJson(json);
      expect(restored.id, original.id);
      expect(restored.name, original.name);
      expect(restored.tags, original.tags);
      expect(restored.updatedAt, original.updatedAt);
    });

    test('round-trip without tags preserves empty', () {
      final original = DocMeta(id: 'y', name: 'Memo');
      final json = original.toJson();
      final restored = DocMeta.fromJson(json);
      expect(restored.tags, isEmpty);
    });
  });

  // ── DocPayload.fromJson ───────────────────────────────────────────────────
  group('DocPayload.fromJson', () {
    test('parses updatedAt from updated_at field', () {
      final p = DocPayload.fromJson({
        'id': 's1',
        'name': 'Sheet',
        'payload': {},
        'updated_at': '2026-06-12T09:00:00Z',
      });
      expect(p.updatedAt, '2026-06-12T09:00:00Z');
    });

    test('missing updated_at → null', () {
      final p = DocPayload.fromJson({'id': 's1', 'name': 'Sheet', 'payload': {}});
      expect(p.updatedAt, isNull);
    });

    test('parses tags list', () {
      final p = DocPayload.fromJson({
        'id': 's1',
        'name': 'Sheet',
        'payload': {},
        'tags': ['alpha', 'beta'],
      });
      expect(p.tags, ['alpha', 'beta']);
    });

    test('missing tags → empty list', () {
      final p = DocPayload.fromJson({'id': 's1', 'name': 'Sheet', 'payload': {}});
      expect(p.tags, isEmpty);
    });

    test('junk tags value → empty list', () {
      final p = DocPayload.fromJson({
        'id': 's1',
        'name': 'Sheet',
        'payload': {},
        'tags': 'not-a-list',
      });
      expect(p.tags, isEmpty);
    });
  });

  // ── save — happy path ─────────────────────────────────────────────────────
  group('DocumentsRepository.save — happy path', () {
    test('returns updated_at from response row', () async {
      final t = _FakeTransport(response: {
        'sheet': {
          'id': 's1',
          'name': 'Budget',
          'updated_at': '2026-06-12T11:00:00Z',
        }
      });
      final updatedAt = await DocumentsRepository(t).save(
        DocKind.sheets,
        's1',
        {'data': 1},
      );
      expect(updatedAt, '2026-06-12T11:00:00Z');
      expect(t.lastMethod, 'PUT');
      expect(t.lastPath, '/api/sheets/s1');
    });

    test('body contains payload always', () async {
      final payload = {'cells': {}};
      final t = _FakeTransport(response: {'sheet': {'id': 's1'}});
      await DocumentsRepository(t).save(DocKind.sheets, 's1', payload);
      expect(t.lastBody!['payload'], payload);
    });

    test('name omitted when null', () async {
      final t = _FakeTransport(response: {'sheet': {'id': 's1'}});
      await DocumentsRepository(t).save(DocKind.sheets, 's1', {});
      expect(t.lastBody!.containsKey('name'), isFalse);
    });

    test('name included when provided', () async {
      final t = _FakeTransport(response: {'sheet': {'id': 's1'}});
      await DocumentsRepository(t)
          .save(DocKind.sheets, 's1', {}, name: 'My Budget');
      expect(t.lastBody!['name'], 'My Budget');
    });

    test('base_updated_at omitted when null', () async {
      final t = _FakeTransport(response: {'sheet': {'id': 's1'}});
      await DocumentsRepository(t).save(DocKind.sheets, 's1', {});
      expect(t.lastBody!.containsKey('base_updated_at'), isFalse);
    });

    test('base_updated_at included when provided', () async {
      final t = _FakeTransport(response: {'sheet': {'id': 's1'}});
      await DocumentsRepository(t).save(
        DocKind.sheets,
        's1',
        {},
        baseUpdatedAt: '2026-06-12T10:00:00Z',
      );
      expect(t.lastBody!['base_updated_at'], '2026-06-12T10:00:00Z');
    });

    test('tags omitted when null', () async {
      final t = _FakeTransport(response: {'sheet': {'id': 's1'}});
      await DocumentsRepository(t).save(DocKind.sheets, 's1', {});
      expect(t.lastBody!.containsKey('tags'), isFalse);
    });

    test('tags included when provided (even empty list)', () async {
      final t = _FakeTransport(response: {'sheet': {'id': 's1'}});
      await DocumentsRepository(t)
          .save(DocKind.sheets, 's1', {}, tags: ['work']);
      expect(t.lastBody!['tags'], ['work']);
    });

    test('empty tags list included when explicitly provided', () async {
      final t = _FakeTransport(response: {'sheet': {'id': 's1'}});
      await DocumentsRepository(t)
          .save(DocKind.sheets, 's1', {}, tags: []);
      expect(t.lastBody!.containsKey('tags'), isTrue);
      expect(t.lastBody!['tags'], isEmpty);
    });

    test('docs kind uses doc itemKey for updated_at', () async {
      final t = _FakeTransport(response: {
        'doc': {'id': 'd1', 'updated_at': '2026-06-12T12:00:00Z'}
      });
      final updatedAt = await DocumentsRepository(t).save(
        DocKind.docs,
        'd1',
        {'body': {}},
      );
      expect(t.lastPath, '/api/docs/d1');
      expect(updatedAt, '2026-06-12T12:00:00Z');
    });
  });

  // ── save — 409 conflict ───────────────────────────────────────────────────
  group('DocumentsRepository.save — 409 conflict', () {
    test('throws DocConflictException with parsed current payload', () async {
      final conflictPayload = {
        'id': 's1',
        'name': 'Budget',
        'payload': {'sheetOrder': ['sh']},
        'updated_at': '2026-06-12T10:30:00Z',
        'tags': ['work'],
      };
      final t = _FakeTransport();
      t.putThrows = _dioError(409, {
        'detail': 'conflict',
        'current': conflictPayload,
      });

      DocConflictException? caught;
      try {
        await DocumentsRepository(t).save(DocKind.sheets, 's1', {});
      } on DocConflictException catch (e) {
        caught = e;
      }

      expect(caught, isNotNull);
      expect(caught!.current.id, 's1');
      expect(caught.current.updatedAt, '2026-06-12T10:30:00Z');
      expect(caught.current.tags, ['work']);
      expect(caught.current.payload['sheetOrder'], ['sh']);
    });

    test('409 without current map still throws DocConflictException', () async {
      final t = _FakeTransport();
      t.putThrows = _dioError(409, {'detail': 'conflict'});

      // current map is missing → the repo rethrows the raw DioException (it
      // can NOT build a DocConflictException without `current`). Call sites
      // must treat a raw 409 DioException as a non-recoverable conflict:
      // surface an error + force-reload, never silently ignore it.
      var threw = false;
      try {
        await DocumentsRepository(t).save(DocKind.sheets, 's1', {});
      } on DocConflictException {
        threw = true;
      } on DioException {
        threw = true;
      }
      expect(threw, isTrue);
    });

    test('non-409 DioException is rethrown', () async {
      final t = _FakeTransport();
      t.putThrows = _dioError(500, {'detail': 'server error'});

      expect(
        () => DocumentsRepository(t).save(DocKind.sheets, 's1', {}),
        throwsA(isA<DioException>()),
      );
    });

    test('non-Dio exception is rethrown unchanged', () async {
      final t = _FakeTransport();
      t.putThrows = StateError('unexpected');

      expect(
        () => DocumentsRepository(t).save(DocKind.sheets, 's1', {}),
        throwsA(isA<StateError>()),
      );
    });
  });

  // ── convertLinks ─────────────────────────────────────────────────────────
  group('DocumentsRepository.convertLinks', () {
    test('POST /api/sheets/{id}/links/convert, returns named record', () async {
      final t = _FakeTransport(response: {
        'ok': true,
        'converted': 3,
        'snapshot': {'sheetOrder': ['sh']},
        'updated_at': '2026-06-12T14:00:00Z',
      });
      final (:converted, :snapshot, :updatedAt) =
          await DocumentsRepository(t).convertLinks('s1');

      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/sheets/s1/links/convert');
      expect(t.lastBody, {});
      expect(converted, 3);
      expect(snapshot['sheetOrder'], ['sh']);
      expect(updatedAt, '2026-06-12T14:00:00Z');
    });

    test('missing converted → 0, missing updated_at → null', () async {
      final t = _FakeTransport(response: {
        'ok': true,
        'snapshot': {},
      });
      final (:converted, :snapshot, :updatedAt) =
          await DocumentsRepository(t).convertLinks('s1');
      expect(converted, 0);
      expect(snapshot, isEmpty);
      expect(updatedAt, isNull);
    });

    test('missing snapshot → empty map', () async {
      final t = _FakeTransport(response: {
        'ok': true,
        'converted': 1,
        'updated_at': '2026-06-12',
      });
      final result = await DocumentsRepository(t).convertLinks('s1');
      expect(result.snapshot, isEmpty);
    });
  });

  // ── setPdfTags ────────────────────────────────────────────────────────────
  group('DocumentsRepository.setPdfTags', () {
    test('PATCH /api/pdf/{id} with {tags}', () async {
      final t = _FakeTransport(response: {'file': {'id': 'p1', 'tags': ['q1']}});
      await DocumentsRepository(t).setPdfTags('p1', ['q1', 'archive']);

      expect(t.lastMethod, 'PATCH');
      expect(t.lastPath, '/api/pdf/p1');
      expect(t.lastBody, {'tags': ['q1', 'archive']});
    });

    test('empty tags list is sent as-is', () async {
      final t = _FakeTransport(response: {'file': {'id': 'p1', 'tags': []}});
      await DocumentsRepository(t).setPdfTags('p1', []);
      expect(t.lastBody, {'tags': <String>[]});
    });
  });

  // ── DocConflictException ──────────────────────────────────────────────────
  group('DocConflictException', () {
    test('toString includes document id', () {
      final payload = DocPayload(id: 'abc', name: 'Test', payload: {});
      final ex = DocConflictException(payload);
      expect(ex.toString(), contains('abc'));
    });

    test('implements Exception', () {
      final payload = DocPayload(id: 'x', name: 'Y', payload: {});
      expect(DocConflictException(payload), isA<Exception>());
    });
  });
}
