// Data models for the unified Inbox (threads + messages).
//
// These are pure immutable value objects — no UI, no networking.
// JSON parsing adapts the server snake_case shape to Dart camelCase fields.

class InboxThread {
  final String id;
  final String channel;
  final String contactHandle;
  final String? contactName;
  final String? lastPreview;
  final int unreadCount;
  final String lastActivity;
  final String updatedAt;

  /// Server flag: the row's encrypted identity fields (handle/name) failed to
  /// decrypt (mis-keyed / corrupted). Such a thread can't be opened, so the UI
  /// renders a distinct "unreadable — re-sync" row instead of a broken thread
  /// titled "[encrypted]".
  final bool decryptError;

  const InboxThread({
    required this.id,
    required this.channel,
    required this.contactHandle,
    this.contactName,
    this.lastPreview,
    this.unreadCount = 0,
    required this.lastActivity,
    required this.updatedAt,
    this.decryptError = false,
  });

  factory InboxThread.fromJson(Map<String, dynamic> j) => InboxThread(
        id: j['id'] as String,
        channel: j['channel'] as String,
        contactHandle: (j['contact_handle'] as String?) ?? '',
        contactName: j['contact_name'] as String?,
        lastPreview: j['last_preview'] as String?,
        unreadCount: (j['unread_count'] as int?) ?? 0,
        lastActivity: (j['last_activity'] ?? '').toString(),
        updatedAt: (j['updated_at'] ?? '').toString(),
        decryptError: j['decrypt_error'] == true,
      );
}

/// Media metadata attached to a message (voice note, photo, video, file).
///
/// Mirrors the server's `media` dict (sourced from the WhatsApp MCP's
/// describeMedia). The actual bytes are fetched on demand via
/// `GET /api/inbox/threads/{id}/media/{messageId}`.
class InboxMedia {
  final String kind; // image | video | audio | document | sticker
  final String mimetype;
  final String? fileName;
  final int? seconds;
  final bool voiceNote;
  final int? sizeBytes;

  const InboxMedia({
    required this.kind,
    required this.mimetype,
    this.fileName,
    this.seconds,
    this.voiceNote = false,
    this.sizeBytes,
  });

  factory InboxMedia.fromJson(Map<String, dynamic> j) => InboxMedia(
        kind: (j['kind'] ?? 'document').toString(),
        mimetype: (j['mimetype'] ?? 'application/octet-stream').toString(),
        fileName: j['file_name'] as String?,
        seconds: (j['seconds'] as num?)?.toInt(),
        voiceNote: j['voice_note'] == true,
        sizeBytes: (j['size_bytes'] as num?)?.toInt(),
      );
}

const _kMonths = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// Formats a channel message timestamp for display in the inbox.
///
/// The WhatsApp MCP emits `"2026-05-25 20:37:14 UTC"` (mcp-whatsapp
/// src/index.js:1171); other channels may emit ISO-8601. This parses to the
/// device-local zone and returns a short human form:
///   today     → "2:37 PM"
///   yesterday → "Yesterday 2:37 PM"
///   this year → "25 May, 2:37 PM"
///   older     → "25 May 2026"
///
/// Unparseable input is returned verbatim (never lose data); empty → empty.
/// `now` is injectable for deterministic tests.
String formatInboxTimestamp(String raw, {DateTime? now}) {
  final trimmed = raw.trim();
  if (trimmed.isEmpty) return '';

  var s = trimmed;
  var isUtc = false;
  if (s.toUpperCase().endsWith(' UTC')) {
    s = s.substring(0, s.length - 4).trim();
    isUtc = true;
  }
  // "2026-05-25 20:37:14" → "2026-05-25T20:37:14" so DateTime.parse accepts it.
  s = s.replaceFirst(' ', 'T');
  if (isUtc && !s.endsWith('Z')) s = '${s}Z';

  final parsed = DateTime.tryParse(s);
  if (parsed == null) return trimmed; // unknown shape — show as-is, don't drop.

  final dt = parsed.toLocal();
  final ref = (now ?? DateTime.now()).toLocal();

  final h = dt.hour % 12 == 0 ? 12 : dt.hour % 12;
  final period = dt.hour < 12 ? 'AM' : 'PM';
  final clock = '$h:${dt.minute.toString().padLeft(2, '0')} $period';

  final today = DateTime(ref.year, ref.month, ref.day);
  final thatDay = DateTime(dt.year, dt.month, dt.day);
  final dayDelta = today.difference(thatDay).inDays;

  if (dayDelta == 0) return clock;
  if (dayDelta == 1) return 'Yesterday $clock';
  final month = _kMonths[dt.month - 1];
  if (dt.year == ref.year) return '${dt.day} $month, $clock';
  return '${dt.day} $month ${dt.year}';
}

/// Human-friendly fallback label for a thread with no saved contact name.
///
/// Strips WhatsApp JID suffixes (`@s.whatsapp.net` / `@lid` / `@g.us`) and a
/// device suffix, formatting a bare phone as `+NNN`; prefixes an Instagram
/// username with `@`; leaves emails (and anything else) as-is. Never returns
/// empty for a non-empty handle — so the inbox never shows a raw
/// `34600@s.whatsapp.net` as a title.
String prettyHandle(String channel, String handle) {
  final h = handle.trim();
  if (h.isEmpty) return h;
  switch (channel) {
    case 'whatsapp':
      final local = h.split('@').first.split(':').first;
      // Only a PHONE JID becomes "+digits"; groups (@g.us) and @lid have
      // all-digit local parts too but aren't phone numbers.
      if (h.contains('@s.whatsapp.net') && RegExp(r'^\d{6,15}$').hasMatch(local)) {
        return '+$local';
      }
      return local.isNotEmpty ? local : h;
    case 'instagram':
      return h.startsWith('@') ? h : '@$h';
    default:
      return h; // email + anything else: already readable
  }
}

class InboxMessage {
  final String sender;
  final String text;
  final String timestamp;
  final bool isMine;

  /// Message id from the channel read — needed to fetch media bytes.
  final String? id;

  /// Media metadata when this bubble carries a voice note / photo / file.
  final InboxMedia? media;

  const InboxMessage({
    required this.sender,
    required this.text,
    required this.timestamp,
    this.isMine = false,
    this.id,
    this.media,
  });

  factory InboxMessage.fromJson(Map<String, dynamic> j) => InboxMessage(
        sender: (j['sender'] ?? '').toString(),
        text: (j['text'] ?? '').toString(),
        timestamp: (j['timestamp'] ?? '').toString(),
        isMine: j['is_mine'] == true,
        id: j['id'] as String?,
        media: j['media'] is Map
            ? InboxMedia.fromJson(Map<String, dynamic>.from(j['media'] as Map))
            : null,
      );
}
