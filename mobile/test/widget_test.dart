// Smoke test: LazyClawApp boots without crashing.
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/main.dart';
import 'package:lazyclaw_mobile/providers/gateway_provider.dart';

void main() {
  testWidgets('LazyClawApp widget smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          // Seed the gateway with a loopback URL so the startup checkSession's
          // `/api/auth/me` fails fast (connection refused) instead of hanging on
          // a real-network connect to the default remote URL — which would leave
          // a pending timer past the 6s pump below.
          bootstrapBaseUrlProvider.overrideWithValue('http://127.0.0.1:18789'),
        ],
        child: const LazyClawApp(),
      ),
    );
    // Flush the startup checkSession's meTimeout timer (the /api/auth/me call
    // never completes in the test env — the meTimeout budget, currently 12s,
    // must fire and resolve through the offline-cache fallback) so no timer is
    // pending at teardown.
    await tester.pump(const Duration(seconds: 13));
    // App renders without throwing.
    expect(tester.takeException(), isNull);
  });
}
