import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { Job } from "../api";
import Modal from "../components/Modal";
import { useToast } from "../context/ToastContext";
import { useInterval } from "../hooks/useInterval";
import { ListSkeleton } from "../components/Skeleton";

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const toast = useToast();

  // Create form
  const [cName, setCName] = useState("");
  const [cInstruction, setCInstruction] = useState("");
  const [cType, setCType] = useState<"cron" | "one_off">("cron");
  const [cCron, setCCron] = useState("");
  const [cContext, setCContext] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.listJobs();
      setJobs(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load jobs");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);
  useInterval(load, 60_000);

  const handleCreate = async () => {
    if (!cName.trim() || !cInstruction.trim()) return;
    if (cType === "cron" && !cCron.trim()) return;
    setSaving(true);
    try {
      await api.createJob({
        name: cName.trim(),
        instruction: cInstruction.trim(),
        job_type: cType,
        cron_expression: cType === "cron" ? cCron.trim() : undefined,
        context: cContext.trim() || undefined,
      });
      setShowCreate(false);
      setCName(""); setCInstruction(""); setCCron(""); setCContext("");
      toast.success("Job created");
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create job");
    } finally {
      setSaving(false);
    }
  };

  const handlePause = async (id: string) => {
    try { await api.pauseJob(id); toast.success("Job paused"); load(); } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to pause job");
    }
  };

  const handleResume = async (id: string) => {
    try { await api.resumeJob(id); toast.success("Job resumed"); load(); } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to resume job");
    }
  };

  const handleDelete = async (id: string) => {
    try { await api.deleteJob(id); toast.success("Job deleted"); load(); } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete job");
    }
  };

  const statusColor = (s: string) => {
    if (s === "active") return "bg-accent";
    if (s === "paused") return "bg-yellow-500";
    if (s === "completed") return "bg-cyan";
    return "bg-text-muted";
  };

  if (loading) return <ListSkeleton rows={4} />;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Jobs</h1>
            <p className="text-sm text-text-muted">{jobs.length} total</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setShowCreate(true)} className="text-xs text-accent hover:text-accent-dim px-3 py-1.5 rounded-lg border border-accent/30 hover:bg-accent-soft transition-colors">
              + Create job
            </button>
            <button onClick={load} className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors">
              Refresh
            </button>
          </div>
        </div>

        {jobs.length === 0 && (
          <div className="text-center py-12 text-text-muted text-sm">No jobs yet. Create cron or one-off jobs.</div>
        )}

        {jobs.length > 0 && (
          <div className="space-y-2">
            {jobs.map((job) => (
              <div key={job.id} className="px-4 py-4 rounded-xl bg-bg-secondary border border-border hover:border-border-light transition-colors">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`w-2 h-2 rounded-full shrink-0 ${statusColor(job.status)}`} />
                      <p className="text-sm font-medium text-text-primary truncate">{job.name}</p>
                    </div>
                    <p className="text-xs text-text-muted truncate">{job.instruction}</p>
                    <div className="flex items-center gap-4 mt-2 text-[11px] text-text-muted">
                      {job.cron_expression && <span className="font-mono">cron: {job.cron_expression}</span>}
                      <span>status: {job.status}</span>
                      {job.last_run && <span>last: {new Date(job.last_run).toLocaleString()}</span>}
                      {job.next_run && <span>next: {new Date(job.next_run).toLocaleString()}</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {job.status === "active" && (
                      <button onClick={() => handlePause(job.id)} className="text-xs text-text-muted hover:text-yellow-400 px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors">Pause</button>
                    )}
                    {job.status === "paused" && (
                      <button onClick={() => handleResume(job.id)} className="text-xs text-text-muted hover:text-accent px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors">Resume</button>
                    )}
                    <button onClick={() => handleDelete(job.id)} className="text-xs text-text-muted hover:text-error px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors">Delete</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Create job modal */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Create Job">
        <div className="space-y-3">
          <div className="flex gap-2">
            {(["cron", "one_off"] as const).map((t) => (
              <button key={t} onClick={() => setCType(t)} className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${cType === t ? "border-accent bg-accent-soft text-accent" : "border-border text-text-muted hover:bg-bg-hover"}`}>
                {t === "cron" ? "Cron" : "One-off"}
              </button>
            ))}
          </div>
          <input type="text" value={cName} onChange={(e) => setCName(e.target.value)} placeholder="Job name" className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light" />
          <textarea value={cInstruction} onChange={(e) => setCInstruction(e.target.value)} placeholder="Instruction for the agent..." rows={3} className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light resize-y" />
          {cType === "cron" && (
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1.5">Cron expression</label>
              <input type="text" value={cCron} onChange={(e) => setCCron(e.target.value)} placeholder="0 9 * * *" className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light" />
              <p className="text-[10px] text-text-muted mt-1">min hour day month weekday (e.g. "0 9 * * *" = daily 9am)</p>
            </div>
          )}
          <textarea value={cContext} onChange={(e) => setCContext(e.target.value)} placeholder="Extra context (optional)" rows={2} className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light resize-y" />
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors">Cancel</button>
            <button onClick={handleCreate} disabled={saving || !cName.trim() || !cInstruction.trim() || (cType === "cron" && !cCron.trim())} className="px-4 py-2 text-sm bg-accent text-bg-primary rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity">
              {saving ? "Creating..." : "Create"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
