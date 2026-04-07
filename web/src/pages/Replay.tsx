import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { TraceSummary, TraceEntry, ShareInfo } from "../api";

function formatTime(ts: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }).format(new Date(ts));
}

function formatDuration(startTs: string, endTs: string): string {
  const ms = new Date(endTs).getTime() - new Date(startTs).getTime();
  if (Number.isNaN(ms) || ms < 0) return "";
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = secs % 60;
  return `${mins}m ${remSecs}s`;
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

function MetadataBlock({ metadata }: { metadata: Record<string, unknown> }) {
  return (
    <details className="mt-1.5">
      <summary className="text-[10px] text-text-muted cursor-pointer hover:text-text-secondary">
        metadata
      </summary>
      <pre className="text-[10px] text-text-muted whitespace-pre-wrap font-mono bg-bg-hover rounded-lg px-3 py-2 mt-1 max-h-[150px] overflow-y-auto">
        {JSON.stringify(metadata, null, 2)}
      </pre>
    </details>
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
            {e.metadata && Object.keys(e.metadata).length > 0 && (
              <MetadataBlock metadata={e.metadata} />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ShareModal({
  url,
  onClose,
}: {
  readonly url: string;
  readonly onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-bg-secondary border border-border rounded-xl p-5 max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-text-primary mb-3">Share Link Created</h3>
        <div className="flex gap-2 mb-4">
          <input
            type="text"
            readOnly
            value={url}
            className="flex-1 px-3 py-2 text-xs font-mono bg-bg-tertiary border border-border rounded-lg text-text-primary"
          />
          <button
            onClick={handleCopy}
            className="px-3 py-2 text-xs text-accent border border-accent/30 rounded-lg hover:bg-accent-soft transition-colors shrink-0"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
        <div className="flex justify-end">
          <button onClick={onClose} className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Replay() {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [entries, setEntries] = useState<TraceEntry[]>([]);
  const [loadingEntries, setLoadingEntries] = useState(false);

  // Share state
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [shares, setShares] = useState<ShareInfo[]>([]);
  const [sharingId, setSharingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [traceData, shareData] = await Promise.allSettled([
        api.listTraces(30),
        api.listShares(),
      ]);
      setTraces(traceData.status === "fulfilled" && Array.isArray(traceData.value) ? traceData.value : []);
      setShares(shareData.status === "fulfilled" && Array.isArray(shareData.value) ? shareData.value : []);
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

  const handleShare = async (traceId: string) => {
    setSharingId(traceId);
    try {
      const result = await api.shareTrace(traceId);
      setShareUrl(result.url);
      // Refresh shares list
      const updated = await api.listShares();
      setShares(Array.isArray(updated) ? updated : []);
    } catch { /* ignore */ }
    finally { setSharingId(null); }
  };

  const handleRevokeShare = async (shareId: string) => {
    try {
      await api.deleteShare(shareId);
      setShares((prev) => prev.filter((s) => s.id !== shareId));
    } catch { /* ignore */ }
  };

  // Compute duration and entry_types for trace list
  const getTraceDuration = (t: TraceSummary): string => {
    const started = t.started_at as string | undefined;
    const ended = t.ended_at as string | undefined;
    if (started && ended) return formatDuration(started, ended);
    return "";
  };

  const getEntryTypes = (t: TraceSummary): string[] => {
    const types = t.entry_types as string[] | undefined;
    return Array.isArray(types) ? types : [];
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
              const duration = getTraceDuration(t);
              const entryTypes = getEntryTypes(t);
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
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[11px] text-text-muted">
                          {t.entry_count} entries · {formatTime(t.created_at)}
                          {duration && ` · ${duration}`}
                        </span>
                        {entryTypes.map((et) => (
                          <EntryTypeBadge key={et} type={et} />
                        ))}
                      </div>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleShare(t.trace_session_id); }}
                      disabled={sharingId === t.trace_session_id}
                      className="text-xs text-text-muted hover:text-accent px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors"
                    >
                      {sharingId === t.trace_session_id ? "..." : "Share"}
                    </button>
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

        {/* Shared Links */}
        {!loading && shares.length > 0 && (
          <div className="mt-8">
            <h2 className="text-sm font-semibold text-text-primary mb-3">Shared Links</h2>
            <div className="bg-bg-secondary border border-border rounded-xl overflow-hidden">
              {shares.map((s) => (
                <div key={s.id} className="flex items-center gap-3 px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-bg-hover/50 transition-colors">
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-text-primary font-mono truncate">{s.trace_session_id.slice(0, 12)}...</p>
                    <p className="text-[10px] text-text-muted">
                      Token: {s.share_token.slice(0, 8)}... · Expires: {formatTime(s.expires_at)}
                    </p>
                  </div>
                  <button
                    onClick={() => handleRevokeShare(s.id)}
                    className="text-xs text-red-400 border border-red-400/30 px-2 py-1 rounded-lg hover:bg-red-400/10 transition-colors"
                  >
                    Revoke
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Share URL Modal */}
      {shareUrl && (
        <ShareModal url={shareUrl} onClose={() => setShareUrl(null)} />
      )}
    </div>
  );
}
