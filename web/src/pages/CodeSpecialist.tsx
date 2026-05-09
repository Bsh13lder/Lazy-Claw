import { useMemo, useState } from "react";
import { useAgentStatus } from "../context/AgentStatusContext";
import type { AgentTask } from "../api";

/**
 * Code Specialist — live view of every Claude Code MCP background run,
 * grouped by `project_tag` so you can see at a glance what the agent
 * has been working on lately ("upwork:job-X", "reddit:dm",
 * "gig:abc123", or untagged "user_request").
 *
 * Reads live state from the existing `useAgentStatus()` context — no
 * new endpoint, no new WS subscription. The context already polls
 * `/api/agents/status` every 3s and is nudged immediately by the chat
 * WS on lifecycle events. The Code Specialist page filters/groups what
 * Activity already shows, with a richer per-task tool-stream drawer.
 */

interface ProjectGroup {
  key: string;            // project_tag, or "" for untagged
  label: string;          // human-readable header
  badge: string;          // short pill text (e.g. "Upwork", "Reddit")
  active: AgentTask[];
  recent: AgentTask[];
}

function classifyProject(tag: string): { label: string; badge: string } {
  if (!tag) return { label: "User Requests", badge: "User" };
  const [scope, ...rest] = tag.split(":");
  const detail = rest.join(":") || "—";
  switch (scope.toLowerCase()) {
    case "upwork":   return { label: `Upwork — ${detail}`, badge: "Upwork" };
    case "reddit":   return { label: `Reddit — ${detail}`, badge: "Reddit" };
    case "gig":      return { label: `Gig — ${detail}`, badge: "Gig" };
    case "fiverr":   return { label: `Fiverr — ${detail}`, badge: "Fiverr" };
    case "internal": return { label: `Internal — ${detail}`, badge: "Internal" };
    default:         return { label: tag, badge: scope.slice(0, 8) };
  }
}

function fmtElapsed(seconds?: number, fallback = "—"): string {
  if (seconds == null || !isFinite(seconds)) return fallback;
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}m ${r}s`;
}

function statusColor(status: string): string {
  switch (status) {
    case "running":   return "bg-amber-500/20 text-amber-300 border-amber-500/30";
    case "done":      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/30";
    case "failed":    return "bg-rose-500/20 text-rose-300 border-rose-500/30";
    case "cancelled": return "bg-slate-500/20 text-slate-300 border-slate-500/30";
    default:          return "bg-slate-500/15 text-slate-300 border-slate-500/30";
  }
}

function isCodeSpecialistTask(t: AgentTask): boolean {
  // Heuristic: a "code specialist" task is anything routed through the
  // claude-code MCP. Detection is loose because instructions are user-
  // authored — we look at the instruction body, the task name, and the
  // current/recent tools for any claude-code signature. False positives
  // here are fine (the page IS about background work in general); false
  // negatives miss the point.
  const hay = [
    t.instruction || "",
    t.name || "",
    t.current_tool || "",
    ...(t.recent_tools || []),
  ].join(" ").toLowerCase();
  return hay.includes("claude-code") || hay.includes("claude_code") || hay.includes("code_specialist");
}

interface TaskCardProps {
  task: AgentTask;
  expanded: boolean;
  onToggle: () => void;
}

function TaskCard({ task, expanded, onToggle }: TaskCardProps) {
  const elapsed = task.elapsed_s != null
    ? fmtElapsed(task.elapsed_s)
    : task.duration_s != null ? fmtElapsed(task.duration_s) : "—";
  return (
    <div
      className={`rounded-lg border ${expanded ? "border-accent/50" : "border-bg-border"} bg-bg-secondary transition-colors`}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-3 py-2 flex items-center gap-3 hover:bg-bg-hover rounded-lg"
      >
        <span className={`px-2 py-0.5 rounded-full text-xs border ${statusColor(task.status)}`}>
          {task.status}
        </span>
        <span className="font-medium text-text-primary truncate flex-1">
          {task.name}
        </span>
        <span className="text-xs text-text-muted whitespace-nowrap">
          {elapsed}
        </span>
        {task.step_count != null && task.step_count > 0 && (
          <span className="text-xs text-text-muted whitespace-nowrap">
            · {task.step_count} steps
          </span>
        )}
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-2 text-xs">
          {task.instruction && (
            <div>
              <div className="text-text-muted uppercase tracking-wide text-[10px] mb-0.5">
                Instruction
              </div>
              <div className="text-text-secondary whitespace-pre-wrap break-words">
                {task.instruction}
              </div>
            </div>
          )}
          {task.current_tool && task.status === "running" && (
            <div>
              <div className="text-text-muted uppercase tracking-wide text-[10px] mb-0.5">
                Current tool
              </div>
              <div className="text-amber-300 font-mono">{task.current_tool}</div>
            </div>
          )}
          {task.recent_tools && task.recent_tools.length > 0 && (
            <div>
              <div className="text-text-muted uppercase tracking-wide text-[10px] mb-0.5">
                Recent tools
              </div>
              <div className="flex flex-wrap gap-1">
                {task.recent_tools.map((t, i) => (
                  <span
                    key={`${t}-${i}`}
                    className="px-1.5 py-0.5 rounded bg-bg-tertiary text-text-secondary font-mono text-[11px]"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}
          {task.result && task.status === "done" && (
            <div>
              <div className="text-text-muted uppercase tracking-wide text-[10px] mb-0.5">
                Result
              </div>
              <div className="text-text-secondary whitespace-pre-wrap break-words max-h-48 overflow-auto">
                {task.result}
              </div>
            </div>
          )}
          {task.error && (
            <div>
              <div className="text-rose-400 uppercase tracking-wide text-[10px] mb-0.5">
                Error
              </div>
              <div className="text-rose-300 whitespace-pre-wrap break-words">
                {task.error}
              </div>
            </div>
          )}
          {task.cost_usd != null && task.cost_usd > 0 && (
            <div className="text-text-muted">
              Cost: ${task.cost_usd.toFixed(4)} · Tokens: {task.tokens_used ?? 0} · Calls: {task.llm_calls ?? 0}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function CodeSpecialist() {
  const { agentStatus } = useAgentStatus();
  const [filter, setFilter] = useState<string>("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  const groups = useMemo<ProjectGroup[]>(() => {
    const active = (agentStatus?.background || []).filter(isCodeSpecialistTask);
    const recent = (agentStatus?.background_recent || []).filter(isCodeSpecialistTask);
    const byTag = new Map<string, ProjectGroup>();
    const ensure = (tag: string): ProjectGroup => {
      const existing = byTag.get(tag);
      if (existing) return existing;
      const cls = classifyProject(tag);
      const fresh: ProjectGroup = {
        key: tag,
        label: cls.label,
        badge: cls.badge,
        active: [],
        recent: [],
      };
      byTag.set(tag, fresh);
      return fresh;
    };
    for (const t of active) ensure(t.project_tag || "").active.push(t);
    for (const t of recent) ensure(t.project_tag || "").recent.push(t);
    // Sort: groups with active tasks first, then by most-recent-recent
    return Array.from(byTag.values()).sort((a, b) => {
      if (a.active.length !== b.active.length) {
        return b.active.length - a.active.length;
      }
      return a.label.localeCompare(b.label);
    });
  }, [agentStatus]);

  const allBadges = useMemo(() => {
    const set = new Set<string>(["all"]);
    for (const g of groups) set.add(g.badge);
    return Array.from(set);
  }, [groups]);

  const visibleGroups = useMemo(() => {
    if (filter === "all") return groups;
    return groups.filter((g) => g.badge === filter);
  }, [groups, filter]);

  const totalActive = groups.reduce((acc, g) => acc + g.active.length, 0);
  const totalRecent = groups.reduce((acc, g) => acc + g.recent.length, 0);

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">Code Specialist</h1>
          <p className="text-sm text-text-muted">
            Live Claude Code MCP background runs — grouped by project. {totalActive} running, {totalRecent} recent.
          </p>
        </div>
      </header>

      <div className="flex flex-wrap gap-1.5">
        {allBadges.map((b) => (
          <button
            key={b}
            type="button"
            onClick={() => setFilter(b)}
            className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${
              filter === b
                ? "bg-accent text-bg-primary border-accent"
                : "bg-bg-secondary text-text-secondary border-bg-border hover:border-accent/50"
            }`}
          >
            {b === "all" ? "All projects" : b}
          </button>
        ))}
      </div>

      {visibleGroups.length === 0 && (
        <div className="rounded-lg border border-bg-border bg-bg-secondary p-8 text-center text-sm text-text-muted">
          No Code Specialist activity yet. Start a background coding task and it'll appear here.
        </div>
      )}

      {visibleGroups.map((g) => (
        <section
          key={g.key || "_untagged"}
          className="rounded-xl border border-bg-border bg-bg-primary/40 p-4 space-y-3"
        >
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 rounded text-xs bg-accent/20 text-accent border border-accent/30">
              {g.badge}
            </span>
            <h2 className="text-base font-semibold text-text-primary truncate">
              {g.label}
            </h2>
            <span className="ml-auto text-xs text-text-muted">
              {g.active.length} running · {g.recent.length} recent
            </span>
          </div>

          {g.active.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[10px] uppercase tracking-wide text-amber-400">Running</div>
              {g.active.map((t) => (
                <TaskCard
                  key={t.task_id}
                  task={t}
                  expanded={expanded === t.task_id}
                  onToggle={() =>
                    setExpanded((prev) => (prev === t.task_id ? null : t.task_id))
                  }
                />
              ))}
            </div>
          )}

          {g.recent.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[10px] uppercase tracking-wide text-text-muted">Recent</div>
              {g.recent.map((t) => (
                <TaskCard
                  key={t.task_id}
                  task={t}
                  expanded={expanded === t.task_id}
                  onToggle={() =>
                    setExpanded((prev) => (prev === t.task_id ? null : t.task_id))
                  }
                />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
