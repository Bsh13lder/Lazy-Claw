/// Maps a chat `_connect()` failure to a short, human-readable banner message.
///
/// The connect banner used to render `e.toString()` raw, which leaked
/// internals like `DatabaseException(database_closed 1)` straight to the user.
/// This mapper is PURE (unit-tested without widgets) and fail-closed: any
/// unrecognised error gets the generic connectivity message — never the raw
/// exception text. Callers should `debugPrint` the raw error themselves so
/// diagnosis is not lost.
library;

import 'package:sqflite_sqlcipher/sqflite.dart' show DatabaseException;

/// Banner text when the local encrypted DB hiccuped (e.g. a stale handle).
const String kConnectErrorStorage = 'Local storage hiccup — tap Retry.';

/// Banner text for every other connect failure (DNS, socket, TLS, timeout,
/// server down, unexpected errors).
const String kConnectErrorGeneric = 'Could not connect — check the server.';

/// Short human message for the chat connect-error banner.
String connectErrorMessage(Object error) {
  if (error is DatabaseException) return kConnectErrorStorage;
  return kConnectErrorGeneric;
}
