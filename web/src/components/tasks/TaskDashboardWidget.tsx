import { useEffect, useRef, useState } from "react";
import type { TaskItem } from "../../api";
import { completeTask, listTasks } from "../../api";
import type { Page } from "../NavShell";
import { QuickAddBar } from "./QuickAddBar";
import {
  DUE_TONE_CLASS,
  PRIORITY_GUTTER,
  PRIORITY_RANK,
  formatDueChip,
} from "./taskHelpers";
import { useLiveCountdown } from "./useLiveCountdown";

/**
 * Overview-page widget: AI quick-add on top, five most-urgent upcoming
 * user tasks beneath. Collapsible; state persists in localStorage so it
 * stays open/closed across page loads.
 *
 * This is deliberately separate from MyTasksPanel (the compact sidebar
 * list that lives in the right column). This widget's job is *adding* —
 * it's the piece the user was missing on the dashboard.
 */

const OPEN_STORAGE_KEY = "lazyclaw.overview.aiTasksOpen";

function readOpen(): boolean {
  try {
    const v = localStorage.getItem(OPEN_STORAGE_KEY);
    return v === null ? true : v === "1";
  } catch {
    return true;
  }
}

function writeOpen(open: boolean): void {
  try {
    localStorage.setItem(OPEN_STORAGE_KEY, open ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function UpcomingRow({ task, onChanged }: { task: TaskItem; onChanged: () => void }) {
  const [ticked, setTicked] = useState(false);
  const [busy, setBusy] = useState(false);
  useLiveCountdown(task.due_date, task.reminder_at);
  const chip = formatDueChip(task.due_date, task.reminder_at);

  const onTick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy || ticked) return;
    setTicked(true);
    setBusy(true);
    try {
      await completeTask(task.id);
      setTimeout(onChanged, 220);
    } catch {
      setTicked(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`relative flex items-center gap-2 pl-3 pr-2 py-1.5 rounded-md hover:bg-bg-hover/50 transition-colors ${ticked ? "opacity-40" : ""}`}>
      <span
        className={`absolute left-0 top-1 bottom-1 w-[2px] rounded-full ${PRIORITY_GUTTER[task.priority]}`}
        aria-hidden="true"
      />
      <button
        onClick={onTick}
        disabled={busy}
        className={`w-[13px] h-[13px] rounded border shrink-0 transition-all flex items-center justify-center ${
          ticked ? "bg-accent border-accent" : "border-border hover:border-accent bg-bg-tertiary"
        }`}
        aria-label={ticked ? "Completed" : "Mark complete"}
      >
        {ticked && (
          <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        )}
      </button>
      <span className={`flex-1 text-[13px] truncate ${ticked ? "line-through text-text-muted" : "text-text-primary"}`}>
        {task.title}
      </span>
      {chip.label && (
        <span className={`text-[10px] ticker shrink-0 ${DUE_TONE_CLASS[chip.tone]}`}>
          {chip.label}
        </span>
      )}
    </div>
  );
}

export function TaskDashboardWidget({ onNavigate }: { onNavigate: (p: Page) => void }) {
  const [open, setOpen] = useState<boolean>(readOpen);
  const [upcoming, setUpcoming] = useState<TaskItem[]>([]);
  const [reloadTick, setReloadTick] = useState(0);
  const aliveRef = useRef(true);

  useEffect(() => { writeOpen(open); }, [open]);

  useEffect(() => {
    aliveRef.current = true;
    const load = async () => {
      try {
        const all = await listTasks({ owner: "user", status: "todo", bucket: "all" });
        if (!aliveRef.current) return;
        // Sort: overdue/today first, then soonest, then priority.
        const sorted = [...all].sort((a, b) => {
          const aTime = a.due_date ? new Date(`${a.due_date}T00:00:00`).getTime() : Number.MAX_SAFE_INTEGER;
          const bTime = b.due_date ? new Date(`${b.due_date}T00:00:00`).getTime() : Number.MAX_SAFE_INTEGER;
          if (aTime !== bTime) return aTime - bTime;
          return PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
        });
        setUpcoming(sorted.slice(0, 5));
      } catch {
        if (aliveRef.current) setUpcoming([]);
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => { aliveRef.current = false; clearInterval(id); };
  }, [reloadTick]);

  return (
    <section className="rounded-2xl bg-bg-secondary/60 border border-border/60 overflow-hidden">
      <header className="flex items-center gap-2 px-4 py-2.5 border-b border-border/60">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="text-text-muted hover:text-text-primary transition-colors"
          aria-label={open ? "Collapse" : "Expand"}
          aria-expanded={open}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            className={`transition-transform ${open ? "rotate-90" : ""}`}
          >
            <polyline points="9 6 15 12 9 18" />
          </svg>
        </button>
        <h3 className="text-[13px] font-semibold text-text-primary inline-flex items-center gap-1.5">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-accent">
            <path d="M12 2l2 6 6 0-5 4 2 6-5-4-5 4 2-6-5-4 6 0z" />
          </svg>
          Add task with AI
        </h3>
        <span className="text-[10px] text-text-muted">
          natural language · dates, tags, auto-steps
        </span>
        <button
          type="button"
          onClick={() => onNavigate("tasks")}
          className="ml-auto text-[10px] uppercase tracking-wider text-text-muted hover:text-accent transition-colors"
        >
          all tasks →
        </button>
      </header>

      {open && (
        <div className="p-3 space-y-3">
          <QuickAddBar
            onAdded={() => setReloadTick((n) => n + 1)}
            variant="compact"
            placeholder='e.g. "tomorrow 9am buy milk urgent", "next friday ship the deck #work"'
          />

          {upcoming.length > 0 ? (
            <div className="space-y-0.5">
              <p className="text-[9px] font-semibold uppercase tracking-[0.12em] text-text-secondary px-1">
                Up next
              </p>
              {upcoming.map((t) => (
                <UpcomingRow
                  key={t.id}
                  task={t}
                  onChanged={() => setReloadTick((n) => n + 1)}
                />
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-text-muted italic px-1 py-2">
              No open tasks. Queue one up above.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
