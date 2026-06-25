import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/settings_repository.dart';

class _FakeT implements SettingsTransport {
  Map<String, dynamic>? lastPatch;
  @override
  Future<Map<String, dynamic>> getJson(String p) async => {
        'success': true,
        'data': {'agent_mode': 'ask', 'process_data_on_device': true},
      };
  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async =>
      {'success': true, 'data': {}};
  @override
  Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async {
    lastPatch = b;
    return {
      'success': true,
      'data': {...b},
    };
  }
}

void main() {
  test('parses process_data_on_device from general settings', () async {
    final r = SettingsRepository(_FakeT());
    final g = await r.getGeneral();
    expect(g.assistantProcessDataOnDevice, isTrue);
    expect(g.assistantConfirmCloudRequests, isTrue); // default true when absent
  });
  test('setAssistantFlags PATCHes only provided keys', () async {
    final t = _FakeT();
    await SettingsRepository(t).setAssistantFlags(processDataOnDevice: false);
    expect(t.lastPatch, {'process_data_on_device': false});
  });
}
