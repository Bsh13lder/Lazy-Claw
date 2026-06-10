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

class InboxMessage {
  final String sender;
  final String text;
  final String timestamp;
  final bool isMine;

  const InboxMessage({
    required this.sender,
    required this.text,
    required this.timestamp,
    this.isMine = false,
  });

  factory InboxMessage.fromJson(Map<String, dynamic> j) => InboxMessage(
        sender: (j['sender'] ?? '').toString(),
        text: (j['text'] ?? '').toString(),
        timestamp: (j['timestamp'] ?? '').toString(),
        isMine: j['is_mine'] == true,
      );
}
