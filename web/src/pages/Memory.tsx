import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { Memory as MemoryItem, DailyLog } from "../api";
import { useToast } from "../context/ToastContext";
import { useInterval } from "../hooks/useInterval";
import { ListSkeleton } from "../components/Skeleton";

export default function Memory() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [logs, setLogs] = useState<DailyLog[]>([]);
  const [selectedLog, setSelectedLog] = useState<DailyLog | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState<string | null>(null);
  const [tab, setTab] = useState<"personal" | "daily">("personal");
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [mem, lg] = await Promise.allSettled([api.listMemories(), api.listDailyLogs()]);
      setMemories(mem.status === "fulfilled" ? (Array.isArray(mem.value) ? mem.value : []) : []);
      setLogs(lg.status === "fulfilled" ? (Array.isArray(lg.value) ? lg.value : []) : []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load memory");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);
  useInterval(load, 60_000);

  const handleDeleteMemory = async (id: string) => {
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
      toast.success("Log generated");
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to generate log");
    }
    setGenerating(null);
  };

  const handleDeleteLog = async (date: string) => {
    try {
      await api.deleteDailyLog(date);
      setLogs((prev) => prev.filter((l) => l.date !== date));
      if (selectedLog?.date === date) setSelectedLog(null);
      toast.success("Log deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete log");
    }
  };

  if (loading) return <ListSkeleton rows={5} />;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Memory</h1>
            <p className="text-sm text-text-muted">Personal facts &amp; daily logs</p>
          </div>
          <button onClick={load} className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors">
            Refresh
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 p-1 bg-bg-secondary rounded-xl border border-border w-fit">
          {(["personal", "daily"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 text-xs rounded-lg transition-colors ${tab === t ? "bg-bg-hover text-text-primary" : "text-text-muted hover:text-text-secondary"}`}
            >
              {t === "personal" ? "Personal Memories" : "Daily Logs"}
            </button>
          ))}
        </div>

        {tab === "personal" && (
          <div className="space-y-1">
            {memories.length === 0 && <p className="text-sm text-text-muted text-center py-8">No personal memories yet. The agent learns facts about you over time.</p>}
            {memories.map((m) => (
              <div key={m.id} className="flex items-start gap-3 px-4 py-3 rounded-xl bg-bg-secondary border border-border hover:border-border-light transition-colors">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-text-primary">{m.key}</p>
                  <p className="text-xs text-text-secondary mt-0.5">{m.value}</p>
                  <p className="text-[10px] text-text-muted mt-1">{new Date(m.created_at).toLocaleString()}</p>
                </div>
                <button onClick={() => handleDeleteMemory(m.id)} className="text-xs text-text-muted hover:text-error px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors shrink-0">
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}

        {tab === "daily" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Log list */}
            <div className="space-y-1">
              {logs.length === 0 && <p className="text-sm text-text-muted text-center py-8">No daily logs yet.</p>}
              {logs.map((log) => (
                <div key={log.date} className="flex items-center gap-2 px-4 py-3 rounded-xl bg-bg-secondary border border-border hover:border-border-light transition-colors">
                  <button onClick={() => handleViewLog(log.date)} className="text-sm text-text-primary hover:text-accent transition-colors flex-1 text-left font-mono">
                    {log.date}
                  </button>
                  <button
                    onClick={() => handleGenerateLog(log.date)}
                    disabled={generating === log.date}
                    className="text-[10px] text-text-muted hover:text-accent px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors disabled:opacity-50"
                  >
                    {generating === log.date ? "..." : "Regen"}
                  </button>
                  <button onClick={() => handleDeleteLog(log.date)} className="text-[10px] text-text-muted hover:text-error px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors">
                    Del
                  </button>
                </div>
              ))}
            </div>

            {/* Log detail */}
            <div className="bg-bg-secondary border border-border rounded-xl p-4 min-h-[200px]">
              {selectedLog ? (
                <>
                  <p className="text-xs font-mono text-text-muted mb-2">{selectedLog.date}</p>
                  <p className="text-sm text-text-secondary whitespace-pre-wrap leading-relaxed">{selectedLog.summary}</p>
                </>
              ) : (
                <p className="text-sm text-text-muted text-center py-12">Select a date to view the log</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
