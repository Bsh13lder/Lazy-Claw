import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/ui/components/lz_dialog.dart';

/// Regression test for the "delete popup freezes" bug on the expense/task detail
/// sheets.
///
/// The sheet is opened with `showModalBottomSheet` (defaults to
/// `useRootNavigator: false` → the sheet lives on the *nearest/nested*
/// navigator). `LzConfirm.show` internally uses `showDialog` (defaults to
/// `useRootNavigator: true` → the confirm popup lives on the *root* navigator).
///
/// If the confirm/cancel buttons pop via the *caller's* context, they resolve to
/// the nested navigator and pop the underlying SHEET instead of the popup. The
/// popup's future never completes → the awaiting `_delete()` hangs and the popup
/// stays frozen on screen. The buttons must pop via the dialog's OWN context.
void main() {
  testWidgets(
    'LzConfirm confirm button resolves when shown over a modal sheet on a nested navigator',
    (tester) async {
      final rootKey = GlobalKey<NavigatorState>();
      bool? result;

      await tester.pumpWidget(
        MaterialApp(
          theme: buildAppTheme(),
          navigatorKey: rootKey,
          home: Scaffold(
            // A nested Navigator mimics a go_router shell route (the app's tabs
            // each host their own navigator). The modal sheet lands here; the
            // confirm popup lands on the root navigator above it.
            body: Navigator(
              onGenerateRoute: (_) => MaterialPageRoute<void>(
                builder: (nestedCtx) => Scaffold(
                  body: Center(
                    child: ElevatedButton(
                      key: const Key('open-sheet'),
                      onPressed: () {
                        showModalBottomSheet<void>(
                          context: nestedCtx,
                          builder: (sheetCtx) => ElevatedButton(
                            key: const Key('sheet-delete'),
                            onPressed: () async {
                              result = await LzConfirm.show(
                                sheetCtx,
                                title: 'Delete expense?',
                                message: 'This expense will be removed.',
                                confirmLabel: 'Delete',
                                danger: true,
                              );
                            },
                            child: const Text('sheet body'),
                          ),
                        );
                      },
                      child: const Text('open'),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );

      await tester.tap(find.byKey(const Key('open-sheet')));
      await tester.pumpAndSettle();

      // Open the confirm popup from inside the sheet.
      await tester.tap(find.byKey(const Key('sheet-delete')));
      await tester.pumpAndSettle();
      expect(find.text('Delete expense?'), findsOneWidget);

      // Tap the popup's "Delete" (distinct from the sheet body text).
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();

      // The popup must have resolved to `true` — with the bug it pops the sheet
      // instead and this future never completes (result stays null).
      expect(result, isTrue,
          reason: 'confirm popup future must complete when confirmed');
      // And the popup itself must be gone.
      expect(find.text('Delete expense?'), findsNothing);
    },
  );

  testWidgets(
    'LzConfirm cancel button resolves to false over a nested navigator',
    (tester) async {
      bool? result;

      await tester.pumpWidget(
        MaterialApp(
          theme: buildAppTheme(),
          home: Scaffold(
            body: Navigator(
              onGenerateRoute: (_) => MaterialPageRoute<void>(
                builder: (nestedCtx) => Scaffold(
                  body: Center(
                    child: ElevatedButton(
                      key: const Key('open-sheet'),
                      onPressed: () {
                        showModalBottomSheet<void>(
                          context: nestedCtx,
                          builder: (sheetCtx) => ElevatedButton(
                            key: const Key('sheet-delete'),
                            onPressed: () async {
                              result = await LzConfirm.show(
                                sheetCtx,
                                title: 'Delete expense?',
                                confirmLabel: 'Delete',
                                danger: true,
                              );
                            },
                            child: const Text('sheet body'),
                          ),
                        );
                      },
                      child: const Text('open'),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );

      await tester.tap(find.byKey(const Key('open-sheet')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('sheet-delete')));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();

      expect(result, isFalse);
      expect(find.text('Delete expense?'), findsNothing);
    },
  );
}
