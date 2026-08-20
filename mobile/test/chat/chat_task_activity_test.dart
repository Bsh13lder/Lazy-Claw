import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

/// Bug 4 (task_id keying) + Bug 2 (streaming bubble never clears on
/// background/specialist terminals) + Bug 5 (phantom bg bubble settles).
///
/// The frozen wire contract carries a stable `task_id` on every task_* / bg_*
/// frame. task_step/task_phase/task_completed do NOT carry `name`, so today the
/// client falls back to the literal subject `'task'` and the terminal can't
/// match the started row → the row spins forever. These tests pin the fix.
void main() {
  // ── ws_frames: task_id threads onto the parsed activity frame ──────────────

  test('parseServerFrame threads task_id onto task_* frames', () {
    final started = parseServerFrame(
            '{"type":"task_started","task_id":"T","name":"Foo","description":"do it"}')
        as AgentActivityFrame;
    expect(started.taskId, 'T');

    final step = parseServerFrame(
            '{"type":"task_step","task_id":"T","step":"reading inbox"}')
        as AgentActivityFrame;
    expect(step.taskId, 'T', reason: 'task_step carries task_id even w/o name');
    expect(step.subject, 'task', reason: 'no name → literal subject fallback');

    final completed = parseServerFrame(
            '{"type":"task_completed","task_id":"T","status":"done"}')
        as AgentActivityFrame;
    expect(completed.taskId, 'T');
    expect(completed.done, isTrue);
  });

  test('parseServerFrame threads task_id onto bg_* frames', () {
    final call = parseServerFrame(
            '{"type":"bg_tool_call","task_id":"T","task_name":"job","name":"browser","args":{}}')
        as AgentActivityFrame;
    expect(call.taskId, 'T');

    final started = parseServerFrame(
            '{"type":"background_started","task_id":"T","name":"job"}')
        as AgentActivityFrame;
    expect(started.taskId, 'T');
  });

  test('specialist_* frames leave task_id null (subject-keyed)', () {
    final start = parseServerFrame(
            '{"type":"specialist_start","name":"research"}')
        as AgentActivityFrame;
    expect(start.taskId, isNull);

    final tool = parseServerFrame(
            '{"type":"specialist_tool","specialist":"research","tool":"web_search"}')
        as AgentActivityFrame;
    expect(tool.taskId, isNull);
  });

  // ── Bug 4: rows key by task_id, not the 'task' subject fallback ────────────

  test('task_started then task_completed (same task_id) settles ONE row and '
      'clears the phantom bubble', () {
    final c = ChatReducer();
    // No foreground user turn — a pure background/task lifecycle.
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'Foo', detail: 'started', taskId: 'T'));
    expect(c.messages.last.streaming, isTrue, reason: 'phantom bubble mounts');

    // task_completed carries NO name → subject falls back to 'task', but the
    // stable task_id 'T' must still land it on the started row.
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'task', detail: 'done', done: true, taskId: 'T'));

    final carriers =
        c.messages.where((m) => m.agentActivities.isNotEmpty).toList();
    expect(carriers.length, 1, reason: 'exactly one message carries the row');
    expect(carriers.single.agentActivities.length, 1,
        reason: 'same task_id → one row, no phantom "task" row');
    expect(carriers.single.agentActivities.single.done, isTrue);
    expect(carriers.single.streaming, isFalse,
        reason: 'Bug 2: host bubble spinner cleared once the row settled');
    expect(c.isStreaming, isFalse);
  });

  test('task_step frames with the same task_id UPSERT onto the started row', () {
    final c = ChatReducer();
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'Foo', detail: 'started', taskId: 'T'));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'task', detail: 'step one', taskId: 'T'));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'task', detail: 'step two', taskId: 'T'));

    final acts = c.messages.last.agentActivities;
    expect(acts.length, 1, reason: 'one row keyed by task_id, no "task" rows');
    expect(acts.single.subject, 'Foo', reason: 'started subject preserved');
    expect(acts.single.events, ['started', 'step one', 'step two']);
  });

  test('distinct task_ids get distinct rows even with the same subject', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'job', detail: 'started', taskId: 'A'));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'job', detail: 'started', taskId: 'B'));
    expect(c.messages.last.agentActivities.length, 2,
        reason: 'two different task_ids never collapse into one row');
  });

  // ── Bug 2 / Bug 5: phantom bubble settles its own spinner ──────────────────

  test('phantom specialist bubble clears streaming when the specialist finishes',
      () {
    final c = ChatReducer();
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research', detail: 'started'));
    expect(c.messages.single.streaming, isTrue);

    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research', detail: 'finished', done: true));
    expect(c.messages.single.streaming, isFalse,
        reason: 'specialist_done must clear the phantom spinner');
    expect(c.isStreaming, isFalse);
  });

  test('phantom bg bubble whose only activity settles stops spinning', () {
    final c = ChatReducer();
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'watcher', detail: 'started', taskId: 'w1'));
    expect(c.messages.single.streaming, isTrue);
    c.onFrame(const BackgroundDoneFrame('watcher', 'w1', 'ok', 100));
    final host = c.messages.firstWhere((m) => m.agentActivities.isNotEmpty);
    expect(host.streaming, isFalse,
        reason: 'background_done settles the row AND clears the spinner');
    expect(c.isStreaming, isFalse);
  });

  // ── Bug 2 invariant: never clear a live foreground turn ────────────────────

  test('a live foreground turn is NOT settled by a concurrent bg terminal', () {
    final c = ChatReducer();
    c.onUserSend('go'); // foreground turn begins
    c.onFrame(const TokenFrame('working'));

    // A background task mounts on the same streaming bubble, then completes.
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'watcher', detail: 'started', taskId: 'w1'));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'watcher', detail: 'done', done: true, taskId: 'w1'));

    expect(c.messages.last.streaming, isTrue,
        reason: 'foreground turn still live — bg terminal must not clear it');
    expect(c.isStreaming, isTrue);

    // Only the foreground terminal settles it.
    c.onFrame(const DoneFrame('final answer', null));
    expect(c.messages.last.streaming, isFalse);
    expect(c.isStreaming, isFalse);
  });

  test('a live foreground turn is NOT settled by a concurrent background_done',
      () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const TokenFrame('working'));
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'watcher', detail: 'started', taskId: 'w1'));
    c.onFrame(const BackgroundDoneFrame('watcher', 'w1', 'ok', 100));

    // background_done appends a bg_task card as the last message; the settled
    // watcher row lives on the FOREGROUND assistant bubble, which must keep
    // spinning — _addBgResult's finalizer is gated by _foregroundActive.
    final fg = c.messages.firstWhere((m) => m.role == 'assistant');
    expect(fg.streaming, isTrue,
        reason: 'foreground token turn must keep spinning through a bg_done');
    expect(fg.agentActivities.single.done, isTrue,
        reason: 'the bg row itself is settled');
    expect(c.messages.last.role, 'bg_task', reason: 'the card is still added');
    expect(c.isStreaming, isTrue);
  });

  // ── 2026-08-18: "whatsapp · delegated" chip spun forever ───────────────────
  // team_delegate mints a kind='delegate' row, but NO server frame ever
  // carries a terminal for that (kind, subject) — the specialist completes
  // under its own name. A sync delegation cannot outlive its turn, so the
  // turn-end frame must settle any still-spinning non-bg rows.

  test('delegate chip with no terminal frame settles when the turn ends', () {
    final c = ChatReducer();
    c.onUserSend('reply to the whatsapp message');
    c.onFrame(const AgentActivityFrame(
        kind: 'delegate', subject: 'whatsapp', detail: 'delegated'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'messaging_specialist', detail: 'started'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'messaging_specialist',
        detail: 'finished',
        done: true));
    c.onFrame(const DoneFrame('Sent ✓', null));

    final host = c.messages.firstWhere((m) => m.agentActivities.isNotEmpty);
    final delegate =
        host.agentActivities.firstWhere((a) => a.kind == 'delegate');
    expect(delegate.done, isTrue,
        reason: 'sync delegation cannot outlive its turn');
    expect(host.streaming, isFalse);
    expect(c.isStreaming, isFalse);
  });

  test('turn-end also settles delegate rows on error frames', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'delegate', subject: 'whatsapp', detail: 'delegated'));
    c.onFrame(const ErrorFrame('boom'));
    final host = c.messages.firstWhere((m) => m.agentActivities.isNotEmpty);
    expect(host.agentActivities.single.done, isTrue);
  });

  test('bg rows are NOT settled by the turn-end frame', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'job', detail: 'started', taskId: 'T'));
    c.onFrame(const DoneFrame('dispatched — running in background', null));
    final host = c.messages.firstWhere((m) => m.agentActivities.isNotEmpty);
    expect(host.agentActivities.single.done, isFalse,
        reason: 'background work legitimately outlives the turn');
  });

  // ── 2026-08-20: sync dispatch now emits specialist_done MID-turn ───────────
  // The backend historically never sent a terminal for sync `agent`
  // dispatches, so a completed specialist's row spun until turn end while
  // its siblings ran. This locks the wire contract the fix relies on: a
  // specialist_done settles ITS row immediately, turn still open.

  test('specialist_done settles the row mid-turn while the turn streams', () {
    final c = ChatReducer();
    c.onUserSend('check whatsapp and the blog');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'browser_specialist', detail: 'started'));
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'messaging_specialist',
        detail: 'started'));
    // First dispatch completes — NO DoneFrame yet, siblings still working.
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist',
        subject: 'browser_specialist',
        detail: 'finished',
        done: true));

    final host = c.messages.firstWhere((m) => m.agentActivities.isNotEmpty);
    final browser = host.agentActivities
        .firstWhere((a) => a.subject == 'browser_specialist');
    final messaging = host.agentActivities
        .firstWhere((a) => a.subject == 'messaging_specialist');
    expect(browser.done, isTrue,
        reason: 'a completed dispatch must stop spinning mid-turn');
    expect(messaging.done, isFalse,
        reason: 'its still-running sibling keeps spinning');
    expect(c.isStreaming, isTrue, reason: 'the turn itself is still open');
  });

  // ── 2026-08-20: settle frames lost in a WS blip leave zombie spinners ──────
  // A container restart dropped the socket right as a turn's terminals went
  // out; the answer text later arrived via history refresh but the old
  // bubble's activity rows kept spinning forever ("leftover up there").
  // Sync activities cannot outlive their turn — any bubble that is not the
  // live streaming tail must have its rows settled by the sweep, which runs
  // on reconnect and when a new user turn starts.

  test('a new user send settles zombie rows from a terminal-less turn', () {
    final c = ChatReducer();
    c.onUserSend('check seo');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'research_specialist', detail: 'started'));
    c.onFrame(const ToolCallFrame('agent', {'agent_type': 'research'}, 'tc1'));
    // No DoneFrame / specialist_done — the WS dropped. Next turn starts:
    c.onUserSend('and the weather?');

    final host = c.messages.firstWhere((m) => m.agentActivities.isNotEmpty);
    expect(host.agentActivities.single.done, isTrue,
        reason: 'sync rows cannot outlive their turn');
    expect(host.toolActivities.single.status, isNot(ToolStatus.running),
        reason: 'orphaned chips must stop spinning');
  });

  test('settleStaleSyncActivities spares the live streaming tail', () {
    final c = ChatReducer();
    c.onUserSend('first');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'old_specialist', detail: 'started'));
    c.onFrame(const DoneFrame('done text', null));
    c.onUserSend('second');
    c.onFrame(const AgentActivityFrame(
        kind: 'specialist', subject: 'live_specialist', detail: 'started'));

    c.settleStaleSyncActivities();

    final live = c.messages.last;
    expect(live.streaming, isTrue);
    expect(live.agentActivities.single.done, isFalse,
        reason: 'the in-flight turn keeps its live spinners');
  });

  test('the sweep leaves bg rows running (they outlive turns)', () {
    final c = ChatReducer();
    c.onUserSend('go');
    c.onFrame(const AgentActivityFrame(
        kind: 'bg', subject: 'job', detail: 'started', taskId: 'T'));
    c.onFrame(const DoneFrame('dispatched', null));
    c.onUserSend('next');

    final host = c.messages.firstWhere((m) => m.agentActivities.isNotEmpty);
    expect(host.agentActivities.single.done, isFalse);
  });
}
