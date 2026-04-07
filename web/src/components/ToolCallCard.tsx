import { useState } from "react";
import type { ToolCallInfo } from "../hooks/useChatStream";

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const secs = (ms / 1000).toFixed(1);
  return `${secs}s`;
}

function StatusIcon({ status }: { status: "running" | "done" | "error" }) {
  if (status === "running") {
    return (
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner text-cyan shrink-0">
        <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
      </svg>
    );
  }
  if (status === "error") {
    return (
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-red-400 shrink-0">
        <circle cx="12" cy="12" r="10" />
        <line x1="15" y1="9" x2="9" y2="15" />
        <line x1="9" y1="9" x2="15" y2="15" />
      </svg>
    );
  }
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-accent shrink-0">
      <path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ToolIcon({ isTeam }: { isTeam: boolean }) {
  if (isTeam) {
    return (
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-orange-400 shrink-0">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    );
  }
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-cyan shrink-0">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  );
}

export default function ToolCallCard({ tool }: { readonly tool: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(tool.status === "running");

  const isTeam = tool.name.startsWith("team:");
  const displayName = isTeam ? tool.name.slice(5) : tool.name;
  const label = isTeam ? "Delegated to" : "Using";

  const borderClass =
    tool.status === "running"
      ? "border-l-2 border-l-cyan"
      : tool.status === "error"
        ? "border-l-2 border-l-red-400"
        : "border-l-2 border-l-transparent";

  const hasArgs = tool.args && Object.keys(tool.args).length > 0;
  const hasDetail = hasArgs || tool.preview || tool.error;

  return (
    <div className={`rounded-lg bg-bg-tertiary/60 border border-border/50 overflow-hidden animate-fade-in ${borderClass}`}>
      {/* Header — always visible */}
      <button
        onClick={() => hasDetail && setExpanded(!expanded)}
        className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs ${hasDetail ? "cursor-pointer hover:bg-bg-hover/30" : "cursor-default"} transition-colors`}
      >
        <StatusIcon status={tool.status} />
        <ToolIcon isTeam={isTeam} />
        <span className="text-text-secondary">
          {label}{" "}
          <span className={isTeam ? "text-orange-400 font-medium" : "text-cyan font-medium"}>
            {displayName}
          </span>
        </span>

        {tool.duration_ms != null && (
          <span className="text-[10px] text-text-muted tabular-nums ml-auto mr-1">
            {formatDuration(tool.duration_ms)}
          </span>
        )}

        {tool.status === "done" && !expanded && tool.preview && (
          <span className="text-text-muted truncate max-w-[180px] text-[10px]">{tool.preview}</span>
        )}

        {hasDetail && (
          <svg
            width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            className={`text-text-muted shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        )}
      </button>

      {/* Expanded body */}
      {expanded && hasDetail && (
        <div className="px-3 pb-2 pt-1 space-y-1.5 border-t border-border/30">
          {hasArgs && (
            <div>
              <p className="text-[9px] text-text-muted uppercase tracking-wider mb-0.5">Arguments</p>
              <pre className="text-[10px] text-text-secondary font-mono bg-bg-hover/50 rounded px-2 py-1.5 max-h-[120px] overflow-y-auto whitespace-pre-wrap">
                {JSON.stringify(tool.args, null, 2)}
              </pre>
            </div>
          )}

          {tool.preview && (
            <div>
              <p className="text-[9px] text-text-muted uppercase tracking-wider mb-0.5">Result</p>
              <p className="text-[10px] text-text-secondary bg-bg-hover/50 rounded px-2 py-1.5 max-h-[80px] overflow-y-auto whitespace-pre-wrap">
                {tool.preview}
              </p>
            </div>
          )}

          {tool.error && (
            <div>
              <p className="text-[9px] text-red-400 uppercase tracking-wider mb-0.5">Error</p>
              <p className="text-[10px] text-red-300 bg-red-900/20 rounded px-2 py-1.5 whitespace-pre-wrap">
                {tool.error}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
