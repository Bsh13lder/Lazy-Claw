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

/// Agent is calling a tool (foreground).
class ToolCallFrame extends ServerFrame {
  final String name;
  final Map<String, dynamic> args;
  final String? toolCallId;
  const ToolCallFrame(this.name, this.args, this.toolCallId);
}

/// Tool returned a result (foreground).
class ToolResultFrame extends ServerFrame {
  final String name;
  final String preview;
  final String? toolCallId;
  const ToolResultFrame(this.name, this.preview, this.toolCallId);
}

/// A background task completed successfully.
class BackgroundDoneFrame extends ServerFrame {
  final String name;
  final String? taskId;
  final String? result;
  final int? durationMs;
  const BackgroundDoneFrame(this.name, this.taskId, this.result, this.durationMs);
}

/// A background task failed.
class BackgroundFailedFrame extends ServerFrame {
  final String name;
  final String? taskId;
  final String? error;
  final int? durationMs;
  const BackgroundFailedFrame(this.name, this.taskId, this.error, this.durationMs);
}

/// TAOR phase transition (think / act / observe / reflect).
class PhaseFrame extends ServerFrame {
  final String phase;
  final int? iteration;
  const PhaseFrame(this.phase, this.iteration);
}

/// Agent produced a plan and is awaiting user approval.
/// Approval is sent back as a normal `message` frame (plain text reply).
class PlanPendingFrame extends ServerFrame {
  final String plan;
  final List<String> steps;
  const PlanPendingFrame(this.plan, this.steps);
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
      case 'tool_call':
        return ToolCallFrame(
          (m['name'] as String?) ?? '',
          (m['args'] is Map)
              ? Map<String, dynamic>.from(m['args'] as Map)
              : const {},
          m['tool_call_id'] as String?,
        );
      case 'tool_result':
        return ToolResultFrame(
          (m['name'] as String?) ?? '',
          (m['preview'] as String?) ?? '',
          m['tool_call_id'] as String?,
        );
      case 'background_done':
        return BackgroundDoneFrame(
          (m['name'] as String?) ?? '',
          m['task_id'] as String?,
          m['result'] as String?,
          m['duration_ms'] is int ? m['duration_ms'] as int : null,
        );
      case 'background_failed':
        return BackgroundFailedFrame(
          (m['name'] as String?) ?? '',
          m['task_id'] as String?,
          m['error'] as String?,
          m['duration_ms'] is int ? m['duration_ms'] as int : null,
        );
      case 'phase':
        return PhaseFrame(
          (m['phase'] as String?) ?? '',
          m['iteration'] is int ? m['iteration'] as int : null,
        );
      case 'plan_pending':
        final rawSteps = m['steps'];
        final steps = (rawSteps is List)
            ? rawSteps.map((s) => s.toString()).toList()
            : <String>[];
        return PlanPendingFrame(
          (m['plan'] as String?) ?? '',
          steps,
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
