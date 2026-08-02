// Widget tests for the "Add link" dialog used by the Notes editor.
//
// Exercised through a bottom-sheet-over-app harness (not a bare
// pumpWidget(AddLinkDialog...)) because the load-bearing behavior here is the
// over-sheet-freeze gotcha: the dialog's buttons MUST pop the DIALOG's own
// context, never an ancestor sheet's — a wrong context there freezes the
// sheet underneath (documented project pattern, see
// feedback_confirm_dialog_over_sheet_navigator). The harness opens a modal
// bottom sheet first, then the dialog from inside it, so a regression that
// pops the sheet instead of the dialog is caught.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_link_dialog.dart';

void main() {
  Widget host(ValueChanged<String?> onResult) {
    return MaterialApp(
      home: Scaffold(
        body: Center(
          child: Builder(
            builder: (rootCtx) => ElevatedButton(
              key: const Key('open-sheet'),
              onPressed: () => showModalBottomSheet<void>(
                context: rootCtx,
                builder: (sheetCtx) => SizedBox(
                  height: 200,
                  child: Center(
                    child: ElevatedButton(
                      key: const Key('open-dialog'),
                      onPressed: () async {
                        final result = await showAddLinkDialog(sheetCtx);
                        onResult(result);
                      },
                      child: const Text('add link'),
                    ),
                  ),
                ),
              ),
              child: const Text('open sheet'),
            ),
          ),
        ),
      ),
    );
  }

  Future<void> openDialog(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('open-sheet')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('open-dialog')));
    await tester.pumpAndSettle();
  }

  testWidgets(
    'entering text + a valid url pops with the [text](url) markdown',
    (tester) async {
      String? captured = 'unset';
      await tester.pumpWidget(host((r) => captured = r));
      await openDialog(tester);

      await tester.enterText(
        find.byKey(const Key('add-link-text')),
        'docs',
      );
      await tester.enterText(
        find.byKey(const Key('add-link-url')),
        'https://a.io',
      );
      await tester.tap(find.byKey(const Key('add-link-insert')));
      await tester.pumpAndSettle();

      expect(captured, '[docs](https://a.io)');
      // The dialog is gone, but the sheet underneath survived.
      expect(find.byKey(const Key('add-link-insert')), findsNothing);
      expect(find.byKey(const Key('open-dialog')), findsOneWidget);
    },
  );

  testWidgets(
    'an invalid url shows an inline error and does NOT pop the dialog',
    (tester) async {
      String? captured = 'unset';
      await tester.pumpWidget(host((r) => captured = r));
      await openDialog(tester);

      await tester.enterText(
        find.byKey(const Key('add-link-url')),
        'notaurl',
      );
      await tester.tap(find.byKey(const Key('add-link-insert')));
      await tester.pump();

      expect(find.text('Enter a full http(s):// URL'), findsOneWidget);
      // Still open — the onResult callback never fired.
      expect(captured, 'unset');
      expect(find.byKey(const Key('add-link-insert')), findsOneWidget);
    },
  );

  testWidgets(
    'Cancel pops null and the sheet underneath survives untouched',
    (tester) async {
      String? captured = 'unset';
      await tester.pumpWidget(host((r) => captured = r));
      await openDialog(tester);

      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();

      expect(captured, isNull);
      // The sheet (with its "add link" button) is still there — a wrong
      // Navigator.pop() target here would have closed the sheet instead.
      expect(find.byKey(const Key('open-dialog')), findsOneWidget);
    },
  );
}
