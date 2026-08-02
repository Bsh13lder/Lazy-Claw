import 'dart:convert';

import '../local/uuid.dart';

/// One comment in a task's thread. Lives in the `task_cache.comments` TEXT
/// column (and the server's encrypted `tasks.comments`) as a JSON array:
/// `[{"id","ts","author","text","subtask_id"}]` — the canonical shape the
/// server's `add_comment` emits. `subtask_id` null = task-level comment.
class TaskComment {
  final String id;
  final String ts;      // ISO-8601 UTC
  final String author;  // 'user' | 'agent'
  final String text;
  final String? subtaskId;

  const TaskComment({
    required this.id,
    required this.ts,
    required this.author,
    required this.text,
    this.subtaskId,
  });

  TaskComment copyWith({String? id, String? ts, String? author, String? text,
      String? subtaskId}) =>
      TaskComment(
        id: id ?? this.id,
        ts: ts ?? this.ts,
        author: author ?? this.author,
        text: text ?? this.text,
        subtaskId: subtaskId ?? this.subtaskId,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'ts': ts,
        'author': author,
        'text': text,
        'subtask_id': subtaskId,
      };

  static TaskComment? fromMap(Map<dynamic, dynamic> map) {
    final text = (map['text'] ?? '').toString().trim();
    if (text.isEmpty) return null;
    final author = (map['author'] ?? '').toString();
    final rawId = (map['id'] ?? '').toString().trim();
    final sub = (map['subtask_id'] ?? '').toString().trim();
    return TaskComment(
      id: rawId.isEmpty ? newCommentId() : rawId,
      ts: (map['ts'] ?? '').toString(),
      author: author == 'agent' ? 'agent' : 'user',
      text: text,
      subtaskId: sub.isEmpty ? null : sub,
    );
  }
}

/// Mint a stable client-side comment id (replays idempotently to the server).
String newCommentId() => 'c-${uuidV4()}';

/// Parse the `comments` column JSON. Tolerant: `[]` on null/garbage.
List<TaskComment> parseComments(String? raw) {
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
  final out = <TaskComment>[];
  for (final entry in decoded) {
    if (entry is Map) {
      final c = TaskComment.fromMap(entry);
      if (c != null) out.add(c);
    }
  }
  return out;
}

/// Serialise back to the column JSON. Empty list → null (column clears).
String? serializeComments(List<TaskComment> comments) {
  if (comments.isEmpty) return null;
  return jsonEncode(comments.map((c) => c.toJson()).toList());
}
