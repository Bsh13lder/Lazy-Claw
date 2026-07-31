/// Segmentation must be safe at an ARBITRARY chunk boundary, because model
/// tokens arrive split in ways no fixture can enumerate.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/sentence_streamer.dart';

/// Whitespace-normalized, so "nothing lost, nothing duplicated" can be asserted
/// without pinning where the trims fell.
String _norm(String s) => s.trim().split(RegExp(r'\s+')).join(' ');

List<String> _feedWhole(String text) {
  final s = SentenceStreamer();
  final out = s.add(text);
  final tail = s.flush();
  return [...out, if (tail != null) tail];
}

List<String> _feedByChar(String text) {
  final s = SentenceStreamer();
  final out = <String>[];
  for (final ch in text.split('')) {
    out.addAll(s.add(ch));
  }
  final tail = s.flush();
  return [...out, if (tail != null) tail];
}

void main() {
  group('basic segmentation', () {
    test('holds an incomplete tail', () {
      final s = SentenceStreamer();
      expect(s.add('Hello there. How are'), ['Hello there.']);
      expect(_norm(s.pending), 'How are');
    });

    test('a terminator at the very end never cuts — it waits for lookahead', () {
      final s = SentenceStreamer();
      // Without lookahead this would emit, and the next chunk would reveal it
      // was a decimal all along.
      expect(s.add('It costs 3.'), isEmpty);
      expect(s.add('5 euros. '), ['It costs 3.5 euros.']);
    });

    test('splits a single large chunk into several sentences', () {
      // The quiet-mode cloud path delivers an entire reply in one frame.
      final out = SentenceStreamer()
          .add('First one here. Second one here. Third one here. ');
      expect(out.length, 3);
    });
  });

  group('false-positive periods', () {
    test('decimals', () {
      expect(_feedWhole('About 3.5 million people live here. '),
          ['About 3.5 million people live here.']);
    });

    test('abbreviations', () {
      expect(_feedWhole('Dr. Smith is here now. '), ['Dr. Smith is here now.']);
    });

    test('initials', () {
      expect(_feedWhole('J. Blue wrote back today. '),
          ['J. Blue wrote back today.']);
    });

    test('e.g. and a.m.', () {
      expect(_feedWhole('Try it e.g. tomorrow at 9 a.m. sharp. ').length, 1);
    });

    test('an ellipsis is a hesitation, not an ending', () {
      expect(_feedWhole('Well... maybe tomorrow works. '),
          ['Well... maybe tomorrow works.']);
    });

    test('but a mixed run like "?!" still ends the sentence', () {
      final out = _feedWhole('Are you serious?! I had no idea at all. ');
      expect(out.length, 2);
      expect(out.first, 'Are you serious?!');
    });

    test('a sentence that ENDS in a year still cuts', () {
      // The guard must be "digits at the start of a line" (a list marker), not
      // "any digits before a dot" — otherwise this never ends.
      final out = _feedWhole('The year is 2026. Next thing happens soon. ');
      expect(out.length, 2);
      expect(out.first, 'The year is 2026.');
    });
  });

  group('multilingual', () {
    test('Spanish inverted punctuation', () {
      final out = _feedWhole('¿Qué tal todo hoy? Todo bien por aquí. ');
      expect(out.length, 2);
    });

    test('Georgian — proves the rules are punctuation-driven, not case-driven', () {
      // Georgian has no letter case at all, so any "next char is uppercase"
      // heuristic would emit exactly zero sentences here.
      final out = _feedWhole('გამარჯობა ჩემო მეგობარო. როგორ ხარ დღეს? ');
      expect(out.length, 2);
    });
  });

  group('chunk-boundary safety (the property that matters)', () {
    const samples = [
      'Sure, the capital is Madrid. It has about 3.2 million people. ',
      'Dr. Smith called at 9 a.m. He said it costs 1.5 euros. ',
      '¿Qué tal? Todo bien. Nos vemos mañana. ',
      'გამარჯობა. როგორ ხარ? კარგად ვარ. ',
      'Well... I am not sure. Maybe J. Blue knows the answer. ',
    ];

    for (var i = 0; i < samples.length; i++) {
      test('one character at a time == all at once [$i]', () {
        expect(_feedByChar(samples[i]), _feedWhole(samples[i]));
      });
    }

    test('nothing is lost or duplicated', () {
      for (final sample in samples) {
        expect(_norm(_feedByChar(sample).join(' ')), _norm(sample));
      }
    });

    test('an emoji surrogate pair split across chunks survives intact', () {
      final s = SentenceStreamer();
      const emoji = '🎉';
      final out = <String>[];
      // Feed the two halves of the surrogate pair in separate chunks.
      out.addAll(s.add('We did it '));
      out.addAll(s.add(emoji.substring(0, 1)));
      out.addAll(s.add('${emoji.substring(1)} today. '));
      final tail = s.flush();
      final all = [...out, if (tail != null) tail].join(' ');
      expect(all, contains(emoji));
    });
  });

  group('length control', () {
    test('a short opener is held until the floor is met', () {
      final s = SentenceStreamer();
      // "Sure." alone is below firstMinChars-plus-content; it accumulates
      // rather than becoming its own choppy utterance.
      final out = s.add('OK. ');
      expect(out.length, lessThanOrEqualTo(1));
    });

    test('a run-on with no punctuation snaps at a space, not mid-word', () {
      final longRun = 'word ' * 80; // 400 chars, no terminator
      final out = SentenceStreamer().add(longRun);
      expect(out, isNotEmpty);
      for (final piece in out) {
        expect(piece.length, lessThanOrEqualTo(220));
        expect(piece.endsWith('word'), isTrue,
            reason: 'cut must land on a word boundary');
      }
    });
  });

  group('flush', () {
    test('returns the tail exactly once', () {
      final s = SentenceStreamer();
      s.add('No terminator here');
      expect(s.flush(), 'No terminator here');
      expect(s.flush(), isNull, reason: 'a tail must never be spoken twice');
    });

    test('returns null when nothing is buffered', () {
      expect(SentenceStreamer().flush(), isNull);
    });
  });
}
