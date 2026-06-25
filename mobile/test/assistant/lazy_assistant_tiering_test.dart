import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/assistant_backend_mode.dart';
import 'package:lazyclaw_mobile/assistant/cloud_turn_client.dart';
import 'package:lazyclaw_mobile/assistant/lazy_assistant_controller.dart';
import 'package:lazyclaw_mobile/local_ai/local_llm_engine.dart';

class _FakeEngine implements LocalLlmEngine {
  _FakeEngine(this._reply);
  final String _reply;
  @override
  bool get isLoaded => true;
  @override
  String? get loadedModelId => 'fake';
  @override
  Future<void> load(String id, String path) async {}
  @override
  Future<void> unload() async {}
  @override
  Stream<String> generate(List<LocalLlmMessage> m, {String? systemPrompt}) async* {
    yield _reply;
  }
}

class _FakeCloud implements CloudTurns {
  _FakeCloud(this._reply);
  final String _reply;
  @override
  Stream<String> streamTurn(String t) async* {
    yield _reply;
  }
}

void main() {
  // The controller eagerly constructs FlutterTts/SpeechToText (which register
  // platform method-call handlers) in its field initializers, so the test
  // binding must be live before any controller is built.
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Auto + plain chat → local (TurnSource.onDevice)', () async {
    final c = LazyAssistantController(
      _FakeEngine('a metaphor'),
      _FakeCloud('CLOUD'),
      () => AssistantBackendMode.preferOnDevice,
      () => false,
      ensureCloud: () async => true,
    );
    await c.askForTest('tell me a metaphor');
    expect(c.debugState.source, TurnSource.onDevice);
    expect(c.debugState.response, contains('metaphor'));
  });

  test('Auto + action verb → cloud (TurnSource.cloud)', () async {
    final c = LazyAssistantController(
      _FakeEngine('LOCAL'),
      _FakeCloud('Logged 12 euros to groceries'),
      () => AssistantBackendMode.preferOnDevice,
      () => false,
      ensureCloud: () async => true,
    );
    await c.askForTest('log a 12 euro expense for groceries');
    expect(c.debugState.source, TurnSource.cloud);
    expect(c.debugState.response, contains('Logged'));
  });

  test('processDataOnDevice forces local even for an action', () async {
    final c = LazyAssistantController(
      _FakeEngine("I can't do that on-device"),
      _FakeCloud('CLOUD'),
      () => AssistantBackendMode.preferOnDevice,
      () => true,
      ensureCloud: () async => true,
    );
    await c.askForTest('send a message to John');
    expect(c.debugState.source, TurnSource.onDevice);
  });

  group('first-cloud-hop consent', () {
    test('confirmCloud + consent-not-given pauses before the cloud send', () async {
      var consentGiven = false;
      var marked = 0;
      final c = LazyAssistantController(
        _FakeEngine('LOCAL'),
        _FakeCloud('Logged 12 euros to groceries'),
        () => AssistantBackendMode.preferOnDevice,
        () => false,
        ensureCloud: () async => true,
        readConfirmCloud: () => true,
        readConsentGiven: () => consentGiven,
        markConsentGiven: () {
          consentGiven = true;
          marked += 1;
        },
      );
      await c.askForTest('log a 12 euro expense for groceries');
      // It must pause, NOT send to the cloud.
      expect(c.debugState.phase, AssistantPhase.awaitingCloudConsent);
      expect(c.pendingCloudPrompt, 'log a 12 euro expense for groceries');
      expect(c.debugState.response, isEmpty);
      expect(marked, 0);
    });

    test('approveCloudOnce marks consent and runs the cloud turn', () async {
      var consentGiven = false;
      final c = LazyAssistantController(
        _FakeEngine('LOCAL'),
        _FakeCloud('Logged 12 euros to groceries'),
        () => AssistantBackendMode.preferOnDevice,
        () => false,
        ensureCloud: () async => true,
        readConfirmCloud: () => true,
        readConsentGiven: () => consentGiven,
        markConsentGiven: () => consentGiven = true,
      );
      await c.askForTest('log a 12 euro expense for groceries');
      expect(c.debugState.phase, AssistantPhase.awaitingCloudConsent);
      await c.approveCloudOnce();
      expect(consentGiven, isTrue);
      expect(c.debugState.source, TurnSource.cloud);
      expect(c.debugState.response, contains('Logged'));
      expect(c.pendingCloudPrompt, isNull);
    });

    test('denyCloud falls back to the local engine', () async {
      final c = LazyAssistantController(
        _FakeEngine('Keeping this on your phone'),
        _FakeCloud('CLOUD'),
        () => AssistantBackendMode.preferOnDevice,
        () => false,
        ensureCloud: () async => true,
        readConfirmCloud: () => true,
        readConsentGiven: () => false,
        markConsentGiven: () {},
      );
      await c.askForTest('log a 12 euro expense for groceries');
      expect(c.debugState.phase, AssistantPhase.awaitingCloudConsent);
      await c.denyCloud();
      expect(c.debugState.source, TurnSource.onDevice);
      expect(c.pendingCloudPrompt, isNull);
    });

    test('consent already given skips the gate', () async {
      final c = LazyAssistantController(
        _FakeEngine('LOCAL'),
        _FakeCloud('Logged 12 euros to groceries'),
        () => AssistantBackendMode.preferOnDevice,
        () => false,
        ensureCloud: () async => true,
        readConfirmCloud: () => true,
        readConsentGiven: () => true,
        markConsentGiven: () {},
      );
      await c.askForTest('log a 12 euro expense for groceries');
      expect(c.debugState.source, TurnSource.cloud);
      expect(c.debugState.response, contains('Logged'));
    });

    test('confirmCloud off skips the gate entirely', () async {
      final c = LazyAssistantController(
        _FakeEngine('LOCAL'),
        _FakeCloud('Logged 12 euros to groceries'),
        () => AssistantBackendMode.preferOnDevice,
        () => false,
        ensureCloud: () async => true,
        readConfirmCloud: () => false,
        readConsentGiven: () => false,
        markConsentGiven: () {},
      );
      await c.askForTest('log a 12 euro expense for groceries');
      expect(c.debugState.source, TurnSource.cloud);
      expect(c.debugState.response, contains('Logged'));
    });
  });
}
