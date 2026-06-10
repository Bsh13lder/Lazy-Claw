import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
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
}
