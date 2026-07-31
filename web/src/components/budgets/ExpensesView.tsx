import { useEffect, useMemo, useState } from "react";
import * as api from "../../api";
import type { Expense, InboxSuggestion, Project, TaskItem } from "../../api";
import { fmtMoney } from "./money";
import { ExpenseRow } from "./ExpenseRow";
import { ProjectExpenseAdder } from "./ExpenseAdder";

/**
 * Global Expenses workspace tab — every expense across all projects, grouped
 * by project with per-project subtotals + a grand total. An add-expense form
 * picks the project (incl. the catch-all "General"). Mirrors how chat-captured
 * expenses land: a lone "spent 12 on coffee" shows up under General here.
 */

interface Group {
  projectId: string;
  name: string;
  currency: string;
  rows: Expense[];
  subtotal: number;
}

export function ExpensesView({ onChanged }: { onChanged?: () => void }) {
  const [expenses, setExpenses] = useState<Expense[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [tick, setTick] = useState(0);
  const [adding, setAdding] = useState(false);
  const [addProject, setAddProject] = useState<string>("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [starredOnly, setStarredOnly] = useState(false);
  const [inboxOnly, setInboxOnly] = useState(false);
  // Surfaced near the totals block below — a failed assign (e.g. the target
  // project got deleted elsewhere) must never be swallowed silently.
  const [assignError, setAssignError] = useState<string | null>(null);

  // --- Bulk select + bulk assign + AI auto-assign (inbox-only bulk bar) ---
  // Kept deliberately separate from `assignError` above: that state is the
  // single-row block-and-retry banner (Task 3). Bulk operations are
  // partial-failure by nature ("moved 8 of 10") so they get their own
  // summary state instead of reusing the single-row error slot.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkTargetProjectId, setBulkTargetProjectId] = useState("");
  const [bulkAssignBusy, setBulkAssignBusy] = useState(false);
  const [bulkAssignResult, setBulkAssignResult] = useState<
    { total: number; moved: number; failures: string[] } | null
  >(null);
  const [suggestions, setSuggestions] = useState<Map<string, InboxSuggestion>>(new Map());
  const [suggestSkipped, setSuggestSkipped] = useState(0);
  const [suggestBusy, setSuggestBusy] = useState(false);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const [applyBusy, setApplyBusy] = useState(false);
  const [applyingIds, setApplyingIds] = useState<Set<string>>(new Set());
  const [applyResult, setApplyResult] = useState<
    { total: number; moved: number; failures: string[] } | null
  >(null);

  useEffect(() => {
    let alive = true;
    // Tasks are fetched unfiltered (every owner/status/bucket) so the assign
    // panel's task dropdown can match ANY task by category, not just the
    // current user's open todos — mirrors the same `listTasks` call other
    // budget surfaces already make, just with wider filters.
    Promise.all([
      api.listAllExpenses(),
      api.listProjects("all"),
      api.listTasks({ owner: "all", status: "all", bucket: "all" }),
    ])
      .then(([ex, ps, ts]) => { if (alive) { setExpenses(ex); setProjects(ps); setTasks(ts); } })
      .catch(() => { if (alive) { setExpenses([]); setProjects([]); setTasks([]); } });
    return () => { alive = false; };
  }, [tick]);

  // Refetch when the user returns to this tab/window. Expenses land on the
  // server out-of-band (a mobile sync push), and this view is otherwise a
  // one-shot fetch that stays stale until a full reload — so a phone-added
  // expense looked "missing on Web" even after it synced. Cheap: two GETs when
  // focus/visibility is regained, not a poll.
  useEffect(() => {
    const refetch = () => setTick((n) => n + 1);
    const onVisible = () => {
      if (document.visibilityState === "visible") refetch();
    };
    window.addEventListener("focus", refetch);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", refetch);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  // Poll while this tab is visible so an expense added on another client (a
  // phone sync push, or the agent) shows up WITHOUT needing to refocus — the
  // focus/visibility effect above only fires on return-to-tab, so a
  // continuously-watched tab stayed stale. Mirrors the Tasks page's 30s poll;
  // paused while hidden to avoid needless load and background-tab throttling.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((n) => n + 1);
    }, 30_000);
    return () => window.clearInterval(id);
  }, []);

  const refresh = () => { setTick((n) => n + 1); onChanged?.(); };

  const currencyByProject = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of projects) m.set(p.id, p.currency || "EUR");
    return m;
  }, [projects]);

  // Inbox = the catch-all "General" project. Detected by name_key, never the
  // (renameable) display name.
  const inboxProject = useMemo(
    () => projects.find((p) => p.name_key === "general"),
    [projects],
  );
  const inboxCount = useMemo(
    () => (expenses || []).filter((e) => e.project_id === inboxProject?.id).length,
    [expenses, inboxProject],
  );

  // Assign targets: every real project, General excluded — you can't assign
  // an inbox expense back into the inbox.
  const assignableProjects = useMemo(
    () => projects.filter((p) => p.id !== inboxProject?.id),
    [projects, inboxProject],
  );

  // Membership set for the inbox — every General-project expense id,
  // regardless of the starred filter (that's a display filter, not an inbox
  // membership fact). Used below to prune selection + suggestions once an
  // expense actually leaves the inbox by ANY path.
  const generalExpenseIds = useMemo(
    () => new Set((expenses || []).filter((e) => e.project_id === inboxProject?.id).map((e) => e.id)),
    [expenses, inboxProject],
  );

  // Selection and AI-suggestion state both key off individual expense ids.
  // Once an id leaves the inbox — single-row assign, bulk assign, an applied
  // suggestion, a deletion, or even another client's change picked up by the
  // background poll — its checkbox AND any stale "→ ProjectX" suggestion
  // must vanish too, or a leftover suggestion could later be applied to an
  // expense that has already moved (or been reassigned to something else).
  useEffect(() => {
    setSelected((prev) => {
      let changed = false;
      const next = new Set<string>();
      for (const id of prev) {
        if (generalExpenseIds.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
    setSuggestions((prev) => {
      let changed = false;
      const next = new Map<string, InboxSuggestion>();
      for (const [id, s] of prev) {
        if (generalExpenseIds.has(id)) next.set(id, s);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [generalExpenseIds]);

  // Leaving inbox-only mode clears the whole bulk-selection UI so re-entering
  // it later starts clean rather than resurrecting a stale pick/summary.
  useEffect(() => {
    if (!inboxOnly) {
      setSelected(new Set());
      setBulkTargetProjectId("");
      setBulkAssignResult(null);
      setSuggestions(new Map());
      setSuggestSkipped(0);
      setSuggestError(null);
      setApplyResult(null);
    }
  }, [inboxOnly]);

  // ONE shared mutation lock for the whole bulk family (bulk-assign, the
  // suggestion fetch, and apply/apply-all) plus the single-row Assign path —
  // all of them can PATCH the same inbox rows, so only one may run at a
  // time. Individual flags (`bulkAssignBusy`/`suggestBusy`/`applyBusy`) are
  // kept for per-button spinner labels, but every guard clause and every
  // disabled= below must check `anyBulkBusy`, never its own flag alone.
  const anyBulkBusy = bulkAssignBusy || suggestBusy || applyBusy;

  // Failure summaries need to name WHICH expense failed, or two failures
  // render as identical strings ("Network error", "Network error", …).
  const expenseLabel = (id: string): string => {
    const e = (expenses || []).find((x) => x.id === id);
    if (!e) return id;
    return e.description || e.vendor || fmtMoney(e.amount, e.currency);
  };

  // Single-expense assign, wired per-row below (General group only). Guarded
  // on `anyBulkBusy` too — a bulk write in flight touches the same rows, so
  // a concurrent single-row PATCH would race it just like two bulk loops
  // would. Rethrows after recording the error so ExpenseRow's own
  // busy/panel state knows the attempt failed and keeps the picker open.
  const assignExpense = async (expenseId: string, projectId: string, taskId: string | null) => {
    if (anyBulkBusy) {
      setAssignError("A bulk operation is running — wait for it to finish and try again.");
      throw new Error("bulk operation in progress");
    }
    setAssignError(null);
    try {
      await api.updateExpense(expenseId, { project_id: projectId, task_id: taskId });
      refresh();
    } catch (e) {
      setAssignError(e instanceof Error ? e.message : String(e));
      throw e;
    }
  };

  // Bulk assign: sequential PATCH loop over the current selection into one
  // target project. Sequential (not Promise.all) because counts here are at
  // most a few dozen and sequential keeps a failure attributable to a
  // specific id rather than an unordered Promise.allSettled dump.
  const runBulkAssign = async () => {
    if (anyBulkBusy || selected.size === 0 || !bulkTargetProjectId) return;
    setBulkAssignBusy(true);
    setBulkAssignResult(null);
    const ids = Array.from(selected);
    const failures: string[] = [];
    let moved = 0;
    for (const id of ids) {
      try {
        await api.updateExpense(id, { project_id: bulkTargetProjectId });
        moved++;
      } catch (e) {
        failures.push(`${expenseLabel(id)}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    refresh();
    setBulkAssignResult({ total: ids.length, moved, failures });
    setBulkAssignBusy(false);
  };

  // Auto-assign: fetch AI suggestions for the selection (or, with nothing
  // selected, the whole inbox — capped at 10 server-side). Suggestions render
  // per-row via the `suggestions` map; nothing is applied here. Still gated
  // on `anyBulkBusy` — a bulk-assign or apply loop in flight can move the
  // very rows a suggestion fetch is about to describe.
  const runAutoAssign = async () => {
    if (anyBulkBusy) return;
    setSuggestBusy(true);
    setSuggestError(null);
    try {
      const ids = selected.size > 0 ? Array.from(selected) : undefined;
      const { suggestions: sugs, skipped } = await api.getInboxSuggestions(ids);
      const m = new Map<string, InboxSuggestion>();
      for (const s of sugs) m.set(s.expense_id, s);
      setSuggestions(m);
      setSuggestSkipped(skipped);
    } catch (e) {
      setSuggestError(e instanceof Error ? e.message : String(e));
    } finally {
      setSuggestBusy(false);
    }
  };

  // Shared apply path for both the per-row "Apply" button (a single-item
  // array) and "Apply all confident" (every high/medium suggestion with a
  // non-null project_id). Guarded on `anyBulkBusy` (not just `applyBusy`) so
  // a per-row Apply, "Apply all confident", a running bulk-assign, and an
  // in-flight suggestion fetch can never race the same rows from two
  // directions at once.
  const applySuggestions = async (items: InboxSuggestion[]) => {
    const applicable = items.filter((s) => s.project_id);
    if (applicable.length === 0 || anyBulkBusy) return;
    setApplyBusy(true);
    setApplyResult(null);
    setApplyingIds(new Set(applicable.map((s) => s.expense_id)));
    const failures: string[] = [];
    let moved = 0;
    for (const s of applicable) {
      try {
        await api.updateExpense(s.expense_id, { project_id: s.project_id as string });
        moved++;
        // Drop it immediately so a re-render before the refetch lands can't
        // show an already-applied suggestion as still actionable.
        setSuggestions((prev) => {
          const next = new Map(prev);
          next.delete(s.expense_id);
          return next;
        });
      } catch (e) {
        failures.push(`${expenseLabel(s.expense_id)}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    refresh();
    setApplyResult({ total: applicable.length, moved, failures });
    setApplyingIds(new Set());
    setApplyBusy(false);
  };

  // Auto-reset the filter once its own result set empties out (last inbox
  // expense got assigned or deleted elsewhere) — otherwise the user is
  // stranded looking at an empty list with no visible way out. Adjusted
  // synchronously during render (React's documented pattern for resetting
  // state in response to a state change) rather than in a useEffect, which
  // would cause an extra committed render just to flip the filter back off.
  //
  // Gated on `expenses.length > 0`: the refetch's `.catch` (above) sets
  // `expenses` to `[]` on ANY fetch error, which would otherwise make
  // `inboxCount === 0` look like a genuinely emptied inbox and silently
  // clear the user's filter choice on a transient network blip — only to
  // have the data (and their still-active filter intent) reappear on the
  // next successful poll, with no error ever surfaced. A non-empty expense
  // list proves the fetch actually succeeded, so a true 0 here is trustworthy.
  // (A brand-new user with zero expenses everywhere can't have `inboxOnly`
  // true in the first place — the chip only renders once `inboxCount > 0`.)
  const [prevInboxCount, setPrevInboxCount] = useState(inboxCount);
  if (inboxCount !== prevInboxCount) {
    setPrevInboxCount(inboxCount);
    if (inboxOnly && (expenses?.length ?? 0) > 0 && inboxCount === 0) setInboxOnly(false);
  }

  const groups = useMemo<Group[]>(() => {
    if (!expenses) return [];
    const src = expenses.filter((e) =>
      (!starredOnly || e.is_favorite) &&
      (!inboxOnly || e.project_id === inboxProject?.id));
    const byId = new Map<string, Group>();
    for (const e of src) {
      const pid = e.project_id;
      let g = byId.get(pid);
      if (!g) {
        g = {
          projectId: pid,
          name: e.project_name || "(unknown project)",
          currency: currencyByProject.get(pid) || e.currency || "EUR",
          rows: [],
          subtotal: 0,
        };
        byId.set(pid, g);
      }
      g.rows.push(e);
      g.subtotal += e.amount || 0;
    }
    const list = Array.from(byId.values());
    list.sort((a, b) => {
      // Pin the inbox (General) group first, unfiltered only — filtered views
      // already show a single group, so pinning would be a no-op there.
      if (!inboxOnly && inboxProject) {
        if (a.projectId === inboxProject.id) return -1;
        if (b.projectId === inboxProject.id) return 1;
      }
      return a.name.localeCompare(b.name);
    });
    return list;
  }, [expenses, currencyByProject, starredOnly, inboxOnly, inboxProject]);

  // The currently VISIBLE inbox row ids — i.e. `groups`' General group,
  // which already has the starred filter folded in when both are active.
  // This (not `generalExpenseIds` above) is what "Select all" toggles,
  // matching the brief's "reflects the visible inbox set".
  const inboxRowIds = useMemo(() => {
    if (!inboxOnly || !inboxProject) return [];
    const g = groups.find((x) => x.projectId === inboxProject.id);
    return g ? g.rows.map((r) => r.id) : [];
  }, [groups, inboxOnly, inboxProject]);

  const allInboxSelected = inboxRowIds.length > 0 && inboxRowIds.every((id) => selected.has(id));

  const toggleSelect = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleSelectAll = () =>
    setSelected((prev) => {
      if (inboxRowIds.length > 0 && inboxRowIds.every((id) => prev.has(id))) return new Set();
      return new Set(inboxRowIds);
    });

  // Suggestions eligible for the one-click "Apply all confident" — high or
  // medium confidence AND an actual project match (never "no match" rows).
  const confidentSuggestions = useMemo(
    () => Array.from(suggestions.values()).filter(
      (s) => s.project_id && (s.confidence === "high" || s.confidence === "medium"),
    ),
    [suggestions],
  );

  // Total of the currently-shown set (starred/inbox filters applied), split
  // per currency — no FX conversion (matches spending report).
  const totalsByCurrency = useMemo(() => {
    const src = (expenses || []).filter((e) =>
      (!starredOnly || e.is_favorite) &&
      (!inboxOnly || e.project_id === inboxProject?.id));
    const m = new Map<string, number>();
    for (const e of src) {
      const c = e.currency || "EUR";
      m.set(c, (m.get(c) || 0) + (e.amount || 0));
    }
    return Array.from(m.entries());
  }, [expenses, starredOnly, inboxOnly, inboxProject]);

  // Always-visible starred subtotal, so the "★" total shows even without the
  // filter on — this is the "starred only" overview number the user wanted.
  const starredTotalsByCurrency = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of (expenses || []).filter((x) => x.is_favorite)) {
      const c = e.currency || "EUR";
      m.set(c, (m.get(c) || 0) + (e.amount || 0));
    }
    return Array.from(m.entries());
  }, [expenses]);

  // Dropdown options: General first, then known projects, then any project a
  // (legacy) expense references that isn't in the active list.
  const projectNames = useMemo(
    () => Array.from(new Set([
      "General",
      ...projects.map((p) => p.name),
      ...groups.map((g) => g.name),
    ])).filter(Boolean),
    [projects, groups],
  );

  const toggleGroup = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (expenses === null) {
    return <div className="p-6 text-sm text-text-muted">Loading expenses…</div>;
  }

  return (
    <div className="p-3 flex flex-col gap-3">
      {/* Header: add toggle + grand total */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={() => {
            setAdding((o) => !o);
            if (!addProject) setAddProject(projectNames[0] || "General");
          }}
          className="text-[12px] px-3 py-1.5 rounded-lg border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20"
        >
          {adding ? "Close" : "+ Add expense"}
        </button>
        <button
          onClick={() => setStarredOnly((v) => !v)}
          title="Show only starred expenses"
          className={`text-[12px] px-3 py-1.5 rounded-lg border ${
            starredOnly
              ? "border-amber-400/50 text-amber-300 bg-amber-400/10"
              : "border-border/60 text-text-secondary hover:text-amber-300"
          }`}
        >
          {starredOnly ? "★ Starred only" : "☆ Starred only"}
        </button>
        {inboxProject && inboxCount > 0 && (
          <button
            onClick={() => setInboxOnly((v) => !v)}
            title="Show only unassigned (General) expenses"
            className={`text-[12px] px-3 py-1.5 rounded-lg border ${
              inboxOnly
                ? "border-accent/40 bg-accent-soft text-accent"
                : "border-border/60 text-text-secondary hover:text-amber-300"
            }`}
          >
            📥 Inbox ({inboxCount})
          </button>
        )}
        <span className="ml-auto text-[12px] text-text-secondary">
          {starredTotalsByCurrency.length > 0 && !starredOnly && (
            <span className="mr-3 text-amber-300/90">
              ★ {starredTotalsByCurrency.map(([c, v]) => fmtMoney(v, c)).join(" · ")}
            </span>
          )}
          {starredOnly ? "Starred total" : "Total"}:{" "}
          <span className="font-medium text-text-primary">
            {totalsByCurrency.length === 0
              ? fmtMoney(0)
              : totalsByCurrency.map(([c, v]) => fmtMoney(v, c)).join(" · ")}
          </span>
        </span>
      </div>

      {assignError && (
        <div className="text-[11px] text-rose-400">Couldn't assign: {assignError}</div>
      )}

      {inboxOnly && inboxProject && (
        <div className="flex flex-col gap-2 rounded-lg border border-border/60 bg-bg-secondary/40 p-2.5">
          <div className="flex items-center gap-2 flex-wrap text-[11px]">
            <label className="flex items-center gap-1.5 text-text-secondary">
              <input
                type="checkbox"
                checked={allInboxSelected}
                onChange={toggleSelectAll}
                disabled={inboxRowIds.length === 0}
                className="accent-accent"
              />
              Select all
            </label>
            <span className="text-text-muted">{selected.size} selected</span>
            <span className="text-text-muted">·</span>
            <select
              value={bulkTargetProjectId}
              onChange={(e) => setBulkTargetProjectId(e.target.value)}
              disabled={anyBulkBusy || assignableProjects.length === 0}
              className="bg-bg-primary border border-border rounded px-2 py-1 text-text-primary"
            >
              <option value="">Assign to…</option>
              {assignableProjects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            <button
              onClick={() => void runBulkAssign()}
              disabled={anyBulkBusy || selected.size === 0 || !bulkTargetProjectId}
              className="px-2.5 py-1 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
            >
              {bulkAssignBusy ? "Assigning…" : "✓ Assign"}
            </button>
            <button
              onClick={() => void runAutoAssign()}
              disabled={anyBulkBusy}
              className="px-2.5 py-1 rounded border border-accent/40 bg-accent-soft text-accent hover:bg-accent/20 disabled:opacity-40"
            >
              {suggestBusy ? "Analyzing…" : "✨ Auto-assign"}
            </button>
            {confidentSuggestions.length > 0 && (
              <button
                onClick={() => void applySuggestions(confidentSuggestions)}
                disabled={anyBulkBusy}
                className="px-2.5 py-1 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
              >
                {applyBusy ? "Applying…" : `Apply all confident (${confidentSuggestions.length})`}
              </button>
            )}
          </div>
          {bulkAssignResult && (
            <div className="text-[11px] text-text-secondary">
              Moved {bulkAssignResult.moved} of {bulkAssignResult.total}.
              {bulkAssignResult.failures.length > 0 && (
                <span className="text-rose-400"> Failed: {bulkAssignResult.failures.join("; ")}</span>
              )}
            </div>
          )}
          {applyResult && (
            <div className="text-[11px] text-text-secondary">
              Moved {applyResult.moved} of {applyResult.total}.
              {applyResult.failures.length > 0 && (
                <span className="text-rose-400"> Failed: {applyResult.failures.join("; ")}</span>
              )}
            </div>
          )}
          {suggestError && (
            <div className="text-[11px] text-rose-400">Auto-assign failed: {suggestError}</div>
          )}
          {suggestSkipped > 0 && (
            <div className="text-[11px] text-text-muted">
              {suggestSkipped} more not analyzed — run again.
            </div>
          )}
        </div>
      )}

      {adding && (
        <div className="flex flex-col gap-2 rounded-lg border border-border/60 bg-bg-secondary/40 p-2.5">
          <label className="text-[11px] text-text-secondary flex items-center gap-2">
            Project
            <select
              value={addProject}
              onChange={(e) => setAddProject(e.target.value)}
              className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary"
            >
              {projectNames.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <ProjectExpenseAdder
            projectName={addProject || "General"}
            defaultCurrency={projects.find((p) => p.name === addProject)?.currency || "EUR"}
            onSaved={() => { setAdding(false); refresh(); }}
          />
        </div>
      )}

      {groups.length === 0 ? (
        <p className="text-sm text-text-muted">
          {inboxOnly && starredOnly
            ? "No starred expenses in the inbox."
            : starredOnly
            ? "No starred expenses yet. Tap the ☆ on an expense to star it."
            : inboxOnly
            ? "Inbox is empty."
            : 'No expenses yet. Add one above, or just say "spent 12 on coffee" in chat — it lands under General.'}
        </p>
      ) : (
        groups.map((g) => {
          const open = !collapsed.has(g.projectId);
          return (
            <div key={g.projectId} className="rounded-lg border border-border/60 bg-bg-secondary/40">
              <button
                onClick={() => toggleGroup(g.projectId)}
                className="w-full flex items-center gap-2 px-3 py-2 text-left"
              >
                <svg
                  width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                  className={`transition-transform text-text-muted ${open ? "rotate-90" : ""}`}
                >
                  <polyline points="9 18 15 12 9 6" />
                </svg>
                <span className="text-[13px] font-semibold text-text-primary flex-1 truncate">{g.name}</span>
                <span className="text-[11px] text-text-muted whitespace-nowrap">
                  {fmtMoney(g.subtotal, g.currency)}
                </span>
              </button>
              {open && (
                <div className="px-2 pb-2 flex flex-col gap-1">
                  {g.rows.map((e) => {
                    const isInboxGroup = !!inboxProject && g.projectId === inboxProject.id;
                    const suggestion = suggestions.get(e.id) ?? null;
                    return (
                      <ExpenseRow
                        key={e.id}
                        date={e.spent_at || ""}
                        expense={e}
                        onChanged={refresh}
                        {...(isInboxGroup
                          ? {
                              assignable: true,
                              onAssign: (projectId: string, taskId: string | null) =>
                                assignExpense(e.id, projectId, taskId),
                              assignProjects: assignableProjects,
                              assignTasks: tasks,
                              // A bulk write (assign/apply/suggestion-fetch)
                              // touches the same rows — lock the single-row
                              // Assign affordance out from under it too.
                              bulkLocked: anyBulkBusy,
                            }
                          : {})}
                        {...(isInboxGroup && inboxOnly
                          ? {
                              selectable: true,
                              selected: selected.has(e.id),
                              onToggleSelect: () => toggleSelect(e.id),
                              suggestion,
                              onApplySuggestion: suggestion
                                ? () => applySuggestions([suggestion])
                                : undefined,
                              suggestionBusy: applyingIds.has(e.id),
                              suggestionLocked: anyBulkBusy && !applyingIds.has(e.id),
                            }
                          : {})}
                      />
                    );
                  })}
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
