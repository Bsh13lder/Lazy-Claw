/// Matches a trailing `Z` or a numeric UTC offset (`+00:00`, `-0500`, ...) so
/// [parseInstantMicros] can tell an explicit zone apart from an absent one.
final RegExp _hasExplicitOffset = RegExp(r'(Z|[+-]\d{2}:?\d{2})$');

/// Parse an ISO-8601 timestamp to UTC microseconds-since-epoch.
///
/// Accepts every shape that crosses the sync boundary: a `T` or space
/// separator, and a `Z` or `+00:00` (or absent) UTC offset. Returns null when
/// the string is empty or unparseable.
///
/// `DateTime.tryParse` treats a string with no explicit zone as LOCAL time,
/// not UTC, which would make this function's result depend on the device's
/// timezone (verified failure on a UTC+2 machine: the space-separated case
/// landed 2 hours away from the `Z`/`+00:00` cases). Since the sync boundary
/// only ever emits naive timestamps that are already UTC, an absent offset is
/// normalized to `Z` before parsing so the instant is timezone-independent.
int? parseInstantMicros(String? s) {
  if (s == null || s.isEmpty) return null;
  final normalized = _hasExplicitOffset.hasMatch(s) ? s : '${s}Z';
  final dt = DateTime.tryParse(normalized);
  return dt?.toUtc().microsecondsSinceEpoch;
}

/// True when the SERVER row should win last-write-wins over the LOCAL row.
///
/// Preserves the exact contract of the old `_gte(server, local)` helpers:
///  - an empty server time never wins;
///  - an empty local time always loses to the server;
///  - equal instants → server wins (tie-break toward server authority).
///
/// The one behavioral change: it compares PARSED INSTANTS, not raw strings, so
/// a same-instant `...Z` (client-minted) vs `...+00:00` (server-stamped) pair
/// no longer mis-ranks and strands the server's edit. Falls back to the legacy
/// lexical compare only if a value fails to parse, so a malformed timestamp
/// can never crash the merge.
bool serverWinsByTime(String? server, String? local) {
  final s = server ?? '';
  final l = local ?? '';
  if (s.isEmpty) return false;
  if (l.isEmpty) return true;
  final si = parseInstantMicros(s);
  final li = parseInstantMicros(l);
  if (si == null || li == null) {
    return s.compareTo(l) >= 0;
  }
  return si >= li;
}
