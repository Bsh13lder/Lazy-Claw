import { useState } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ChatProvider } from "./context/ChatContext";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Activity from "./pages/Activity";
import SkillHub from "./pages/SkillHub";
import Skills from "./pages/Skills";
import Jobs from "./pages/Jobs";
import Mcp from "./pages/Mcp";
import Memory from "./pages/Memory";
import Vault from "./pages/Vault";
import Settings from "./pages/Settings";
import NavShell, { type Page } from "./components/NavShell";

function AppContent() {
  const { user, loading } = useAuth();
  const [page, setPage] = useState<Page>("overview");

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
      case "hub": return <SkillHub />;
      case "skills": return <Skills />;
      case "jobs": return <Jobs />;
      case "mcp": return <Mcp />;
      case "memory": return <Memory />;
      case "vault": return <Vault />;
      case "settings": return <Settings />;
      default: return <Overview onNavigate={setPage} />;
    }
  })();

  return (
    <ChatProvider>
      <NavShell activePage={page} onNavigate={setPage}>
        {pageContent}
      </NavShell>
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
