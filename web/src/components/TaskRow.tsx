/**
 * Shared task display components used by both Dashboard (Overview) and Activity pages.
 */

import type { AgentTask } from "../api";

/* ── Helpers ─────────────────────────────────────────────────── */

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

/* ── Badges ──────────────────────────────────────────────────── */

const STATUS_STYLES: Record<string, string> = {
  running: "bg-accent-soft text-accent",
  queued: "bg-blue-900/30 text-blue-400",
  done: "bg-green-900/30 text-green-400",
  failed: "bg-red-900/30 text-red-400",
  cancelled: "bg-yellow-900/30 text-yellow-400",
};

const LANE_STYLES: Record<string, string> = {
  foreground: "bg-blue-900/30 text-blue-400",
  background: "bg-purple-900/30 text-purple-400",
  specialist: "bg-orange-900/30 text-orange-400",
  fast: "bg-cyan-900/30 text-cyan-400",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wider ${
        STATUS_STYLES[status] || "bg-bg-tertiary text-text-muted"
      }`}
    >
      {status}
    </span>
  );
}

export function LaneBadge({ lane }: { lane: string }) {
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
        LANE_STYLES[lane] || "bg-bg-tertiary text-text-muted"
      }`}
    >
      {lane}
    </span>
  );
}

/* ── Expandable Task Row ─────────────────────────────────────── */

export function RecentTaskRow({
  task,
  expanded,
  onToggle,
}: {
  task: AgentTask;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="border-b border-border last:border-b-0">
      <div
        role="button"
        tabIndex={0}
        className="flex items-center gap-3 px-4 py-3 hover:bg-bg-hover/50 transition-colors cursor-pointer"
        onClick={onToggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}
      >
        <StatusBadge status={task.status} />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-text-primary truncate">{task.name}</p>
          {!expanded && task.result_preview && (
            <p className="text-xs text-text-muted truncate">{task.result_preview}</p>
          )}
          {!expanded && task.error && (
            <p className="text-xs text-red-400 truncate">{task.error}</p>
          )}
        </div>
        <LaneBadge lane={task.lane} />
        <span className="text-xs text-text-muted tabular-nums shrink-0">
          {task.duration_s != null ? formatElapsed(task.duration_s) : "-"}
        </span>
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`text-text-muted transition-transform shrink-0 ${expanded ? "rotate-180" : ""}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>
      {expanded && (
        <div className="px-4 pb-3 pt-1 space-y-2 animate-fade-in">
          {task.description && (
            <div>
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Task</p>
              <p className="text-xs text-text-secondary">{task.description}</p>
            </div>
          )}
          {task.result_preview && (
            <div>
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Result</p>
              <div className="text-xs text-text-secondary bg-bg-tertiary rounded-lg px-3 py-2 max-h-[200px] overflow-y-auto whitespace-pre-wrap font-mono">
                {task.result_preview}
              </div>
            </div>
          )}
          {task.error && (
            <div>
              <p className="text-[10px] text-red-400 uppercase tracking-wider mb-0.5">Error</p>
              <div className="text-xs text-red-300 bg-red-900/20 rounded-lg px-3 py-2 whitespace-pre-wrap font-mono">
                {task.error}
              </div>
            </div>
          )}
          <div className="flex gap-4 text-[11px] text-text-muted">
            {task.duration_s != null && <span>Duration: {formatElapsed(task.duration_s)}</span>}
            <span>Lane: {task.lane}</span>
            <span>
              ID: <span className="font-mono">{task.task_id.slice(0, 8)}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
