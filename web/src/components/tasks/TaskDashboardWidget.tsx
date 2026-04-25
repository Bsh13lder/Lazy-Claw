import { useEffect, useMemo, useRef, useState } from "react";
import type { TaskItem } from "../../api";
import { completeTask, listTasks } from "../../api";
import type { Page } from "../NavShell";
import { QuickAddBar } from "./QuickAddBar";
import {
  DUE_TONE_CLASS,
  PRIORITY_GUTTER,
  PRIORITY_LABEL,
  PRIORITY_RANK,
  PRIORITY_TEXT,
  formatDueChip,
  parseTags,
} from "./taskHelpers";
import { useLiveCountdown } from "./useLiveCountdown";

/**
 * The Overview-page tasks widget.
 *
 * Combines the AI quick-add bar (top) with a dense, information-rich task
 * list (below). Each row shows priority gutter · checkbox · title · category
 * chip · #tags · due chip · priority pill — same density as the full Tasks
 * page card but compressed into a single line so 8–10 rows fit naturally.
 *
 *   ┌─────────────────────────────────────────────────────────┐
 *   │ ✦ My tasks    [Today] [Upcoming] [All]   all tasks →    │
 *   ├─────────────────────────────────────────────────────────┤
 *   │ ▌ ☐ Pay rent              [finance] #bills   today  HI  │
 *   │ ▌ ☐ Buy organic eggs                #shopping  in 2d    │
 *   │ ▌ ☐ Submit invoice            [work]          fri 14:00 │
 *   │  …                                                       │
 *   ├─────────────────────────────────────────────────────────┤
 *   │ Quick-add: type "tomorrow 9am buy milk urgent" …      ⏎ │
 *   ├─────────────────────────────────────────────────────────┤
 *   │ 12 open · 3 today · 1 overdue                           │
 *   └─────────────────────────────────────────────────────────┘
 */

const FILTER_KEY = "lazyclaw.overview.tasks.filter";
const ROW_LIMIT = 10;

type Filter = "today" | "upcoming" | "all";

const FILTER_OPTS: { id: Filter; label: string; sub: string }[] = [
  { id: "today", label: "Today", sub: "Due today or overdue" },
  { id: "upcoming", label: "Upcoming", sub: "Due in the future" },
  { id: "all", label: "All", sub: "Everything open" },
];

function readFilter(): Filter {
  try {
    const v = localStorage.getItem(FILTER_KEY);
    if (v === "today" || v === "upcoming" || v === "all") return v;
  } catch { /* ignore */ }
  return "today";
}
function writeFilter(f: Filter) {
  try { localStorage.setItem(FILTER_KEY, f); } catch { /* ignore */ }
}

function todayIsoDate(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString().slice(0, 10);
}

function dueBucket(t: TaskItem, today: string): "overdue" | "today" | "future" | "none" {
  if (!t.due_date) return "none";
  if (t.due_date < today) return "overdue";
  if (t.due_date === today) return "today";
  return "future";
}

function TaskRow({ task, onChanged }: { task: TaskItem; onChanged: () => void }) {
  const [ticked, setTicked] = useState(false);
  const [busy, setBusy] = useState(false);
  useLiveCountdown(task.due_date, task.reminder_at);
  const chip = formatDueChip(task.due_date, task.reminder_at);
  const tags = useMemo(() => parseTags(task.tags), [task.tags]);

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
    <div
      className={`group relative flex items-center gap-2 pl-3 pr-2 py-1.5 rounded-md hover:bg-bg-hover/50 transition-colors min-w-0 ${
        ticked ? "opacity-40" : ""
      }`}
    >
      <span
        className={`absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full ${PRIORITY_GUTTER[task.priority]}`}
        aria-hidden
      />
      <button
        onClick={onTick}
        disabled={busy}
        aria-label={ticked ? "Completed" : "Mark complete"}
        className={`w-[13px] h-[13px] rounded border shrink-0 transition-all flex items-center justify-center ${
          ticked
            ? "bg-accent border-accent"
            : "border-border hover:border-accent bg-bg-tertiary"
        }`}
      >
        {ticked && (
          <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        )}
      </button>

      <span
        className={`flex-1 text-[13px] truncate ${
          ticked ? "line-through text-text-muted" : "text-text-primary"
        }`}
        title={task.title}
      >
        {task.title}
      </span>

      {/* Category + tags — squeeze in only when there's space. */}
      {(task.category || tags.length > 0) && (
        <div className="hidden md:flex items-center gap-1 flex-none max-w-[200px] overflow-hidden">
          {task.category && (
            <span className="text-[9px] uppercase tracking-wider px-1 py-0.5 rounded bg-bg-tertiary/80 text-text-muted whitespace-nowrap">
              {task.category}
            </span>
          )}
          {tags.slice(0, 2).map((t) => (
            <span
              key={t}
              className="text-[10px] text-text-muted whitespace-nowrap"
              title={`#${t}`}
            >
              #{t}
            </span>
          ))}
          {tags.length > 2 ? (
            <span className="text-[10px] text-text-muted">+{tags.length - 2}</span>
          ) : null}
        </div>
      )}

      {chip.label && (
        <span
          className={`text-[10px] ticker shrink-0 whitespace-nowrap tabular-nums ${DUE_TONE_CLASS[chip.tone]}`}
          title={task.due_date ?? task.reminder_at ?? ""}
        >
          {chip.label}
        </span>
      )}

      {(task.priority === "urgent" || task.priority === "high") && (
        <span
          className={`text-[9px] uppercase tracking-wider font-semibold shrink-0 ${PRIORITY_TEXT[task.priority]}`}
        >
          {PRIORITY_LABEL[task.priority]}
        </span>
      )}
    </div>
  );
}

export function TaskDashboardWidget({ onNavigate }: { onNavigate: (p: Page) => void }) {
  const [filter, setFilter] = useState<Filter>(readFilter);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [reloadTick, setReloadTick] = useState(0);
  const aliveRef = useRef(true);

  useEffect(() => { writeFilter(filter); }, [filter]);

  useEffect(() => {
    aliveRef.current = true;
    const load = async () => {
      try {
        const all = await listTasks({ owner: "user", status: "todo", bucket: "all" });
        if (!aliveRef.current) return;
        setTasks(all);
      } catch {
        if (aliveRef.current) setTasks([]);
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => { aliveRef.current = false; clearInterval(id); };
  }, [reloadTick]);

  const today = todayIsoDate();

  // Counters for the summary footer + filter pill labels.
  const { overdueCount, todayCount, upcomingCount, totalCount } = useMemo(() => {
    let overdue = 0, td = 0, up = 0;
    for (const t of tasks) {
      const b = dueBucket(t, today);
      if (b === "overdue") overdue++;
      else if (b === "today") td++;
      else if (b === "future") up++;
    }
    return {
      overdueCount: overdue,
      todayCount: td,
      upcomingCount: up,
      totalCount: tasks.length,
    };
  }, [tasks, today]);

  // Filtered + sorted view for the list. Order: overdue first, then today,
  // then by due date soonest, then by priority.
  const visible = useMemo(() => {
    const base = tasks.filter((t) => {
      const b = dueBucket(t, today);
      if (filter === "today") return b === "overdue" || b === "today";
      if (filter === "upcoming") return b === "future";
      return true;
    });
    base.sort((a, b) => {
      const at = a.due_date ? new Date(`${a.due_date}T00:00:00`).getTime() : Number.MAX_SAFE_INTEGER;
      const bt = b.due_date ? new Date(`${b.due_date}T00:00:00`).getTime() : Number.MAX_SAFE_INTEGER;
      if (at !== bt) return at - bt;
      return PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
    });
    return base.slice(0, ROW_LIMIT);
  }, [tasks, filter, today]);

  return (
    <section className="rounded-2xl bg-bg-secondary/60 border border-border/60 overflow-hidden flex flex-col">
      {/* Header — title + filter chips + link to full Tasks page */}
      <header className="flex items-center gap-2 px-4 py-2.5 border-b border-border/60">
        <h3 className="text-[13px] font-semibold text-text-primary inline-flex items-center gap-1.5">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-accent">
            <path d="M9 11l3 3L22 4" />
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
          </svg>
          My tasks
        </h3>

        <div className="ml-2 flex items-center gap-0.5 bg-bg-tertiary/60 rounded-md p-0.5">
          {FILTER_OPTS.map((opt) => {
            const active = filter === opt.id;
            const count =
              opt.id === "today"
                ? overdueCount + todayCount
                : opt.id === "upcoming"
                  ? upcomingCount
                  : totalCount;
            return (
              <button
                key={opt.id}
                onClick={() => setFilter(opt.id)}
                title={opt.sub}
                className={[
                  "px-2 py-0.5 rounded text-[11px] transition-colors flex items-center gap-1",
                  active
                    ? "bg-accent-soft text-accent"
                    : "text-text-secondary hover:text-text-primary",
                ].join(" ")}
              >
                {opt.label}
                <span className="text-[9px] tabular-nums opacity-70">{count}</span>
              </button>
            );
          })}
        </div>

        <button
          onClick={() => onNavigate("tasks")}
          className="ml-auto text-[10px] uppercase tracking-wider text-text-muted hover:text-accent transition-colors"
        >
          all tasks →
        </button>
      </header>

      {/* AI quick-add bar */}
      <div className="px-3 py-2 border-b border-border/40">
        <QuickAddBar
          onAdded={() => setReloadTick((n) => n + 1)}
          variant="compact"
          placeholder='e.g. "within 2 days finish report urgent #work" or "by friday call mom"'
        />
      </div>

      {/* Task list */}
      <div className="px-2 py-2 min-h-[120px]">
        {visible.length > 0 ? (
          <div className="space-y-0.5">
            {visible.map((t) => (
              <TaskRow
                key={t.id}
                task={t}
                onChanged={() => setReloadTick((n) => n + 1)}
              />
            ))}
          </div>
        ) : (
          <div className="text-center text-[11px] text-text-muted italic py-6">
            {filter === "today"
              ? "Nothing due today. 🟢"
              : filter === "upcoming"
                ? "No upcoming deadlines."
                : "No open tasks. Capture one above."}
          </div>
        )}
      </div>

      {/* Summary footer */}
      <footer className="flex items-center gap-3 px-4 py-2 border-t border-border/40 text-[10px] text-text-muted tabular-nums">
        <span>{totalCount} open</span>
        {todayCount > 0 && (
          <>
            <span className="opacity-40">·</span>
            <span className="text-amber">{todayCount} today</span>
          </>
        )}
        {overdueCount > 0 && (
          <>
            <span className="opacity-40">·</span>
            <span className="text-red-400">{overdueCount} overdue</span>
          </>
        )}
        {upcomingCount > 0 && (
          <>
            <span className="opacity-40">·</span>
            <span>{upcomingCount} upcoming</span>
          </>
        )}
        {tasks.length > ROW_LIMIT && (
          <span className="ml-auto text-text-muted">
            showing {visible.length} of {tasks.length}
          </span>
        )}
      </footer>
    </section>
  );
}
