part of '../smart_add_parser.dart';

// ── Priority ─────────────────────────────────────────────────────────────────

const Map<String, String> _priorityByCode = {
  '1': 'urgent',
  '2': 'high',
  '3': 'medium',
  '4': 'low',
};

// `!p1`/`!1` … `!p4`/`!4` — only as a standalone whitespace-delimited token.
final RegExp _priorityCode = RegExp(
  r'(^|\s)!p?([1-4])(?=\s|$)',
  caseSensitive: false,
);

// NOTE: bare `p1`/`p2`/`p3`/`p4` (no leading bang) was deleted (G1 #1) — it
// was a pure false-positive generator (`my p1 project` → urgent today) with
// no compensating value: the bang form (`!p1`) already covers the same
// intent unambiguously.

// Bangs: `!!`=high, `!!!`=urgent — standalone token only. A single `!` is
// intentionally NOT matched (G1 #2): it used to set `medium`, which is
// already the app's default when no priority is parsed at all, so matching
// it was pure downside — it silently ate a literal `!` out of the title for
// zero semantic gain.
final RegExp _priorityBangs = RegExp(r'(^|\s)(!{2,3})(?=\s|$)');

/// Every priority matcher, run against the original input. All emit
/// `SmartTokenKind.priority` [Raw]s ranked `_rankPriority`.
void _collectPriority(_Collector c) {
  c.scan(
    _priorityCode,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.priority,
      rank: _rankPriority,
      priority: _priorityByCode[m.group(2)!],
    ),
  );
  c.scan(_priorityBangs, (m, s) {
    final n = m.group(2)!.length; // 2 or 3, per `_priorityBangs`.
    return Raw(
      s,
      m.end,
      SmartTokenKind.priority,
      rank: _rankPriority,
      priority: n == 3 ? 'urgent' : 'high',
    );
  });
}
