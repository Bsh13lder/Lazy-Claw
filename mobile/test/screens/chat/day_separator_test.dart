// Day separators in the chat transcript.
//
// `buildChatListItems` is pure — the grouping rules (one divider per LOCAL
// calendar day, "Today"/"Yesterday"/"13 Aug 2026", null timestamps attach to
// the open day) are asserted without pumping a widget. Only the last group
// pumps, and it renders a single leaf widget (no sqflite, no timers).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/screens/chat/day_separator.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// A message stamped at LOCAL [y]-[m]-[d] [hh]:[mm] (stored UTC, as the
/// history mapper does).
ChatMessage _at(int y, int m, int d, int hh, int mm, {String role = 'user'}) =>
    ChatMessage(
      role: role,
      content: '$y-$m-$d $hh:$mm',
      createdAt: DateTime(y, m, d, hh, mm).toUtc(),
    );

ChatMessage _undated([String content = 'legacy row']) =>
    ChatMessage(role: 'assistant', content: content);

List<String> _labels(List<ChatListItem> items) => [
      for (final i in items)
        if (i is ChatDayItem) i.label,
    ];

void main() {
  // Fixed "now" so Today/Yesterday never depend on the wall clock.
  final now = DateTime(2026, 8, 14, 11, 0);

  group('buildChatListItems', () {
    test('empty input yields no items', () {
      expect(buildChatListItems(const [], now: now), isEmpty);
    });

    test('a divider opens the first dated message', () {
      final items = buildChatListItems([_at(2026, 8, 14, 9, 0)], now: now);
      expect(items, hasLength(2));
      expect(items.first, isA<ChatDayItem>());
      expect((items.first as ChatDayItem).label, 'Today');
      expect(items.last, isA<ChatMessageItem>());
    });

    test('no divider between messages on the same local day', () {
      final items = buildChatListItems([
        _at(2026, 8, 14, 9, 0),
        _at(2026, 8, 14, 9, 5),
        _at(2026, 8, 14, 23, 59),
      ], now: now);
      expect(items.whereType<ChatDayItem>(), hasLength(1));
      expect(items.whereType<ChatMessageItem>(), hasLength(3));
    });

    test('today / yesterday / older labels', () {
      final items = buildChatListItems([
        _at(2026, 8, 11, 8, 0),
        _at(2026, 8, 13, 20, 0),
        _at(2026, 8, 14, 9, 0),
      ], now: now);
      expect(_labels(items), ['11 Aug 2026', 'Yesterday', 'Today']);
    });

    test('an older year is labelled with its own year', () {
      final items = buildChatListItems([_at(2025, 12, 31, 23, 0)], now: now);
      expect(_labels(items), ['31 Dec 2025']);
    });

    test('a new divider opens every day change, in order', () {
      final items = buildChatListItems([
        _at(2026, 8, 13, 22, 0),
        _at(2026, 8, 14, 0, 5),
        _at(2026, 8, 14, 10, 0),
      ], now: now);
      expect(_labels(items), ['Yesterday', 'Today']);
      // Separator sits directly above the first message of its day.
      expect(items[0], isA<ChatDayItem>());
      expect(items[1], isA<ChatMessageItem>());
      expect(items[2], isA<ChatDayItem>());
      expect(items[3], isA<ChatMessageItem>());
      expect(items[4], isA<ChatMessageItem>());
    });

    test('a null createdAt attaches to the open day and never adds a divider',
        () {
      final items = buildChatListItems([
        _at(2026, 8, 13, 22, 0),
        _undated(),
        _at(2026, 8, 14, 9, 0),
      ], now: now);
      expect(_labels(items), ['Yesterday', 'Today']);
      expect(items[1], isA<ChatMessageItem>());
      expect(items[2], isA<ChatMessageItem>(),
          reason: 'the undated row rides along with the previous day group');
    });

    test('leading undated rows render with no divider above them', () {
      final items = buildChatListItems([
        _undated('first'),
        _undated('second'),
        _at(2026, 8, 14, 9, 0),
      ], now: now);
      expect(items[0], isA<ChatMessageItem>());
      expect(items[1], isA<ChatMessageItem>());
      expect(items[2], isA<ChatDayItem>());
      expect(_labels(items), ['Today']);
    });

    test('an undated row between two same-day rows does not re-open the day',
        () {
      final items = buildChatListItems([
        _at(2026, 8, 14, 9, 0),
        _undated(),
        _at(2026, 8, 14, 9, 30),
      ], now: now);
      expect(items.whereType<ChatDayItem>(), hasLength(1));
    });

    test('the separator day is local midnight of the message day', () {
      final items = buildChatListItems([_at(2026, 8, 14, 9, 0)], now: now);
      final day = (items.first as ChatDayItem).day;
      expect(day, DateTime(2026, 8, 14));
    });
  });

  group('dayLabel', () {
    final today = DateTime(2026, 3, 30); // day after Europe/Madrid DST start

    test('same day is Today', () {
      expect(dayLabel(DateTime(2026, 3, 30), today: today), 'Today');
    });

    test('the previous CALENDAR day is Yesterday even across a DST shift', () {
      // A 23-hour day makes `today.subtract(Duration(days: 1))` land on the
      // wrong date — calendar arithmetic must be used instead.
      expect(dayLabel(DateTime(2026, 3, 29), today: today), 'Yesterday');
    });

    test('month boundaries roll back correctly', () {
      expect(dayLabel(DateTime(2026, 2, 28), today: DateTime(2026, 3, 1)),
          'Yesterday');
      expect(dayLabel(DateTime(2025, 12, 31), today: DateTime(2026, 1, 1)),
          'Yesterday');
    });

    test('older days use "d MMM yyyy"', () {
      expect(dayLabel(DateTime(2026, 8, 13), today: DateTime(2026, 8, 15)),
          '13 Aug 2026');
      expect(dayLabel(DateTime(2026, 1, 5), today: DateTime(2026, 8, 15)),
          '5 Jan 2026');
    });
  });

  group('ChatDaySeparator widget', () {
    testWidgets('renders the label in a centered pill', (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: const Scaffold(body: ChatDaySeparator(label: 'Yesterday')),
      ));
      expect(find.text('Yesterday'), findsOneWidget);
      expect(find.byType(LzPill), findsOneWidget);
      expect(find.byType(Center), findsWidgets);
    });
  });
}
