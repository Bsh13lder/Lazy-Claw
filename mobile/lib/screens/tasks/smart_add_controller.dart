import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// A [TextEditingController] that paints recognized smart-add tokens inline, so
/// the field highlights dates, times, priority, and `#project` live as the user
/// types (Todoist/TickTick style).
///
/// The hosting widget re-parses on every change and pushes the fresh spans via
/// [tokens]; setting them notifies listeners and the field re-renders. The
/// controller never mutates the text — only its visual [buildTextSpan].
class SmartAddController extends TextEditingController {
  SmartAddController({super.text});

  List<SmartToken> _tokens = const [];

  /// The latest recognized token spans (indexing the current [text]).
  List<SmartToken> get tokens => _tokens;

  /// Replace the highlighted spans. No-ops (no rebuild) when unchanged; stores
  /// an unmodifiable copy so callers can't mutate our state.
  set tokens(List<SmartToken> value) {
    if (_sameSpans(_tokens, value)) return;
    _tokens = List.unmodifiable(value);
    notifyListeners();
  }

  @override
  TextSpan buildTextSpan({
    required BuildContext context,
    TextStyle? style,
    required bool withComposing,
  }) {
    final src = text;
    // Defer to the default rendering while an IME composition is active (keeps
    // the compose-region underline + input working) or when nothing matched.
    if (_tokens.isEmpty || (withComposing && value.composing.isValid)) {
      return super.buildTextSpan(
        context: context,
        style: style,
        withComposing: withComposing,
      );
    }

    final base = style ?? const TextStyle();
    final children = <TextSpan>[];
    var cursor = 0;
    for (final t in _tokens) {
      // Clamp defensively against spans that lag a keystroke behind the text.
      final start = t.start.clamp(0, src.length);
      final end = t.end.clamp(0, src.length);
      if (end <= start || start < cursor) continue;
      if (start > cursor) {
        children.add(TextSpan(text: src.substring(cursor, start), style: base));
      }
      children.add(
        TextSpan(
          text: src.substring(start, end),
          style: _styleFor(t, src, base),
        ),
      );
      cursor = end;
    }
    if (cursor < src.length) {
      children.add(TextSpan(text: src.substring(cursor), style: base));
    }
    return TextSpan(style: base, children: children);
  }

  // ── styling ────────────────────────────────────────────────────────────────

  TextStyle _styleFor(SmartToken t, String src, TextStyle base) {
    final c = _colorFor(t, src);
    return base.copyWith(
      color: c,
      fontWeight: FontWeight.w600,
      backgroundColor: c.withValues(alpha: 0.14),
    );
  }

  Color _colorFor(SmartToken t, String src) {
    switch (t.kind) {
      case SmartTokenKind.date:
      case SmartTokenKind.time:
      case SmartTokenKind.recurrence:
        return AppColors.accent;
      case SmartTokenKind.project:
        return AppColors.info;
      case SmartTokenKind.amount:
        return AppColors.success;
      case SmartTokenKind.priority:
        final start = t.start.clamp(0, src.length);
        final end = t.end.clamp(0, src.length);
        return _priorityColor(src.substring(start, end));
    }
  }

  /// Map a raw priority token (`!p1`, `!1`, `!!`, `!!!`) to its color.
  static Color _priorityColor(String raw) {
    final digit = RegExp(r'[1-4]').firstMatch(raw)?.group(0);
    switch (digit) {
      case '1':
        return AppColors.error; // urgent
      case '2':
        return AppColors.warn; // high
      case '3':
        return AppColors.info; // medium
      case '4':
        return AppColors.textMuted; // low
    }
    // No digit means this came from `_priorityBangs`, which only ever matches
    // 2 or 3 bangs (G1 #2 deleted the bare single-`!` match, so a one-bang
    // "medium" fallback can no longer reach here).
    final bangs = '!'.allMatches(raw).length;
    return bangs >= 3 ? AppColors.error : AppColors.warn; // urgent : high
  }

  static bool _sameSpans(List<SmartToken> a, List<SmartToken> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i].start != b[i].start ||
          a[i].end != b[i].end ||
          a[i].kind != b[i].kind) {
        return false;
      }
    }
    return true;
  }
}
