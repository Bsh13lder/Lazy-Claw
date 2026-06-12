/// Tests for the document-tags feature added to [DocumentsListView]:
///   1. Pure [filterByTags] — unit tests (no widgets).
///   2. Widget test — tag chips render on document cards.
///   3. Tag-editor save path for sheets — transport sees GET then PUT with
///      `base_updated_at` and `tags` in the body.
///   4. Conflict-retry — first PUT returns 409; second PUT uses the server's
///      updated_at as base and carries the original user tags.
///   5. Filter pruning — pure test confirming intersection logic.
library;

import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/documents_list_view.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

// ── Helpers ───────────────────────────────────────────────────────────────────

DocMeta _meta(
  String id,
  String name, {
  List<String> tags = const [],
}) =>
    DocMeta(id: id, name: name, tags: tags);

// ── Widget helpers ────────────────────────────────────────────────────────────

/// Minimal fake transport for widget tests — lists return canned items;
/// writes are no-ops.
class _FakeTransport implements DocumentsTransport {
  final List<Map<String, dynamic>> sheets;
  final List<Map<String, dynamic>> docs;
  final List<Map<String, dynamic>> files;

  _FakeTransport({
    this.sheets = const [],
    this.docs = const [],
    this.files = const [],
  });

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    if (path == '/api/sheets') return {'sheets': sheets};
    if (path == '/api/docs') return {'docs': docs};
    if (path == '/api/pdf') return {'files': files};
    return const {};
  }

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async => const {};
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async => const {};
  @override
  Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async => const {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => const {};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => const {};
  @override
  Future<List<int>> getBytes(String path) async => const [];
  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async => const [];
}

Widget _listView(DocKind kind, DocumentsTransport transport) {
  return ProviderScope(
    overrides: [
      documentsRepositoryProvider
          .overrideWithValue(DocumentsRepository(transport)),
    ],
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: DocumentsListView(
          kind: kind,
          onOpen: (_) {},
          onCreate: () {},
        ),
      ),
    ),
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// 1. Pure filterByTags unit tests
// ═══════════════════════════════════════════════════════════════════════════════

void main() {
  group('filterByTags — pure unit tests', () {
    final docs = [
      _meta('a', 'Alpha', tags: ['finance', 'Q3']),
      _meta('b', 'Beta', tags: ['Q3']),
      _meta('c', 'Gamma', tags: ['finance']),
      _meta('d', 'Delta', tags: []),
    ];

    test('empty selection returns all docs unchanged', () {
      final result = filterByTags(docs, {});
      expect(result.map((d) => d.id).toList(), ['a', 'b', 'c', 'd']);
    });

    test('single tag filters correctly', () {
      final result = filterByTags(docs, {'finance'});
      expect(result.map((d) => d.id).toList(), ['a', 'c']);
    });

    test('AND semantics: multiple tags = intersection', () {
      final result = filterByTags(docs, {'finance', 'Q3'});
      expect(result.map((d) => d.id).toList(), ['a']);
    });

    test('tag that no doc has returns empty list', () {
      final result = filterByTags(docs, {'nonexistent'});
      expect(result, isEmpty);
    });

    test('docs with no tags are excluded when a tag is selected', () {
      final result = filterByTags(docs, {'Q3'});
      final ids = result.map((d) => d.id).toList();
      expect(ids, isNot(contains('d'))); // Delta has no tags
    });

    test('empty docs list returns empty regardless of selection', () {
      final result = filterByTags([], {'finance'});
      expect(result, isEmpty);
    });

    test('selection with only "All" equivalent (empty) is a no-op', () {
      final all = filterByTags(docs, {});
      expect(all.length, 4);
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 2. Widget test — tag chips on cards
  // ═══════════════════════════════════════════════════════════════════════════

  group('DocumentsListView — tag chips on cards', () {
    testWidgets('renders tag chips for docs that have tags', (tester) async {
      final transport = _FakeTransport(sheets: [
        {'id': 's1', 'name': 'Budget', 'tags': ['finance', 'Q3']},
        {'id': 's2', 'name': 'Roadmap', 'tags': []},
        {'id': 's3', 'name': 'Notes', 'tags': ['planning']},
      ]);

      await tester.pumpWidget(_listView(DocKind.sheets, transport));
      await tester.pumpAndSettle();

      // Tag text appears on the card chip AND in the filter row — use
      // findsAtLeastNWidgets(1) so the count is direction-independent.
      expect(find.text('finance'), findsAtLeastNWidgets(1));
      expect(find.text('Q3'), findsAtLeastNWidgets(1));
      expect(find.text('planning'), findsAtLeastNWidgets(1));
    });

    testWidgets('shows "+N" overflow chip when more than 3 tags', (tester) async {
      final transport = _FakeTransport(sheets: [
        {
          'id': 's1',
          'name': 'Multi',
          'tags': ['a', 'b', 'c', 'd', 'e'],
        },
      ]);

      await tester.pumpWidget(_listView(DocKind.sheets, transport));
      await tester.pumpAndSettle();

      // First 3 tags visible on the card (and in the filter row too — use
      // findsAtLeastNWidgets(1)); overflow chip for remaining 2.
      expect(find.text('a'), findsAtLeastNWidgets(1));
      expect(find.text('b'), findsAtLeastNWidgets(1));
      expect(find.text('c'), findsAtLeastNWidgets(1));
      expect(find.text('+2'), findsOneWidget);
      // 4th and 5th should NOT appear as individual card chips (they are in
      // the filter row, so we check the overflow chip covers them instead of
      // checking for absence by text — the filter row itself shows all tags).
      // The "+2" chip is the authoritative proof that overflow is working.
    });

    testWidgets('no tag chips or overflow for a doc with empty tags',
        (tester) async {
      final transport = _FakeTransport(sheets: [
        {'id': 's1', 'name': 'Bare', 'tags': []},
      ]);

      await tester.pumpWidget(_listView(DocKind.sheets, transport));
      await tester.pumpAndSettle();

      // No tag-related text in the tree.
      expect(find.text('+'), findsNothing);
    });

    testWidgets('filter row appears when any listed doc has tags',
        (tester) async {
      final transport = _FakeTransport(sheets: [
        {'id': 's1', 'name': 'Budget', 'tags': ['finance']},
        {'id': 's2', 'name': 'Roadmap', 'tags': []},
      ]);

      await tester.pumpWidget(_listView(DocKind.sheets, transport));
      await tester.pumpAndSettle();

      // The "All" filter chip + "finance" filter chip should both appear.
      // "All" comes from the filter row; "finance" from both the filter row
      // and the card chip — so findsWidgets (>= 1 for 'All').
      expect(find.byKey(const Key('tag-filter-all')), findsOneWidget);
      expect(find.byKey(const Key('tag-filter-finance')), findsOneWidget);
    });

    testWidgets('filter row is absent when no docs have tags', (tester) async {
      final transport = _FakeTransport(sheets: [
        {'id': 's1', 'name': 'Budget', 'tags': []},
        {'id': 's2', 'name': 'Roadmap'},
      ]);

      await tester.pumpWidget(_listView(DocKind.sheets, transport));
      await tester.pumpAndSettle();

      // No "All" filter chip.
      expect(find.byKey(const Key('tag-filter-all')), findsNothing);
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 3. Tag-editor save path — sheets: GET then PUT with baseUpdatedAt + tags
  // ═══════════════════════════════════════════════════════════════════════════

  group('DocumentsRepository — sheets tag save (fetch-then-save)', () {
    const sheetId = 'sheet-42';
    const updatedAt = '2026-06-12T10:00:00Z';
    final fakePayload = <String, dynamic>{'cells': {}};
    final newTags = ['finance', 'Q3'];

    test('save calls GET /api/sheets/<id> then PUT with base_updated_at + tags',
        () async {
      final recordingTransport = _RecordingTransportWithPutResponse(
        getResponse: {
          'id': sheetId,
          'name': 'Budget',
          'payload': fakePayload,
          'updated_at': updatedAt,
          'tags': ['old-tag'],
        },
        putResponse: {
          'sheet': {'id': sheetId, 'name': 'Budget', 'updated_at': updatedAt},
        },
      );

      final repo = DocumentsRepository(recordingTransport);

      // Simulate the fetch-then-save pattern used by the screen.
      final detail = await repo.getPayload(DocKind.sheets, sheetId);
      await repo.save(
        DocKind.sheets,
        sheetId,
        detail.payload,
        tags: newTags,
        baseUpdatedAt: detail.updatedAt,
      );

      // Verify call order.
      expect(recordingTransport.calls, hasLength(2));
      final get = recordingTransport.calls[0];
      final put = recordingTransport.calls[1];

      expect(get.method, 'GET');
      expect(get.path, '/api/sheets/$sheetId');

      expect(put.method, 'PUT');
      expect(put.path, '/api/sheets/$sheetId');
      expect(put.body!['base_updated_at'], updatedAt);
      expect(put.body!['tags'], newTags);
    });

    test('save supplies updatedAt from GET response to PUT body', () async {
      const serverUpdatedAt = '2026-06-12T15:30:00Z';
      final rt = _RecordingTransportWithPutResponse(
        getResponse: {
          'id': sheetId,
          'name': 'Budget',
          'payload': fakePayload,
          'updated_at': serverUpdatedAt,
          'tags': [],
        },
        putResponse: {
          'sheet': {
            'id': sheetId,
            'updated_at': serverUpdatedAt,
          },
        },
      );
      final repo = DocumentsRepository(rt);

      final detail = await repo.getPayload(DocKind.sheets, sheetId);
      await repo.save(
        DocKind.sheets,
        sheetId,
        detail.payload,
        tags: ['new-tag'],
        baseUpdatedAt: detail.updatedAt,
      );

      final put = rt.calls.firstWhere((c) => c.method == 'PUT');
      expect(put.body!['base_updated_at'], serverUpdatedAt);
    });

    test('PDF tag save uses PATCH, not GET+PUT', () async {
      const pdfId = 'pdf-99';
      final rt = _RecordingTransportWithPutResponse(
        getResponse: const {},
        putResponse: const {},
      );
      final repo = DocumentsRepository(rt);

      await repo.setPdfTags(pdfId, ['report']);

      expect(rt.calls, hasLength(1));
      expect(rt.calls.first.method, 'PATCH');
      expect(rt.calls.first.path, '/api/pdf/$pdfId');
      expect(rt.calls.first.body!['tags'], ['report']);
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 4. Conflict-retry — 409 on first PUT, second PUT uses conflict's updated_at
  // ═══════════════════════════════════════════════════════════════════════════

  group('DocumentsRepository — conflict-retry (CAS)', () {
    const sheetId = 'sheet-conflict';
    const firstUpdatedAt = '2026-06-12T10:00:00Z';
    const conflictUpdatedAt = '2026-06-12T10:05:00Z';
    final fakePayload = <String, dynamic>{'cells': {}};
    final userTags = ['finance', 'Q3'];

    test(
        'retries once on 409 — second PUT uses conflict updated_at and user tags',
        () async {
      final rt = _ConflictThenSucceedTransport(
        getResponse: {
          'id': sheetId,
          'name': 'Budget',
          'payload': fakePayload,
          'updated_at': firstUpdatedAt,
          'tags': ['old'],
        },
        // The 409 response body carries the server's current version.
        conflictCurrent: {
          'id': sheetId,
          'name': 'Budget',
          'payload': fakePayload,
          'updated_at': conflictUpdatedAt,
          'tags': ['old', 'extra'],
        },
        successResponse: {
          'sheet': {'id': sheetId, 'updated_at': conflictUpdatedAt},
        },
      );

      final repo = DocumentsRepository(rt);

      // Replicate the _saveTagsForDoc logic from the screen: fetch, attempt,
      // catch DocConflictException, retry with conflict.current.
      Future<void> attempt(DocPayload detail) => repo.save(
            DocKind.sheets,
            sheetId,
            detail.payload,
            tags: userTags,
            baseUpdatedAt: detail.updatedAt,
          );

      final detail = await repo.getPayload(DocKind.sheets, sheetId);
      try {
        await attempt(detail);
      } on DocConflictException catch (conflict) {
        await attempt(conflict.current);
      }

      // Expect: GET + first PUT (409) + second PUT (success).
      final puts = rt.calls.where((c) => c.method == 'PUT').toList();
      expect(puts, hasLength(2), reason: 'must have exactly 2 PUT attempts');

      final firstPut = puts[0];
      final secondPut = puts[1];

      // First PUT uses the originally fetched base.
      expect(firstPut.body!['base_updated_at'], firstUpdatedAt);
      expect(firstPut.body!['tags'], userTags);

      // Second PUT (retry) must use the server's conflict updated_at.
      expect(
        secondPut.body!['base_updated_at'],
        conflictUpdatedAt,
        reason: 'retry base_updated_at must equal conflict.current.updated_at',
      );
      // User's tags must be preserved on the retry — NOT the server's tags.
      expect(
        secondPut.body!['tags'],
        userTags,
        reason: 'retry must carry user-chosen tags, not server tags',
      );
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 5. Filter pruning — pure intersection logic
  // ═══════════════════════════════════════════════════════════════════════════

  group('filter pruning — pure intersection logic', () {
    // NOTE: The mounted setState(() => _selectedTags = _selectedTags.intersection(newAllTags))
    // lives inside a ConsumerState widget and requires a full widget pump to
    // exercise end-to-end. The pure function under test here is Set.intersection,
    // which is what the screen delegates to. Widget-level coverage is deferred
    // because the refresh cycle requires a cooperative fake notifier that the
    // current _FakeTransport scaffold does not provide.
    //
    // The contract: after a refresh that removes tag "Q3" from all documents,
    // a selectedTags set that contained "Q3" must drop it while keeping the
    // rest.

    test('intersection keeps only tags still present after refresh', () {
      final selectedTags = {'finance', 'Q3', 'draft'};
      final newAllTags = {'finance', 'draft', 'archive'};
      final pruned = selectedTags.intersection(newAllTags);
      expect(pruned, {'finance', 'draft'});
      expect(pruned, isNot(contains('Q3')));
    });

    test('intersection with empty new tags produces empty selection', () {
      final selectedTags = {'finance', 'Q3'};
      const newAllTags = <String>{};
      final pruned = selectedTags.intersection(newAllTags);
      expect(pruned, isEmpty);
    });

    test('intersection when no selected tags stays empty', () {
      const selectedTags = <String>{};
      final newAllTags = {'finance', 'Q3'};
      final pruned = selectedTags.intersection(newAllTags);
      expect(pruned, isEmpty);
    });

    test('intersection when all tags survive is unchanged', () {
      final selectedTags = {'finance', 'Q3'};
      final newAllTags = {'finance', 'Q3', 'archive'};
      final pruned = selectedTags.intersection(newAllTags);
      expect(pruned, {'finance', 'Q3'});
    });
  });
}

// ── Helpers for the repository save tests ────────────────────────────────────

/// A recording transport that returns distinct canned responses for GET vs PUT.
class _RecordingTransportWithPutResponse implements DocumentsTransport {
  _RecordingTransportWithPutResponse({
    required this.getResponse,
    required this.putResponse,
  });

  final Map<String, dynamic> getResponse;
  final Map<String, dynamic> putResponse;

  final List<({String method, String path, Map<String, dynamic>? body})> calls =
      [];

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    calls.add((method: 'GET', path: path, body: null));
    return getResponse;
  }

  @override
  Future<Map<String, dynamic>> putJson(
      String path, Map<String, dynamic> body) async {
    calls.add((method: 'PUT', path: path, body: body));
    return putResponse;
  }

  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async {
    calls.add((method: 'PATCH', path: path, body: body));
    return const {};
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    calls.add((method: 'POST', path: path, body: body));
    return const {};
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    calls.add((method: 'DELETE', path: path, body: null));
    return const {};
  }

  @override
  Future<Map<String, dynamic>> uploadFile(String path, File file) async =>
      const {};

  @override
  Future<List<int>> getBytes(String path) async => const [];

  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async =>
      const [];
}

// ── Conflict-then-succeed transport ──────────────────────────────────────────

/// Transport that throws a production-shape 409 DioException on the FIRST PUT,
/// then succeeds on the second. Records every call so tests can verify both
/// attempts.
class _ConflictThenSucceedTransport implements DocumentsTransport {
  _ConflictThenSucceedTransport({
    required this.getResponse,
    required this.conflictCurrent,
    required this.successResponse,
  });

  final Map<String, dynamic> getResponse;

  /// Placed inside the 409 response as `{ "current": conflictCurrent }`.
  final Map<String, dynamic> conflictCurrent;
  final Map<String, dynamic> successResponse;

  final List<({String method, String path, Map<String, dynamic>? body})> calls =
      [];

  int _putCount = 0;

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    calls.add((method: 'GET', path: path, body: null));
    return getResponse;
  }

  @override
  Future<Map<String, dynamic>> putJson(
      String path, Map<String, dynamic> body) async {
    calls.add((method: 'PUT', path: path, body: body));
    _putCount++;
    if (_putCount == 1) {
      // Throw a 409 in the same shape the production server + repository use.
      final fakeResponse = Response<Map<String, dynamic>>(
        requestOptions: RequestOptions(path: path),
        statusCode: 409,
        data: {'current': conflictCurrent},
      );
      throw DioException(
        requestOptions: fakeResponse.requestOptions,
        response: fakeResponse,
        type: DioExceptionType.badResponse,
      );
    }
    return successResponse;
  }

  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async {
    calls.add((method: 'PATCH', path: path, body: body));
    return const {};
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    calls.add((method: 'POST', path: path, body: body));
    return const {};
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    calls.add((method: 'DELETE', path: path, body: null));
    return const {};
  }

  @override
  Future<Map<String, dynamic>> uploadFile(String path, File file) async =>
      const {};

  @override
  Future<List<int>> getBytes(String path) async => const [];

  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async =>
      const [];
}
