import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';

/// Records calls and returns a canned response. Mirrors the repo's transport
/// seam so we assert path + body without a real Dio.
class _RecordingTransport implements DocumentsTransport {
  Map<String, dynamic> response;
  String? lastPath;
  Map<String, dynamic>? lastBody;
  String? lastMethod;

  _RecordingTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(String path) async => response;

  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> putJson(String path, Map<String, dynamic> body) async {
    lastMethod = 'PUT';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => response;

  @override
  Future<Map<String, dynamic>> uploadFile(String path, File file) async => response;

  @override
  Future<List<int>> getBytes(String path) async => const [];
}

/// Throws the PRODUCTION exception shape (ApiError) on every verb — per the
/// sync-engine lesson, fakes that throw a generic error green-light data-loss.
class _ThrowingTransport implements DocumentsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path) async => throw ApiError(500, 'x');
  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async =>
      throw ApiError(500, 'recalc failed');
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async =>
      throw ApiError(500, 'save failed');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => throw ApiError(500, 'x');
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => throw ApiError(500, 'x');
  @override
  Future<List<int>> getBytes(String path) async => throw ApiError(500, 'x');
}

void main() {
  test('recalc posts the payload and returns the recalced snapshot', () async {
    final fresh = {
      'sheets': {
        's1': {'cellData': {'2': {'0': {'v': 5}}}},
      },
    };
    final t = _RecordingTransport({'ok': true, 'snapshot': fresh});
    final repo = DocumentsRepository(t);

    final out = await repo.recalc('wb-1', {'sheets': {}});
    expect(t.lastMethod, 'POST');
    expect(t.lastPath, '/api/sheets/wb-1/recalc');
    expect(t.lastBody, {'payload': {'sheets': {}}});
    expect(out['sheets']['s1']['cellData']['2']['0']['v'], 5);
  });

  test('recalc falls back to the input snapshot when response lacks one', () async {
    final t = _RecordingTransport({'ok': true});
    final repo = DocumentsRepository(t);
    final input = {'sheets': {'s1': {}}};
    final out = await repo.recalc('wb-1', input);
    expect(out, input);
  });

  test('save PUTs name + payload to the kind path', () async {
    final t = _RecordingTransport({'ok': true});
    final repo = DocumentsRepository(t);
    await repo.save(DocKind.sheets, 'wb-1', 'Budget', {'sheets': {}});
    expect(t.lastMethod, 'PUT');
    expect(t.lastPath, '/api/sheets/wb-1');
    expect(t.lastBody, {'name': 'Budget', 'payload': {'sheets': {}}});
  });

  test('recalc propagates the production ApiError', () async {
    final repo = DocumentsRepository(_ThrowingTransport());
    expect(
      () => repo.recalc('wb-1', {'sheets': {}}),
      throwsA(isA<ApiError>()),
    );
  });

  test('save propagates the production ApiError', () async {
    final repo = DocumentsRepository(_ThrowingTransport());
    expect(
      () => repo.save(DocKind.docs, 'd-1', 'Doc', {'body': {}}),
      throwsA(isA<ApiError>()),
    );
  });
}
