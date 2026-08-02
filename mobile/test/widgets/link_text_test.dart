import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';

void main() {
  test(
    'tokenizeLinks handles named links, bare urls, trailing punctuation',
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
      expect(tokenizeLinks('no links here'), [
        LinkSpanToken('no links here', null),
      ]);
    },
  );

  test('tokenizeLinks returns empty-plain token for an empty string', () {
    expect(tokenizeLinks(''), [LinkSpanToken('', null)]);
  });

  test(
    'tokenizeLinks handles a link at the very start and end of the text',
    () {
      expect(tokenizeLinks('https://a.io/x https://b.io/y'), [
        LinkSpanToken('https://a.io/x', 'https://a.io/x'),
        LinkSpanToken(' ', null),
        LinkSpanToken('https://b.io/y', 'https://b.io/y'),
      ]);
    },
  );

  test('tokenizeLinks handles multiple named links back to back', () {
    expect(tokenizeLinks('[a](https://a.io)[b](https://b.io)'), [
      LinkSpanToken('a', 'https://a.io'),
      LinkSpanToken('b', 'https://b.io'),
    ]);
  });

  test('tokenizeLinks trims a trailing close-paren off a bare url '
      '(no smart paren-balancing, matches univer_links.dart precedent)', () {
    expect(tokenizeLinks('see https://en.wikipedia.org/wiki/Foo_(bar) now'), [
      LinkSpanToken('see ', null),
      LinkSpanToken(
        'https://en.wikipedia.org/wiki/Foo_(bar',
        'https://en.wikipedia.org/wiki/Foo_(bar',
      ),
      LinkSpanToken(') now', null),
    ]);
  });

  test(
    'tokenizeLinks keeps an internal (unbalanced-at-end) paren in a url',
    () {
      expect(tokenizeLinks('see https://a.io/(x)/y now'), [
        LinkSpanToken('see ', null),
        LinkSpanToken('https://a.io/(x)/y', 'https://a.io/(x)/y'),
        LinkSpanToken(' now', null),
      ]);
    },
  );

  testWidgets('tapping a link span invokes onOpen with the Uri', (
    tester,
  ) async {
    Uri? opened;
    await tester.pumpWidget(
      MaterialApp(
        home: LinkText(
          'go https://a.io/d now',
          onOpen: (u) async => opened = u,
        ),
      ),
    );
    // Fire the link span's recognizer directly (span taps aren't hit-testable
    // by widget predicates).
    final rich = tester.widget<Text>(find.byType(Text).first);
    final root = rich.textSpan as TextSpan;
    final linkSpan = root.children!.whereType<TextSpan>().firstWhere(
      (s) => s.recognizer != null,
    );
    (linkSpan.recognizer as TapGestureRecognizer).onTap!();
    expect(opened, Uri.parse('https://a.io/d'));
  });

  // Regression test for a use-after-dispose path: `build()` used to dispose
  // every recognizer and rebuild a fresh batch on EVERY call, even when
  // `text`/`style` hadn't changed. A rebuild while a tap is in-flight (the
  // gesture arena still holding the OLD recognizer) would dispose the very
  // recognizer handling that gesture. Fixed by only rebuilding recognizers
  // in `didUpdateWidget` when `text`/`style` actually change, so an
  // unrelated ancestor rebuild reuses the same recognizer instances.
  testWidgets(
    'rebuilding with unchanged text/style does not throw and reuses the '
    'same recognizer instance (no dispose-while-live risk)',
    (tester) async {
      Uri? opened;
      Widget host() => MaterialApp(
        home: LinkText(
          'go https://a.io/d now',
          onOpen: (u) async => opened = u,
        ),
      );

      TapGestureRecognizer recognizerFor() {
        final rich = tester.widget<Text>(find.byType(Text).first);
        final root = rich.textSpan as TextSpan;
        return root.children!
                .whereType<TextSpan>()
                .firstWhere((s) => s.recognizer != null)
                .recognizer
            as TapGestureRecognizer;
      }

      await tester.pumpWidget(host());
      final first = recognizerFor();

      // Two more rebuilds with the SAME text/style — must not throw, and the
      // recognizer instance must be reused (not disposed + recreated).
      await tester.pumpWidget(host());
      await tester.pumpWidget(host());
      expect(tester.takeException(), isNull);

      final second = recognizerFor();
      expect(second, same(first));

      // The (reused) recognizer still fires correctly.
      second.onTap!();
      expect(opened, Uri.parse('https://a.io/d'));
    },
  );

  testWidgets(
    'rebuilding with DIFFERENT text swaps in a new recognizer and taps '
    'still fire for the new content',
    (tester) async {
      Uri? opened;
      Widget host(String text) =>
          MaterialApp(home: LinkText(text, onOpen: (u) async => opened = u));

      await tester.pumpWidget(host('go https://a.io/d now'));
      await tester.pumpWidget(host('go https://b.io/e now'));
      expect(tester.takeException(), isNull);

      final rich = tester.widget<Text>(find.byType(Text).first);
      final root = rich.textSpan as TextSpan;
      final linkSpan = root.children!.whereType<TextSpan>().firstWhere(
        (s) => s.recognizer != null,
      );
      (linkSpan.recognizer as TapGestureRecognizer).onTap!();
      expect(opened, Uri.parse('https://b.io/e'));
    },
  );
}
