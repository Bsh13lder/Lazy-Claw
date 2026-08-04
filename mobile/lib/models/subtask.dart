import 'dart:convert';

import '../local/uuid.dart';

/// A single sub-task (checklist item) attached to a [Task].
///
/// ## Storage shape
/// Sub-tasks live in the `task_cache.steps` TEXT column (and the server's
/// encrypted `tasks.steps`) as a JSON **array of objects**:
///
/// ```json
/// [{"id": "<uuid>", "title": "Draft outline", "done": false}, ...]
/// ```
///
/// This is the SAME canonical shape the server emits from `_normalize_steps`
/// (`lazyclaw/tasks/store.py`: `{id, title, done}`), so a sub-task created on
/// mobile round-trips cleanly through the offline cache and the server's
/// `/api/tasks/changes` delta feed without re-shaping.
///
/// Two OPTIONAL timestamp keys ride along:
///
/// ```json
/// {"id": "…", "title": "…", "done": true,
///  "created_at": "2026-08-04T09:15:00.000000+00:00",
///  "completed_at": "2026-08-04T11:42:13.500000+00:00"}
/// ```
///
/// Both are OMITTED when null, so an ordinary checklist keeps its lean
/// `{id,title,done}` shape — exactly how the server carries its `cascaded`
/// marker (`_normalize_steps`: "Only carried when true, so an ordinary
/// checklist keeps its lean shape"). Sub-tasks created before this existed
/// have neither key; that is normal and PERMANENT — nothing backfills them,
/// because inventing a creation time we never observed would be a lie the UI
/// then renders as fact.
///
/// Parsing is deliberately tolerant (mirrors the server): a bare JSON string
/// entry is accepted as a title-only step, partial dicts fill missing fields,
/// and entries with an empty title are dropped. An empty list serialises back
/// to `null` so the column clears rather than storing a literal `"[]"`.
class Subtask {
  final String id;
  final String title;
  final bool done;

  /// When this sub-task was created, as an ISO-8601 UTC string, or null for a
  /// row that predates the field. Stamped once by the editor and then carried
  /// verbatim forever — the server does not rewrite a value the client
  /// already set, so the two agree instead of fighting over it.
  final String? createdAt;

  /// When [done] last became true, as an ISO-8601 UTC string. Null whenever
  /// [done] is false: un-ticking CLEARS it (see [copyWith]'s
  /// `clearCompletedAt`), so a stale completion time can't outlive the tick
  /// that produced it.
  final String? completedAt;

  const Subtask({
    required this.id,
    required this.title,
    required this.done,
    this.createdAt,
    this.completedAt,
  });

  /// Returns a copy with the given fields replaced.
  ///
  /// A null argument means "unchanged" for every field, so clearing the
  /// nullable [completedAt] needs the explicit [clearCompletedAt] flag — a
  /// plain `completedAt: null` is indistinguishable from "leave it alone".
  /// Mirrors `Task.copyWith`'s `clearDueDate` / `clearAllocatedBudget`.
  ///
  /// [createdAt] deliberately has NO clear flag: a creation time is only ever
  /// set once and never revoked, and an unused clear path is one more way to
  /// erase history by accident.
  Subtask copyWith({
    String? id,
    String? title,
    bool? done,
    String? createdAt,
    String? completedAt,
    bool clearCompletedAt = false,
  }) =>
      Subtask(
        id: id ?? this.id,
        title: title ?? this.title,
        done: done ?? this.done,
        createdAt: createdAt ?? this.createdAt,
        completedAt:
            clearCompletedAt ? null : (completedAt ?? this.completedAt),
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'done': done,
        if (createdAt != null) 'created_at': createdAt,
        if (completedAt != null) 'completed_at': completedAt,
      };

  /// Build a [Subtask] from a decoded map, tolerating loose/partial input.
  /// Mints a fresh id when one is absent. Returns null when the title is empty
  /// (caller drops it) so a half-typed row never persists as a blank step.
  static Subtask? fromMap(Map<dynamic, dynamic> map) {
    final title = (map['title'] ?? '').toString().trim();
    if (title.isEmpty) return null;
    final rawId = (map['id'] ?? '').toString().trim();
    return Subtask(
      id: rawId.isEmpty ? newSubtaskId() : rawId,
      title: title,
      done: _coerceDone(map['done']),
      createdAt: _coerceTimestamp(map['created_at']),
      completedAt: _coerceTimestamp(map['completed_at']),
    );
  }

  @override
  bool operator ==(Object other) =>
      other is Subtask &&
      other.id == id &&
      other.title == title &&
      other.done == done &&
      other.createdAt == createdAt &&
      other.completedAt == completedAt;

  // Timestamps MUST participate: the editor hands the parent a fresh list on
  // every mutation and the autosave layer diffs it against the last saved one.
  // Omitting them here would make an un-tick (done already false server-side,
  // only completed_at dropped) compare EQUAL to the stored row and never
  // persist.
  @override
  int get hashCode => Object.hash(id, title, done, createdAt, completedAt);

  @override
  String toString() => 'Subtask(id: $id, title: $title, done: $done, '
      'createdAt: $createdAt, completedAt: $completedAt)';
}

/// Mint a stable client-side sub-task id (RFC-4122 v4, prefixed for clarity).
String newSubtaskId() => 's-${uuidV4()}';

/// Mint a sub-task timestamp: the current instant as a UTC ISO-8601 string.
///
/// Byte-identical to how the client already stamps a task's own `created_at`
/// (`TaskDao._defaultNowIso`), which renders a trailing `Z` while the server's
/// `datetime.now(timezone.utc).isoformat()` renders `+00:00`. Both shapes are
/// first-class across the sync boundary by design — `sync_time.dart` compares
/// PARSED INSTANTS precisely so a client-minted `Z` and a server-stamped
/// `+00:00` for the same moment don't mis-rank.
///
/// The lexical-comparison hazard documented on `TaskSync._formatServerCursor`
/// does NOT apply here: these values live inside the encrypted `steps` blob
/// and are never used as a delta-feed cursor, so nothing string-compares them.
String subtaskNowIso() => DateTime.now().toUtc().toIso8601String();

/// Coerce a JSON `done` value into a bool, tolerating bool / int / string.
bool _coerceDone(dynamic v) {
  if (v is bool) return v;
  if (v is num) return v != 0;
  if (v is String) return v.toLowerCase() == 'true' || v == '1';
  return false;
}

/// Coerce a JSON timestamp into a validated ISO-8601 string, or null.
///
/// Absent, null, non-string and unparseable values all degrade to null rather
/// than throwing — a hand-edited or half-migrated blob must cost one missing
/// date, never the whole checklist.
///
/// The ACCEPTED string is returned verbatim rather than re-emitted from the
/// parsed `DateTime`: round-tripping through `toIso8601String()` would rewrite
/// the server's `+00:00` into Dart's `Z` and churn every row on the next sync
/// for no semantic change. `DateTime.tryParse` is used purely as a validator,
/// so its parse-naive-as-LOCAL behaviour is irrelevant here — the value it
/// produces is discarded.
String? _coerceTimestamp(dynamic v) {
  if (v is! String) return null;
  final trimmed = v.trim();
  if (trimmed.isEmpty) return null;
  return DateTime.tryParse(trimmed) == null ? null : trimmed;
}

/// Parse the `steps` column JSON string into a list of [Subtask].
///
/// Tolerant by design: returns `[]` for null / empty / non-list / malformed
/// input. Plain-string array entries become title-only steps (matching the
/// server's loose input handling); empty-title entries are skipped.
List<Subtask> parseSubtasks(String? raw) {
  if (raw == null) return const [];
  final trimmed = raw.trim();
  if (trimmed.isEmpty) return const [];

  dynamic decoded;
  try {
    decoded = jsonDecode(trimmed);
  } catch (_) {
    return const [];
  }
  if (decoded is! List) return const [];

  final out = <Subtask>[];
  for (final entry in decoded) {
    if (entry is String) {
      final title = entry.trim();
      if (title.isEmpty) continue;
      out.add(Subtask(id: newSubtaskId(), title: title, done: false));
    } else if (entry is Map) {
      final sub = Subtask.fromMap(entry);
      if (sub != null) out.add(sub);
    }
  }
  return out;
}

/// Serialise [subtasks] back into the `steps` column JSON string.
///
/// Returns `null` for an empty list so the column is cleared rather than
/// storing `"[]"` — matching how the server stores `None` for no steps.
String? serializeSubtasks(List<Subtask> subtasks) {
  if (subtasks.isEmpty) return null;
  return jsonEncode(subtasks.map((s) => s.toJson()).toList());
}

/// The `(done, total)` counts for a list of sub-tasks.
({int done, int total}) subtaskProgress(List<Subtask> subtasks) =>
    (done: subtasks.where((s) => s.done).length, total: subtasks.length);

/// A compact `done/total` progress label (e.g. `2/4`), or null when there are
/// no sub-tasks (so the card can omit the badge entirely).
String? subtaskProgressLabel(List<Subtask> subtasks) {
  if (subtasks.isEmpty) return null;
  final p = subtaskProgress(subtasks);
  return '${p.done}/${p.total}';
}
