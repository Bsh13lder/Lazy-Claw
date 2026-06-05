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
}
