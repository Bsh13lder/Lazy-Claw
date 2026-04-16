import { useEffect, useState } from "react";
import type { PhaseInfo, ToolCallInfo } from "../hooks/useChatStream";
import { iconFor, colorFor } from "./toolIcons";

interface ThinkingCardProps {
  phase?: PhaseInfo;
  tools: ToolCallInfo[];
  sideNotes: string[];
  startedAt?: number;
}

const PHASE_LABEL: Record<PhaseInfo["phase"], string> = {
  think: "Thinking",
  act: "Acting",
  observe: "Observing",
  reflect: "Reflecting",
};

const PHASE_COLOR: Record<PhaseInfo["phase"], string> = {
  think: "text-cyan border-cyan/40 bg-cyan/10",
  act: "text-accent border-accent/40 bg-accent/10",
  observe: "text-amber border-amber/40 bg-amber/10",
  reflect: "text-purple-400 border-purple-400/40 bg-purple-400/10",
};

const PHASE_DOT: Record<PhaseInfo["phase"], string> = {
  think: "bg-cyan",
  act: "bg-accent",
  observe: "bg-amber",
  reflect: "bg-purple-400",
};

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}m ${sec}s`;
}

export default function ThinkingCard({
  phase,
  tools,
  sideNotes,
  startedAt,
}: ThinkingCardProps) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, []);

  const phaseKey = phase?.phase ?? "think";
  const label = PHASE_LABEL[phaseKey];
  const badgeClass = PHASE_COLOR[phaseKey];
  const dotClass = PHASE_DOT[phaseKey];

  const startMs = startedAt ?? now;
  const elapsed = Math.max(0, now - startMs);

  // Last 3 tools for the mini-timeline (most recent first but render oldest-first)
  const runningTool = tools.find((t) => t.status === "running");
  const recentDone = tools.filter((t) => t.status !== "running").slice(-3);
  const extra = tools.length > recentDone.length + (runningTool ? 1 : 0)
    ? tools.length - recentDone.length - (runningTool ? 1 : 0)
    : 0;

  return (
    <div className="py-3 animate-fade-in">
      <div className="max-w-3xl mx-auto px-4">
        <div className="flex items-start gap-3">
          {/* Avatar */}
          <div className="w-6 h-6 rounded-full bg-accent-soft flex items-center justify-center shrink-0 mt-0.5">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
              <rect x="3" y="11" width="18" height="11" rx="2" />
              <path d="M7 11V7a5 5 0 0110 0v4" />
            </svg>
          </div>

          {/* Body */}
          <div className="flex-1 min-w-0 space-y-1.5">
            {/* Header: phase badge + elapsed */}
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10px] font-medium uppercase tracking-wider ${badgeClass}`}>
                <span className={`w-1.5 h-1.5 rounded-full pulse-dot ${dotClass}`} />
                {label}
              </span>
              <span className="text-[10px] text-text-muted tabular-nums">
                {formatElapsed(elapsed)}
              </span>
              {phase?.iteration != null && (
                <span className="text-[10px] text-text-muted">
                  step {phase.iteration + 1}
                </span>
              )}
            </div>

            {/* Current tool */}
            {runningTool && (
              <div className="flex items-center gap-1.5 text-[11px] text-text-secondary">
                <span className={colorFor(runningTool.name)}>
                  {iconFor(runningTool.name)}
                </span>
                <span>
                  using{" "}
                  <span className="font-medium text-text-primary">
                    {runningTool.name.startsWith("team:")
                      ? runningTool.name.slice(5)
                      : runningTool.name}
                  </span>
                </span>
              </div>
            )}

            {/* Phase=act with hinted tools */}
            {!runningTool && phase?.phase === "act" && phase.tools && phase.tools.length > 0 && (
              <div className="text-[11px] text-text-muted">
                preparing: {phase.tools.slice(0, 4).join(", ")}
                {phase.tools.length > 4 ? ` +${phase.tools.length - 4}` : ""}
              </div>
            )}

            {/* Recent tool strip */}
            {recentDone.length > 0 && (
              <div className="flex items-center gap-1 flex-wrap">
                {recentDone.map((t, i) => {
                  const isErr = t.status === "error";
                  const name = t.name.startsWith("team:") ? t.name.slice(5) : t.name;
                  return (
                    <span
                      key={i}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border ${
                        isErr
                          ? "border-red-400/40 bg-red-400/10 text-red-300"
                          : "border-border/50 bg-bg-tertiary/40 text-text-muted"
                      }`}
                      title={t.preview || t.error}
                    >
                      <span className={isErr ? "text-red-400" : "text-text-muted"}>
                        {iconFor(t.name)}
                      </span>
                      <span className="truncate max-w-[90px]">{name}</span>
                      {t.duration_ms != null && (
                        <span className="text-[9px] opacity-70 tabular-nums">
                          {t.duration_ms < 1000 ? `${t.duration_ms}ms` : `${(t.duration_ms / 1000).toFixed(1)}s`}
                        </span>
                      )}
                    </span>
                  );
                })}
                {extra > 0 && (
                  <span className="text-[10px] text-text-muted px-1">+{extra} earlier</span>
                )}
              </div>
            )}

            {/* Side-notes queued by the user mid-run */}
            {sideNotes.length > 0 && (
              <div className="flex items-start gap-1.5 text-[10px] text-cyan pt-0.5">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="shrink-0 mt-0.5">
                  <path d="M9 18l6-6-6-6" />
                </svg>
                <span className="truncate">
                  note{sideNotes.length > 1 ? "s" : ""}:{" "}
                  {sideNotes.map((n) => `“${n}”`).join(" · ")}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
