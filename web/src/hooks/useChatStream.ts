import { useCallback, useEffect, useRef, useState } from "react";
import { useWebSocket, type ConnectionStatus } from "./useWebSocket";

export interface ToolCallInfo {
  name: string;
  // Pretty label minted server-side (frame field `display_name`).
  // Cards prefer this over the raw tool name when present.
  display?: string;
  args: Record<string, unknown>;
  preview?: string;
  status: "running" | "done" | "error";
  started_at?: number;
  completed_at?: number;
  duration_ms?: number;
  error?: string;
  // Stable id minted server-side; lets tool_result frames attach back
  // to the right call when the brain fires the same tool twice in one
  // turn. Falls back to name+running matching when missing.
  tool_call_id?: string;
}

export interface UsageInfo {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cost?: number;
  model?: string;
}

export interface PhaseInfo {
  phase: "think" | "act" | "observe" | "reflect";
  iteration?: number;
  tools?: string[];
  startedAt: number;
}

export interface BrowserEvent {
  kind: string;        // action | navigate | snapshot | checkpoint | alert | takeover | done
  ts: number;
  action?: string;     // click | type | goto | scroll | screenshot | press_key | tabs
  target?: string;
  url?: string;
  title?: string;
  detail?: string;
  extra?: Record<string, unknown>;
}

export interface BrowserFramePair {
  action: string;
  target?: string;
  preB64?: string;   // WebP before the action
  postB64?: string;  // WebP after the action
  ts: number;
}

export interface BrowserSession {
  url?: string;
  title?: string;
  events: BrowserEvent[];   // ring buffer, last 8
  thumbnailVersion: number; // bumps on URL change → triggers thumb refetch
  takeoverUrl?: string;     // set when remote VNC session opens
  pendingCheckpoint?: { name: string; detail?: string; ts: number };
  active: boolean;
  updatedAt: number;
  lastFramePair?: BrowserFramePair; // Live-mode-only pre/post flipbook
}

export interface TemplateSavedToast {
  templateId: string;
  name: string;
  icon?: string;
  host?: string;
  wasCreated: boolean;
  runCount: number;
  createdAt: number;
  // "saved" → upsert toast, "correction" → mid-flight tutoring captured.
  kind?: "saved" | "correction";
  correctionsPending?: number;
  correctionSource?: string;
}

export interface PendingPlanInfo {
  kind: "plan";
  plan: string;
  steps: string[];
  createdAt: number;
}

export interface PendingPlanQuestionInfo {
  kind: "question";
  question: string;
  createdAt: number;
}

export type PendingInteractionInfo =
  | PendingPlanInfo
  | PendingPlanQuestionInfo;

export interface StreamingState {
  isStreaming: boolean;
  streamContent: string;
  activeTools: ToolCallInfo[];
  currentPhase?: PhaseInfo;
  sideNotes: string[];  // side-notes the user queued for the running turn
  startedAt?: number;   // turn start timestamp for elapsed display
  browserSession?: BrowserSession;
  templateSaved?: TemplateSavedToast;
  pendingPlan?: PendingInteractionInfo;
  planAutoApproveSession?: boolean;
  thinkingContent?: string;    // live reasoning stream for the Thinking panel
  thinkingDone?: boolean;      // true once </think> arrived
}

interface OnCompletePayload {
  content: string;
  toolCalls: ToolCallInfo[];
  usage?: UsageInfo | null;
  latency_ms?: number;
  modelUsed?: string;
  fallbackReason?: string;
}

export interface BackgroundCompletePayload {
  kind: "background_done" | "background_failed";
  taskId: string;
  name: string;
  result?: string;
  error?: string;
  durationMs?: number;
  totalTokens?: number;
  llmCalls?: number;
  totalCost?: number;
  toolsUsed?: string[];
}

// Live tool-call/tool-result emitted by a running background task.
// Consumers render these under a per-task "Background: <name>" card
// that lives below the brain's reply, persists past the foreground
// "done" event, and finalizes when the matching background_done lands.
export interface BackgroundToolEvent {
  kind: "tool_call" | "tool_result";
  taskId: string;
  taskName: string;
  name: string;             // tool name, e.g. mcp_scraper_extract_entities
  toolCallId?: string;      // unique per-call id (matches bg_tool_result)
  args?: Record<string, unknown>;
  preview?: string;
  ts: number;
}

// Generic non-tool event from a running background task (bg agent's
// `token` / `phase` / `thinking_*` / `specialist_*` / `plan_*`
// frames). The chat UI must NOT render these as foreground bubbles —
// they belong in the per-task Background card. Server-side demux at
// `chat_ws.py` re-emits the leak set as `bg_event` instead of letting
// each frame hit a `token` / `phase` / etc. handler.
export interface BackgroundEvent {
  taskId: string;
  taskName: string;
  eventKind: string;        // original AgentEvent.kind (e.g. "token", "phase")
  detail: string;
  metadata: Record<string, unknown>;
  ts: number;
}

// Server-initiated approval prompt. The server is awaiting an
// `approval_response` frame for this `requestId` and will deny by
// default after 2 minutes if no response lands.
export interface ApprovalRequest {
  requestId: string;
  skill: string;
  args: Record<string, unknown>;
  ts: number;
}

interface UseChatStreamOptions {
  onComplete: (payload: OnCompletePayload) => void;
  onError: (message: string) => void;
  onBackgroundComplete?: (payload: BackgroundCompletePayload) => void;
  // Fired on every live tool_call / tool_result emitted by a running
  // background task. The host typically appends to a per-task list and
  // renders a "Background: <name>" card under the brain's reply.
  onBackgroundTool?: (evt: BackgroundToolEvent) => void;
  // Fired on every non-tool bg event (token, phase, thinking_*,
  // specialist_*, plan_*). Demuxed server-side as `bg_event`. Host
  // typically appends a one-line activity entry to the same per-task
  // Background card.
  onBackgroundEvent?: (evt: BackgroundEvent) => void;
  // Fired on TeamLead / TaskRunner lifecycle frames (`task_started`,
  // `task_step`, `task_phase`, `task_completed`, `background_started`,
  // `background_done`, `background_failed`). Consumers usually trigger an
  // immediate AgentStatusContext.refreshStatus() so Activity/Overview pages
  // update without waiting for the 3 s poll.
  onAgentTaskEvent?: () => void;
  // Server saw a "message" frame while a turn was active and absorbed it
  // as a side-note. The host should add a visible "queued" user bubble so
  // the message doesn't silently vanish.
  onQueuedUserMessage?: (content: string) => void;
  // Server is awaiting an approval decision. The host renders a
  // dialog and calls `sendApprovalResponse(requestId, approved)`.
  onApprovalRequest?: (req: ApprovalRequest) => void;
  enabled?: boolean;
}

interface UseChatStreamReturn {
  sendMessage: (content: string, sessionId: string) => void;
  sendSideNote: (content: string) => void;
  cancelGeneration: () => void;
  sendApprovalResponse: (requestId: string, approved: boolean) => void;
  dismissBrowserSession: () => void;
  dismissTemplateSavedToast: () => void;
  clearPendingPlan: () => void;
  streamingState: StreamingState;
  connectionStatus: ConnectionStatus;
}

export function useChatStream({
  onComplete,
  onError,
  onBackgroundComplete,
  onBackgroundTool,
  onBackgroundEvent,
  onAgentTaskEvent,
  onQueuedUserMessage,
  onApprovalRequest,
  enabled = true,
}: UseChatStreamOptions): UseChatStreamReturn {
  const [streamingState, setStreamingState] = useState<StreamingState>({
    isStreaming: false,
    streamContent: "",
    activeTools: [],
    sideNotes: [],
  });

  // Buffer tokens and flush via rAF to avoid excessive re-renders
  const bufferRef = useRef("");
  const rafRef = useRef<number>(0);
  const toolsRef = useRef<ToolCallInfo[]>([]);
  const usageRef = useRef<UsageInfo | null>(null);
  const sendTimeRef = useRef<number>(0);
  const firstTokenTimeRef = useRef<number>(0);
  const phaseRef = useRef<PhaseInfo | undefined>(undefined);
  const sideNotesRef = useRef<string[]>([]);
  const startedAtRef = useRef<number>(0);
  const browserSessionRef = useRef<BrowserSession | undefined>(undefined);
  const browserClearTimerRef = useRef<number>(0);
  const templateSavedRef = useRef<TemplateSavedToast | undefined>(undefined);
  const templateSavedClearTimerRef = useRef<number>(0);
  const pendingPlanRef = useRef<PendingInteractionInfo | undefined>(undefined);
  const planAutoApproveSessionRef = useRef<boolean>(false);
  const thinkingContentRef = useRef<string>("");
  const thinkingDoneRef = useRef<boolean>(false);
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  const onBackgroundCompleteRef = useRef(onBackgroundComplete);
  const onBackgroundToolRef = useRef(onBackgroundTool);
  const onBackgroundEventRef = useRef(onBackgroundEvent);
  const onAgentTaskEventRef = useRef(onAgentTaskEvent);
  const onQueuedUserMessageRef = useRef(onQueuedUserMessage);
  const onApprovalRequestRef = useRef(onApprovalRequest);
  useEffect(() => {
    onCompleteRef.current = onComplete;
    onErrorRef.current = onError;
    onBackgroundCompleteRef.current = onBackgroundComplete;
    onBackgroundToolRef.current = onBackgroundTool;
    onBackgroundEventRef.current = onBackgroundEvent;
    onAgentTaskEventRef.current = onAgentTaskEvent;
    onQueuedUserMessageRef.current = onQueuedUserMessage;
    onApprovalRequestRef.current = onApprovalRequest;
  }, [onComplete, onError, onBackgroundComplete, onBackgroundTool, onBackgroundEvent, onAgentTaskEvent, onQueuedUserMessage, onApprovalRequest]);

  const flushBuffer = useCallback(() => {
    const content = bufferRef.current;
    const tools = [...toolsRef.current];
    const isStreaming =
      !!startedAtRef.current ||
      !!browserSessionRef.current?.active ||
      !!pendingPlanRef.current;
    setStreamingState({
      isStreaming,
      streamContent: content,
      activeTools: tools,
      currentPhase: phaseRef.current,
      sideNotes: [...sideNotesRef.current],
      startedAt: startedAtRef.current || undefined,
      browserSession: browserSessionRef.current,
      templateSaved: templateSavedRef.current,
      pendingPlan: pendingPlanRef.current,
      planAutoApproveSession: planAutoApproveSessionRef.current,
      thinkingContent: thinkingContentRef.current || undefined,
      thinkingDone: thinkingDoneRef.current,
    });
    rafRef.current = 0;
  }, []);

  const scheduleFlush = useCallback(() => {
    if (!rafRef.current) {
      rafRef.current = requestAnimationFrame(flushBuffer);
    }
  }, [flushBuffer]);

  const resetStream = useCallback(() => {
    bufferRef.current = "";
    toolsRef.current = [];
    usageRef.current = null;
    firstTokenTimeRef.current = 0;
    phaseRef.current = undefined;
    sideNotesRef.current = [];
    startedAtRef.current = 0;
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    // NOTE: do NOT clear browserSessionRef or templateSavedRef — their
    // lifecycles are independent of a single turn's streaming state.
    setStreamingState({
      isStreaming: false,
      streamContent: "",
      activeTools: [],
      sideNotes: [],
      browserSession: browserSessionRef.current,
      templateSaved: templateSavedRef.current,
    });
  }, []);

  const dismissBrowserSession = useCallback(() => {
    if (browserClearTimerRef.current) {
      window.clearTimeout(browserClearTimerRef.current);
      browserClearTimerRef.current = 0;
    }
    browserSessionRef.current = undefined;
    scheduleFlush();
  }, [scheduleFlush]);

  const dismissTemplateSavedToast = useCallback(() => {
    if (templateSavedClearTimerRef.current) {
      window.clearTimeout(templateSavedClearTimerRef.current);
      templateSavedClearTimerRef.current = 0;
    }
    templateSavedRef.current = undefined;
    scheduleFlush();
  }, [scheduleFlush]);

  const clearPendingPlan = useCallback(() => {
    pendingPlanRef.current = undefined;
    scheduleFlush();
  }, [scheduleFlush]);

  const handleMessage = useCallback(
    (data: unknown) => {
      const msg = data as Record<string, unknown>;
      const type = msg.type as string;

      switch (type) {
        case "token":
          if (!firstTokenTimeRef.current) {
            firstTokenTimeRef.current = Date.now();
          }
          bufferRef.current += msg.content as string;
          scheduleFlush();
          break;

        case "tool_call": {
          const tool: ToolCallInfo = {
            name: msg.name as string,
            display: msg.display_name as string | undefined,
            args: (msg.args as Record<string, unknown>) ?? {},
            status: "running",
            started_at: Date.now(),
            tool_call_id: msg.tool_call_id as string | undefined,
          };
          toolsRef.current = [...toolsRef.current, tool];
          scheduleFlush();
          break;
        }

        case "tool_result": {
          const name = msg.name as string;
          const preview = msg.preview as string;
          const error = msg.error as string | undefined;
          const tool_call_id = msg.tool_call_id as string | undefined;
          const now = Date.now();
          // Prefer matching by id (stable, mints server-side). Fall
          // back to name+running for legacy / synthetic frames that
          // don't carry an id (e.g. hallucination-cap bail).
          let matchedById = false;
          let nextTools = toolsRef.current.map((t) => {
            if (
              tool_call_id &&
              t.tool_call_id === tool_call_id &&
              t.status === "running"
            ) {
              matchedById = true;
              return {
                ...t,
                status: error ? ("error" as const) : ("done" as const),
                preview,
                error,
                completed_at: now,
                duration_ms: t.started_at ? now - t.started_at : undefined,
              };
            }
            return t;
          });
          if (!matchedById) {
            // Legacy fallback — first running call with the same name
            // gets the result.
            let consumed = false;
            nextTools = nextTools.map((t) => {
              if (
                !consumed &&
                t.name === name &&
                t.status === "running"
              ) {
                consumed = true;
                return {
                  ...t,
                  status: error ? ("error" as const) : ("done" as const),
                  preview,
                  error,
                  completed_at: now,
                  duration_ms: t.started_at ? now - t.started_at : undefined,
                };
              }
              return t;
            });
          }
          toolsRef.current = nextTools;
          scheduleFlush();
          break;
        }

        // Live tool stream from a running background task. Surfaced as a
        // separate event so consumers render a per-task "Background:
        // <name>" card under the brain's reply that grows live and
        // finalizes when the matching background_done arrives. NEVER
        // mutates toolsRef — that's foreground-only.
        case "bg_tool_call":
        case "bg_tool_result": {
          const cb = onBackgroundToolRef.current;
          if (cb) {
            cb({
              kind: type === "bg_tool_call" ? "tool_call" : "tool_result",
              taskId: (msg.task_id as string) ?? "",
              taskName: (msg.task_name as string) ?? "Background task",
              name: (msg.name as string) ?? "",
              toolCallId: msg.tool_call_id as string | undefined,
              args: type === "bg_tool_call"
                ? (msg.args as Record<string, unknown>) ?? {}
                : undefined,
              preview: type === "bg_tool_result"
                ? (msg.preview as string)
                : undefined,
              ts: Date.now(),
            });
          }
          // Also nudge dashboard to refresh — the bg task's
          // current_tool / recent_tools list updates via TeamLead.
          onAgentTaskEventRef.current?.();
          break;
        }

        // Catch-all for non-tool bg events demuxed at chat_ws.py
        // (token / phase / thinking_* / specialist_* / plan_*). The
        // chat UI MUST NOT render these as foreground bubbles — the
        // server already filters them so the foreground transcript
        // stays clean. Consumers append to the same per-task
        // Background card as bg_tool_*.
        case "bg_event": {
          const cb = onBackgroundEventRef.current;
          if (cb) {
            cb({
              taskId: (msg.task_id as string) ?? "",
              taskName: (msg.task_name as string) ?? "Background task",
              eventKind: (msg.kind as string) ?? "",
              detail: (msg.detail as string) ?? "",
              metadata: (msg.metadata as Record<string, unknown>) ?? {},
              ts: Date.now(),
            });
          }
          onAgentTaskEventRef.current?.();
          break;
        }

        case "specialist_start":
        case "specialist_thinking":
        case "specialist_tool":
        case "specialist_done":
        case "team_delegate":
          // Parallel / delegated agents are NOT foreground work — they
          // belong in the Activity / Overview Background lane (dispatched
          // subagents) or Specialist lane (single delegate). We no longer
          // mount a `team:*` chip in the streaming chat tool widget; we
          // just nudge the dashboard so it repolls and shows the live
          // specialist immediately.
          onAgentTaskEventRef.current?.();
          break;

        case "thinking":
          // Agent reasoning — captured for future display
          break;

        case "phase": {
          phaseRef.current = {
            phase: msg.phase as PhaseInfo["phase"],
            iteration: msg.iteration as number | undefined,
            tools: msg.tools as string[] | undefined,
            startedAt: Date.now(),
          };
          scheduleFlush();
          break;
        }

        case "side_note_ack": {
          const note = msg.message as string;
          sideNotesRef.current = [...sideNotesRef.current, note];
          scheduleFlush();
          break;
        }

        case "queued_user_message": {
          // Server absorbed a "message" frame as a side-note while a turn
          // was active. Surface it as a visible user bubble so the message
          // doesn't silently vanish into the ThinkingCard chip.
          const content = (msg.content as string) ?? "";
          if (content && onQueuedUserMessageRef.current) {
            onQueuedUserMessageRef.current(content);
          }
          break;
        }

        case "plan_pending": {
          pendingPlanRef.current = {
            kind: "plan",
            plan: (msg.plan as string) ?? "",
            steps: (msg.steps as string[]) ?? [],
            createdAt: Date.now(),
          };
          scheduleFlush();
          break;
        }

        case "plan_question": {
          pendingPlanRef.current = {
            kind: "question",
            question: (msg.question as string) ?? "",
            createdAt: Date.now(),
          };
          scheduleFlush();
          break;
        }

        case "plan_approved": {
          pendingPlanRef.current = undefined;
          planAutoApproveSessionRef.current = !!msg.auto_approve_session;
          scheduleFlush();
          break;
        }

        case "thinking_delta": {
          thinkingContentRef.current += (msg.content as string) ?? "";
          thinkingDoneRef.current = false;
          scheduleFlush();
          break;
        }

        case "thinking_done": {
          thinkingDoneRef.current = true;
          scheduleFlush();
          break;
        }

        case "usage": {
          // Token usage event from backend
          usageRef.current = {
            input_tokens: msg.input_tokens as number | undefined,
            output_tokens: msg.output_tokens as number | undefined,
            total_tokens: msg.total_tokens as number | undefined,
            cost: msg.cost as number | undefined,
            model: msg.model as string | undefined,
          };
          break;
        }

        case "done": {
          const content = (msg.content as string) || bufferRef.current;
          const tools = [...toolsRef.current];
          // Capture usage from done event payload or from earlier usage event
          const msgUsage = msg.usage as Record<string, unknown> | undefined;
          const usage: UsageInfo | null = msgUsage
            ? {
                input_tokens: msgUsage.input_tokens as number | undefined,
                output_tokens: msgUsage.output_tokens as number | undefined,
                total_tokens: msgUsage.total_tokens as number | undefined,
                cost: msgUsage.cost as number | undefined,
                model: msgUsage.model as string | undefined,
              }
            : usageRef.current;
          const latency_ms = firstTokenTimeRef.current && sendTimeRef.current
            ? firstTokenTimeRef.current - sendTimeRef.current
            : undefined;
          const modelUsed = msg.model_used as string | undefined;
          const fallbackReason = msg.fallback_reason as string | undefined;
          resetStream();
          onCompleteRef.current({
            content,
            toolCalls: tools,
            usage,
            latency_ms,
            modelUsed,
            fallbackReason,
          });
          break;
        }

        case "error":
          resetStream();
          onErrorRef.current(msg.message as string);
          break;

        case "cancelled":
          resetStream();
          break;

        case "template_saved": {
          templateSavedRef.current = {
            templateId: (msg.template_id as string) || "",
            name: (msg.name as string) || "Saved flow",
            icon: msg.icon as string | undefined,
            host: msg.host as string | undefined,
            wasCreated: Boolean(msg.was_created),
            runCount: (msg.run_count as number) ?? 1,
            createdAt: Date.now(),
            kind: "saved",
          };
          if (templateSavedClearTimerRef.current) {
            window.clearTimeout(templateSavedClearTimerRef.current);
          }
          templateSavedClearTimerRef.current = window.setTimeout(() => {
            templateSavedRef.current = undefined;
            templateSavedClearTimerRef.current = 0;
            scheduleFlush();
          }, 4000);
          scheduleFlush();
          break;
        }

        case "template_correction_added": {
          templateSavedRef.current = {
            templateId: (msg.template_id as string) || "",
            name: (msg.name as string) || "template",
            wasCreated: false,
            runCount: 0,
            correctionsPending: (msg.corrections_pending as number) ?? 1,
            correctionSource: (msg.source as string) || undefined,
            createdAt: Date.now(),
            kind: "correction",
          };
          if (templateSavedClearTimerRef.current) {
            window.clearTimeout(templateSavedClearTimerRef.current);
          }
          templateSavedClearTimerRef.current = window.setTimeout(() => {
            templateSavedRef.current = undefined;
            templateSavedClearTimerRef.current = 0;
            scheduleFlush();
          }, 4000);
          scheduleFlush();
          break;
        }

        case "background_started":
        case "task_started":
        case "task_step":
        case "task_phase":
        case "task_completed": {
          // Live TeamLead / TaskRunner lifecycle events bridged through
          // task_event_bus → chat WS. They never enter the chat transcript;
          // they only nudge AgentStatusContext to repoll so Activity and
          // Overview light up without the 3 s lag.
          onAgentTaskEventRef.current?.();
          break;
        }

        case "background_done":
        case "background_failed": {
          const cb = onBackgroundCompleteRef.current;
          if (cb) {
            cb({
              kind: type,
              taskId: (msg.task_id as string) ?? "",
              name: (msg.name as string) ?? "Task",
              result: msg.result as string | undefined,
              error: msg.error as string | undefined,
              durationMs: msg.duration_ms as number | undefined,
              totalTokens: msg.total_tokens as number | undefined,
              llmCalls: msg.llm_calls as number | undefined,
              totalCost: msg.total_cost as number | undefined,
              toolsUsed: msg.tools_used as string[] | undefined,
            });
          }
          // Also nudge dashboard to refresh history.
          onAgentTaskEventRef.current?.();
          break;
        }

        case "browser_event": {
          const evt: BrowserEvent = {
            kind: (msg.kind as string) ?? "action",
            ts: ((msg.ts as number) ?? Date.now() / 1000),
            action: msg.action as string | undefined,
            target: msg.target as string | undefined,
            url: msg.url as string | undefined,
            title: msg.title as string | undefined,
            detail: msg.detail as string | undefined,
            extra: msg.extra as Record<string, unknown> | undefined,
          };
          // LazyBrain piggybacks on the browser event bus for zero-token chips,
          // but its events are NOT browser activity — skip creating a
          // BrowserCanvas session for note save/delete kinds.
          if (evt.kind === "note_saved" || evt.kind === "note_deleted") {
            break;
          }
          const prev = browserSessionRef.current;
          // Frame events feed the pre/post flipbook, not the action timeline.
          let nextFramePair: BrowserFramePair | undefined = prev?.lastFramePair;
          if (evt.kind === "frame") {
            const phase = evt.extra?.phase as string | undefined;
            const frameB64 = evt.extra?.frame_b64 as string | undefined;
            if (phase === "pre") {
              nextFramePair = {
                action: evt.action ?? "action",
                target: evt.target,
                preB64: frameB64,
                ts: evt.ts,
              };
            } else if (phase === "post") {
              if (
                nextFramePair &&
                nextFramePair.action === (evt.action ?? "action") &&
                nextFramePair.target === evt.target &&
                !nextFramePair.postB64
              ) {
                nextFramePair = { ...nextFramePair, postB64: frameB64, ts: evt.ts };
              } else {
                // Post without matching pre — show just the post as a single frame.
                nextFramePair = {
                  action: evt.action ?? "action",
                  target: evt.target,
                  postB64: frameB64,
                  ts: evt.ts,
                };
              }
            }
            // Skip the usual timeline/event-ring updates for frame-only events.
            const next: BrowserSession = {
              url: prev?.url,
              title: prev?.title,
              events: prev?.events ?? [],
              thumbnailVersion: prev?.thumbnailVersion ?? 0,
              takeoverUrl: prev?.takeoverUrl,
              pendingCheckpoint: prev?.pendingCheckpoint,
              active: true,
              updatedAt: Date.now(),
              lastFramePair: nextFramePair,
            };
            browserSessionRef.current = next;
            scheduleFlush();
            break;
          }
          const events = prev ? [...prev.events, evt].slice(-12) : [evt];
          const urlChanged = !!evt.url && evt.url !== prev?.url;
          // A checkpoint event with extra.resolved means it was handled —
          // drop any pending banner.
          const resolved = evt.extra?.resolved as string | undefined;
          let nextCheckpoint = prev?.pendingCheckpoint;
          if (evt.kind === "checkpoint") {
            if (resolved === "approved" || resolved === "rejected") {
              nextCheckpoint = undefined;
            } else {
              nextCheckpoint = {
                name: evt.target ?? evt.detail ?? "Checkpoint",
                detail: evt.detail,
                ts: evt.ts,
              };
            }
          }
          const next: BrowserSession = {
            url: evt.url ?? prev?.url,
            title: evt.title ?? prev?.title,
            events,
            thumbnailVersion: urlChanged
              ? (prev?.thumbnailVersion ?? 0) + 1
              : (prev?.thumbnailVersion ?? 0),
            takeoverUrl:
              evt.kind === "takeover"
                ? (evt.extra?.url as string | undefined) ?? undefined
                : prev?.takeoverUrl,
            pendingCheckpoint: nextCheckpoint,
            active: true,
            updatedAt: Date.now(),
            lastFramePair: nextFramePair,
          };
          browserSessionRef.current = next;
          // Auto-clear after 5 minutes idle so the canvas disappears.
          if (browserClearTimerRef.current) {
            window.clearTimeout(browserClearTimerRef.current);
          }
          browserClearTimerRef.current = window.setTimeout(() => {
            browserSessionRef.current = undefined;
            scheduleFlush();
          }, 5 * 60 * 1000);
          scheduleFlush();
          break;
        }

        // Server is awaiting an approval decision for a gated skill.
        // The host renders a dialog and calls sendApprovalResponse.
        // Default deny is enforced server-side after a 2-min timeout
        // — see WebSocketCallback.on_approval_request.
        case "approval_request": {
          const cb = onApprovalRequestRef.current;
          if (cb) {
            cb({
              requestId: (msg.request_id as string) ?? "",
              skill: (msg.skill as string) ?? "",
              args: (msg.args as Record<string, unknown>) ?? {},
              ts: Date.now(),
            });
          } else if (import.meta.env?.DEV) {
            // eslint-disable-next-line no-console
            console.warn(
              "[useChatStream] approval_request received but no host " +
              "handler is registered — server will time out and deny.",
              msg,
            );
          }
          break;
        }

        // ── Frames the server emits but the chat UI doesn't surface
        //    yet. Explicit no-op cases (with debug log) so they don't
        //    silently fall through `default` — easier to spot in dev
        //    tools when adding UI for one of them.
        case "llm_call":
        case "approval":  // post-decision audit frame; UI dialog is approval_request
        case "stream_done":
        case "BROWSER_PLAN":
        case "BROWSER_ACTION":
          if (import.meta.env?.DEV) {
            // eslint-disable-next-line no-console
            console.debug(
              "[useChatStream] no-op frame:",
              type,
              msg,
            );
          }
          break;

        // Tool result attachments (binary data — currently lost in the
        // chat UI but the server does emit them). When a renderer is
        // ready we'll grow this case; for now we log so it's visible.
        case "attachment":
          if (import.meta.env?.DEV) {
            // eslint-disable-next-line no-console
            console.debug(
              "[useChatStream] attachment received (no UI yet):",
              {
                media_type: msg.media_type,
                filename: msg.filename,
                bytes: typeof msg.data === "string"
                  ? (msg.data as string).length
                  : undefined,
              },
            );
          }
          break;

        default:
          if (import.meta.env?.DEV) {
            // eslint-disable-next-line no-console
            console.debug("[useChatStream] unhandled frame:", type, msg);
          }
          break;
      }
    },
    [scheduleFlush, resetStream],
  );

  const { send, status: connectionStatus } = useWebSocket({
    onMessage: handleMessage,
    enabled,
  });

  // Hard reset on WS reconnect — browser/template/plan refs are
  // CONNECTION-scoped, not turn-scoped. Without this, a stale
  // BrowserCanvas or template-saved toast from the previous WS lifetime
  // surfaces on the new connection. resetStream() (per-turn)
  // intentionally preserves these refs; only resetStreamHard clears
  // them.
  const lastStatusRef = useRef<ConnectionStatus>(connectionStatus);
  useEffect(() => {
    const prev = lastStatusRef.current;
    lastStatusRef.current = connectionStatus;
    if (connectionStatus !== "connected" || prev === "connected") return;
    // Fresh connection (initial OR reconnect) — wipe connection-scoped state.
    if (browserClearTimerRef.current) {
      window.clearTimeout(browserClearTimerRef.current);
      browserClearTimerRef.current = 0;
    }
    if (templateSavedClearTimerRef.current) {
      window.clearTimeout(templateSavedClearTimerRef.current);
      templateSavedClearTimerRef.current = 0;
    }
    browserSessionRef.current = undefined;
    templateSavedRef.current = undefined;
    pendingPlanRef.current = undefined;
    planAutoApproveSessionRef.current = false;
    scheduleFlush();
  }, [connectionStatus, scheduleFlush]);

  const sendMessage = useCallback(
    (content: string, sessionId: string) => {
      bufferRef.current = "";
      toolsRef.current = [];
      usageRef.current = null;
      firstTokenTimeRef.current = 0;
      phaseRef.current = undefined;
      sideNotesRef.current = [];
      thinkingContentRef.current = "";
      thinkingDoneRef.current = false;
      sendTimeRef.current = Date.now();
      startedAtRef.current = Date.now();
      setStreamingState({
        isStreaming: true,
        streamContent: "",
        activeTools: [],
        sideNotes: [],
        startedAt: Date.now(),
      });
      send({ type: "message", content, session_id: sessionId });
    },
    [send],
  );

  const sendSideNote = useCallback(
    (content: string) => {
      // Append to pending side-notes immediately (optimistic) — server will
      // ack with side_note_ack which we treat as confirmation.
      sideNotesRef.current = [...sideNotesRef.current, content];
      scheduleFlush();
      send({ type: "side_note", content });
    },
    [send, scheduleFlush],
  );

  const cancelGeneration = useCallback(() => {
    send({ type: "cancel" });
  }, [send]);

  const sendApprovalResponse = useCallback(
    (requestId: string, approved: boolean) => {
      send({
        type: "approval_response",
        request_id: requestId,
        approved,
      });
    },
    [send],
  );

  return {
    sendMessage,
    sendSideNote,
    cancelGeneration,
    sendApprovalResponse,
    dismissBrowserSession,
    dismissTemplateSavedToast,
    clearPendingPlan,
    streamingState,
    connectionStatus,
  };
}
