// Widget-level tests for [SmartAddController.buildTextSpan] — verifies that the
// recognized smart-add tokens are painted as distinct, highlighted spans while
// non-token text stays plain, and that the spans always reconstruct the input.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';
import 'package:lazyclaw_mobile/screens/tasks/smart_add_controller.dart';

void main() {
  // Saturday, 6 June 2026 — same anchor as the parser tests.
  final now = DateTime(2026, 6, 6);

  /// Build the controller's span for [text] under a real [BuildContext].
  Future<TextSpan> spanFor(WidgetTester tester, String text,
      {bool withComposing = false}) async {
    final controller = SmartAddController(text: text);
    controller.tokens = parseSmartAdd(text, now: now).tokens;
    late BuildContext ctx;
    await tester.pumpWidget(MaterialApp(
      home: Builder(builder: (c) {
        ctx = c;
        return const SizedBox.shrink();
      }),
    ));
    return controller.buildTextSpan(
      context: ctx,
      style: const TextStyle(),
      withComposing: withComposing,
    );
  }

  List<TextSpan> kids(TextSpan span) =>
      (span.children ?? const <InlineSpan>[]).cast<TextSpan>();

  testWidgets('emits one highlighted span per token, plain text in between',
      (tester) async {
    const input = 'pay rent tom !p1 #home';
    final parsed = parseSmartAdd(input, now: now);
    final span = await spanFor(tester, input);
    final children = kids(span);

    // The styled (highlighted) spans = those with a background fill.
    final highlighted =
        children.where((s) => s.style?.backgroundColor != null).toList();
    expect(highlighted.length, parsed.tokens.length); // tom + !p1 + #home = 3

    // Highlighted text matches exactly what each token covers.
    expect(highlighted.map((s) => s.text).toList(), ['tom', '!p1', '#home']);

    // Concatenating every child must rebuild the original text — no dropped or
    // duplicated characters.
    expect(children.map((s) => s.text).join(), input);
  });

  testWidgets('priority tokens are bold + colored', (tester) async {
    final span = await spanFor(tester, 'ship !!!');
    final styled =
        kids(span).firstWhere((s) => s.style?.backgroundColor != null);
    expect(styled.text, '!!!');
    expect(styled.style?.fontWeight, FontWeight.w600);
    expect(styled.style?.color, isNotNull);
  });

  testWidgets('no tokens -> falls back to a plain span', (tester) async {
    final span = await spanFor(tester, 'just plain text');
    // No highlighted children when nothing was recognized.
    final highlighted =
        kids(span).where((s) => s.style?.backgroundColor != null);
    expect(highlighted, isEmpty);
  });

  testWidgets('defers to default rendering while an IME composition is active',
      (tester) async {
    final controller = SmartAddController();
    controller.value = const TextEditingValue(
      text: 'meet tom',
      composing: TextRange(start: 5, end: 8), // "tom" mid-composition
    );
    controller.tokens = parseSmartAdd('meet tom', now: now).tokens;
    late BuildContext ctx;
    await tester.pumpWidget(MaterialApp(
      home: Builder(builder: (c) {
        ctx = c;
        return const SizedBox.shrink();
      }),
    ));
    final span = controller.buildTextSpan(
      context: ctx,
      style: const TextStyle(),
      withComposing: true,
    );
    // While composing we hand off to the base controller, which does not paint
    // our token highlight — so no child carries our background fill.
    final highlighted = (span.children ?? const <InlineSpan>[])
        .whereType<TextSpan>()
        .where((s) => s.style?.backgroundColor != null);
    expect(highlighted, isEmpty);
  });

  testWidgets('setting identical tokens does not notify listeners',
      (tester) async {
    final controller = SmartAddController(text: 'x tom');
    final tokens = parseSmartAdd('x tom', now: now).tokens;
    controller.tokens = tokens;
    var notifications = 0;
    controller.addListener(() => notifications++);
    controller.tokens = parseSmartAdd('x tom', now: now).tokens; // same shape
    expect(notifications, 0);
  });
}
