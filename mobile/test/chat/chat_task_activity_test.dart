import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
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
}
