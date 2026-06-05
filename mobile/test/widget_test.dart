// Smoke test: LazyClawApp boots without crashing.
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/main.dart';

void main() {
  testWidgets('LazyClawApp widget smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(
      const ProviderScope(child: LazyClawApp()),
    );
    // App renders without throwing.
    expect(tester.takeException(), isNull);
  });
}
