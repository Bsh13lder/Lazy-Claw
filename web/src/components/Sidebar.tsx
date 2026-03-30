interface ChatSession {
  id: string;
  title: string;
}

interface SidebarProps {
  open: boolean;
  sessions: ChatSession[];
  activeSessionId: string;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  onClose: () => void;
}

export default function Sidebar({
  open,
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  onClose,
}: SidebarProps) {
  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 bg-black/60 z-20 md:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={`
          fixed md:relative z-30 top-0 left-0 h-full
          bg-bg-secondary border-r border-border
          sidebar-transition flex flex-col
          ${open
            ? "w-64 translate-x-0"
            : "w-0 -translate-x-full md:translate-x-0"
          }
        `}
      >
        <div className="flex flex-col h-full w-64 overflow-hidden">
          {/* Top bar with new chat + close */}
          <div className="flex items-center justify-between p-2 h-12 shrink-0">
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
              aria-label="Close sidebar"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <path d="M9 3v18" />
              </svg>
            </button>

            <button
              onClick={onNewChat}
              className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
              aria-label="New chat"
              title="New chat"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </button>
          </div>

          {/* Chat list */}
          <div className="flex-1 overflow-y-auto px-2 pb-2">
            <div className="space-y-0.5">
              {sessions.map((s) => (
                <button
                  key={s.id}
                  onClick={() => onSelectSession(s.id)}
                  className={`
                    w-full text-left px-3 py-2.5 rounded-lg text-[13px] truncate transition-colors
                    ${s.id === activeSessionId
                      ? "bg-bg-hover text-text-primary"
                      : "text-text-secondary hover:bg-bg-hover/60 hover:text-text-primary"
                    }
                  `}
                >
                  {s.title}
                </button>
              ))}
            </div>
          </div>

          {/* Bottom */}
          <div className="p-3 border-t border-border shrink-0">
            <div className="flex items-center gap-2 text-[11px] text-text-muted">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent shrink-0">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0110 0v4" />
              </svg>
              <span>AES-256-GCM encrypted</span>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
