// Unit tests for the pure helper that narrows AI inbox suggestions down to
// the ones worth a one-tap bulk apply — backs the Ledger's "Apply N
// confident" button (Task 5 of the expense-inbox Phase 3 mobile plan).

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/inbox_suggestion.dart';

InboxSuggestion _suggestion({
  String expenseId = 'e1',
  String? projectId = 'p1',
  String confidence = 'high',
}) =>
    InboxSuggestion(
      expenseId: expenseId,
      projectId: projectId,
      projectName: projectId == null ? null : 'Project $projectId',
      confidence: confidence,
    );

void main() {
  group('confidentSuggestions', () {
    test('keeps a high-confidence matched suggestion', () {
      final s = _suggestion(confidence: 'high');
      expect(confidentSuggestions([s]), [s]);
    });

    test('keeps a medium-confidence matched suggestion', () {
      final s = _suggestion(confidence: 'medium');
      expect(confidentSuggestions([s]), [s]);
    });

    test('drops a low-confidence suggestion', () {
      final s = _suggestion(confidence: 'low');
      expect(confidentSuggestions([s]), isEmpty);
    });

    test('drops a "none" confidence suggestion', () {
      final s = _suggestion(confidence: 'none');
      expect(confidentSuggestions([s]), isEmpty);
    });

    test('drops a suggestion with a null projectId even at high confidence', () {
      final s = _suggestion(projectId: null, confidence: 'high');
      expect(confidentSuggestions([s]), isEmpty);
    });

    test('filters a mixed list down to only the confident, matched ones', () {
      final keepHigh = _suggestion(expenseId: 'e1', confidence: 'high');
      final keepMedium = _suggestion(expenseId: 'e2', confidence: 'medium');
      final dropLow = _suggestion(expenseId: 'e3', confidence: 'low');
      final dropNoMatch =
          _suggestion(expenseId: 'e4', projectId: null, confidence: 'high');
      final dropNone = _suggestion(expenseId: 'e5', confidence: 'none');

      final result = confidentSuggestions(
        [keepHigh, dropLow, keepMedium, dropNoMatch, dropNone],
      );

      expect(result.map((s) => s.expenseId), ['e1', 'e2']);
    });

    test('empty list yields an empty result', () {
      expect(confidentSuggestions(const []), isEmpty);
    });
  });
}
