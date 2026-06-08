/// Immutable model mirroring the /api/lazybrain/notes LazyBrainNote shape
/// from web/src/api.ts.
///
/// All fields are null/type-tolerant: external data is never trusted directly.
class Note {
  final String id;
  final String? title;
  final String content;
  final List<String> tags;
  final int importance;
  final bool pinned;
  final String? traceSessionId;
  final String? titleKey;
  final String createdAt;
  final String updatedAt;

  const Note({
    required this.id,
    this.title,
    required this.content,
    required this.tags,
    required this.importance,
    required this.pinned,
    this.traceSessionId,
    this.titleKey,
    required this.createdAt,
    required this.updatedAt,
  });

  // ── Derived helpers ────────────────────────────────────────────────────────

  /// Short preview of content for list tiles (max 120 chars).
  /// Strips a leading `# Heading` line when [title] is already present so the
  /// preview doesn't repeat the heading.
  String get contentPreview {
    var text = content;
    if (title != null && text.startsWith('#')) {
      final newline = text.indexOf('\n');
      if (newline != -1) {
        text = text.substring(newline + 1).trimLeft();
      } else {
        text = '';
      }
    }
    if (text.length <= 120) return text;
    return '${text.substring(0, 120)}…';
  }

  // ── Deserialisation ────────────────────────────────────────────────────────

  factory Note.fromJson(Map<String, dynamic> json) {
    return Note(
      id: _str(json['id']) ?? '',
      title: _str(json['title']),
      content: _str(json['content']) ?? '',
      tags: _strList(json['tags']),
      importance: _int(json['importance']) ?? 0,
      pinned: _bool(json['pinned']) ?? false,
      traceSessionId: _str(json['trace_session_id']),
      titleKey: _str(json['title_key']),
      createdAt: _str(json['created_at']) ?? '',
      updatedAt: _str(json['updated_at']) ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'content': content,
        'tags': tags,
        'importance': importance,
        'pinned': pinned,
        'trace_session_id': traceSessionId,
        'title_key': titleKey,
        'created_at': createdAt,
        'updated_at': updatedAt,
      };

  // ── Immutable copy ─────────────────────────────────────────────────────────

  Note copyWith({
    String? id,
    Object? title = _sentinel,
    String? content,
    List<String>? tags,
    int? importance,
    bool? pinned,
    Object? traceSessionId = _sentinel,
    Object? titleKey = _sentinel,
    String? createdAt,
    String? updatedAt,
  }) =>
      Note(
        id: id ?? this.id,
        title: title == _sentinel ? this.title : title as String?,
        content: content ?? this.content,
        tags: tags ?? this.tags,
        importance: importance ?? this.importance,
        pinned: pinned ?? this.pinned,
        traceSessionId: traceSessionId == _sentinel
            ? this.traceSessionId
            : traceSessionId as String?,
        titleKey:
            titleKey == _sentinel ? this.titleKey : titleKey as String?,
        createdAt: createdAt ?? this.createdAt,
        updatedAt: updatedAt ?? this.updatedAt,
      );

  // ── Equality ───────────────────────────────────────────────────────────────

  @override
  bool operator ==(Object other) =>
      identical(this, other) || other is Note && other.id == id;

  @override
  int get hashCode => id.hashCode;
}

// ── Sentinel for nullable copyWith fields ──────────────────────────────────
const Object _sentinel = Object();

// ── Private helpers ────────────────────────────────────────────────────────

String? _str(dynamic v) {
  if (v == null) return null;
  return v.toString();
}

int? _int(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  if (v is double) return v.toInt();
  return int.tryParse(v.toString());
}

bool? _bool(dynamic v) {
  if (v == null) return null;
  if (v is bool) return v;
  if (v is int) return v != 0;
  if (v is String) return v == 'true' || v == '1';
  return null;
}

List<String> _strList(dynamic v) {
  if (v == null) return const [];
  if (v is List) return v.map((e) => e.toString()).toList();
  return const [];
}
