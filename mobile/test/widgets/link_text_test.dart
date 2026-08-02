import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';

void main() {
  test('tokenizeLinks handles named links, bare urls, trailing punctuation',
      () {
    expect(
      tokenizeLinks('see [docs](https://a.io/d) or https://b.io/x, ok'),
      [
        LinkSpanToken('see ', null),
        LinkSpanToken('docs', 'https://a.io/d'),
        LinkSpanToken(' or ', null),
        LinkSpanToken('https://b.io/x', 'https://b.io/x'),
        LinkSpanToken(', ok', null),
      ],
    );
    expect(tokenizeLinks('no links here'),
        [LinkSpanToken('no links here', null)]);
  });

  test('tokenizeLinks returns empty-plain token for an empty string', () {
    expect(tokenizeLinks(''), [LinkSpanToken('', null)]);
  });

  test('tokenizeLinks handles a link at the very start and end of the text',
      () {
    expect(
      tokenizeLinks('https://a.io/x https://b.io/y'),
      [
        LinkSpanToken('https://a.io/x', 'https://a.io/x'),
        LinkSpanToken(' ', null),
        LinkSpanToken('https://b.io/y', 'https://b.io/y'),
      ],
    );
  });

  test('tokenizeLinks handles multiple named links back to back', () {
    expect(
      tokenizeLinks('[a](https://a.io)[b](https://b.io)'),
      [
        LinkSpanToken('a', 'https://a.io'),
        LinkSpanToken('b', 'https://b.io'),
      ],
    );
  });

  test(
      'tokenizeLinks trims a trailing close-paren off a bare url '
      '(no smart paren-balancing, matches univer_links.dart precedent)', () {
    expect(
      tokenizeLinks('see https://en.wikipedia.org/wiki/Foo_(bar) now'),
      [
        LinkSpanToken('see ', null),
        LinkSpanToken('https://en.wikipedia.org/wiki/Foo_(bar',
            'https://en.wikipedia.org/wiki/Foo_(bar'),
        LinkSpanToken(') now', null),
      ],
    );
  });

  test('tokenizeLinks keeps an internal (unbalanced-at-end) paren in a url',
      () {
    expect(
      tokenizeLinks('see https://a.io/(x)/y now'),
      [
        LinkSpanToken('see ', null),
        LinkSpanToken('https://a.io/(x)/y', 'https://a.io/(x)/y'),
        LinkSpanToken(' now', null),
      ],
    );
  });

  testWidgets('tapping a link span invokes onOpen with the Uri',
      (tester) async {
    Uri? opened;
    await tester.pumpWidget(MaterialApp(
        home: LinkText('go https://a.io/d now',
            onOpen: (u) async => opened = u)));
    // Fire the link span's recognizer directly (span taps aren't hit-testable
    // by widget predicates).
    final rich = tester.widget<Text>(find.byType(Text).first);
    final root = rich.textSpan as TextSpan;
    final linkSpan = root.children!
        .whereType<TextSpan>()
        .firstWhere((s) => s.recognizer != null);
    (linkSpan.recognizer as TapGestureRecognizer).onTap!();
    expect(opened, Uri.parse('https://a.io/d'));
  });
}
