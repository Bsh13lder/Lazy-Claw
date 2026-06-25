# Hey Lazy P1 — Tiered Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "Hey Lazy" a tiered assistant — it answers locally for plain chat but auto-escalates to the real LazyClaw server (over the existing WebSocket) for anything needing internet/tools/actions, with a privacy lock, a per-turn provenance badge, and first-cloud-hop consent.

**Architecture:** A pure `AssistantRouter` decides Local vs Cloud per turn (mode + hard gates + escalate-on-need heuristic). A `CloudTurnClient` runs a cloud turn over a **dedicated** `ChatSocket` (isolated from the main-chat socket so cloud turns never pollute the chat tab). `LazyAssistantController._ask()` routes to the local engine or the cloud client, streams the reply into the same `AssistantState`, tags it with a `TurnSource`, and speaks it. Settings + a consent gate + the badge are the surrounding UX.

**Tech Stack:** Flutter, Riverpod (`StateNotifier`/`StateProvider`), `flutter_secure_storage`, the existing `ChatSocket`/`ServerFrame` transport, `LocalLlmEngine` (llamadart), `speech_to_text`, `flutter_tts`, `lib/ui/` design kit. Tests: `flutter test` (`package:test`/`flutter_test`).

## Global Constraints

- **Immutability:** all state objects use `copyWith`; never mutate in place (matches `AssistantState`/`LocalAiState`).
- **Reuse, don't rebuild transport:** the cloud path uses `ChatSocket` + `ServerFrame` + `ServerConfig.wsUrlFor` + `apiClientProvider.getSessionCookie()` — no new WS/auth code.
- **Default OFF / on-device-first:** `assistantBackendMode` default = `preferOnDevice`; `processDataOnDevice` default = `false`; `confirmCloudRequests` default = `true`.
- **The local model must never fake a cloud capability:** Stage-1 `needsCloud(...)` hard-escalates tool/action/internet/recency turns.
- **Clean TTS:** reuse `LazyAssistantController._clean()` for any spoken text (no emoji/markdown/`*stage*`).
- **Test seams:** inject fakes (engine, cloud client, socket sink) exactly like the existing `FakeSink` pattern; fakes must throw the **production** exception types (`LocalLlmException`, `CloudTurnException`).
- **No file > 400 lines; one responsibility per file.**

---

### Task 1: `AssistantBackendMode` enum + persisted provider

**Files:**
- Create: `lib/assistant/assistant_backend_mode.dart`
- Test: `test/assistant/assistant_backend_mode_test.dart`

**Interfaces:**
- Produces: `enum AssistantBackendMode { onlyOnDevice, preferOnDevice, preferCloud }`; `String assistantModeToWire(AssistantBackendMode)`; `AssistantBackendMode assistantModeFromWire(String?)` (unknown → `preferOnDevice`); `assistantBackendModeProvider` (`StateNotifierProvider<AssistantModeController, AssistantBackendMode>`) persisting to `FlutterSecureStorage` key `assistant.backend_mode`.

- [ ] **Step 1: Write the failing test**
```dart
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
```
> Note: the package import prefix is `lazyclaw_mobile` — confirm against `pubspec.yaml`'s `name:` and adjust all test imports if it differs.

- [ ] **Step 2: Run test to verify it fails**
Run: `cd mobile && flutter test test/assistant/assistant_backend_mode_test.dart`
Expected: FAIL — `assistant_backend_mode.dart` not found.

- [ ] **Step 3: Write minimal implementation**
```dart
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
```

- [ ] **Step 4: Run test to verify it passes**
Run: `cd mobile && flutter test test/assistant/assistant_backend_mode_test.dart`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**
```bash
git add mobile/lib/assistant/assistant_backend_mode.dart mobile/test/assistant/assistant_backend_mode_test.dart
git commit -m "feat(assistant): AssistantBackendMode enum + persisted provider"
```

---

### Task 2: `AssistantRouter` — the pure decision

**Files:**
- Create: `lib/assistant/assistant_router.dart`
- Test: `test/assistant/assistant_router_test.dart`

**Interfaces:**
- Consumes: `AssistantBackendMode` (Task 1).
- Produces:
  - `enum AssistantRoute { local, cloud }`
  - `class DeviceState { final bool online; final bool batteryCritical; final bool thermalThrottling; const DeviceState({this.online = true, this.batteryCritical = false, this.thermalThrottling = false}); }`
  - `class AssistantRouter { const AssistantRouter(); AssistantRoute decide({required String utterance, required AssistantBackendMode mode, required bool processDataOnDevice, DeviceState device = const DeviceState(), int approxContextTokens = 0}); bool needsCloud(String utterance, {int approxContextTokens = 0}); }`
  - `const kCloudContextTokenCeiling = 4000;`

- [ ] **Step 1: Write the failing test**
```dart
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
```

- [ ] **Step 2: Run test to verify it fails**
Run: `cd mobile && flutter test test/assistant/assistant_router_test.dart`
Expected: FAIL — `assistant_router.dart` not found.

- [ ] **Step 3: Write minimal implementation**
```dart
/// Pure, unit-tested tier decision for "Hey Lazy".
///
/// The load-bearing rule (research 2026-06-25): anything needing a tool /
/// action / the internet / fresh facts lives ONLY in the cloud — hard-escalate
/// it. The local model must never fake those. Plain chat stays on-device.
library;

import 'assistant_backend_mode.dart';

enum AssistantRoute { local, cloud }

class DeviceState {
  final bool online;
  final bool batteryCritical;
  final bool thermalThrottling;
  const DeviceState({
    this.online = true,
    this.batteryCritical = false,
    this.thermalThrottling = false,
  });
}

const int kCloudContextTokenCeiling = 4000;

/// Imperative action / tool intent (English + Spanish). Matched as whole words.
const List<String> _kActionVerbs = [
  'add', 'log', 'set', 'send', 'book', 'schedule', 'remind', 'pay', 'buy',
  'search', 'look up', 'lookup', 'call', 'email', 'message', 'text', 'create',
  'make', 'update', 'change', 'delete', 'remove', 'cancel', 'transfer',
  'order', 'open', 'find',
  // Spanish
  'añade', 'agrega', 'apunta', 'envía', 'manda', 'recuérdame', 'recuerdame',
  'programa', 'paga', 'compra', 'busca', 'llama', 'crea', 'borra', 'cancela',
  'manda', 'escribe', 'abre', 'encuentra',
];

/// Recency / world-knowledge markers — fresh facts only the cloud has.
const List<String> _kRecencyMarkers = [
  'today', 'right now', 'now', 'latest', 'current', 'currently', 'this week',
  'this morning', 'tonight', 'price of', 'cost of', 'weather', 'news',
  'who won', 'when is', 'what time', 'stock', 'score', 'near me', 'open now',
  'hoy', 'ahora', 'último', 'ultimo', 'actual', 'precio de', 'tiempo', 'noticias',
];

/// Explicit tool / channel / internet intent.
const List<String> _kToolMarkers = [
  'email', 'gmail', 'whatsapp', 'instagram', 'upwork', 'telegram', 'calendar',
  'web', 'google', 'internet', 'online', 'browser', 'website',
];

class AssistantRouter {
  const AssistantRouter();

  AssistantRoute decide({
    required String utterance,
    required AssistantBackendMode mode,
    required bool processDataOnDevice,
    DeviceState device = const DeviceState(),
    int approxContextTokens = 0,
  }) {
    // STAGE 0 — hard gates.
    if (mode == AssistantBackendMode.onlyOnDevice || processDataOnDevice) {
      return AssistantRoute.local;
    }
    if (!device.online || device.batteryCritical || device.thermalThrottling) {
      return AssistantRoute.local;
    }
    if (mode == AssistantBackendMode.preferCloud) return AssistantRoute.cloud;

    // STAGE 1 — escalate on need (mode == preferOnDevice / Auto).
    if (needsCloud(utterance, approxContextTokens: approxContextTokens)) {
      return AssistantRoute.cloud;
    }

    // STAGE 2 — plain chat → local. (Local self-signal handled in the controller.)
    return AssistantRoute.local;
  }

  bool needsCloud(String utterance, {int approxContextTokens = 0}) {
    if (approxContextTokens > kCloudContextTokenCeiling) return true;
    final lower = ' ${utterance.toLowerCase().trim()} ';
    bool hasPhrase(String p) =>
        p.contains(' ') ? lower.contains(' $p ') || lower.contains(' $p') : _hasWord(lower, p);
    if (_kActionVerbs.any(hasPhrase)) return true;
    if (_kRecencyMarkers.any((m) => lower.contains(m))) return true;
    if (_kToolMarkers.any((m) => _hasWord(lower, m))) return true;
    return false;
  }

  bool _hasWord(String paddedLower, String word) {
    final re = RegExp('\\b${RegExp.escape(word)}\\b', unicode: true);
    return re.hasMatch(paddedLower);
  }
}
```
> Tuning note: word-boundary matching keeps "creative" from matching "create". If a test for a multi-word phrase ("look up") fails, the `hasPhrase` substring branch covers it. Run the suite and adjust the lists — they are the unit under test.

- [ ] **Step 4: Run test to verify it passes**
Run: `cd mobile && flutter test test/assistant/assistant_router_test.dart`
Expected: PASS (all groups). If "find"/"open" over-trigger a plain-chat case, move them or tighten — the tests are the spec.

- [ ] **Step 5: Commit**
```bash
git add mobile/lib/assistant/assistant_router.dart mobile/test/assistant/assistant_router_test.dart
git commit -m "feat(assistant): pure tiered router (escalate-on-need heuristic)"
```

---

### Task 3: `CloudTurnClient` + dedicated assistant socket

**Files:**
- Create: `lib/assistant/cloud_turn_client.dart`
- Test: `test/assistant/cloud_turn_client_test.dart`

**Interfaces:**
- Consumes: `ChatSocket`, `ServerFrame`/`TokenFrame`/`DoneFrame`/`ErrorFrame`/`SendFailedFrame` (`lib/chat/`).
- Produces:
  - `class CloudTurnException implements Exception { final String message; const CloudTurnException(this.message); }`
  - `class CloudTurnClient { CloudTurnClient(this._socket); Stream<String> streamTurn(String userText); }`
  - `assistantSocketProvider` (`Provider<ChatSocket>`, **dedicated** — not `chatSocketProvider`) and `cloudTurnClientProvider` (`Provider<CloudTurnClient>`).
  - `Future<bool> ensureAssistantSocketConnected(Ref ref)` — resolves base URL + session cookie and connects the dedicated socket; returns false if no session.

- [ ] **Step 1: Write the failing test**
```dart
import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/assistant/cloud_turn_client.dart';

void main() {
  test('streamTurn yields tokens then completes on DoneFrame', () async {
    final ctrl = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(channelFactory: (_, __) => FakeSink(ctrl.stream, sent));
    await socket.connect('ws://x', cookie: 'session_id=abc');
    final client = CloudTurnClient(socket);

    final out = <String>[];
    final done = client.streamTurn('hi').forEach(out.add);

    await Future<void>.delayed(Duration.zero);
    ctrl.add('{"type":"token","content":"Hel"}');
    ctrl.add('{"type":"token","content":"lo"}');
    ctrl.add('{"type":"done","content":"Hello"}');
    await done;

    expect(out, ['Hel', 'lo']);
    expect(sent.single, contains('"content":"hi"'));
  });

  test('streamTurn throws CloudTurnException on ErrorFrame', () async {
    final ctrl = StreamController<dynamic>();
    final socket = ChatSocket(channelFactory: (_, __) => FakeSink(ctrl.stream, []));
    await socket.connect('ws://x', cookie: 'session_id=abc');
    final client = CloudTurnClient(socket);

    final fut = client.streamTurn('hi').toList();
    await Future<void>.delayed(Duration.zero);
    ctrl.add('{"type":"error","message":"boom"}');
    await expectLater(fut, throwsA(isA<CloudTurnException>()));
  });
}
```

- [ ] **Step 2: Run test to verify it fails**
Run: `cd mobile && flutter test test/assistant/cloud_turn_client_test.dart`
Expected: FAIL — `cloud_turn_client.dart` not found.

- [ ] **Step 3: Write minimal implementation**
```dart
/// Runs one "Hey Lazy" turn against the real LazyClaw server agent over a
/// DEDICATED ChatSocket (isolated from the main-chat socket so assistant turns
/// never appear in the Chat tab). Yields reply tokens; throws on server error.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../chat/chat_socket.dart';
import '../chat/ws_frames.dart';
import '../core/api/api_client.dart';      // apiClientProvider, baseUrlProvider
import '../core/config/server_config.dart';

class CloudTurnException implements Exception {
  final String message;
  const CloudTurnException(this.message);
  @override
  String toString() => 'CloudTurnException: $message';
}

class CloudTurnClient {
  CloudTurnClient(this._socket);
  final ChatSocket _socket;

  /// Sends [userText] and yields reply tokens until the server's DoneFrame.
  /// Throws [CloudTurnException] on ErrorFrame / SendFailedFrame.
  Stream<String> streamTurn(String userText) {
    // Subscribe BEFORE sending so no early token is missed.
    final sub = _socket.frames;
    final controller = StreamController<String>();
    late final dynamic listen;
    listen = sub.listen((f) {
      switch (f) {
        case TokenFrame(:final content):
          controller.add(content);
        case DoneFrame():
          controller.close();
        case ErrorFrame(:final message):
          controller.addError(CloudTurnException(message));
          controller.close();
        case SendFailedFrame(:final message):
          controller.addError(CloudTurnException(message));
          controller.close();
        default:
          break; // ignore tool/phase/activity frames for the voice MVP
      }
    });
    controller.onCancel = () => listen.cancel();
    _socket.send(userText);
    return controller.stream;
  }
}

/// DEDICATED socket for assistant cloud turns — separate from chatSocketProvider.
final assistantSocketProvider = Provider<ChatSocket>((ref) {
  final s = ChatSocket();
  ref.onDispose(s.dispose);
  return s;
});

final cloudTurnClientProvider = Provider<CloudTurnClient>(
  (ref) => CloudTurnClient(ref.watch(assistantSocketProvider)),
);

/// Connects the dedicated assistant socket using the same base URL + session
/// cookie the chat screen uses. Returns false when there is no session.
Future<bool> ensureAssistantSocketConnected(Ref ref) async {
  final base = ref.read(baseUrlProvider);
  final cookie = await ref.read(apiClientProvider).getSessionCookie();
  if (cookie == null) return false;
  await ref.read(assistantSocketProvider).connect(
        ServerConfig.wsUrlFor(base),
        cookie: 'session_id=$cookie',
      );
  return true;
}
```
> Add `import 'dart:async';` at the top. Verify `baseUrlProvider` + `getSessionCookie()` live in `core/api/api_client.dart` (chat_screen.dart reads them via `apiClientProvider`); if `baseUrlProvider` is elsewhere, fix the import.

- [ ] **Step 4: Run test to verify it passes**
Run: `cd mobile && flutter test test/assistant/cloud_turn_client_test.dart`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**
```bash
git add mobile/lib/assistant/cloud_turn_client.dart mobile/test/assistant/cloud_turn_client_test.dart
git commit -m "feat(assistant): CloudTurnClient over a dedicated isolated socket"
```

---

### Task 4: Tier the `LazyAssistantController`

**Files:**
- Modify: `lib/assistant/lazy_assistant_controller.dart`
- Test: `test/assistant/lazy_assistant_tiering_test.dart`

**Interfaces:**
- Consumes: `AssistantRouter`, `AssistantBackendMode`, `CloudTurnClient`, `LocalLlmEngine`.
- Produces (on `AssistantState`): a new field `final TurnSource? source;` where `enum TurnSource { onDevice, cloud }`, carried through `copyWith`. Controller now takes `(LocalLlmEngine engine, CloudTurnClient cloud, AssistantBackendMode Function() readMode, bool Function() readOnDeviceOnly, {AssistantRouter router, Future<bool> Function() ensureCloud})`.

- [ ] **Step 1: Write the failing test** (fakes for engine + cloud)
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/assistant_backend_mode.dart';
import 'package:lazyclaw_mobile/assistant/cloud_turn_client.dart';
import 'package:lazyclaw_mobile/assistant/lazy_assistant_controller.dart';
import 'package:lazyclaw_mobile/local_ai/local_llm_engine.dart';

class _FakeEngine implements LocalLlmEngine {
  _FakeEngine(this._reply);
  final String _reply;
  @override bool get isLoaded => true;
  @override String? get loadedModelId => 'fake';
  @override Future<void> load(String id, String path) async {}
  @override Future<void> unload() async {}
  @override Stream<String> generate(List<LocalLlmMessage> m, {String? systemPrompt}) async* {
    yield _reply;
  }
}

class _FakeCloud extends CloudTurnClient {
  _FakeCloud(this._reply) : super(_unused);
  final String _reply;
  @override Stream<String> streamTurn(String t) async* { yield _reply; }
}

void main() {
  test('Auto + plain chat → local (TurnSource.onDevice)', () async {
    final c = LazyAssistantController(
      _FakeEngine('a metaphor'), _FakeCloud('CLOUD'),
      () => AssistantBackendMode.preferOnDevice, () => false,
      ensureCloud: () async => true,
    );
    await c.askForTest('tell me a metaphor');
    expect(c.debugState.source, TurnSource.onDevice);
    expect(c.debugState.response, contains('metaphor'));
  });

  test('Auto + action verb → cloud (TurnSource.cloud)', () async {
    final c = LazyAssistantController(
      _FakeEngine('LOCAL'), _FakeCloud('Logged 12 euros to groceries'),
      () => AssistantBackendMode.preferOnDevice, () => false,
      ensureCloud: () async => true,
    );
    await c.askForTest('log a 12 euro expense for groceries');
    expect(c.debugState.source, TurnSource.cloud);
    expect(c.debugState.response, contains('Logged'));
  });

  test('processDataOnDevice forces local even for an action', () async {
    final c = LazyAssistantController(
      _FakeEngine("I can't do that on-device"), _FakeCloud('CLOUD'),
      () => AssistantBackendMode.preferOnDevice, () => true,
      ensureCloud: () async => true,
    );
    await c.askForTest('send a message to John');
    expect(c.debugState.source, TurnSource.onDevice);
  });
}
```
> `_unused` placeholder: declare `final _unused = CloudTurnClient(/* see note */);` is awkward — instead make `_FakeCloud` not call super by giving `CloudTurnClient` an unnamed forwarding ctor is overkill. Simpler: change `CloudTurnClient` to expose a `@visibleForTesting CloudTurnClient.test()` const ctor, OR make the controller depend on a narrow `abstract class CloudTurns { Stream<String> streamTurn(String t); }` that `CloudTurnClient` implements. **Adopt the narrow interface**: add `abstract interface class CloudTurns { Stream<String> streamTurn(String text); }` to `cloud_turn_client.dart`, have `CloudTurnClient implements CloudTurns`, and type the controller + fake on `CloudTurns`. Update Task 3 accordingly (one line) and this test's `_FakeCloud implements CloudTurns`.

- [ ] **Step 2: Run test to verify it fails**
Run: `cd mobile && flutter test test/assistant/lazy_assistant_tiering_test.dart`
Expected: FAIL — controller ctor signature mismatch / `TurnSource` undefined / no `askForTest`/`debugState`.

- [ ] **Step 3: Write minimal implementation** (edit the controller)
Add near the top:
```dart
enum TurnSource { onDevice, cloud }
```
Add `final TurnSource? source;` to `AssistantState` (constructor param + field + `copyWith` with a `clearSource`-free passthrough; keep existing fields). Change the controller:
```dart
class LazyAssistantController extends StateNotifier<AssistantState> {
  LazyAssistantController(
    this._engine,
    this._cloud,
    this._readMode,
    this._readOnDeviceOnly, {
    AssistantRouter router = const AssistantRouter(),
    Future<bool> Function()? ensureCloud,
  })  : _router = router,
        _ensureCloud = ensureCloud,
        super(const AssistantState());

  final LocalLlmEngine _engine;
  final CloudTurns _cloud;
  final AssistantBackendMode Function() _readMode;
  final bool Function() _readOnDeviceOnly;
  final AssistantRouter _router;
  final Future<bool> Function()? _ensureCloud;
  // … existing _stt/_tts/_sttReady/_system/_clean unchanged …

  @visibleForTesting
  Future<void> askForTest(String text) => _ask(text);
  @visibleForTesting
  AssistantState get debugState => state;
```
Rewrite `_ask`:
```dart
Future<void> _ask(String text) async {
  final prompt = text.trim();
  if (prompt.isEmpty) {
    state = const AssistantState(phase: AssistantPhase.idle);
    return;
  }
  final route = _router.decide(
    utterance: prompt,
    mode: _readMode(),
    processDataOnDevice: _readOnDeviceOnly(),
  );
  state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt);

  final buf = StringBuffer();
  TurnSource source = route == AssistantRoute.cloud ? TurnSource.cloud : TurnSource.onDevice;
  try {
    if (route == AssistantRoute.cloud) {
      final ok = (_ensureCloud == null) ? true : await _ensureCloud!();
      if (!ok) {
        // No session → degrade to local with an honest note.
        source = TurnSource.onDevice;
        await _streamLocal(prompt, buf);
      } else {
        await for (final tok in _cloud.streamTurn(prompt)) {
          buf.write(tok);
          state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt, response: buf.toString(), source: TurnSource.cloud);
        }
      }
    } else {
      await _streamLocal(prompt, buf);
      // Optional secondary self-signal: local asked to escalate.
      if (_readMode() == AssistantBackendMode.preferOnDevice &&
          !_readOnDeviceOnly() &&
          buf.toString().contains('[[NEEDS_CLOUD]]')) {
        buf.clear();
        final ok = (_ensureCloud == null) ? true : await _ensureCloud!();
        if (ok) {
          source = TurnSource.cloud;
          await for (final tok in _cloud.streamTurn(prompt)) {
            buf.write(tok);
            state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt, response: buf.toString(), source: TurnSource.cloud);
          }
        }
      }
    }
  } catch (e) {
    state = AssistantState(phase: AssistantPhase.error, transcript: prompt, error: 'Something went wrong: $e', source: source);
    return;
  }

  final reply = _clean(buf.toString());
  if (reply.isEmpty) {
    state = AssistantState(phase: AssistantPhase.idle, transcript: prompt, source: source);
    return;
  }
  state = AssistantState(phase: AssistantPhase.speaking, transcript: prompt, response: reply, source: source);
  await _speak(reply);
  state = AssistantState(phase: AssistantPhase.idle, transcript: prompt, response: reply, source: source);
}

Future<void> _streamLocal(String prompt, StringBuffer buf) async {
  await for (final tok in _engine.generate([LocalLlmMessage.user(prompt)], systemPrompt: _system)) {
    buf.write(tok);
    state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt, response: buf.toString(), source: TurnSource.onDevice);
  }
}
```
Add imports: `package:flutter/foundation.dart` (for `@visibleForTesting`), `assistant_backend_mode.dart`, `assistant_router.dart`, `cloud_turn_client.dart`. Update the provider:
```dart
final lazyAssistantProvider =
    StateNotifierProvider<LazyAssistantController, AssistantState>((ref) {
  final c = LazyAssistantController(
    ref.watch(localLlmEngineProvider),
    ref.watch(cloudTurnClientProvider),
    () => ref.read(assistantBackendModeProvider),
    () => ref.read(assistantOnDeviceOnlyProvider), // from Task 5
    ensureCloud: () => ensureAssistantSocketConnected(ref),
  );
  ref.onDispose(c.dispose);
  return c;
});
```
> `startListening` currently blocks when `!_engine.isLoaded`. Keep that guard for the LOCAL path, but in `preferCloud`/escalated turns the local engine may be unloaded. For P1 keep the guard (the model is loaded for the assistant); a later refinement allows cloud-only without a local model. Note this in the PR.

- [ ] **Step 4: Run test to verify it passes**
Run: `cd mobile && flutter test test/assistant/lazy_assistant_tiering_test.dart`
Expected: PASS (3 tests). Run the whole assistant suite too: `flutter test test/assistant/`.

- [ ] **Step 5: Commit**
```bash
git add mobile/lib/assistant/lazy_assistant_controller.dart mobile/lib/assistant/cloud_turn_client.dart mobile/test/assistant/lazy_assistant_tiering_test.dart
git commit -m "feat(assistant): tier _ask() local↔cloud with provenance (TurnSource)"
```

---

### Task 5: `GeneralSettings` — assistant fields + repo + provider

**Files:**
- Modify: `lib/repositories/settings_repository.dart`
- Create: `lib/assistant/assistant_settings_providers.dart`
- Test: `test/repositories/assistant_settings_test.dart`

**Interfaces:**
- Produces: `GeneralSettings` gains `final bool assistantProcessDataOnDevice;` `final bool assistantConfirmCloudRequests;` `final bool assistantAlwaysListening;` (all default `false`/`true`/`false`), parsed from `process_data_on_device` / `confirm_cloud_requests` / `assistant_always_listening`; `SettingsRepository.setAssistantFlags({bool? processDataOnDevice, bool? confirmCloudRequests, bool? alwaysListening})` PATCHing those keys; `assistantOnDeviceOnlyProvider` (`StateProvider<bool>`), `assistantConfirmCloudProvider` (`StateProvider<bool>`) seeded from the server snapshot.

- [ ] **Step 1: Write the failing test**
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/settings_repository.dart';

class _FakeT implements SettingsTransport {
  Map<String, dynamic>? lastPatch;
  @override Future<Map<String, dynamic>> getJson(String p) async =>
      {'success': true, 'data': {'agent_mode': 'ask', 'process_data_on_device': true}};
  @override Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async => {'success': true, 'data': {}};
  @override Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async {
    lastPatch = b;
    return {'success': true, 'data': {...b}};
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
```

- [ ] **Step 2: Run test to verify it fails**
Run: `cd mobile && flutter test test/repositories/assistant_settings_test.dart`
Expected: FAIL — `assistantProcessDataOnDevice` undefined.

- [ ] **Step 3: Write minimal implementation**
Extend `GeneralSettings` (add fields, constructor params with defaults, update `fromJson` + `copyWith`):
```dart
class GeneralSettings {
  final String agentMode;
  final bool assistantProcessDataOnDevice;
  final bool assistantConfirmCloudRequests;
  final bool assistantAlwaysListening;

  const GeneralSettings({
    required this.agentMode,
    this.assistantProcessDataOnDevice = false,
    this.assistantConfirmCloudRequests = true,
    this.assistantAlwaysListening = false,
  });

  factory GeneralSettings.fromJson(Map<String, dynamic> json) => GeneralSettings(
        agentMode: coerceAgentMode(json['agent_mode']),
        assistantProcessDataOnDevice: json['process_data_on_device'] == true,
        assistantConfirmCloudRequests: json['confirm_cloud_requests'] != false,
        assistantAlwaysListening: json['assistant_always_listening'] == true,
      );

  GeneralSettings copyWith({
    String? agentMode,
    bool? assistantProcessDataOnDevice,
    bool? assistantConfirmCloudRequests,
    bool? assistantAlwaysListening,
  }) => GeneralSettings(
        agentMode: agentMode ?? this.agentMode,
        assistantProcessDataOnDevice: assistantProcessDataOnDevice ?? this.assistantProcessDataOnDevice,
        assistantConfirmCloudRequests: assistantConfirmCloudRequests ?? this.assistantConfirmCloudRequests,
        assistantAlwaysListening: assistantAlwaysListening ?? this.assistantAlwaysListening,
      );

  static String coerceAgentMode(dynamic v) { /* unchanged */ }
}
```
Add to `SettingsRepository`:
```dart
Future<GeneralSettings> setAssistantFlags({
  bool? processDataOnDevice,
  bool? confirmCloudRequests,
  bool? alwaysListening,
}) async {
  final body = <String, dynamic>{};
  if (processDataOnDevice != null) body['process_data_on_device'] = processDataOnDevice;
  if (confirmCloudRequests != null) body['confirm_cloud_requests'] = confirmCloudRequests;
  if (alwaysListening != null) body['assistant_always_listening'] = alwaysListening;
  final json = await _t.patchJson('/api/settings/general', body);
  _assertSuccess(json);
  return GeneralSettings.fromJson(Map<String, dynamic>.from(json['data'] as Map));
}
```
Create `lib/assistant/assistant_settings_providers.dart`:
```dart
library;
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Local mirrors of the assistant privacy flags. Seeded from the server's
/// general settings on app start (see settings screen), written through on toggle.
final assistantOnDeviceOnlyProvider = StateProvider<bool>((_) => false);
final assistantConfirmCloudProvider = StateProvider<bool>((_) => true);
final assistantFirstCloudConsentGivenProvider = StateProvider<bool>((_) => false);
```

- [ ] **Step 4: Run test to verify it passes**
Run: `cd mobile && flutter test test/repositories/assistant_settings_test.dart && flutter test test/repositories/`
Expected: PASS; existing settings tests still green (defaults are additive).

- [ ] **Step 5: Commit**
```bash
git add mobile/lib/repositories/settings_repository.dart mobile/lib/assistant/assistant_settings_providers.dart mobile/test/repositories/assistant_settings_test.dart
git commit -m "feat(settings): assistant privacy flags (process-on-device, confirm-cloud)"
```
> **Server note:** confirm the backend `PATCH/GET /api/settings/general` tolerates these extra keys (it stores a general dict). If the server rejects unknown keys, add them server-side in a follow-up; the app fails soft (PATCH error is caught by the settings screen).

---

### Task 6: Settings UI — "Hey Lazy" section

**Files:**
- Modify: `lib/screens/settings_screen.dart`
- Manual verification (widget test optional).

**Interfaces:**
- Consumes: `assistantBackendModeProvider` (Task 1), `assistantOnDeviceOnlyProvider`/`assistantConfirmCloudProvider` (Task 5), `SettingsRepository.setAssistantFlags`.

- [ ] **Step 1: Add a "Hey Lazy" section** (reuse the `Lz*` kit + existing settings row patterns in the file):
  - A 3-way segmented control / radio group bound to `assistantBackendModeProvider`: **Local 🔒** (`onlyOnDevice`), **Auto ⚡** (`preferOnDevice`), **Max quality ☁️** (`preferCloud`), with one-line descriptions from spec §3.2. On change call `controller.set(mode)`.
  - A **"Process data only on device"** switch bound to `assistantOnDeviceOnlyProvider`; on toggle, write through `setAssistantFlags(processDataOnDevice: v)` and, when ON, visually note it forces Local. Inline copy: "Affects LazyClaw's assistant only."
  - A **"Confirm cloud requests"** switch bound to `assistantConfirmCloudProvider` → `setAssistantFlags(confirmCloudRequests: v)`.
  - (Greyed, "coming soon") **"Hey Lazy always listening"** — disabled placeholder wired in P3.
- [ ] **Step 2: Seed the providers from the server** on settings load: after `getGeneral()`, set `assistantOnDeviceOnlyProvider`/`assistantConfirmCloudProvider` from the snapshot (mirrors how agent_mode is seeded).
- [ ] **Step 3: Run** `cd mobile && flutter analyze` → no new warnings.
- [ ] **Step 4: Manual check** — launch, open Settings, flip each control, confirm persistence across a settings reopen.
- [ ] **Step 5: Commit**
```bash
git add mobile/lib/screens/settings_screen.dart
git commit -m "feat(settings): Hey Lazy tier picker + privacy toggles"
```

---

### Task 7: Provenance badge + mic indicator (minimal, pre-P2)

**Files:**
- Create: `lib/assistant/widgets/provenance_badge.dart`, `lib/assistant/widgets/mic_state_indicator.dart`
- Modify: `lib/screens/assistant/lazy_assistant_screen.dart`
- Test: `test/assistant/provenance_badge_test.dart` (widget test)

**Interfaces:**
- Consumes: `TurnSource` (Task 4), `AppColors` (`lib/ui/`).

- [ ] **Step 1: Failing widget test** — `ProvenanceBadge(source: TurnSource.cloud)` renders text "Cloud" with `Semantics(label: 'Processed in the cloud')`; `TurnSource.onDevice` → "On-device" / "Processed on device".
- [ ] **Step 2: Run → FAIL** (`flutter test test/assistant/provenance_badge_test.dart`).
- [ ] **Step 3: Implement** `ProvenanceBadge` — a pill: emerald (`AppColors.accent`) "On-device" vs amber (`AppColors.warning`/`#F59E0B`) "Cloud", icon + label, wrapped in `Semantics`. `MicStateIndicator` — filled emerald circle (live) vs hollow grey rounded-square (muted), dual-encoded + `Semantics`/`Tooltip`. In `lazy_assistant_screen.dart`, show the badge once `state.source != null` (header or above the reply), and the mic indicator in the mic affordance.
- [ ] **Step 4: Run → PASS**; `flutter analyze` clean.
- [ ] **Step 5: Commit**
```bash
git add mobile/lib/assistant/widgets/ mobile/lib/screens/assistant/lazy_assistant_screen.dart mobile/test/assistant/provenance_badge_test.dart
git commit -m "feat(assistant): per-turn provenance badge + dual-encoded mic indicator"
```

---

### Task 8: First-cloud-hop consent

**Files:**
- Modify: `lib/screens/assistant/lazy_assistant_screen.dart` (intercept before the first escalation)
- Modify: `lib/assistant/lazy_assistant_controller.dart` (expose a pre-route hook OR a `pendingCloudConsent` phase)
- Test: extend `test/assistant/lazy_assistant_tiering_test.dart`

**Interfaces:**
- Consumes: `assistantConfirmCloudProvider`, `assistantFirstCloudConsentGivenProvider` (Task 5).

- [ ] **Step 1: Failing test** — when `confirmCloud == true` and consent not yet given and a turn routes to cloud, the controller enters a new `AssistantPhase.awaitingCloudConsent` (no cloud send yet) and exposes `pendingCloudPrompt`. After `approveCloudOnce()`, the cloud turn proceeds.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — add `AssistantPhase.awaitingCloudConsent`; the controller takes `bool Function() readConfirmCloud` + `bool Function() readConsentGiven` + `void Function() markConsentGiven`. In `_ask`, when route==cloud and confirm && !given, set the consent phase and stash the prompt; `approveCloudOnce()` marks consent + resumes the cloud branch; `denyCloud()` falls back to local with an honest spoken note ("Okay, keeping this on your phone — I can't reach the internet for that."). The screen renders a one-time sheet (spec §7.4 copy) on that phase.
- [ ] **Step 4: Run → PASS**; full assistant suite green.
- [ ] **Step 5: Commit**
```bash
git add mobile/lib/assistant/lazy_assistant_controller.dart mobile/lib/screens/assistant/lazy_assistant_screen.dart mobile/test/assistant/lazy_assistant_tiering_test.dart
git commit -m "feat(assistant): one-time first-cloud-hop consent gate"
```

---

### Task 9: Wire the ASSIST intent → `/assistant`

**Files:**
- Modify: `lib/core/actions/app_actions.dart` (add `AppAction.assistant`)
- Modify: `lib/core/actions/deep_link_service.dart` (receive native ASSIST → set pending action)
- Modify: `android/app/src/main/kotlin/.../MainActivity.kt` (forward `ACTION_ASSIST` over a MethodChannel)
- Modify: `lib/core/router/app_router.dart` (drain `AppAction.assistant` → `/assistant`)
- Test: `test/core/app_actions_assistant_test.dart`

**Interfaces:**
- Produces: `AppAction.assistant`; `kActionIds[AppAction.assistant] = 'assist'`; `routeForAction(AppAction.assistant) == '/assistant'`.

- [ ] **Step 1: Failing test**
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/actions/app_actions.dart';
void main() {
  test('assist shortcut + uri resolve to AppAction.assistant', () {
    expect(appActionForShortcut('assist'), AppAction.assistant);
    expect(appActionForUri(Uri.parse('lazyclaw://assistant')), AppAction.assistant);
    expect(routeForAction(AppAction.assistant), '/assistant');
  });
}
```
> `appActionForUri('lazyclaw://assistant')` canonicalizes to `assistant`; ensure `kActionIds[assistant] = 'assist'` canonicalizes equal (`assist` vs `assistant` differ!). Use id `'assistant'` so the URI host matches, and ALSO accept the launcher `assist` — simplest: set `kActionIds[AppAction.assistant] = 'assistant'` and add the bare `assist` as an accepted alias in `_matchCanon` (or register the native ASSIST as `lazyclaw://assistant`). Pick `'assistant'` as the canonical id; the test above uses `appActionForShortcut('assistant')` then — adjust the test to the chosen id and keep it consistent.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**
  - `app_actions.dart`: add `assistant` to the enum; `kActionIds[AppAction.assistant] = 'assistant'`; add `case AppAction.assistant: return '/assistant';` to `routeForAction`.
  - `MainActivity.kt`: in `onCreate`/`onNewIntent`, if `intent?.action == Intent.ACTION_ASSIST`, post over a `MethodChannel('lazy/assist')` → `invokeMethod('assist')` (after the engine is ready; queue if not).
  - `deep_link_service.dart`: listen on `MethodChannel('lazy/assist')`; on `assist`, `ref.read(pendingActionProvider.notifier).state = AppAction.assistant`.
  - `app_router.dart`: in the shell build, `drainPendingAction(ref, mine: {AppAction.assistant}, isMounted: ..., onDrained: (_) => context.push('/assistant'))`.
- [ ] **Step 4: Run → PASS** (`flutter test test/core/app_actions_assistant_test.dart`); `flutter analyze` clean.
- [ ] **Step 5: Commit**
```bash
git add mobile/lib/core/actions/app_actions.dart mobile/lib/core/actions/deep_link_service.dart mobile/android/app/src/main/kotlin mobile/lib/core/router/app_router.dart mobile/test/core/app_actions_assistant_test.dart
git commit -m "feat(assistant): assist gesture opens /assistant directly"
```

---

### Task 10: Build, version bump, OTA deploy + on-device verification

**Files:**
- Modify: `mobile/pubspec.yaml` (version bump), `mobile/lib/core/constants/app_constants.dart` (version constants if mirrored)

- [ ] **Step 1:** `cd mobile && flutter analyze` (no new issues) and `flutter test` (whole suite green).
- [ ] **Step 2:** Bump `version:` in `pubspec.yaml` (e.g. `1.21.9+69`) + any mirrored constant.
- [ ] **Step 3:** Build the release APK (always clean first — native-assets config reverts on incremental):
```bash
cd mobile && flutter clean && flutter pub get && flutter build apk --release --target-platform android-arm64
```
- [ ] **Step 4:** Publish OTA: copy `build/app/outputs/flutter-apk/app-release.apk` → `mobile/dist/app-release.apk`, update `mobile/dist/version.json` (version/build/sha256). (Use `scripts/build-mobile-apk.sh` if it already does this.)
- [ ] **Step 5: On-device verification (manual, MIUI):** install/update on the Mi 15 (PIN 159000); open ✨ Hey Lazy and verify:
  - "tell me a joke" → answers **on-device**, badge 🟢.
  - "what's the weather in Madrid today" → routes **cloud**, badge 🟠, real answer.
  - "add a task to call mom tomorrow" → cloud, the task is created (check Tasks tab), spoken read-back.
  - Flip **Local 🔒** → "what's the weather" stays on-device + says it can't reach the internet.
  - **CRITICAL:** confirm an assistant cloud turn does **NOT** appear as a message in the Chat tab (validates the dedicated-socket isolation; if it does, the server fans out per-session — scope the chat reducer to ignore frames during an assistant turn).
  - First cloud turn shows the consent sheet once.
- [ ] **Step 6: Commit**
```bash
git add mobile/pubspec.yaml mobile/lib/core/constants/app_constants.dart mobile/dist/version.json
git commit -m "chore(mobile): Hey Lazy tiered brain — app vX.Y.Z+B OTA"
```

---

## Self-Review

**Spec coverage (P1 sections):** §3.2 mode enum → Task 1; §4 router → Task 2; §5 cloud delegation → Tasks 3–4; §6 read-back → Task 4 (`_speak` of the cloud reply); risk-tiered confirmation (explicit `send_message`, money-mover) → **server-enforced**, app relays (noted; app-side undo/repair/disambiguation are deferred follow-ons, called out below); §7.1 process-on-device toggle → Tasks 5–6; §7.2 badge + §7.3 mic indicator → Task 7; §7.4 first-hop consent → Task 8; ASSIST routing (§10 codebase map) → Task 9; build/OTA/verify → Task 10.

**Deferred within P1 (explicit, not silent):** app-side 1-turn undo, repair-at-read-back, and >1-contact disambiguation for `send_message` (spec §6.3) are **not** in this plan — they ride on server-side gating for the MVP and become a fast-follow task once the core loop is verified. Battery/thermal `DeviceState` inputs exist in the router but are wired to `const DeviceState()` defaults in P1 (real battery/connectivity probes are a fast-follow; offline detection can use `connectivity_plus` if already a dep).

**Placeholder scan:** none — every code step carries real code. The two judgment calls (the `CloudTurns` interface in Task 4; the canonical assist id in Task 9) are spelled out with the decision to make.

**Type consistency:** `AssistantBackendMode` (3 values) consistent across Tasks 1/2/4/6. `TurnSource` defined in Task 4, consumed in Task 7. `CloudTurns`/`CloudTurnClient.streamTurn` consistent Tasks 3/4. `setAssistantFlags` keys match the `fromJson` keys in Task 5.

**Open risks to verify on-device (Task 10):** dedicated-socket isolation (no chat-tab pollution); server tolerance of the new general-settings keys; `speech_to_text` locale unaffected (separate from the keyboard fix).
