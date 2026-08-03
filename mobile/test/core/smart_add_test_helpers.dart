// Shared assertion helper for smart-add parser fixture tests.
//
// `SmartAddController.buildTextSpan` (mobile/lib/screens/tasks/
// smart_add_controller.dart:52) SILENTLY DROPS any token with
// `end <= start || start < cursor` — a malformed span never crashes, it just
// vanishes from the highlighted UI. The parser test suite's strongest span
// assertion (`spans are sorted by start and never overlap`) still passes even
// when a span like that is produced, so nothing today catches it. Call this
// after every `parseSmartAdd` in a fixture-driven test to close that gap.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';

/// Asserts [parsed]'s token spans (as parsed from [input]) are well-formed:
/// each is non-empty and in-bounds, and the whole list is ascending by
/// `start` with no two spans overlapping.
void expectWellFormedSpans(String input, ParsedTask parsed) {
  var cursor = 0;
  for (final t in parsed.tokens) {
    expect(
      t.start,
      inInclusiveRange(0, input.length),
      reason: 'token start out of bounds for "$input": $t',
    );
    expect(
      t.end,
      inInclusiveRange(t.start + 1, input.length),
      reason: 'token end out of bounds (or empty span) for "$input": $t',
    );
    expect(
      t.start,
      greaterThanOrEqualTo(cursor),
      reason:
          'tokens must be ascending and non-overlapping for "$input": '
          '$t starts before the previous token ended (cursor=$cursor)',
    );
    cursor = t.end;
  }
}
