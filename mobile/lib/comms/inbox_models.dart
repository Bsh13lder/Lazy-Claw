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

  const InboxThread({
    required this.id,
    required this.channel,
    required this.contactHandle,
    this.contactName,
    this.lastPreview,
    this.unreadCount = 0,
    required this.lastActivity,
    required this.updatedAt,
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
