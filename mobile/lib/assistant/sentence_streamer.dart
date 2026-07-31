/// Chunk-boundary-safe sentence segmentation for streamed model output.
///
/// Feed it raw token chunks; it returns only the sentences that are COMPLETE
/// and keeps the tail buffered. This is what lets speech start one sentence
/// into a reply instead of after the whole generation.
///
/// The rules are **punctuation-driven only**. The classic English heuristic
/// "cut only when the next character is uppercase" is deliberately absent: it
/// fails completely on Georgian, which has no letter case, and on lowercase
/// Spanish continuations.
///
/// The load-bearing rule is the mandatory one-character lookahead — a
/// terminator that is the last character in the buffer never cuts. That single
/// rule is what makes an arbitrary chunk boundary safe, and it is why feeding a
/// reply one character at a time produces byte-identical output to feeding it
/// whole.
library;

/// Words that take a trailing period without ending a sentence.
const Set<String> _abbreviations = {
  'dr', 'mr', 'mrs', 'ms', 'sr', 'sra', 'srta', 'st', 'vs', 'etc', 'aprox',
  'no', 'fig', 'approx',
};

const Set<String> _terminators = {'.', '!', '?', '…', '。', '！', '？'};

/// Soft boundaries accepted only for the FIRST utterance of a reply, and only
/// once far enough in — they buy time-to-first-audio on a long opening sentence.
const Set<String> _softBoundaries = {',', ';', ':'};

class SentenceStreamer {
  SentenceStreamer({
    this.minChars = 12,
    this.firstMinChars = 4,
    this.softBoundaryAfter = 40,
    this.maxChars = 220,
  });

  /// Minimum length of a non-first sentence — stops "Sure." becoming its own
  /// utterance, which sounds choppy and costs an extra platform round trip.
  final int minChars;

  /// Lower floor for the first utterance, so audio starts sooner.
  final int firstMinChars;

  /// Only past this offset may the first utterance cut on a comma/colon.
  final int softBoundaryAfter;

  /// A run-on with no punctuation snaps here, at the last space before it.
  /// Also keeps every utterance far below the platform TTS input cap.
  final int maxChars;

  final StringBuffer _buffer = StringBuffer();
  bool _emittedAny = false;
  bool _flushed = false;

  /// The complete sentences contained in [chunk]. May be empty, or several.
  List<String> add(String chunk) {
    if (chunk.isEmpty) return const [];
    _buffer.write(chunk);
    final out = <String>[];
    while (true) {
      final cut = _findCut(_buffer.toString());
      if (cut <= 0) break;
      final s = _buffer.toString();
      final sentence = s.substring(0, cut).trim();
      final rest = s.substring(cut);
      _buffer
        ..clear()
        ..write(rest);
      if (sentence.isEmpty) continue;
      out.add(sentence);
      _emittedAny = true;
    }
    return out;
  }

  /// Whatever is still buffered, at end of stream. Returns null on any call
  /// after the first, so a stream that ends twice can't speak its tail twice.
  String? flush() {
    if (_flushed) return null;
    _flushed = true;
    final tail = _buffer.toString().trim();
    _buffer.clear();
    if (tail.isEmpty) return null;
    _emittedAny = true;
    return tail;
  }

  /// The un-emitted tail, for rendering the reply as it streams.
  String get pending => _buffer.toString();

  /// Index at which to cut, or -1 when more input is needed.
  int _findCut(String s) {
    if (s.isEmpty) return -1;
    final floor = _emittedAny ? minChars : firstMinChars;

    // A blank line is an unambiguous boundary and needs no lookahead.
    final blank = s.indexOf('\n\n');
    if (blank >= 0) return blank + 2;

    for (var i = 0; i < s.length; i++) {
      final c = s[i];

      if (_terminators.contains(c)) {
        // Collapse a run ("...", "?!") and require lookahead past ALL of it.
        var end = i;
        while (end < s.length && _terminators.contains(s[end])) {
          end++;
        }
        if (end >= s.length) return -1; // terminator is last → wait for more
        // An ellipsis is a hesitation, not an ending — "Well... maybe" is one
        // sentence. Erring toward a longer utterance is the safe direction: a
        // wrongly-merged sentence still reads naturally, a wrongly-split one
        // sounds choppy. A mixed run like "?!" IS an ending and still cuts.
        if (_isEllipsisRun(s, i, end)) {
          i = end - 1;
          continue;
        }
        if (c == '.' && _noCutAtDot(s, i)) {
          i = end - 1;
          continue;
        }
        if (end < floor) {
          i = end - 1;
          continue;
        }
        return end;
      }

      // First-utterance soft boundary: a long opening clause may start speaking
      // at a comma rather than waiting for the full sentence.
      if (!_emittedAny &&
          i >= softBoundaryAfter &&
          _softBoundaries.contains(c) &&
          i + 1 < s.length) {
        return i + 1;
      }
    }

    // Run-on with no usable punctuation: snap at the last space before the cap.
    // Snapping to a space (never a raw index) is what guarantees a cut can't
    // land inside a surrogate pair or a grapheme cluster.
    if (s.length > maxChars) {
      final space = s.lastIndexOf(' ', maxChars);
      if (space > 0) return space + 1;
    }
    return -1;
  }

  /// True when the terminator run [start, end) is an ellipsis: two or more
  /// dots, or the single-character form.
  static bool _isEllipsisRun(String s, int start, int end) {
    final run = s.substring(start, end);
    if (run.contains('…')) return true;
    return run.length >= 2 && RegExp(r'^\.+$').hasMatch(run);
  }

  /// True when this period does NOT end a sentence.
  bool _noCutAtDot(String s, int i) {
    // Decimal: digit . digit
    if (i > 0 &&
        _isDigit(s[i - 1]) &&
        i + 1 < s.length &&
        _isDigit(s[i + 1])) {
      return true;
    }

    // Numbered-list marker: a digit run at the very start of the buffer or of a
    // line. Deliberately NOT "any digits before a dot" — that would refuse to
    // ever end a sentence like "The year is 2026."
    var j = i - 1;
    while (j >= 0 && _isDigit(s[j])) {
      j--;
    }
    if (j < i - 1 && (j < 0 || s[j] == '\n')) return true;

    // The preceding word token.
    var k = i - 1;
    while (k >= 0 && _isWordChar(s[k])) {
      k--;
    }
    final token = s.substring(k + 1, i);
    if (token.isEmpty) return false;
    // A single letter is an initial ("J. Blue"). This also covers the trailing
    // dot of "e.g." and "a.m.", whose last token is one letter.
    if (token.length == 1 && _isLetter(token)) return true;
    return _abbreviations.contains(token.toLowerCase());
  }

  static bool _isDigit(String c) => c.codeUnitAt(0) ^ 0x30 <= 9;

  static bool _isLetter(String c) {
    final u = c.toLowerCase().codeUnitAt(0);
    return u >= 0x61 && u <= 0x7A;
  }

  static bool _isWordChar(String c) =>
      _isDigit(c) || RegExp(r'[^\s.!?…。！？,;:()\[\]"]').hasMatch(c);
}
