import { useEffect, useMemo, useState } from "react";
import * as api from "../../api";
import type { Expense, Project } from "../../api";
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
  const [tick, setTick] = useState(0);
  const [adding, setAdding] = useState(false);
  const [addProject, setAddProject] = useState<string>("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [starredOnly, setStarredOnly] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([api.listAllExpenses(), api.listProjects("all")])
      .then(([ex, ps]) => { if (alive) { setExpenses(ex); setProjects(ps); } })
      .catch(() => { if (alive) { setExpenses([]); setProjects([]); } });
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

  const refresh = () => { setTick((n) => n + 1); onChanged?.(); };

  const currencyByProject = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of projects) m.set(p.id, p.currency || "EUR");
    return m;
  }, [projects]);

  const groups = useMemo<Group[]>(() => {
    if (!expenses) return [];
    const src = starredOnly ? expenses.filter((e) => e.is_favorite) : expenses;
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
    return Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [expenses, currencyByProject, starredOnly]);

  // Total of the currently-shown set (all, or starred-only when filtered), split
  // per currency — no FX conversion (matches spending report).
  const totalsByCurrency = useMemo(() => {
    const src = starredOnly
      ? (expenses || []).filter((e) => e.is_favorite)
      : expenses || [];
    const m = new Map<string, number>();
    for (const e of src) {
      const c = e.currency || "EUR";
      m.set(c, (m.get(c) || 0) + (e.amount || 0));
    }
    return Array.from(m.entries());
  }, [expenses, starredOnly]);

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
          {starredOnly
            ? "No starred expenses yet. Tap the ☆ on an expense to star it."
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
                  {g.rows.map((e) => (
                    <ExpenseRow
                      key={e.id}
                      date={e.spent_at || ""}
                      expense={e}
                      onChanged={refresh}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
