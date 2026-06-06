/// Immutable model mirroring the `Skill` interface in web/src/api.ts.
///
/// Shape from `GET /api/skills` → `{ skills: Skill[] }`:
///   id          String   — unique identifier
///   name        String   — human-readable name
///   description String   — what the skill does
///   skill_type  String   — "instruction" | "code" | "builtin" | "mcp" | …
///   enabled     bool     — whether the skill is active
///   category    String?  — optional grouping label (falls back to skill_type)
class Skill {
  final String id;
  final String name;
  final String description;
  final String skillType;
  final bool enabled;
  final String? category;

  const Skill({
    required this.id,
    required this.name,
    required this.description,
    required this.skillType,
    required this.enabled,
    this.category,
  });

  /// The display category; prefers [category] when set, falls back to
  /// [skillType].
  String get displayCategory => category ?? skillType;

  factory Skill.fromJson(Map<String, dynamic> json) {
    return Skill(
      id: _str(json['id']) ?? '',
      name: _str(json['name']) ?? '',
      description: _str(json['description']) ?? '',
      skillType: _str(json['skill_type']) ?? 'other',
      enabled: _bool(json['enabled']) ?? false,
      category: _str(json['category']),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'description': description,
        'skill_type': skillType,
        'enabled': enabled,
        'category': category,
      };

  Skill copyWith({
    String? id,
    String? name,
    String? description,
    String? skillType,
    bool? enabled,
    String? category,
  }) =>
      Skill(
        id: id ?? this.id,
        name: name ?? this.name,
        description: description ?? this.description,
        skillType: skillType ?? this.skillType,
        enabled: enabled ?? this.enabled,
        category: category ?? this.category,
      );

  @override
  bool operator ==(Object other) =>
      identical(this, other) || other is Skill && other.id == id;

  @override
  int get hashCode => id.hashCode;
}

// ── Private helpers ────────────────────────────────────────────────────────

String? _str(dynamic v) {
  if (v == null) return null;
  return v.toString();
}

bool? _bool(dynamic v) {
  if (v == null) return null;
  if (v is bool) return v;
  if (v is int) return v != 0;
  if (v is String) return v.toLowerCase() == 'true';
  return null;
}
