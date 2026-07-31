// Unit tests for the pure gate deciding whether the Ledger's inbox
// bulk-selection mode should exit. Backs a fix (fix round 1) for a stuck-
// selection-mode bug: comparing `projectFilter != inboxProjectId` directly
// null-collapses to `false` when the inbox project is deleted (both sides
// become null), so selection mode never exited and every row across the
// whole Ledger stayed stuck toggling selection instead of opening the detail
// sheet, with no bulk bar (and no ✕ cancel) left to escape through.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/expenses/selection_mode.dart';

void main() {
  group('shouldExitSelection', () {
    test(
        'null-collapsing case: inbox project deleted (both filter and inbox '
        'id are null) exits selection mode', () {
      final result = shouldExitSelection(
        selectionMode: true,
        projectFilter: null,
        inboxProjectId: null,
      );
      expect(result, isTrue);
    });

    test('active case: filter equals a non-null inbox id stays in selection',
        () {
      final result = shouldExitSelection(
        selectionMode: true,
        projectFilter: 'inbox-1',
        inboxProjectId: 'inbox-1',
      );
      expect(result, isFalse);
    });

    test('filter moved to a different (non-inbox) project exits selection',
        () {
      final result = shouldExitSelection(
        selectionMode: true,
        projectFilter: 'other-project',
        inboxProjectId: 'inbox-1',
      );
      expect(result, isTrue);
    });

    test('filter cleared to "All" while inbox still exists exits selection',
        () {
      final result = shouldExitSelection(
        selectionMode: true,
        projectFilter: null,
        inboxProjectId: 'inbox-1',
      );
      expect(result, isTrue);
    });

    test('not in selection mode never signals exit, regardless of filters',
        () {
      final result = shouldExitSelection(
        selectionMode: false,
        projectFilter: null,
        inboxProjectId: null,
      );
      expect(result, isFalse);
    });

    test('not in selection mode stays false even when filter is stale', () {
      final result = shouldExitSelection(
        selectionMode: false,
        projectFilter: 'other-project',
        inboxProjectId: 'inbox-1',
      );
      expect(result, isFalse);
    });
  });
}
