// Widget tests for the Add Task sheet's LIVE project list: a project created
// FROM INSIDE the sheet (via "＋ New project" or the `/` strip's "Create
// project" row) must become visible to the sheet's own PROJECT picker and `/`
// suggestion strip immediately — the sheet was previously built against a
// construction-time `widget.projects` snapshot, so a project created mid-sheet
// stayed invisible to a re-opened picker (and a re-typed `/token`), inviting a
// duplicate create.
//
// Uses real (in-memory) sqflite so `BudgetsNotifier.createProject` actually
// lands a row — the provider-free/no-DB-override tests elsewhere in this
// suite never exercise the create path, only the read side. Follows the
// home_screen_test.dart pattern: an always-offline transport (no real network)
// and DB/pump work wrapped in tester.runAsync() (sqflite_ffi uses real
// isolates whose timers FakeAsync never advances).

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart'
    show appDatabaseProvider, reachabilityProvider;
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_sheet.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Always-offline transport (mirrors home_screen_test.dart) ─────────────────

class _OfflineBudgetsTransport implements BudgetsTransport {
  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

// ── No-op ConnectivityProbe (avoids the connectivity_plus platform channel,
// mirrors home_screen_test.dart) ──────────────────────────────────────────────

class _NopProbe implements ConnectivityProbe {
  @override
  Stream<bool> get onChanged => const Stream.empty();
  @override
  Future<bool> hasLink() async => false;
  @override
  Future<bool> pingHost() async => false;
}

int _dbSeq = 0;

Future<Database> _openMemDb() => databaseFactoryFfi.openDatabase(
  'file:add_task_live_projects_${_dbSeq++}?mode=memory&cache=shared',
  options: OpenDatabaseOptions(
    version: kAppDbVersion,
    singleInstance: false,
    onCreate: (db, _) => createAppDbSchema(db),
  ),
);

/// Settle after a DB-touching action (project create) — NOT `pumpAndSettle`.
/// `AddProjectSheet`'s indeterminate `CircularProgressIndicator` (shown while
/// `_loading`) never reaches a fixed point on its own, so `pumpAndSettle`
/// spins until it hits its own timeout regardless of how fast the underlying
/// write finishes. Worse, `sqflite_ffi`'s isolate round-trip needs a REAL
/// event-loop gap to deliver its response — a tight `pump()` loop with no
/// actual delay between iterations never gives it one. Mirrors
/// home_screen_test.dart's `_pumpHome` helper (call inside `runAsync`).
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 10; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 30));
    await tester.pump();
  }
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  String? capturedCategory;
  bool captured = false;

  Widget host(Database db) => ProviderScope(
    overrides: [
      appDatabaseProvider.overrideWithValue(db),
      budgetsRepositoryProvider.overrideWithValue(
        BudgetsRepository(_OfflineBudgetsTransport()),
      ),
      reachabilityProvider.overrideWithValue(Reachability(_NopProbe())),
    ],
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: Center(
          child: Builder(
            builder: (ctx) => ElevatedButton(
              onPressed: () async {
                final r = await showAddTaskSheet(ctx);
                captured = r != null;
                capturedCategory = r?.category;
              },
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );

  setUp(() {
    captured = false;
    capturedCategory = null;
  });

  testWidgets(
    'a project created via "＋ New project" appears (and is checked) in a '
    're-opened picker',
    (tester) async {
      await tester.runAsync(() async {
        final db = await _openMemDb();
        addTearDown(db.close);

        await tester.pumpWidget(host(db));
        await tester.tap(find.text('open'));
        await tester.pumpAndSettle();

        // A title is required for `_submit` to actually pop a result — this
        // test verifies the create-then-reopen-picker flow all the way
        // through to submit, not just the picker's contents.
        await tester.enterText(find.byType(TextField).first, 'buy paint');
        await tester.pump();

        // Open the PROJECT chip's picker and tap "＋ New project".
        await tester.tap(find.byKey(const Key('add-task-project')));
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const Key('project-pick-create')));
        await tester.pumpAndSettle();

        // Fill in the new project's name and submit. Located via its "Project
        // name" label rather than TextField ordinal position: the New Project
        // sheet stacks ON TOP of the still-mounted AddTaskSheet, so a plain
        // `find.byType(TextField)` also matches the (still-present) title
        // field and the New Project sheet's OWN second (Budget) field.
        final nameField = find.descendant(
          of: find
              .ancestor(
                of: find.text('Project name'),
                matching: find.byType(Column),
              )
              .first,
          matching: find.byType(TextField),
        );
        await tester.enterText(nameField, 'Renovation');
        await tester.tap(find.text('Create Project'));
        await _settle(tester);

        // The chip now shows "Renovation" as the (touched) category.
        expect(find.text('Renovation'), findsWidgets);

        // Re-open the picker: the just-created project must be LISTED (not
        // just reflected on the chip) and CHECKED — the old bug left the
        // picker showing only "No project" / "＋ New project" because it was
        // still reading the construction-time `widget.projects` snapshot.
        await tester.tap(find.byKey(const Key('add-task-project')));
        await tester.pumpAndSettle();

        final renovationRow = find.ancestor(
          of: find.text('Renovation'),
          matching: find.byType(LzListTile),
        );
        expect(
          renovationRow,
          findsOneWidget,
          reason:
              'the just-created project must be a selectable row in the '
              're-opened picker, not absent',
        );
        final tile = tester.widget<LzListTile>(renovationRow);
        expect(
          tile.trailing,
          isNotNull,
          reason:
              'the just-created project must show as the CURRENT '
              'selection (check icon), not unchecked',
        );

        // Close the picker by re-tapping the (already-selected) row, then
        // submit — the category still carries through end to end.
        await tester.tap(renovationRow);
        await tester.pumpAndSettle();
        // The submit affordance is the floating square, which is anchored to
        // the sheet's viewport — no ensureVisible() needed, and that it is
        // always hit-testable is exactly the point of it.
        await tester.tap(find.byKey(kAddTaskSubmitKey));
        // `_settle`, not `pumpAndSettle`: the project create earlier in this
        // test left a real sqflite round-trip (BudgetsNotifier's fire-and-
        // forget `_syncThenRefresh`) in flight, and `pumpAndSettle` gives the
        // ffi isolate no REAL event-loop gap to land it. Without this the
        // work resolves AFTER the ProviderScope is torn down and the notifier
        // throws "used after dispose" post-test.
        await _settle(tester);

        expect(captured, isTrue);
        expect(capturedCategory, 'Renovation');
      });
    },
  );

  testWidgets(
    'a project created via the `/` strip\'s "Create project" row no longer '
    'offers to create it again on a re-typed exact token',
    (tester) async {
      await tester.runAsync(() async {
        final db = await _openMemDb();
        addTearDown(db.close);

        await tester.pumpWidget(host(db));
        await tester.tap(find.text('open'));
        await tester.pumpAndSettle();

        await tester.enterText(find.byType(TextField).first, 'buy paint /Reno');
        await tester.pump();

        await tester.tap(find.byKey(const Key('project-suggest-create')));
        await _settle(tester);

        // Re-type the same token; now that "Reno" exists, the strip must
        // show it as a MATCH row (not just a stale "Create project 'Reno'"
        // row) — the old bug kept offering to create it again because the
        // strip read the construction-time snapshot, not the freshly-created
        // project.
        await tester.enterText(find.byType(TextField).first, 'buy paint /Reno');
        await tester.pump();

        expect(
          find.byKey(const ValueKey('project-suggest-Reno')),
          findsOneWidget,
          reason: 'the just-created project must show as a live match row',
        );
        expect(
          find.byKey(const Key('project-suggest-create')),
          findsNothing,
          reason:
              'an exact match must hide the create row — otherwise the '
              'user can spawn a duplicate project with the same name',
        );
      });
    },
  );
}
