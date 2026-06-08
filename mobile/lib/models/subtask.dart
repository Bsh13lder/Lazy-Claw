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
/// Parsing is deliberately tolerant (mirrors the server): a bare JSON string
/// entry is accepted as a title-only step, partial dicts fill missing fields,
/// and entries with an empty title are dropped. An empty list serialises back
/// to `null` so the column clears rather than storing a literal `"[]"`.
class Subtask {
  final String id;
  final String title;
  final bool done;

  const Subtask({
    required this.id,
    required this.title,
    required this.done,
  });

  Subtask copyWith({String? id, String? title, bool? done}) => Subtask(
        id: id ?? this.id,
        title: title ?? this.title,
        done: done ?? this.done,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'done': done,
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
    );
  }

  @override
  bool operator ==(Object other) =>
      other is Subtask &&
      other.id == id &&
      other.title == title &&
      other.done == done;

  @override
  int get hashCode => Object.hash(id, title, done);

  @override
  String toString() => 'Subtask(id: $id, title: $title, done: $done)';
}

/// Mint a stable client-side sub-task id (RFC-4122 v4, prefixed for clarity).
String newSubtaskId() => 's-${uuidV4()}';

/// Coerce a JSON `done` value into a bool, tolerating bool / int / string.
bool _coerceDone(dynamic v) {
  if (v is bool) return v;
  if (v is num) return v != 0;
  if (v is String) return v.toLowerCase() == 'true' || v == '1';
  return false;
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
