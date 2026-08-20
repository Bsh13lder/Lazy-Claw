// Markdown tables in chat bubbles (2026-08-20 SEO-report incident).
//
// flutter_markdown's DEFAULT table rendering flexes columns into the
// bubble width — a 3-column keyword-rankings table rendered as vertical
// word-shards ("Keywo rd", "barcel onawe edmap .com") with no way to
// scroll. `assistantMarkdownStyle` pins `IntrinsicColumnWidth`, which
// sizes columns to content and flips flutter_markdown into its built-in
// horizontal table scroller.

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/screens/chat/chat_bubble.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

const _table = '| Keyword | HiMap.co position | Who is winning |\n'
    '| --- | --- | --- |\n'
    '| "cannabis clubs Barcelona" | Not in top 9 | barcelonaweedmap.com |\n'
    '| "cannabis social club Barcelona" | Not in top 10 | reddit |';

Future<void> _pump(WidgetTester tester, String data) => tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: SingleChildScrollView(
            child: MarkdownBody(
              data: data,
              styleSheet: assistantMarkdownStyle(),
            ),
          ),
        ),
      ),
    );

Iterable<SingleChildScrollView> _horizontals(WidgetTester tester) => tester
    .widgetList<SingleChildScrollView>(find.byType(SingleChildScrollView))
    .where((s) => s.scrollDirection == Axis.horizontal);

void main() {
  testWidgets('a table renders inside a horizontal scroller', (tester) async {
    await _pump(tester, 'Intro.\n\n$_table\n\nOutro.');
    expect(_horizontals(tester).length, 1,
        reason: 'IntrinsicColumnWidth must flip flutter_markdown into its '
            'built-in horizontal table scroll — without it the columns '
            'crush into unreadable vertical word-shards');
    expect(find.textContaining('Intro.'), findsOneWidget);
    expect(find.textContaining('Outro.'), findsOneWidget);
  });

  testWidgets('table cells keep their words on one line', (tester) async {
    await _pump(tester, _table);
    // The whole point: a long cell is never broken into shards; it lays
    // out at intrinsic width inside the scroller.
    expect(find.text('barcelonaweedmap.com'), findsOneWidget);
  });

  testWidgets('tableless content renders without a horizontal scroller',
      (tester) async {
    await _pump(tester, 'just **text**, no table');
    expect(_horizontals(tester), isEmpty);
  });

  test('style pins intrinsic columns and a visible border', () {
    final style = assistantMarkdownStyle();
    expect(style.tableColumnWidth, isA<IntrinsicColumnWidth>());
    expect(style.tableBorder, isNotNull,
        reason: 'default table border is invisible on the dark theme');
  });
}
