import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/providers/settings_repo_provider.dart';
import 'package:lazyclaw_mobile/repositories/settings_repository.dart';
import 'package:lazyclaw_mobile/screens/chat/mode_switcher.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

// ── Fake transport ─────────────────────────────────────────────────────────
//
// Mirrors the seam used by the repository's own tests. Lets us drive the
// shared [agentModeProvider] through a real [SettingsRepository] without a Dio
// instance, and assert exactly what the chat-screen pill POSTs.

class _FakeTransport implements SettingsTransport {
  /// Current agent_mode the GET reports.
  String currentMode;

  /// If non-null, PATCH throws this (error path).
  Object? patchError;

  String? lastPatchPath;
  Map<String, dynamic>? lastPatchBody;
  int patchCount = 0;

  _FakeTransport({this.currentMode = 'ask', this.patchError});

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    return {
      'success': true,
      'data': {'agent_mode': currentMode},
    };
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    throw UnimplementedError('postJson not used by the mode switcher');
  }

  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async {
    patchCount++;
    lastPatchPath = path;
    lastPatchBody = body;
    if (patchError != null) {
      // Surface as the server's {success:false} envelope so the repo raises a
      // SettingsException — the exact shape the notifier translates to a string.
      return {'success': false, 'error': patchError.toString()};
    }
    currentMode = body['agent_mode'] as String;
    return {
      'success': true,
      'data': {'agent_mode': currentMode},
    };
  }
}

Widget _harness(SettingsRepository repo) {
  return ProviderScope(
    overrides: [
      settingsRepositoryProvider.overrideWithValue(repo),
    ],
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        appBar: AppBar(actions: const [ModeSwitcher()]),
        body: const SizedBox.shrink(),
      ),
    ),
  );
}

void main() {
  // ── Label mapping ─────────────────────────────────────────────────────────

  group('ModeSwitcher label', () {
    testWidgets('renders the label for the current stored mode', (t) async {
      // Stored value 'auto' must render as the "Execute" label.
      final repo = SettingsRepository(_FakeTransport(currentMode: 'auto'));
      await t.pumpWidget(_harness(repo));
      await t.pumpAndSettle();

      expect(find.text('Execute'), findsOneWidget);
      // The other labels are not shown until the sheet opens.
      expect(find.text('Action'), findsNothing);
    });

    testWidgets('falls back to the default label while loading', (t) async {
      // First frame before the GET resolves — shows the default 'ask' → "Action".
      final repo = SettingsRepository(_FakeTransport(currentMode: 'plan'));
      await t.pumpWidget(_harness(repo));
      // No settle: assert the pre-load fallback label.
      expect(find.text('Action'), findsOneWidget);

      // After settle it reflects the real stored mode.
      await t.pumpAndSettle();
      expect(find.text('Plan'), findsOneWidget);
    });
  });

  // ── Picking a mode (happy path) ───────────────────────────────────────────

  group('ModeSwitcher pick', () {
    testWidgets('opens the sheet, persists the pick, and confirms', (t) async {
      final transport = _FakeTransport(currentMode: 'ask');
      final repo = SettingsRepository(transport);
      await t.pumpWidget(_harness(repo));
      await t.pumpAndSettle();

      // Tap the pill to open the bottom sheet.
      await t.tap(find.byType(ModeSwitcher));
      await t.pumpAndSettle();

      // All four labels visible in the sheet.
      expect(find.text('Ask'), findsOneWidget);
      expect(find.text('Plan'), findsOneWidget);
      expect(find.text('Execute'), findsOneWidget);

      // Pick "Plan" (stored value 'plan').
      await t.tap(find.text('Plan'));
      await t.pumpAndSettle();

      // PATCHed the stored VALUE key, not the label.
      expect(transport.patchCount, 1);
      expect(transport.lastPatchPath, '/api/settings/general');
      expect(transport.lastPatchBody, {'agent_mode': 'plan'});

      // Confirming SnackBar + pill now reflects the new mode.
      expect(find.text('Mode set to Plan'), findsOneWidget);
      expect(find.text('Plan'), findsWidgets); // pill + (transient) snackbar
    });

    testWidgets('picking the already-selected mode is a no-op', (t) async {
      final transport = _FakeTransport(currentMode: 'plan');
      final repo = SettingsRepository(transport);
      await t.pumpWidget(_harness(repo));
      await t.pumpAndSettle();

      await t.tap(find.byType(ModeSwitcher));
      await t.pumpAndSettle();

      // Tap the already-current "Plan" row.
      await t.tap(find.text('Plan').last);
      await t.pumpAndSettle();

      // No PATCH, no SnackBar.
      expect(transport.patchCount, 0);
      expect(find.textContaining('Mode set to'), findsNothing);
    });
  });

  // ── Error path ────────────────────────────────────────────────────────────

  group('ModeSwitcher error', () {
    testWidgets('surfaces a failure without changing the pill', (t) async {
      final transport =
          _FakeTransport(currentMode: 'ask', patchError: 'backend down');
      final repo = SettingsRepository(transport);
      await t.pumpWidget(_harness(repo));
      await t.pumpAndSettle();

      await t.tap(find.byType(ModeSwitcher));
      await t.pumpAndSettle();

      await t.tap(find.text('Execute'));
      await t.pumpAndSettle();

      // Attempted the PATCH but it failed → error SnackBar, pill stays "Action".
      expect(transport.patchCount, 1);
      expect(find.textContaining('Could not change mode'), findsOneWidget);
      expect(find.text('Action'), findsOneWidget);
      expect(find.text('Execute'), findsNothing);
    });
  });
}
