import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements DocumentsTransport {
  final Map<String, dynamic> response;
  final List<int> bytes;

  String? lastMethod;
  String? lastPath;
  Map<String, dynamic>? lastBody;
  File? lastFile;

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
}

class _ThrowingTransport implements DocumentsTransport {
  final Object error;
  _ThrowingTransport([this.error = 'network error']);
  @override
  Future<Map<String, dynamic>> getJson(String path) async => throw error;
  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async =>
      throw error;
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => throw error;
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => throw error;
  @override
  Future<List<int>> getBytes(String path) async => throw error;
}

void main() {
  // ── list ─────────────────────────────────────────────────────────────────
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

    test('propagates transport errors', () async {
      expect(
        () => DocumentsRepository(_ThrowingTransport('boom')).list(DocKind.docs),
        throwsA(equals('boom')),
      );
    });
  });

  // ── create ─────────────────────────────────────────────────────────────────
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

  // ── importPdf ──────────────────────────────────────────────────────────────
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

  // ── getPayload ───────────────────────────────────────────────────────────────
  group('DocumentsRepository.getPayload', () {
    test('sheet → GET /api/sheets/{id}, parses payload', () async {
      final t = _FakeTransport(response: {
        'id': 's1',
        'name': 'Budget',
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
      expect(p.payload['sheets'], isA<Map>());
    });

    test('doc → GET /api/docs/{id}', () async {
      final t = _FakeTransport(response: {
        'id': 'd1',
        'name': 'Letter',
        'payload': {
          'body': {'dataStream': 'Hi\r\n'}
        },
      });
      final p = await DocumentsRepository(t).getPayload(DocKind.docs, 'd1');
      expect(t.lastPath, '/api/docs/d1');
      expect(p.payload['body'], isA<Map>());
    });
  });

  // ── pdf bytes / extract ──────────────────────────────────────────────────────
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

  // ── aiEdit ─────────────────────────────────────────────────────────────────
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

  // ── delete ─────────────────────────────────────────────────────────────────
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

  // ── DocMeta.fromJson ─────────────────────────────────────────────────────────
  group('DocMeta.fromJson', () {
    test('defaults blank name to Untitled', () {
      expect(DocMeta.fromJson({'id': 'x', 'name': ''}).name, 'Untitled');
      expect(DocMeta.fromJson({'id': 'x'}).name, 'Untitled');
    });

    test('parses pages as int from string', () {
      expect(DocMeta.fromJson({'id': 'x', 'pages': '5'}).pages, 5);
    });
  });
}
