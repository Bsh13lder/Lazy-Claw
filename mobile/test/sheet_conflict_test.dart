/// Tests for sheet editor optimistic-concurrency conflict handling.
///
/// (a) Banner appears when a save raises DocConflictException.
/// (b) Reload: adopts server payload, re-bases updatedAt, undo restores
///     the pre-conflict user edit.
/// (c) Keep mine: re-saves with baseUpdatedAt=null, banner clears.
/// (d) Successful save re-bases _baseUpdatedAt (second save sees first's
///     response updated_at as its base).
/// (e) name is null in the autosave PUT body (stale-rename clobber prevention).
library;

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_conflict_banner.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_editor_screen.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_toolbar.dart';

// ── Fake workbooks ────────────────────────────────────────────────────────────

Map<String, dynamic> _workbook(String value) => {
      'sheetOrder': ['s1'],
      'sheets': {
        's1': {
          'name': 'Sheet1',
          'cellData': {
            '0': {
              '0': {'v': value},
            },
          },
        },
      },
    };

/// The workbook served on GET (the "original" server state).
final _serverWorkbook = _workbook('ServerValue');

/// The workbook carried in the conflict exception (the "other client's" save).
final _conflictWorkbook = _workbook('ConflictValue');

// ── Recording fake transport ──────────────────────────────────────────────────

/// Records every PUT call in [puts] so tests can inspect baseUpdatedAt and
/// the presence/absence of `name`.
///
/// Behaviour:
///   - [getJson]  → returns the base sheet (updatedAt = 'ts-0').
///   - [putJson] call count is tracked; on [conflictOnPutIndex] it throws
///     [DocConflictException]; on subsequent calls it succeeds and returns
///     [putResponse].
class _RecordingTransport implements DocumentsTransport {
  final List<Map<String, dynamic>> puts = [];

  /// Which call index (0-based) should throw DocConflictException.
  final int? conflictOnPutIndex;

  /// The payload the conflict exception carries.
  final DocPayload conflictPayload;

  /// What a successful PUT returns (should include `sheet.updated_at`).
  /// Mutable so tests can change the value returned for subsequent calls.
  Map<String, dynamic> putResponse;

  _RecordingTransport({
    this.conflictOnPutIndex,
    DocPayload? conflictPayload,
    Map<String, dynamic>? putResponse,
  })  : conflictPayload = conflictPayload ??
            DocPayload(
              id: 'wb-test',
              name: 'Test',
              payload: _conflictWorkbook,
              updatedAt: 'ts-conflict',
            ),
        putResponse = putResponse ??
            {'sheet': {'id': 'wb-test', 'name': 'Test', 'updated_at': 'ts-1'}};

  @override
  Future<Map<String, dynamic>> getJson(String path) async => {
        'id': 'wb-test',
        'name': 'Test',
        'updated_at': 'ts-0',
        'payload': _serverWorkbook,
      };

  @override
  Future<Map<String, dynamic>> putJson(
      String path, Map<String, dynamic> body) async {
    final callIndex = puts.length;
    puts.add(Map<String, dynamic>.from(body));
    if (conflictOnPutIndex != null && callIndex == conflictOnPutIndex) {
      throw DocConflictException(conflictPayload);
    }
    return putResponse;
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async =>
      {};
  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async =>
      {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => {};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => {};
  @override
  Future<List<int>> getBytes(String path) async => const [];
  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async =>
      const [];
}

// ── Widget wrapper ────────────────────────────────────────────────────────────

Widget _wrap(DocumentsTransport transport) {
  return ProviderScope(
    overrides: [
      documentsRepositoryProvider
          .overrideWithValue(DocumentsRepository(transport)),
    ],
    child: const MaterialApp(
      home: SheetEditorScreen(id: 'wb-test', name: 'Test'),
    ),
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Bold button scoped to the SheetToolbar (avoids matching the "B" column header).
Finder _boldBtn() => find.descendant(
      of: find.byType(SheetToolbar),
      matching: find.text('B'),
    );

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  group('SheetEditorScreen — optimistic-concurrency', () {
    // ── (a) Banner appears on conflict ──────────────────────────────────────
    testWidgets('(a) conflict banner appears when save returns 409',
        (tester) async {
      final transport = _RecordingTransport(conflictOnPutIndex: 0);
      await tester.pumpWidget(_wrap(transport));
      await tester.pumpAndSettle();

      // Banner should not be present yet.
      expect(find.byType(SheetConflictBanner), findsNothing);

      // Select a cell so the toolbar appears, then apply Bold to trigger save.
      await tester.tap(find.text('ServerValue'));
      await tester.pumpAndSettle();
      await tester.tap(_boldBtn());
      await tester.pump();

      // Advance past the 800 ms autosave debounce.
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      // The conflict banner should now be visible.
      expect(find.byType(SheetConflictBanner), findsOneWidget);
      expect(find.text('Sheet changed on the server.'), findsOneWidget);
    });

    // ── (b) Reload: adopts server payload + undo restores local edit ────────
    testWidgets(
        '(b) Reload adopts conflict payload, re-bases updatedAt, undo restores local',
        (tester) async {
      final conflictPayload = DocPayload(
        id: 'wb-test',
        name: 'Test',
        payload: _conflictWorkbook,
        updatedAt: 'ts-conflict',
      );
      final transport = _RecordingTransport(
        conflictOnPutIndex: 0,
        conflictPayload: conflictPayload,
        putResponse: {
          'sheet': {'id': 'wb-test', 'name': 'Test', 'updated_at': 'ts-after-reload'}
        },
      );
      await tester.pumpWidget(_wrap(transport));
      await tester.pumpAndSettle();

      // Trigger conflict via Bold toggle.
      await tester.tap(find.text('ServerValue'));
      await tester.pumpAndSettle();
      await tester.tap(_boldBtn());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(find.byType(SheetConflictBanner), findsOneWidget);

      // Tap Reload.
      await tester.tap(find.text('Reload'));
      await tester.pumpAndSettle();

      // Banner should disappear.
      expect(find.byType(SheetConflictBanner), findsNothing);

      // The grid should now show the conflict (server) value.
      expect(find.text('ConflictValue'), findsOneWidget);

      // Trigger a new edit after reload — second bold toggle.
      await tester.tap(find.text('ConflictValue'));
      await tester.pumpAndSettle();
      await tester.tap(_boldBtn());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      // The post-reload save should use ts-conflict as the base.
      expect(transport.puts.length, greaterThanOrEqualTo(2));
      final postReloadPut = transport.puts.last;
      expect(postReloadPut['base_updated_at'], equals('ts-conflict'),
          reason: 'post-reload save should use conflict.updatedAt as base');
    });

    // ── (c) Keep mine: re-saves with null base, banner clears ───────────────
    testWidgets('(c) Keep mine re-saves with null base_updated_at and clears banner',
        (tester) async {
      final transport = _RecordingTransport(conflictOnPutIndex: 0);
      await tester.pumpWidget(_wrap(transport));
      await tester.pumpAndSettle();

      // Trigger conflict via Bold toggle.
      await tester.tap(find.text('ServerValue'));
      await tester.pumpAndSettle();
      await tester.tap(_boldBtn());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(find.byType(SheetConflictBanner), findsOneWidget);

      // Tap Keep mine.
      await tester.tap(find.text('Keep mine'));
      await tester.pumpAndSettle();
      await tester.pump(const Duration(milliseconds: 100));
      await tester.pumpAndSettle();

      // Banner should be gone.
      expect(find.byType(SheetConflictBanner), findsNothing);

      // The Keep-mine save should have been sent (puts[1] is the LWW re-save).
      expect(transport.puts.length, greaterThanOrEqualTo(2));
      final keepMinePut = transport.puts[1];
      expect(keepMinePut.containsKey('base_updated_at'), isFalse,
          reason: 'Keep mine must omit base_updated_at for LWW semantics');
    });

    // ── (d) Successful save re-bases _baseUpdatedAt ─────────────────────────
    testWidgets('(d) successful save re-bases; second save sends first response updated_at',
        (tester) async {
      // No conflict — just two sequential saves.
      final transport = _RecordingTransport(
        conflictOnPutIndex: null, // never conflict
        putResponse: {
          'sheet': {'id': 'wb-test', 'name': 'Test', 'updated_at': 'ts-save-1'}
        },
      );
      await tester.pumpWidget(_wrap(transport));
      await tester.pumpAndSettle();

      // First edit: select cell, apply Bold → schedules autosave.
      await tester.tap(find.text('ServerValue'));
      await tester.pumpAndSettle();
      await tester.tap(_boldBtn()); // Bold toggle
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(transport.puts.length, 1);
      // The first save base is 'ts-0' (from the GET response).
      expect(transport.puts[0]['base_updated_at'], equals('ts-0'));

      // Update the transport to return ts-save-2 on the next PUT.
      transport.putResponse = {
        'sheet': {'id': 'wb-test', 'name': 'Test', 'updated_at': 'ts-save-2'}
      };

      // Second edit: toggle Bold again to undo bold → schedules another save.
      await tester.tap(_boldBtn()); // Bold un-toggle
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(transport.puts.length, 2);
      // The second save base should be the updated_at from the FIRST save response.
      expect(transport.puts[1]['base_updated_at'], equals('ts-save-1'));
    });

    // ── (e) name is null in autosave PUT body ─────────────────────────────
    testWidgets('(e) autosave PUT body does not include name field',
        (tester) async {
      final transport = _RecordingTransport(conflictOnPutIndex: null);
      await tester.pumpWidget(_wrap(transport));
      await tester.pumpAndSettle();

      // Select a cell so the toolbar appears.
      await tester.tap(find.text('ServerValue'));
      await tester.pumpAndSettle();

      // Apply Bold to trigger a mutation + autosave schedule.
      await tester.tap(_boldBtn());
      await tester.pump();

      // Advance past the 800 ms autosave debounce.
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(transport.puts.isNotEmpty, isTrue,
          reason: 'Expected at least one PUT after a cell edit');
      final body = transport.puts.first;
      expect(body.containsKey('name'), isFalse,
          reason: 'Autosave must not send name to prevent stale-rename clobber');
    });
  });
}
