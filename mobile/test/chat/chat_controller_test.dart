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
}
