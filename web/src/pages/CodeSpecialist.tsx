import { useMemo, useState } from "react";
import { useAgentStatus } from "../context/AgentStatusContext";
import type { AgentTask, CodeSpecialistStep } from "../api";
import NewCodeTaskModal from "../components/NewCodeTaskModal";

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

interface CollapsibleProps {
  label: string;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

// Pure CSS collapsible — mirrors ThinkingPanel from ThinkingPanel.tsx
// without pulling that component in (lives in chat-only contexts).
// `count` adds a small pill next to the label so users can see size at
// a glance ("Transcript · 12").
function Collapsible({ label, count, defaultOpen = false, children }: CollapsibleProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-bg-border first:border-t-0 pt-2 first:pt-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 text-left text-text-muted uppercase tracking-wide text-[10px] hover:text-text-secondary"
      >
        <span
          className={`transition-transform inline-block ${open ? "rotate-90" : ""}`}
        >
          ▶
        </span>
        <span>{label}</span>
        {count != null && (
          <span className="ml-auto px-1.5 py-0.5 rounded bg-bg-tertiary text-text-secondary text-[10px] font-mono">
            {count}
          </span>
        )}
      </button>
      {open && (
        <div className="mt-2 text-xs text-text-secondary">{children}</div>
      )}
    </div>
  );
}

function copyToClipboard(text: string) {
  // navigator.clipboard requires HTTPS or localhost — Vite dev is fine.
  // Best-effort: silently no-op if the API isn't available.
  try {
    navigator.clipboard?.writeText(text);
  } catch {
    /* ignore */
  }
}

// Format a single TranscriptStep row for the timeline. Reuses the
// status palette from AgentConsole's LaneRow so the page reads
// consistently with the chat-side activity strip.
function StepRow({ step }: { step: CodeSpecialistStep }) {
  const isErr = step.success === false || !!step.error;
  const dot = isErr
    ? "bg-rose-500"
    : step.duration_ms > 0
    ? "bg-emerald-500"
    : "bg-amber-500";
  return (
    <div className="flex items-start gap-2 py-1 border-t border-bg-border/40 first:border-t-0">
      <span className={`mt-1.5 h-1.5 w-1.5 rounded-full shrink-0 ${dot}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-text-primary text-[11px] truncate">
            {step.name}
          </span>
          {step.duration_ms > 0 && (
            <span className="text-text-muted text-[10px]">
              {step.duration_ms}ms
            </span>
          )}
        </div>
        {step.args_summary && (
          <div className="text-text-muted font-mono text-[11px] truncate">
            {step.args_summary}
          </div>
        )}
        {step.result_summary && (
          <div
            className={`font-mono text-[11px] truncate ${
              isErr ? "text-rose-300" : "text-text-secondary"
            }`}
          >
            → {step.result_summary}
          </div>
        )}
      </div>
    </div>
  );
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
  // Short description pill is always visible (collapsed AND expanded) so
  // the user can scan a tall list of tasks and see what each one is
  // *for* without expanding. Falls back to the existing description /
  // truncated instruction so non-code-specialist rows still get a
  // meaningful subtitle.
  const subtitle =
    task.short_description ||
    task.description ||
    (task.instruction ? task.instruction.split("\n")[0].slice(0, 120) : "");

  return (
    <div
      className={`rounded-lg border ${expanded ? "border-accent/50" : "border-bg-border"} bg-bg-secondary transition-colors`}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-3 py-2 flex flex-col gap-1 hover:bg-bg-hover rounded-lg"
      >
        <div className="flex items-center gap-3">
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
        </div>
        {subtitle && (
          <div className="text-[11px] text-text-secondary truncate pl-1">
            {subtitle}
          </div>
        )}
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-2 text-xs">
          {/* Workspace folder + files. Clickable path copies to
              clipboard so the user can paste it into a terminal.
              Files are chip-list — truncated at 8 with overflow count. */}
          {task.workspace_dir && (
            <div className="rounded border border-bg-border bg-bg-tertiary/40 p-2 space-y-1.5">
              <div className="text-text-muted uppercase tracking-wide text-[10px]">
                Workspace
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  copyToClipboard(task.workspace_dir || "");
                }}
                title="Click to copy path"
                className="font-mono text-[11px] text-accent hover:underline break-all text-left w-full"
              >
                📁 {task.workspace_dir}
              </button>
              {task.files_touched && task.files_touched.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {task.files_touched.slice(0, 8).map((f) => (
                    <span
                      key={f}
                      className="px-1.5 py-0.5 rounded bg-bg-secondary text-text-secondary font-mono text-[10px]"
                    >
                      {f}
                    </span>
                  ))}
                  {task.files_touched.length > 8 && (
                    <span className="px-1.5 py-0.5 rounded bg-bg-secondary text-text-muted font-mono text-[10px]">
                      +{task.files_touched.length - 8} more
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

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

          {/* Full prompt sent to claude-code MCP. Collapsible because
              system prompt + workspace hint + user task often runs to
              several hundred lines. Hidden when empty (e.g. browser
              specialist tasks). */}
          {task.mcp_prompt && (
            <Collapsible label="Prompt sent">
              <div className="rounded bg-bg-tertiary/40 p-2 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px]">
                {task.mcp_prompt}
              </div>
            </Collapsible>
          )}

          {/* Per-step transcript — the visibility win the user asked
              for. Each row shows tool + args + result + duration.
              Defaults open for running tasks so live progress is
              visible; closed for completed tasks to keep the page
              scrollable. */}
          {task.mcp_transcript && task.mcp_transcript.length > 0 && (
            <Collapsible
              label="Transcript"
              count={task.mcp_transcript.length}
              defaultOpen={task.status === "running"}
            >
              <div className="rounded bg-bg-tertiary/40 p-2 max-h-72 overflow-auto">
                {task.mcp_transcript.map((s, i) => (
                  <StepRow key={`${s.name}-${i}`} step={s} />
                ))}
              </div>
            </Collapsible>
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
  const [modalOpen, setModalOpen] = useState(false);

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
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="px-4 py-2 rounded-lg bg-accent text-bg-primary text-sm font-medium hover:bg-accent/90 transition-colors flex items-center gap-2 shrink-0"
        >
          <span className="text-lg leading-none">+</span>
          New Code Task
        </button>
      </header>
      <NewCodeTaskModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
      />


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

      {visibleGroups.map((g) => {
        // Collect distinct Goal Executor goal_ids in this group so
        // users can see "Goal X is driving N code tasks here" at the
        // group header level without expanding individual cards.
        const goalIds = Array.from(
          new Set(
            [...g.active, ...g.recent]
              .map((t) => t.goal_id)
              .filter((id): id is string => !!id),
          ),
        );
        return (
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
          {goalIds.length > 0 && (
            <div className="flex flex-wrap gap-1.5 text-[10px] text-text-muted">
              <span className="uppercase tracking-wide">
                Driven by goal{goalIds.length > 1 ? "s" : ""}:
              </span>
              {goalIds.map((id) => (
                <span
                  key={id}
                  className="px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-300 font-mono"
                  title={id}
                >
                  {id.slice(0, 12)}
                </span>
              ))}
            </div>
          )}

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
        );
      })}
    </div>
  );
}
