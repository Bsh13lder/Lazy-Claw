import { useState, type ReactNode } from "react";
import type { AgentTask } from "../api";
import { cancelTask } from "../api";
import { LaneTaskCard } from "./LaneTaskCard";

/**
 * Lane column — one of Foreground / Background / Specialist on the Ops Deck.
 * Header: label + count + "Cancel all in lane" (only when >= 2 tasks running).
 * Body:   stack of LaneTaskCards, or an empty state.
 *
 * Cancel-all uses individual cancelTask calls (not cancelAllTasks, which is
 * global) so we only kill this lane's work.
 */

const LANE_LABELS: Record<string, { label: string; hint: string; accent: string; emptyIcon: ReactNode }> = {
  foreground: {
    label: "FOREGROUND",
    hint: "Tasks attached to your active chat.",
    accent: "text-blue-400",
    emptyIcon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3" className="opacity-30">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    ),
  },
  background: {
    label: "BACKGROUND",
    hint: "Agents working for you in parallel.",
    accent: "text-purple-400",
    emptyIcon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3" className="opacity-30">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </svg>
    ),
  },
  specialist: {
    label: "SPECIALISTS",
    hint: "Delegated sub-agents.",
    accent: "text-orange-400",
    emptyIcon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3" className="opacity-30">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
      </svg>
    ),
  },
};

export function LaneColumn({
  lane,
  tasks,
  emptyLabel,
  onCta,
  ctaLabel,
  compact = true,
}: {
  lane: "foreground" | "background" | "specialist";
  tasks: AgentTask[];
  emptyLabel: string;
  onCta?: () => void;
  ctaLabel?: string;
  compact?: boolean;
}) {
  const cfg = LANE_LABELS[lane];
  const [killing, setKilling] = useState(false);
  const active = tasks.length > 0;

  const cancelLane = async () => {
    if (killing) return;
    setKilling(true);
    try {
      await Promise.allSettled(tasks.map((t) => cancelTask(t.task_id)));
    } finally {
      setKilling(false);
    }
  };

  return (
    <div
      className="lane-column"
      data-lane={lane}
      data-active={active ? "true" : "false"}
    >
      <div className="flex items-center gap-2">
        <p className={`text-[10px] font-semibold uppercase tracking-[0.12em] ${cfg.accent}`}>
          {cfg.label}
        </p>
        {active && (
          <span className={`text-[10px] ticker px-1.5 py-0.5 rounded-full bg-bg-tertiary ${cfg.accent}`}>
            {tasks.length}
          </span>
        )}
        {tasks.length >= 2 && (
          <button
            onClick={cancelLane}
            disabled={killing}
            className="ml-auto text-[10px] text-text-muted hover:text-red-400 transition-colors uppercase tracking-wider disabled:opacity-40"
            title={`Cancel all ${tasks.length} tasks in this lane`}
          >
            cancel all
          </button>
        )}
      </div>

      {active ? (
        <div className="flex flex-col gap-2">
          {tasks.map((t) => (
            <LaneTaskCard key={t.task_id} task={t} compact={compact} />
          ))}
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center text-center gap-1.5 py-5">
          {cfg.emptyIcon}
          <p className="text-[11px] text-text-muted leading-snug max-w-[18ch]">{emptyLabel}</p>
          {onCta && ctaLabel && (
            <button
              onClick={onCta}
              className="mt-1 px-2.5 py-1 text-[10px] rounded-md border border-border text-text-secondary hover:border-accent hover:text-accent transition-colors"
            >
              {ctaLabel}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
