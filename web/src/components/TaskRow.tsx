/**
 * Shared task display components used by both Dashboard (Overview) and Activity pages.
 */

import { useState } from "react";
import type { AgentTask } from "../api";

/* ── Helpers ─────────────────────────────────────────────────── */

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const onClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  };
  return (
    <button
      onClick={onClick}
      className="text-[10px] text-text-muted hover:text-accent transition-colors inline-flex items-center gap-1"
      title={`Copy ${label}`}
    >
      {copied ? (
        <>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          copied
        </>
      ) : (
        <>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <rect x="9" y="9" width="13" height="13" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
          copy
        </>
      )}
    </button>
  );
}

/* ── Badges ──────────────────────────────────────────────────── */

const STATUS_STYLES: Record<string, string> = {
  running: "bg-accent-soft text-accent",
  queued: "bg-blue-900/30 text-blue-400",
  done: "bg-green-900/30 text-green-400",
  failed: "bg-red-900/30 text-red-400",
  cancelled: "bg-yellow-900/30 text-yellow-400",
  cancelling: "bg-yellow-900/30 text-yellow-400",
};

const LANE_STYLES: Record<string, string> = {
  foreground: "bg-blue-900/30 text-blue-400",
  background: "bg-purple-900/30 text-purple-400",
  specialist: "bg-orange-900/30 text-orange-400",
  fast: "bg-cyan-900/30 text-cyan-400",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wider ${
        STATUS_STYLES[status] || "bg-bg-tertiary text-text-muted"
      }`}
    >
      {status}
    </span>
  );
}

export function LaneBadge({ lane }: { lane: string }) {
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
        LANE_STYLES[lane] || "bg-bg-tertiary text-text-muted"
      }`}
    >
      {lane}
    </span>
  );
}

/* ── Expandable Task Row ─────────────────────────────────────── */

export function RecentTaskRow({
  task,
  expanded,
  onToggle,
  onCancel,
}: {
  task: AgentTask;
  expanded: boolean;
  onToggle: () => void;
  onCancel?: () => void;
}) {
  const fullRequest = task.instruction || task.description || "";
  const fullResult = task.result || task.result_preview || "";

  return (
    <div className="border-b border-border last:border-b-0">
      <div
        role="button"
        tabIndex={0}
        className="flex items-center gap-3 px-4 py-3 hover:bg-bg-hover/50 transition-colors cursor-pointer"
        onClick={onToggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}
      >
        <StatusBadge status={task.status} />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-text-primary truncate">{task.name}</p>
          {!expanded && fullRequest && (
            <p className="text-xs text-text-muted truncate">
              <span className="text-text-muted/60">›</span> {fullRequest}
            </p>
          )}
          {!expanded && task.result_preview && !fullRequest && (
            <p className="text-xs text-text-muted truncate">{task.result_preview}</p>
          )}
          {!expanded && task.error && (
            <p className="text-xs text-red-400 truncate">{task.error}</p>
          )}
        </div>
        <LaneBadge lane={task.lane} />
        {onCancel && task.status === "running" && (
          <button
            onClick={(e) => { e.stopPropagation(); onCancel(); }}
            className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-red-900/30 text-red-400 hover:bg-red-900/50 transition-colors"
            title="Cancel this task"
          >
            cancel
          </button>
        )}
        <span className="text-xs text-text-muted tabular-nums shrink-0">
          {task.duration_s != null ? formatElapsed(task.duration_s) : task.elapsed_s != null ? formatElapsed(task.elapsed_s) : "-"}
        </span>
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`text-text-muted transition-transform shrink-0 ${expanded ? "rotate-180" : ""}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>
      {expanded && (
        <div className="px-4 pb-3 pt-1 space-y-2.5 animate-fade-in">
          {fullRequest && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <p className="text-[10px] text-text-muted uppercase tracking-wider">Request</p>
                <CopyButton text={fullRequest} label="request" />
              </div>
              <div className="text-xs text-text-secondary bg-bg-tertiary rounded-lg px-3 py-2 max-h-[160px] overflow-y-auto whitespace-pre-wrap">
                {fullRequest}
              </div>
            </div>
          )}
          {fullResult && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <p className="text-[10px] text-text-muted uppercase tracking-wider">
                  Result {task.result && task.result_preview && task.result !== task.result_preview ? "(full)" : ""}
                </p>
                <CopyButton text={fullResult} label="result" />
              </div>
              <div className="text-xs text-text-secondary bg-bg-tertiary rounded-lg px-3 py-2 max-h-[320px] overflow-y-auto whitespace-pre-wrap font-mono">
                {fullResult}
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
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
            {task.duration_s != null && <span>Duration: {formatElapsed(task.duration_s)}</span>}
            {task.elapsed_s != null && task.duration_s == null && <span>Elapsed: {formatElapsed(task.elapsed_s)}</span>}
            <span>Lane: {task.lane}</span>
            {task.phase && <span>Phase: {task.phase}</span>}
            {task.current_tool && <span>Tool: {task.current_tool}</span>}
            {task.step_count != null && task.step_count > 0 && <span>Steps: {task.step_count}</span>}
            <span>
              ID: <span className="font-mono">{task.task_id.slice(0, 8)}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
