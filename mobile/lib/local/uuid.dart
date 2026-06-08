import 'dart:math';

/// Minimal RFC-4122 version-4 (random) UUID generator.
///
/// We mint UUIDs client-side for locally-created tasks so the SAME id can be
/// replayed to the server idempotently (POST /api/tasks accepts a client `id`).
/// A dedicated `package:uuid` dependency would also work; this keeps the
/// offline layer free of an extra package for a 30-line need.
String uuidV4([Random? random]) {
  final rng = random ?? Random.secure();
  final bytes = List<int>.generate(16, (_) => rng.nextInt(256));

  // Per RFC 4122 §4.4: set the version (4) and variant (10xx) bits.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  String hex(int start, int end) {
    final buf = StringBuffer();
    for (var i = start; i < end; i++) {
      buf.write(bytes[i].toRadixString(16).padLeft(2, '0'));
    }
    return buf.toString();
  }

  return '${hex(0, 4)}-${hex(4, 6)}-${hex(6, 8)}-${hex(8, 10)}-${hex(10, 16)}';
}
