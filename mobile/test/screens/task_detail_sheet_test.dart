// Widget tests for the tap-a-task detail/edit sheet.
//
// The sheet is opened via the public showTaskDetailSheet helper (so the modal
// route + pop behaviour is exercised end-to-end) with tasksProvider overridden
// by a stub notifier that records updateTask / deleteTask invocations. The
// stub's TaskDao is backed by a noSuchMethod fake Database so no real sqflite
// isolate is spun up (its timer would hang pumpAndSettle under FakeAsync); the
// overridden methods never touch the DAO anyway.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_budget_control.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_detail_sheet.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_tags_field.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';
import 'package:sqflite_common/sqlite_api.dart';

class _OfflineTransport implements TasksTransport {
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
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

class _NoopSync extends TaskSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<SyncResult> sync({bool retryRejected = false}) async =>
      const SyncResult();
}

// ── budgetsProvider stub ─────────────────────────────────────────────────────
//
// TaskDetailSheet now reads budgetsProvider (to compute the sub-task money
// chip's per-sub-task expense totals — see task_detail_subtasks_test.dart for
// the dedicated coverage of that). The real budgetsProvider throws unless
// appDatabaseProvider is overridden with a live DB, so every ProviderScope in
// this file needs a stub too, even though none of these tests exercise money.

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

class _NoopBudgetsSync extends BudgetsSync {
  _NoopBudgetsSync(super.dao, super.repo);
  @override
  Future<BudgetsSyncResult> sync({bool retryRejected = false}) async =>
      const BudgetsSyncResult();
}

class _StubBudgetsNotifier extends BudgetsNotifier {
  _StubBudgetsNotifier(super.dao, super.sync);
  @override
  Future<void> load() async {}
  @override
  Future<void> refresh() async {}
  @override
  Future<void> syncNow() async {}
}

_StubBudgetsNotifier _stubBudgets() {
  final dao = BudgetsDao(_FakeDatabase());
  return _StubBudgetsNotifier(
    dao,
    _NoopBudgetsSync(dao, BudgetsRepository(_OfflineBudgetsTransport())),
  );
}

/// Records the editor's writes without touching the DAO/network.
class _StubTasksNotifier extends TasksNotifier {
  _StubTasksNotifier(super.dao, super.sync);

  final List<Map<String, dynamic>> updateCalls = [];
  final List<String> deleteCalls = [];

  @override
  Future<void> updateTask(
    String id, {
    String? title,
    String? description,
    String? priority,
    String? dueDate,
    String? category,
    String? steps,
    String? reminderAt,
    String? recurring,
    String? recurUntil,
    String? tags,
    double? allocatedBudget,
    bool clearAllocatedBudget = false,
  }) async {
    updateCalls.add({
      'id': id,
      'title': title,
      'description': description,
      'priority': priority,
      'dueDate': dueDate,
      'category': category,
      'steps': steps,
      'reminderAt': reminderAt,
      'recurring': recurring,
      'recurUntil': recurUntil,
      'tags': tags,
      'allocatedBudget': allocatedBudget,
      'clearAllocatedBudget': clearAllocatedBudget,
    });
  }

  @override
  Future<void> deleteTask(String id) async => deleteCalls.add(id);
}

/// A Database that throws on any access. The stub notifier overrides every
/// method that would touch the DAO, so this is never actually invoked — it only
/// satisfies TaskDao's non-null constructor arg WITHOUT spinning up a real
/// sqflite isolate (whose timer would hang pumpAndSettle under FakeAsync).
class _FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
}

_StubTasksNotifier _stub() {
  final dao = TaskDao(_FakeDatabase());
  return _StubTasksNotifier(
    dao,
    _NoopSync(dao, TasksRepository(_OfflineTransport())),
  );
}

const _sample = Task(
  id: 'task-42',
  userId: 'u1',
  title: 'Original title',
  description: 'Original notes',
  priority: 'high',
  status: 'todo',
  owner: 'user',
  dueDate: '2026-06-10',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

/// A task whose notes contain a bare URL, for the read-only preview tests.
const _withLinkNote = Task(
  id: 'task-link',
  userId: 'u1',
  title: 'Read the doc',
  description: 'see https://a.io',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

void main() {
  Widget host(_StubTasksNotifier stub, {List<Project> projects = const []}) =>
      ProviderScope(
        overrides: [
          tasksProvider.overrideWith((ref) => stub),
          budgetsProvider.overrideWith((ref) => _stubBudgets()),
        ],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: Consumer(
            builder: (ctx, ref, _) => Scaffold(
              body: Center(
                child: ElevatedButton(
                  onPressed: () => showTaskDetailSheet(
                    ctx,
                    ref,
                    _sample,
                    projects: projects,
                  ),
                  child: const Text('open'),
                ),
              ),
            ),
          ),
        ),
      );

  Future<void> openSheet(
    WidgetTester tester,
    _StubTasksNotifier stub, {
    List<Project> projects = const [],
  }) async {
    // The detail sheet (title + notes + priority + project + due + time +
    // subtasks + footer) is taller than the 800×600 default, so give it a
    // roomier surface to keep the footer buttons on-screen and tappable.
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(800, 2200);
    addTearDown(tester.view.reset);

    await tester.pumpWidget(host(stub, projects: projects));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  Project project(String id, String name, {String? color}) => Project(
    id: id,
    name: name,
    budget: 0,
    currency: 'USD',
    status: 'active',
    color: color,
  );

  testWidgets('pre-fills the title and notes from the task', (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    final titleField = tester.widget<TextField>(
      find.byKey(const Key('task-detail-title')),
    );
    expect(titleField.controller!.text, 'Original title');

    // Non-empty notes render as a read-only preview by default; tap it to
    // reveal the editable field and confirm the prefill survived the switch.
    await tester.tap(find.byKey(const Key('task-detail-notes-preview')));
    await tester.pumpAndSettle();

    final notesField = tester.widget<TextField>(
      find.byKey(const Key('task-detail-notes')),
    );
    expect(notesField.controller!.text, 'Original notes');
  });

  testWidgets('empty notes open straight in the editor (no preview)', (
    tester,
  ) async {
    const noNotes = Task(
      id: 'task-empty-notes',
      userId: 'u1',
      title: 'No notes yet',
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );
    final stub = _stub();
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(800, 2200);
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          tasksProvider.overrideWith((ref) => stub),
          budgetsProvider.overrideWith((ref) => _stubBudgets()),
        ],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: Consumer(
            builder: (ctx, ref, _) => Scaffold(
              body: Center(
                child: ElevatedButton(
                  onPressed: () => showTaskDetailSheet(ctx, ref, noNotes),
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

    expect(find.byKey(const Key('task-detail-notes')), findsOneWidget);
    expect(find.byKey(const Key('task-detail-notes-preview')), findsNothing);
  });

  testWidgets(
    'notes containing a link render as a read-only LinkText preview',
    (tester) async {
      final stub = _stub();
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = const Size(800, 2200);
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            tasksProvider.overrideWith((ref) => stub),
            budgetsProvider.overrideWith((ref) => _stubBudgets()),
          ],
          child: MaterialApp(
            theme: buildAppTheme(),
            home: Consumer(
              builder: (ctx, ref, _) => Scaffold(
                body: Center(
                  child: ElevatedButton(
                    onPressed: () =>
                        showTaskDetailSheet(ctx, ref, _withLinkNote),
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

      expect(find.byType(LinkText), findsOneWidget);
      expect(find.byKey(const Key('task-detail-notes')), findsNothing);

      // Tap the preview: it swaps in the editable TextField with the text
      // preserved.
      await tester.tap(find.byKey(const Key('task-detail-notes-preview')));
      await tester.pumpAndSettle();

      final notesField = tester.widget<TextField>(
        find.byKey(const Key('task-detail-notes')),
      );
      expect(notesField.controller!.text, 'see https://a.io');
      expect(find.byType(LinkText), findsNothing);
    },
  );

  testWidgets(
    'the Add-link button inserts the dialog result and Save persists it',
    (tester) async {
      final stub = _stub();
      // Empty notes open straight in the editor, so the "Add link" button is
      // visible without first tapping a preview — and the controller's
      // selection is still the default collapsed-at--1, exercising the
      // append-when-invalid-selection fallback.
      const noNotes = Task(
        id: 'task-add-link',
        userId: 'u1',
        title: 'Needs a link',
        priority: 'medium',
        status: 'todo',
        owner: 'user',
        nagCount: 0,
        createdAt: '2026-06-06T00:00:00Z',
      );
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = const Size(800, 2200);
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            tasksProvider.overrideWith((ref) => stub),
            budgetsProvider.overrideWith((ref) => _stubBudgets()),
          ],
          child: MaterialApp(
            theme: buildAppTheme(),
            home: Consumer(
              builder: (ctx, ref, _) => Scaffold(
                body: Center(
                  child: ElevatedButton(
                    onPressed: () => showTaskDetailSheet(ctx, ref, noNotes),
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

      await tester.tap(find.byKey(const Key('task-detail-notes-add-link')));
      await tester.pumpAndSettle();

      await tester.enterText(find.byKey(const Key('add-link-text')), 'docs');
      await tester.enterText(
        find.byKey(const Key('add-link-url')),
        'https://a.io',
      );
      await tester.tap(find.byKey(const Key('add-link-insert')));
      await tester.pumpAndSettle();

      final notesField = tester.widget<TextField>(
        find.byKey(const Key('task-detail-notes')),
      );
      expect(notesField.controller!.text, '[docs](https://a.io)');

      await tester.ensureVisible(find.byKey(const Key('task-detail-save')));
      await tester.tap(find.byKey(const Key('task-detail-save')));
      await tester.pumpAndSettle();

      expect(stub.updateCalls.single['description'], '[docs](https://a.io)');
    },
  );

  testWidgets('editing the title + tapping Save invokes updateTask', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.enterText(
      find.byKey(const Key('task-detail-title')),
      'Edited title',
    );
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();

    expect(stub.updateCalls, hasLength(1));
    expect(stub.updateCalls.single['id'], 'task-42');
    expect(stub.updateCalls.single['title'], 'Edited title');
    // The sheet closed after saving.
    expect(find.byKey(const Key('task-detail-title')), findsNothing);
  });

  // Both controls moved behind an affordance in the 2026-08-03 pass: TAGS
  // into a popup (the sheet was dominated by an always-on field), and the
  // allocated-budget field behind the allocated FIGURE itself (tap it to edit
  // it in place — see task_budget_control.dart). The SAVE semantics they
  // exercise are unchanged, so these tests only gained the step that opens
  // each surface.

  testWidgets('adding a tag + budget then Save passes them to updateTask', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    // Open the tags popup, then type a tag and submit it.
    await tester.tap(find.byKey(kTaskTagsChipKey));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('task-detail-tag-input')),
      'work',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('task-detail-tag-work')), findsOneWidget);
    await tester.tap(find.byKey(kTaskTagsDoneKey));
    await tester.pumpAndSettle();

    // Tap the allocated figure to edit it in place, then fill it.
    await tester.tap(find.byKey(kTaskBudgetSummaryTapKey));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(kTaskBudgetFieldKey), '250');

    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();

    // Auto-save commits each decision on its own beat: submitting the tag is
    // a discrete change (written at once), the allocation rides the flush the
    // floating submit performs. Both land; they simply no longer share one
    // payload — and the second write correctly omits `tags`, because by then
    // the tag IS the baseline.
    expect(stub.updateCalls, hasLength(2));
    // tags ride as the JSON-array string the DAO/cache carry.
    expect(jsonDecode(stub.updateCalls.first['tags'] as String), ['work']);
    expect(stub.updateCalls.last['allocatedBudget'], 250.0);
    expect(stub.updateCalls.last['clearAllocatedBudget'], false);
  });

  testWidgets('an un-submitted tag is folded in on Save', (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    // Type a tag but DON'T submit, and close the popup — Save must still
    // capture it (the controller is owned by the sheet, not by the popup).
    await tester.tap(find.byKey(kTaskTagsChipKey));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('task-detail-tag-input')),
      'urgent',
    );
    await tester.tap(find.byKey(kTaskTagsDoneKey));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();

    expect(jsonDecode(stub.updateCalls.single['tags'] as String), ['urgent']);
  });

  testWidgets('Delete asks to confirm then invokes deleteTask', (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.tap(find.byKey(const Key('task-detail-delete')));
    await tester.pumpAndSettle();

    // Confirmation dialog is up; nothing deleted yet.
    expect(stub.deleteCalls, isEmpty);

    // Tap the dialog's "Delete" confirm (footer button reads "Delete Task").
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();

    expect(stub.deleteCalls, ['task-42']);
    expect(find.byKey(const Key('task-detail-title')), findsNothing);
  });

  testWidgets('picking a project then Save commits the category', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(
      tester,
      stub,
      projects: [project('p1', 'Home', color: '#FF0000')],
    );

    // The project control starts at "No project" (sample has no category).
    expect(find.text('No project'), findsOneWidget);

    await tester.tap(find.byKey(const Key('task-detail-project')));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('project-pick-p1')));
    await tester.pumpAndSettle();

    // The control now reflects the chosen project.
    expect(find.text('Home'), findsWidgets);

    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();

    expect(stub.updateCalls, hasLength(1));
    expect(stub.updateCalls.single['category'], 'Home');
  });

  testWidgets('an untouched recurrence is NOT written on Save', (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    // Only edit the title; never touch the REPEAT picker.
    await tester.enterText(
      find.byKey(const Key('task-detail-title')),
      'Edited',
    );
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();

    expect(stub.updateCalls, hasLength(1));
    // Null = "leave recurring alone" (the column isn't churned).
    expect(stub.updateCalls.single['recurring'], isNull);
  });

  testWidgets('picking Daily then Save sends a daily cron', (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    // The sample has a date-only due of 2026-06-10 → no time → default 09:00.
    await tester.ensureVisible(
      find.byKey(const ValueKey('recurrence-opt-daily')),
    );
    await tester.tap(find.byKey(const ValueKey('recurrence-opt-daily')));
    await tester.pump();

    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();

    expect(stub.updateCalls, hasLength(1));
    expect(stub.updateCalls.single['recurring'], '0 9 * * *');
  });

  testWidgets('a recurring task pre-selects its option + clearing sends ""', (
    tester,
  ) async {
    final stub = _stub();
    // A weekly-Monday recurring task.
    const recurring = Task(
      id: 'task-99',
      userId: 'u1',
      title: 'Weekly sync',
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      dueDate: '2026-06-08',
      recurring: '0 9 * * 1',
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(800, 2200);
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          tasksProvider.overrideWith((ref) => stub),
          budgetsProvider.overrideWith((ref) => _stubBudgets()),
        ],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: Consumer(
            builder: (ctx, ref, _) => Scaffold(
              body: Center(
                child: ElevatedButton(
                  onPressed: () => showTaskDetailSheet(ctx, ref, recurring),
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

    // The Weekly option is pre-selected from the stored cron.
    final weekly = tester.widget<LzChip>(
      find.byKey(const ValueKey('recurrence-opt-weekly')),
    );
    expect(weekly.selected, isTrue);

    // Clear it: tap "No repeat", then Save → empty string clears the column.
    await tester.ensureVisible(
      find.byKey(const ValueKey('recurrence-opt-none')),
    );
    await tester.tap(find.byKey(const ValueKey('recurrence-opt-none')));
    await tester.pump();
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();

    expect(stub.updateCalls, hasLength(1));
    expect(stub.updateCalls.single['recurring'], '');
  });
}
