import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { Memory as MemoryItem, DailyLog } from "../api";
import { useToast } from "../context/ToastContext";

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

function borderColorForKey(key: string): string {
  const k = key.toLowerCase();
  if (k.includes("preference") || k.includes("like")) return "border-l-green-500";
  if (k.includes("name") || k.includes("identity")) return "border-l-cyan-400";
  return "border-l-border";
}

/* ------------------------------------------------------------------ */
/*  Icons                                                             */
/* ------------------------------------------------------------------ */

function BrainIcon({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7Z" />
      <path d="M9 21h6" />
      <path d="M10 17v4" />
      <path d="M14 17v4" />
      <path d="M9 10h.01" />
      <path d="M15 10h.01" />
      <path d="M12 14a2 2 0 0 0 2-2 2 2 0 0 0-2-2 2 2 0 0 0-2 2 2 2 0 0 0 2 2Z" />
    </svg>
  );
}

function CalendarIcon({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4" />
      <path d="M8 2v4" />
      <path d="M3 10h18" />
    </svg>
  );
}

function TrashIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

function RefreshIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 0 1-15.36 6.36" />
      <path d="M3 12a9 9 0 0 1 15.36-6.36" />
      <polyline points="21 3 21 9 15 9" />
      <polyline points="3 21 3 15 9 15" />
    </svg>
  );
}

function SearchIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <path d="M21 21l-4.35-4.35" />
    </svg>
  );
}

function SpinnerIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={`${className} animate-spin`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

export default function Memory() {
  const toast = useToast();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [logs, setLogs] = useState<DailyLog[]>([]);
  const [selectedLog, setSelectedLog] = useState<DailyLog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState<string | null>(null);
  const [tab, setTab] = useState<"personal" | "daily">("personal");
  const [search, setSearch] = useState("");

  /* -- data loading ------------------------------------------------ */

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [mem, lg] = await Promise.allSettled([
        api.listMemories(),
        api.listDailyLogs(),
      ]);
      setMemories(
        mem.status === "fulfilled" ? (Array.isArray(mem.value) ? mem.value : []) : [],
      );
      setLogs(
        lg.status === "fulfilled" ? (Array.isArray(lg.value) ? lg.value : []) : [],
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  /* -- filtered memories ------------------------------------------- */

  const filteredMemories = useMemo(() => {
    if (!search.trim()) return memories;
    const q = search.toLowerCase();
    return memories.filter(
      (m) =>
        m.key.toLowerCase().includes(q) || m.value.toLowerCase().includes(q),
    );
  }, [memories, search]);

  /* -- handlers ---------------------------------------------------- */

  const handleDeleteMemory = async (id: string) => {
    if (!window.confirm("Delete this memory?")) return;
    try {
      await api.deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
      toast.success("Memory deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete memory");
    }
  };

  const handleViewLog = async (date: string) => {
    try {
      const log = await api.getDailyLog(date);
      setSelectedLog(log);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load log");
    }
  };

  const handleGenerateLog = async (date: string) => {
    setGenerating(date);
    try {
      const result = await api.generateDailyLog(date);
      setSelectedLog({ date, summary: result.summary });
      toast.success("Daily log generated");
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to generate log");
    }
    setGenerating(null);
  };

  const handleDeleteLog = async (date: string) => {
    if (!window.confirm(`Delete log for ${date}?`)) return;
    try {
      await api.deleteDailyLog(date);
      setLogs((prev) => prev.filter((l) => l.date !== date));
      if (selectedLog?.date === date) setSelectedLog(null);
      toast.success("Log deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete log");
    }
  };

  /* -- render ------------------------------------------------------ */

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8 animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
              <BrainIcon className="w-5 h-5 text-accent" />
              Memory
            </h1>
            <p className="text-sm text-text-muted mt-0.5">
              Personal facts & daily logs
            </p>
          </div>
          <button
            onClick={load}
            className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors"
          >
            <RefreshIcon className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 p-1 bg-bg-secondary rounded-xl border border-border w-fit">
          <button
            onClick={() => setTab("personal")}
            className={`relative flex items-center gap-1.5 px-4 py-1.5 text-xs rounded-lg transition-colors ${
              tab === "personal"
                ? "bg-bg-hover text-text-primary"
                : "text-text-muted hover:text-text-secondary"
            }`}
          >
            <BrainIcon className="w-3.5 h-3.5" />
            Personal Memories
            {tab === "personal" && (
              <span className="absolute bottom-0 left-3 right-3 h-0.5 bg-accent rounded-full" />
            )}
          </button>
          <button
            onClick={() => setTab("daily")}
            className={`relative flex items-center gap-1.5 px-4 py-1.5 text-xs rounded-lg transition-colors ${
              tab === "daily"
                ? "bg-bg-hover text-text-primary"
                : "text-text-muted hover:text-text-secondary"
            }`}
          >
            <CalendarIcon className="w-3.5 h-3.5" />
            Daily Logs
            {tab === "daily" && (
              <span className="absolute bottom-0 left-3 right-3 h-0.5 bg-accent rounded-full" />
            )}
          </button>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-12 justify-center">
            <SpinnerIcon />
            Loading...
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="px-4 py-3 rounded-xl bg-error-soft border border-error/15 text-error text-sm mb-4">
            {error}
          </div>
        )}

        {/* ====== Personal Memories Tab ====== */}
        {!loading && !error && tab === "personal" && (
          <div className="space-y-4">
            {/* Search bar */}
            <div className="relative">
              <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search memories..."
                className="w-full pl-9 pr-4 py-2 text-sm bg-bg-secondary border border-border rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/40 transition-colors"
              />
            </div>

            {/* Empty state */}
            {memories.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="w-12 h-12 rounded-2xl bg-accent-soft flex items-center justify-center mb-4">
                  <BrainIcon className="w-6 h-6 text-accent" />
                </div>
                <p className="text-sm font-medium text-text-primary mb-1">
                  No memories yet
                </p>
                <p className="text-xs text-text-muted max-w-xs">
                  The agent learns about you over time as you interact with it
                </p>
              </div>
            )}

            {/* No search results */}
            {memories.length > 0 && filteredMemories.length === 0 && (
              <p className="text-sm text-text-muted text-center py-8">
                No memories matching &ldquo;{search}&rdquo;
              </p>
            )}

            {/* Memory cards */}
            <div className="space-y-2">
              {filteredMemories.map((m) => (
                <div
                  key={m.id}
                  className={`group card-hover flex items-start gap-3 px-4 py-3 rounded-xl bg-bg-secondary border border-border border-l-[3px] ${borderColorForKey(m.key)} transition-colors`}
                >
                  {/* Icon */}
                  <div className="mt-0.5 shrink-0 w-8 h-8 rounded-lg bg-accent-soft flex items-center justify-center">
                    <BrainIcon className="w-4 h-4 text-accent" />
                  </div>

                  {/* Content */}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-text-primary">
                      {m.key}
                    </p>
                    <p className="text-xs text-text-secondary mt-0.5 leading-relaxed">
                      {m.value}
                    </p>
                    <p className="text-[10px] text-text-muted mt-1.5">
                      {relativeTime(m.created_at)}
                    </p>
                  </div>

                  {/* Delete (hover only) */}
                  <button
                    onClick={() => handleDeleteMemory(m.id)}
                    className="shrink-0 opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-text-muted hover:text-error hover:bg-bg-hover transition-all"
                    title="Delete memory"
                  >
                    <TrashIcon className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ====== Daily Logs Tab ====== */}
        {!loading && !error && tab === "daily" && (
          <>
            {logs.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="w-12 h-12 rounded-2xl bg-cyan-soft flex items-center justify-center mb-4">
                  <CalendarIcon className="w-6 h-6 text-cyan" />
                </div>
                <p className="text-sm font-medium text-text-primary mb-1">
                  No daily logs yet
                </p>
                <p className="text-xs text-text-muted max-w-xs">
                  Daily logs are generated from your conversation history
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-[280px_1fr] gap-4">
                {/* Date list */}
                <div className="space-y-1 max-h-[600px] overflow-y-auto">
                  {logs.map((log) => {
                    const isSelected = selectedLog?.date === log.date;
                    const hasSummary = Boolean(log.summary);
                    return (
                      <button
                        key={log.date}
                        onClick={() => handleViewLog(log.date)}
                        className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-left transition-colors ${
                          isSelected
                            ? "bg-bg-hover border border-accent/20"
                            : "border border-transparent hover:bg-bg-hover"
                        }`}
                      >
                        {/* Status dot */}
                        <span
                          className={`shrink-0 w-2 h-2 rounded-full ${
                            hasSummary ? "bg-green-500" : "bg-border"
                          }`}
                        />
                        <span
                          className={`text-sm font-mono flex-1 ${
                            isSelected
                              ? "text-text-primary"
                              : "text-text-secondary"
                          }`}
                        >
                          {log.date}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {/* Detail panel */}
                <div className="bg-bg-secondary border border-border rounded-xl p-5 min-h-[300px]">
                  {selectedLog ? (
                    <div className="animate-fade-in">
                      {/* Header with actions */}
                      <div className="flex items-center justify-between mb-4">
                        <p className="text-xs font-mono text-text-muted">
                          {selectedLog.date}
                        </p>
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() =>
                              handleGenerateLog(selectedLog.date)
                            }
                            disabled={generating === selectedLog.date}
                            className="flex items-center gap-1 text-[10px] text-text-muted hover:text-accent px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors disabled:opacity-50"
                            title="Regenerate log"
                          >
                            {generating === selectedLog.date ? (
                              <SpinnerIcon className="w-3 h-3" />
                            ) : (
                              <RefreshIcon className="w-3 h-3" />
                            )}
                          </button>
                          <button
                            onClick={() =>
                              handleDeleteLog(selectedLog.date)
                            }
                            className="flex items-center gap-1 text-[10px] text-text-muted hover:text-error px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors"
                            title="Delete log"
                          >
                            <TrashIcon className="w-3 h-3" />
                          </button>
                        </div>
                      </div>

                      {/* Summary content */}
                      <div className="text-sm text-text-secondary whitespace-pre-wrap leading-relaxed">
                        {selectedLog.summary}
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full py-12 text-center">
                      <CalendarIcon className="w-8 h-8 text-text-muted mb-3 opacity-40" />
                      <p className="text-sm text-text-muted">
                        Select a date to view the log
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
