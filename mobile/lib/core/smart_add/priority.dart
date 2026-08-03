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

// Bare `p1`/`p2`/`p3`/`p4` (no leading bang), standalone token only.
final RegExp _priorityBare = RegExp(
  r'(^|\s)p([1-4])(?=\s|$)',
  caseSensitive: false,
);

// Bare bangs: `!`=medium, `!!`=high, `!!!`=urgent — standalone token only.
final RegExp _priorityBangs = RegExp(r'(^|\s)(!{1,3})(?=\s|$)');

/// Every priority matcher, run against the original input. All emit
/// `SmartTokenKind.priority` [Raw]s ranked `_rankPriority`.
void collectPriority(Collector c) {
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
  c.scan(
    _priorityBare,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.priority,
      rank: _rankPriority,
      priority: _priorityByCode[m.group(2)!],
    ),
  );
  c.scan(_priorityBangs, (m, s) {
    final n = m.group(2)!.length;
    return Raw(
      s,
      m.end,
      SmartTokenKind.priority,
      rank: _rankPriority,
      priority: n == 3 ? 'urgent' : (n == 2 ? 'high' : 'medium'),
    );
  });
}
