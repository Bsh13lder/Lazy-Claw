/// Token / cost metrics attached to a finished assistant turn.
///
/// Unions the two payload shapes the server emits:
///  - the standalone `usage` frame ({input_tokens, output_tokens,
///    total_tokens, cost, model}), and
///  - the `usage` key on the terminal `done` payload (the agent's
///    WorkSummary: {total_tokens, llm_calls, duration_ms, ...}).
/// Every field is optional — parse leniently, render only what's present.
class UsageInfo {
  final int? inputTokens;
  final int? outputTokens;
  final int? totalTokens;
  final int? llmCalls;
  final int? durationMs;
  final double? cost;
  final String? model;

  const UsageInfo({
    this.inputTokens,
    this.outputTokens,
    this.totalTokens,
    this.llmCalls,
    this.durationMs,
    this.cost,
    this.model,
  });

  bool get isEmpty =>
      inputTokens == null &&
      outputTokens == null &&
      totalTokens == null &&
      llmCalls == null &&
      durationMs == null &&
      cost == null &&
      model == null;

  static int? _int(dynamic v) =>
      v is num ? v.toInt() : (v is String ? int.tryParse(v) : null);

  static double? _double(dynamic v) =>
      v is num ? v.toDouble() : (v is String ? double.tryParse(v) : null);

  /// Lenient parse — returns null for non-map input or a map carrying no
  /// recognized metric, so callers can treat "no usage" uniformly.
  static UsageInfo? fromMap(dynamic raw) {
    if (raw is! Map) return null;
    final info = UsageInfo(
      inputTokens: _int(raw['input_tokens']),
      outputTokens: _int(raw['output_tokens']),
      totalTokens: _int(raw['total_tokens']),
      llmCalls: _int(raw['llm_calls']),
      durationMs: _int(raw['duration_ms']),
      cost: _double(raw['cost'] ?? raw['total_cost']),
      model: raw['model'] is String ? raw['model'] as String : null,
    );
    return info.isEmpty ? null : info;
  }

  /// Compact one-line summary for a caption footer, e.g.
  /// "1.2k tokens · 3 calls · 4.5s · $0.0123". Empty string when nothing set.
  String get summaryLine {
    final parts = <String>[
      if (totalTokens != null) '${_compact(totalTokens!)} tokens',
      if (llmCalls != null) '$llmCalls calls',
      if (durationMs != null) '${(durationMs! / 1000).toStringAsFixed(1)}s',
      if (cost != null) '\$${cost!.toStringAsFixed(4)}',
      if (model != null && model!.isNotEmpty) model!,
    ];
    return parts.join(' · ');
  }

  static String _compact(int n) =>
      n >= 1000 ? '${(n / 1000).toStringAsFixed(1)}k' : '$n';
}
