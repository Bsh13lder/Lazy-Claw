import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import Login from "./pages/Login";
import Chat from "./pages/Chat";
import Overview from "./pages/Overview";
import Skills from "./pages/Skills";
import Jobs from "./pages/Jobs";
import Mcp from "./pages/Mcp";
import Memory from "./pages/Memory";
import Vault from "./pages/Vault";
import Settings from "./pages/Settings";
import NavShell from "./components/NavShell";

function AppContent() {
  const { user, loading } = useAuth();

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

  return (
    <NavShell>
      <Routes>
        <Route path="/overview" element={<Overview />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/skills" element={<Skills />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/mcp" element={<Mcp />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/vault" element={<Vault />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Routes>
    </NavShell>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <AppContent />
      </ToastProvider>
    </AuthProvider>
  );
}
