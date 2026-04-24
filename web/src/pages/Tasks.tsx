import { useEffect, useMemo, useRef, useState } from "react";
import type { TaskItem } from "../api";
import { listTasks, parseSteps } from "../api";
import { QuickAddBar } from "../components/tasks/QuickAddBar";
import { TaskCard } from "../components/tasks/TaskCard";
import { TaskDetail } from "../components/tasks/TaskDetail";
import { PRIORITY_RANK } from "../components/tasks/taskHelpers";

/**
 * Tasks page — three-pane layout:
 *   ┌────────────┬─────────────────┬───────────────────┐
 *   │  Filters   │   Task list     │   Task detail     │
 *   │  (rail)    │   (central)     │   (right pane)    │
 *   └────────────┴─────────────────┴───────────────────┘
 *
 * The rail offers owner + bucket filters. The central list renders cards with
 * priority gutters. The right pane is full control over the selected task —
 * inline title edit, priority chips, due/remind pickers, steps, tags, notes.
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

  // Sort each bucket by priority, then by date.
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
  const aliveRef = useRef(true);

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
        // If the current selection is no longer in the list, keep it if we
        // have it elsewhere — otherwise clear.
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
  // Note: selectedId intentionally omitted — it causes a reload loop.
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

  // Summary counters for the filter rail.
  const countsByChannel = useMemo(() => {
    const out = { mine: 0, ai: 0 };
    for (const t of tasks) {
      if (t.status !== "todo") continue;
      if (t.owner === "user") out.mine++;
      else if (t.owner === "agent") out.ai++;
    }
    return out;
  }, [tasks]);

  return (
    <div className="h-full overflow-hidden grid-bg">
      <div className="h-full max-w-[1400px] mx-auto px-5 py-5 flex flex-col gap-4">

        {/* Header */}
        <header className="flex flex-wrap items-baseline gap-3">
          <h1 className="text-lg font-semibold text-text-primary">Tasks</h1>
          <p className="text-[11px] text-text-muted">
            Encrypted todos · NL time detection · sub-task steps
          </p>
          <div className="ml-auto flex items-center gap-2">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search title, notes, tags, steps…"
              className="w-64 px-3 py-1.5 text-xs rounded-md bg-bg-tertiary border border-border text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-accent/40"
            />
          </div>
        </header>

        {/* Quick add */}
        <QuickAddBar onAdded={() => setReloadTick((n) => n + 1)} />

        {/* 3-pane */}
        <div className="flex-1 grid grid-cols-1 lg:grid-cols-[180px_1fr_420px] gap-4 min-h-0">

          {/* Filters rail */}
          <aside className="space-y-4">
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
                      onClick={() => setChannel(c)}
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
                    onClick={() => setBucket(b.id)}
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
          </aside>

          {/* List */}
          <main className="rounded-xl bg-bg-secondary/40 border border-border overflow-y-auto min-h-0">
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
                        onClick={() => setSelectedId(t.id)}
                        onChanged={() => setReloadTick((n) => n + 1)}
                      />
                    ))}
                  </section>
                );
              })
            )}
          </main>

          {/* Detail pane */}
          <aside className="min-h-0 overflow-y-auto">
            {selectedTask ? (
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
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
