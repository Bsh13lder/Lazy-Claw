import { useCallback, useEffect, useRef, useState } from "react";
import { useChatStream, type ToolCallInfo } from "../hooks/useChatStream";
import MessageBubble from "../components/MessageBubble";
import ChatInput from "../components/ChatInput";
import ConnectionStatus from "../components/ConnectionStatus";

// ── Types ──────────────────────────────────────────────────────────────────

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  toolCalls?: ToolCallInfo[];
}

interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
}

// ── Helpers ────────────────────────────────────────────────────────────────

function createSession(): ChatSession {
  return { id: crypto.randomUUID(), title: "New Chat", messages: [] };
}

function getSessionIdFromUrl(): string | null {
  const params = new URLSearchParams(window.location.search);
  return params.get("session");
}

function setSessionIdInUrl(sessionId: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("session", sessionId);
  window.history.replaceState(null, "", url.toString());
}

function makeMessage(
  role: "user" | "assistant",
  content: string,
  toolCalls?: ToolCallInfo[],
): Message {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    timestamp: Date.now(),
    toolCalls,
  };
}

// ── Component ──────────────────────────────────────────────────────────────

export default function Chat() {
  const [sessions, setSessions] = useState<ChatSession[]>(() => [createSession()]);
  const [activeId, setActiveId] = useState<string>(() => {
    const urlId = getSessionIdFromUrl();
    return urlId ?? sessions[0].id;
  });
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;

  const activeSession = sessions.find((s) => s.id === activeId) ?? sessions[0];

  // Sync activeId to URL
  useEffect(() => {
    setSessionIdInUrl(activeId);
  }, [activeId]);

  // Ensure URL session exists in sessions list (mount only)
  useEffect(() => {
    const urlId = getSessionIdFromUrl();
    if (urlId && !sessions.some((s) => s.id === urlId)) {
      const newSession: ChatSession = { id: urlId, title: "New Chat", messages: [] };
      setSessions((prev) => [newSession, ...prev]);
      setActiveId(urlId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateSession = useCallback(
    (id: string, updater: (s: ChatSession) => ChatSession) => {
      setSessions((prev) => prev.map((s) => (s.id === id ? updater(s) : s)));
    },
    [],
  );

  // ── Streaming ────────────────────────────────────────────────────────────

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

  // Auto-scroll on new messages or streaming content
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeSession.messages.length, streamingState.streamContent]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleSend = useCallback(
    (text: string) => {
      const sid = activeId;
      const userMsg = makeMessage("user", text);
      updateSession(sid, (s) => ({
        ...s,
        messages: [...s.messages, userMsg],
        title: s.messages.length === 0 ? text.slice(0, 50) : s.title,
      }));
      wsSendMessage(text, sid);
    },
    [activeId, updateSession, wsSendMessage],
  );

  const handleNewChat = useCallback(() => {
    const s = createSession();
    setSessions((prev) => [s, ...prev]);
    setActiveId(s.id);
  }, []);

  const isEmpty = activeSession.messages.length === 0;
  const isStreaming = streamingState.isStreaming;

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="h-full flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-border bg-bg-secondary overflow-x-auto shrink-0">
        <button
          onClick={handleNewChat}
          className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors shrink-0"
          title="New chat"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <div className="w-px h-5 bg-border mx-1" />
        {sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => setActiveId(s.id)}
            className={`px-3 py-1.5 rounded-lg text-xs truncate max-w-[140px] transition-colors shrink-0 ${
              s.id === activeId
                ? "bg-bg-hover text-text-primary"
                : "text-text-muted hover:bg-bg-hover/60 hover:text-text-secondary"
            }`}
          >
            {s.title}
          </button>
        ))}
        <div className="flex-1" />
        <ConnectionStatus status={connectionStatus} />
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isEmpty && !isStreaming ? (
          <div className="h-full flex flex-col items-center justify-center px-4">
            <div className="w-12 h-12 rounded-2xl bg-accent-soft flex items-center justify-center mb-5">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0110 0v4" />
              </svg>
            </div>
            <h2 className="text-base font-semibold text-text-primary mb-1">Chat with LazyClaw</h2>
            <p className="text-sm text-text-muted text-center max-w-sm">
              Browse, research, code, automate tasks — all E2E encrypted.
            </p>
          </div>
        ) : (
          <div className="py-2">
            {activeSession.messages.map((m) => (
              <MessageBubble
                key={m.id}
                role={m.role}
                content={m.content}
                timestamp={m.timestamp}
                toolCalls={m.toolCalls}
              />
            ))}

            {/* Streaming message or thinking indicator */}
            {isStreaming && (
              streamingState.streamContent ? (
                <MessageBubble
                  role="assistant"
                  content={streamingState.streamContent}
                  toolCalls={streamingState.activeTools}
                  isStreaming
                />
              ) : (
                <div className="py-4 animate-fade-in">
                  <div className="max-w-3xl mx-auto flex gap-4 px-4">
                    <div className="w-7 h-7 rounded-full bg-accent-soft flex items-center justify-center shrink-0">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                        <rect x="3" y="11" width="18" height="11" rx="2" />
                        <path d="M7 11V7a5 5 0 0110 0v4" />
                      </svg>
                    </div>
                    <div className="flex gap-1.5 items-center pt-2">
                      <span className="w-2 h-2 bg-text-muted rounded-full pulse-dot" />
                      <span className="w-2 h-2 bg-text-muted rounded-full pulse-dot" style={{ animationDelay: "0.2s" }} />
                      <span className="w-2 h-2 bg-text-muted rounded-full pulse-dot" style={{ animationDelay: "0.4s" }} />
                    </div>
                  </div>
                </div>
              )
            )}
            <div className="h-4" />
          </div>
        )}
      </div>

      <ChatInput
        onSend={handleSend}
        disabled={isStreaming || connectionStatus !== "connected"}
        isStreaming={isStreaming}
        onCancel={cancelGeneration}
      />
    </div>
  );
}
