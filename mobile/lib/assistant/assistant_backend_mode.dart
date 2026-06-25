/// Which tier "Hey Lazy" uses, mirroring Google's InferenceMode.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

enum AssistantBackendMode { onlyOnDevice, preferOnDevice, preferCloud }

const _wire = {
  AssistantBackendMode.onlyOnDevice: 'only_on_device',
  AssistantBackendMode.preferOnDevice: 'prefer_on_device',
  AssistantBackendMode.preferCloud: 'prefer_cloud',
};

String assistantModeToWire(AssistantBackendMode m) => _wire[m]!;

AssistantBackendMode assistantModeFromWire(String? v) {
  for (final e in _wire.entries) {
    if (e.value == v) return e.key;
  }
  return AssistantBackendMode.preferOnDevice;
}

const _kModeKey = 'assistant.backend_mode';

class AssistantModeController extends StateNotifier<AssistantBackendMode> {
  AssistantModeController(this._storage)
      : super(AssistantBackendMode.preferOnDevice) {
    _restore();
  }
  final FlutterSecureStorage _storage;

  Future<void> _restore() async {
    state = assistantModeFromWire(await _storage.read(key: _kModeKey));
  }

  Future<void> set(AssistantBackendMode m) async {
    state = m;
    await _storage.write(key: _kModeKey, value: assistantModeToWire(m));
  }
}

final assistantBackendModeProvider =
    StateNotifierProvider<AssistantModeController, AssistantBackendMode>(
  (_) => AssistantModeController(const FlutterSecureStorage()),
);
