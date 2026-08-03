part of '../smart_add_parser.dart';

// Tiebreak ranks for `_resolveOverlaps` (see `smart_add_parser.dart`): when
// two accepted matches share both `start` AND `length` — a genuine tie, since
// Dart's `List.sort` is NOT stable — the higher `rank` wins. Full intended
// scale (Task 4 of the parser plan): range 100 > explicitDate 90 >
// relativeDate 80 > recurrence 70 > reminder 60 > time 50 > priority 30 >
// project 20. Only the kinds with a matcher today are declared below;
// `range`/`reminder` are reserved for matcher families landing in later
// tasks and would be unused-declaration warnings if added before they exist.
const int _rankExplicitDate = 90;
const int _rankRelativeDate = 80;
const int _rankRecurrence = 70;
const int _rankTime = 50;
const int _rankPriority = 30;
const int _rankProject = 20;

/// Token-start helper shared by every matcher family: excludes the leading
/// `(^|\s)` boundary group (group 1) so a highlight covers exactly the token
/// characters typed, not the preceding space.
int _tokenStart(RegExpMatch m) => m.start + (m.group(1)?.length ?? 0);

/// Per-parse loop state shared by every matcher family's `collectX(Collector)`
/// function (`smart_add/dates.dart`, `times.dart`, `priority.dart`,
/// `recurrence_patterns.dart`, `project.dart`). [scan] runs [re] against
/// [input] and, for each match, computes the token start via [_tokenStart]
/// then invokes [f]; a non-null [Raw] is appended to [raws].
class Collector {
  final String input;
  final DateTime ref;
  final DateTime today;
  final List<Raw> raws = [];

  Collector(this.input, this.ref, this.today);

  void scan(RegExp re, Raw? Function(RegExpMatch m, int start) f) {
    for (final m in re.allMatches(input)) {
      final r = f(m, _tokenStart(m));
      if (r != null) raws.add(r);
    }
  }
}
