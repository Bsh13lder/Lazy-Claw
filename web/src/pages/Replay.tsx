import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { TraceSummary, TraceEntry } from "../api";

function formatTime(ts: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }).format(new Date(ts));
}

function EntryTypeBadge({ type }: { type: string }) {
  const styles: Record<string, string> = {
    user_message: "bg-blue-900/30 text-blue-400",
    assistant_message: "bg-accent-soft text-accent",
    tool_call: "bg-cyan-900/30 text-cyan-400",
    tool_result: "bg-purple-900/30 text-purple-400",
    specialist: "bg-orange-900/30 text-orange-400",
    error: "bg-red-900/30 text-red-400",
    system: "bg-bg-tertiary text-text-muted",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wider ${styles[type] || "bg-bg-tertiary text-text-muted"}`}>
      {type.replace(/_/g, " ")}
    </span>
  );
}

function TraceTimeline({ entries }: { entries: TraceEntry[] }) {
  return (
    <div className="space-y-1">
      {entries.map((e) => (
        <div key={e.id} className="flex gap-3 px-4 py-2 hover:bg-bg-hover/50 transition-colors rounded-lg">
          <div className="w-16 shrink-0 text-[10px] text-text-muted tabular-nums pt-0.5">
            #{e.sequence}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <EntryTypeBadge type={e.entry_type} />
              <span className="text-[10px] text-text-muted">{formatTime(e.created_at)}</span>
            </div>
            <pre className="text-xs text-text-secondary whitespace-pre-wrap font-mono bg-bg-tertiary rounded-lg px-3 py-2 max-h-[200px] overflow-y-auto">
              {e.content}
            </pre>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Replay() {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [entries, setEntries] = useState<TraceEntry[]>([]);
  const [loadingEntries, setLoadingEntries] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listTraces(30);
      setTraces(Array.isArray(data) ? data : []);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSelect = async (traceId: string) => {
    if (selectedId === traceId) { setSelectedId(null); return; }
    setSelectedId(traceId);
    setLoadingEntries(true);
    try {
      const data = await api.getTrace(traceId);
      setEntries(Array.isArray(data) ? data : []);
    } catch { setEntries([]); }
    finally { setLoadingEntries(false); }
  };

  const handleDelete = async (traceId: string) => {
    try {
      await api.deleteTrace(traceId);
      setTraces((prev) => prev.filter((t) => t.trace_session_id !== traceId));
      if (selectedId === traceId) setSelectedId(null);
    } catch { /* ignore */ }
  };

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Session Replay</h1>
            <p className="text-sm text-text-muted">Trace agent reasoning, tool calls, and results</p>
          </div>
          <button onClick={load} className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors">
            Refresh
          </button>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-8 justify-center">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
            </svg>
            Loading traces...
          </div>
        )}

        {!loading && traces.length === 0 && (
          <div className="text-center py-16 text-text-muted text-sm">
            No session traces recorded yet
          </div>
        )}

        {!loading && traces.length > 0 && (
          <div className="space-y-2">
            {traces.map((t) => {
              const isSelected = selectedId === t.trace_session_id;
              return (
                <div key={t.trace_session_id} className={`rounded-xl bg-bg-secondary border transition-colors ${isSelected ? "border-border-light" : "border-border"}`}>
                  <div
                    className="px-4 py-3 cursor-pointer flex items-center gap-3"
                    onClick={() => handleSelect(t.trace_session_id)}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-cyan-400 shrink-0">
                      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                    </svg>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-text-primary font-mono truncate">{t.trace_session_id.slice(0, 12)}...</p>
                      <p className="text-[11px] text-text-muted">{t.entry_count} entries · {formatTime(t.created_at)}</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDelete(t.trace_session_id); }}
                      className="text-xs text-text-muted hover:text-red-400 px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors"
                    >
                      Delete
                    </button>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                      className={`text-text-muted transition-transform ${isSelected ? "rotate-180" : ""}`}>
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </div>

                  {isSelected && (
                    <div className="border-t border-border px-2 py-3">
                      {loadingEntries ? (
                        <div className="flex items-center gap-2 text-text-muted text-xs py-4 justify-center">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
                            <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                          </svg>
                          Loading timeline...
                        </div>
                      ) : entries.length === 0 ? (
                        <p className="text-xs text-text-muted text-center py-4">No entries</p>
                      ) : (
                        <TraceTimeline entries={entries} />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
