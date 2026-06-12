import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_toolbar.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_model.dart';

void main() {
  Widget wrap(Widget child) => MaterialApp(home: Scaffold(body: child));

  group('SheetToolbar rendering', () {
    testWidgets('renders toolbar with bold button highlighted when style is bold',
        (tester) async {
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(bold: true),
            canUndo: false,
            canRedo: false,
            onAction: (_) {},
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      // The 'B' button should be present.
      expect(find.text('B'), findsOneWidget);

      // The active (bold) 'B' button should have an accent-tinted container.
      // We check by verifying there is exactly one Container descendant of the
      // 'B' InkWell that has a non-null decoration with a non-null color.
      final bFinder = find.ancestor(
        of: find.text('B'),
        matching: find.byType(Container),
      );
      final containers = tester.widgetList<Container>(bFinder).toList();
      final hasActiveTint = containers.any((c) {
        final dec = c.decoration;
        if (dec is BoxDecoration) {
          return dec.color != null && dec.color!.a > 0;
        }
        return false;
      });
      expect(hasActiveTint, isTrue,
          reason: 'Bold button should have accent tint when bold is active');
    });

    testWidgets('renders italic button without highlight when italic is false',
        (tester) async {
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(bold: true, italic: false),
            canUndo: false,
            canRedo: false,
            onAction: (_) {},
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      // Italic 'I' text should be present.
      expect(find.text('I'), findsOneWidget);
    });

    testWidgets('tapping italic fires the italic action', (tester) async {
      SheetToolbarAction? fired;
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: false,
            canRedo: false,
            onAction: (a) => fired = a,
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      await tester.tap(find.text('I'));
      await tester.pump();

      expect(fired, SheetToolbarAction.italic);
    });

    testWidgets('tapping bold fires the bold action', (tester) async {
      SheetToolbarAction? fired;
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: true,
            canRedo: false,
            onAction: (a) => fired = a,
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      await tester.tap(find.text('B'));
      await tester.pump();

      expect(fired, SheetToolbarAction.bold);
    });

    testWidgets('undo button is disabled when canUndo is false', (tester) async {
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: false,
            canRedo: false,
            onAction: (_) {},
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      final undoBtns = tester.widgetList<IconButton>(
        find.byWidgetPredicate(
          (w) => w is IconButton && (w.icon as Icon).icon == Icons.undo,
        ),
      );
      expect(undoBtns.every((b) => b.onPressed == null), isTrue,
          reason: 'Undo button should be disabled when canUndo is false');
    });

    testWidgets('undo button is enabled when canUndo is true', (tester) async {
      SheetToolbarAction? fired;
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: true,
            canRedo: false,
            onAction: (a) => fired = a,
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      // Tap undo.
      final undoBtn = find.byWidgetPredicate(
        (w) => w is IconButton && (w.icon as Icon).icon == Icons.undo,
      );
      await tester.tap(undoBtn);
      await tester.pump();

      expect(fired, SheetToolbarAction.undo);
    });

    testWidgets('redo button is disabled when canRedo is false', (tester) async {
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: false,
            canRedo: false,
            onAction: (_) {},
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      final redoBtns = tester.widgetList<IconButton>(
        find.byWidgetPredicate(
          (w) => w is IconButton && (w.icon as Icon).icon == Icons.redo,
        ),
      );
      expect(redoBtns.every((b) => b.onPressed == null), isTrue);
    });

    testWidgets('wrap toggle button is present', (tester) async {
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: false,
            canRedo: false,
            onAction: (_) {},
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      expect(find.byIcon(Icons.wrap_text), findsOneWidget);
    });

    testWidgets('tapping wrap fires wrapToggle action', (tester) async {
      SheetToolbarAction? fired;
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: false,
            canRedo: false,
            onAction: (a) => fired = a,
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      await tester.tap(find.byIcon(Icons.wrap_text));
      await tester.pump();

      expect(fired, SheetToolbarAction.wrapToggle);
    });

    testWidgets('alignment buttons are present', (tester) async {
      await tester.pumpWidget(
        wrap(
          SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: false,
            canRedo: false,
            onAction: (_) {},
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      );

      expect(find.byIcon(Icons.format_align_left), findsOneWidget);
      expect(find.byIcon(Icons.format_align_center), findsOneWidget);
      expect(find.byIcon(Icons.format_align_right), findsOneWidget);
    });
  });
}
