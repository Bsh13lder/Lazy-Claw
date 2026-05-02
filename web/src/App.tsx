import { Component, useEffect, useState } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ChatProvider } from "./context/ChatContext";
import { AgentStatusProvider } from "./context/AgentStatusContext";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Activity from "./pages/Activity";
import ChatPage from "./pages/ChatPage";
import Tasks from "./pages/Tasks";
import Replay from "./pages/Replay";
import Audit from "./pages/Audit";
import SkillHub from "./pages/SkillHub";
import Skills from "./pages/Skills";
import BrowserTemplates from "./pages/BrowserTemplates";
import Jobs from "./pages/Jobs";
import Watchers from "./pages/Watchers";
import Mcp from "./pages/Mcp";
import Memory from "./pages/Memory";
import LazyBrain from "./pages/LazyBrain";
import Notes from "./pages/Notes";
import Vault from "./pages/Vault";
import Settings from "./pages/Settings";
import NavShell, { type Page } from "./components/NavShell";

const VALID_PAGES: readonly Page[] = [
  "overview", "activity", "chat", "tasks", "notes", "replay", "audit", "hub", "skills",
  "templates", "jobs", "watchers", "mcp", "memory", "lazybrain",
  "vault", "settings",
];

function readPageFromUrl(): Page {
  if (typeof window === "undefined") return "overview";
  const q = new URLSearchParams(window.location.search).get("page");
  return (VALID_PAGES as readonly string[]).includes(q ?? "") ? (q as Page) : "overview";
}

/** Per-page error boundary — surfaces a readable error pane instead of
 *  collapsing the whole shell when a single page crashes. Without this
 *  React's default behavior on a render-time exception is to unmount the
 *  entire tree, which used to look like "the page just isn't loading". */
class PageErrorBoundary extends Component<
  { name: string; children: ReactNode },
  { error: Error | null; componentStack: string | null }
> {
  state = { error: null as Error | null, componentStack: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error(`[${this.props.name}] crashed:`, error, info.componentStack);
    this.setState({ componentStack: info.componentStack ?? null });
  }
  render() {
    if (this.state.error) {
      return (
        <div className="h-full w-full overflow-y-auto bg-bg-primary">
          <div className="max-w-3xl mx-auto p-6">
            <div className="rounded-xl border border-rose-400/40 bg-bg-secondary text-text-primary p-6">
              <div className="text-sm font-semibold text-rose-400 mb-3">
                {this.props.name} failed to render
              </div>
              <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1">
                Error
              </div>
              <pre className="text-[12px] text-rose-300 whitespace-pre-wrap font-mono mb-4 p-2 rounded bg-bg-tertiary">
                {this.state.error.message}
              </pre>
              {this.state.error.stack && (
                <>
                  <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1">
                    Stack
                  </div>
                  <pre className="text-[11px] text-text-muted whitespace-pre-wrap font-mono mb-4 p-2 rounded bg-bg-tertiary max-h-48 overflow-y-auto">
                    {this.state.error.stack}
                  </pre>
                </>
              )}
              {this.state.componentStack && (
                <>
                  <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1">
                    Component stack
                  </div>
                  <pre className="text-[11px] text-text-secondary whitespace-pre-wrap font-mono mb-4 p-2 rounded bg-bg-tertiary max-h-72 overflow-y-auto">
                    {this.state.componentStack}
                  </pre>
                </>
              )}
              <button
                onClick={() => this.setState({ error: null, componentStack: null })}
                className="text-xs px-3 py-1.5 rounded bg-accent text-bg-primary hover:opacity-90"
              >
                Retry
              </button>
              <a
                href="/?page=overview"
                className="text-xs px-3 py-1.5 rounded ml-2 border border-border hover:bg-bg-hover"
              >
                Go to Overview
              </a>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function AppContent() {
  const { user, loading } = useAuth();
  const [page, setPageState] = useState<Page>(readPageFromUrl);

  const setPage = (next: Page) => {
    setPageState(next);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      if (next === "overview") {
        url.searchParams.delete("page");
      } else {
        url.searchParams.set("page", next);
      }
      window.history.replaceState(null, "", url.toString());
    }
  };

  useEffect(() => {
    const onPop = () => setPageState(readPageFromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <div className="flex items-center gap-3 text-text-muted">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="spinner text-accent">
            <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
          </svg>
          Loading...
        </div>
      </div>
    );
  }

  if (!user) return <Login />;

  const pageContent = (() => {
    switch (page) {
      case "overview": return <Overview onNavigate={setPage} />;
      case "activity": return <Activity />;
      case "chat": return <ChatPage />;
      case "tasks": return <Tasks />;
      // /notes is now the Notes tab inside the Tasks workspace. Redirect
      // so Telegram deep-links and any old bookmarks keep working without
      // splitting the surface.
      case "notes": return <Notes />;
      case "replay": return <Replay />;
      case "audit": return <Audit />;
      case "hub": return <SkillHub />;
      case "skills": return <Skills />;
      case "templates": return <BrowserTemplates />;
      case "jobs": return <Jobs onNavigate={setPage} />;
      case "watchers": return <Watchers />;
      case "mcp": return <Mcp />;
      case "memory": return <Memory />;
      case "lazybrain": return <PageErrorBoundary name="LazyBrain"><LazyBrain /></PageErrorBoundary>;
      case "vault": return <Vault />;
      case "settings": return <Settings />;
      default: return <Overview onNavigate={setPage} />;
    }
  })();

  return (
    // AgentStatusProvider MUST wrap ChatProvider — ChatProvider's WebSocket
    // hook reads `useAgentStatus().refreshStatus` to nudge the dashboard
    // immediately on TeamLead / TaskRunner lifecycle events.
    <AgentStatusProvider>
      <ChatProvider>
        <NavShell activePage={page} onNavigate={setPage}>
          {pageContent}
        </NavShell>
      </ChatProvider>
    </AgentStatusProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}
