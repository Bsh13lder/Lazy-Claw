/// Day separators for the chat transcript.
///
/// The message list is a flat oldest-first stream, so a conversation spanning
/// several days reads as one undifferentiated wall. [buildChatListItems] is a
/// PURE function turning that stream into render items — messages interleaved
/// with a separator wherever the LOCAL calendar day changes (and above the
/// first dated message). Keeping it pure means the grouping rules are unit
/// tested without pumping a single widget.
///
/// Timestamps are UTC (`ChatMessage.createdAt`); grouping is by the LOCAL day,
/// because "Today" has to mean the user's today. Messages with a null
/// `createdAt` (legacy rows with unparseable timestamps) never open a group —
/// they attach to whatever day is currently open, so one bad row can't split a
/// day in two.
library;

import 'package:flutter/material.dart';

import '../../chat/chat_message.dart';
import '../../ui/ui.dart';

// ── Render items ───────────────────────────────────────────────────────────

/// One row of the rendered chat list: a message, or a day separator.
sealed class ChatListItem {
  const ChatListItem();
}

/// A normal chat message row.
class ChatMessageItem extends ChatListItem {
  const ChatMessageItem(this.message);
  final ChatMessage message;
}

/// A centered "Today" / "Yesterday" / "13 Aug 2026" divider row.
class ChatDayItem extends ChatListItem {
  const ChatDayItem({required this.day, required this.label});

  /// Local midnight of the day this separator opens.
  final DateTime day;

  /// Human label rendered in the pill.
  final String label;
}

// ── Pure computation ───────────────────────────────────────────────────────

/// Expands an oldest-first [messages] list into render items, inserting a
/// [ChatDayItem] above the first message of every local calendar day.
/// [now] is injectable so "Today"/"Yesterday" are testable.
List<ChatListItem> buildChatListItems(
  List<ChatMessage> messages, {
  DateTime? now,
}) {
  if (messages.isEmpty) return const [];
  final today = _dayOf(now ?? DateTime.now());
  final out = <ChatListItem>[];
  DateTime? openDay;
  for (final m in messages) {
    final at = m.createdAt;
    if (at != null) {
      final day = _dayOf(at.toLocal());
      if (openDay == null || !_sameDay(day, openDay)) {
        out.add(ChatDayItem(day: day, label: dayLabel(day, today: today)));
        openDay = day;
      }
    }
    out.add(ChatMessageItem(m));
  }
  return out;
}

/// "Today" / "Yesterday" / "13 Aug 2026" for a local-midnight [day].
String dayLabel(DateTime day, {required DateTime today}) {
  if (_sameDay(day, today)) return 'Today';
  // Calendar arithmetic, NOT `subtract(Duration(days: 1))` — the latter
  // shifts by 24 absolute hours and lands on the wrong date across a DST
  // boundary (a 23-hour day would label yesterday as "Today").
  final yesterday = DateTime(today.year, today.month, today.day - 1);
  if (_sameDay(day, yesterday)) return 'Yesterday';
  return '${day.day} ${_monthAbbr[day.month - 1]} ${day.year}';
}

const List<String> _monthAbbr = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', //
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

DateTime _dayOf(DateTime local) => DateTime(local.year, local.month, local.day);

bool _sameDay(DateTime a, DateTime b) =>
    a.year == b.year && a.month == b.month && a.day == b.day;

// ── Widget ─────────────────────────────────────────────────────────────────

/// Centered day divider — same visual family as `CronSystemRow`'s schedule
/// pill: a muted caption inside a subtle elevated pill, kit tokens only.
class ChatDaySeparator extends StatelessWidget {
  const ChatDaySeparator({super.key, required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      child: Center(
        child: LzPill(label: label, color: AppColors.textMuted),
      ),
    );
  }
}
