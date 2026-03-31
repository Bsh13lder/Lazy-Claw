import { useCallback, useEffect, useRef, useState } from "react";
import { sendMessage } from "../api";
import MessageBubble from "../components/MessageBubble";
import ChatInput from "../components/ChatInput";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
}

function createSession(): ChatSession {
  return { id: crypto.randomUUID(), title: "New Chat", messages: [] };
}

export default function Chat() {
  const [sessions, setSessions] = useState<ChatSession[]>(() => [createSession()]);
  const [activeId, setActiveId] = useState(() => sessions[0].id);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const activeSession = sessions.find((s) => s.id === activeId) ?? sessions[0];

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeSession.messages.length, loading]);

  const updateSession = useCallback(
    (id: string, updater: (s: ChatSession) => ChatSession) => {
      setSessions((prev) => prev.map((s) => (s.id === id ? updater(s) : s)));
    },
    [],
  );

  const handleSend = useCallback(
    async (text: string) => {
      const sid = activeId;
      const userMsg: Message = { role: "user", content: text };
      updateSession(sid, (s) => ({
        ...s,
        messages: [...s.messages, userMsg],
        title: s.messages.length === 0 ? text.slice(0, 50) : s.title,
      }));

      setLoading(true);
      try {
        const res = await sendMessage(text);
        updateSession(sid, (s) => ({
          ...s,
          messages: [...s.messages, { role: "assistant", content: res.response }],
        }));
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : "Something went wrong";
        updateSession(sid, (s) => ({
          ...s,
          messages: [...s.messages, { role: "assistant", content: `**Error:** ${errMsg}` }],
        }));
      } finally {
        setLoading(false);
      }
    },
    [activeId, updateSession],
  );

  const handleNewChat = () => {
    const s = createSession();
    setSessions((prev) => [s, ...prev]);
    setActiveId(s.id);
  };

  const isEmpty = activeSession.messages.length === 0;

  return (
    <div className="h-full flex flex-col">
      {/* Top bar: sessions tabs */}
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
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isEmpty ? (
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
            {activeSession.messages.map((m, i) => (
              <MessageBubble key={i} role={m.role} content={m.content} />
            ))}
            {loading && (
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
            )}
            <div className="h-4" />
          </div>
        )}
      </div>

      <ChatInput onSend={handleSend} disabled={loading} />
    </div>
  );
}
