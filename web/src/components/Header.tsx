import { useAuth } from "../context/AuthContext";

interface HeaderProps {
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
}

export default function Header({ onToggleSidebar, sidebarOpen }: HeaderProps) {
  const { user, logout } = useAuth();

  return (
    <header className="flex items-center justify-between h-12 px-3 bg-bg-secondary border-b border-border shrink-0">
      {/* Left: sidebar toggle + model indicator */}
      <div className="flex items-center gap-2">
        {!sidebarOpen && (
          <button
            onClick={onToggleSidebar}
            className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
            aria-label="Open sidebar"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M3 6h18M3 12h18M3 18h18" />
            </svg>
          </button>
        )}

        <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors cursor-default">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
            <rect x="3" y="11" width="18" height="11" rx="2" />
            <path d="M7 11V7a5 5 0 0110 0v4" />
          </svg>
          <span className="text-sm font-medium text-text-primary">LazyClaw</span>
          <span className="text-xs text-text-muted hidden sm:inline ml-0.5">Agent</span>
        </div>
      </div>

      {/* Right: user + logout */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-2 px-2 py-1 rounded-lg">
          <div className="w-6 h-6 rounded-full bg-accent-soft flex items-center justify-center">
            <span className="text-xs font-semibold text-accent">
              {(user?.username?.[0] ?? "U").toUpperCase()}
            </span>
          </div>
          <span className="text-sm text-text-secondary hidden sm:inline">{user?.username}</span>
        </div>
        <button
          onClick={logout}
          className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
          aria-label="Logout"
          title="Logout"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16,17 21,12 16,7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
        </button>
      </div>
    </header>
  );
}
