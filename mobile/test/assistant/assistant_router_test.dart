import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/assistant_backend_mode.dart';
import 'package:lazyclaw_mobile/assistant/assistant_router.dart';

void main() {
  const r = AssistantRouter();
  const auto = AssistantBackendMode.preferOnDevice;

  group('Stage 0 — hard gates', () {
    test('onlyOnDevice never escalates', () {
      expect(
        r.decide(utterance: 'send a message to John', mode: AssistantBackendMode.onlyOnDevice, processDataOnDevice: false),
        AssistantRoute.local);
    });
    test('processDataOnDevice forces local', () {
      expect(
        r.decide(utterance: 'what is the weather today', mode: auto, processDataOnDevice: true),
        AssistantRoute.local);
    });
    test('offline forces local', () {
      expect(
        r.decide(utterance: 'search the web', mode: auto, processDataOnDevice: false, device: const DeviceState(online: false)),
        AssistantRoute.local);
    });
    test('battery critical forces local', () {
      expect(
        r.decide(utterance: 'add a task', mode: auto, processDataOnDevice: false, device: const DeviceState(batteryCritical: true)),
        AssistantRoute.local);
    });
    test('preferCloud goes cloud when online', () {
      expect(
        r.decide(utterance: 'hello', mode: AssistantBackendMode.preferCloud, processDataOnDevice: false),
        AssistantRoute.cloud);
    });
  });

  group('Stage 1 — escalate on need (Auto)', () {
    for (final u in [
      'add milk to my shopping list',
      'log a 20 euro expense for lunch',
      'remind me to call mom at six',
      'send a message to John',
      "what's the weather in Madrid today",
      'who won the match last night',
      'search the web for the price of bitcoin',
      'email Sara the invoice',
    ]) {
      test('CLOUD: "$u"', () {
        expect(r.decide(utterance: u, mode: auto, processDataOnDevice: false), AssistantRoute.cloud);
      });
    }
    test('Spanish action verb escalates', () {
      expect(r.decide(utterance: 'añade leche a la lista', mode: auto, processDataOnDevice: false), AssistantRoute.cloud);
    });
    test('long context escalates', () {
      expect(r.decide(utterance: 'summarize this', mode: auto, processDataOnDevice: false, approxContextTokens: 5000), AssistantRoute.cloud);
    });
  });

  group('Stage 2 — plain chat stays local', () {
    for (final u in [
      "what's a good metaphor for patience",
      'tell me a short joke',
      "translate 'good morning' to Spanish",
      'how are you',
    ]) {
      test('LOCAL: "$u"', () {
        expect(r.decide(utterance: u, mode: auto, processDataOnDevice: false), AssistantRoute.local);
      });
    }
  });
}
