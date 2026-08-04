// REGRESSION GUARD: the CREATE sheets must never auto-save.
//
// Auto-save was added to the EDIT sheets (task detail, expense detail, project
// edit), where a persisted record already exists and a write is a patch. The
// create sheets are the opposite case: there is no record yet, so an auto-save
// would not "not lose your work" — it would spray half-typed rows into the
// encrypted cache and the sync outbox, one per pause in typing, each of them a
// real task/expense/project the user never asked to create.
//
// Every test here types into a create sheet, waits out MORE than the auto-save
// debounce, and asserts nothing was submitted. They exist so a future pass that
// "makes auto-save consistent everywhere" fails loudly instead of shipping.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/autosave.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/screens/expenses/add_expense_sheet.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// Comfortably past the debounce, several times over — if a create sheet ever
/// grows a debounced writer, this window catches it.
final _wellPastDebounce = kAutosaveDebounce * 4;

Project _project(String id, String name) => Project(
  id: id,
  name: name,
  budget: 0,
  currency: 'USD',
  status: 'active',
);

void main() {
  group('Add Expense', () {
    testWidgets('typing every field submits NOTHING until the button is '
        'pressed', (tester) async {
      await tester.binding.setSurfaceSize(const Size(600, 1400));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      final submitted = <List<Object?>>[];
      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: buildAppTheme(),
            home: Scaffold(
              body: AddExpenseSheet(
                projects: [_project('p1', 'Marketing')],
                initialProjectId: 'p1',
                onSubmit: (projectId, amount, description, vendor) async {
                  submitted.add([projectId, amount, description, vendor]);
                  return true;
                },
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(
        find.byKey(const Key('expense-description-field')),
        'Half a thought',
      );
      await tester.pump(_wellPastDebounce);
      await tester.enterText(
        find.byKey(const Key('expense-amount-field')),
        '25',
      );
      await tester.pump(_wellPastDebounce);
      await tester.pumpAndSettle();

      expect(submitted, isEmpty,
          reason: 'a create sheet that writes on a pause invents records');

      // ...and the explicit submit still works.
      await tester.tap(find.byKey(const Key('expense-submit-fab')));
      await tester.pumpAndSettle();
      expect(submitted, hasLength(1));
      expect(submitted.single[2], 'Half a thought');
    });
  });

  group('Add Task', () {
    testWidgets('typing a title submits NOTHING and leaves the sheet open',
        (tester) async {
      await tester.binding.setSurfaceSize(const Size(600, 1400));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      var resultCount = 0;
      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: buildAppTheme(),
            home: Scaffold(
              body: Center(
                child: Builder(
                  builder: (ctx) => ElevatedButton(
                    onPressed: () async {
                      final r = await showAddTaskSheet(ctx);
                      if (r != null) resultCount++;
                    },
                    child: const Text('open'),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField).first, 'Half a task');
      await tester.pump(_wellPastDebounce);
      await tester.pumpAndSettle();

      expect(resultCount, 0, reason: 'the sheet must not resolve on its own');
      expect(find.byType(TextField), findsWidgets,
          reason: 'the sheet is still open, waiting for an explicit submit');

      // ...and the explicit submit still works.
      await tester.tap(find.byKey(kAddTaskSubmitKey));
      await tester.pumpAndSettle();
      expect(resultCount, 1);
    });
  });

  group('Add Project', () {
    testWidgets('typing a name submits NOTHING until Create is pressed',
        (tester) async {
      await tester.binding.setSurfaceSize(const Size(600, 1400));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      final submitted = <String>[];
      await tester.pumpWidget(
        MaterialApp(
          theme: buildAppTheme(),
          home: Scaffold(
            body: SingleChildScrollView(
              child: AddProjectSheet(
                onSubmit: (name, budget, color, startDate, dueDate) async {
                  submitted.add(name);
                  return true;
                },
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField).first, 'Half a project');
      await tester.pump(_wellPastDebounce);
      await tester.pumpAndSettle();

      expect(submitted, isEmpty);
    });
  });
}
