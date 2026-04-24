import { useEffect, useRef } from "react";
import { useChat } from "../context/ChatContext";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import ConnectionStatus from "./ConnectionStatus";
import ThinkingCard from "./ThinkingCard";
import BrowserCanvas from "./BrowserCanvas";
import TemplateSuggestBanner from "./TemplateSuggestBanner";
import PlanApprovalCard from "./PlanApprovalCard";
import PlanModeToggle from "./PlanModeToggle";
import BrainBadge from "./BrainBadge";

export default function ChatSidebar() {
  const {
    activeSession,
    streamingState,
    connectionStatus,
    sendMessage,
    cancelGeneration,
    dismissBrowserSession,
    dismissTemplateSuggest,
    clearPendingPlan,
    chatOpen,
    chatExpanded,
    toggleChat,
    toggleExpanded,
    createSession,
  } = useChat();

  // Forward "Help" submissions from the BrowserCanvas into the side-note
  // channel so the running agent picks them up between TAOR iterations.
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail;
      if (detail) sendMessage(detail);
    };
    window.addEventListener("lazyclaw:browser-help", handler as EventListener);
    return () =>
      window.removeEventListener("lazyclaw:browser-help", handler as EventListener);
  }, [sendMessage]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const isStreaming = streamingState.isStreaming;

  // Auto-scroll on new messages or streaming
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeSession.messages.length, streamingState.streamContent]);

  if (!chatOpen) {
    return (
      <button
        onClick={toggleChat}
        className="w-[48px] bg-bg-secondary border-l border-border flex flex-col items-center pt-4 gap-2 shrink-0"
        title="Open chat"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="text-text-muted">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        {isStreaming && (
          <span className="w-2 h-2 rounded-full bg-accent live-pulse" />
        )}
      </button>
    );
  }

  const isEmpty = activeSession.messages.length === 0 && !isStreaming;

  const widthClass = chatExpanded
    ? "flex-1"
    : "w-[380px] min-w-[320px] max-w-[50vw]";

  return (
    <div className={`${widthClass} bg-bg-primary border-l border-border flex flex-col shrink-0 transition-all duration-200`}>
      {/* Header */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-border bg-bg-secondary shrink-0">
        {/* Title */}
        <span className="text-xs font-medium text-text-primary truncate flex-1 min-w-0">
          {activeSession.title || "Chat"}
        </span>

        {/* Active brain model — matches what Telegram uses, visible drift check */}
        <BrainBadge />

        {/* Plan mode toggle — quick switch between Plan ↔ Auto */}
        <PlanModeToggle />

        {/* New chat */}
        <button
          onClick={createSession}
          className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-accent transition-colors"
          title="New chat"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>

        <ConnectionStatus status={connectionStatus} />

        {/* Expand/collapse toggle */}
        <button
          onClick={toggleExpanded}
          className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
          title={chatExpanded ? "Shrink chat" : "Expand chat"}
        >
          {chatExpanded ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <polyline points="4 14 10 14 10 20" />
              <polyline points="20 10 14 10 14 4" />
              <line x1="14" y1="10" x2="21" y2="3" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <polyline points="15 3 21 3 21 9" />
              <polyline points="9 21 3 21 3 15" />
              <line x1="21" y1="3" x2="14" y2="10" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          )}
        </button>
        {/* Close */}
        <button
          onClick={toggleChat}
          className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
          title="Close chat panel"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      </div>

      {/* Live browser canvas — only when an event has arrived */}
      {streamingState.browserSession && (
        <BrowserCanvas
          session={streamingState.browserSession}
          onDismiss={dismissBrowserSession}
        />
      )}

      {/* Post-turn "save this as a template?" suggestion */}
      {streamingState.templateSuggest && (
        <TemplateSuggestBanner
          suggest={streamingState.templateSuggest}
          onDismiss={dismissTemplateSuggest}
        />
      )}

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isEmpty ? (
          <div className="h-full flex flex-col items-center justify-center px-4">
            <div className="w-10 h-10 rounded-xl bg-accent-soft flex items-center justify-center mb-4">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0110 0v4" />
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-text-primary mb-1">Chat with LazyClaw</h3>
            <p className="text-xs text-text-muted text-center max-w-[240px]">
              Browse, research, automate — E2E encrypted.
            </p>
          </div>
        ) : (
          <div className="py-1">
            {activeSession.messages.map((m) => (
              <MessageBubble
                key={m.id}
                role={m.role}
                content={m.content}
                timestamp={m.timestamp}
                toolCalls={m.toolCalls}
                tokens={m.tokens}
                cost={m.cost}
                model={m.model}
                latency_ms={m.latency_ms}
                modelUsed={m.modelUsed}
                fallbackReason={m.fallbackReason}
              />
            ))}

            {streamingState.pendingPlan && (
              <PlanApprovalCard
                plan={streamingState.pendingPlan}
                onResolved={clearPendingPlan}
              />
            )}

            {isStreaming && !streamingState.pendingPlan && (
              <>
                <ThinkingCard
                  phase={streamingState.currentPhase}
                  tools={streamingState.activeTools}
                  sideNotes={streamingState.sideNotes}
                  startedAt={streamingState.startedAt}
                  thinkingContent={streamingState.thinkingContent}
                  thinkingDone={streamingState.thinkingDone}
                />
                {streamingState.streamContent && (
                  <MessageBubble
                    role="assistant"
                    content={streamingState.streamContent}
                    toolCalls={streamingState.activeTools}
                    isStreaming
                  />
                )}
              </>
            )}
            <div className="h-2" />
          </div>
        )}
      </div>

      {/* Input */}
      <ChatInput
        onSend={sendMessage}
        disabled={connectionStatus !== "connected"}
        isStreaming={isStreaming}
        onCancel={cancelGeneration}
      />
    </div>
  );
}
