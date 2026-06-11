import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/chat/usage_info.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  test('user send then streamed tokens then done builds two messages', () {
    final c = ChatReducer();
    c.onUserSend('hello');
    expect(c.messages.length, 2); // user + empty streaming assistant
    expect(c.messages.first.role, 'user');
    expect(c.messages.last.streaming, isTrue);

    c.onFrame(const TokenFrame('Hi '));
    c.onFrame(const TokenFrame('there'));
    expect(c.messages.last.content, 'Hi there');

    c.onFrame(const DoneFrame('Hi there', 'claude'));
    expect(c.messages.last.streaming, isFalse);
    expect(c.messages.last.content, 'Hi there');
  });

  test('done with empty content keeps streamed buffer', () {
    final c = ChatReducer();
    c.onUserSend('x');
    c.onFrame(const TokenFrame('buffered'));
    c.onFrame(const DoneFrame('', null));
    expect(c.messages.last.content, 'buffered');
  });

  test('approval_request surfaces a pending approval on the assistant msg', () {
    final c = ChatReducer();
    c.onUserSend('do it');
    c.onFrame(const ApprovalRequestFrame('req1', 'send_email', {}));
    expect(c.messages.last.pendingApprovalId, 'req1');
    expect(c.messages.last.pendingApprovalSkill, 'send_email');
  });

  // HIGH-1: ErrorFrame before any onUserSend must NOT throw.
  test('ErrorFrame before any send does not throw and produces an assistant bubble', () {
    final c = ChatReducer();
    expect(() => c.onFrame(const ErrorFrame('server error')), returnsNormally);
    expect(c.messages.length, 1);
    expect(c.messages.first.role, 'assistant');
    expect(c.messages.first.content, contains('server error'));
    expect(c.messages.first.streaming, isFalse);
  });

  // HIGH-1: TokenFrame before any onUserSend must NOT throw.
  test('TokenFrame before any send does not throw and leaves messages empty', () {
    final c = ChatReducer();
    expect(() => c.onFrame(const TokenFrame('early token')), returnsNormally);
    expect(c.messages, isEmpty);
  });

  // MEDIUM: respondApproval clears the pending approval fields.
  test('approval fields are null after respondApproval clears them', () {
    final c = ChatReducer();
    c.onUserSend('do it');
    c.onFrame(const ApprovalRequestFrame('req42', 'send_email', {}));
    expect(c.messages.last.pendingApprovalId, 'req42');

    // Simulate the controller's clearApproval path directly on the reducer.
    final idx = c.messages.indexWhere((m) => m.pendingApprovalId == 'req42');
    expect(idx, isNot(-1));
    c.messages[idx] = c.messages[idx].clearApproval();

    expect(c.messages[idx].pendingApprovalId, isNull);
    expect(c.messages[idx].pendingApprovalSkill, isNull);
  });

  // ── Live activity streaming (phase / thinking / specialists / bg) ─────────

  test('phase frame sets the streaming bubble phase', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const PhaseFrame('act', 2));
    expect(c.messages.last.phase, 'act');
    expect(c.messages.last.streaming, isTrue);
  });

  test('phase frame before any send creates a streaming bubble', () {
    final c = ChatReducer();
    c.onFrame(const PhaseFrame('think', 1));
    expect(c.messages.length, 1);
    expect(c.messages.last.role, 'assistant');
    expect(c.messages.last.phase, 'think');
  });

  test('thinking_delta turns on thinking; token turns it off', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const ThinkingDeltaFrame('reasoning…'));
    expect(c.messages.last.thinking, isTrue);
    c.onFrame(const TokenFrame('Hello'));
    expect(c.messages.last.thinking, isFalse);
    expect(c.messages.last.content, 'Hello');
  });

  test('thinking_done clears the thinking flag', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const ThinkingDeltaFrame('x'));
    c.onFrame(const ThinkingDoneFrame());
    expect(c.messages.last.thinking, isFalse);
  });

  test('agent activity rows upsert by subject instead of stacking', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research', detail: 'started'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research', detail: 'using web_search'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'research',
        detail: 'finished',
        done: true));
    final acts = c.messages.last.agentActivities;
    expect(acts.length, 1);
    expect(acts.single.detail, 'finished');
    expect(acts.single.done, isTrue);
  });

  test('distinct subjects get their own activity rows', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research', detail: 'started'));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'check whatsapp', detail: 'using whatsapp_read'));
    expect(c.messages.last.agentActivities.length, 2);
  });

  test('activity survives token streaming and stays after done', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research', detail: 'started'));
    c.onFrame(const TokenFrame('Working on it'));
    c.onFrame(const DoneFrame('Here is the answer', null));
    expect(c.messages.last.agentActivities.length, 1);
    expect(c.messages.last.streaming, isFalse);
    expect(c.messages.last.content, 'Here is the answer');
  });

  // ── Thinking accumulation ──────────────────────────────────────────────────

  test('thinking deltas accumulate into thinkingText', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const ThinkingDeltaFrame('Let me '));
    c.onFrame(const ThinkingDeltaFrame('reason about this.'));
    expect(c.messages.last.thinkingText, 'Let me reason about this.');
    expect(c.messages.last.thinking, isTrue);
  });

  test('thinking_done clears the live flag but keeps the text', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const ThinkingDeltaFrame('hmm'));
    c.onFrame(const ThinkingDoneFrame());
    expect(c.messages.last.thinking, isFalse);
    expect(c.messages.last.thinkingText, 'hmm');
  });

  test('thinking text survives tokens and done', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const ThinkingDeltaFrame('plan: search first'));
    c.onFrame(const TokenFrame('Searching…'));
    c.onFrame(const DoneFrame('Found it', null));
    expect(c.messages.last.thinkingText, 'plan: search first');
    expect(c.messages.last.thinking, isFalse);
  });

  // ── Usage metrics ──────────────────────────────────────────────────────────

  test('usage on the done payload attaches to the message', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const DoneFrame('reply', null,
        usage: UsageInfo(totalTokens: 4200, llmCalls: 3)));
    expect(c.messages.last.usage, isNotNull);
    expect(c.messages.last.usage!.totalTokens, 4200);
  });

  test('standalone usage frame is stashed and attached on done', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const UsageFrame(UsageInfo(totalTokens: 99, cost: 0.01)));
    expect(c.messages.last.usage, isNull, reason: 'not attached mid-stream');
    c.onFrame(const DoneFrame('reply', null));
    expect(c.messages.last.usage!.totalTokens, 99);
  });

  test('done-payload usage wins over an earlier usage frame', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const UsageFrame(UsageInfo(totalTokens: 1)));
    c.onFrame(
        const DoneFrame('reply', null, usage: UsageInfo(totalTokens: 2)));
    expect(c.messages.last.usage!.totalTokens, 2);
  });

  test('stashed usage does not leak into the next turn', () {
    final c = ChatReducer();
    c.onUserSend('one');
    c.onFrame(const UsageFrame(UsageInfo(totalTokens: 5)));
    c.onFrame(const DoneFrame('a', null));
    c.onUserSend('two');
    c.onFrame(const DoneFrame('b', null));
    expect(c.messages.last.usage, isNull);
  });

  // ── Plan question / approved ───────────────────────────────────────────────

  test('plan_question adds a question-kind plan message', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const PlanQuestionFrame('Which account should I use?'));
    expect(c.messages.last.role, 'plan');
    expect(c.messages.last.planKind, 'question');
    expect(c.messages.last.planText, 'Which account should I use?');
  });

  test('plan_approved resolves the latest pending plan card', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const PlanPendingFrame('Do A then B', ['A', 'B']));
    expect(c.messages.last.planResolved, isFalse);
    c.onFrame(const PlanApprovedFrame(false));
    final plan = c.messages.lastWhere((m) => m.role == 'plan');
    expect(plan.planResolved, isTrue);
  });

  test('plan_approved with no pending plan is a safe no-op', () {
    final c = ChatReducer();
    c.onUserSend('go');
    expect(() => c.onFrame(const PlanApprovedFrame(true)), returnsNormally);
  });

  test('plan_approved does not resolve a question card', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const PlanQuestionFrame('Which one?'));
    c.onFrame(const PlanApprovedFrame(false));
    expect(c.messages.last.planResolved, isFalse);
  });

  // ── Activity timeline (chronological events + tool counting) ──────────────

  test('activity rows keep a chronological event log behind the upsert', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research', detail: 'started'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'research',
        detail: 'using web_search',
        tool: 'web_search'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'research',
        detail: 'using browser',
        tool: 'browser'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'research',
        detail: 'finished',
        done: true));

    final acts = c.messages.last.agentActivities;
    expect(acts.length, 1, reason: 'still one row per subject');
    expect(acts.single.events,
        ['started', 'using web_search', 'using browser', 'finished']);
    expect(acts.single.toolsUsed, ['web_search', 'browser']);
    expect(acts.single.done, isTrue);
  });

  test('browser activity upserts onto a single browser row', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'browser', subject: 'browser', detail: 'goto upwork.com'));
    c.onFrame(const AgentActivityFrame(
        kind: 'browser', subject: 'browser', detail: 'Clicked Sign in'));
    final acts = c.messages.last.agentActivities;
    expect(acts.length, 1);
    expect(acts.single.detail, 'Clicked Sign in');
    expect(acts.single.events, ['goto upwork.com', 'Clicked Sign in']);
  });

  // ── Background completion folds the timeline into the card ────────────────

  test('background_done attaches the captured timeline and settles the row',
      () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'check whatsapp', detail: 'started'));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg',
        subject: 'check whatsapp',
        detail: 'using whatsapp_read',
        tool: 'whatsapp_read'));
    c.onFrame(const BackgroundDoneFrame('check whatsapp', 't1', '3 chats', 900));

    final card = c.messages.last.bgTaskResult!;
    expect(card.events, ['started', 'using whatsapp_read']);
    expect(card.toolsUsed, ['whatsapp_read']);

    // The live row in the bubble is settled too — no orphaned spinner.
    final bubble =
        c.messages.lastWhere((m) => m.agentActivities.isNotEmpty);
    expect(bubble.agentActivities.single.done, isTrue);
    expect(bubble.agentActivities.single.failed, isFalse);
  });

  test('background_failed marks the matching row failed', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'send email', detail: 'started'));
    c.onFrame(
        const BackgroundFailedFrame('send email', 't2', 'SMTP down', 100));

    expect(c.messages.last.bgTaskResult!.success, isFalse);
    final bubble =
        c.messages.lastWhere((m) => m.agentActivities.isNotEmpty);
    expect(bubble.agentActivities.single.failed, isTrue);
  });

  test('background_done without a matching row still renders a card', () {
    final c = ChatReducer();
    c.onFrame(const BackgroundDoneFrame('cron job', 't3', 'ok', null));
    expect(c.messages.last.bgTaskResult, isNotNull);
    expect(c.messages.last.bgTaskResult!.events, isEmpty);
  });

  // ── Duplicate delivery: bg card repeating the consolidated reply ──────────
  // Heartbeat/scheduled tasks send the SAME text twice: once as the
  // consolidated assistant message and again as the BackgroundDoneFrame
  // result. The card must still appear (the task settled) but flagged so
  // the widget collapses to header-only instead of repeating the wall of text.

  const briefing = 'Daily briefing: 3 tasks due today, 2 overdue. '
      'Focus on the eStreet bot delivery and the invoice follow-up.';

  test('bg result matching the previous assistant reply is flagged duplicate',
      () {
    final c = ChatReducer();
    c.onUserSend('briefing');
    c.onFrame(const DoneFrame(briefing, null));
    c.onFrame(const BackgroundDoneFrame('Task Guardian', 't9', briefing, 1200));

    final card = c.messages.last.bgTaskResult!;
    expect(card.duplicateOfReply, isTrue);
    expect(card.detail, briefing, reason: 'data kept, only rendering changes');
  });

  test('duplicate check normalizes whitespace and accepts containment', () {
    final c = ChatReducer();
    c.onUserSend('briefing');
    c.onFrame(const DoneFrame('Intro line.\n\n$briefing\n\nOutro.', null));
    // Result is a whitespace-mangled substring of the reply.
    c.onFrame(BackgroundDoneFrame(
        'Task Guardian', 't9', briefing.replaceAll(' ', '  \n'), 1200));
    expect(c.messages.last.bgTaskResult!.duplicateOfReply, isTrue);
  });

  test('duplicate check scans the last few assistant messages, not just one',
      () {
    final c = ChatReducer();
    c.onUserSend('briefing');
    c.onFrame(const DoneFrame(briefing, null));
    c.onUserSend('thanks');
    c.onFrame(const DoneFrame('You are welcome!', null));
    c.onFrame(const BackgroundDoneFrame('Task Guardian', 't9', briefing, 1200));
    expect(c.messages.last.bgTaskResult!.duplicateOfReply, isTrue);
  });

  test('bg result with distinct content keeps the full card', () {
    final c = ChatReducer();
    c.onUserSend('briefing');
    c.onFrame(const DoneFrame(briefing, null));
    c.onFrame(const BackgroundDoneFrame('check whatsapp', 't9',
        'Found 4 unread chats: Buchvardi, James, and two groups.', 800));
    expect(c.messages.last.bgTaskResult!.duplicateOfReply, isFalse);
  });

  test('short generic results are never flagged duplicate', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const DoneFrame('ok, all done here', null));
    c.onFrame(const BackgroundDoneFrame('tiny job', 't1', 'done', 50));
    expect(c.messages.last.bgTaskResult!.duplicateOfReply, isFalse,
        reason: 'a tiny "done"-style detail must not collapse the card');
  });

  // ── Terminal frames with no prior row (settle-or-surface + dedup) ─────────
  // A task that completed while the chat was closed must still show up in the
  // thread (the server never replays background_done on reconnect) — but a
  // reconnect replay of the same terminal frame must NOT re-create the card,
  // and the surfaced row must never spin (it arrives already settled).

  test('first-seen terminal frame with no prior row appends ONE settled row',
      () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const DoneFrame('all done', null));

    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'Task Guardian', detail: 'done', done: true));

    final carriers =
        c.messages.where((m) => m.agentActivities.isNotEmpty).toList();
    expect(carriers.length, 1, reason: 'exactly one message carries the row');
    final row = carriers.single.agentActivities.single;
    expect(row.subject, 'Task Guardian');
    expect(row.done, isTrue);
    expect(row.failed, isFalse);
    expect(row.detail, 'done', reason: 'frame payload reaches the user');
    expect(c.isStreaming, isFalse,
        reason: 'surfacing a settled row must not start a phantom spinner');
    expect(c.messages.any((m) => m.streaming), isFalse);
  });

  test('replayed terminal frame does not create a second row or re-grow it',
      () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const DoneFrame('all done', null));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'stale task', detail: 'done', done: true));
    final count = c.messages.length;
    final eventsBefore = c.messages
        .lastWhere((m) => m.agentActivities.isNotEmpty)
        .agentActivities
        .single
        .events
        .length;

    // WS reconnect replays the same task_completed frame.
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'stale task', detail: 'done', done: true));

    expect(c.messages.length, count, reason: 'no second card on replay');
    final carriers =
        c.messages.where((m) => m.agentActivities.isNotEmpty).toList();
    expect(carriers.length, 1);
    expect(carriers.single.agentActivities.length, 1);
    expect(carriers.single.agentActivities.single.events.length, eventsBefore,
        reason: 'replay must not append duplicate event lines');
    expect(c.isStreaming, isFalse);
  });

  test('terminal frame on an empty reducer surfaces one settled message', () {
    final c = ChatReducer();
    expect(
        () => c.onFrame(const AgentActivityFrame(
            kind: 'specialist',
            subject: 'research',
            detail: 'failed',
            done: true,
            failed: true)),
        returnsNormally);
    expect(c.messages.length, 1);
    expect(c.messages.single.role, 'assistant');
    expect(c.messages.single.streaming, isFalse);
    expect(c.messages.single.agentActivities.single.failed, isTrue);
    expect(c.isStreaming, isFalse);
  });

  test('settling an existing row records the dedup key — replay is a no-op',
      () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'long job', detail: 'started'));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'long job', detail: 'finished', done: true));
    final count = c.messages.length;
    final bubble = c.messages.lastWhere((m) => m.agentActivities.isNotEmpty);
    expect(bubble.agentActivities.single.done, isTrue);
    final eventsBefore = bubble.agentActivities.single.events.length;

    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'long job', detail: 'finished', done: true));

    expect(c.messages.length, count);
    final after = c.messages.lastWhere((m) => m.agentActivities.isNotEmpty);
    expect(after.agentActivities.single.events.length, eventsBefore);
  });

  test('background_done with no row blocks a later replayed terminal frame',
      () {
    final c = ChatReducer();
    c.onFrame(const BackgroundDoneFrame('cron job', 't3', 'ok', null));
    final count = c.messages.length;

    // Reconnect replays the matching task_completed frame — the bg card
    // already told the story, so no settled activity row appears.
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'cron job', detail: 'done', done: true));

    expect(c.messages.length, count);
    expect(c.messages.any((m) => m.agentActivities.isNotEmpty), isFalse);
  });

  test('terminal frame settles a row on an earlier message in place', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'long job', detail: 'started'));
    c.onFrame(const DoneFrame('answer', null));
    c.onUserSend('next question'); // fresh streaming bubble on top
    final count = c.messages.length;

    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'long job', detail: 'finished', done: true));

    expect(c.messages.length, count, reason: 'no new bubble');
    final earlier = c.messages.firstWhere((m) => m.agentActivities.isNotEmpty);
    expect(earlier.agentActivities.single.done, isTrue);
    expect(earlier.agentActivities.single.failed, isFalse);
    // The live streaming bubble was not touched.
    expect(c.messages.last.streaming, isTrue);
    expect(c.messages.last.agentActivities, isEmpty);
  });

  test('non-terminal frames keep the current ensure-bubble behavior', () {
    final c = ChatReducer();
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'watcher turn', detail: 'started'));
    expect(c.messages.length, 1);
    expect(c.messages.single.role, 'assistant');
    expect(c.messages.single.agentActivities.single.subject, 'watcher turn');
  });

  // ── Live tool visibility (currentTool + tool dedupe) ───────────────────────

  test('merge dedupes repeated tools, order preserved', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'research',
        detail: 'using web_search',
        tool: 'web_search'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'research',
        detail: 'using web_search',
        tool: 'web_search'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'research',
        detail: 'using browser',
        tool: 'browser'));
    final acts = c.messages.last.agentActivities;
    expect(acts.single.toolsUsed, ['web_search', 'browser']);
  });

  test('currentTool follows the latest tool frame and clears on non-tool',
      () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'research',
        detail: 'using web_search',
        tool: 'web_search'));
    expect(c.messages.last.agentActivities.single.currentTool, 'web_search');

    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research', detail: 'thinking…'));
    expect(c.messages.last.agentActivities.single.currentTool, isNull,
        reason: 'a frame without a tool clears the live tool');
  });

  test('settling via background_done clears currentTool', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'bg',
        subject: 'check whatsapp',
        detail: 'using whatsapp_read',
        tool: 'whatsapp_read'));
    c.onFrame(const BackgroundDoneFrame('check whatsapp', 't1', 'ok', 100));
    final bubble = c.messages.lastWhere((m) => m.agentActivities.isNotEmpty);
    expect(bubble.agentActivities.single.done, isTrue);
    expect(bubble.agentActivities.single.currentTool, isNull);
  });

  // ── Cancel / streaming state ───────────────────────────────────────────────

  test('isStreaming flips on send and off on done / cancelled', () {
    final c = ChatReducer();
    expect(c.isStreaming, isFalse);
    c.onUserSend('go');
    expect(c.isStreaming, isTrue);
    c.onFrame(const CancelledFrame());
    expect(c.isStreaming, isFalse);

    c.onUserSend('again');
    expect(c.isStreaming, isTrue);
    c.onFrame(const DoneFrame('done', null));
    expect(c.isStreaming, isFalse);
  });

  test('cancelled keeps the streamed partial content', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const TokenFrame('partial answer'));
    c.onFrame(const CancelledFrame());
    expect(c.messages.last.content, 'partial answer');
    expect(c.messages.last.streaming, isFalse);
  });

  test('ChatController.cancel sends a cancel frame over the socket',
      () async {
    final incoming = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(
      channelFactory: (url, headers) => FakeSink(incoming.stream, sent),
    );
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    final controller = ChatController(socket);

    controller.cancel();
    expect(sent.last, '{"type":"cancel"}');

    controller.dispose();
    await incoming.close();
    await socket.dispose();
  });
}
