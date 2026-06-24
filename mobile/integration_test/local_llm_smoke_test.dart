// On-device smoke test for the local LLM pipeline.
//
// Proves, on the real phone, that `llamadart` can load a GGUF PUSHED into the
// app's external models dir and stream tokens — the load-bearing assumption of
// the whole on-device feature. Run with the Mi 15 connected:
//
//   flutter test integration_test/local_llm_smoke_test.dart -d <device-id>
//
// Prereq: the model must already be on the device (pushed over USB):
//   adb push Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
//     /sdcard/Android/data/com.lazyclaw.lazyclaw_mobile/files/models/
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import 'package:lazyclaw_mobile/local_ai/llamadart_engine.dart';
import 'package:lazyclaw_mobile/local_ai/local_llm_engine.dart';
import 'package:lazyclaw_mobile/local_ai/local_model.dart';
import 'package:lazyclaw_mobile/local_ai/local_model_store.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  test('llamadart loads the pushed Qwen3 GGUF and generates on-device',
      () async {
    final store = LocalModelStore();
    final model = localModelById('qwen3-4b-instruct-2507-q4_k_m')!;

    final status = await store.status(model);
    // ignore: avoid_print
    print('SMOKE: model present=${status.present} '
        'size=${status.sizeBytes} complete=${status.complete}');
    expect(status.present, isTrue,
        reason: 'Qwen3 GGUF not in the app models dir — push it over USB first');

    final path = (await store.resolvePath(model))!;
    final engine = LlamadartEngine();

    final loadStart = DateTime.now();
    await engine.load(model.id, path);
    expect(engine.isLoaded, isTrue);
    // ignore: avoid_print
    print('SMOKE: model loaded in '
        '${DateTime.now().difference(loadStart).inMilliseconds}ms');

    final buf = StringBuffer();
    final genStart = DateTime.now();
    await for (final tok in engine.generate([
      LocalLlmMessage.user('In one short sentence, say hello as LazyClaw.'),
    ])) {
      buf.write(tok);
      if (buf.length > 240) break; // enough to prove streaming works
    }
    final elapsed = DateTime.now().difference(genStart).inMilliseconds;
    await engine.unload();

    // ignore: avoid_print
    print('SMOKE: generated ${buf.length} chars in ${elapsed}ms');
    // ignore: avoid_print
    print('SMOKE_OUTPUT>>>${buf.toString()}<<<');
    expect(buf.toString().trim(), isNotEmpty, reason: 'no tokens generated');
  }, timeout: const Timeout(Duration(minutes: 6)));
}
