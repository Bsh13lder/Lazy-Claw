import { useEffect, useMemo, useRef, useState } from "react";
import type { TaskItem } from "../api";
import { listTasks, parseSteps } from "../api";
import { QuickAddBar } from "../components/tasks/QuickAddBar";
import { TaskCard } from "../components/tasks/TaskCard";
import { TaskDetail } from "../components/tasks/TaskDetail";
import { PRIORITY_RANK } from "../components/tasks/taskHelpers";

/**
 * Tasks page — three-pane layout that degrades gracefully:
 *
 *   ≥lg (1024px+)  ┌────────┬───────────┬──────────┐
 *                   │ rail   │ list      │ detail   │  ← hideable via toggle
 *                   └────────┴───────────┴──────────┘
 *
 *   md↔lg          ┌────────┬───────────┐  detail opens as overlay card
 *                   │ rail   │ list      │
 *                   └────────┴───────────┘
 *
 *   <md            [filters] collapsible summary on top, list below, detail
 *                  opens as a full-width overlay. Filter drawer toggles via
 *                  the "☰ Filters" button in the header.
 *
 * Detail-pane-hidden state persists in localStorage so wide-screen users get
 * their preferred layout back across page loads.
 */

type Channel = "mine" | "ai" | "all";
type Bucket = "today" | "upcoming" | "someday" | "done" | "all";

const CHANNEL_LABELS: Record<Channel, { label: string; hint: string }> = {
  mine: { label: "Mine", hint: "Things I dictated" },
  ai: { label: "AI's", hint: "Agent-owned work" },
  all: { label: "Both", hint: "Everything" },
};

const BUCKETS: { id: Bucket; label: string; sub: string }[] = [
  { id: "today", label: "Today", sub: "Due today or overdue" },
  { id: "upcoming", label: "Upcoming", sub: "Due in the future" },
  { id: "someday", label: "Someday", sub: "No due date" },
  { id: "done", label: "Done", sub: "Completed" },
  { id: "all", label: "All", sub: "Everything open" },
];

const DETAIL_OPEN_KEY = "lazyclaw.tasks.detailOpen";

function readDetailOpen(): boolean {
  try {
    const v = localStorage.getItem(DETAIL_OPEN_KEY);
    return v === null ? true : v === "1";
  } catch {
    return true;
  }
}

function writeDetailOpen(open: boolean): void {
  try {
    localStorage.setItem(DETAIL_OPEN_KEY, open ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function groupByDueBucket(tasks: TaskItem[]): Record<string, TaskItem[]> {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate() + 1);
  const weekEnd = new Date(today); weekEnd.setDate(weekEnd.getDate() + 7);

  const out: Record<string, TaskItem[]> = {
    "Overdue": [],
    "Today": [],
    "Tomorrow": [],
    "This week": [],
    "Later": [],
    "Someday": [],
  };

  for (const t of tasks) {
    if (!t.due_date) {
      out["Someday"].push(t);
      continue;
    }
    const d = new Date(`${t.due_date}T00:00:00`);
    if (Number.isNaN(d.getTime())) { out["Someday"].push(t); continue; }
    if (d.getTime() < today.getTime()) out["Overdue"].push(t);
    else if (d.toDateString() === today.toDateString()) out["Today"].push(t);
    else if (d.toDateString() === tomorrow.toDateString()) out["Tomorrow"].push(t);
    else if (d.getTime() < weekEnd.getTime()) out["This week"].push(t);
    else out["Later"].push(t);
  }

  for (const k of Object.keys(out)) {
    out[k].sort((a, b) => {
      const p = PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
      if (p !== 0) return p;
      const ad = a.due_date ? new Date(a.due_date).getTime() : Number.MAX_SAFE_INTEGER;
      const bd = b.due_date ? new Date(b.due_date).getTime() : Number.MAX_SAFE_INTEGER;
      return ad - bd;
    });
  }
  return out;
}

export default function Tasks() {
  const [channel, setChannel] = useState<Channel>("mine");
  const [bucket, setBucket] = useState<Bucket>("today");
  const [search, setSearch] = useState("");
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [detailOpen, setDetailOpen] = useState<boolean>(readDetailOpen);
  const [railDrawerOpen, setRailDrawerOpen] = useState(false);
  const aliveRef = useRef(true);

  useEffect(() => { writeDetailOpen(detailOpen); }, [detailOpen]);

  useEffect(() => {
    aliveRef.current = true;

    const load = async () => {
      try {
        const owner = channel === "mine" ? "user" : channel === "ai" ? "agent" : "all";
        const status = bucket === "done" ? "done" : "todo";
        const apiBucket = bucket === "done" || bucket === "all" ? "all" : bucket;
        const data = await listTasks({ owner, status, bucket: apiBucket });
        if (!aliveRef.current) return;
        setTasks(data);
        if (selectedId && !data.some((t) => t.id === selectedId)) {
          setSelectedId(null);
        }
      } catch {
        if (aliveRef.current) setTasks([]);
      } finally {
        if (aliveRef.current) setLoaded(true);
      }
    };

    load();
    const id = setInterval(load, 30_000);
    return () => { aliveRef.current = false; clearInterval(id); };
  // selectedId intentionally omitted — it causes a reload loop.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel, bucket, reloadTick]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tasks;
    return tasks.filter((t) => {
      if (t.title.toLowerCase().includes(q)) return true;
      if (t.description?.toLowerCase().includes(q)) return true;
      if (t.category?.toLowerCase().includes(q)) return true;
      if (parseSteps(t.steps).some((s) => s.title.toLowerCase().includes(q))) return true;
      return false;
    });
  }, [tasks, search]);

  const grouped = useMemo(() => groupByDueBucket(filtered), [filtered]);
  const selectedTask = tasks.find((t) => t.id === selectedId) ?? null;

  const countsByChannel = useMemo(() => {
    const out = { mine: 0, ai: 0 };
    for (const t of tasks) {
      if (t.status !== "todo") continue;
      if (t.owner === "user") out.mine++;
      else if (t.owner === "agent") out.ai++;
    }
    return out;
  }, [tasks]);

  // When a card is clicked, always try to show the detail pane.
  const onSelectTask = (id: string) => {
    setSelectedId(id);
    setDetailOpen(true);
  };

  const filterRail = (
    <div className="space-y-4">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary mb-2">
          Channel
        </p>
        <div className="flex flex-col gap-1">
          {(Object.keys(CHANNEL_LABELS) as Channel[]).map((c) => {
            const meta = CHANNEL_LABELS[c];
            const count = c === "mine" ? countsByChannel.mine : c === "ai" ? countsByChannel.ai : undefined;
            return (
              <button
                key={c}
                onClick={() => { setChannel(c); setRailDrawerOpen(false); }}
                className={`flex items-center gap-2 px-2.5 py-1.5 rounded-md text-left transition-colors border ${
                  channel === c
                    ? "border-accent/40 bg-accent-soft text-accent"
                    : "border-transparent text-text-secondary hover:bg-bg-hover"
                }`}
              >
                <span className="text-[13px] font-medium flex-1">{meta.label}</span>
                {count !== undefined && count > 0 && (
                  <span className="text-[10px] ticker text-text-muted">{count}</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary mb-2">
          Bucket
        </p>
        <div className="flex flex-col gap-1">
          {BUCKETS.map((b) => (
            <button
              key={b.id}
              onClick={() => { setBucket(b.id); setRailDrawerOpen(false); }}
              className={`text-left px-2.5 py-1.5 rounded-md transition-colors border ${
                bucket === b.id
                  ? "border-accent/40 bg-accent-soft text-accent"
                  : "border-transparent text-text-secondary hover:bg-bg-hover"
              }`}
            >
              <p className="text-[13px] font-medium">{b.label}</p>
              <p className="text-[10px] text-text-muted">{b.sub}</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );

  const listPane = (
    <main className="relative rounded-xl bg-bg-secondary/40 border border-border overflow-y-auto min-h-0">
      {/* Wide-screen detail toggle, pinned to top-right of the list area. */}
      <button
        type="button"
        onClick={() => setDetailOpen((o) => !o)}
        className="hidden lg:flex absolute top-2 right-2 items-center gap-1 text-[10px] uppercase tracking-wider px-2 py-1 rounded border border-border bg-bg-secondary/80 backdrop-blur text-text-muted hover:text-accent hover:border-accent/40 transition-colors z-20"
        title={detailOpen ? "Hide detail pane for a wider list" : "Show the detail pane"}
      >
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
          {detailOpen ? (
            <polyline points="15 6 9 12 15 18" />
          ) : (
            <polyline points="9 6 15 12 9 18" />
          )}
        </svg>
        {detailOpen ? "hide detail" : "show detail"}
      </button>

      {!loaded ? (
        <p className="text-xs text-text-muted text-center py-16">Loading…</p>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center gap-2">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" className="text-text-muted opacity-30">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M7 9l2 2 4-4M7 15l2 2 4-4" />
          </svg>
          <p className="text-xs text-text-muted">
            {search
              ? "Nothing matches that search."
              : bucket === "today"
                ? "Nothing due today. Ahead of the game."
                : "No tasks in this bucket."}
          </p>
          {bucket !== "done" && !search && (
            <p className="text-[10px] text-text-muted max-w-[26ch]">
              Type "tomorrow at 9 buy milk urgent" above and hit ⏎.
            </p>
          )}
        </div>
      ) : (
        Object.entries(grouped).map(([groupName, groupTasks]) => {
          if (groupTasks.length === 0) return null;
          return (
            <section key={groupName}>
              <header className="flex items-center gap-2 px-4 py-2 bg-bg-tertiary/40 border-b border-border/60 sticky top-0 backdrop-blur-sm z-10">
                <p className={`text-[10px] font-semibold uppercase tracking-[0.12em] ${
                  groupName === "Overdue" ? "text-red-400" :
                  groupName === "Today" ? "text-amber" :
                  "text-text-secondary"
                }`}>
                  {groupName}
                </p>
                <span className="text-[10px] ticker text-text-muted">{groupTasks.length}</span>
              </header>
              {groupTasks.map((t) => (
                <TaskCard
                  key={t.id}
                  task={t}
                  selected={selectedId === t.id}
                  onClick={() => onSelectTask(t.id)}
                  onChanged={() => setReloadTick((n) => n + 1)}
                />
              ))}
            </section>
          );
        })
      )}
    </main>
  );

  const detailPane = selectedTask ? (
    <TaskDetail
      task={selectedTask}
      onChanged={() => setReloadTick((n) => n + 1)}
    />
  ) : (
    <div className="rounded-xl bg-bg-secondary/40 border border-dashed border-border p-8 text-center text-sm text-text-muted min-h-[320px] flex flex-col items-center justify-center gap-2">
      <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" className="opacity-30">
        <path d="M9 11l3 3L22 4" />
        <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
      </svg>
      <p>Pick a task on the left.</p>
      <p className="text-[10px] text-text-muted max-w-[22ch]">
        Full control: title, priority, due date, reminders, steps, tags, notes.
      </p>
    </div>
  );

  // Grid template switches based on detailOpen for lg+ screens.
  const lgGrid = detailOpen
    ? "lg:grid-cols-[180px_1fr_420px]"
    : "lg:grid-cols-[180px_1fr]";

  return (
    <div className="h-full overflow-hidden grid-bg">
      <div className="h-full max-w-[1400px] mx-auto px-4 sm:px-5 py-4 sm:py-5 flex flex-col gap-4">

        {/* Header */}
        <header className="flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold text-text-primary">Tasks</h1>
          <p className="hidden sm:block text-[11px] text-text-muted">
            Encrypted · NL time detection · markdown notes · AI sub-steps
          </p>
          <div className="ml-auto flex items-center gap-2">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search…"
              className="w-40 sm:w-64 px-3 py-1.5 text-xs rounded-md bg-bg-tertiary border border-border text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-accent/40"
            />
            <button
              type="button"
              onClick={() => setRailDrawerOpen((o) => !o)}
              className="lg:hidden text-[11px] uppercase tracking-wider px-2.5 py-1.5 rounded-md bg-bg-tertiary border border-border text-text-secondary hover:text-accent hover:border-accent/40 transition-colors"
              aria-expanded={railDrawerOpen}
              aria-controls="tasks-filter-drawer"
            >
              ☰ Filters
            </button>
          </div>
        </header>

        {/* Quick add */}
        <QuickAddBar onAdded={() => setReloadTick((n) => n + 1)} />

        {/* Mobile filter drawer (below lg) */}
        {railDrawerOpen && (
          <div
            id="tasks-filter-drawer"
            className="lg:hidden rounded-xl bg-bg-secondary/60 border border-border p-4"
          >
            {filterRail}
          </div>
        )}

        {/* 3-pane (lg) / 2-pane (md) / stacked (sm) */}
        <div className={`flex-1 grid grid-cols-1 ${lgGrid} gap-4 min-h-0 relative`}>
          {/* Filter rail — only visible at lg+. Narrow screens use the drawer above. */}
          <aside className="hidden lg:block">
            {filterRail}
          </aside>

          {/* List */}
          {listPane}

          {/* Detail pane (lg+ only when open) */}
          {detailOpen && (
            <aside className="hidden lg:block min-h-0 overflow-y-auto">
              {detailPane}
            </aside>
          )}

          {/* Narrow-screen overlay detail (<lg) when a task is selected */}
          {selectedTask && (
            <div className="lg:hidden absolute inset-0 z-30 bg-bg-primary/95 backdrop-blur-sm rounded-xl overflow-y-auto p-3">
              <div className="flex items-center gap-2 mb-3">
                <button
                  type="button"
                  onClick={() => setSelectedId(null)}
                  className="text-[11px] uppercase tracking-wider px-2.5 py-1 rounded-md bg-bg-tertiary border border-border text-text-secondary hover:text-accent hover:border-accent/40 transition-colors"
                >
                  ← back
                </button>
                <span className="text-[10px] text-text-muted">task detail</span>
              </div>
              {detailPane}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
