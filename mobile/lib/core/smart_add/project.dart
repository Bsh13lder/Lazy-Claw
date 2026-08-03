part of '../smart_add_parser.dart';

// ── Project ──────────────────────────────────────────────────────────────────

// `#name` or `/name` at a token boundary. The token-boundary anchor protects
// against `and/or` (slash mid-word) and `6/10` (M/D dates) being misread.
final RegExp _project = RegExp(r'(^|\s)[#/]([A-Za-z0-9_-]+)');

/// The project matcher, run against the original input. Only its FIRST hit is
/// collected (later `#tags` stay in the title). Emits a single
/// `SmartTokenKind.project` [Raw] ranked `_rankProject`, or none.
void collectProject(Collector c) {
  final pm = _project.firstMatch(c.input);
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
