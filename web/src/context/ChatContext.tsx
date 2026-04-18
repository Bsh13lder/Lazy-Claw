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
  type BackgroundCompletePayload,
  type StreamingState,
  type ToolCallInfo,
  type UsageInfo,
} from "../hooks/useChatStream";
import type { ConnectionStatus } from "../hooks/useWebSocket";
import * as api from "../api";

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
}

export interface ChatSessionLocal {
  id: string;
  title: string;
  messages: Message[];
  loaded: boolean;
}

interface ChatContextValue {
  sessions: ChatSessionLocal[];
  activeSessionId: string;
  activeSession: ChatSessionLocal;
  streamingState: StreamingState;
  connectionStatus: ConnectionStatus;
  chatOpen: boolean;
  chatExpanded: boolean;
  sendMessage: (text: string) => void;
  cancelGeneration: () => void;
  dismissBrowserSession: () => void;
  dismissTemplateSuggest: () => void;
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
  };
}

// ── Provider ───────────────────────────────────────────────────────────────

export function ChatProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<ChatSessionLocal[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [chatOpen, setChatOpen] = useState(true);
  const [chatExpanded, setChatExpanded] = useState(false);
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

      // Case 2: no persisted id
      if (local.length === 0) {
        api.createChatSession("New Chat").then((created) => {
          if (!alive) return;
          const s = makeLocalSession(created.id, created.title);
          setSessions([s]);
          setActiveSessionId(s.id);
        });
      } else {
        setSessions(local);
        setActiveSessionId(local[0].id);
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
        toolCalls: m.tool_calls?.map((tc) => ({
          name: tc.name,
          args: tc.args,
          status: "done" as const,
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
    (payload: { content: string; toolCalls: ToolCallInfo[]; usage?: UsageInfo | null; latency_ms?: number }) => {
      const sid = activeIdRef.current;
      const msg = makeMessage("assistant", payload.content, payload.toolCalls, payload.usage, payload.latency_ms);
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
    },
    [updateSession],
  );

  const {
    sendMessage: wsSendMessage,
    sendSideNote: wsSendSideNote,
    cancelGeneration,
    dismissBrowserSession,
    dismissTemplateSuggest,
    streamingState,
    connectionStatus,
  } = useChatStream({
    onComplete: handleComplete,
    onError: handleError,
    onBackgroundComplete: handleBackgroundComplete,
  });

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
        sendMessage,
        cancelGeneration,
        dismissBrowserSession,
        dismissTemplateSuggest,
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
