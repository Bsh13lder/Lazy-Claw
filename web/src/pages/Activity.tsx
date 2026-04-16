import { useState } from "react";
import { useAgentStatus } from "../context/AgentStatusContext";
import type { AgentTask, ActivityEvent, AgentMetrics } from "../api";
import { cancelTask } from "../api";
import { StatusBadge, LaneBadge, RecentTaskRow, formatElapsed } from "../components/TaskRow";
import { iconFor, colorFor } from "../components/toolIcons";

/* ── Helpers ─────────────────────────────────────────────────── */

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return iso;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/* ── Filter Pills ────────────────────────────────────────────── */

function PillGroup<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly { id: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex gap-1">
      {options.map((o) => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          className={`px-2.5 py-1 text-[10px] rounded-lg border transition-colors ${
            value === o.id
              ? "border-accent bg-accent-soft text-accent"
              : "border-border text-text-muted hover:bg-bg-hover"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ── Quick Stats ─────────────────────────────────────────────── */

function QuickStats({
  active,
  background,
  done,
  failed,
  metrics,
}: {
  active: number;
  background: number;
  done: number;
  failed: number;
  metrics: AgentMetrics | null;
}) {
  return (
    <div className="grid grid-cols-3 md:grid-cols-6 gap-3 mb-6">
      <StatCard label="Active" value={active} accent={active > 0} />
      <StatCard label="Background" value={background} accent={background > 0} color="text-purple-400" />
      <StatCard label="Completed" value={done} color="text-green-400" />
      <StatCard label="Failed" value={failed} accent={failed > 0} color="text-red-400" />
      <StatCard
        label="Avg Duration"
        value={metrics ? `${metrics.avg_duration_s}s` : "—"}
        color="text-text-secondary"
      />
      <StatCard
        label="Success Rate"
        value={metrics ? `${metrics.success_rate}%` : "—"}
        color={metrics && metrics.success_rate >= 80 ? "text-green-400" : metrics && metrics.success_rate >= 50 ? "text-amber" : "text-red-400"}
      />
    </div>
  );
}

function StatCard({
  label,
  value,
  accent,
  color,
}: {
  label: string;
  value: string | number;
  accent?: boolean;
  color?: string;
}) {
  return (
    <div className="px-4 py-3 rounded-xl bg-bg-secondary border border-border">
      <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">{label}</p>
      <p className={`text-lg font-semibold ${color ?? (accent ? "text-accent" : "text-text-muted")}`}>{value}</p>
    </div>
  );
}

/* ── Phase Badge ──────────────────────────────────────────────── */

const PHASE_COLORS: Record<string, string> = {
  think: "bg-cyan/10 text-cyan border-cyan/30",
  act: "bg-accent/10 text-accent border-accent/30",
  observe: "bg-amber/10 text-amber border-amber/30",
  reflect: "bg-purple-400/10 text-purple-400 border-purple-400/30",
};

function PhaseBadge({ phase }: { phase: string }) {
  const cls = PHASE_COLORS[phase] || "bg-bg-tertiary text-text-muted border-border";
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border text-[9px] font-medium uppercase tracking-wider ${cls}`}>
      <span className="w-1 h-1 rounded-full pulse-dot bg-current" />
      {phase}
    </span>
  );
}

/* ── Active Task Card ────────────────────────────────────────── */

function ActiveTaskCard({ task, onCancelled }: { task: AgentTask; onCancelled?: () => void }) {
  const [cancelling, setCancelling] = useState(false);

  const handleCancel = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (cancelling) return;
    setCancelling(true);
    try {
      await cancelTask(task.task_id);
      onCancelled?.();
    } catch {
      setCancelling(false);
    }
  };

  const currentTool = task.current_tool || task.current_step;
  const recentTools = task.recent_tools || [];
  const request = task.instruction || task.description;

  return (
    <div className="bg-bg-secondary border border-border rounded-xl p-4 animate-fade-in hover:border-border-light transition-colors">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="inline-block w-2 h-2 rounded-full bg-accent live-pulse" />
        <span className="text-sm font-medium text-text-primary flex-1 min-w-0 truncate">{task.name}</span>
        {task.phase && <PhaseBadge phase={task.phase} />}
        <LaneBadge lane={task.lane} />
        <span className="text-xs text-text-muted tabular-nums shrink-0">{formatElapsed(task.elapsed_s ?? 0)}</span>
        <button
          onClick={handleCancel}
          disabled={cancelling}
          className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-red-900/30 text-red-400 hover:bg-red-900/50 disabled:opacity-50 transition-colors"
          title="Cancel this task"
        >
          {cancelling ? "cancelling…" : "cancel"}
        </button>
      </div>
      {request && (
        <p className="text-xs text-text-secondary mb-2 line-clamp-2">
          <span className="text-text-muted/60">›</span> {request}
        </p>
      )}
      {/* Current tool being used */}
      {currentTool && (
        <div className="flex items-center gap-1.5 text-[11px] text-text-muted">
          <span className={colorFor(currentTool)}>{iconFor(currentTool)}</span>
          <span>Using <span className="text-cyan font-medium">{currentTool}</span></span>
          {(task.step_count ?? 0) > 0 && (
            <span className="text-text-muted">· step {task.step_count}</span>
          )}
        </div>
      )}
      {/* Recent tools strip */}
      {recentTools.length > 1 && (
        <div className="flex items-center gap-1 mt-1.5 flex-wrap">
          {recentTools.slice(-5).map((t, i) => (
            <span
              key={`${t}-${i}`}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border border-border/50 bg-bg-tertiary/40 text-text-muted"
            >
              <span className={colorFor(t)}>{iconFor(t)}</span>
              <span className="truncate max-w-[80px]">{t}</span>
            </span>
          ))}
        </div>
      )}
      {/* Progress bar */}
      {(task.step_count ?? 0) > 0 && (
        <div className="mt-2">
          <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all duration-500 animate-bar-fill"
              style={{ width: `${Math.min(((Number(task.step_count) || 0) / Math.max(Number(task.step_count) || 1, 5)) * 100, 95)}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function SpecialistSubCard({ task }: { task: AgentTask }) {
  const [cancelling, setCancelling] = useState(false);
  const handleCancel = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (cancelling) return;
    setCancelling(true);
    try {
      await cancelTask(task.task_id);
    } catch {
      setCancelling(false);
    }
  };
  const currentTool = task.current_tool || task.current_step;
  return (
    <div className="bg-bg-secondary border border-border rounded-lg px-3 py-2 animate-fade-in">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="w-1.5 h-1.5 rounded-full bg-orange-400 live-pulse" />
        <LaneBadge lane="specialist" />
        <span className="text-xs text-text-primary truncate flex-1 min-w-0">{task.name}</span>
        {task.phase && <PhaseBadge phase={task.phase} />}
        <span className="text-[10px] text-text-muted tabular-nums">{formatElapsed(task.elapsed_s ?? 0)}</span>
        <button
          onClick={handleCancel}
          disabled={cancelling}
          className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-red-900/30 text-red-400 hover:bg-red-900/50 disabled:opacity-50 transition-colors"
        >
          {cancelling ? "…" : "cancel"}
        </button>
      </div>
      {currentTool && (
        <p className="text-[10px] text-text-muted mt-1 flex items-center gap-1.5">
          <span className={colorFor(currentTool)}>{iconFor(currentTool)}</span>
          Using <span className="text-cyan">{currentTool}</span>
        </p>
      )}
    </div>
  );
}

/* ── Timeline View ───────────────────────────────────────────── */

const EVENT_STYLE: Record<string, { dot: string; icon: string }> = {
  task: { dot: "bg-blue-400", icon: "text-blue-400" },
  tool_execution: { dot: "bg-cyan", icon: "text-cyan" },
  specialist: { dot: "bg-orange-400", icon: "text-orange-400" },
  approval: { dot: "bg-amber", icon: "text-amber" },
  error: { dot: "bg-red-400", icon: "text-red-400" },
};

function TimelineView({ events }: { events: ActivityEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-text-muted text-center py-8">No activity events</p>;
  }

  return (
    <div className="relative pl-6">
      {/* Vertical line */}
      <div className="absolute left-[9px] top-2 bottom-2 w-px bg-border" />

      {events.map((ev) => {
        const style = EVENT_STYLE[ev.type] ?? EVENT_STYLE.task;
        const isRunning = ev.status === "running";
        return (
          <div key={ev.id} className="relative flex gap-3 pb-4 group">
            {/* Dot */}
            <div className={`relative z-10 mt-1.5 w-[10px] h-[10px] rounded-full shrink-0 ${style.dot} ${isRunning ? "live-pulse" : ""}`} />

            {/* Card */}
            <div className="flex-1 min-w-0 bg-bg-secondary border border-border rounded-lg px-3 py-2 hover:bg-bg-hover/50 transition-colors">
              <div className="flex items-center gap-2 mb-0.5">
                <span className={`text-[10px] font-medium uppercase tracking-wider ${style.icon}`}>
                  {ev.type.replace(/_/g, " ")}
                </span>
                {ev.status !== "done" && <StatusBadge status={ev.status} />}
                {ev.duration_ms != null && (
                  <span className="text-[10px] text-text-muted tabular-nums ml-auto">{formatMs(ev.duration_ms)}</span>
                )}
              </div>
              <p className="text-sm text-text-primary truncate">{ev.title}</p>
              {ev.detail && <p className="text-xs text-text-muted truncate">{ev.detail}</p>}
              <p className="text-[10px] text-text-muted mt-0.5">{timeAgo(ev.timestamp)}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────────── */

type ViewMode = "cards" | "timeline";
type LaneFilter = "all" | "foreground" | "background" | "specialist";
type StatusFilter = "all" | "running" | "done" | "failed";

const LANE_OPTIONS = [
  { id: "all" as const, label: "All" },
  { id: "foreground" as const, label: "Foreground" },
  { id: "background" as const, label: "Background" },
  { id: "specialist" as const, label: "Specialist" },
];

const STATUS_OPTIONS = [
  { id: "all" as const, label: "All" },
  { id: "running" as const, label: "Running" },
  { id: "done" as const, label: "Done" },
  { id: "failed" as const, label: "Failed" },
];

export default function Activity() {
  const { agentStatus: data, activityFeed, metrics } = useAgentStatus();
  const [expandedTask, setExpandedTask] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("cards");
  const [laneFilter, setLaneFilter] = useState<LaneFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");

  const allActive = [
    ...(data?.active ?? []),
    ...(data?.background ?? []),
  ];

  // Split active into parent and specialist tasks
  const parentTasks = allActive.filter((t) => t.lane !== "specialist");
  const specialistTasks = allActive.filter((t) => t.lane === "specialist");

  // Apply filters to recent
  const filteredRecent = (data?.recent ?? []).filter((t) => {
    if (laneFilter !== "all" && t.lane !== laneFilter) return false;
    if (statusFilter !== "all" && t.status !== statusFilter) return false;
    if (search && !t.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const done = (data?.recent ?? []).filter((t) => t.status === "done").length;
  const failed = (data?.recent ?? []).filter((t) => t.status === "failed").length;

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="max-w-4xl mx-auto space-y-6">

        {/* Quick Stats */}
        <QuickStats
          active={data?.active.length ?? 0}
          background={data?.background.length ?? 0}
          done={done}
          failed={failed}
          metrics={metrics}
        />

        {/* Filters + View toggle */}
        <div className="flex flex-wrap items-center gap-3">
          <PillGroup options={LANE_OPTIONS} value={laneFilter} onChange={setLaneFilter} />
          <PillGroup options={STATUS_OPTIONS} value={statusFilter} onChange={setStatusFilter} />
          <input
            type="text"
            placeholder="Search tasks..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="px-3 py-1 text-xs rounded-lg bg-bg-tertiary border border-border text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light w-40"
          />
          <div className="ml-auto flex gap-1">
            <button
              onClick={() => setView("cards")}
              className={`px-2.5 py-1 text-[10px] rounded-lg border transition-colors ${
                view === "cards" ? "border-accent bg-accent-soft text-accent" : "border-border text-text-muted hover:bg-bg-hover"
              }`}
            >
              Cards
            </button>
            <button
              onClick={() => setView("timeline")}
              className={`px-2.5 py-1 text-[10px] rounded-lg border transition-colors ${
                view === "timeline" ? "border-accent bg-accent-soft text-accent" : "border-border text-text-muted hover:bg-bg-hover"
              }`}
            >
              Timeline
            </button>
          </div>
        </div>

        {view === "timeline" ? (
          /* Timeline View */
          <section>
            <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
              Execution Timeline
            </h2>
            <TimelineView events={activityFeed} />
          </section>
        ) : (
          <>
            {/* Running Now — with specialist hierarchy */}
            <section>
              <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                  <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                </svg>
                Running Now
                {allActive.length > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full bg-accent-soft text-accent text-[10px] font-medium">
                    {allActive.length}
                  </span>
                )}
              </h2>
              {allActive.length === 0 ? (
                <p className="text-sm text-text-muted text-center py-8">No active tasks</p>
              ) : (
                <div className="grid gap-3">
                  {parentTasks.map((t) => (
                    <div key={t.task_id}>
                      <ActiveTaskCard task={t} />
                      {/* Nested specialist tasks */}
                      {specialistTasks.length > 0 && (
                        <div className="ml-6 mt-1 space-y-1 border-l-2 border-orange-400/30 pl-3">
                          {specialistTasks.map((st) => (
                            <SpecialistSubCard key={st.task_id} task={st} />
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                  {/* Show orphan specialists (no parent visible) */}
                  {parentTasks.length === 0 && specialistTasks.map((st) => (
                    <ActiveTaskCard key={st.task_id} task={st} />
                  ))}
                </div>
              )}
            </section>

            {/* Recent Activity */}
            <section>
              <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-text-muted">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                Recent Activity
                {filteredRecent.length > 0 && (
                  <span className="text-[10px] text-text-muted font-normal">
                    {filteredRecent.length} tasks
                  </span>
                )}
              </h2>
              {filteredRecent.length === 0 ? (
                <p className="text-sm text-text-muted text-center py-8">No recent activity</p>
              ) : (
                <div className="bg-bg-secondary border border-border rounded-xl overflow-hidden">
                  {filteredRecent.map((t) => (
                    <RecentTaskRow
                      key={t.task_id}
                      task={t}
                      expanded={expandedTask === t.task_id}
                      onToggle={() => setExpandedTask(expandedTask === t.task_id ? null : t.task_id)}
                      onCancel={t.status === "running" ? () => { cancelTask(t.task_id).catch(() => {}); } : undefined}
                    />
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
