import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ChatProvider } from "./context/ChatContext";
import { AgentStatusProvider } from "./context/AgentStatusContext";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Activity from "./pages/Activity";
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
  "overview", "activity", "tasks", "notes", "replay", "audit", "hub", "skills",
  "templates", "jobs", "watchers", "mcp", "memory", "lazybrain",
  "vault", "settings",
];

function readPageFromUrl(): Page {
  if (typeof window === "undefined") return "overview";
  const q = new URLSearchParams(window.location.search).get("page");
  return (VALID_PAGES as readonly string[]).includes(q ?? "") ? (q as Page) : "overview";
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
      case "lazybrain": return <LazyBrain />;
      case "vault": return <Vault />;
      case "settings": return <Settings />;
      default: return <Overview onNavigate={setPage} />;
    }
  })();

  return (
    <ChatProvider>
      <AgentStatusProvider>
        <NavShell activePage={page} onNavigate={setPage}>
          {pageContent}
        </NavShell>
      </AgentStatusProvider>
    </ChatProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}
