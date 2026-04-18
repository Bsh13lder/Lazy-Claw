import { useCallback, useEffect, useRef, useState } from "react";
import { useWebSocket, type ConnectionStatus } from "./useWebSocket";

export interface ToolCallInfo {
  name: string;
  args: Record<string, unknown>;
  preview?: string;
  status: "running" | "done" | "error";
  started_at?: number;
  completed_at?: number;
  duration_ms?: number;
  error?: string;
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

export interface BrowserSession {
  url?: string;
  title?: string;
  events: BrowserEvent[];   // ring buffer, last 8
  thumbnailVersion: number; // bumps on URL change → triggers thumb refetch
  takeoverUrl?: string;     // set when remote VNC session opens
  pendingCheckpoint?: { name: string; detail?: string; ts: number };
  active: boolean;
  updatedAt: number;
}

export interface TemplateSuggest {
  suggestedName: string;
  setupUrls: string[];
  checkpoints: string[];
  actionCount: number;
  createdAt: number;
}

export interface PendingPlanInfo {
  plan: string;
  steps: string[];
  createdAt: number;
}

export interface StreamingState {
  isStreaming: boolean;
  streamContent: string;
  activeTools: ToolCallInfo[];
  currentPhase?: PhaseInfo;
  sideNotes: string[];  // side-notes the user queued for the running turn
  startedAt?: number;   // turn start timestamp for elapsed display
  browserSession?: BrowserSession;
  templateSuggest?: TemplateSuggest;
  pendingPlan?: PendingPlanInfo;
  planAutoApproveSession?: boolean;
}

interface OnCompletePayload {
  content: string;
  toolCalls: ToolCallInfo[];
  usage?: UsageInfo | null;
  latency_ms?: number;
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

interface UseChatStreamOptions {
  onComplete: (payload: OnCompletePayload) => void;
  onError: (message: string) => void;
  onBackgroundComplete?: (payload: BackgroundCompletePayload) => void;
  enabled?: boolean;
}

interface UseChatStreamReturn {
  sendMessage: (content: string, sessionId: string) => void;
  sendSideNote: (content: string) => void;
  cancelGeneration: () => void;
  dismissBrowserSession: () => void;
  dismissTemplateSuggest: () => void;
  clearPendingPlan: () => void;
  streamingState: StreamingState;
  connectionStatus: ConnectionStatus;
}

export function useChatStream({
  onComplete,
  onError,
  onBackgroundComplete,
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
  const templateSuggestRef = useRef<TemplateSuggest | undefined>(undefined);
  const pendingPlanRef = useRef<PendingPlanInfo | undefined>(undefined);
  const planAutoApproveSessionRef = useRef<boolean>(false);
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  const onBackgroundCompleteRef = useRef(onBackgroundComplete);
  useEffect(() => {
    onCompleteRef.current = onComplete;
    onErrorRef.current = onError;
    onBackgroundCompleteRef.current = onBackgroundComplete;
  }, [onComplete, onError, onBackgroundComplete]);

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
      templateSuggest: templateSuggestRef.current,
      pendingPlan: pendingPlanRef.current,
      planAutoApproveSession: planAutoApproveSessionRef.current,
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
    // NOTE: do NOT clear browserSessionRef or templateSuggestRef — their
    // lifecycles are independent of a single turn's streaming state.
    setStreamingState({
      isStreaming: false,
      streamContent: "",
      activeTools: [],
      sideNotes: [],
      browserSession: browserSessionRef.current,
      templateSuggest: templateSuggestRef.current,
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

  const dismissTemplateSuggest = useCallback(() => {
    templateSuggestRef.current = undefined;
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
            args: (msg.args as Record<string, unknown>) ?? {},
            status: "running",
            started_at: Date.now(),
          };
          toolsRef.current = [...toolsRef.current, tool];
          scheduleFlush();
          break;
        }

        case "tool_result": {
          const name = msg.name as string;
          const preview = msg.preview as string;
          const error = msg.error as string | undefined;
          const now = Date.now();
          toolsRef.current = toolsRef.current.map((t) =>
            t.name === name && t.status === "running"
              ? {
                  ...t,
                  status: error ? ("error" as const) : ("done" as const),
                  preview,
                  error,
                  completed_at: now,
                  duration_ms: t.started_at ? now - t.started_at : undefined,
                }
              : t,
          );
          scheduleFlush();
          break;
        }

        case "specialist_start": {
          const tool: ToolCallInfo = {
            name: `team:${msg.name as string}`,
            args: { task: msg.task as string },
            status: "running",
            started_at: Date.now(),
          };
          toolsRef.current = [...toolsRef.current, tool];
          scheduleFlush();
          break;
        }

        case "specialist_done": {
          const teamName = `team:${msg.name as string}`;
          const now = Date.now();
          toolsRef.current = toolsRef.current.map((t) =>
            t.name === teamName && t.status === "running"
              ? {
                  ...t,
                  status: "done" as const,
                  completed_at: now,
                  duration_ms: t.started_at ? now - t.started_at : undefined,
                }
              : t,
          );
          scheduleFlush();
          break;
        }

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

        case "plan_pending": {
          pendingPlanRef.current = {
            plan: (msg.plan as string) ?? "",
            steps: (msg.steps as string[]) ?? [],
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
          resetStream();
          onCompleteRef.current({ content, toolCalls: tools, usage, latency_ms });
          break;
        }

        case "error":
          resetStream();
          onErrorRef.current(msg.message as string);
          break;

        case "cancelled":
          resetStream();
          break;

        case "template_suggest": {
          templateSuggestRef.current = {
            suggestedName: (msg.suggested_name as string) || "Saved flow",
            setupUrls: (msg.setup_urls as string[]) ?? [],
            checkpoints: (msg.checkpoints as string[]) ?? [],
            actionCount: (msg.action_count as number) ?? 0,
            createdAt: Date.now(),
          };
          scheduleFlush();
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
      }
    },
    [scheduleFlush, resetStream],
  );

  const { send, status: connectionStatus } = useWebSocket({
    onMessage: handleMessage,
    enabled,
  });

  const sendMessage = useCallback(
    (content: string, sessionId: string) => {
      bufferRef.current = "";
      toolsRef.current = [];
      usageRef.current = null;
      firstTokenTimeRef.current = 0;
      phaseRef.current = undefined;
      sideNotesRef.current = [];
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

  return {
    sendMessage,
    sendSideNote,
    cancelGeneration,
    dismissBrowserSession,
    dismissTemplateSuggest,
    clearPendingPlan,
    streamingState,
    connectionStatus,
  };
}
