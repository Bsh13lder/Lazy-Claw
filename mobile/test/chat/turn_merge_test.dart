// One TURN = one bubble.
//
// The server batch-persists every row of an agent turn with the SAME
// `created_at`, and a turn usually writes two assistant rows: an interim
// status line carrying the tool_calls metadata, then the final reply. Rendered
// raw that is a doubled bubble per turn. `mergeTurnRows` collapses them; the
// absorbed rows' ids ride along in `absorbedIds` so the history delta-merge
// can't re-insert them on the next re-fetch.
//
// HAZARD notes honored: plain `test` only — no FakeAsync, no widget pumping,
// no sqflite.
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/chat/turn_merge.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

final _t1 = DateTime.utc(2026, 8, 13, 15, 31, 9);
final _t2 = DateTime.utc(2026, 8, 13, 15, 31, 12);

ChatMessage _row(
  String id,
  String role,
  String content, {
  DateTime? at,
  String? kind,
  List<ToolActivity> tools = const [],
}) =>
    ChatMessage(
      role: role,
      content: content,
      id: id,
      kind: kind,
      createdAt: at,
      toolActivities: tools,
    );

ToolActivity _chip(String name, {String? callId}) =>
    ToolActivity(name: name, args: const {}, toolCallId: callId);

void main() {
  group('mergeTurnRows — collapse', () {
    test('interim status + final reply collapse into one bubble', () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'Checking now…', at: _t1),
        _row('a2', 'assistant', 'Here is the answer.', at: _t1),
      ]);

      expect(merged, hasLength(1));
      expect(merged.single.content, 'Here is the answer.',
          reason: 'interim narration is dropped, the final reply survives');
      expect(merged.single.id, 'a2', reason: 'the LAST row keeps identity');
      expect(merged.single.absorbedIds, ['a1']);
    });

    test('three rows of one turn collapse; every absorbed id is recorded', () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'Checking…', at: _t1),
        _row('a2', 'assistant', 'Still working…', at: _t1),
        _row('a3', 'assistant', 'Done — 3 jobs found.', at: _t1),
      ]);

      expect(merged, hasLength(1));
      expect(merged.single.content, 'Done — 3 jobs found.');
      expect(merged.single.absorbedIds, ['a1', 'a2']);
    });

    test('chips union across the turn, de-duped by toolCallId', () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'Checking now…', at: _t1, tools: [
          _chip('upwork_last_conversation', callId: 'c1'),
          _chip('search_tools', callId: 'c2'),
        ]),
        _row('a2', 'assistant', 'Final.', at: _t1, tools: [
          // c2 re-persisted on the final row — must NOT double up.
          _chip('search_tools', callId: 'c2'),
          _chip('send_message', callId: 'c3'),
        ]),
      ]);

      expect(merged.single.toolActivities.map((t) => t.toolCallId),
          ['c1', 'c2', 'c3']);
    });

    test('chips without a toolCallId are all kept (nothing to dedup by)', () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'x', at: _t1, tools: [_chip('browser')]),
        _row('a2', 'assistant', 'y', at: _t1, tools: [_chip('browser')]),
      ]);
      expect(merged.single.toolActivities, hasLength(2));
    });

    test('a chips-only trailing row falls back to the newest row with text',
        () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'The real answer.', at: _t1),
        _row('a2', 'assistant', '', at: _t1, tools: [_chip('save_memory')]),
      ]);
      expect(merged, hasLength(1));
      expect(merged.single.content, 'The real answer.');
      expect(merged.single.id, 'a2');
      expect(merged.single.toolActivities, hasLength(1));
    });

    test('display text drives the "non-empty" pick, not raw content', () {
      // A row whose entire content is an internal <plan> block renders empty —
      // it must not win the content slot over the real reply.
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'Real reply.', at: _t1),
        _row('a2', 'assistant', '<plan>step 1\nstep 2</plan>', at: _t1),
      ]);
      expect(merged.single.displayContent, 'Real reply.');
    });
  });

  group('mergeTurnRows — never merges', () {
    test('rows with DIFFERENT timestamps stay separate', () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'first turn', at: _t1),
        _row('a2', 'assistant', 'second turn', at: _t2),
      ]);
      expect(merged.map((m) => m.id), ['a1', 'a2']);
      expect(merged.every((m) => m.absorbedIds.isEmpty), isTrue);
    });

    test('a notification row never merges with a normal assistant row', () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'Checking now…', at: _t1),
        _row('n1', 'assistant', 'Reminder\nPay the invoice',
            at: _t1, kind: 'notification'),
      ]);
      expect(merged.map((m) => m.id), ['a1', 'n1']);
      expect(merged.last.kind, 'notification');
    });

    test('two notification rows in the same second each keep their bubble', () {
      final merged = mergeTurnRows([
        _row('n1', 'assistant', 'Reminder\nStandup', at: _t1,
            kind: 'notification'),
        _row('n2', 'assistant', 'Watcher\nNew job posted', at: _t1,
            kind: 'notification'),
      ]);
      expect(merged, hasLength(2),
          reason: 'collapsing pings would silently drop one of them');
    });

    test('user rows are untouched and break the grouping', () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'reply one', at: _t1),
        _row('u1', 'user', 'follow-up', at: _t1),
        _row('a2', 'assistant', 'reply two', at: _t1),
      ]);
      expect(merged.map((m) => m.id), ['a1', 'u1', 'a2']);
    });

    test('cron system rows are untouched', () {
      final merged = mergeTurnRows([
        _row('c1', 'user', '[JOB:daily] run it', at: _t1, kind: 'cron'),
        _row('a1', 'assistant', 'ran it', at: _t1),
      ]);
      expect(merged.map((m) => m.id), ['c1', 'a1']);
      expect(merged.first.kind, 'cron');
    });

    test('rows with a null createdAt never merge (legacy timestamps)', () {
      final merged = mergeTurnRows([
        _row('a1', 'assistant', 'one'),
        _row('a2', 'assistant', 'two'),
      ]);
      expect(merged.map((m) => m.id), ['a1', 'a2']);
    });

    test('a single-row turn passes through unchanged', () {
      final rows = [_row('a1', 'assistant', 'solo', at: _t1)];
      final merged = mergeTurnRows(rows);
      expect(merged, hasLength(1));
      expect(identical(merged.single, rows.single), isTrue);
      expect(merged.single.absorbedIds, isEmpty);
    });

    test('empty and single-element inputs are no-ops', () {
      expect(mergeTurnRows(const []), isEmpty);
      final one = [_row('a1', 'assistant', 'x', at: _t1)];
      expect(mergeTurnRows(one), same(one));
    });

    test('the input list is never mutated', () {
      final rows = [
        _row('a1', 'assistant', 'Checking…', at: _t1),
        _row('a2', 'assistant', 'Final.', at: _t1),
      ];
      mergeTurnRows(rows);
      expect(rows, hasLength(2));
      expect(rows.first.content, 'Checking…');
    });
  });

  group('mergeTurnRows — mixed transcript', () {
    test('two turns around a user message collapse independently', () {
      final t3 = DateTime.utc(2026, 8, 13, 15, 40, 0);
      final merged = mergeTurnRows([
        _row('u1', 'user', 'first question', at: _t1),
        _row('a1', 'assistant', 'Checking…', at: _t1),
        _row('a2', 'assistant', 'Answer one.', at: _t1),
        _row('u2', 'user', 'second question', at: t3),
        _row('a3', 'assistant', 'On it…', at: t3),
        _row('a4', 'assistant', 'Answer two.', at: t3),
      ]);
      expect(merged.map((m) => m.id), ['u1', 'a2', 'u2', 'a4']);
      expect(merged[1].absorbedIds, ['a1']);
      expect(merged[3].absorbedIds, ['a3']);
    });
  });

  group('absorbedIds dedup contract (mergeHistoryTail)', () {
    test('re-fetching a merged turn inserts NOTHING — absorbed ids count as '
        'known', () {
      final tail = mergeTurnRows([
        _row('u1', 'user', 'question', at: _t1),
        _row('a1', 'assistant', 'Checking now…', at: _t1),
        _row('a2', 'assistant', 'The answer.', at: _t1),
      ]);
      expect(tail, hasLength(2));

      final r = ChatReducer();
      r.seedHistory(tail);
      expect(r.mergeHistoryTail(tail), isFalse,
          reason: 'nothing new — the collapsed turn is already present');
      expect(r.messages, hasLength(2));
      expect(r.messages.map((m) => m.id), ['u1', 'a2']);
    });

    test('an UNMERGED interim row arriving later is still recognized by its '
        'absorbed id', () {
      // Defensive: even if a raw (unmerged) tail ever reaches the reducer, the
      // interim row it already absorbed must not re-appear as a bubble.
      final r = ChatReducer();
      r.seedHistory(mergeTurnRows([
        _row('a1', 'assistant', 'Checking now…', at: _t1),
        _row('a2', 'assistant', 'The answer.', at: _t1),
      ]));
      final changed = r.mergeHistoryTail([
        _row('a1', 'assistant', 'Checking now…', at: _t1),
      ]);
      expect(changed, isFalse);
      expect(r.messages, hasLength(1));
    });

    test('a genuinely new turn still merges in after a collapsed one', () {
      final r = ChatReducer();
      r.seedHistory(mergeTurnRows([
        _row('a1', 'assistant', 'Checking…', at: _t1),
        _row('a2', 'assistant', 'Answer one.', at: _t1),
      ]));
      final changed = r.mergeHistoryTail(mergeTurnRows([
        _row('a1', 'assistant', 'Checking…', at: _t1),
        _row('a2', 'assistant', 'Answer one.', at: _t1),
        _row('a3', 'assistant', 'Working…', at: _t2),
        _row('a4', 'assistant', 'Answer two.', at: _t2),
      ]));
      expect(changed, isTrue);
      expect(r.messages.map((m) => m.id), ['a2', 'a4']);
      expect(r.messages.last.absorbedIds, ['a3']);
    });

    test('an interim row inserted MID-TURN is retroactively absorbed', () {
      // A refresh can land before the turn's final row exists server-side, so
      // the interim status row inserts as its own bubble. When the completed
      // turn arrives collapsed, that stray bubble must disappear — not sit
      // beside the merged one.
      final r = ChatReducer();
      r.seedHistory([_row('u1', 'user', 'question', at: _t1)]);
      expect(
          r.mergeHistoryTail(
              [_row('a1', 'assistant', 'Checking now…', at: _t1)]),
          isTrue);
      expect(r.messages.map((m) => m.id), ['u1', 'a1']);

      final changed = r.mergeHistoryTail(mergeTurnRows([
        _row('a1', 'assistant', 'Checking now…', at: _t1),
        _row('a2', 'assistant', 'The answer.', at: _t1),
      ]));

      expect(changed, isTrue);
      expect(r.messages.map((m) => m.id), ['u1', 'a2'],
          reason: 'the standalone interim bubble was absorbed');
      expect(r.messages.last.content, 'The answer.');
    });

    test('retroactive absorption never touches a live streaming bubble', () {
      final r = ChatReducer();
      r.onUserSend('go');
      r.onFrame(const TokenFrame('partial'));
      final changed = r.mergeHistoryTail(mergeTurnRows([
        _row('a1', 'assistant', 'Checking now…', at: _t1),
        _row('a2', 'assistant', 'Unrelated older turn.', at: _t1),
      ]));
      expect(changed, isTrue);
      expect(r.messages.last.streaming, isTrue);
      expect(r.messages.last.content, 'partial');
    });

    test('a live bubble adopting a merged row also adopts its absorbed ids',
        () {
      final r = ChatReducer();
      r.onUserSend('hello there');
      r.onFrame(const DoneFrame('The answer.', null));

      final tail = mergeTurnRows([
        _row('u1', 'user', 'hello there', at: _t1),
        _row('a1', 'assistant', 'Checking now…', at: _t1),
        _row('a2', 'assistant', 'The answer.', at: _t1),
      ]);
      expect(r.mergeHistoryTail(tail), isTrue);
      expect(r.messages, hasLength(2), reason: 'no duplicate bubbles');
      expect(r.messages.last.id, 'a2');
      expect(r.messages.last.absorbedIds, ['a1'],
          reason: 'the adopted identity carries the absorbed row ids');
      // And a re-fetch is now a pure no-op.
      expect(r.mergeHistoryTail(tail), isFalse);
    });
  });
}
