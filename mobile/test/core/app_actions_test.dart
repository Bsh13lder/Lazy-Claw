import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/actions/app_actions.dart';

void main() {
  group('appActionForShortcut', () {
    test('maps the four launcher shortcut types', () {
      expect(appActionForShortcut('add_task'), AppAction.addTask);
      expect(appActionForShortcut('add_expense'), AppAction.addExpense);
      expect(appActionForShortcut('chat'), AppAction.chat);
      expect(appActionForShortcut('new_note'), AppAction.newNote);
    });

    test('is tolerant of camelCase / case differences', () {
      expect(appActionForShortcut('addTask'), AppAction.addTask);
      expect(appActionForShortcut('AddExpense'), AppAction.addExpense);
      expect(appActionForShortcut('NEWNOTE'), AppAction.newNote);
    });

    test('returns null for unknown or empty/null input', () {
      expect(appActionForShortcut('something_else'), isNull);
      expect(appActionForShortcut(''), isNull);
      expect(appActionForShortcut(null), isNull);
    });
  });

  group('appActionForUri', () {
    test('maps lazyclaw://<host> deep links by URI host', () {
      expect(appActionForUri(Uri.parse('lazyclaw://addTask')),
          AppAction.addTask);
      expect(appActionForUri(Uri.parse('lazyclaw://addExpense')),
          AppAction.addExpense);
      expect(appActionForUri(Uri.parse('lazyclaw://newNote')),
          AppAction.newNote);
      expect(appActionForUri(Uri.parse('lazyclaw://chat')), AppAction.chat);
    });

    test('maps the Today-tasks widget host (lazyclaw://tasks) → openTasks', () {
      expect(appActionForUri(Uri.parse('lazyclaw://tasks')),
          AppAction.openTasks);
    });

    test('matches snake_case hosts too', () {
      expect(appActionForUri(Uri.parse('lazyclaw://add_task')),
          AppAction.addTask);
    });

    test('falls back to the first path segment when host is empty', () {
      final uri = Uri.parse('lazyclaw:///add_expense');
      expect(uri.host, isEmpty);
      expect(appActionForUri(uri), AppAction.addExpense);
    });

    test('returns null for unknown host or null uri', () {
      expect(appActionForUri(Uri.parse('lazyclaw://bogus')), isNull);
      expect(appActionForUri(null), isNull);
    });
  });

  group('routeForAction', () {
    test('routes each action to its branch', () {
      expect(routeForAction(AppAction.addTask), '/tasks');
      expect(routeForAction(AppAction.newNote), '/tasks');
      expect(routeForAction(AppAction.openTasks), '/tasks');
      expect(routeForAction(AppAction.addExpense), '/expenses');
      expect(routeForAction(AppAction.chat), '/chat');
      expect(routeForAction(AppAction.openInbox), '/inbox');
    });
  });

  group('kShortcutSpecs', () {
    test('declares the four LAUNCHER shortcut actions, each round-trippable',
        () {
      // The launcher long-press menu carries the four capture/chat shortcuts —
      // NOT openTasks (widget body tap) or openInbox (notification deep-link),
      // so the spec list is exactly AppAction.values minus those two.
      final shortcutActions = AppAction.values
          .where(
              (a) => a != AppAction.openTasks && a != AppAction.openInbox)
          .toSet();
      expect(kShortcutSpecs.length, shortcutActions.length);
      final resolved =
          kShortcutSpecs.map((s) => appActionForShortcut(s.type)).toSet();
      expect(resolved, shortcutActions);
      // Every spec carries a non-empty drawable icon name.
      for (final spec in kShortcutSpecs) {
        expect(spec.icon, isNotEmpty);
        expect(spec.title, isNotEmpty);
      }
    });
  });

  group('kActionIds', () {
    test('every AppAction has a stable id and a route', () {
      for (final action in AppAction.values) {
        expect(kActionIds[action], isNotNull);
        expect(routeForAction(action), startsWith('/'));
      }
    });
  });
}
