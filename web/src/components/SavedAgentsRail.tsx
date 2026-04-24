import { useEffect, useRef, useState } from "react";
import type { Job, Watcher } from "../api";
import { listJobs, pauseJob, resumeJob, listWatchers, pauseWatcher, resumeWatcher } from "../api";
import type { Page } from "./NavShell";

/**
 * Two-column rail showing the user's saved agents:
 *   • Scheduled Jobs  (cron + one-off)
 *   • Watchers        (zero-token browser monitors)
 *
 * Reads existing endpoints only. Polls every 15s — matches the
 * AgentStatusContext cadence without hammering the server.
 */

function timeUntil(isoOrEpoch: string | number | null | undefined): { label: string; cls: string } {
  if (!isoOrEpoch) return { label: "—", cls: "" };
  const ts = typeof isoOrEpoch === "number"
    ? isoOrEpoch * 1000
    : new Date(isoOrEpoch).getTime();
  if (Number.isNaN(ts)) return { label: "—", cls: "" };
  const diff = ts - Date.now();
  const abs = Math.abs(diff);
  const suffix = diff < 0 ? " ago" : "";
  const prefix = diff >= 0 ? "in " : "";

  const mins = Math.round(abs / 60000);
  let label: string;
  if (abs < 45_000) label = diff < 0 ? "just now" : "imminent";
  else if (mins < 60) label = `${prefix}${mins}m${suffix}`;
  else if (mins < 60 * 24) label = `${prefix}${Math.round(mins / 60)}h${suffix}`;
  else label = `${prefix}${Math.round(mins / (60 * 24))}d${suffix}`;

  const cls = diff < 0
    ? "overdue"
    : diff < 60_000
      ? "due-soon"
      : "";
  return { label, cls };
}

function humanizeCron(cron: string | null): string {
  if (!cron) return "manual";
  const t = cron.trim();
  if (t === "* * * * *") return "every minute";
  if (t === "0 * * * *") return "hourly";
  if (t === "0 0 * * *") return "daily at midnight";
  if (t === "0 9 * * *") return "daily at 09:00";
  if (t === "*/5 * * * *") return "every 5m";
  if (t === "*/10 * * * *") return "every 10m";
  if (t === "*/15 * * * *") return "every 15m";
  if (t === "*/30 * * * *") return "every 30m";
  if (t === "0 */2 * * *") return "every 2h";
  if (t === "0 */6 * * *") return "every 6h";
  if (t === "0 9 * * 1-5") return "weekdays 09:00";
  return t;
}

function StatusDot({ status }: { status: string }) {
  const cls = status === "active" || status === "running"
    ? "bg-accent live-pulse"
    : status === "paused"
      ? "bg-amber"
      : status === "error" || status === "failed"
        ? "bg-error"
        : "bg-text-muted";
  return <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${cls}`} />;
}

function JobRow({ job, onChanged }: { job: Job; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const paused = job.status === "paused";
  const next = timeUntil(job.next_run);

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    try {
      if (paused) await resumeJob(job.id);
      else await pauseJob(job.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="group flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-bg-hover/40 transition-colors min-w-0">
      <StatusDot status={job.status} />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-text-primary truncate" title={job.instruction}>
          {job.name || job.instruction?.slice(0, 40) || "Unnamed job"}
        </p>
        <p className="text-[10px] text-text-muted tracking-wide">
          {humanizeCron(job.cron_expression)}
        </p>
      </div>
      <span className={`countdown text-[10px] shrink-0 ${next.cls}`} title={job.next_run ?? ""}>
        {next.label}
      </span>
      <button
        onClick={toggle}
        disabled={busy}
        className="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1 -m-1 rounded text-text-muted hover:text-accent disabled:opacity-40"
        title={paused ? "Resume" : "Pause"}
      >
        {paused ? (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3" /></svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></svg>
        )}
      </button>
    </div>
  );
}

function WatcherRow({ watcher, onChanged }: { watcher: Watcher; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const paused = watcher.status === "paused";
  const next = timeUntil(watcher.next_check_ts ?? watcher.next_run);
  const title = watcher.name || watcher.url || "Watcher";

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    try {
      if (paused) await resumeWatcher(watcher.id);
      else await pauseWatcher(watcher.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="group flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-bg-hover/40 transition-colors min-w-0">
      <StatusDot status={watcher.status} />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-text-primary truncate" title={title}>
          {watcher.template_icon ? <span className="mr-1">{watcher.template_icon}</span> : null}
          {title}
        </p>
        <p className="text-[10px] text-text-muted tracking-wide truncate" title={watcher.what_to_watch ?? ""}>
          {watcher.trigger_count} hits · {watcher.check_count} checks
        </p>
      </div>
      <span className={`countdown text-[10px] shrink-0 ${next.cls}`}>
        {next.label}
      </span>
      <button
        onClick={toggle}
        disabled={busy}
        className="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1 -m-1 rounded text-text-muted hover:text-accent disabled:opacity-40"
        title={paused ? "Resume" : "Pause"}
      >
        {paused ? (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3" /></svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></svg>
        )}
      </button>
    </div>
  );
}

function RailColumn({
  title,
  count,
  empty,
  emptyCta,
  onCta,
  children,
  onHeader,
}: {
  title: string;
  count: number;
  empty: string;
  emptyCta?: string;
  onCta?: () => void;
  children: React.ReactNode;
  onHeader?: () => void;
}) {
  return (
    <div className="rounded-xl bg-bg-secondary border border-border">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/60">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
          {title}
        </p>
        {count > 0 && (
          <span className="text-[10px] ticker px-1.5 py-0.5 rounded-full bg-bg-tertiary text-text-muted">
            {count}
          </span>
        )}
        {onHeader && (
          <button
            onClick={onHeader}
            className="ml-auto text-[10px] text-text-muted hover:text-accent transition-colors uppercase tracking-wider"
          >
            open →
          </button>
        )}
      </div>
      <div className="p-1 max-h-[280px] overflow-y-auto">
        {count > 0 ? (
          children
        ) : (
          <div className="px-3 py-6 text-center">
            <p className="text-[11px] text-text-muted">{empty}</p>
            {emptyCta && onCta && (
              <button
                onClick={onCta}
                className="mt-2 px-2.5 py-1 text-[10px] rounded-md border border-border text-text-secondary hover:border-accent hover:text-accent transition-colors"
              >
                {emptyCta}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function SavedAgentsRail({ onNavigate }: { onNavigate: (page: Page) => void }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [watchers, setWatchers] = useState<Watcher[]>([]);
  const aliveRef = useRef(true);

  const [reloadTick, setReloadTick] = useState(0);
  const triggerReload = () => setReloadTick((n) => n + 1);

  useEffect(() => {
    aliveRef.current = true;

    const load = async () => {
      try {
        const [j, w] = await Promise.allSettled([listJobs(), listWatchers()]);
        if (!aliveRef.current) return;
        if (j.status === "fulfilled") setJobs(Array.isArray(j.value) ? j.value : []);
        if (w.status === "fulfilled") setWatchers(Array.isArray(w.value) ? w.value : []);
      } catch {
        /* ignore */
      }
    };

    load();
    const id = setInterval(load, 15_000);
    return () => {
      aliveRef.current = false;
      clearInterval(id);
    };
  }, [reloadTick]);

  // Sort jobs by next_run ascending, paused last
  const sortedJobs = [...jobs].sort((a, b) => {
    if (a.status === "paused" && b.status !== "paused") return 1;
    if (b.status === "paused" && a.status !== "paused") return -1;
    const an = a.next_run ? new Date(a.next_run).getTime() : Number.MAX_SAFE_INTEGER;
    const bn = b.next_run ? new Date(b.next_run).getTime() : Number.MAX_SAFE_INTEGER;
    return an - bn;
  }).slice(0, 6);

  const sortedWatchers = [...watchers].sort((a, b) => {
    if (a.status === "paused" && b.status !== "paused") return 1;
    if (b.status === "paused" && a.status !== "paused") return -1;
    const an = a.next_check_ts ?? Number.MAX_SAFE_INTEGER;
    const bn = b.next_check_ts ?? Number.MAX_SAFE_INTEGER;
    return an - bn;
  }).slice(0, 6);

  return (
    <section>
      <div className="flex items-baseline gap-2 mb-3">
        <h2 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
          Saved agents
        </h2>
        <span className="text-[10px] text-text-muted">
          Scheduled automations + zero-token monitors
        </span>
        <button
          onClick={() => onNavigate("templates")}
          className="ml-auto text-[10px] text-text-muted hover:text-accent transition-colors uppercase tracking-wider"
        >
          browser templates →
        </button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <RailColumn
          title="Scheduled jobs"
          count={jobs.length}
          empty="No scheduled jobs yet."
          emptyCta="+ Schedule a job"
          onCta={() => onNavigate("jobs")}
          onHeader={jobs.length > 0 ? () => onNavigate("jobs") : undefined}
        >
          {sortedJobs.map((j) => (
            <JobRow key={j.id} job={j} onChanged={triggerReload} />
          ))}
          {jobs.length > sortedJobs.length && (
            <button
              onClick={() => onNavigate("jobs")}
              className="w-full text-[10px] text-text-muted hover:text-accent transition-colors uppercase tracking-wider py-2"
            >
              + {jobs.length - sortedJobs.length} more →
            </button>
          )}
        </RailColumn>
        <RailColumn
          title="Watchers"
          count={watchers.length}
          empty="No browser watchers running."
          emptyCta="+ Add watcher"
          onCta={() => onNavigate("watchers")}
          onHeader={watchers.length > 0 ? () => onNavigate("watchers") : undefined}
        >
          {sortedWatchers.map((w) => (
            <WatcherRow key={w.id} watcher={w} onChanged={triggerReload} />
          ))}
          {watchers.length > sortedWatchers.length && (
            <button
              onClick={() => onNavigate("watchers")}
              className="w-full text-[10px] text-text-muted hover:text-accent transition-colors uppercase tracking-wider py-2"
            >
              + {watchers.length - sortedWatchers.length} more →
            </button>
          )}
        </RailColumn>
      </div>
    </section>
  );
}
