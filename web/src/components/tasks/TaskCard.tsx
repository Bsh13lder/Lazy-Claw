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

/**
 * A single task row in the list pane of the Tasks page.
 *
 * Layout:
 *   ┃  ☐  Title                                  🗓 in 2h   URGENT
 *   ┃     #tag #tag · 2/4 steps done
 *   ┃
 *   └── colored gutter on the left edge = priority at-a-glance
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

  const chip = formatDueChip(task.due_date, task.reminder_at);
  const tags = parseTags(task.tags);
  const steps = parseSteps(task.steps);
  const stepsDone = steps.filter((s) => s.done).length;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } }}
      className={`relative flex items-start gap-2.5 pl-4 pr-3 py-2.5 border-b border-border/60 last:border-b-0 cursor-pointer transition-colors ${
        selected ? "bg-accent-soft/30" : "hover:bg-bg-hover/40"
      } ${ticked ? "opacity-40" : ""}`}
    >
      {/* Priority gutter */}
      <span
        className={`absolute left-0 top-0 bottom-0 w-[3px] ${PRIORITY_GUTTER[task.priority]}`}
        aria-hidden="true"
      />

      {/* Checkbox */}
      <button
        onClick={onTick}
        disabled={busy}
        className={`mt-0.5 w-[14px] h-[14px] rounded border shrink-0 transition-all flex items-center justify-center ${
          ticked
            ? "bg-accent border-accent"
            : "border-border hover:border-accent bg-bg-tertiary"
        }`}
        aria-label={ticked ? "Completed" : "Mark complete"}
      >
        {ticked && (
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        )}
      </button>

      {/* Title + meta */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className={`text-[13.5px] truncate leading-snug ${ticked ? "line-through text-text-muted" : "text-text-primary"}`}>
            {task.title}
          </p>
          {task.owner === "agent" && (
            <span className="text-[9px] uppercase tracking-[0.1em] px-1 py-0.5 rounded bg-cyan-soft text-cyan shrink-0">
              ai
            </span>
          )}
        </div>
        {(tags.length > 0 || task.category || steps.length > 0) && (
          <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
            {task.category && (
              <span className="text-[10px] uppercase tracking-wider text-text-muted">
                {task.category}
              </span>
            )}
            {tags.slice(0, 4).map((t) => (
              <span key={t} className="text-[10px] text-text-muted">
                #{t}
              </span>
            ))}
            {steps.length > 0 && (
              <span className="text-[10px] text-text-muted ticker">
                ☰ {stepsDone}/{steps.length}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Time + priority label */}
      <div className="flex flex-col items-end gap-0.5 shrink-0">
        {chip.label && (
          <span className={`text-[10px] ticker ${DUE_TONE_CLASS[chip.tone]}`}>
            {chip.label}
          </span>
        )}
        {task.priority !== "medium" && (
          <span className={`text-[9px] uppercase tracking-[0.1em] ${PRIORITY_TEXT[task.priority]}`}>
            {PRIORITY_LABEL[task.priority]}
          </span>
        )}
      </div>
    </div>
  );
}
