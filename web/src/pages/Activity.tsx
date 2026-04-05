import { useEffect, useState } from "react";
import * as api from "../api";
import type { AgentStatus, AgentTask } from "../api";

const POLL_INTERVAL = 3000;

/* ── Status badge ─────────────────────────────────────────────────── */

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: "bg-accent-soft text-accent",
    queued: "bg-blue-900/30 text-blue-400",
    done: "bg-green-900/30 text-green-400",
    failed: "bg-red-900/30 text-red-400",
    cancelled: "bg-yellow-900/30 text-yellow-400",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wider ${styles[status] || "bg-bg-tertiary text-text-muted"}`}>
      {status}
    </span>
  );
}

function LaneBadge({ lane }: { lane: string }) {
  const styles: Record<string, string> = {
    foreground: "bg-blue-900/30 text-blue-400",
    background: "bg-purple-900/30 text-purple-400",
    specialist: "bg-orange-900/30 text-orange-400",
    fast: "bg-cyan-900/30 text-cyan-400",
    slow: "bg-amber-900/30 text-amber-400",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${styles[lane] || "bg-bg-tertiary text-text-muted"}`}>
      {lane}
    </span>
  );
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

/* ── Active Task Card (rich) ──────────────────────────────────────── */

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

      {/* Progress bar */}
      {(task.step_count ?? 0) > 0 && task.current_step != null && (
        <div className="mb-2">
          <div className="flex items-center justify-between text-[10px] text-text-muted mb-1">
            <span>Step {task.current_step} of {task.step_count}</span>
            <span>{Math.round(((Number(task.current_step) || 0) / (task.step_count || 1)) * 100)}%</span>
          </div>
          <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all duration-500 animate-bar-fill"
              style={{ width: `${((Number(task.current_step) || 0) / (task.step_count || 1)) * 100}%` }}
            />
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 text-[11px] text-text-muted">
        {task.current_step != null && (task.step_count ?? 0) === 0 && (
          <span>Step: <span className="text-text-secondary">{task.current_step}</span></span>
        )}
      </div>
    </div>
  );
}

/* ── Recent Task Row (expandable) ─────────────────────────────────── */

function RecentTaskRow({
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
        className="flex items-center gap-3 px-4 py-3 hover:bg-bg-hover/50 transition-colors cursor-pointer"
        onClick={onToggle}
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
          width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
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
            <span>ID: <span className="font-mono">{task.task_id.slice(0, 8)}</span></span>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Quick Stats ──────────────────────────────────────────────────── */

function QuickStats({ data }: { data: AgentStatus }) {
  const active = data.active.length;
  const bg = data.background.length;
  const done = data.recent.filter((t) => t.status === "done").length;
  const failed = data.recent.filter((t) => t.status === "failed").length;

  return (
    <div className="grid grid-cols-4 gap-3 mb-6">
      <div className="px-4 py-3 rounded-xl bg-bg-secondary border border-border">
        <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Active</p>
        <p className={`text-lg font-semibold ${active > 0 ? "text-accent" : "text-text-muted"}`}>{active}</p>
      </div>
      <div className="px-4 py-3 rounded-xl bg-bg-secondary border border-border">
        <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Background</p>
        <p className={`text-lg font-semibold ${bg > 0 ? "text-purple-400" : "text-text-muted"}`}>{bg}</p>
      </div>
      <div className="px-4 py-3 rounded-xl bg-bg-secondary border border-border">
        <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Completed</p>
        <p className="text-lg font-semibold text-green-400">{done}</p>
      </div>
      <div className="px-4 py-3 rounded-xl bg-bg-secondary border border-border">
        <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Failed</p>
        <p className={`text-lg font-semibold ${failed > 0 ? "text-red-400" : "text-text-muted"}`}>{failed}</p>
      </div>
    </div>
  );
}

/* ── Empty state ──────────────────────────────────────────────────── */

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center py-8 text-text-muted text-sm">
      {message}
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────────────── */

export default function Activity() {
  const [data, setData] = useState<AgentStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedTask, setExpandedTask] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    const poll = async () => {
      try {
        const status = await api.getAgentStatus();
        if (alive) {
          setData(status);
          setError(null);
        }
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "Failed to load");
      }
    };

    poll();
    const id = setInterval(poll, POLL_INTERVAL);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const allActive = [
    ...(data?.active ?? []),
    ...(data?.background ?? []),
  ];

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="max-w-4xl mx-auto space-y-6">

        {/* Quick Stats */}
        {data && <QuickStats data={data} />}

        {/* Running Now */}
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
          {error && (
            <div className="bg-red-900/20 border border-red-800/30 rounded-lg p-3 text-sm text-red-400 mb-3">
              {error}
            </div>
          )}
          {allActive.length === 0 ? (
            <EmptyState message="No active tasks" />
          ) : (
            <div className="grid gap-3">
              {allActive.map((t) => (
                <ActiveTaskCard key={t.task_id} task={t} />
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
            {(data?.recent ?? []).length > 0 && (
              <span className="text-[10px] text-text-muted font-normal">
                (click to expand)
              </span>
            )}
          </h2>
          {(data?.recent ?? []).length === 0 ? (
            <EmptyState message="No recent activity" />
          ) : (
            <div className="bg-bg-secondary border border-border rounded-xl overflow-hidden">
              {data!.recent.map((t) => (
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

      </div>
    </div>
  );
}
