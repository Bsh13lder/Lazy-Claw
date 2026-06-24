/// Which backend the MAIN chat tab is currently talking to.
///
/// `null` = the server LazyClaw agent (the default — full tools, tasks,
/// channels, approvals, history over the WebSocket). A non-null value is the
/// id of an on-device [LocalModel] (see `local_ai/local_model.dart`): the chat
/// then runs entirely on-device with no server round-trip, no approvals, and
/// no history.
///
/// This is a thin routing seam only — a `StateProvider<String?>` the chat
/// screen watches to decide which message controller / send path to use. It
/// holds NO chat state itself; the actual conversations live in
/// `chatControllerProvider` (server) and `localChatControllerProvider` (local).
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

/// The active chat backend for the main chat tab.
///
/// `null` → server agent; non-null → the selected local model id.
final chatBackendProvider = StateProvider<String?>((_) => null);
