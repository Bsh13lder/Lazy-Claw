import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/assistant_backend_mode.dart';

void main() {
  test('wire round-trips every mode', () {
    for (final m in AssistantBackendMode.values) {
      expect(assistantModeFromWire(assistantModeToWire(m)), m);
    }
  });
  test('unknown wire value coerces to preferOnDevice', () {
    expect(assistantModeFromWire(null), AssistantBackendMode.preferOnDevice);
    expect(assistantModeFromWire('garbage'), AssistantBackendMode.preferOnDevice);
  });
}
