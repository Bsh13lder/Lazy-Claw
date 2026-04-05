import { useEffect, useState } from "react";
import { useChat } from "../context/ChatContext";
import * as api from "../api";

export default function StatusBar() {
  const { connectionStatus, streamingState, chatOpen, toggleChat } = useChat();
  const [taskCount, setTaskCount] = useState({ active: 0, background: 0 });

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const status = await api.getAgentStatus();
        if (alive) {
          setTaskCount({
            active: status.active.length,
            background: status.background.length,
          });
        }
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const wsColor =
    connectionStatus === "connected" ? "bg-accent" :
    connectionStatus === "connecting" ? "bg-amber" : "bg-error";

  const totalActive = taskCount.active + taskCount.background;

  return (
    <div className="h-7 bg-bg-secondary border-t border-border flex items-center px-3 gap-4 text-[11px] text-text-muted shrink-0">
      {/* Connection */}
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full ${wsColor}`} />
        <span>{connectionStatus}</span>
      </div>

      {/* Active tasks */}
      {totalActive > 0 && (
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-accent live-pulse" />
          <span>
            {taskCount.active > 0 && `${taskCount.active} active`}
            {taskCount.active > 0 && taskCount.background > 0 && " · "}
            {taskCount.background > 0 && `${taskCount.background} background`}
          </span>
        </div>
      )}

      {/* Streaming indicator */}
      {streamingState.isStreaming && (
        <div className="flex items-center gap-1.5">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner text-cyan">
            <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
          </svg>
          <span className="text-cyan">Generating...</span>
        </div>
      )}

      <div className="flex-1" />

      {/* Chat toggle */}
      {!chatOpen && (
        <button
          onClick={toggleChat}
          className="flex items-center gap-1.5 hover:text-text-secondary transition-colors"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          Open Chat
        </button>
      )}
    </div>
  );
}
