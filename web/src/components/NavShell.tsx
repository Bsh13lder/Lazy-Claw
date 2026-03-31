import { useAuth } from "../context/AuthContext";

export type Page = "chat" | "overview" | "hub" | "skills" | "jobs" | "mcp" | "memory" | "vault" | "settings";

interface NavShellProps {
  activePage: Page;
  onNavigate: (page: Page) => void;
  children: React.ReactNode;
}

const PAGE_META: Record<Page, { label: string; description: string }> = {
  chat: { label: "Chat", description: "Conversation with your agent" },
  overview: { label: "Overview", description: "System health & activity" },
  hub: { label: "Skill Hub", description: "Browse & discover skills" },
  skills: { label: "Skills", description: "Manage agent tools" },
  jobs: { label: "Jobs", description: "Scheduled & cron tasks" },
  mcp: { label: "MCP", description: "Server integrations" },
  memory: { label: "Memory", description: "Personal facts & logs" },
  vault: { label: "Vault", description: "Encrypted credentials" },
  settings: { label: "Settings", description: "Agent configuration" },
};

const NAV_ITEMS: { page: Page; icon: React.ReactNode }[] = [
  {
    page: "chat",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    ),
  },
  {
    page: "overview",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    ),
  },
  {
    page: "hub",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <rect x="3" y="3" width="7" height="9" rx="1" />
        <rect x="14" y="3" width="7" height="5" rx="1" />
        <rect x="14" y="12" width="7" height="9" rx="1" />
        <rect x="3" y="16" width="7" height="5" rx="1" />
      </svg>
    ),
  },
  {
    page: "skills",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
      </svg>
    ),
  },
  {
    page: "jobs",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <circle cx="12" cy="12" r="10" />
        <polyline points="12 6 12 12 16 14" />
      </svg>
    ),
  },
  {
    page: "mcp",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
      </svg>
    ),
  },
  {
    page: "memory",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z" />
        <path d="M12 16v-4M12 8h.01" />
      </svg>
    ),
  },
  {
    page: "vault",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <rect x="3" y="11" width="18" height="11" rx="2" />
        <path d="M7 11V7a5 5 0 0110 0v4" />
      </svg>
    ),
  },
  {
    page: "settings",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
      </svg>
    ),
  },
];

export default function NavShell({ activePage, onNavigate, children }: NavShellProps) {
  const { user, logout } = useAuth();
  const meta = PAGE_META[activePage];

  return (
    <div className="h-screen flex bg-bg-primary">
      {/* Left nav rail */}
      <nav className="w-[56px] md:w-[60px] bg-bg-secondary border-r border-border flex flex-col items-center py-3 shrink-0">
        {/* Logo */}
        <button
          onClick={() => onNavigate("overview")}
          className="mb-4 p-1.5 rounded-lg hover:bg-bg-hover transition-colors"
          title="LazyClaw"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
            <rect x="3" y="11" width="18" height="11" rx="2" />
            <path d="M7 11V7a5 5 0 0110 0v4" />
          </svg>
        </button>

        {/* Nav items */}
        <div className="flex-1 flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => {
            const isActive = activePage === item.page;
            return (
              <button
                key={item.page}
                onClick={() => onNavigate(item.page)}
                title={PAGE_META[item.page].label}
                className={`relative p-2 rounded-lg transition-all duration-150 ${
                  isActive
                    ? "bg-bg-hover text-text-primary"
                    : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                }`}
              >
                {isActive && (
                  <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-4 bg-accent rounded-r-full" />
                )}
                {item.icon}
              </button>
            );
          })}
        </div>

        {/* Bottom: user avatar + logout */}
        <div className="flex flex-col items-center gap-2 mt-2">
          <div
            className="w-8 h-8 rounded-full bg-accent-soft flex items-center justify-center cursor-default"
            title={user?.username ?? "User"}
          >
            <span className="text-xs font-semibold text-accent">
              {(user?.username?.[0] ?? "U").toUpperCase()}
            </span>
          </div>
          <button
            onClick={logout}
            className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
            title="Logout"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16,17 21,12 16,7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </button>
        </div>
      </nav>

      {/* Main content area */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Page header bar */}
        {activePage !== "chat" && (
          <header className="shrink-0 px-6 py-3 border-b border-border bg-bg-secondary/50 backdrop-blur-sm">
            <div className="flex items-center gap-2">
              <span className="text-text-muted text-xs">LazyClaw</span>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-text-placeholder">
                <polyline points="9 18 15 12 9 6" />
              </svg>
              <span className="text-sm font-medium text-text-primary">{meta.label}</span>
              <span className="text-xs text-text-muted ml-2 hidden sm:inline">{meta.description}</span>
            </div>
          </header>
        )}

        {/* Page content */}
        <main className="flex-1 min-w-0 overflow-hidden">
          {children}
        </main>
      </div>
    </div>
  );
}
