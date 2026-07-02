import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';

// `now` is injected so these are deterministic. Inputs WITHOUT a " UTC" suffix
// parse as local time, so day-bucketing doesn't depend on the machine TZ.
void main() {
  final now = DateTime(2026, 5, 25, 18, 0); // Mon 25 May 2026, 6pm local

  group('formatInboxTimestamp', () {
    test('empty input → empty string', () {
      expect(formatInboxTimestamp('', now: now), '');
      expect(formatInboxTimestamp('   ', now: now), '');
    });

    test('unparseable input is returned verbatim (never lose data)', () {
      expect(formatInboxTimestamp('not a date', now: now), 'not a date');
    });

    test('today → bare 12-hour clock', () {
      expect(formatInboxTimestamp('2026-05-25 14:37:00', now: now), '2:37 PM');
    });

    test('yesterday → "Yesterday H:MM"', () {
      expect(
        formatInboxTimestamp('2026-05-24 09:05:00', now: now),
        'Yesterday 9:05 AM',
      );
    });

    test('earlier this year → "D Mon, H:MM"', () {
      expect(
        formatInboxTimestamp('2026-03-10 23:00:00', now: now),
        '10 Mar, 11:00 PM',
      );
    });

    test('a previous year → "D Mon YYYY"', () {
      expect(
        formatInboxTimestamp('2024-12-31 08:00:00', now: now),
        '31 Dec 2024',
      );
    });

    test('midnight and noon map to 12 AM / 12 PM', () {
      expect(formatInboxTimestamp('2026-05-25 00:15:00', now: now), '12:15 AM');
      expect(formatInboxTimestamp('2026-05-25 12:00:00', now: now), '12:00 PM');
    });

    test('WhatsApp " UTC" suffix is parsed, not shown raw', () {
      // TZ-independent: assert it parsed (differs from raw and looks like a time).
      final out = formatInboxTimestamp('2026-05-25 20:37:14 UTC', now: now);
      expect(out, isNot(contains('UTC')));
      expect(out, matches(RegExp(r'\d{1,2}:\d{2} (AM|PM)')));
    });

    test('ISO-8601 with offset (thread lastActivity shape) parses', () {
      // channel_threads.last_activity is datetime.now(utc).isoformat().
      final out = formatInboxTimestamp('2026-05-25T18:37:14+00:00', now: now);
      expect(out, isNot(contains('T')));
      expect(out, matches(RegExp(r'\d{1,2}:\d{2} (AM|PM)')));
    });
  });

  group('prettyHandle', () {
    test('whatsapp phone JID → +digits', () {
      expect(prettyHandle('whatsapp', '34600111222@s.whatsapp.net'), '+34600111222');
      expect(prettyHandle('whatsapp', '34600111222:5@s.whatsapp.net'), '+34600111222');
    });

    test('whatsapp group / lid → local part (no raw suffix)', () {
      expect(prettyHandle('whatsapp', '120363000@g.us'), '120363000');
      expect(prettyHandle('whatsapp', 'abc123@lid'), 'abc123');
    });

    test('instagram username gets an @', () {
      expect(prettyHandle('instagram', 'pal'), '@pal');
      expect(prettyHandle('instagram', '@pal'), '@pal');
    });

    test('email and unknown channels pass through', () {
      expect(prettyHandle('email', 'bob@x.com'), 'bob@x.com');
      expect(prettyHandle('whatever', 'raw-handle'), 'raw-handle');
      expect(prettyHandle('whatsapp', ''), '');
    });
  });
}
