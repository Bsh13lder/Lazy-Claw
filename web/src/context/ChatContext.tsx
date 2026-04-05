import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useChatStream, type StreamingState, type ToolCallInfo } from "../hooks/useChatStream";
import type { ConnectionStatus } from "../hooks/useWebSocket";
import * as api from "../api";

// ── Types ──────────────────────────────────────────────────────────────────

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  toolCalls?: ToolCallInfo[];
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
  createSession: () => void;
  selectSession: (id: string) => void;
  deleteSession: (id: string) => void;
  setChatOpen: (open: boolean) => void;
  toggleChat: () => void;
  toggleExpanded: () => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

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
): Message {
  return { id: crypto.randomUUID(), role, content, timestamp: Date.now(), toolCalls };
}

// ── Provider ───────────────────────────────────────────────────────────────

export function ChatProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<ChatSessionLocal[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [chatOpen, setChatOpen] = useState(true);
  const [chatExpanded, setChatExpanded] = useState(false);
  const activeIdRef = useRef(activeSessionId);
  activeIdRef.current = activeSessionId;

  // Load sessions from backend on mount
  useEffect(() => {
    let alive = true;
    api.listChatSessions().then((remote) => {
      if (!alive) return;
      if (remote.length === 0) {
        // No sessions yet — create one
        api.createChatSession("New Chat").then((created) => {
          if (!alive) return;
          const s = makeLocalSession(created.id, created.title);
          setSessions([s]);
          setActiveSessionId(s.id);
        });
      } else {
        const local = remote.map((r) => ({
          id: r.id,
          title: r.title,
          messages: [] as Message[],
          loaded: false,
        }));
        setSessions(local);
        setActiveSessionId(local[0].id);
      }
    }).catch(() => {
      // Backend unavailable — create local session
      const s = makeLocalSession();
      setSessions([s]);
      setActiveSessionId(s.id);
    });
    return () => { alive = false; };
  }, []);

  // Load messages when switching to an unloaded session
  useEffect(() => {
    if (!activeSessionId) return;
    const session = sessions.find((s) => s.id === activeSessionId);
    if (!session || session.loaded) return;

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
  }, [activeSessionId, sessions]);

  const updateSession = useCallback(
    (id: string, updater: (s: ChatSessionLocal) => ChatSessionLocal) => {
      setSessions((prev) => prev.map((s) => (s.id === id ? updater(s) : s)));
    },
    [],
  );

  // ── Streaming ──────────────────────────────────────────────────────────

  const handleComplete = useCallback(
    (payload: { content: string; toolCalls: ToolCallInfo[] }) => {
      const sid = activeIdRef.current;
      const msg = makeMessage("assistant", payload.content, payload.toolCalls);
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

  const {
    sendMessage: wsSendMessage,
    cancelGeneration,
    streamingState,
    connectionStatus,
  } = useChatStream({
    onComplete: handleComplete,
    onError: handleError,
  });

  // ── Actions ────────────────────────────────────────────────────────────

  const sendMessage = useCallback(
    (text: string) => {
      const sid = activeIdRef.current;
      const userMsg = makeMessage("user", text);
      updateSession(sid, (s) => ({
        ...s,
        messages: [...s.messages, userMsg],
        title: s.messages.length === 0 ? text.slice(0, 50) : s.title,
      }));
      wsSendMessage(text, sid);
    },
    [updateSession, wsSendMessage],
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
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== id);
        if (next.length === 0) {
          const fresh = makeLocalSession();
          api.createChatSession("New Chat").catch(() => {});
          return [fresh];
        }
        if (activeIdRef.current === id) {
          setActiveSessionId(next[0].id);
        }
        return next;
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

export function useChat(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
