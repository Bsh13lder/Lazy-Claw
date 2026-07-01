import 'dart:convert';

import 'usage_info.dart';

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

  /// Token/cost metrics carried on the terminal payload (WorkSummary).
  final UsageInfo? usage;
  const DoneFrame(this.content, this.modelUsed, {this.usage});
}

/// Standalone token-usage event — stashed by the reducer and attached to the
/// assistant message when `done` arrives (mirrors the web client).
class UsageFrame extends ServerFrame {
  final UsageInfo usage;
  const UsageFrame(this.usage);
}

class ErrorFrame extends ServerFrame {
  final String message;
  const ErrorFrame(this.message);
}

/// Client-local frame — never parsed off the wire. Injected by [ChatSocket]
/// when a queued outbound message could not be delivered (its outbox TTL
/// expired before a reconnect, or it was evicted from a full outbox). The
/// reducer marks the matching user bubble failed and surfaces [message] so
/// the user knows to resend.
class SendFailedFrame extends ServerFrame {
  final String message;
  const SendFailedFrame(this.message);
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

/// Extended-thinking token stream (Claude reasoning). The mobile UI shows a
/// "Reasoning…" indicator rather than the raw reasoning text.
class ThinkingDeltaFrame extends ServerFrame {
  final String content;
  const ThinkingDeltaFrame(this.content);
}

/// End of the extended-thinking stream.
class ThinkingDoneFrame extends ServerFrame {
  const ThinkingDoneFrame();
}

/// A live "what the agent is doing" event: delegation, specialist progress,
/// or background-task activity. Each frame carries a stable [subject] (the
/// specialist or background-task name) so the reducer can UPSERT one activity
/// row per subject instead of spamming a line per event.
class AgentActivityFrame extends ServerFrame {
  /// 'delegate' | 'specialist' | 'bg' | 'browser' — drives the row icon.
  final String kind;

  /// Stable identity for upsert (specialist name / background task name).
  final String subject;

  /// Short human line for the current state, e.g. 'using web_search'.
  final String detail;

  /// Terminal event for this subject (render check / error instead of spinner).
  final bool done;
  final bool failed;

  /// Tool name when this event is a tool CALL (specialist_tool /
  /// bg_tool_call) — feeds the per-subject tools-used counter.
  final String? tool;

  /// Stable server-minted task identity carried by every task_* / bg_* frame.
  /// task_step/task_phase/task_completed carry NO `name`, so their [subject]
  /// falls back to the literal 'task'; the reducer keys rows by [taskId] when
  /// present so a terminal always settles the row its task_started opened.
  /// Null for specialist_* / delegate / browser frames (subject-keyed).
  final String? taskId;

  const AgentActivityFrame({
    required this.kind,
    required this.subject,
    required this.detail,
    this.done = false,
    this.failed = false,
    this.tool,
    this.taskId,
  });
}

/// Agent produced a plan and is awaiting user approval.
/// Approval is sent back as a normal `message` frame (plain text reply).
class PlanPendingFrame extends ServerFrame {
  final String plan;
  final List<String> steps;
  const PlanPendingFrame(this.plan, this.steps);
}

/// Agent needs one piece of information before it can plan. The user answers
/// with a normal chat message.
class PlanQuestionFrame extends ServerFrame {
  final String question;
  const PlanQuestionFrame(this.question);
}

/// A pending plan was approved (by the user or session auto-approve).
class PlanApprovedFrame extends ServerFrame {
  final bool autoApproveSession;
  const PlanApprovedFrame(this.autoApproveSession);
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
          (m['content'] as String?) ?? '',
          m['model_used'] as String?,
          usage: UsageInfo.fromMap(m['usage']),
        );
      case 'usage':
        return UsageFrame(UsageInfo.fromMap(m) ?? const UsageInfo());
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
      case 'thinking_delta':
        return ThinkingDeltaFrame((m['content'] as String?) ?? '');
      case 'thinking_done':
        return const ThinkingDoneFrame();
      case 'team_delegate':
        final specialist = (m['specialist'] as String?) ?? '';
        final name = (m['name'] as String?) ?? '';
        return AgentActivityFrame(
          kind: 'delegate',
          subject: specialist.isNotEmpty ? specialist : name,
          detail: 'delegated',
        );
      case 'specialist_start':
        return AgentActivityFrame(
          kind: 'specialist',
          subject: (m['name'] as String?) ?? '',
          detail: 'started',
        );
      case 'specialist_thinking':
        return AgentActivityFrame(
          kind: 'specialist',
          subject: (m['specialist'] as String?) ?? '',
          detail: 'thinking…',
        );
      case 'specialist_tool':
        return AgentActivityFrame(
          kind: 'specialist',
          subject: (m['specialist'] as String?) ?? '',
          detail: 'using ${(m['tool'] as String?) ?? 'a tool'}',
          tool: m['tool'] as String?,
        );
      case 'specialist_done':
        final ok = m['success'] != false;
        return AgentActivityFrame(
          kind: 'specialist',
          subject: (m['name'] as String?) ?? '',
          detail: ok ? 'finished' : 'failed',
          done: true,
          failed: !ok,
        );
      case 'bg_tool_call':
        return AgentActivityFrame(
          kind: 'bg',
          subject: (m['task_name'] as String?) ?? 'background task',
          detail: 'using ${(m['name'] as String?) ?? 'a tool'}',
          tool: m['name'] as String?,
          taskId: m['task_id'] as String?,
        );
      case 'bg_tool_result':
        return AgentActivityFrame(
          kind: 'bg',
          subject: (m['task_name'] as String?) ?? 'background task',
          detail: '${(m['name'] as String?) ?? 'tool'} done',
          taskId: m['task_id'] as String?,
        );
      case 'bg_event':
        return AgentActivityFrame(
          kind: 'bg',
          subject: (m['task_name'] as String?) ?? 'background task',
          detail: _bgEventDetail(m),
          taskId: m['task_id'] as String?,
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
      case 'plan_question':
        return PlanQuestionFrame((m['question'] as String?) ?? '');
      case 'plan_approved':
        return PlanApprovedFrame(m['auto_approve_session'] == true);
      // ── TeamLead / TaskRunner lifecycle (task_event_bus → chat WS) ────
      // Folded into the same per-task activity row as the bg_* frames so
      // one background task = one upserting timeline subject.
      case 'background_started':
        return AgentActivityFrame(
          kind: 'bg',
          subject: _taskSubject(m),
          detail: 'started',
          taskId: m['task_id'] as String?,
        );
      case 'task_started':
        return AgentActivityFrame(
          kind: 'bg',
          subject: _taskSubject(m),
          detail: _firstNonEmpty([m['description']]) ?? 'started',
          taskId: m['task_id'] as String?,
        );
      case 'task_step':
        return AgentActivityFrame(
          kind: 'bg',
          subject: _taskSubject(m),
          detail: _firstNonEmpty([m['step']]) ?? 'working…',
          taskId: m['task_id'] as String?,
        );
      case 'task_phase':
        return AgentActivityFrame(
          kind: 'bg',
          subject: _taskSubject(m),
          detail: _firstNonEmpty([m['phase']]) ?? 'working…',
          taskId: m['task_id'] as String?,
        );
      case 'task_completed':
        final status = _firstNonEmpty([m['status']]) ?? 'done';
        return AgentActivityFrame(
          kind: 'bg',
          subject: _taskSubject(m),
          detail: status,
          done: true,
          failed: status == 'failed',
          taskId: m['task_id'] as String?,
        );
      case 'browser_event':
        return _parseBrowserEvent(m);
      default:
        return UnknownFrame(type);
    }
  } catch (_) {
    return const UnknownFrame('');
  }
}

/// Stable subject for TeamLead / TaskRunner lifecycle frames — the human task
/// name (same string the bg_* frames carry as `task_name`).
String _taskSubject(Map m) =>
    _firstNonEmpty([m['name'], m['task_name']]) ?? 'task';

/// First non-empty string from [candidates], else null.
String? _firstNonEmpty(List<dynamic> candidates) {
  for (final c in candidates) {
    if (c is String && c.trim().isNotEmpty) return c.trim();
  }
  return null;
}

/// Minimal one-line activity entry for a `browser_event` frame.
///
/// LazyBrain piggybacks note_saved / note_deleted on the browser bus — those
/// are not browser activity, so they fall through as unknown (dropped), same
/// as the web client.
ServerFrame _parseBrowserEvent(Map m) {
  final kind = (m['kind'] as String?) ?? 'action';
  if (kind == 'note_saved' || kind == 'note_deleted') {
    return const UnknownFrame('browser_event');
  }
  final detail = _firstNonEmpty([m['detail']]) ??
      _firstNonEmpty([m['action'], kind]) ??
      kind;
  return AgentActivityFrame(
    kind: 'browser',
    subject: 'browser',
    detail: detail,
    done: kind == 'done',
  );
}

/// A short display line for a generic `bg_event` frame: prefer the human
/// `detail` text, fall back to the event kind (e.g. 'phase'), else 'working…'.
String _bgEventDetail(Map m) {
  final detail = (m['detail'] as String?)?.trim() ?? '';
  if (detail.isNotEmpty) {
    return detail.length <= 80 ? detail : '${detail.substring(0, 79)}…';
  }
  final kind = (m['kind'] as String?)?.trim() ?? '';
  return kind.isNotEmpty ? kind : 'working…';
}

String encodeClientMessage(String content, {String? sessionId}) =>
    jsonEncode({'type': 'message', 'content': content, 'session_id': sessionId});

String encodeApprovalResponse(String requestId, bool approved) => jsonEncode(
    {'type': 'approval_response', 'request_id': requestId, 'approved': approved});

String encodePing() => jsonEncode({'type': 'ping'});
String encodeCancel() => jsonEncode({'type': 'cancel'});
