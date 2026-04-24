import { useMemo, useState } from "react";
import type { AgentTask, ActivityEvent } from "../api";
import { cancelTask } from "../api";
import { RecentTaskRow, StatusBadge, formatElapsed } from "./TaskRow";

/**
 * History + cancel log panel with rich filtering.
 *
 * Inputs:
 *   • recent — completed/failed/cancelled tasks from /api/agents/status.recent
 *   • events — merged tool/task/approval stream from /api/agents/activity/feed
 *
 * The panel lets the user slice the same data three ways:
 *   Cards    → expandable RecentTaskRow list (best for task drill-down)
 *   Timeline → chronological event stream with colored dots
 *   Table    → compact scannable rows (name · status · lane · duration · when)
 *
 * Filters: Lane · Status · Kind · Range · free-text search. All client-side.
 */

export type LaneFilter = "all" | "foreground" | "background" | "specialist";
export type StatusFilter = "all" | "running" | "done" | "failed" | "cancelled";
export type KindFilter = "all" | "task" | "tool" | "approval" | "error";
export type RangeFilter = "today" | "7d" | "30d" | "all";
export type ViewMode = "cards" | "timeline" | "table";

const LANE_OPTS: readonly { id: LaneFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "foreground", label: "Foreground" },
  { id: "background", label: "Background" },
  { id: "specialist", label: "Specialist" },
];
const STATUS_OPTS: readonly { id: StatusFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "running", label: "Running" },
  { id: "done", label: "Done" },
  { id: "failed", label: "Failed" },
  { id: "cancelled", label: "Cancelled" },
];
const KIND_OPTS: readonly { id: KindFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "task", label: "Tasks" },
  { id: "tool", label: "Tool calls" },
  { id: "approval", label: "Approvals" },
  { id: "error", label: "Errors" },
];
const RANGE_OPTS: readonly { id: RangeFilter; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "7d", label: "7d" },
  { id: "30d", label: "30d" },
  { id: "all", label: "All" },
];

function PillGroup<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: readonly { id: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  label?: string;
}) {
  return (
    <div className="flex items-center gap-1.5">
      {label && <span className="text-[10px] uppercase tracking-wider text-text-muted">{label}</span>}
      <div className="flex gap-1">
        {options.map((o) => (
          <button
            key={o.id}
            onClick={() => onChange(o.id)}
            className={`px-2.5 py-1 text-[10px] rounded-md border transition-colors ${
              value === o.id
                ? "border-accent bg-accent-soft text-accent"
                : "border-border text-text-muted hover:bg-bg-hover hover:text-text-secondary"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function rangeSince(range: RangeFilter): number {
  const now = Date.now();
  if (range === "today") {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }
  if (range === "7d") return now - 7 * 864e5;
  if (range === "30d") return now - 30 * 864e5;
  return 0;
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

function formatMs(ms?: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const EVENT_DOT: Record<string, string> = {
  task: "bg-blue-400",
  tool_execution: "bg-cyan",
  specialist: "bg-orange-400",
  approval: "bg-amber",
  error: "bg-red-400",
};

function TimelineRow({ ev }: { ev: ActivityEvent }) {
  const dot = EVENT_DOT[ev.type] ?? "bg-text-muted";
  const running = ev.status === "running";
  return (
    <div className="relative flex gap-3 pb-3 group">
      <div className={`relative z-10 mt-1.5 w-[9px] h-[9px] rounded-full shrink-0 ${dot} ${running ? "live-pulse" : ""}`} />
      <div className="flex-1 min-w-0 rounded-lg bg-bg-secondary border border-border/70 px-3 py-2 hover:border-border-light transition-colors">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-[10px] uppercase tracking-wider text-text-muted">
            {ev.type.replace(/_/g, " ")}
          </span>
          {ev.status !== "done" && <StatusBadge status={ev.status} />}
          {ev.duration_ms != null && (
            <span className="ticker text-[10px] text-text-muted ml-auto">{formatMs(ev.duration_ms)}</span>
          )}
        </div>
        <p className="text-sm text-text-primary truncate">{ev.title}</p>
        {ev.detail && <p className="text-xs text-text-muted truncate">{ev.detail}</p>}
        <p className="text-[10px] text-text-muted mt-0.5">{timeAgo(ev.timestamp)}</p>
      </div>
    </div>
  );
}

function TableRow({ task }: { task: AgentTask }) {
  const duration = task.duration_s ?? task.elapsed_s ?? 0;
  return (
    <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-3 items-center px-3 py-2 hover:bg-bg-hover/40 border-b border-border/40 last:border-b-0 text-[12px]">
      <StatusBadge status={task.status} />
      <p className="truncate text-text-primary" title={task.name}>{task.name}</p>
      <span className="text-[10px] text-text-muted uppercase tracking-wider">{task.lane}</span>
      <span className="ticker text-[10px] text-text-muted shrink-0">{formatElapsed(duration)}</span>
      <span className="text-[10px] text-text-muted shrink-0">
        {task.status === "running" ? "live" : ""}
      </span>
    </div>
  );
}

export function HistoryPanel({
  recent,
  events,
  defaultView = "cards",
  defaultStatus = "all",
}: {
  recent: AgentTask[];
  events: ActivityEvent[];
  defaultView?: ViewMode;
  defaultStatus?: StatusFilter;
}) {
  const [view, setView] = useState<ViewMode>(defaultView);
  const [lane, setLane] = useState<LaneFilter>("all");
  const [status, setStatus] = useState<StatusFilter>(defaultStatus);
  const [kind, setKind] = useState<KindFilter>("all");
  const [range, setRange] = useState<RangeFilter>("7d");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const since = rangeSince(range);
  const q = search.trim().toLowerCase();

  const filteredTasks = useMemo(() => {
    return recent.filter((t) => {
      if (lane !== "all" && t.lane !== lane) return false;
      if (status !== "all" && t.status !== status) return false;
      if (kind !== "all") {
        if (kind === "error" && t.status !== "failed") return false;
        if (kind === "task" && t.lane === "specialist") return false;
        // "tool" and "approval" kinds are event-only — filter them out of the task list
        if (kind === "tool" || kind === "approval") return false;
      }
      if (q && !t.name.toLowerCase().includes(q) && !(t.instruction ?? "").toLowerCase().includes(q)) return false;
      return true;
    });
  }, [recent, lane, status, kind, q]);

  const filteredEvents = useMemo(() => {
    return events.filter((ev) => {
      const ts = new Date(ev.timestamp).getTime();
      if (!Number.isNaN(ts) && since > 0 && ts < since) return false;
      if (kind === "task" && ev.type !== "task") return false;
      if (kind === "tool" && ev.type !== "tool_execution") return false;
      if (kind === "approval" && ev.type !== "approval") return false;
      if (kind === "error" && ev.type !== "error") return false;
      if (status !== "all" && ev.status !== status) return false;
      if (q && !ev.title.toLowerCase().includes(q) && !ev.detail.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [events, since, kind, status, q]);

  const cancelledCount = useMemo(() => recent.filter((t) => t.status === "cancelled").length, [recent]);

  return (
    <section className="space-y-3">
      {/* Toggle bar */}
      <div className="flex flex-wrap items-center gap-3">
        <PillGroup options={LANE_OPTS} value={lane} onChange={setLane} />
        <PillGroup options={STATUS_OPTS} value={status} onChange={setStatus} />
        <PillGroup options={KIND_OPTS} value={kind} onChange={setKind} />
        <PillGroup options={RANGE_OPTS} value={range} onChange={setRange} />
        <input
          type="text"
          placeholder="Search name or instruction…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-1 text-xs rounded-md bg-bg-tertiary border border-border text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-accent/50 w-48"
        />

        <div className="ml-auto flex items-center gap-1 bg-bg-tertiary rounded-md p-0.5 border border-border">
          {(["cards", "timeline", "table"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2.5 py-1 text-[10px] rounded transition-colors capitalize ${
                view === v
                  ? "bg-accent-soft text-accent"
                  : "text-text-muted hover:text-text-secondary"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Active filter count line */}
      <div className="flex items-center gap-3 text-[10px] text-text-muted">
        <span>
          {view === "timeline"
            ? `${filteredEvents.length} events`
            : `${filteredTasks.length} tasks`}
        </span>
        {cancelledCount > 0 && status !== "cancelled" && (
          <button
            onClick={() => setStatus("cancelled")}
            className="text-amber hover:text-amber/80 transition-colors"
          >
            · {cancelledCount} cancelled →
          </button>
        )}
      </div>

      {/* Views */}
      {view === "timeline" ? (
        filteredEvents.length === 0 ? (
          <p className="text-sm text-text-muted text-center py-10">No events in this range.</p>
        ) : (
          <div className="relative pl-5">
            <div className="absolute left-[8px] top-2 bottom-2 w-px bg-border" />
            {filteredEvents.map((ev) => <TimelineRow key={ev.id} ev={ev} />)}
          </div>
        )
      ) : view === "table" ? (
        filteredTasks.length === 0 ? (
          <p className="text-sm text-text-muted text-center py-10">No tasks match your filters.</p>
        ) : (
          <div className="rounded-xl bg-bg-secondary border border-border overflow-hidden">
            <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-3 items-center px-3 py-2 border-b border-border text-[10px] uppercase tracking-wider text-text-muted">
              <span>Status</span>
              <span>Name</span>
              <span>Lane</span>
              <span>Duration</span>
              <span>When</span>
            </div>
            {filteredTasks.map((t) => <TableRow key={t.task_id} task={t} />)}
          </div>
        )
      ) : (
        filteredTasks.length === 0 ? (
          <p className="text-sm text-text-muted text-center py-10">No tasks match your filters.</p>
        ) : (
          <div className="rounded-xl bg-bg-secondary border border-border overflow-hidden">
            {filteredTasks.map((t) => (
              <RecentTaskRow
                key={t.task_id}
                task={t}
                expanded={expanded === t.task_id}
                onToggle={() => setExpanded(expanded === t.task_id ? null : t.task_id)}
                onCancel={t.status === "running" ? () => { cancelTask(t.task_id).catch(() => {}); } : undefined}
              />
            ))}
          </div>
        )
      )}
    </section>
  );
}
