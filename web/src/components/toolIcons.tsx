import type { ReactNode } from "react";

/**
 * Tool-category icon lookup. Matches on tool-name prefix so new skills
 * automatically pick up the right icon without needing explicit registration.
 * Falls back to a generic "wrench" icon for unknown names.
 */

type Category =
  | "browser"
  | "memory"
  | "task"
  | "mcp"
  | "code"
  | "search"
  | "file"
  | "computer"
  | "team"
  | "skill"
  | "time"
  | "calc"
  | "permission"
  | "delegate"
  | "default";

const iconProps = {
  width: 13,
  height: 13,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const ICONS: Record<Category, ReactNode> = {
  browser: (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15 15 0 0 1 4 10 15 15 0 0 1-4 10 15 15 0 0 1-4-10 15 15 0 0 1 4-10z" />
    </svg>
  ),
  memory: (
    <svg {...iconProps}>
      <path d="M21 15a2 2 0 0 1-2 2h-5l-4 4v-4H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  ),
  task: (
    <svg {...iconProps}>
      <polyline points="9 11 12 14 22 4" />
      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
    </svg>
  ),
  mcp: (
    <svg {...iconProps}>
      <rect x="2" y="7" width="20" height="14" rx="2" />
      <path d="M6 21v-4M18 21v-4M2 14h20M10 3v4M14 3v4" />
    </svg>
  ),
  code: (
    <svg {...iconProps}>
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  ),
  search: (
    <svg {...iconProps}>
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  ),
  file: (
    <svg {...iconProps}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  ),
  computer: (
    <svg {...iconProps}>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <line x1="8" y1="21" x2="16" y2="21" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  ),
  team: (
    <svg {...iconProps}>
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  ),
  skill: (
    <svg {...iconProps}>
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  ),
  time: (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  ),
  calc: (
    <svg {...iconProps}>
      <rect x="4" y="2" width="16" height="20" rx="2" />
      <line x1="8" y1="6" x2="16" y2="6" />
      <line x1="8" y1="14" x2="8" y2="14" />
      <line x1="12" y1="14" x2="12" y2="14" />
      <line x1="16" y1="14" x2="16" y2="14" />
      <line x1="8" y1="18" x2="8" y2="18" />
      <line x1="12" y1="18" x2="12" y2="18" />
      <line x1="16" y1="18" x2="16" y2="18" />
    </svg>
  ),
  permission: (
    <svg {...iconProps}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  ),
  delegate: (
    <svg {...iconProps}>
      <path d="M7 17l10-10M7 7h10v10" />
    </svg>
  ),
  default: (
    <svg {...iconProps}>
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  ),
};

const COLORS: Record<Category, string> = {
  browser: "text-sky-400",
  memory: "text-purple-400",
  task: "text-accent",
  mcp: "text-indigo-400",
  code: "text-amber",
  search: "text-cyan",
  file: "text-emerald-400",
  computer: "text-slate-300",
  team: "text-orange-400",
  skill: "text-yellow-300",
  time: "text-text-muted",
  calc: "text-pink-400",
  permission: "text-rose-400",
  delegate: "text-orange-400",
  default: "text-cyan",
};

function categorize(name: string): Category {
  const n = name.toLowerCase();
  if (n.startsWith("team:") || n === "delegate" || n === "dispatch_subagents") return "team";
  if (n.startsWith("browse") || n.startsWith("browser") || n.startsWith("read_page") || n.startsWith("see_browser") || n.includes("page_")) return "browser";
  if (n.startsWith("search_tools")) return "skill";
  if (n.startsWith("recall_mem") || n.startsWith("save_mem") || n.includes("memory") || n.includes("_log") || n.includes("daily_log")) return "memory";
  if (n.startsWith("web_search") || n.startsWith("search")) return "search";
  if (n.includes("task") || n === "add_task" || n === "list_tasks" || n === "complete_task" || n === "update_task" || n === "delete_task" || n.startsWith("run_background") || n.startsWith("stop_background") || n === "daily_briefing" || n === "work_todos") return "task";
  if (n.startsWith("mcp_") || n.includes("_mcp") || n.startsWith("connect_mcp") || n.startsWith("add_mcp") || n.startsWith("remove_mcp")) return "mcp";
  if (n === "code" || n === "run_code" || n === "python" || n === "skill_writer" || n.startsWith("create_skill") || n.startsWith("edit_skill")) return "code";
  if (n.startsWith("read_file") || n.startsWith("write_file") || n.startsWith("list_dir") || n.startsWith("list_directory")) return "file";
  if (n.startsWith("run_command") || n.includes("screenshot") || n === "take_screenshot" || n === "run_shell" || n === "connector") return "computer";
  if (n === "get_time" || n.startsWith("time")) return "time";
  if (n === "calculate" || n.startsWith("calc")) return "calc";
  if (n.startsWith("permission") || n.startsWith("approve") || n.startsWith("deny") || n.includes("audit")) return "permission";
  return "default";
}

export function iconFor(name: string): ReactNode {
  return ICONS[categorize(name)];
}

export function colorFor(name: string): string {
  return COLORS[categorize(name)];
}

/** Per-action icons for the BrowserCanvas timeline (click/type/goto/scroll/...). */
const BROWSER_ACTION_ICONS: Record<string, ReactNode> = {
  click: (
    <svg {...iconProps}>
      <path d="M9 9l5 12 1.8-5.2L21 14z" />
      <path d="M7.2 2.2l1.4 1.4M2.2 7.2l1.4 1.4M5 5l3 3" />
    </svg>
  ),
  click_by_role: (
    <svg {...iconProps}>
      <path d="M9 9l5 12 1.8-5.2L21 14z" />
    </svg>
  ),
  type: (
    <svg {...iconProps}>
      <rect x="2" y="6" width="20" height="12" rx="2" />
      <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M6 14h12" />
    </svg>
  ),
  goto: (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20M12 2a15 15 0 0 1 4 10 15 15 0 0 1-4 10 15 15 0 0 1-4-10 15 15 0 0 1 4-10z" />
    </svg>
  ),
  scroll: (
    <svg {...iconProps}>
      <path d="M12 4v16M6 10l6-6 6 6M6 14l6 6 6-6" />
    </svg>
  ),
  screenshot: (
    <svg {...iconProps}>
      <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
      <circle cx="12" cy="13" r="4" />
    </svg>
  ),
  press_key: (
    <svg {...iconProps}>
      <rect x="3" y="6" width="18" height="12" rx="2" />
      <path d="M7 12h10" />
    </svg>
  ),
  close_tab: (
    <svg {...iconProps}>
      <line x1="6" y1="6" x2="18" y2="18" />
      <line x1="6" y1="18" x2="18" y2="6" />
    </svg>
  ),
  checkpoint: (
    <svg {...iconProps}>
      <path d="M9 11l3 3L22 4" />
      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
    </svg>
  ),
  takeover: (
    <svg {...iconProps}>
      <path d="M12 19l7-7 3 3-7 7-3-3z" />
      <path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z" />
    </svg>
  ),
};

export function browserActionIcon(action: string | undefined): ReactNode {
  if (!action) return ICONS.browser;
  return BROWSER_ACTION_ICONS[action] ?? ICONS.browser;
}

/** Condense tool args to a short human-readable summary.
 *  Picks the first non-trivial string arg and truncates. */
export function argSummary(args: Record<string, unknown> | undefined): string {
  if (!args) return "";
  const priority = ["action", "query", "url", "instruction", "task", "name", "text", "content", "prompt"];
  for (const k of priority) {
    const v = args[k];
    if (typeof v === "string" && v.length > 0) {
      return v.length > 60 ? v.slice(0, 60) + "…" : v;
    }
  }
  // Fall back: first string value
  for (const [, v] of Object.entries(args)) {
    if (typeof v === "string" && v.length > 0) {
      return v.length > 60 ? v.slice(0, 60) + "…" : v;
    }
  }
  const n = Object.keys(args).length;
  return n > 0 ? `${n} arg${n === 1 ? "" : "s"}` : "";
}
