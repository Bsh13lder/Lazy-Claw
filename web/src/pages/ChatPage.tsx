import AgentConsole from "../components/AgentConsole";
import ChatSidebar from "../components/ChatSidebar";

/**
 * Dedicated full-screen Chat page.
 *
 * Reuses the same `ChatSidebar` component the right-rail uses — it owns
 * its own data via `useChat()` so we just hand it `presentationMode="page"`
 * and it renders full-width without the sidebar chrome.
 *
 * Above it, `AgentConsole` provides a collapsible activity strip that
 * surfaces foreground / background / recent agent tasks in real time.
 *
 * The right-side `ChatSidebar` is suppressed by `NavShell` while this
 * page is active so we never have two chat instances mounted at once.
 */
export default function ChatPage() {
  return (
    <div className="h-full w-full flex flex-col bg-bg-primary min-h-0">
      <AgentConsole />
      <div className="flex-1 min-h-0 flex flex-col">
        <ChatSidebar presentationMode="page" />
      </div>
    </div>
  );
}
