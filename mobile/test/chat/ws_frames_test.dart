import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  test('parses token frame', () {
    final f = parseServerFrame('{"type":"token","content":"hel"}');
    expect(f, isA<TokenFrame>());
    expect((f as TokenFrame).content, 'hel');
  });

  test('parses done frame with content', () {
    final f = parseServerFrame(
        '{"type":"done","content":"final reply","model_used":"claude"}');
    expect(f, isA<DoneFrame>());
    expect((f as DoneFrame).content, 'final reply');
  });

  test('parses error frame', () {
    final f = parseServerFrame('{"type":"error","message":"boom"}');
    expect((f as ErrorFrame).message, 'boom');
  });

  test('parses approval_request frame', () {
    final f = parseServerFrame(
        '{"type":"approval_request","request_id":"abc123","skill":"send_email","args":{"to":"x"}}');
    expect(f, isA<ApprovalRequestFrame>());
    expect((f as ApprovalRequestFrame).requestId, 'abc123');
    expect(f.skill, 'send_email');
  });

  test('unknown type -> UnknownFrame (never throws)', () {
    final f = parseServerFrame('{"type":"specialist_thinking","x":1}');
    expect(f, isA<UnknownFrame>());
    expect((f as UnknownFrame).type, 'specialist_thinking');
  });

  test('malformed json -> UnknownFrame', () {
    final f = parseServerFrame('not json');
    expect(f, isA<UnknownFrame>());
  });

  test('encodes a client message frame', () {
    expect(encodeClientMessage('hello'),
        '{"type":"message","content":"hello","session_id":null}');
  });

  test('encodes approval response', () {
    expect(encodeApprovalResponse('abc123', true),
        '{"type":"approval_response","request_id":"abc123","approved":true}');
  });

  // ── New frame types ────────────────────────────────────────────────────────

  test('parses tool_call frame with all fields', () {
    final f = parseServerFrame(
        '{"type":"tool_call","name":"browser","args":{"url":"https://example.com"},"tool_call_id":"tc1"}');
    expect(f, isA<ToolCallFrame>());
    final tc = f as ToolCallFrame;
    expect(tc.name, 'browser');
    expect(tc.args['url'], 'https://example.com');
    expect(tc.toolCallId, 'tc1');
  });

  test('parses tool_call frame with missing optional fields', () {
    final f = parseServerFrame('{"type":"tool_call","name":"search_tools"}');
    expect(f, isA<ToolCallFrame>());
    final tc = f as ToolCallFrame;
    expect(tc.name, 'search_tools');
    expect(tc.args, isEmpty);
    expect(tc.toolCallId, isNull);
  });

  test('parses tool_result frame', () {
    final f = parseServerFrame(
        '{"type":"tool_result","name":"browser","preview":"Page loaded OK","tool_call_id":"tc1"}');
    expect(f, isA<ToolResultFrame>());
    final tr = f as ToolResultFrame;
    expect(tr.name, 'browser');
    expect(tr.preview, 'Page loaded OK');
    expect(tr.toolCallId, 'tc1');
  });

  test('parses tool_result frame with null preview', () {
    final f = parseServerFrame('{"type":"tool_result","name":"recall_memories"}');
    expect(f, isA<ToolResultFrame>());
    final tr = f as ToolResultFrame;
    expect(tr.name, 'recall_memories');
    expect(tr.preview, '');
    expect(tr.toolCallId, isNull);
  });

  test('parses background_done frame', () {
    final f = parseServerFrame(
        '{"type":"background_done","name":"Send email","task_id":"t42","result":"Sent!","duration_ms":3200}');
    expect(f, isA<BackgroundDoneFrame>());
    final bd = f as BackgroundDoneFrame;
    expect(bd.name, 'Send email');
    expect(bd.taskId, 't42');
    expect(bd.result, 'Sent!');
    expect(bd.durationMs, 3200);
  });

  test('parses background_done frame with null fields', () {
    final f = parseServerFrame('{"type":"background_done","name":"task"}');
    expect(f, isA<BackgroundDoneFrame>());
    final bd = f as BackgroundDoneFrame;
    expect(bd.taskId, isNull);
    expect(bd.result, isNull);
    expect(bd.durationMs, isNull);
  });

  test('parses background_failed frame', () {
    final f = parseServerFrame(
        '{"type":"background_failed","name":"Send email","task_id":"t43","error":"Connection refused","duration_ms":500}');
    expect(f, isA<BackgroundFailedFrame>());
    final bf = f as BackgroundFailedFrame;
    expect(bf.name, 'Send email');
    expect(bf.taskId, 't43');
    expect(bf.error, 'Connection refused');
    expect(bf.durationMs, 500);
  });

  test('parses phase frame', () {
    final f = parseServerFrame(
        '{"type":"phase","phase":"act","iteration":2}');
    expect(f, isA<PhaseFrame>());
    final pf = f as PhaseFrame;
    expect(pf.phase, 'act');
    expect(pf.iteration, 2);
  });

  test('parses phase frame with null iteration', () {
    final f = parseServerFrame('{"type":"phase","phase":"think"}');
    expect(f, isA<PhaseFrame>());
    expect((f as PhaseFrame).iteration, isNull);
  });

  test('parses plan_pending frame', () {
    final f = parseServerFrame(
        '{"type":"plan_pending","plan":"Step A then B","steps":["Step A","Step B"]}');
    expect(f, isA<PlanPendingFrame>());
    final pp = f as PlanPendingFrame;
    expect(pp.plan, 'Step A then B');
    expect(pp.steps, ['Step A', 'Step B']);
  });

  test('parses plan_pending frame with empty steps', () {
    final f = parseServerFrame('{"type":"plan_pending","plan":"Do stuff"}');
    expect(f, isA<PlanPendingFrame>());
    expect((f as PlanPendingFrame).steps, isEmpty);
  });
}
