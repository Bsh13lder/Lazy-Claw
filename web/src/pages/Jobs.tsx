import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { Job } from "../api";
import Modal from "../components/Modal";
import { useToast } from "../context/ToastContext";
import type { Page } from "../components/NavShell";

/* ── Helpers ─────────────────────────────────────────────── */

function relativeTime(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;

  const diffMs = then - now;
  const absDiff = Math.abs(diffMs);
  const future = diffMs > 0;

  if (absDiff < 60_000) return "just now";

  const minutes = Math.floor(absDiff / 60_000);
  if (minutes < 60) {
    const label = `${minutes}m`;
    return future ? `in ${label}` : `${label} ago`;
  }

  const hours = Math.floor(absDiff / 3_600_000);
  if (hours < 24) {
    const label = `${hours}h`;
    return future ? `in ${label}` : `${label} ago`;
  }

  const days = Math.floor(absDiff / 86_400_000);
  if (days < 30) {
    const label = `${days}d`;
    return future ? `in ${label}` : `${label} ago`;
  }

  const months = Math.floor(days / 30);
  const label = `${months}mo`;
  return future ? `in ${label}` : `${label} ago`;
}

type StatusKey = "active" | "paused" | "completed" | "failed";

interface StatusConfig {
  readonly dot: string;
  readonly badge: string;
  readonly badgeText: string;
  readonly label: string;
}

const STATUS_MAP: Record<StatusKey, StatusConfig> = {
  active: {
    dot: "bg-cyan live-pulse",
    badge: "bg-cyan-soft text-cyan",
    badgeText: "Running",
    label: "active",
  },
  paused: {
    dot: "bg-amber",
    badge: "bg-amber-soft text-amber",
    badgeText: "Paused",
    label: "paused",
  },
  completed: {
    dot: "bg-green-400",
    badge: "bg-green-400/10 text-green-400",
    badgeText: "Completed",
    label: "completed",
  },
  failed: {
    dot: "bg-error",
    badge: "bg-error-soft text-error",
    badgeText: "Failed",
    label: "failed",
  },
};

const FALLBACK_STATUS: StatusConfig = {
  dot: "bg-text-muted",
  badge: "bg-bg-hover text-text-muted",
  badgeText: "Unknown",
  label: "unknown",
};

function getStatusConfig(status: string): StatusConfig {
  return STATUS_MAP[status as StatusKey] ?? FALLBACK_STATUS;
}

/* ── Icons ───────────────────────────────────────────────── */

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none">
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none">
      <rect x="6" y="4" width="4" height="16" rx="1" />
      <rect x="14" y="4" width="4" height="16" rx="1" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14" />
    </svg>
  );
}

function ClockIcon({ className }: { readonly className?: string }) {
  return (
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v6l4 2" />
    </svg>
  );
}

/* ── Job Card ────────────────────────────────────────────── */

interface JobCardProps {
  readonly job: Job;
  readonly onPause: (id: string) => void;
  readonly onResume: (id: string) => void;
  readonly onDelete: (id: string) => void;
}

function JobCard({ job, onPause, onResume, onDelete }: JobCardProps) {
  const cfg = getStatusConfig(job.status);

  return (
    <div className="px-4 py-4 rounded-xl bg-bg-secondary border border-border card-hover transition-colors">
      <div className="flex items-start justify-between gap-3">
        {/* Left content */}
        <div className="min-w-0 flex-1">
          {/* Status badge + name */}
          <div className="flex items-center gap-2.5 mb-1.5">
            <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium ${cfg.badge}`}>
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${cfg.dot}`} />
              {cfg.badgeText}
            </span>
            <p className="text-sm font-medium text-text-primary truncate">{job.name}</p>
          </div>

          {/* Instruction */}
          <p className="text-xs text-text-muted truncate mb-2">{job.instruction}</p>

          {/* Metadata row */}
          <div className="flex items-center gap-4 text-[11px] text-text-muted">
            {job.cron_expression && (
              <span className="font-mono bg-bg-hover px-1.5 py-0.5 rounded text-text-secondary">
                {job.cron_expression}
              </span>
            )}
            {job.last_run && (
              <span>Last: {relativeTime(job.last_run)}</span>
            )}
            {job.next_run && (
              <span>Next: {relativeTime(job.next_run)}</span>
            )}
          </div>
        </div>

        {/* Quick actions */}
        <div className="flex items-center gap-1 shrink-0">
          {job.status === "active" && (
            <button
              onClick={() => onPause(job.id)}
              title="Pause"
              className="p-1.5 rounded-lg text-text-muted hover:text-amber hover:bg-bg-hover transition-colors"
            >
              <PauseIcon />
            </button>
          )}
          {job.status === "paused" && (
            <button
              onClick={() => onResume(job.id)}
              title="Resume"
              className="p-1.5 rounded-lg text-text-muted hover:text-accent hover:bg-bg-hover transition-colors"
            >
              <PlayIcon />
            </button>
          )}
          <button
            onClick={() => onDelete(job.id)}
            title="Delete"
            className="p-1.5 rounded-lg text-text-muted hover:text-error hover:bg-bg-hover transition-colors"
          >
            <TrashIcon />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Empty State ─────────────────────────────────────────── */

function EmptyState({ onNavigate }: { readonly onNavigate?: (page: Page) => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <ClockIcon className="text-text-muted mb-4 opacity-40" />
      <h2 className="text-sm font-medium text-text-secondary mb-1">No jobs scheduled</h2>
      <p className="text-xs text-text-muted max-w-xs mb-3">
        Create cron or one-off jobs to automate tasks.
      </p>
      {onNavigate && (
        <p className="text-[11px] text-text-muted">
          Monitoring a page for changes?{" "}
          <button
            onClick={() => onNavigate("watchers")}
            className="text-accent hover:underline"
          >
            Use the Watchers tab instead →
          </button>
        </p>
      )}
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────── */

interface JobsProps {
  readonly onNavigate?: (page: Page) => void;
}

export default function Jobs({ onNavigate }: JobsProps = {}) {
  const toast = useToast();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  // Create form
  const [cName, setCName] = useState("");
  const [cInstruction, setCInstruction] = useState("");
  const [cType, setCType] = useState<"cron" | "one_off">("cron");
  const [cCron, setCCron] = useState("");
  const [cContext, setCContext] = useState("");
  const [saving, setSaving] = useState(false);

  // AI draft
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listJobs();
      // Watchers live on their own page — never show them here.
      const rows = Array.isArray(data) ? data : [];
      setJobs(rows.filter((j) => (j as Job & { job_type?: string }).job_type !== "watcher"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load jobs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

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
    try {
      await api.pauseJob(id);
      toast.success("Job paused");
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to pause job");
    }
  };
  const handleResume = async (id: string) => {
    try {
      await api.resumeJob(id);
      toast.success("Job resumed");
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to resume job");
    }
  };
  const handleDelete = async (id: string) => {
    if (!window.confirm("Delete this job?")) return;
    try {
      await api.deleteJob(id);
      toast.success("Job deleted");
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete job");
    }
  };

  const runAiDraft = async () => {
    const prompt = aiPrompt.trim();
    if (!prompt) return;
    setAiBusy(true);
    setAiError(null);
    try {
      const draft = await api.createJobFromPrompt(prompt);
      setCName(draft.name || "");
      setCInstruction(draft.instruction || "");
      setCType(draft.job_type === "one_off" ? "one_off" : "cron");
      setCCron(draft.cron_expression || "");
      setCContext(draft.context || "");
      setAiOpen(false);
      setAiPrompt("");
      setShowCreate(true);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : "Failed to draft job");
    } finally {
      setAiBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8 animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Jobs</h1>
            <p className="text-sm text-text-muted">
              {jobs.length} scheduled {jobs.length === 1 ? "job" : "jobs"} · cron & one-off
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => { setAiError(null); setAiOpen((o) => !o); }}
              className="text-xs text-accent hover:text-accent-dim px-3 py-1.5 rounded-lg border border-accent/30 hover:bg-accent-soft transition-colors"
              title="Describe the job in plain English — LazyClaw drafts it"
            >
              ✨ Create with AI
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors"
            >
              + Create job
            </button>
            <button
              onClick={load}
              className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors"
            >
              Refresh
            </button>
          </div>
        </div>

        {aiOpen && (
          <div className="mb-4 border border-accent/30 bg-accent-soft rounded-xl p-3 flex flex-col gap-2">
            <div className="text-xs font-medium text-text-primary">Describe the job</div>
            <div className="text-[11px] text-text-muted">
              Plain English. LazyClaw drafts the cron expression + instruction; you review before saving.
            </div>
            <textarea
              rows={2}
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && aiPrompt.trim() && !aiBusy) {
                  e.preventDefault();
                  void runAiDraft();
                }
              }}
              placeholder="e.g. remind me every Monday at 9am to review sales pipeline"
              className="w-full px-3 py-2 rounded-lg bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light resize-y"
              autoFocus
            />
            {aiError && <div className="text-[11px] text-error">{aiError}</div>}
            <div className="flex justify-end gap-1.5">
              <button
                onClick={() => { setAiOpen(false); setAiPrompt(""); setAiError(null); }}
                disabled={aiBusy}
                className="text-xs px-2 py-1 rounded text-text-muted hover:text-text-primary"
              >
                Cancel
              </button>
              <button
                onClick={runAiDraft}
                disabled={aiBusy || !aiPrompt.trim()}
                className="text-xs px-3 py-1 rounded bg-accent text-bg-primary font-medium disabled:opacity-40"
              >
                {aiBusy ? "Drafting…" : "Draft job (⌘+Enter)"}
              </button>
            </div>
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-8 justify-center">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
            </svg>
            Loading jobs...
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="px-4 py-3 rounded-xl bg-error-soft border border-error/15 text-error text-sm mb-4">
            {error}
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && jobs.length === 0 && <EmptyState onNavigate={onNavigate} />}

        {/* Job list */}
        {!loading && !error && jobs.length > 0 && (
          <div className="space-y-2">
            {jobs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                onPause={handlePause}
                onResume={handleResume}
                onDelete={handleDelete}
              />
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
