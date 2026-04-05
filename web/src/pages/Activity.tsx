import { useEffect, useState } from "react";
import * as api from "../api";
import type { AgentStatus, AgentTask } from "../api";

const POLL_INTERVAL = 3000;

/* ── Status badge ─────────────────────────────────────────────────── */

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: "bg-accent-soft text-accent",
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
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${styles[lane] || "bg-bg-tertiary text-text-muted"}`}>
      {lane}
    </span>
  );
}

/* ── Task card ────────────────────────────────────────────────────── */

function ActiveTaskCard({ task }: { task: AgentTask }) {
  return (
    <div className="bg-bg-secondary border border-border rounded-xl p-4 animate-fade-in">
      <div className="flex items-center gap-2 mb-2">
        <span className="inline-block w-2 h-2 rounded-full bg-accent live-pulse" />
        <span className="text-sm font-medium text-text-primary">{task.name}</span>
        <LaneBadge lane={task.lane} />
        <span className="ml-auto text-xs text-text-muted tabular-nums">{task.elapsed_s}s</span>
      </div>
      {task.description && (
        <p className="text-xs text-text-secondary mb-2 line-clamp-2">{task.description}</p>
      )}
      <div className="flex items-center gap-3 text-[11px] text-text-muted">
        {task.current_step && (
          <span>Step: <span className="text-text-secondary">{task.current_step}</span></span>
        )}
        {(task.step_count ?? 0) > 0 && (
          <span className="tabular-nums">{task.step_count} steps</span>
        )}
      </div>
    </div>
  );
}

function RecentTaskRow({ task }: { task: AgentTask }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-border last:border-b-0 hover:bg-bg-hover/50 transition-colors">
      <StatusBadge status={task.status} />
      <div className="min-w-0 flex-1">
        <p className="text-sm text-text-primary truncate">{task.name}</p>
        {task.result_preview && (
          <p className="text-xs text-text-muted truncate">{task.result_preview}</p>
        )}
        {task.error && (
          <p className="text-xs text-red-400 truncate">{task.error}</p>
        )}
      </div>
      <LaneBadge lane={task.lane} />
      <span className="text-xs text-text-muted tabular-nums shrink-0">
        {task.duration_s != null ? `${task.duration_s}s` : "-"}
      </span>
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
          </h2>
          {(data?.recent ?? []).length === 0 ? (
            <EmptyState message="No recent activity" />
          ) : (
            <div className="bg-bg-secondary border border-border rounded-xl overflow-hidden">
              {data!.recent.map((t) => (
                <RecentTaskRow key={t.task_id} task={t} />
              ))}
            </div>
          )}
        </section>

      </div>
    </div>
  );
}
