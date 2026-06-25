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
