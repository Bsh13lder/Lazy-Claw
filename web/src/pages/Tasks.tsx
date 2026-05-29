import { useEffect, useMemo, useRef, useState } from "react";
import type { TaskItem } from "../api";
import { listTasks, parseSteps } from "../api";
import { QuickAddBar } from "../components/tasks/QuickAddBar";
import { TaskCard } from "../components/tasks/TaskCard";
import { TaskDetail } from "../components/tasks/TaskDetail";
import { PRIORITY_RANK } from "../components/tasks/taskHelpers";
import { NotesView } from "../components/notes/NotesView";
import { ProjectBudgetRail } from "../components/budgets/ProjectBudgetRail";
import { ProjectPanel } from "../components/budgets/ProjectPanel";
import { ExpensesView } from "../components/budgets/ExpensesView";

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
const TAB_KEY = "lazyclaw.tasks.tab";

type WorkspaceTab = "tasks" | "notes" | "expenses";

const WORKSPACE_TABS: readonly WorkspaceTab[] = ["tasks", "notes", "expenses"];
const isWorkspaceTab = (v: string | null): v is WorkspaceTab =>
  v != null && (WORKSPACE_TABS as readonly string[]).includes(v);

function readActiveTab(): WorkspaceTab {
  // URL wins so deep-links land on the right tab.
  if (typeof window !== "undefined") {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (isWorkspaceTab(t)) return t;
  }
  try {
    const v = localStorage.getItem(TAB_KEY);
    if (isWorkspaceTab(v)) return v;
  } catch {
    /* ignore */
  }
  return "tasks";
}
function writeActiveTab(t: WorkspaceTab) {
  try {
    localStorage.setItem(TAB_KEY, t);
  } catch {
    /* ignore */
  }
  if (typeof window !== "undefined") {
    const url = new URL(window.location.href);
    if (t === "tasks") url.searchParams.delete("tab");
    else url.searchParams.set("tab", t);
    window.history.replaceState(null, "", url.toString());
  }
}

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

// Collapsible rail sections (Channel / Bucket) — open by default, persisted.
const RAIL_SECTION_KEY = "lazyclaw.tasks.rail";
function readSectionOpen(section: string, def = true): boolean {
  try {
    const v = localStorage.getItem(`${RAIL_SECTION_KEY}.${section}.open`);
    return v === null ? def : v === "1";
  } catch {
    return def;
  }
}
function writeSectionOpen(section: string, open: boolean): void {
  try {
    localStorage.setItem(`${RAIL_SECTION_KEY}.${section}.open`, open ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function renderEmptyState({
  bucket,
  search,
  channel,
}: {
  bucket: Bucket;
  search: string;
  channel: Channel;
}) {
  if (search) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center gap-3 px-6">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" className="text-text-muted opacity-40">
          <circle cx="11" cy="11" r="8" />
          <path d="M21 21l-4.3-4.3M8 11h6" />
        </svg>
        <p className="text-[12px] text-text-secondary">No matches for "{search.slice(0, 36)}".</p>
        <p className="text-[10.5px] text-text-muted max-w-[28ch] leading-relaxed">
          Search scans titles, descriptions, categories, and steps. Try a single keyword.
        </p>
      </div>
    );
  }

  if (bucket === "done") {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center gap-3 px-6">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" className="text-text-muted opacity-40">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M9 12h6M9 8h6M9 16h6" />
        </svg>
        <p className="text-[12px] text-text-secondary">Nothing finished yet.</p>
        <p className="text-[10.5px] text-text-muted max-w-[28ch] leading-relaxed">
          Once you tick tasks off, they land here. The Done bucket keeps the last 90 days for review.
        </p>
      </div>
    );
  }

  if (bucket === "today") {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center gap-3 px-6">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.1" className="text-accent/50">
          <circle cx="12" cy="12" r="9" />
          <path d="M9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <p className="text-[12px] text-text-secondary">Inbox zero. Nothing's due today.</p>
        <p className="text-[10.5px] text-text-muted max-w-[28ch] leading-relaxed">
          Pull something forward from <span className="text-accent">Upcoming</span>, or capture
          a new one with the bar above — "tomorrow 9am buy milk urgent".
        </p>
      </div>
    );
  }

  if (bucket === "upcoming") {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center gap-3 px-6">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" className="text-text-muted opacity-40">
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <path d="M16 2v4M8 2v4M3 10h18" />
        </svg>
        <p className="text-[12px] text-text-secondary">Nothing scheduled ahead.</p>
        <p className="text-[10.5px] text-text-muted max-w-[28ch] leading-relaxed">
          The road's clear. Add a future task with a phrase like "next Monday 14:00 review PRD".
        </p>
      </div>
    );
  }

  if (bucket === "someday") {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center gap-3 px-6">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" className="text-text-muted opacity-40">
          <path d="M12 2v4M12 18v4M2 12h4M18 12h4M5 5l3 3M16 16l3 3M5 19l3-3M16 8l3-3" />
        </svg>
        <p className="text-[12px] text-text-secondary">No undated tasks.</p>
        <p className="text-[10.5px] text-text-muted max-w-[28ch] leading-relaxed">
          Add things you'll get to "eventually" by typing a title without a time.
        </p>
      </div>
    );
  }

  // bucket === "all"
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center gap-3 px-6">
      <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" className="text-text-muted opacity-40">
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M7 9l2 2 4-4M7 15l2 2 4-4" />
      </svg>
      <p className="text-[12px] text-text-secondary">No tasks {channel === "ai" ? "for the agent" : channel === "mine" ? "of yours" : ""} yet.</p>
      <p className="text-[10.5px] text-text-muted max-w-[28ch] leading-relaxed">
        Use the bar above to dictate one — natural language only, no forms.
      </p>
    </div>
  );
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

function SectionHeader({
  label, open, onToggle,
}: { label: string; open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="w-full flex items-center gap-1.5 mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary hover:text-text-primary transition-colors"
    >
      <svg
        width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
        className={`transition-transform ${open ? "rotate-90" : ""}`}
      >
        <polyline points="9 18 15 12 9 6" />
      </svg>
      <span>{label}</span>
    </button>
  );
}

export default function Tasks() {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>(readActiveTab);
  const [channel, setChannel] = useState<Channel>("mine");
  const [bucket, setBucket] = useState<Bucket>("today");
  const [search, setSearch] = useState("");
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [detailOpen, setDetailOpen] = useState<boolean>(readDetailOpen);
  const [railDrawerOpen, setRailDrawerOpen] = useState(false);
  const [projectFilter, setProjectFilter] = useState<string | null>(null);
  const [channelOpen, setChannelOpen] = useState(() => readSectionOpen("channel"));
  const [bucketOpen, setBucketOpen] = useState(() => readSectionOpen("bucket"));
  const aliveRef = useRef(true);

  const toggleChannel = () => {
    const next = !channelOpen;
    setChannelOpen(next);
    writeSectionOpen("channel", next);
  };
  const toggleBucket = () => {
    const next = !bucketOpen;
    setBucketOpen(next);
    writeSectionOpen("bucket", next);
  };

  useEffect(() => { writeDetailOpen(detailOpen); }, [detailOpen]);
  useEffect(() => { writeActiveTab(activeTab); }, [activeTab]);

  // React to popstate so back/forward buttons swap the tab cleanly.
  useEffect(() => {
    const onPop = () => setActiveTab(readActiveTab());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

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
    return tasks.filter((t) => {
      if (projectFilter && (t.category || "").trim().toLowerCase() !== projectFilter) {
        return false;
      }
      if (!q) return true;
      if (t.title.toLowerCase().includes(q)) return true;
      if (t.description?.toLowerCase().includes(q)) return true;
      if (t.category?.toLowerCase().includes(q)) return true;
      if (parseSteps(t.steps).some((s) => s.title.toLowerCase().includes(q))) return true;
      return false;
    });
  }, [tasks, search, projectFilter]);

  // Distinct task categories feed the Projects rail's auto-detect — any
  // category typed on a task shows up as a project even before it has a budget.
  const categories = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const t of tasks) {
      const c = (t.category || "").trim();
      if (!c) continue;
      const k = c.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(c);
    }
    return out;
  }, [tasks]);

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
        <SectionHeader label="Channel" open={channelOpen} onToggle={toggleChannel} />
        {channelOpen && (
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
        )}
      </div>

      <div>
        <SectionHeader label="Bucket" open={bucketOpen} onToggle={toggleBucket} />
        {bucketOpen && (
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
        )}
      </div>

      <ProjectBudgetRail
        categories={categories}
        selectedKey={projectFilter}
        onSelect={(key) => {
          setProjectFilter(key);
          // Show ALL of a project's tasks regardless of due bucket when
          // drilling into it, so the project view isn't hidden by "Today".
          if (key) setBucket("all");
          setRailDrawerOpen(false);
        }}
        reloadKey={reloadTick}
      />
    </div>
  );

  const emptyState = renderEmptyState({ bucket, search, channel });
  const groupRule: Record<string, string> = {
    Overdue: "before:bg-red-400/40",
    Today: "before:bg-amber/40",
    Tomorrow: "before:bg-accent/30",
    "This week": "before:bg-text-muted/20",
    Later: "before:bg-text-muted/10",
    Someday: "before:bg-text-muted/10",
  };
  const groupTone: Record<string, string> = {
    Overdue: "text-red-400",
    Today: "text-amber",
    Tomorrow: "text-accent",
    "This week": "text-text-secondary",
    Later: "text-text-secondary",
    Someday: "text-text-muted",
  };

  const listPane = (
    <main className="relative rounded-xl bg-bg-secondary/30 ring-1 ring-border/50 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)] overflow-y-auto min-h-0">
      {/* Wide-screen detail toggle, pinned to top-right of the list area. */}
      <button
        type="button"
        onClick={() => setDetailOpen((o) => !o)}
        className="hidden lg:flex absolute top-2 right-2 items-center gap-1 text-[10px] uppercase tracking-wider px-2 py-1 rounded border border-border/60 bg-bg-secondary/80 backdrop-blur text-text-muted hover:text-accent hover:border-accent/40 transition-colors z-20"
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

      {projectFilter && (
        <ProjectPanel
          projectKey={projectFilter}
          displayName={
            categories.find((c) => c.toLowerCase() === projectFilter) ?? projectFilter
          }
          onChanged={() => setReloadTick((n) => n + 1)}
          onDeleted={() => { setProjectFilter(null); setReloadTick((n) => n + 1); }}
        />
      )}

      {!loaded ? (
        <p className="text-xs text-text-muted text-center py-16 italic">Loading tasks…</p>
      ) : filtered.length === 0 ? emptyState : (
        Object.entries(grouped).map(([groupName, groupTasks]) => {
          if (groupTasks.length === 0) return null;
          return (
            <section key={groupName}>
              <header
                className={`relative flex items-center gap-2 px-4 py-1.5 bg-bg-tertiary/30 border-b border-border/40 sticky top-0 backdrop-blur-md z-10 before:content-[''] before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:h-[2px] before:w-3 ${groupRule[groupName] ?? "before:bg-text-muted/10"}`}
              >
                <p className={`text-[10px] font-semibold uppercase tracking-[0.14em] pl-3 ${groupTone[groupName] ?? "text-text-secondary"}`}>
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
    <div className="rounded-xl bg-bg-secondary/30 ring-1 ring-border/40 ring-dashed p-8 text-center text-sm text-text-muted min-h-[320px] flex flex-col items-center justify-center gap-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
      <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.1" className="text-accent/40">
        <path d="M9 11l3 3L22 4" />
        <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
      </svg>
      <p className="text-[12px] text-text-secondary">Pick a task on the left.</p>
      <p className="text-[10.5px] text-text-muted max-w-[24ch] leading-relaxed">
        Detail pane handles title, priority, deadline, reschedule, steps, tags, and notes — all inline.
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

        {/* Workspace header — tab strip lives where the H1 used to.
            "Tasks" / "Notes" toggle keeps both surfaces side-by-side
            without leaving the page. URL ?tab=notes deep-links the
            Notes tab so Telegram links land where expected. */}
        <header className="flex flex-wrap items-center gap-3 border-b border-border/40 pb-3">
          <div className="flex items-center gap-1 bg-bg-tertiary/40 rounded-lg p-0.5">
            <button
              type="button"
              onClick={() => setActiveTab("tasks")}
              className={`px-3 py-1.5 rounded-md text-[14px] font-semibold transition-colors flex items-center gap-2 ${
                activeTab === "tasks"
                  ? "bg-bg-primary text-text-primary shadow-[0_1px_0_rgba(255,255,255,0.04)_inset]"
                  : "text-text-muted hover:text-text-primary"
              }`}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={activeTab === "tasks" ? "text-accent" : ""}>
                <path d="M9 11l3 3L22 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
              Tasks
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("notes")}
              className={`px-3 py-1.5 rounded-md text-[14px] font-semibold transition-colors flex items-center gap-2 ${
                activeTab === "notes"
                  ? "bg-bg-primary text-text-primary shadow-[0_1px_0_rgba(255,255,255,0.04)_inset]"
                  : "text-text-muted hover:text-text-primary"
              }`}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={activeTab === "notes" ? "text-accent" : ""}>
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6M9 13h6M9 17h6" />
              </svg>
              Notes
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("expenses")}
              className={`px-3 py-1.5 rounded-md text-[14px] font-semibold transition-colors flex items-center gap-2 ${
                activeTab === "expenses"
                  ? "bg-bg-primary text-text-primary shadow-[0_1px_0_rgba(255,255,255,0.04)_inset]"
                  : "text-text-muted hover:text-text-primary"
              }`}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={activeTab === "expenses" ? "text-accent" : ""}>
                <line x1="12" y1="1" x2="12" y2="23" />
                <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
              </svg>
              Expenses
            </button>
          </div>

          <p className="hidden sm:block text-[11px] text-text-muted">
            {activeTab === "tasks"
              ? "Encrypted · NL time · markdown · AI sub-steps"
              : activeTab === "notes"
              ? "Encrypted · markdown · [[wikilinks]] · no AI"
              : "Encrypted · grouped by project · log & delete spend"}
          </p>

          {activeTab === "tasks" && (
            <div className="ml-auto flex items-center gap-2">
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search tasks…"
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
          )}
        </header>

        {activeTab === "tasks" ? (
          <>
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
              <aside className="hidden lg:block">
                {filterRail}
              </aside>

              {listPane}

              {detailOpen && (
                <aside className="hidden lg:block min-h-0 overflow-y-auto">
                  {detailPane}
                </aside>
              )}

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
          </>
        ) : activeTab === "notes" ? (
          <div className="flex-1 min-h-0 overflow-y-auto">
            <NotesView variant="embedded" />
          </div>
        ) : (
          <div className="flex-1 min-h-0 overflow-y-auto">
            <ExpensesView onChanged={() => setReloadTick((n) => n + 1)} />
          </div>
        )}
      </div>
    </div>
  );
}
