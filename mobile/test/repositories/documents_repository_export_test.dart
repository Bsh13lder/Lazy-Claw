import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';

/// Records the path passed to getBytes/uploadFile and returns canned data.
class _RecordingTransport implements DocumentsTransport {
  String? lastPath;
  File? lastFile;
  final List<int> bytes;
  final Map<String, dynamic> json;

  _RecordingTransport({this.bytes = const [1, 2, 3], this.json = const {}});

  Map<String, dynamic>? lastBody;

  @override
  Future<List<int>> getBytes(String path) async {
    lastPath = path;
    return bytes;
  }

  @override
  Future<List<int>> postBytes(String path, Map<String, dynamic> body) async {
    lastPath = path;
    lastBody = body;
    return bytes;
  }

  @override
  Future<Map<String, dynamic>> uploadFile(String path, File file) async {
    lastPath = path;
    lastFile = file;
    return json;
  }

  @override
  Future<Map<String, dynamic>> getJson(String path) async => json;
  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async => json;
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async => json;
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => json;
}

void main() {
  test('exportBytes sheets → /export?format=xlsx', () async {
    final t = _RecordingTransport(bytes: const [80, 75]); // "PK"
    final repo = DocumentsRepository(t);
    final out = await repo.exportBytes(DocKind.sheets, 'wb-1', 'xlsx');
    expect(t.lastPath, '/api/sheets/wb-1/export?format=xlsx');
    expect(out, [80, 75]);
  });

  test('exportBytes docs → /export?format=pdf', () async {
    final t = _RecordingTransport();
    final repo = DocumentsRepository(t);
    await repo.exportBytes(DocKind.docs, 'd-1', 'pdf');
    expect(t.lastPath, '/api/docs/d-1/export?format=pdf');
  });

  test('downloadPdf → /pdf/{id}/download', () async {
    final t = _RecordingTransport();
    final repo = DocumentsRepository(t);
    await repo.downloadPdf('p-1');
    expect(t.lastPath, '/api/pdf/p-1/download');
  });

  test('exportBytes with password → POST /export with {format, password}', () async {
    final t = _RecordingTransport();
    final repo = DocumentsRepository(t);
    await repo.exportBytes(DocKind.sheets, 'wb-1', 'xlsx', password: 'pw');
    expect(t.lastPath, '/api/sheets/wb-1/export');
    expect(t.lastBody, {'format': 'xlsx', 'password': 'pw'});
  });

  test('downloadPdf with password → POST /download with {password}', () async {
    final t = _RecordingTransport();
    final repo = DocumentsRepository(t);
    await repo.downloadPdf('p-1', password: 'pw');
    expect(t.lastPath, '/api/pdf/p-1/download');
    expect(t.lastBody, {'password': 'pw'});
  });

  test('importSheet → POST /api/sheets/import, unwraps {sheet:row}', () async {
    final t = _RecordingTransport(json: {'sheet': {'id': 's9', 'name': 'Budget'}});
    final repo = DocumentsRepository(t);
    final meta = await repo.importSheet(File('/tmp/x.xlsx'));
    expect(t.lastPath, '/api/sheets/import');
    expect(meta.id, 's9');
    expect(meta.name, 'Budget');
  });

  test('importDoc → POST /api/docs/import, unwraps {doc:row}', () async {
    final t = _RecordingTransport(json: {'doc': {'id': 'd9', 'name': 'Report'}});
    final repo = DocumentsRepository(t);
    final meta = await repo.importDoc(File('/tmp/x.docx'));
    expect(t.lastPath, '/api/docs/import');
    expect(meta.id, 'd9');
  });
}
