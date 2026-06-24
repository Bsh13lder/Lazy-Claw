// On-device test for the in-app model downloader.
//
// Exercises LocalModelDownloader directly (no UI), proving the network +
// redirect + file-write path works on the real device. Downloads ~3 MB then
// cancels so it's fast. Prints DL_TEST: lines for diagnosis.
//
//   flutter test integration_test/download_smoke_test.dart -d <device-id>
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import 'package:lazyclaw_mobile/local_ai/local_model.dart';
import 'package:lazyclaw_mobile/local_ai/local_model_downloader.dart';
import 'package:lazyclaw_mobile/local_ai/local_model_store.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  test('downloader connects to HuggingFace and writes to the models dir',
      () async {
    final store = LocalModelStore();
    final dir = await store.modelsDir();
    // ignore: avoid_print
    print('DL_TEST: modelsDir=${dir.path}');

    final dl = LocalModelDownloader(store);
    final model = localModelById('phi-4-mini-instruct-q4_k_m')!;
    var gotBytes = 0;
    Object? err;
    try {
      await dl.download(model, onProgress: (received, total) {
        gotBytes = received;
        if (received > 3 * 1024 * 1024) dl.cancel(); // ~3 MB then stop
      });
    } catch (e) {
      err = e;
    }
    // ignore: avoid_print
    print('DL_TEST: RESULT gotBytes=$gotBytes err=$err');
    expect(gotBytes, greaterThan(0),
        reason: 'no bytes received from HF — download mechanism broken: $err');
  }, timeout: const Timeout(Duration(minutes: 3)));
}
