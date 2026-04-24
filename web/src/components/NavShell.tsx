import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { useAgentStatus } from "../context/AgentStatusContext";
import ChatSidebar from "./ChatSidebar";
import StatusBar from "./StatusBar";

export type Page = "overview" | "activity" | "tasks" | "replay" | "audit" | "hub" | "skills" | "templates" | "jobs" | "watchers" | "mcp" | "memory" | "lazybrain" | "vault" | "settings";

interface NavShellProps {
  activePage: Page;
  onNavigate: (page: Page) => void;
  children: React.ReactNode;
}

const PAGE_META: Record<Page, { label: string; description: string }> = {
  overview: { label: "Overview", description: "System health & activity" },
  activity: { label: "Activity", description: "Live agent & task monitor" },
  tasks: { label: "Tasks", description: "Encrypted todos with NL time + steps" },
  replay: { label: "Replay", description: "Session traces & debugging" },
  audit: { label: "Audit", description: "Action log & security" },
  hub: { label: "Skill Hub", description: "Discover & install skills" },
  skills: { label: "Skills", description: "Manage agent tools" },
  templates: { label: "Templates", description: "Saved browser recipes" },
  jobs: { label: "Jobs", description: "Scheduled & cron tasks" },
  watchers: { label: "Watchers", description: "Zero-token site monitors" },
  mcp: { label: "MCP", description: "Server integrations" },
  memory: { label: "Memory", description: "Personal facts & logs" },
  lazybrain: { label: "LazyBrain", description: "Encrypted Logseq-style PKM" },
  vault: { label: "Vault", description: "Encrypted credentials" },
  settings: { label: "Settings", description: "Agent configuration" },
};

type NavIcon = React.ReactNode;

const ICONS: Record<Page, NavIcon> = {
  overview: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  ),
  activity: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  ),
  tasks: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 11l3 3L22 4" />
      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
    </svg>
  ),
  lazybrain: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a7 7 0 0 0-7 7c0 2.4 1.2 4.5 3 5.7V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.3c1.8-1.3 3-3.3 3-5.7a7 7 0 0 0-7-7Z" />
      <path d="M9 21h6" />
      <path d="M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" />
    </svg>
  ),
  memory: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z" />
      <path d="M12 16v-4M12 8h.01" />
    </svg>
  ),
  vault: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <rect x="3" y="11" width="18" height="11" rx="2" />
      <path d="M7 11V7a5 5 0 0110 0v4" />
    </svg>
  ),
  jobs: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  ),
  watchers: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  templates: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <rect x="3" y="4" width="18" height="4" rx="1" />
      <rect x="3" y="11" width="18" height="4" rx="1" />
      <rect x="3" y="18" width="11" height="3" rx="1" />
    </svg>
  ),
  hub: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <rect x="3" y="3" width="7" height="9" rx="1" />
      <rect x="14" y="3" width="7" height="5" rx="1" />
      <rect x="14" y="12" width="7" height="9" rx="1" />
      <rect x="3" y="16" width="7" height="5" rx="1" />
    </svg>
  ),
  skills: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  ),
  mcp: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
    </svg>
  ),
  replay: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  ),
  audit: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  ),
  settings: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
};

// Disciplined grouping — read top-to-bottom as the typical user journey:
// start on Home, work in your brain, run automations, configure tools, debug.
const NAV_GROUPS: { label: string; items: Page[] }[] = [
  { label: "Home",       items: ["overview", "activity", "tasks"] },
  { label: "Knowledge",  items: ["lazybrain", "memory", "vault"] },
  { label: "Automation", items: ["jobs", "watchers", "templates"] },
  { label: "Tools",      items: ["hub", "skills", "mcp"] },
  { label: "Debug",      items: ["replay", "audit"] },
];

const EXPAND_KEY = "lazyclaw:nav-expanded";

export default function NavShell({ activePage, onNavigate, children }: NavShellProps) {
  const { user, logout } = useAuth();
  const { agentStatus } = useAgentStatus();
  const meta = PAGE_META[activePage];

  const [expanded, setExpanded] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(EXPAND_KEY) === "1";
  });

  useEffect(() => {
    window.localStorage.setItem(EXPAND_KEY, expanded ? "1" : "0");
  }, [expanded]);

  const runningCount = (agentStatus?.active.length ?? 0) + (agentStatus?.background.length ?? 0);

  const railWidth = expanded ? "w-[200px]" : "w-[56px] md:w-[60px]";

  return (
    <div className="h-screen flex flex-col bg-bg-primary">
      <div className="flex-1 flex min-h-0">
        {/* Left nav rail */}
        <nav
          className={`${railWidth} bg-bg-secondary border-r border-border flex flex-col py-3 shrink-0 transition-[width] duration-150 ease-out`}
        >
          {/* Top row: logo + collapse toggle */}
          <div className={`flex items-center mb-3 ${expanded ? "px-3 justify-between" : "justify-center"}`}>
            <button
              onClick={() => onNavigate("overview")}
              className="p-1.5 rounded-lg hover:bg-bg-hover transition-colors flex items-center gap-2"
              title="LazyClaw"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0110 0v4" />
              </svg>
              {expanded && (
                <span className="text-sm font-semibold text-text-primary">LazyClaw</span>
              )}
            </button>
            <button
              onClick={() => setExpanded((v) => !v)}
              className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors"
              title={expanded ? "Collapse sidebar" : "Expand sidebar"}
              aria-label={expanded ? "Collapse sidebar" : "Expand sidebar"}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                {expanded ? (
                  <polyline points="15 18 9 12 15 6" />
                ) : (
                  <polyline points="9 18 15 12 9 6" />
                )}
              </svg>
            </button>
          </div>

          {/* Grouped nav items */}
          <div className="flex-1 overflow-y-auto flex flex-col gap-3 px-1">
            {NAV_GROUPS.map((group) => (
              <div key={group.label} className="flex flex-col gap-0.5">
                {expanded && (
                  <div className="px-3 pt-1 pb-0.5 text-[10px] uppercase tracking-wider text-text-muted/70 font-semibold">
                    {group.label}
                  </div>
                )}
                {group.items.map((page) => {
                  const isActive = activePage === page;
                  return (
                    <button
                      key={page}
                      onClick={() => onNavigate(page)}
                      title={expanded ? undefined : PAGE_META[page].label}
                      className={`relative flex items-center rounded-lg transition-all duration-150 ${
                        expanded ? "mx-1 px-2 py-1.5 gap-2.5" : "mx-auto p-2"
                      } ${
                        isActive
                          ? "bg-bg-hover text-text-primary"
                          : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                      }`}
                    >
                      {isActive && (
                        <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-4 bg-accent rounded-r-full" />
                      )}
                      <span className="shrink-0">{ICONS[page]}</span>
                      {expanded && (
                        <span className="text-sm truncate">{PAGE_META[page].label}</span>
                      )}
                      {page === "activity" && runningCount > 0 && (
                        <span className={`${expanded ? "ml-auto" : "absolute -top-0.5 -right-0.5"} w-[14px] h-[14px] rounded-full bg-accent text-[8px] font-bold text-bg-primary flex items-center justify-center`}>
                          {runningCount > 9 ? "9+" : runningCount}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>

          {/* Bottom block: external n8n + settings + user */}
          <div className={`mt-2 flex flex-col gap-1 ${expanded ? "px-1" : "items-center"}`}>
            <a
              href="http://localhost:5678"
              target="_blank"
              rel="noopener noreferrer"
              title={expanded ? undefined : "n8n Workflows"}
              className={`flex items-center rounded-lg text-text-muted hover:bg-bg-hover hover:text-text-secondary transition-all duration-150 ${
                expanded ? "mx-1 px-2 py-1.5 gap-2.5" : "p-2"
              }`}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="6" cy="12" r="3" />
                <circle cx="18" cy="6" r="3" />
                <circle cx="18" cy="18" r="3" />
                <path d="M9 12h3l3-6M12 12l3 6" />
              </svg>
              {expanded && <span className="text-sm">n8n</span>}
            </a>

            <button
              onClick={() => onNavigate("settings")}
              title={expanded ? undefined : PAGE_META.settings.label}
              className={`flex items-center rounded-lg transition-all duration-150 ${
                expanded ? "mx-1 px-2 py-1.5 gap-2.5" : "p-2"
              } ${
                activePage === "settings"
                  ? "bg-bg-hover text-text-primary"
                  : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
              }`}
            >
              <span className="shrink-0">{ICONS.settings}</span>
              {expanded && <span className="text-sm">Settings</span>}
            </button>

            <div className={`flex items-center gap-2 mt-1 ${expanded ? "px-2 py-1.5" : "flex-col"}`}>
              <div
                className="w-8 h-8 rounded-full bg-accent-soft flex items-center justify-center cursor-default shrink-0"
                title={user?.username ?? "User"}
              >
                <span className="text-xs font-semibold text-accent">
                  {(user?.username?.[0] ?? "U").toUpperCase()}
                </span>
              </div>
              {expanded && (
                <span className="text-xs text-text-secondary truncate flex-1">
                  {user?.username ?? "User"}
                </span>
              )}
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
          </div>
        </nav>

        {/* Main workspace area */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          {/* Page header bar — hidden on LazyBrain so the PKM gets full canvas */}
          {activePage !== "lazybrain" && (
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

        {/* Right chat sidebar — always visible so user can chat with LazyClaw
            from any page, including LazyBrain. Toggle behavior lives inside
            <ChatSidebar> itself (its own collapse button). */}
        <ChatSidebar />
      </div>

      {/* Bottom status bar */}
      <StatusBar />
    </div>
  );
}
