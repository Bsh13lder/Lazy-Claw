// Proactive-ping delivery + stale-chat fix: history DELTA-merge by message
// id (ChatReducer.mergeHistoryTail), the reworked seed-once/merge-always
// guard, and the controller's refresh triggers (notification frame, WS
// reconnect, unconditional refreshHistory for app resume).
//
// HAZARD notes honored: everything here is plain `test` (no FakeAsync /
// widget pumping) with fake sinks and fake loaders — no real network or DB.
import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

ChatMessage _row(String id, String role, String content, {String? kind}) =>
    ChatMessage(role: role, content: content, id: id, kind: kind);

void main() {
  group('ChatReducer.mergeHistoryTail', () {
    test('empty tail is a no-op', () {
      final r = ChatReducer();
      expect(r.mergeHistoryTail(const []), isFalse);
      expect(r.messages, isEmpty);
    });

    test('re-fetching the same tail dedupes by id (no duplicates)', () {
      final r = ChatReducer();
      final tail = [
        _row('m1', 'user', 'hello'),
        _row('m2', 'assistant', 'hi there'),
      ];
      r.seedHistory(tail);
      expect(r.mergeHistoryTail(tail), isFalse);
      expect(r.messages, hasLength(2));
      expect(r.messages.map((m) => m.id), ['m1', 'm2']);
    });

    test('new id-carrying rows append in order at the end', () {
      final r = ChatReducer();
      r.seedHistory([_row('m1', 'user', 'q')]);
      final changed = r.mergeHistoryTail([
        _row('m1', 'user', 'q'),
        _row('m2', 'assistant', 'a'),
        _row('m3', 'assistant', 'Reminder\nPay invoice', kind: 'notification'),
      ]);
      expect(changed, isTrue);
      expect(r.messages.map((m) => m.id), ['m1', 'm2', 'm3']);
      expect(r.messages.last.kind, 'notification',
          reason: 'kind survives the merge so rendering stays distinct');
    });

    test('merge inserts BEFORE a trailing streaming bubble and later tokens '
        'still land on that bubble', () {
      final r = ChatReducer();
      r.onUserSend('live question'); // user bubble + streaming assistant
      r.onFrame(const TokenFrame('partial '));

      r.mergeHistoryTail([
        _row('n1', 'assistant', 'Watcher alert\nNew job posted',
            kind: 'notification'),
      ]);

      // The streaming bubble is still LAST; the merged row sits above it.
      expect(r.messages.last.streaming, isTrue);
      expect(r.messages[r.messages.length - 2].id, 'n1');

      // Token/done frames keep landing on the streaming bubble, not the
      // merged row.
      r.onFrame(const TokenFrame('answer'));
      r.onFrame(const DoneFrame('partial answer', null));
      expect(r.messages.last.content, 'partial answer');
      expect(r.messages.last.streaming, isFalse);
      final merged = r.messages.firstWhere((m) => m.id == 'n1');
      expect(merged.content, 'Watcher alert\nNew job posted',
          reason: 'merge never clobbers or gets clobbered by the live turn');
    });

    test('adopts the server id onto an id-less live bubble with the same '
        'content instead of duplicating it', () {
      final r = ChatReducer();
      r.onUserSend('hello there');
      r.onFrame(const DoneFrame('hi!', null));

      final changed = r.mergeHistoryTail([
        _row('u1', 'user', 'hello there'),
        _row('a1', 'assistant', 'hi!'),
      ]);

      expect(changed, isTrue);
      expect(r.messages, hasLength(2), reason: 'no duplicate bubbles');
      expect(r.messages[0].id, 'u1');
      expect(r.messages[1].id, 'a1');
      // A second merge is now a pure id-dedup no-op.
      expect(
          r.mergeHistoryTail(
              [_row('u1', 'user', 'hello there'), _row('a1', 'assistant', 'hi!')]),
          isFalse);
    });

    test('adoption normalizes whitespace when matching', () {
      final r = ChatReducer();
      r.onUserSend('two  words');
      r.onFrame(const DoneFrame('ok', null));
      r.mergeHistoryTail([_row('u1', 'user', 'two words')]);
      expect(r.messages, hasLength(2));
      expect(r.messages.first.id, 'u1');
    });

    test('a fetched row matching a STILL-STREAMING bubble is skipped '
        '(no insert, no adopt)', () {
      final r = ChatReducer();
      r.onUserSend('go');
      r.onFrame(const TokenFrame('the full reply'));

      // Server persisted the assistant row before the done frame arrived.
      final changed =
          r.mergeHistoryTail([_row('a1', 'assistant', 'the full reply')]);

      expect(changed, isFalse);
      expect(r.messages, hasLength(2), reason: 'no duplicate of the live turn');
      expect(r.messages.last.streaming, isTrue);
      expect(r.messages.last.id, isNull,
          reason: 'the done frame owns finalization of a streaming bubble');
    });

    test('id-less fetched rows that match nothing are dropped, not inserted',
        () {
      final r = ChatReducer();
      r.seedHistory([_row('m1', 'user', 'a')]);
      final changed = r.mergeHistoryTail(
          const [ChatMessage(role: 'assistant', content: 'no id row')]);
      expect(changed, isFalse);
      expect(r.messages, hasLength(1),
          reason: 'inserting id-less rows would duplicate on every re-fetch');
    });
  });

  group('ChatController seed-once / merge-always guard', () {
    test('first call seeds, later calls delta-merge new rows in', () {
      final socket = ChatSocket();
      final c = ChatController(socket);
      c.seedHistory([_row('m1', 'user', 'one')]);
      c.seedHistory([
        _row('m1', 'user', 'one'),
        _row('m2', 'assistant', 'Reminder\nStandup at 10', kind: 'notification'),
      ]);
      expect(c.state.map((m) => m.id), ['m1', 'm2']);
      expect(c.state.last.kind, 'notification');
      c.dispose();
      socket.dispose();
    });

    test('an empty first seed still marks seeded; later merges still land',
        () {
      final socket = ChatSocket();
      final c = ChatController(socket);
      c.seedHistory(const []);
      c.seedHistory([_row('m9', 'assistant', 'late row')]);
      expect(c.state, hasLength(1));
      expect(c.state.single.id, 'm9');
      c.dispose();
      socket.dispose();
    });
  });

  group('ChatController.refreshHistory', () {
    test('seeds on first run, merges on later runs — unconditional (resume '
        'path calls it without any change-detector)', () async {
      final socket = ChatSocket();
      var call = 0;
      final c = ChatController(socket, historyLoader: () async {
        call++;
        return [
          _row('m1', 'user', 'q'),
          if (call >= 2) _row('m2', 'assistant', 'proactive ping'),
        ];
      });

      await c.refreshHistory();
      expect(c.state.map((m) => m.id), ['m1']);

      // Second unconditional refresh (app resume) merges only the delta.
      await c.refreshHistory();
      expect(c.state.map((m) => m.id), ['m1', 'm2']);

      // Third refresh with identical data changes nothing.
      await c.refreshHistory();
      expect(c.state, hasLength(2));
      expect(call, 3);
      c.dispose();
      socket.dispose();
    });

    test('concurrent triggers coalesce into one trailing re-fetch, never '
        'overlapping fetches', () async {
      final socket = ChatSocket();
      var inFlight = 0;
      var maxInFlight = 0;
      var calls = 0;
      final gate = Completer<void>();
      final c = ChatController(socket, historyLoader: () async {
        calls++;
        inFlight++;
        maxInFlight = inFlight > maxInFlight ? inFlight : maxInFlight;
        if (calls == 1) await gate.future;
        inFlight--;
        return [_row('m1', 'user', 'q')];
      });

      final first = c.refreshHistory();
      // Three more triggers while the first fetch is pending → one queued.
      unawaited(c.refreshHistory());
      unawaited(c.refreshHistory());
      unawaited(c.refreshHistory());
      gate.complete();
      await first;
      await pumpEventQueue();

      expect(calls, 2, reason: 'pending + one coalesced trailing re-fetch');
      expect(maxInFlight, 1, reason: 'fetches are serialized');
      c.dispose();
      socket.dispose();
    });

    test('a failing loader is swallowed and the next trigger retries', () async {
      final socket = ChatSocket();
      var call = 0;
      final c = ChatController(socket, historyLoader: () async {
        call++;
        if (call == 1) throw Exception('network down');
        return [_row('m1', 'user', 'q')];
      });
      await c.refreshHistory(); // fails silently
      expect(c.state, isEmpty);
      await c.refreshHistory(); // retry succeeds
      expect(c.state, hasLength(1));
      c.dispose();
      socket.dispose();
    });
  });

  group('notification frame + reconnect triggers', () {
    test('a notification frame triggers a history refresh and the merged row '
        'appears in the chat (no bubble minted from the frame itself)',
        () async {
      final incoming = StreamController<dynamic>.broadcast();
      final socket = ChatSocket(
        channelFactory: (url, headers) => FakeSink(incoming.stream, []),
      );
      var loaderCalls = 0;
      final c = ChatController(
        socket,
        notificationFollowUp: const Duration(milliseconds: 15),
        historyLoader: () async {
          loaderCalls++;
          return [
            _row('m1', 'user', 'earlier question'),
            if (loaderCalls >= 2)
              _row('n1', 'assistant', 'Cron result\n3 new jobs found',
                  kind: 'notification'),
          ];
        },
      );
      await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
      await pumpEventQueue();
      final callsAfterConnect = loaderCalls;
      expect(callsAfterConnect, greaterThanOrEqualTo(1),
          reason: 'connect (down→up edge) already refreshes once');

      incoming.add(jsonEncode({
        'type': 'notification',
        'id': 'n1',
        'kind': 'cron',
        'title': 'Cron result',
        'body': '3 new jobs found',
        'created_at': '2026-08-09T09:00:00Z',
      }));
      await pumpEventQueue();

      expect(loaderCalls, greaterThan(callsAfterConnect),
          reason: 'the notification frame triggered a delta refresh');
      expect(c.state.any((m) => m.id == 'n1' && m.kind == 'notification'),
          isTrue);
      // Exactly ONE row for the ping — frame itself painted nothing.
      expect(c.state.where((m) => m.content.contains('3 new jobs found')),
          hasLength(1));

      // The settle-delayed follow-up refresh also fires (persist-race guard).
      final beforeFollowUp = loaderCalls;
      await Future<void>.delayed(const Duration(milliseconds: 60));
      expect(loaderCalls, greaterThan(beforeFollowUp));

      c.dispose();
      await incoming.close();
      await socket.dispose();
    });

    test('a notification frame never fires the local-banner callback '
        '(feed poller owns banners)', () async {
      final incoming = StreamController<dynamic>.broadcast();
      final socket = ChatSocket(
        channelFactory: (url, headers) => FakeSink(incoming.stream, []),
      );
      final banners = <String>[];
      final c = ChatController(
        socket,
        onNotify: (title, body) => banners.add(title),
        historyLoader: () async => const [],
      );
      await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
      incoming.add(jsonEncode(
          {'type': 'notification', 'id': 'n2', 'title': 'Ping', 'body': 'b'}));
      await pumpEventQueue();
      expect(banners, isEmpty);
      c.dispose();
      await incoming.close();
      await socket.dispose();
    });

    test('WS reconnect (down→up edge) triggers another refresh', () async {
      var dial = 0;
      final incomings = <StreamController<dynamic>>[];
      final socket = ChatSocket(
        channelFactory: (url, headers) {
          dial++;
          final ctrl = StreamController<dynamic>.broadcast();
          incomings.add(ctrl);
          return FakeSink(ctrl.stream, []);
        },
        baseBackoff: const Duration(milliseconds: 5),
        maxBackoff: const Duration(milliseconds: 10),
      );
      var loaderCalls = 0;
      final c = ChatController(socket, historyLoader: () async {
        loaderCalls++;
        return const [];
      });

      await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
      await pumpEventQueue();
      final afterFirstConnect = loaderCalls;
      expect(afterFirstConnect, greaterThanOrEqualTo(1));

      // Drop the channel — the socket auto-reconnects after the backoff.
      await incomings.first.close();
      await Future<void>.delayed(const Duration(milliseconds: 40));
      await pumpEventQueue();

      expect(dial, greaterThanOrEqualTo(2), reason: 'socket re-dialled');
      expect(loaderCalls, greaterThan(afterFirstConnect),
          reason: 'the reconnect edge merge-refreshed history');

      c.dispose();
      for (final ctrl in incomings) {
        if (!ctrl.isClosed) await ctrl.close();
      }
      await socket.dispose();
    });
  });
}
