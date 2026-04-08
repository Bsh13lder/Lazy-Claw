import { useState } from "react";
import { useAgentStatus } from "../context/AgentStatusContext";
import type { AgentTask, ActivityEvent, AgentMetrics } from "../api";
import { StatusBadge, LaneBadge, RecentTaskRow, formatElapsed } from "../components/TaskRow";

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

/* ── Active Task Card ────────────────────────────────────────── */

function ActiveTaskCard({ task }: { task: AgentTask }) {
  return (
    <div className="bg-bg-secondary border border-border rounded-xl p-4 animate-fade-in">
      <div className="flex items-center gap-2 mb-2">
        <span className="inline-block w-2 h-2 rounded-full bg-accent live-pulse" />
        <span className="text-sm font-medium text-text-primary flex-1 min-w-0 truncate">{task.name}</span>
        <LaneBadge lane={task.lane} />
        <span className="text-xs text-text-muted tabular-nums shrink-0">{formatElapsed(task.elapsed_s ?? 0)}</span>
      </div>
      {task.description && (
        <p className="text-xs text-text-secondary mb-2 line-clamp-2">{task.description}</p>
      )}
      {/* Current tool being used */}
      {task.current_step && (
        <div className="flex items-center gap-2 text-[11px] text-text-muted">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-cyan shrink-0">
            <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
          </svg>
          <span>Using <span className="text-cyan">{task.current_step}</span></span>
          {(task.step_count ?? 0) > 0 && (
            <span className="text-text-muted">({task.step_count} calls)</span>
          )}
        </div>
      )}
      {/* Progress bar */}
      {(task.step_count ?? 0) > 0 && task.current_step != null && (
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
                            <div key={st.task_id} className="bg-bg-secondary border border-border rounded-lg px-3 py-2 animate-fade-in">
                              <div className="flex items-center gap-2">
                                <span className="w-1.5 h-1.5 rounded-full bg-orange-400 live-pulse" />
                                <LaneBadge lane="specialist" />
                                <span className="text-xs text-text-primary truncate flex-1">{st.name}</span>
                                <span className="text-[10px] text-text-muted tabular-nums">{formatElapsed(st.elapsed_s ?? 0)}</span>
                              </div>
                              {st.current_step && (
                                <p className="text-[10px] text-text-muted mt-1 ml-4">Using <span className="text-cyan">{st.current_step}</span></p>
                              )}
                            </div>
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
