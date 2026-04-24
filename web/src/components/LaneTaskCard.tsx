import { useState } from "react";
import type { AgentTask } from "../api";
import { cancelTask } from "../api";
import { iconFor, colorFor } from "./toolIcons";
import { formatElapsed } from "./TaskRow";

/**
 * Shared card for an in-flight task (foreground, background, or specialist).
 * Renders a live-ticking elapsed timer, the current tool, a step progress bar,
 * and a recent-tools chip row. Used by the Overview deck and the Activity deck.
 *
 * `compact` trims the instruction preview and recent-tools row — the dashboard
 * uses compact, Activity uses the full variant.
 */

const LANE_DOT: Record<string, string> = {
  foreground: "bg-blue-400",
  background: "bg-purple-400",
  specialist: "bg-orange-400",
  fast: "bg-cyan",
};

export function LaneTaskCard({
  task,
  compact = false,
  onCancelled,
}: {
  task: AgentTask;
  compact?: boolean;
  onCancelled?: () => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  // Server polls every 3s — the elapsed value refreshes at that cadence.
  // The `.live-pulse` dot + `.ticker` typography carry the "alive" feel.
  const elapsed = task.elapsed_s ?? 0;

  const handleCancel = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (cancelling) return;
    setCancelling(true);
    try {
      await cancelTask(task.task_id);
      onCancelled?.();
    } catch {
      setCancelling(false);
    }
  };

  const currentTool = task.current_tool || task.current_step;
  const recentTools = task.recent_tools ?? [];
  const request = task.instruction || task.description;
  const dot = LANE_DOT[task.lane] ?? "bg-accent";
  const steps = task.step_count ?? 0;
  const phase = task.phase;

  return (
    <div className="relative rounded-xl bg-bg-secondary border border-border/70 p-3 hover:border-border-light transition-colors animate-fade-in">
      {/* Header: lane dot · title · elapsed · cancel */}
      <div className="flex items-center gap-2 min-w-0">
        <span className={`w-1.5 h-1.5 rounded-full ${dot} live-pulse shrink-0`} />
        <p className="text-sm font-medium text-text-primary truncate flex-1" title={task.name}>
          {task.name}
        </p>
        <span className="ticker text-xs text-text-secondary shrink-0" title="Elapsed">
          {formatElapsed(elapsed)}
        </span>
        <button
          onClick={handleCancel}
          disabled={cancelling}
          className="p-1 -m-1 rounded-md text-text-muted hover:text-red-400 hover:bg-red-900/20 transition-colors disabled:opacity-40 shrink-0"
          title="Cancel task"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      {/* Current tool line */}
      {currentTool ? (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-text-secondary min-w-0">
          <span className={`${colorFor(currentTool)} shrink-0`}>{iconFor(currentTool)}</span>
          <span className="truncate">
            <span className="text-accent font-medium">{currentTool}</span>
            {steps > 0 && <span className="text-text-muted"> · step {steps}</span>}
            {phase && <span className="text-text-muted"> · {phase}</span>}
          </span>
        </div>
      ) : (
        <div className="mt-2 text-[11px] text-text-muted italic">thinking…</div>
      )}

      {/* Instruction preview — skip in compact to keep the deck scannable */}
      {!compact && request && (
        <p className="mt-1.5 text-[11px] text-text-muted line-clamp-2">
          <span className="text-text-muted/50">›</span> {request}
        </p>
      )}

      {/* Step progress bar — scales with step count, caps at 95% */}
      {steps > 0 && (
        <div className="mt-2.5 h-[3px] bg-bg-tertiary rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all duration-500"
            style={{ width: `${Math.min((steps / Math.max(steps, 5)) * 100, 95)}%` }}
          />
        </div>
      )}

      {/* Recent-tools chip row (non-compact only) */}
      {!compact && recentTools.length > 1 && (
        <div className="mt-2 flex items-center gap-1 flex-wrap">
          {recentTools.slice(-5).map((rt, i) => (
            <span
              key={`${rt}-${i}`}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border border-border/50 bg-bg-tertiary/40 text-text-muted"
              title={rt}
            >
              <span className={colorFor(rt)}>{iconFor(rt)}</span>
              <span className="truncate max-w-[70px]">{rt}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
