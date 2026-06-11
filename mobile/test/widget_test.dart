// Smoke test: LazyClawApp boots without crashing.
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/main.dart';

void main() {
  testWidgets('LazyClawApp widget smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(
      const ProviderScope(child: LazyClawApp()),
    );
    // Flush the startup checkSession's meTimeout timer (the /api/auth/me call
    // never completes in the test env — the 5s budget must fire and resolve
    // through the offline-cache fallback) so no timer is pending at teardown.
    await tester.pump(const Duration(seconds: 6));
    // App renders without throwing.
    expect(tester.takeException(), isNull);
  });
}
