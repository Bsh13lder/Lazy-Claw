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
    final f = parseServerFrame('{"type":"some_future_frame","x":1}');
    expect(f, isA<UnknownFrame>());
    expect((f as UnknownFrame).type, 'some_future_frame');
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

  // ── Live-activity frames (streaming "what the agent is doing") ────────────

  test('parses thinking_delta frame', () {
    final f =
        parseServerFrame('{"type":"thinking_delta","content":"hmm"}');
    expect(f, isA<ThinkingDeltaFrame>());
    expect((f as ThinkingDeltaFrame).content, 'hmm');
  });

  test('parses thinking_done frame', () {
    expect(parseServerFrame('{"type":"thinking_done"}'),
        isA<ThinkingDoneFrame>());
  });

  test('parses team_delegate as a delegate activity', () {
    final f = parseServerFrame(
        '{"type":"team_delegate","name":"check email","specialist":"email_specialist","instruction":"read inbox"}');
    expect(f, isA<AgentActivityFrame>());
    final a = f as AgentActivityFrame;
    expect(a.kind, 'delegate');
    expect(a.subject, 'email_specialist');
    expect(a.done, isFalse);
  });

  test('parses specialist_start / specialist_tool / specialist_done', () {
    final start = parseServerFrame(
            '{"type":"specialist_start","name":"research","task":"find X"}')
        as AgentActivityFrame;
    expect(start.kind, 'specialist');
    expect(start.subject, 'research');
    expect(start.detail, 'started');

    final tool = parseServerFrame(
            '{"type":"specialist_tool","specialist":"research","tool":"web_search"}')
        as AgentActivityFrame;
    expect(tool.detail, 'using web_search');

    final done = parseServerFrame(
            '{"type":"specialist_done","name":"research","success":true,"duration_ms":1200}')
        as AgentActivityFrame;
    expect(done.done, isTrue);
    expect(done.failed, isFalse);
    expect(done.detail, 'finished');
  });

  test('parses specialist_done failure', () {
    final f = parseServerFrame(
            '{"type":"specialist_done","name":"research","success":false,"error":"boom"}')
        as AgentActivityFrame;
    expect(f.done, isTrue);
    expect(f.failed, isTrue);
    expect(f.detail, 'failed');
  });

  test('parses bg_tool_call / bg_tool_result / bg_event', () {
    final call = parseServerFrame(
            '{"type":"bg_tool_call","task_id":"t1","task_name":"check whatsapp","name":"whatsapp_read","args":{}}')
        as AgentActivityFrame;
    expect(call.kind, 'bg');
    expect(call.subject, 'check whatsapp');
    expect(call.detail, 'using whatsapp_read');

    final result = parseServerFrame(
            '{"type":"bg_tool_result","task_id":"t1","task_name":"check whatsapp","name":"whatsapp_read","preview":"3 chats"}')
        as AgentActivityFrame;
    expect(result.detail, 'whatsapp_read done');

    final evt = parseServerFrame(
            '{"type":"bg_event","task_id":"t1","task_name":"check whatsapp","kind":"phase","detail":"observing results"}')
        as AgentActivityFrame;
    expect(evt.subject, 'check whatsapp');
    expect(evt.detail, 'observing results');
  });

  test('bg_event with blank detail falls back to kind', () {
    final f = parseServerFrame(
            '{"type":"bg_event","task_id":"t1","task_name":"job","kind":"llm_call","detail":""}')
        as AgentActivityFrame;
    expect(f.detail, 'llm_call');
  });

  // ── Tool attribution on activity frames ────────────────────────────────────

  test('specialist_tool and bg_tool_call carry the tool name', () {
    final st = parseServerFrame(
            '{"type":"specialist_tool","specialist":"research","tool":"web_search"}')
        as AgentActivityFrame;
    expect(st.tool, 'web_search');

    final bg = parseServerFrame(
            '{"type":"bg_tool_call","task_id":"t1","task_name":"job","name":"browser","args":{}}')
        as AgentActivityFrame;
    expect(bg.tool, 'browser');

    // Results are completions, not new calls — no tool attribution.
    final res = parseServerFrame(
            '{"type":"bg_tool_result","task_id":"t1","task_name":"job","name":"browser","preview":"ok"}')
        as AgentActivityFrame;
    expect(res.tool, isNull);
  });

  // ── Plan question / approved ───────────────────────────────────────────────

  test('parses plan_question frame', () {
    final f = parseServerFrame(
        '{"type":"plan_question","question":"Which account?"}');
    expect(f, isA<PlanQuestionFrame>());
    expect((f as PlanQuestionFrame).question, 'Which account?');
  });

  test('plan_question with missing question falls back to empty', () {
    final f = parseServerFrame('{"type":"plan_question"}');
    expect((f as PlanQuestionFrame).question, '');
  });

  test('parses plan_approved frame', () {
    final f = parseServerFrame(
        '{"type":"plan_approved","auto_approve_session":true}');
    expect(f, isA<PlanApprovedFrame>());
    expect((f as PlanApprovedFrame).autoApproveSession, isTrue);
  });

  test('plan_approved defaults auto_approve_session to false', () {
    final f = parseServerFrame('{"type":"plan_approved"}');
    expect((f as PlanApprovedFrame).autoApproveSession, isFalse);
  });

  // ── Usage ──────────────────────────────────────────────────────────────────

  test('parses standalone usage frame', () {
    final f = parseServerFrame(
        '{"type":"usage","input_tokens":100,"output_tokens":50,"total_tokens":150,"cost":0.012,"model":"claude"}');
    expect(f, isA<UsageFrame>());
    final u = (f as UsageFrame).usage;
    expect(u.inputTokens, 100);
    expect(u.outputTokens, 50);
    expect(u.totalTokens, 150);
    expect(u.cost, 0.012);
    expect(u.model, 'claude');
  });

  test('usage frame with no metrics still parses safely', () {
    final f = parseServerFrame('{"type":"usage"}');
    expect(f, isA<UsageFrame>());
    expect((f as UsageFrame).usage.isEmpty, isTrue);
  });

  test('done frame carries usage from the WorkSummary payload shape', () {
    final f = parseServerFrame(
        '{"type":"done","content":"hi","usage":{"total_tokens":4200,"llm_calls":3,"duration_ms":5500,"total_cost":0.02}}');
    final d = f as DoneFrame;
    expect(d.usage, isNotNull);
    expect(d.usage!.totalTokens, 4200);
    expect(d.usage!.llmCalls, 3);
    expect(d.usage!.durationMs, 5500);
    expect(d.usage!.cost, 0.02);
  });

  test('done frame with malformed usage yields null usage', () {
    final f =
        parseServerFrame('{"type":"done","content":"hi","usage":"oops"}');
    expect((f as DoneFrame).usage, isNull);
  });

  // ── TeamLead / TaskRunner lifecycle frames ─────────────────────────────────

  test('parses background_started as a bg activity', () {
    final f = parseServerFrame(
            '{"type":"background_started","task_id":"t9","name":"scrape jobs"}')
        as AgentActivityFrame;
    expect(f.kind, 'bg');
    expect(f.subject, 'scrape jobs');
    expect(f.detail, 'started');
    expect(f.done, isFalse);
  });

  test('parses task_started / task_step / task_phase', () {
    final started = parseServerFrame(
            '{"type":"task_started","task_id":"t9","name":"scrape jobs","lane":"background","description":"Scrape Upwork"}')
        as AgentActivityFrame;
    expect(started.subject, 'scrape jobs');
    expect(started.detail, 'Scrape Upwork');

    final step = parseServerFrame(
            '{"type":"task_step","task_id":"t9","name":"scrape jobs","step":"reading inbox","step_count":3}')
        as AgentActivityFrame;
    expect(step.detail, 'reading inbox');

    final phase = parseServerFrame(
            '{"type":"task_phase","task_id":"t9","name":"scrape jobs","phase":"act"}')
        as AgentActivityFrame;
    expect(phase.detail, 'act');
  });

  test('task_completed maps status to terminal flags', () {
    final done = parseServerFrame(
            '{"type":"task_completed","task_id":"t9","name":"scrape jobs","status":"done"}')
        as AgentActivityFrame;
    expect(done.done, isTrue);
    expect(done.failed, isFalse);

    final failed = parseServerFrame(
            '{"type":"task_completed","task_id":"t9","name":"scrape jobs","status":"failed"}')
        as AgentActivityFrame;
    expect(failed.done, isTrue);
    expect(failed.failed, isTrue);
  });

  test('task frames tolerate missing fields', () {
    final f = parseServerFrame('{"type":"task_step"}') as AgentActivityFrame;
    expect(f.subject, 'task');
    expect(f.detail, 'working…');

    final c =
        parseServerFrame('{"type":"task_completed"}') as AgentActivityFrame;
    expect(c.done, isTrue);
    expect(c.failed, isFalse);
  });

  // ── Browser events ─────────────────────────────────────────────────────────

  test('browser_event maps to a one-line browser activity', () {
    final f = parseServerFrame(
            '{"type":"browser_event","kind":"action","action":"click","detail":"Clicked Sign in"}')
        as AgentActivityFrame;
    expect(f.kind, 'browser');
    expect(f.subject, 'browser');
    expect(f.detail, 'Clicked Sign in');
    expect(f.done, isFalse);
  });

  test('browser_event without detail falls back to action then kind', () {
    final byAction = parseServerFrame(
            '{"type":"browser_event","kind":"navigate","action":"goto"}')
        as AgentActivityFrame;
    expect(byAction.detail, 'goto');

    final byKind =
        parseServerFrame('{"type":"browser_event","kind":"snapshot"}')
            as AgentActivityFrame;
    expect(byKind.detail, 'snapshot');
  });

  test('browser_event kind=done marks the row terminal', () {
    final f = parseServerFrame(
            '{"type":"browser_event","kind":"done","detail":"flow finished"}')
        as AgentActivityFrame;
    expect(f.done, isTrue);
  });

  test('lazybrain note events on the browser bus are dropped', () {
    expect(
        parseServerFrame(
            '{"type":"browser_event","kind":"note_saved","detail":"saved"}'),
        isA<UnknownFrame>());
    expect(
        parseServerFrame('{"type":"browser_event","kind":"note_deleted"}'),
        isA<UnknownFrame>());
  });

  test('encodes a cancel frame', () {
    expect(encodeCancel(), '{"type":"cancel"}');
  });
}
