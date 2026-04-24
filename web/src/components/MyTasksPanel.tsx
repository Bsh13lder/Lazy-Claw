import { useEffect, useRef, useState } from "react";
import type { TaskItem } from "../api";
import { listTasks, completeTask, addTask } from "../api";
import type { Page } from "./NavShell";

/**
 * My Tasks — the user's personal todo list.
 *
 * Source: `/api/tasks?owner=user` — encrypted Task Manager entries dictated by
 * the user (e.g. "buy milk", "deadline for X on Friday"). Agent-owned rows
 * (owner=agent, created by background skills) are excluded by default; a
 * toggle reveals them if the user wants to see everything.
 *
 * Interactions:
 *   • Tick a checkbox → POST /api/tasks/{id}/complete
 *   • Quick-add via the bottom input → POST /api/tasks
 *   • Due-date rendering uses relative language (overdue / today / tomorrow / date)
 */

type Priority = TaskItem["priority"];

const PRIORITY_RANK: Record<Priority, number> = { urgent: 0, high: 1, medium: 2, low: 3 };
const PRIORITY_COLOR: Record<Priority, string> = {
  urgent: "text-red-400",
  high: "text-amber",
  medium: "text-text-secondary",
  low: "text-text-muted",
};
const PRIORITY_DOT: Record<Priority, string> = {
  urgent: "bg-red-400",
  high: "bg-amber",
  medium: "bg-text-secondary",
  low: "bg-text-muted",
};

function formatDue(dueDate: string | null): { label: string; cls: string } {
  if (!dueDate) return { label: "", cls: "text-text-muted" };
  // `dueDate` is YYYY-MM-DD (plaintext column).
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = new Date(`${dueDate}T00:00:00`);
  if (Number.isNaN(d.getTime())) return { label: dueDate, cls: "text-text-muted" };

  const diffDays = Math.round((d.getTime() - today.getTime()) / 864e5);

  if (diffDays < 0) return { label: `${Math.abs(diffDays)}d overdue`, cls: "text-red-400" };
  if (diffDays === 0) return { label: "today", cls: "text-amber" };
  if (diffDays === 1) return { label: "tomorrow", cls: "text-accent" };
  if (diffDays < 7) return { label: `in ${diffDays}d`, cls: "text-text-secondary" };
  // Beyond a week — show the date.
  return {
    label: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    cls: "text-text-muted",
  };
}

function TaskRow({
  task,
  onChanged,
}: {
  task: TaskItem;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [ticked, setTicked] = useState(false);
  const due = formatDue(task.due_date);

  const onTick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    e.stopPropagation();
    if (busy) return;
    setTicked(true);
    setBusy(true);
    try {
      await completeTask(task.id);
      // Small delay so the strike-through animation is visible before removal.
      setTimeout(onChanged, 250);
    } catch {
      setTicked(false);
    } finally {
      setBusy(false);
    }
  };

  const tags: string[] = (() => {
    if (!task.tags) return [];
    try { return JSON.parse(task.tags); } catch { return []; }
  })();

  return (
    <label
      className={`group flex items-start gap-2.5 px-3 py-2 rounded-lg hover:bg-bg-hover/40 transition-colors cursor-pointer min-w-0 ${
        ticked ? "opacity-50" : ""
      }`}
    >
      <input
        type="checkbox"
        checked={ticked}
        onChange={onTick}
        disabled={busy}
        className="mt-0.5 w-3.5 h-3.5 rounded border-border bg-bg-tertiary text-accent focus:ring-accent/40 cursor-pointer shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={`w-1 h-1 rounded-full shrink-0 ${PRIORITY_DOT[task.priority]}`} />
          <p className={`text-[13px] truncate ${ticked ? "line-through text-text-muted" : "text-text-primary"}`}>
            {task.title}
          </p>
        </div>
        {(task.category || tags.length > 0) && (
          <div className="flex items-center gap-1 mt-0.5 flex-wrap">
            {task.category && (
              <span className="text-[10px] uppercase tracking-wider text-text-muted">
                {task.category}
              </span>
            )}
            {tags.slice(0, 3).map((t) => (
              <span key={t} className="text-[10px] px-1 rounded bg-bg-tertiary text-text-muted">
                #{t}
              </span>
            ))}
          </div>
        )}
      </div>
      {due.label && (
        <span className={`text-[10px] shrink-0 whitespace-nowrap ${due.cls}`} title={task.due_date ?? ""}>
          {due.label}
        </span>
      )}
      {task.priority !== "medium" && (
        <span className={`text-[9px] uppercase tracking-wider shrink-0 ${PRIORITY_COLOR[task.priority]}`}>
          {task.priority}
        </span>
      )}
    </label>
  );
}

function QuickAdd({ onAdded }: { onAdded: () => void }) {
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const title = value.trim();
    if (!title || submitting) return;
    setSubmitting(true);
    try {
      await addTask({ title, priority: "medium" });
      setValue("");
      onAdded();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); void submit(); }}
      className="flex items-center gap-2 px-3 py-2 border-t border-border/60"
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-text-muted shrink-0">
        <line x1="12" y1="5" x2="12" y2="19" />
        <line x1="5" y1="12" x2="19" y2="12" />
      </svg>
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Add a task…"
        disabled={submitting}
        className="flex-1 text-[13px] bg-transparent text-text-primary placeholder:text-text-placeholder focus:outline-none"
      />
      {value.trim() && (
        <button
          type="submit"
          disabled={submitting}
          className="text-[10px] uppercase tracking-wider text-accent hover:text-accent-dim transition-colors disabled:opacity-40"
        >
          {submitting ? "…" : "add"}
        </button>
      )}
    </form>
  );
}

type Filter = "today" | "upcoming" | "all";

const FILTER_OPTS: { id: Filter; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "upcoming", label: "Upcoming" },
  { id: "all", label: "All" },
];

export function MyTasksPanel({ onNavigate }: { onNavigate?: (p: Page) => void } = {}) {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [filter, setFilter] = useState<Filter>("today");
  const [reloadTick, setReloadTick] = useState(0);
  const aliveRef = useRef(true);
  const triggerReload = () => setReloadTick((n) => n + 1);

  useEffect(() => {
    aliveRef.current = true;

    const load = async () => {
      try {
        const bucket = filter === "all" ? "all" : filter;
        const data = await listTasks({
          owner: "user",
          status: "todo",
          bucket,
        });
        if (!aliveRef.current) return;
        setTasks(data);
      } catch {
        if (aliveRef.current) setTasks([]);
      } finally {
        if (aliveRef.current) setLoaded(true);
      }
    };

    load();
    const id = setInterval(load, 20_000);
    return () => {
      aliveRef.current = false;
      clearInterval(id);
    };
  }, [filter, reloadTick]);

  const sorted = [...tasks].sort((a, b) => {
    // Priority first, then due_date ascending, then newest first
    const pri = PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
    if (pri !== 0) return pri;
    const ad = a.due_date ? new Date(a.due_date).getTime() : Number.MAX_SAFE_INTEGER;
    const bd = b.due_date ? new Date(b.due_date).getTime() : Number.MAX_SAFE_INTEGER;
    if (ad !== bd) return ad - bd;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  const overdue = sorted.filter((t) => {
    if (!t.due_date) return false;
    const d = new Date(`${t.due_date}T00:00:00`);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return d.getTime() < today.getTime();
  }).length;

  return (
    <div className="rounded-xl bg-bg-secondary border border-border">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/60">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
          My to-dos
        </p>
        {tasks.length > 0 && (
          <span className="text-[10px] ticker px-1.5 py-0.5 rounded-full bg-bg-tertiary text-text-muted">
            {tasks.length}
          </span>
        )}
        {overdue > 0 && (
          <span className="text-[10px] ticker px-1.5 py-0.5 rounded-full bg-red-900/30 text-red-400">
            {overdue} overdue
          </span>
        )}

        {onNavigate && (
          <button
            onClick={() => onNavigate("tasks")}
            className="ml-auto text-[10px] text-text-muted hover:text-accent transition-colors uppercase tracking-wider"
            title="Open the full Tasks page"
          >
            open →
          </button>
        )}

        <div className={`${onNavigate ? "" : "ml-auto "}flex gap-1`}>
          {FILTER_OPTS.map((o) => (
            <button
              key={o.id}
              onClick={() => setFilter(o.id)}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                filter === o.id
                  ? "bg-accent-soft text-accent"
                  : "text-text-muted hover:text-text-secondary"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      <div className="max-h-[300px] overflow-y-auto py-1">
        {!loaded ? (
          <p className="text-xs text-text-muted px-3 py-6 text-center">Loading…</p>
        ) : sorted.length === 0 ? (
          <div className="px-3 py-6 text-center">
            <p className="text-xs text-text-muted">
              {filter === "today"
                ? "Nothing due today. Ahead of the game."
                : "No tasks yet — ask me to remember things for you."}
            </p>
          </div>
        ) : (
          sorted.map((t) => (
            <TaskRow key={t.id} task={t} onChanged={triggerReload} />
          ))
        )}
      </div>

      {/* Quick-add */}
      <QuickAdd onAdded={triggerReload} />
    </div>
  );
}
