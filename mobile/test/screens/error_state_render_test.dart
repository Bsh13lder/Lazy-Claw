// Widget tests for the offline-first screens' error + degraded-storage states.
//
// Goal: a real fetch error must surface an LzErrorState (with a Retry), NEVER an
// infinite loading skeleton — and a degraded local DB (in-memory fallback) must
// surface the degraded-storage banner.
//
// Design notes (mirrors home_screen_test):
// - The Notes screen is pumped in full. Its `notesProvider` is overridden with a
//   stub whose async loaders are no-ops, so the state we inject survives the
//   initState `load()` call instead of being clobbered by a cache read.
// - `reachabilityProvider` is overridden with a fake probe so we never touch the
//   connectivity_plus platform channel (no test impl → MissingPluginException).
// - All work runs inside tester.runAsync() and pumps a fixed frame sequence
//   (pumpAndSettle is avoided — LzSkeleton has an infinite shimmer animation).

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/note_dao.dart';
import 'package:lazyclaw_mobile/providers/notes_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/notes_repository.dart';
import 'package:lazyclaw_mobile/screens/notes_screen.dart';
import 'package:lazyclaw_mobile/sync/note_sync.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Fakes ──────────────────────────────────────────────────────────────────

/// Fake transport — never invoked (sync is stubbed) but required to construct
/// a [NotesRepository].
class _NopNotesTransport implements NotesTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
          {Map<String, dynamic>? queryParams}) async =>
      const {};
  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      const {};
  @override
  Future<Map<String, dynamic>> patchJson(
          String path, Map<String, dynamic> body) async =>
      const {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => const {};
}

/// Always-reachable fake probe (no platform channel, no real radios/HTTP).
class _ReachableProbe implements ConnectivityProbe {
  @override
  Stream<bool> get onChanged => const Stream.empty();
  @override
  Future<bool> hasLink() async => true;
  @override
  Future<bool> pingHost() async => true;
}

/// A [NotesNotifier] whose async loaders are no-ops, so the [initial] state we
/// inject survives initState's `load()` call and drives the render.
class _StubNotesNotifier extends NotesNotifier {
  _StubNotesNotifier(super.dao, super.sync, NotesState initial) {
    state = initial;
  }
  @override
  Future<void> load() async {}
  @override
  Future<void> refresh() async {}
  @override
  Future<void> search(String query) async {}
}

// ── Harness ──────────────────────────────────────────────────────────────────

void main() {
  setUpAll(sqfliteFfiInit);

  late Database db;
  setUp(() async {
    db = await databaseFactoryFfi.openDatabase(inMemoryDatabasePath);
  });
  tearDown(() async => db.close());

  _StubNotesNotifier mkNotifier(NotesState s) {
    final dao = NoteDao(db);
    final sync = NoteSync(dao, NotesRepository(_NopNotesTransport()));
    return _StubNotesNotifier(dao, sync, s);
  }

  Widget host(List<Override> overrides) => ProviderScope(
        overrides: [
          reachabilityProvider.overrideWithValue(Reachability(_ReachableProbe())),
          ...overrides,
        ],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: const NotesScreen(),
        ),
      );

  Future<void> pumpFrames(WidgetTester tester) async {
    await tester.pump();
    for (var i = 0; i < 4; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await tester.pump();
    }
  }

  testWidgets(
    'empty cache + error shows LzErrorState, not an infinite skeleton',
    (tester) async {
      await tester.runAsync(() async {
        await tester.pumpWidget(host([
          notesProvider.overrideWith(
            (ref) => mkNotifier(
              const NotesState(notes: [], isLoading: false, error: 'boom'),
            ),
          ),
        ]));
        await pumpFrames(tester);

        expect(find.byType(LzErrorState), findsOneWidget);
        expect(find.text('boom'), findsOneWidget);
        // The whole point: no skeleton is left spinning when something errored.
        expect(find.byType(LzSkeleton), findsNothing);
      });
    },
  );

  testWidgets(
    'degraded DB health renders the degraded-storage banner',
    (tester) async {
      await tester.runAsync(() async {
        await tester.pumpWidget(host([
          notesProvider.overrideWith(
            (ref) => mkNotifier(
              const NotesState(notes: [], isLoading: false),
            ),
          ),
          dbHealthProvider.overrideWith(
            (ref) => DbHealth.degraded(StateError('x')),
          ),
        ]));
        await pumpFrames(tester);

        expect(
          find.text(
            'Local storage unavailable — data may be incomplete.',
          ),
          findsOneWidget,
        );
      });
    },
  );
}
