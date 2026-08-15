/// Collapses a batch-persisted agent TURN into a single chat bubble.
///
/// The server writes every row of one agent turn in ONE batch, so they all
/// share the same `created_at` (second precision). A turn typically persists
/// TWO assistant rows: an interim status line ("Checking now…", "dispatched
/// to the freelance specialist…") that usually carries the turn's
/// `tool_calls` metadata, and then the final reply. Rendered verbatim that is
/// two bubbles per turn — the chat reads as if the agent answered twice.
///
/// [mergeTurnRows] is a PURE post-processing pass over already-mapped history
/// rows (see `repositories/chat_history_repository.dart`): it runs on both the
/// initial seed and every delta-merge tail fetch, so the reducer only ever
/// sees one row per turn.
///
/// Contract:
///  * only CONSECUTIVE assistant rows with a non-null, byte-equal `createdAt`
///    merge — user rows (including `kind == 'cron'` system rows) and rows with
///    an unparseable timestamp are pass-through, and never break their
///    neighbours' grouping other than by sitting between them;
///  * `kind == 'notification'` rows are their own family: a proactive ping is
///    NOT turn narration, and collapsing two of them would silently drop a
///    reminder's text. They never absorb and are never absorbed;
///  * the merged bubble keeps the LAST row's identity (`id`, `kind`,
///    `createdAt`), shows the LAST non-empty display text (the interim status
///    is dropped), and concatenates every row's tool chips in order,
///    de-duplicated by `toolCallId`;
///  * every other row's id lands in `absorbedIds`, which the history
///    delta-merge treats as known — without it each re-fetch would re-insert
///    the absorbed interim rows as duplicate bubbles.
library;

import 'chat_message.dart';

/// Returns a new list where each batch-persisted agent turn is one row.
/// Input order is preserved; the input list is never mutated.
List<ChatMessage> mergeTurnRows(List<ChatMessage> rows) {
  if (rows.length < 2) return rows;
  final out = <ChatMessage>[];
  var i = 0;
  while (i < rows.length) {
    final head = rows[i];
    if (!_mergeable(head)) {
      out.add(head);
      i++;
      continue;
    }
    var end = i + 1;
    while (end < rows.length && _sameTurn(head, rows[end])) {
      end++;
    }
    out.add(end - i == 1 ? head : _collapse(rows.sublist(i, end)));
    i = end;
  }
  return out;
}

/// A row that may participate in a turn collapse: an assistant row with a
/// parsed timestamp that is not a proactive notification ping.
bool _mergeable(ChatMessage m) =>
    m.role == 'assistant' && m.createdAt != null && !_isNotification(m);

/// True when [next] belongs to the same persisted turn as [head] — same
/// kind-family, same exact `created_at`.
bool _sameTurn(ChatMessage head, ChatMessage next) =>
    _mergeable(next) &&
    _isNotification(head) == _isNotification(next) &&
    next.createdAt!.isAtSameMomentAs(head.createdAt!);

bool _isNotification(ChatMessage m) => m.kind == 'notification';

/// Folds a ≥2-row turn into the LAST row.
ChatMessage _collapse(List<ChatMessage> group) {
  final last = group.last;

  // Final text = the LAST row that actually says something. The interim
  // status row is turn narration and is dropped; a trailing chips-only row
  // falls back to the newest row that carried text.
  var content = last.content;
  for (var i = group.length - 1; i >= 0; i--) {
    if (group[i].displayContent.trim().isNotEmpty) {
      content = group[i].content;
      break;
    }
  }

  // Chips concatenate in row order. `toolCallId` is the dedup key — the same
  // call can be persisted on both the interim and the final row. Chips
  // without an id are all kept (nothing to dedup them by).
  final tools = <ToolActivity>[];
  final seenCallIds = <String>{};
  for (final m in group) {
    for (final t in m.toolActivities) {
      final cid = t.toolCallId;
      if (cid != null && cid.isNotEmpty && !seenCallIds.add(cid)) continue;
      tools.add(t);
    }
  }

  final absorbed = <String>[];
  for (var i = 0; i < group.length - 1; i++) {
    final id = group[i].id;
    if (id != null && id.isNotEmpty) absorbed.add(id);
  }

  return last.withAbsorbedRows(
    content: content,
    toolActivities: tools,
    absorbedIds: absorbed,
  );
}
