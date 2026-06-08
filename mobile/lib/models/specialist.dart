/// Immutable model for an agent specialist.
///
/// Mirrors the `GET /api/specialists` envelope
/// (`{ ok, specialists: [Specialist] }`) defined by ADR-0005
/// (specialist-first dispatch):
///   name            String   — unique identifier (frontmatter `name`)
///   display_name    String   — human-readable label (falls back to [name])
///   system_prompt   String   — the markdown body / system prompt
///   tools           [String] — allowed tool/skill names
///   model           String?  — preferred model alias (null = mode default)
///   include_scraper bool     — whether the scraper tool is injected
///   is_builtin      bool     — true for in-repo builtins (read-only)
class Specialist {
  final String name;
  final String displayName;
  final String systemPrompt;
  final List<String> tools;
  final String? model;
  final bool includeScraper;
  final bool isBuiltin;

  const Specialist({
    required this.name,
    required this.displayName,
    required this.systemPrompt,
    this.tools = const [],
    this.model,
    this.includeScraper = false,
    this.isBuiltin = false,
  });

  factory Specialist.fromJson(Map<String, dynamic> json) {
    final name = (json['name'] ?? '').toString();
    final display = (json['display_name'] ?? '').toString();
    final rawTools = json['tools'];
    final tools = rawTools is List
        ? rawTools.map((e) => e.toString()).toList(growable: false)
        : const <String>[];
    return Specialist(
      name: name,
      displayName: display.isNotEmpty ? display : name,
      systemPrompt: (json['system_prompt'] ?? '').toString(),
      tools: tools,
      model: _nullableStr(json['model']),
      includeScraper: _bool(json['include_scraper']),
      isBuiltin: _bool(json['is_builtin']),
    );
  }

  Map<String, dynamic> toJson() => {
        'name': name,
        'display_name': displayName,
        'system_prompt': systemPrompt,
        'tools': tools,
        'model': model,
        'include_scraper': includeScraper,
        'is_builtin': isBuiltin,
      };

  Specialist copyWith({
    String? name,
    String? displayName,
    String? systemPrompt,
    List<String>? tools,
    String? model,
    bool? includeScraper,
    bool? isBuiltin,
  }) =>
      Specialist(
        name: name ?? this.name,
        displayName: displayName ?? this.displayName,
        systemPrompt: systemPrompt ?? this.systemPrompt,
        tools: tools ?? this.tools,
        model: model ?? this.model,
        includeScraper: includeScraper ?? this.includeScraper,
        isBuiltin: isBuiltin ?? this.isBuiltin,
      );

  @override
  bool operator ==(Object other) =>
      identical(this, other) || other is Specialist && other.name == name;

  @override
  int get hashCode => name.hashCode;
}

// ── Private helpers ──────────────────────────────────────────────────────────

/// Returns null for a null value, else the string form (preserving the
/// distinction between "no model" and an explicit alias).
String? _nullableStr(dynamic v) => v?.toString();

bool _bool(dynamic v) {
  if (v is bool) return v;
  if (v is int) return v != 0;
  if (v is String) return v.toLowerCase() == 'true';
  return false;
}
