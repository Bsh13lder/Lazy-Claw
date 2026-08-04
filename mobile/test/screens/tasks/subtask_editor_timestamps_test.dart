// Each sub-task row shows when it was created and, once ticked, when it was
// finished — as a quiet secondary line UNDER the title, not as a fourth chip
// competing with the money sign and the 💬 badge on an already dense row.
//
// Kept out of subtask_editor_test.dart (which owns the money-chip contract)
// so the two concerns stay separately runnable.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/screens/tasks/subtask_editor.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// Pinned so "3h ago" is a fact about the data, not about when CI ran.
///
/// Frozen through the SAME `nowIso` hook the editor stamps with — one clock,
/// so a test can't end up stamping 2026 and rendering against the real wall
/// clock.
final _now = DateTime.utc(2026, 8, 4, 12);

Widget _host(List<Subtask> subtasks) => MaterialApp(
  theme: buildAppTheme(),
  home: Scaffold(
    body: SubtaskEditor(
      subtasks: subtasks,
      onChanged: (_) {},
      nowIso: _now.toIso8601String,
    ),
  ),
);

void main() {
  testWidgets('a sub-task with a creation time shows it', (tester) async {
    await tester.pumpWidget(
      _host(const [
        Subtask(
          id: 's1',
          title: 'Buy flour',
          done: false,
          createdAt: '2026-08-04T09:00:00Z',
        ),
      ]),
    );
    await tester.pump();

    expect(find.byKey(const Key('subtask-s1-created')), findsOneWidget);
    expect(find.text('Created 3h ago'), findsOneWidget);
    expect(find.byKey(const Key('subtask-s1-completed')), findsNothing);
  });

  testWidgets('a ticked sub-task shows both times', (tester) async {
    await tester.pumpWidget(
      _host(const [
        Subtask(
          id: 's1',
          title: 'Buy flour',
          done: true,
          createdAt: '2026-08-04T09:00:00Z',
          completedAt: '2026-08-04T11:55:00Z',
        ),
      ]),
    );
    await tester.pump();

    expect(find.text('Created 3h ago'), findsOneWidget);
    expect(find.text('· Done 5m ago'), findsOneWidget);
  });

  testWidgets('a LEGACY sub-task with no timestamps renders nothing extra', (
    tester,
  ) async {
    // The permanent case: rows created before the fields existed have neither
    // value and nothing backfills them. They must look untouched, not broken.
    await tester.pumpWidget(
      _host(const [Subtask(id: 's1', title: 'Buy flour', done: false)]),
    );
    await tester.pump();

    expect(find.byKey(const Key('subtask-s1-created')), findsNothing);
    expect(find.byKey(const Key('subtask-s1-completed')), findsNothing);
    expect(find.textContaining('null'), findsNothing);
    expect(find.textContaining('1970'), findsNothing);
    expect(find.textContaining('—'), findsNothing);
    // The row itself still renders normally.
    expect(find.text('Buy flour'), findsOneWidget);
  });

  testWidgets('a legacy row ticked TODAY shows only the completion time', (
    tester,
  ) async {
    // `_toggle` deliberately does not backfill `created_at` — we never
    // observed it, and guessing "now" would date every old checklist item to
    // the day it happened to be ticked.
    await tester.pumpWidget(
      _host(const [
        Subtask(
          id: 's1',
          title: 'Buy flour',
          done: true,
          completedAt: '2026-08-04T11:55:00Z',
        ),
      ]),
    );
    await tester.pump();

    expect(find.text('Done 5m ago'), findsOneWidget);
    expect(find.byKey(const Key('subtask-s1-created')), findsNothing);
  });

  testWidgets('two ABSOLUTE dates fit a narrow phone row without overflowing', (
    tester,
  ) async {
    // The worst case for a row that already carries a checkbox, a title, a
    // money sign and a 💬 badge: both labels fall back to full dates. A
    // RenderFlex overflow here throws and fails this test — which is why the
    // line is a Wrap rather than a Row.
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(320, 640);
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      _host(const [
        Subtask(
          id: 's1',
          title: 'Reconcile the December supplier invoices',
          done: true,
          createdAt: '2025-12-04T09:00:00Z',
          completedAt: '2026-01-08T17:30:00Z',
        ),
      ]),
    );
    await tester.pump();

    expect(find.byKey(const Key('subtask-s1-created')), findsOneWidget);
    expect(find.byKey(const Key('subtask-s1-completed')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('tap-to-edit still claims the FULL row width, not just the '
      'width of the title text', (tester) async {
    // Stacking the timestamp line under the title turned the title into a
    // Column child. A Column that sizes its children to their intrinsic width
    // would silently shrink this GestureDetector from "the whole row" to
    // "the ~55px the words occupy" — tapping the empty space beside a short
    // sub-task would stop opening the editor, with nothing failing.
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(800, 1600);
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      _host(const [Subtask(id: 's1', title: 'Buy flour', done: false)]),
    );
    await tester.pump();

    final width = tester
        .getSize(find.byKey(const ValueKey('subtask-text-s1')))
        .width;
    expect(width, greaterThan(400));
  });

  testWidgets('the line survives entering inline edit mode', (tester) async {
    // Hiding it on tap-to-edit would make the whole checklist jump vertically
    // every time a title is corrected.
    await tester.pumpWidget(
      _host(const [
        Subtask(
          id: 's1',
          title: 'Buy flour',
          done: false,
          createdAt: '2026-08-04T09:00:00Z',
        ),
      ]),
    );
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('subtask-text-s1')));
    await tester.pump();

    expect(find.byKey(const ValueKey('subtask-edit-s1')), findsOneWidget);
    expect(find.text('Created 3h ago'), findsOneWidget);
  });
}
