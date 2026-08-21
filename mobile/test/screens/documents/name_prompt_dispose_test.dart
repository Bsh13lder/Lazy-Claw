/// The name-prompt dialog must not dispose its controller while it is still
/// on screen.
///
/// `showDialog`'s future completes the moment `Navigator.pop` is called, but
/// the dialog's widget tree stays mounted for the whole exit transition.
/// Creating the `TextEditingController` in `_promptName` and disposing it after
/// the `await` therefore handed the still-mounted `TextField` a DISPOSED
/// controller; the next frame threw
///
///     A TextEditingController was used after being disposed.
///
/// followed by a 99,626-pixel `RenderFlex` overflow. That throw does not blank
/// the editor's body — it takes down the whole route, app bar included, which
/// is exactly the reported "whole screen black, no top bar".
///
/// Regression introduced 2026-08-21 while fixing a (harmless) controller leak;
/// the fix is that `_NameField`'s own State owns the controller, so disposal is
/// tied to the widget actually leaving the tree.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/documents_screen.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'dart:io';

/// Network-only transport: no local DB, so `createBlank` takes the direct
/// network path and the screen stays a pure widget test.
class _EmptyTransport implements DocumentsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    if (path.contains('/changes')) {
      return const {'items': [], 'server_time': '2026-08-21T12:00:00Z'};
    }
    return const {'sheets': [], 'docs': [], 'files': []};
  }

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async {
    final row = {'id': 'srv-1', 'name': b['name'], 'updated_at': 'now'};
    return {'sheet': row, 'doc': row};
  }

  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async =>
      const {};
  @override
  Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async =>
      const {};
  @override
  Future<Map<String, dynamic>> deleteJson(String p) async => const {};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => const {};
  @override
  Future<List<int>> getBytes(String p) async => const [];
  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async => const [];
}

Widget _host() => ProviderScope(
      overrides: [
        documentsRepositoryProvider
            .overrideWithValue(DocumentsRepository(_EmptyTransport())),
      ],
      child: MaterialApp(theme: buildAppTheme(), home: const DocumentsScreen()),
    );

void main() {
  testWidgets('Create: the dialog closes without a disposed-controller throw',
      (tester) async {
    await tester.pumpWidget(_host());
    await tester.pump();

    await tester.tap(find.byType(FloatingActionButton));
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog), findsOneWidget);

    await tester.tap(find.text('Create'));

    // The frames DURING the dialog's exit transition are where the disposed
    // controller was touched. Step through them one at a time.
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 30));
      expect(tester.takeException(), isNull,
          reason: 'threw on exit-transition frame $i');
    }
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('Cancel: closing without creating also stays clean',
      (tester) async {
    await tester.pumpWidget(_host());
    await tester.pump();

    await tester.tap(find.byType(FloatingActionButton));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Cancel'));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 30));
      expect(tester.takeException(), isNull, reason: 'threw on frame $i');
    }
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog), findsNothing);
  });

  testWidgets('the typed name is what actually reaches the create call',
      (tester) async {
    await tester.pumpWidget(_host());
    await tester.pump();

    await tester.tap(find.byType(FloatingActionButton));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'Q3 budget');
    await tester.pump();
    await tester.tap(find.text('Create'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    // The list prepends the created doc under the name we typed.
    expect(find.text('Q3 budget'), findsWidgets);
  });

  testWidgets('an empty name cancels instead of creating "Untitled"',
      (tester) async {
    await tester.pumpWidget(_host());
    await tester.pump();

    await tester.tap(find.byType(FloatingActionButton));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '   ');
    await tester.pump();
    await tester.tap(find.text('Create'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.byType(DocumentsScreen), findsOneWidget,
        reason: 'should stay on the list, not navigate into an editor');
  });
}
