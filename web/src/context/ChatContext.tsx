import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  useChatStream,
  type ApprovalRequest,
  type BackgroundCompletePayload,
  type BackgroundEvent,
  type BackgroundToolEvent,
  type StreamingState,
  type ToolCallInfo,
  type UsageInfo,
} from "../hooks/useChatStream";
import type { ConnectionStatus } from "../hooks/useWebSocket";
import * as api from "../api";
import { useAgentStatus } from "./AgentStatusContext";

// ── Types ──────────────────────────────────────────────────────────────────

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  toolCalls?: ToolCallInfo[];
  tokens?: number;
  cost?: number;
  model?: string;
  latency_ms?: number;
  // Present when the ECO router silently swapped away from the configured
  // brain (e.g. Sonnet → Haiku via Claude CLI on 529 overload).
  fallbackReason?: string;
  modelUsed?: string;
  // "cron" on user rows injected by scheduled jobs ("[JOB:name] ..." content).
  kind?: string;
}

export interface ChatSessionLocal {
  id: string;
  title: string;
  messages: Message[];
  loaded: boolean;
}

// Live progress for a single running background task. Populated by
// `bg_tool_call` / `bg_tool_result` / `bg_event` frames demuxed at
// chat_ws.py. Consumers (e.g. a `BackgroundTasksPanel` component, the
// AgentConsole BG sub-row) read this state to render per-task feeds.
// **Critical**: bg events MUST land here, NOT in `Message.toolCalls`,
// because the foreground transcript is for the brain's reply only.
export interface BackgroundTaskProgress {
  taskId: string;
  taskName: string;
  status: "running" | "done" | "failed";
  startedAt: number;
  // Tool calls in flight or completed for this bg task.
  tools: ToolCallInfo[];
  // Compact activity log: phase chips, thinking notes, specialist
  // dispatch, plan questions. Capped at 30 entries to keep the UI
  // responsive even on long-running bg tasks.
  events: { kind: string; detail: string; ts: number }[];
}

interface ChatContextValue {
  sessions: ChatSessionLocal[];
  activeSessionId: string;
  activeSession: ChatSessionLocal;
  streamingState: StreamingState;
  connectionStatus: ConnectionStatus;
  chatOpen: boolean;
  chatExpanded: boolean;
  // Live per-task progress map for currently running bg tasks. Cleared
  // entries roll off when `background_done` / `background_failed` lands.
  backgroundTasks: Record<string, BackgroundTaskProgress>;
  // Current pending approval prompt (server is awaiting our decision).
  // Null when no skill is gated.
  pendingApproval: ApprovalRequest | null;
  decideApproval: (requestId: string, approved: boolean) => void;
  sendMessage: (text: string) => void;
  cancelGeneration: () => void;
  dismissBrowserSession: () => void;
  dismissTemplateSavedToast: () => void;
  clearPendingPlan: () => void;
  createSession: () => void;
  selectSession: (id: string) => void;
  deleteSession: (id: string) => void;
  setChatOpen: (open: boolean) => void;
  toggleChat: () => void;
  toggleExpanded: () => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

// ── localStorage persistence ──────────────────────────────────────────────

const SESSION_KEY = "lazyclaw_active_session_id";

function persistActiveSession(id: string): void {
  try { localStorage.setItem(SESSION_KEY, id); } catch { /* private browsing */ }
}

function loadPersistedSession(): string | null {
  try { return localStorage.getItem(SESSION_KEY); } catch { return null; }
}

// ── Helpers ────────────────────────────────────────────────────────────────

function makeLocalSession(id?: string, title?: string): ChatSessionLocal {
  return {
    id: id ?? crypto.randomUUID(),
    title: title ?? "New Chat",
    messages: [],
    loaded: true,
  };
}

function makeMessage(
  role: "user" | "assistant",
  content: string,
  toolCalls?: ToolCallInfo[],
  usage?: UsageInfo | null,
  latency_ms?: number,
  extra?: { modelUsed?: string; fallbackReason?: string },
): Message {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    timestamp: Date.now(),
    toolCalls,
    tokens: usage?.total_tokens,
    cost: usage?.cost,
    model: usage?.model,
    latency_ms,
    modelUsed: extra?.modelUsed,
    fallbackReason: extra?.fallbackReason,
  };
}

// ── Provider ───────────────────────────────────────────────────────────────

export function ChatProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<ChatSessionLocal[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [chatOpen, setChatOpen] = useState(true);
  const [chatExpanded, setChatExpanded] = useState(false);
  // ChatProvider is mounted INSIDE AgentStatusProvider in App.tsx, so we
  // can hand the WS task-event hook a refresh trigger that flips
  // Activity/Overview snapshots immediately on lifecycle frames.
  const { refreshStatus } = useAgentStatus();
  const activeIdRef = useRef(activeSessionId);
  useEffect(() => {
    activeIdRef.current = activeSessionId;
    if (activeSessionId) persistActiveSession(activeSessionId);
  }, [activeSessionId]);

  // Load sessions from backend on mount.
  //
  // Invariants we keep to survive refresh:
  //   1. If localStorage has a session id, that session MUST end up active —
  //      even if the backend's list didn't return it (race, repair pending,
  //      archived-then-unarchived, etc.). We reconstruct a local shell and
  //      let the messages-loader fetch its history.
  //   2. Never silently fall back to "most recent" and drop the user's chat.
  useEffect(() => {
    let alive = true;
    const persisted = loadPersistedSession();

    api.listChatSessions().then((remote) => {
      if (!alive) return;

      const local = remote.map((r) => ({
        id: r.id,
        title: r.title || "New Chat",
        messages: [] as Message[],
        loaded: false,
      }));

      // Case 1: we have a persisted id
      if (persisted) {
        const match = local.find((s) => s.id === persisted);
        if (match) {
          setSessions(local);
          setActiveSessionId(match.id);
          return;
        }
        // Persisted id not in remote list — reconstruct a shell so we can
        // still fetch its messages. The session row may exist server-side
        // (orphan repair runs on next list_sessions call) but wasn't in
        // the first response. We add it to the front of the list.
        const shell: ChatSessionLocal = {
          id: persisted,
          title: "Restored chat",
          messages: [],
          loaded: false,
        };
        setSessions([shell, ...local]);
        setActiveSessionId(persisted);
        return;
      }

      // Case 2: no persisted id — prefer the primary session (shared with
      // Telegram / CLI / TUI / REPL) over "most recent" so the first thing
      // the user sees is the unified conversation.
      if (local.length === 0) {
        api.createChatSession("New Chat").then((created) => {
          if (!alive) return;
          const s = makeLocalSession(created.id, created.title);
          setSessions([s]);
          setActiveSessionId(s.id);
        });
      } else {
        const primary = remote.find((r) => r.is_primary);
        const chosenId = primary?.id ?? local[0].id;
        setSessions(local);
        setActiveSessionId(chosenId);
      }
    }).catch(() => {
      if (!alive) return;
      // Backend unavailable — reuse persisted id if any, else new local shell
      const fallbackId = persisted ?? undefined;
      const s = makeLocalSession(fallbackId);
      setSessions([s]);
      setActiveSessionId(s.id);
    });
    return () => { alive = false; };
  }, []);

  // Load messages when switching to an unloaded session
  // Note: only depends on activeSessionId — sessions is checked via ref-like
  // updater pattern to avoid re-trigger loops from setSessions.
  useEffect(() => {
    if (!activeSessionId) return;

    // Check if session needs loading via updater to avoid sessions dep
    let needsLoad = false;
    setSessions((prev) => {
      const session = prev.find((s) => s.id === activeSessionId);
      needsLoad = !!session && !session.loaded;
      return prev; // no mutation — just reading
    });
    if (!needsLoad) return;

    let alive = true;
    api.getSessionMessages(activeSessionId, { limit: 100 }).then((msgs) => {
      if (!alive) return;
      const converted: Message[] = msgs.map((m) => ({
        id: m.id,
        role: m.role === "tool" ? "assistant" as const : m.role,
        content: m.content,
        timestamp: new Date(m.created_at).getTime(),
        kind: m.kind,
        toolCalls: m.tool_calls?.map((tc) => ({
          name: tc.name,
          display: tc.display,
          args: tc.arguments ?? {},
          // Web has no "unknown" visual — map it to done so a historic
          // call with no persisted result never renders a spinner.
          status: tc.status === "unknown"
            ? ("done" as const)
            : (tc.status ?? ("done" as const)),
          preview: tc.result,
        })),
      }));
      setSessions((prev) =>
        prev.map((s) =>
          s.id === activeSessionId ? { ...s, messages: converted, loaded: true } : s,
        ),
      );
    }).catch(() => {
      // Mark as loaded even on error to avoid retry loop
      setSessions((prev) =>
        prev.map((s) =>
          s.id === activeSessionId ? { ...s, loaded: true } : s,
        ),
      );
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId]);

  const updateSession = useCallback(
    (id: string, updater: (s: ChatSessionLocal) => ChatSessionLocal) => {
      setSessions((prev) => prev.map((s) => (s.id === id ? updater(s) : s)));
    },
    [],
  );

  // ── Streaming ──────────────────────────────────────────────────────────

  const handleComplete = useCallback(
    (payload: {
      content: string;
      toolCalls: ToolCallInfo[];
      usage?: UsageInfo | null;
      latency_ms?: number;
      modelUsed?: string;
      fallbackReason?: string;
    }) => {
      const sid = activeIdRef.current;
      const msg = makeMessage(
        "assistant",
        payload.content,
        payload.toolCalls,
        payload.usage,
        payload.latency_ms,
        { modelUsed: payload.modelUsed, fallbackReason: payload.fallbackReason },
      );
      updateSession(sid, (s) => ({ ...s, messages: [...s.messages, msg] }));
    },
    [updateSession],
  );

  const handleError = useCallback(
    (message: string) => {
      const sid = activeIdRef.current;
      const msg = makeMessage("assistant", `**Error:** ${message}`);
      updateSession(sid, (s) => ({ ...s, messages: [...s.messages, msg] }));
    },
    [updateSession],
  );

  // Server saw a plain "message" frame while a turn was active and absorbed
  // it as a side-note (defensive path for clients that don't check
  // isStreaming themselves). Surface as a visible queued user bubble so the
  // message doesn't silently vanish.
  const handleQueuedUserMessage = useCallback(
    (content: string) => {
      const sid = activeIdRef.current;
      const sideMsg: Message = {
        ...makeMessage("user", content),
        content: `↳ ${content}`,
      };
      updateSession(sid, (s) => ({
        ...s,
        messages: [...s.messages, sideMsg],
      }));
    },
    [updateSession],
  );

  // ── Background task live progress ─────────────────────────────────
  // Keyed by task_id. Updated from `bg_tool_call` / `bg_tool_result` /
  // `bg_event` frames demuxed server-side. NEVER mutates `messages` —
  // the chat transcript stays clean (the foreground turn's transcript
  // is for the brain's reply only). Final completion lands here AND in
  // the chat as a single summary message via handleBackgroundComplete.
  const [backgroundTasks, setBackgroundTasks] = useState<
    Record<string, BackgroundTaskProgress>
  >({});

  const handleBackgroundTool = useCallback((evt: BackgroundToolEvent) => {
    setBackgroundTasks((prev) => {
      const cur = prev[evt.taskId] ?? {
        taskId: evt.taskId,
        taskName: evt.taskName,
        status: "running" as const,
        startedAt: evt.ts,
        tools: [],
        events: [],
      };
      let tools: ToolCallInfo[];
      if (evt.kind === "tool_call") {
        tools = [
          ...cur.tools,
          {
            name: evt.name,
            args: evt.args ?? {},
            status: "running" as const,
            started_at: evt.ts,
          },
        ];
      } else {
        // Match by tool_call_id when present (handles same-name
        // back-to-back calls), else fall back to name+running.
        const matchById = !!evt.toolCallId;
        let matched = false;
        tools = cur.tools.map((t) => {
          const isMatch = matchById
            ? !!evt.toolCallId &&
              (t as ToolCallInfo & { tool_call_id?: string }).tool_call_id ===
                evt.toolCallId
            : t.name === evt.name && t.status === "running" && !matched;
          if (!isMatch) return t;
          matched = true;
          return {
            ...t,
            status: "done" as const,
            preview: evt.preview,
            completed_at: evt.ts,
            duration_ms: t.started_at ? evt.ts - t.started_at : undefined,
          };
        });
      }
      return { ...prev, [evt.taskId]: { ...cur, tools } };
    });
  }, []);

  // ── Approval round-trip ───────────────────────────────────────────
  // Server pings us when a gated skill needs the user's go-ahead. We
  // surface a single dialog (most-recent wins; the old request times
  // out server-side at 2 minutes if shadowed).
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequest | null>(null);

  const handleApprovalRequest = useCallback((req: ApprovalRequest) => {
    setPendingApproval(req);
  }, []);

  const handleBackgroundEvent = useCallback((evt: BackgroundEvent) => {
    setBackgroundTasks((prev) => {
      const cur = prev[evt.taskId] ?? {
        taskId: evt.taskId,
        taskName: evt.taskName,
        status: "running" as const,
        startedAt: evt.ts,
        tools: [],
        events: [],
      };
      const events = [
        ...cur.events,
        { kind: evt.eventKind, detail: evt.detail, ts: evt.ts },
      ].slice(-30); // cap to last 30 entries
      return { ...prev, [evt.taskId]: { ...cur, events } };
    });
  }, []);

  // Background task finished AFTER its originating turn — surface result
  // inline so the user (and the agent on the next turn, via server-side
  // chat history) can see what happened.
  const handleBackgroundComplete = useCallback(
    (payload: BackgroundCompletePayload) => {
      const sid = activeIdRef.current;
      const header =
        payload.kind === "background_done"
          ? `✅ Background task completed — **${payload.name}**`
          : `❌ Background task failed — **${payload.name}**`;
      const body =
        payload.kind === "background_done"
          ? (payload.result || "(no output)")
          : (payload.error || "(unknown error)");
      const content = `${header}\n\n${body}`;
      const usage: UsageInfo | null =
        payload.totalTokens != null || payload.totalCost != null
          ? { total_tokens: payload.totalTokens, cost: payload.totalCost }
          : null;
      const msg = makeMessage("assistant", content, undefined, usage);
      updateSession(sid, (s) => ({ ...s, messages: [...s.messages, msg] }));
      // Roll the task off the live-progress map after a short delay so
      // any final tool_result/event frames in flight still land before
      // the panel removes the row.
      setBackgroundTasks((prev) => {
        if (!prev[payload.taskId]) return prev;
        const next = { ...prev };
        next[payload.taskId] = {
          ...next[payload.taskId],
          status: payload.kind === "background_done" ? "done" : "failed",
        };
        return next;
      });
      window.setTimeout(() => {
        setBackgroundTasks((prev) => {
          if (!prev[payload.taskId]) return prev;
          const { [payload.taskId]: _drop, ...rest } = prev;
          return rest;
        });
      }, 4000);
    },
    [updateSession],
  );

  const {
    sendMessage: wsSendMessage,
    sendSideNote: wsSendSideNote,
    cancelGeneration,
    sendApprovalResponse,
    dismissBrowserSession,
    dismissTemplateSavedToast,
    clearPendingPlan,
    streamingState,
    connectionStatus,
  } = useChatStream({
    onComplete: handleComplete,
    onError: handleError,
    onBackgroundComplete: handleBackgroundComplete,
    onBackgroundTool: handleBackgroundTool,
    onBackgroundEvent: handleBackgroundEvent,
    onAgentTaskEvent: refreshStatus,
    onQueuedUserMessage: handleQueuedUserMessage,
    onApprovalRequest: handleApprovalRequest,
  });

  const decideApproval = useCallback(
    (requestId: string, approved: boolean) => {
      sendApprovalResponse(requestId, approved);
      setPendingApproval((prev) =>
        prev && prev.requestId === requestId ? null : prev,
      );
    },
    [sendApprovalResponse],
  );

  // Keep a live ref to streamingState so sendMessage can decide side-note vs
  // new-turn without re-memoizing on every state change.
  const streamingRef = useRef(streamingState);
  useEffect(() => { streamingRef.current = streamingState; }, [streamingState]);

  // ── Actions ────────────────────────────────────────────────────────────

  const sendMessage = useCallback(
    (text: string) => {
      const sid = activeIdRef.current;
      // If an agent turn is already in-flight, route to the side-channel
      // instead of starting a new turn. Shows up as a dim user note inline.
      if (streamingRef.current.isStreaming) {
        const sideMsg: Message = {
          ...makeMessage("user", text),
          content: `↳ ${text}`,  // marker so we can render differently
        };
        updateSession(sid, (s) => ({
          ...s,
          messages: [...s.messages, sideMsg],
        }));
        wsSendSideNote(text);
        return;
      }
      const userMsg = makeMessage("user", text);
      updateSession(sid, (s) => ({
        ...s,
        messages: [...s.messages, userMsg],
        title: s.messages.length === 0 ? text.slice(0, 50) : s.title,
      }));
      wsSendMessage(text, sid);
    },
    [updateSession, wsSendMessage, wsSendSideNote],
  );

  const createSession = useCallback(() => {
    api.createChatSession("New Chat").then((created) => {
      const s = makeLocalSession(created.id, created.title);
      setSessions((prev) => [s, ...prev]);
      setActiveSessionId(s.id);
      setChatOpen(true);
    }).catch(() => {
      // Fallback: local-only session
      const s = makeLocalSession();
      setSessions((prev) => [s, ...prev]);
      setActiveSessionId(s.id);
      setChatOpen(true);
    });
  }, []);

  const selectSession = useCallback((id: string) => {
    setActiveSessionId(id);
    setChatOpen(true);
  }, []);

  const deleteSession = useCallback(
    (id: string) => {
      api.deleteChatSession(id).catch(() => {});
      const wasActive = activeIdRef.current === id;

      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== id);
        if (next.length === 0) {
          return [makeLocalSession()];
        }
        return next;
      });

      // Handle side effects outside state updater
      setSessions((current) => {
        if (current.length === 1 && current[0].loaded && current[0].messages.length === 0) {
          // Fresh session just created above — sync with backend
          api.createChatSession("New Chat").catch(() => {});
        }
        if (wasActive) {
          setActiveSessionId(current[0].id);
        }
        return current; // no mutation
      });
    },
    [],
  );

  const toggleChat = useCallback(() => setChatOpen((o) => !o), []);
  const toggleExpanded = useCallback(() => setChatExpanded((e) => !e), []);

  const activeSession =
    sessions.find((s) => s.id === activeSessionId) ??
    sessions[0] ??
    makeLocalSession();

  return (
    <ChatContext.Provider
      value={{
        sessions,
        activeSessionId,
        activeSession,
        streamingState,
        connectionStatus,
        chatOpen,
        chatExpanded,
        backgroundTasks,
        pendingApproval,
        decideApproval,
        sendMessage,
        cancelGeneration,
        dismissBrowserSession,
        dismissTemplateSavedToast,
        clearPendingPlan,
        createSession,
        selectSession,
        deleteSession,
        setChatOpen,
        toggleChat,
        toggleExpanded,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useChat(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
