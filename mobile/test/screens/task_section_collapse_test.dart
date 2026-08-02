// Widget tests for the List view's per-section collapse: every section
// (Overdue/Today/Upcoming/Done) gets a chevron (not just Done), tapping one
// toggles its body and reports the new state via onCollapsedChanged, and the
// screen's default wiring (Done starts collapsed, everything else expanded)
// is exercised directly through TaskSection's initialCollapsed contract — no
// full-screen pump, no database, no riverpod overrides. Mirrors
// tasks_project_view_prefs_test.dart's plain-callback harness.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks_screen.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Task _task(String id, String title) => Task(
  id: id,
  userId: 'u1',
  title: title,
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

/// Builds a [TaskSection] with every quick-edit callback stubbed as a no-op —
/// this suite only exercises collapse/expand, never those affordances.
Widget _section({
  required Section section,
  required List<Task> tasks,
  required bool initialCollapsed,
  ValueChanged<bool>? onCollapsedChanged,
}) => TaskSection(
  section: section,
  tasks: tasks,
  dirtyIds: const {},
  projects: const <Project>[],
  initialCollapsed: initialCollapsed,
  onCollapsedChanged: onCollapsedChanged,
  onComplete: (_) {},
  onDelete: (_) {},
  onOpen: (_) {},
  onRenameTitle: (_, _) {},
  onPriorityChanged: (_, _) {},
  onDueDateChanged: (_, _) {},
  onCategoryChanged: (_, _) {},
  onSubtasksChanged: (_, _) {},
  onReschedule: (_) {},
);

Finder _chevronFinder() => find.byWidgetPredicate(
  (w) =>
      w is Icon &&
      (w.icon == Icons.keyboard_arrow_down ||
          w.icon == Icons.keyboard_arrow_up),
);

Widget _host(Widget child) => MaterialApp(
  theme: buildAppTheme(),
  home: Scaffold(body: child),
);

void main() {
  group('TaskSection chevron', () {
    testWidgets('renders for every section, not just Done', (tester) async {
      await tester.pumpWidget(
        _host(
          Column(
            children: [
              for (final section in Section.values)
                _section(
                  section: section,
                  tasks: [
                    _task('${section.name}-1', 'Task in ${section.name}'),
                  ],
                  initialCollapsed: defaultSectionCollapsed(section),
                ),
            ],
          ),
        ),
      );
      // pumpAndSettle (not a single pump): the expanded sections render row
      // entrance animations (flutter_animate staggered fade+slide), whose
      // timers must finish before the test ends or the framework flags a
      // pending timer at teardown.
      await tester.pumpAndSettle();

      expect(_chevronFinder(), findsNWidgets(4));
    });
  });

  group('TaskSection toggle', () {
    testWidgets("tapping Today's chevron hides its rows and fires "
        'onCollapsedChanged(true)', (tester) async {
      bool? reported;
      await tester.pumpWidget(
        _host(
          _section(
            section: Section.today,
            tasks: [_task('t1', 'Water the plants')],
            initialCollapsed: false,
            onCollapsedChanged: (v) => reported = v,
          ),
        ),
      );
      await tester.pump();

      // Expanded by default (initialCollapsed: false) — the row is visible.
      expect(find.text('Water the plants'), findsOneWidget);

      await tester.tap(_chevronFinder());
      await tester.pumpAndSettle();

      expect(find.text('Water the plants'), findsNothing);
      expect(reported, isTrue);
    });
  });

  group('TaskSection default collapse wiring', () {
    test('defaultSectionCollapsed: Done is collapsed, others are not', () {
      expect(defaultSectionCollapsed(Section.overdue), isFalse);
      expect(defaultSectionCollapsed(Section.today), isFalse);
      expect(defaultSectionCollapsed(Section.upcoming), isFalse);
      expect(defaultSectionCollapsed(Section.done), isTrue);
    });

    testWidgets(
      'Done starts collapsed when seeded from defaultSectionCollapsed, '
      'with no tap',
      (tester) async {
        await tester.pumpWidget(
          _host(
            _section(
              section: Section.done,
              tasks: [_task('d1', 'Filed taxes')],
              initialCollapsed: defaultSectionCollapsed(Section.done),
            ),
          ),
        );
        await tester.pump();

        expect(find.text('Filed taxes'), findsNothing);
      },
    );
  });

  group('TaskSection empty-section rendering (regression)', () {
    testWidgets(
      'empty Overdue renders nothing at all (no header, no chevron)',
      (tester) async {
        await tester.pumpWidget(
          _host(
            _section(
              section: Section.overdue,
              tasks: const [],
              initialCollapsed: defaultSectionCollapsed(Section.overdue),
            ),
          ),
        );
        await tester.pump();

        expect(find.text('OVERDUE'), findsNothing);
        expect(_chevronFinder(), findsNothing);
      },
    );

    testWidgets('empty Today still renders its header, chevron, and empty '
        'state', (tester) async {
      await tester.pumpWidget(
        _host(
          _section(
            section: Section.today,
            tasks: const [],
            initialCollapsed: defaultSectionCollapsed(Section.today),
          ),
        ),
      );
      await tester.pump();

      expect(find.text('TODAY'), findsOneWidget);
      expect(_chevronFinder(), findsOneWidget);
      expect(find.text('Nothing due today'), findsOneWidget);
    });
  });
}
