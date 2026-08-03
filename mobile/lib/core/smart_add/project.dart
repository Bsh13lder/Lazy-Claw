part of '../smart_add_parser.dart';

// ── Project ──────────────────────────────────────────────────────────────────

// The `#`/`/` pattern itself lives in `smart_add/project_token.dart` (a plain
// import, not a `part of`) — the sibling expense parser
// (`smart_add_expense_parser.dart`) matches against that exact same
// `projectTokenPattern` so `#project` behavior can never drift between the
// two parsers.

/// The project matcher, run against the original input. Only its FIRST hit is
/// collected (later `#tags` stay in the title). Emits a single
/// `SmartTokenKind.project` [Raw] ranked `_rankProject`, or none.
void _collectProject(_Collector c) {
  final pm = projectTokenPattern.firstMatch(c.input);
  if (pm != null) {
    c.raws.add(
      Raw(
        _tokenStart(pm),
        pm.end,
        SmartTokenKind.project,
        rank: _rankProject,
        project: pm.group(2),
      ),
    );
  }
}
