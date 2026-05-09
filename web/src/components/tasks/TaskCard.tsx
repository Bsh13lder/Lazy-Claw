import { useState } from "react";
import type { TaskItem } from "../../api";
import { completeTask, parseSteps } from "../../api";
import {
  PRIORITY_GUTTER,
  PRIORITY_LABEL,
  PRIORITY_TEXT,
  formatDueChip,
  DUE_TONE_CLASS,
  parseTags,
} from "./taskHelpers";
import { useLiveCountdown } from "./useLiveCountdown";
import { TaskRescheduleInput } from "./TaskRescheduleInput";

/**
 * One row in the list pane. The body is a calm read-only summary at rest;
 * a hover rail reveals secondary actions (Smart reschedule, Reschedule).
 * Clicking the row still selects-for-detail; the rail buttons stop
 * propagation so they don't pull the card into the right pane.
 *
 *   ┃  ☐  Title                              🗓 in 2h    URGENT
 *   ┃     #tag · 2/4 steps                   ┌─ rail ─┐
 *   ┃                                         │  ⏰  ✨ │  ← hover only
 *   ┃                                         └────────┘
 */

export function TaskCard({
  task,
  selected,
  onClick,
  onChanged,
}: {
  task: TaskItem;
  selected: boolean;
  onClick: () => void;
  onChanged: () => void;
}) {
  const [ticked, setTicked] = useState(task.status === "done");
  const [busy, setBusy] = useState(false);
  const [rescheduleOpen, setRescheduleOpen] = useState(false);

  const onTick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy || ticked) return;
    setTicked(true);
    setBusy(true);
    try {
      await completeTask(task.id);
      setTimeout(onChanged, 250);
    } catch {
      setTicked(false);
    } finally {
      setBusy(false);
    }
  };

  useLiveCountdown(task.due_date, task.reminder_at);
  const chip = formatDueChip(task.due_date, task.reminder_at);
  const tags = parseTags(task.tags);
  const steps = parseSteps(task.steps);
  const stepsDone = steps.filter((s) => s.done).length;
  const hasDeadline = !!(task.due_date || task.reminder_at);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } }}
      className={`group relative flex flex-col gap-1.5 pl-4 pr-3 py-2.5 border-b border-border/40 last:border-b-0 cursor-pointer transition-colors ${
        selected ? "bg-accent-soft/25 shadow-[inset_2px_0_0_var(--color-accent)]" : "hover:bg-bg-hover/40"
      } ${ticked ? "opacity-40" : ""}`}
    >
      <span className={`absolute left-0 top-0 bottom-0 w-[3px] ${PRIORITY_GUTTER[task.priority]}`} aria-hidden="true" />

      <div className="flex items-start gap-2.5">
        <button
          onClick={onTick}
          disabled={busy}
          className={`mt-0.5 w-[14px] h-[14px] rounded border shrink-0 transition-all flex items-center justify-center ${
            ticked ? "bg-accent border-accent" : "border-border hover:border-accent bg-bg-tertiary"
          }`}
          aria-label={ticked ? "Completed" : "Mark complete"}
        >
          {ticked && (
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          )}
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className={`text-[13.5px] truncate leading-snug ${ticked ? "line-through text-text-muted" : "text-text-primary"}`}>
              {task.title}
            </p>
            {task.owner === "agent" && (
              <span className="text-[9px] uppercase tracking-[0.1em] px-1 py-0.5 rounded bg-cyan-soft text-cyan shrink-0">ai</span>
            )}
          </div>
          {(tags.length > 0 || task.category || steps.length > 0) && (
            <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
              {task.category && <span className="text-[10px] uppercase tracking-wider text-text-muted">{task.category}</span>}
              {tags.slice(0, 4).map((t) => <span key={t} className="text-[10px] text-text-muted">#{t}</span>)}
              {steps.length > 0 && <span className="text-[10px] text-text-muted ticker">☰ {stepsDone}/{steps.length}</span>}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <div className="flex flex-col items-end gap-0.5">
            {chip.label && <span className={`text-[10px] ticker ${DUE_TONE_CLASS[chip.tone]}`}>{chip.label}</span>}
            {task.priority !== "medium" && (
              <span className={`text-[9px] uppercase tracking-[0.1em] ${PRIORITY_TEXT[task.priority]}`}>
                {PRIORITY_LABEL[task.priority]}
              </span>
            )}
          </div>

          {/* Hover rail — only renders affordances when the card is alive. */}
          {!ticked && (
            <div className="hidden group-hover:flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setRescheduleOpen((o) => !o); }}
                title={rescheduleOpen ? "Close reschedule" : (hasDeadline ? "Reschedule" : "Schedule")}
                className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
                  rescheduleOpen
                    ? "border-accent/50 bg-accent-soft text-accent"
                    : "border-border/60 text-text-muted hover:text-accent hover:border-accent/40"
                }`}
              >
                ⏰
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Inline reschedule strip — slides in beneath the row when toggled. */}
      {rescheduleOpen && !ticked && (
        <div onClick={(e) => e.stopPropagation()} className="pl-[26px] pr-1 pt-0.5">
          <TaskRescheduleInput
            taskId={task.id}
            variant="compact"
            autoFocus
            onChanged={() => { onChanged(); setRescheduleOpen(false); }}
          />
        </div>
      )}
    </div>
  );
}
