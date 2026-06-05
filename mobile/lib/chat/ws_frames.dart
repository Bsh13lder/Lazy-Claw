import 'dart:convert';

sealed class ServerFrame {
  const ServerFrame();
}

class TokenFrame extends ServerFrame {
  final String content;
  const TokenFrame(this.content);
}

class DoneFrame extends ServerFrame {
  final String content;
  final String? modelUsed;
  const DoneFrame(this.content, this.modelUsed);
}

class ErrorFrame extends ServerFrame {
  final String message;
  const ErrorFrame(this.message);
}

class CancelledFrame extends ServerFrame {
  const CancelledFrame();
}

class ApprovalRequestFrame extends ServerFrame {
  final String requestId;
  final String skill;
  final Map<String, dynamic> args;
  const ApprovalRequestFrame(this.requestId, this.skill, this.args);
}

class UnknownFrame extends ServerFrame {
  final String type;
  const UnknownFrame(this.type);
}

ServerFrame parseServerFrame(String raw) {
  try {
    final m = jsonDecode(raw);
    if (m is! Map) return const UnknownFrame('');
    final type = (m['type'] as String?) ?? '';
    switch (type) {
      case 'token':
        return TokenFrame((m['content'] as String?) ?? '');
      case 'done':
        return DoneFrame(
            (m['content'] as String?) ?? '', m['model_used'] as String?);
      case 'error':
        return ErrorFrame((m['message'] as String?) ?? 'unknown error');
      case 'cancelled':
        return const CancelledFrame();
      case 'approval_request':
        return ApprovalRequestFrame(
          (m['request_id'] as String?) ?? '',
          (m['skill'] as String?) ?? '',
          (m['args'] is Map)
              ? Map<String, dynamic>.from(m['args'] as Map)
              : const {},
        );
      default:
        return UnknownFrame(type);
    }
  } catch (_) {
    return const UnknownFrame('');
  }
}

String encodeClientMessage(String content, {String? sessionId}) =>
    jsonEncode({'type': 'message', 'content': content, 'session_id': sessionId});

String encodeApprovalResponse(String requestId, bool approved) => jsonEncode(
    {'type': 'approval_response', 'request_id': requestId, 'approved': approved});

String encodePing() => jsonEncode({'type': 'ping'});
String encodeCancel() => jsonEncode({'type': 'cancel'});
