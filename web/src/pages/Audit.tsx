import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { AuditEntry } from "../api";

function formatTime(ts: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit",
  }).format(new Date(ts));
}

function ActionBadge({ action }: { action: string }) {
  const styles: Record<string, string> = {
    execute: "bg-accent-soft text-accent",
    denied: "bg-red-900/30 text-red-400",
    approved: "bg-green-900/30 text-green-400",
    approval_requested: "bg-amber-900/30 text-amber-400",
    expired: "bg-bg-tertiary text-text-muted",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wider ${styles[action] || "bg-bg-tertiary text-text-muted"}`}>
      {action}
    </span>
  );
}

export default function Audit() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [limit, setLimit] = useState(50);
  const [actionFilter, setActionFilter] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const opts: { action?: string; limit?: number } = { limit };
      if (actionFilter) opts.action = actionFilter;
      const result = await api.getAuditLog(opts);
      setEntries(result.entries);
      setCount(result.count);
    } catch { setEntries([]); }
    finally { setLoading(false); }
  }, [limit, actionFilter]);

  useEffect(() => { load(); }, [load]);

  const actions = ["", "execute", "denied", "approved", "approval_requested"];

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Audit Log</h1>
            <p className="text-sm text-text-muted">{count} entries recorded</p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              className="text-xs bg-bg-tertiary border border-border rounded-lg px-3 py-1.5 text-text-primary"
            >
              {actions.map((a) => (
                <option key={a} value={a}>{a || "All actions"}</option>
              ))}
            </select>
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="text-xs bg-bg-tertiary border border-border rounded-lg px-3 py-1.5 text-text-primary"
            >
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
            <button onClick={load} className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors">
              Refresh
            </button>
          </div>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-8 justify-center">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
            </svg>
            Loading audit log...
          </div>
        )}

        {!loading && entries.length === 0 && (
          <div className="text-center py-16 text-text-muted text-sm">
            No audit entries{actionFilter ? ` for action "${actionFilter}"` : ""}
          </div>
        )}

        {!loading && entries.length > 0 && (
          <div className="bg-bg-secondary border border-border rounded-xl overflow-hidden">
            {/* Header */}
            <div className="grid grid-cols-[100px_1fr_1fr_80px_140px] gap-3 px-4 py-2 border-b border-border text-[10px] text-text-muted uppercase tracking-wider">
              <span>Action</span>
              <span>Skill</span>
              <span>Result</span>
              <span>Source</span>
              <span>Time</span>
            </div>
            {entries.map((e) => (
              <div key={e.id} className="grid grid-cols-[100px_1fr_1fr_80px_140px] gap-3 px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-bg-hover/50 transition-colors items-center">
                <ActionBadge action={e.action} />
                <span className="text-xs text-text-primary font-mono truncate">{e.skill_name || "-"}</span>
                <span className="text-xs text-text-muted truncate">{e.result_summary || "-"}</span>
                <span className="text-[10px] text-text-muted">{e.source}</span>
                <span className="text-[10px] text-text-muted tabular-nums">{formatTime(e.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
