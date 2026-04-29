import { useState } from "react";
import type { AgentTask } from "../api";
import { useAgentStatus } from "../context/AgentStatusContext";
import { useChat } from "../context/ChatContext";

const LS_OPEN = "lazyclaw-agent-console-open";

/**
 * AgentConsole — collapsible activity strip rendered above the chat
 * canvas. Two states:
 *
 * 1. Compact (default): a single ~36px ticker showing the live pulse,
 *    the most recent active agent name, and FG/BG/REC count pills.
 * 2. Expanded: three side-by-side lanes (Foreground · Background ·
 *    Recent) with per-task rows showing phase, current tool, and
 *    elapsed time.
 *
 * Reads from the existing AgentStatusContext (already polled +
 * websocket-nudged), so no new plumbing is introduced. State persisted
 * to localStorage so the user's choice survives page navigation.
 */
export default function AgentConsole() {
  const { agentStatus } = useAgentStatus();
  const { streamingState } = useChat();

  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(LS_OPEN) === "true";
    } catch {
      return false;
    }
  });
  const toggle = () => {
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(LS_OPEN, next ? "true" : "false");
      } catch {
        // Best-effort persistence.
      }
      return next;
    });
  };

  const fg = agentStatus?.active ?? [];
  const bg = agentStatus?.background ?? [];
  const recent = agentStatus?.recent ?? [];

  const hasRunning =
    fg.length > 0 || bg.length > 0 || streamingState.isStreaming;
  const liveLabel = fg[0]?.name ?? bg[0]?.name ?? null;
  const liveTool =
    fg[0]?.current_tool ??
    fg[0]?.recent_tools?.[fg[0].recent_tools.length - 1] ??
    null;

  return (
    <div className="shrink-0 border-b border-border bg-bg-secondary/60 backdrop-blur-sm">
      {/* Compact transmission strip — always visible. The whole row is the
          toggle target so the user can click anywhere to expand. */}
      <button
        onClick={toggle}
        className="w-full flex items-center gap-3 px-4 h-9 hover:bg-bg-hover/40 transition-colors text-left"
        title={open ? "Collapse agent console" : "Expand agent console"}
      >
        {/* Live indicator — emerald pulse when any agent is mid-flight,
            quiet gray otherwise. */}
        <span className="relative flex items-center justify-center w-2 h-2 shrink-0">
          {hasRunning && (
            <span className="absolute inset-[-4px] rounded-full bg-accent/25 animate-ping" />
          )}
          <span
            className={`relative w-2 h-2 rounded-full ${
              hasRunning ? "bg-accent" : "bg-text-muted/40"
            }`}
          />
        </span>

        <span className="text-[10px] uppercase tracking-[0.2em] text-text-muted/80 font-semibold shrink-0">
          Console
        </span>

        {/* Live ticker — the agent name + currently-running tool. Falls
            back to a quiet "idle" line when nothing's running. */}
        <span className="flex-1 truncate text-[11px] font-mono">
          {hasRunning ? (
            <>
              <span className="text-accent">▸</span>{" "}
              <span className="text-text-secondary">{liveLabel ?? "transmitting"}</span>
              {liveTool && (
                <>
                  <span className="text-text-muted/40 mx-1.5">·</span>
                  <span className="text-text-muted truncate">{liveTool}</span>
                </>
              )}
            </>
          ) : (
            <span className="text-text-muted/60 italic">no active agents · idle</span>
          )}
        </span>

        {/* Lane counts — pills sized for tabular consistency. */}
        <div className="flex items-center gap-1 shrink-0">
          <CountPill label="FG" count={fg.length} live={fg.length > 0} />
          <CountPill label="BG" count={bg.length} live={bg.length > 0} amber />
          <CountPill label="REC" count={recent.length} mute />
        </div>

        {/* Chevron */}
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`text-text-muted/70 shrink-0 transition-transform ${
            open ? "rotate-180" : ""
          }`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {/* Expanded lane dashboard */}
      {open && (
        <div className="px-4 pb-3 pt-1 grid grid-cols-1 md:grid-cols-3 gap-3">
          <Lane
            label="Foreground"
            tasks={fg}
            accentColor="var(--color-accent)"
            emptyHint="No foreground task"
            live
          />
          <Lane
            label="Background"
            tasks={bg}
            accentColor="#f59e0b"
            emptyHint="No background task"
            live
          />
          <Lane
            label="Recent"
            tasks={recent.slice(0, 5)}
            accentColor="#64748b"
            emptyHint="No completed tasks yet"
          />
        </div>
      )}
    </div>
  );
}

function CountPill({
  label,
  count,
  live,
  amber,
  mute,
}: {
  label: string;
  count: number;
  live?: boolean;
  amber?: boolean;
  mute?: boolean;
}) {
  let cls =
    "flex items-center gap-1 px-1.5 py-[1px] rounded text-[9px] font-mono uppercase tracking-[0.12em]";
  if (live && amber) {
    cls +=
      " bg-amber-500/15 text-amber-300 border border-amber-500/25";
  } else if (live) {
    cls += " bg-accent-soft text-accent border border-accent/25";
  } else if (mute) {
    cls += " bg-bg-primary/60 text-text-muted/70 border border-border/40";
  } else {
    cls += " bg-bg-hover text-text-secondary border border-border/40";
  }
  return (
    <span className={cls}>
      <span className="font-semibold">{label}</span>
      <span className="tabular-nums">{count}</span>
    </span>
  );
}

function Lane({
  label,
  tasks,
  accentColor,
  emptyHint,
  live,
}: {
  label: string;
  tasks: AgentTask[];
  accentColor: string;
  emptyHint: string;
  live?: boolean;
}) {
  return (
    <div className="rounded-md border border-border/60 bg-bg-primary/40 overflow-hidden">
      <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-border/40 bg-bg-secondary/40">
        <span
          className="text-[10px] uppercase tracking-[0.18em] font-semibold"
          style={{ color: accentColor }}
        >
          {label}
        </span>
        <span className="text-[10px] tabular-nums text-text-muted/60 font-mono">
          {String(tasks.length).padStart(2, "0")}
        </span>
      </div>
      {tasks.length === 0 ? (
        <div className="px-2.5 py-3 text-[10px] text-text-muted/50 italic font-mono">
          {emptyHint}
        </div>
      ) : (
        <div className="divide-y divide-border/30">
          {tasks.map((t) => (
            <LaneRow
              key={t.task_id}
              task={t}
              accentColor={accentColor}
              live={live}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function LaneRow({
  task,
  accentColor,
  live,
}: {
  task: AgentTask;
  accentColor: string;
  live?: boolean;
}) {
  const elapsed = task.elapsed_s ?? task.duration_s ?? null;
  const phase = (task.phase ?? task.status ?? "").trim() || "—";
  const tool =
    task.current_tool ??
    task.recent_tools?.[task.recent_tools.length - 1] ??
    null;
  return (
    <div className="px-2.5 py-1.5">
      <div className="flex items-center gap-1.5">
        <span
          className={`w-1.5 h-1.5 rounded-full shrink-0 ${
            live ? "live-pulse" : ""
          }`}
          style={{ background: accentColor }}
        />
        <span className="text-[11px] text-text-primary truncate flex-1 font-medium">
          {task.name}
        </span>
        {elapsed !== null && (
          <span className="text-[9px] tabular-nums text-text-muted/70 font-mono shrink-0">
            {formatElapsed(elapsed)}
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5 pl-3 mt-0.5 text-[9px] font-mono uppercase tracking-wider min-w-0">
        <span className="px-1 rounded bg-bg-hover/70 text-text-muted/80 shrink-0">
          {phase}
        </span>
        {tool && (
          <span className="text-text-muted/60 truncate">
            <span className="text-text-muted/30 mr-0.5">›</span>
            {tool}
          </span>
        )}
      </div>
    </div>
  );
}

function formatElapsed(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const r = Math.floor(s % 60);
    return `${m}m${String(r).padStart(2, "0")}s`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h${String(m).padStart(2, "0")}m`;
}
