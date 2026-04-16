import { useState } from "react";
import type { ToolCallInfo } from "../hooks/useChatStream";
import { iconFor, colorFor, argSummary } from "./toolIcons";

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const secs = (ms / 1000).toFixed(1);
  return `${secs}s`;
}

function StatusIcon({ status }: { status: "running" | "done" | "error" }) {
  if (status === "running") {
    return (
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner text-cyan shrink-0">
        <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
      </svg>
    );
  }
  if (status === "error") {
    return (
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-red-400 shrink-0">
        <circle cx="12" cy="12" r="10" />
        <line x1="15" y1="9" x2="9" y2="15" />
        <line x1="9" y1="9" x2="15" y2="15" />
      </svg>
    );
  }
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-accent shrink-0">
      <path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function ToolCallCard({ tool }: { readonly tool: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(false);

  const isTeam = tool.name.startsWith("team:");
  const displayName = isTeam ? tool.name.slice(5) : tool.name;
  const accent = colorFor(tool.name);
  const brief = argSummary(tool.args);

  const bgClass =
    tool.status === "running"
      ? "bg-cyan/5 border-cyan/30"
      : tool.status === "error"
        ? "bg-red-400/5 border-red-400/30"
        : "bg-bg-tertiary/40 border-border/50";

  const hasArgs = tool.args && Object.keys(tool.args).length > 0;
  const hasDetail = hasArgs || tool.preview || tool.error;

  return (
    <div className={`rounded-lg border overflow-hidden animate-fade-in ${bgClass}`}>
      {/* Header — always visible */}
      <button
        onClick={() => hasDetail && setExpanded(!expanded)}
        className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-xs text-left ${hasDetail ? "cursor-pointer hover:bg-bg-hover/30" : "cursor-default"} transition-colors`}
      >
        {/* Tool icon */}
        <span className={`shrink-0 ${accent}`}>{iconFor(tool.name)}</span>

        {/* Name + arg summary */}
        <span className="min-w-0 flex-1 flex items-baseline gap-1.5">
          <span className={isTeam ? "text-orange-400 font-medium" : "text-text-primary font-medium"}>
            {displayName}
          </span>
          {brief && (
            <span className="text-text-muted truncate text-[11px] font-mono">
              {brief}
            </span>
          )}
        </span>

        {/* Duration */}
        {tool.duration_ms != null && (
          <span className="text-[10px] text-text-muted tabular-nums shrink-0">
            {formatDuration(tool.duration_ms)}
          </span>
        )}

        {/* Status icon */}
        <StatusIcon status={tool.status} />

        {/* Chevron */}
        {hasDetail && (
          <svg
            width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            className={`text-text-muted shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        )}
      </button>

      {/* Duration bar for running tools */}
      {tool.status === "running" && (
        <div className="h-0.5 bg-cyan/20 overflow-hidden">
          <div className="h-full w-1/3 bg-cyan animate-pulse" />
        </div>
      )}

      {/* Expanded body */}
      {expanded && hasDetail && (
        <div className="px-3 pb-2 pt-1 space-y-1.5 border-t border-border/30">
          {hasArgs && (
            <div>
              <p className="text-[9px] text-text-muted uppercase tracking-wider mb-0.5">Arguments</p>
              <pre className="text-[10px] text-text-secondary font-mono bg-bg-hover/50 rounded px-2 py-1.5 max-h-[160px] overflow-y-auto whitespace-pre-wrap">
                {JSON.stringify(tool.args, null, 2)}
              </pre>
            </div>
          )}

          {tool.preview && (
            <div>
              <p className="text-[9px] text-text-muted uppercase tracking-wider mb-0.5">Result</p>
              <p className="text-[10px] text-text-secondary bg-bg-hover/50 rounded px-2 py-1.5 max-h-[120px] overflow-y-auto whitespace-pre-wrap font-mono">
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
