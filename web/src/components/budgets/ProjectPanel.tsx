import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import * as api from "../../api";
import type { BudgetEntry, Expense, Project } from "../../api";
import { fmtMoney } from "./money";
import { ExpenseRow } from "./ExpenseRow";
import { ProjectExpenseAdder } from "./ExpenseAdder";

/**
 * Project management banner — shown at the top of the task list when a project
 * is selected in the rail. Gives the controls that were missing after project
 * creation: set/top-up the budget, add a task scoped to the project, log a
 * project-level (or recurring) expense, and review the expense log.
 *
 * Layout note: the list pane has an absolutely-positioned "show detail" toggle
 * pinned top-right, so the title row carries right padding and all actions live
 * on their own row below the bar — nothing sits under the toggle.
 */
type Pane = "none" | "rename" | "add" | "task" | "expense" | "log";

export function ProjectPanel({
  projectKey,
  displayName,
  onChanged,
  onDeleted,
  onRenamed,
}: {
  projectKey: string;
  displayName: string;
  onChanged: () => void;
  /** Called after the project is deleted so the parent can clear the filter. */
  onDeleted?: () => void;
  /** Called after a rename with the new name_key so the parent can re-point
   *  its project filter (tasks moved to the new category). */
  onRenamed?: (newProjectKey: string) => void;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [tick, setTick] = useState(0);
  const [pane, setPane] = useState<Pane>("none");
  const [expanded, setExpanded] = useState(false);
  // Two-stage delete: "cascade" appears only after a 409 (still has expenses).
  const [confirm, setConfirm] = useState<"first" | "cascade" | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let alive = true;
    api.listProjects("all")
      .then((ps) => { if (alive) setProject(ps.find((p) => p.name_key === projectKey) ?? null); })
      .catch(() => { if (alive) setProject(null); });
    return () => { alive = false; };
  }, [projectKey, tick]);

  // Refetch when the user returns to this tab/window. The budget bar reflects
  // ``budget``/``spent`` which change out-of-band — a phone top-up (or the
  // agent) bumps them — and this banner is otherwise a one-shot fetch that
  // stays stale until a full reload. Mirrors ExpensesView.
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

  // Poll while this tab is visible so a top-up made on another client updates
  // the budget bar WITHOUT needing to refocus. Mirrors the Tasks page's 30s
  // poll; paused while hidden to avoid background-tab throttling.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((n) => n + 1);
    }, 30_000);
    return () => window.clearInterval(id);
  }, []);

  const refresh = () => { setTick((n) => n + 1); onChanged(); };
  const toggle = (p: Pane) => setPane((cur) => (cur === p ? "none" : p));

  const removeProject = async (cascade: boolean) => {
    if (!project) return;
    setDeleting(true);
    try {
      await api.deleteProject(project.id, cascade);
      setConfirm(null);
      onDeleted?.();
      onChanged();
    } catch (e) {
      if (e instanceof api.ApiError && e.status === 409) setConfirm("cascade");
      else setConfirm(null);
    } finally {
      setDeleting(false);
    }
  };

  const name = project?.name || displayName;
  const budget = project?.budget ?? 0;
  const spent = project?.spent ?? 0;
  const remaining = project?.remaining ?? (budget - spent);
  const rawPct = budget > 0 ? (spent / budget) * 100 : 0;
  const pct = Math.min(100, rawPct);
  const over = budget > 0 && spent > budget;
  // Traffic-light: <70% emerald, 70–90% amber, >90% red.
  const tone = rawPct > 90 ? "bg-red-400" : rawPct >= 70 ? "bg-amber" : "bg-emerald-400";

  return (
    <div className="m-2 rounded-xl border border-border/60 bg-bg-secondary/60 p-3">
      {/* Title row — right padding clears the list pane's show-detail toggle.
          The 💵 toggle expands/collapses the budget controls so the panel
          stays compact by default. */}
      <div className="flex items-center gap-2 pr-28">
        <span className="w-2 h-2 rounded-full bg-accent shrink-0" />
        <span className="text-[13px] font-semibold text-text-primary truncate">{name}</span>
        {budget > 0 && (
          <span className="text-[10px] text-text-muted ml-1 whitespace-nowrap">
            {fmtMoney(spent, project?.currency)} / {fmtMoney(budget, project?.currency)}
          </span>
        )}
        <button
          onClick={() => { setExpanded((o) => !o); setPane("none"); }}
          title={expanded ? "Hide project options" : "Project options"}
          aria-expanded={expanded}
          className={`ml-auto shrink-0 flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border transition-colors ${
            expanded
              ? "border-accent/40 bg-accent-soft text-accent"
              : "border-border text-text-secondary hover:text-text-primary hover:border-accent/30"
          }`}
        >
          {/* Gear = project options/settings */}
          <svg
            width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            className={`transition-transform ${expanded ? "rotate-90" : ""}`}
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
          <span>Options</span>
        </button>
        {project && (
          <button
            onClick={() => setConfirm("first")}
            title="Delete project"
            aria-label="Delete project"
            className="shrink-0 text-text-muted hover:text-rose-400 text-sm leading-none px-1"
          >
            🗑
          </button>
        )}
      </div>

      {confirm && (
        <div className="mt-2 flex items-center gap-2 flex-wrap rounded-lg border border-rose-400/30 bg-rose-400/5 px-2.5 py-2">
          <span className="text-[11px] text-rose-300 flex-1 min-w-0">
            {confirm === "cascade"
              ? `Delete "${name}" plus all its expenses, recurring rules & notes?`
              : `Delete project "${name}"?`}
          </span>
          <button
            onClick={() => void removeProject(confirm === "cascade")}
            disabled={deleting}
            className="text-[11px] px-2.5 py-1 rounded border border-rose-400/40 text-rose-300 bg-rose-400/10 hover:bg-rose-400/20 disabled:opacity-40"
          >
            {deleting ? "Deleting…" : confirm === "cascade" ? "Delete all" : "Delete"}
          </button>
          <button
            onClick={() => setConfirm(null)}
            className="text-[11px] px-2.5 py-1 rounded border border-border text-text-secondary hover:bg-bg-hover"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Budget bar */}
      {budget > 0 ? (
        <div className="mt-2">
          <div className="h-2 rounded-full bg-bg-tertiary overflow-hidden">
            <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
          </div>
          <div className="flex items-center justify-between text-[10px] text-text-muted mt-1">
            <span>
              {Math.round(rawPct)}% used
              {over && <span className="ml-1.5 font-semibold text-red-400">⚠ OVER</span>}
            </span>
            <span className={remaining < 0 ? "text-red-400" : ""}>
              {fmtMoney(remaining, project?.currency)} left
            </span>
          </div>
        </div>
      ) : (
        <p className="mt-1.5 text-[10px] text-text-muted">No budget set yet.</p>
      )}

      {/* Actions — collapsed behind the 💵 toggle; own row, wraps, never
          under the show-detail toggle */}
      {expanded && (
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <PanelBtn active={pane === "rename"} onClick={() => toggle("rename")}>
          ✏️ Rename / describe
        </PanelBtn>
        {/* "+ Add budget" = a sourced top-up (ledger entry); self-creates the
            project + sets the base when budget is still 0. This is the only
            budget control — corrections happen in the 📋 Log. */}
        <PanelBtn active={pane === "add"} onClick={() => toggle("add")}>+ Add budget</PanelBtn>
        <PanelBtn active={pane === "task"} onClick={() => toggle("task")}>+ Task</PanelBtn>
        <PanelBtn active={pane === "expense"} onClick={() => toggle("expense")}>+ Expense</PanelBtn>
        <PanelBtn active={pane === "log"} onClick={() => toggle("log")}>📋 Log</PanelBtn>
      </div>
      )}

      {expanded && pane === "rename" && (
        <ProjectMetaEditor
          projectName={name} existing={project}
          onSaved={(newName) => {
            setPane("none");
            const newKey = newName.trim().toLowerCase();
            if (newKey && newKey !== name.toLowerCase() && onRenamed) {
              onRenamed(newKey);
            } else {
              refresh();
            }
          }}
        />
      )}
      {pane === "add" && (
        <BudgetEditor
          projectName={name} existing={project}
          onSaved={() => { setPane("none"); refresh(); }}
        />
      )}
      {pane === "task" && (
        <TaskAdder category={name} onSaved={() => { setPane("none"); refresh(); }} />
      )}
      {pane === "expense" && (
        <ProjectExpenseAdder
          projectName={name} defaultCurrency={project?.currency || "EUR"}
          onSaved={() => { setPane("none"); refresh(); }}
        />
      )}
      {pane === "log" && project && (
        <ExpenseLog projectId={project.id} currency={project.currency} onChanged={refresh} />
      )}
      {pane === "log" && !project && (
        <p className="mt-2 text-[10px] text-text-muted">No expenses yet — set a budget or add one.</p>
      )}
    </div>
  );
}

function PanelBtn({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`text-[10px] px-2 py-1 rounded-md border transition-colors ${
        active
          ? "border-accent/40 bg-accent-soft text-accent"
          : "border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20"
      }`}
    >
      {children}
    </button>
  );
}

function ProjectMetaEditor({
  projectName, existing, onSaved,
}: {
  projectName: string;
  existing: Project | null;
  /** Receives the (possibly new) project name after a successful save. */
  onSaved: (newName: string) => void;
}) {
  const [newName, setNewName] = useState(projectName);
  const [description, setDescription] = useState(existing?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    const trimmed = newName.trim();
    if (!trimmed) { setErr("Name can't be empty."); return; }
    setBusy(true);
    setErr(null);
    try {
      // A category with no budget/expenses is a "phantom" project — no row yet.
      // Materialize it under its CURRENT name first, then PATCH: the backend
      // re-points every task's category when the name changes (no orphans).
      const proj = existing ?? (await api.createProject({ name: projectName }));
      const renamed = trimmed.toLowerCase() !== projectName.toLowerCase();
      await api.updateProject(proj.id, {
        ...(renamed ? { name: trimmed } : {}),
        description: description.trim() || undefined,
      });
      onSaved(trimmed);
    } catch {
      setErr("Couldn't save. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2 flex flex-col gap-2">
      <input
        value={newName} autoFocus maxLength={200}
        onChange={(e) => setNewName(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") void save(); }}
        placeholder="Project name"
        className="bg-bg-primary border border-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary"
      />
      <textarea
        value={description} maxLength={2000} rows={3}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="What is this project for? (becomes the [[Project]] wikilink page)"
        className="bg-bg-primary border border-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary resize-y"
      />
      <div className="flex items-center gap-2">
        <button
          onClick={() => void save()} disabled={busy}
          className="text-[11px] px-3 py-1.5 rounded-lg border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        {err && <span className="text-[10px] text-red-400">{err}</span>}
      </div>
    </div>
  );
}

function BudgetEditor({
  projectName, existing, onSaved,
}: {
  projectName: string;
  existing: Project | null;
  onSaved: () => void;
}) {
  const [value, setValue] = useState("");
  const [currency, setCurrency] = useState(existing?.currency || "EUR");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const amount = parseFloat(value);
    if (!Number.isFinite(amount)) return;
    setBusy(true);
    try {
      // Top-up → ledger entry with a source, bumps the budget. Self-creates
      // the project when it's still a phantom (no row yet).
      const proj = existing ?? (await api.createProject({ name: projectName, currency }));
      await api.addBudgetEntry(proj.id, {
        amount,
        source: source.trim() || undefined,
        currency,
      });
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <input
          type="number" min={0} value={value} autoFocus
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void save(); }}
          placeholder="Amount to add to budget"
          className="bg-bg-primary border border-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary flex-1"
        />
        <input
          value={currency} maxLength={8}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          className="bg-bg-primary border border-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary w-16"
        />
        <button
          onClick={() => void save()} disabled={busy}
          className="text-[11px] px-3 py-1.5 rounded-lg border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
        >
          {busy ? "Saving…" : "Add"}
        </button>
      </div>
      {/* Source comment — "where is this money from?" */}
      <div className="flex items-center gap-2">
        <span className="text-text-muted" title="Source of the money">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </span>
        <input
          value={source}
          onChange={(e) => setSource(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void save(); }}
          placeholder="Source — where is this money from? (e.g. client deposit)"
          className="bg-bg-primary border border-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary flex-1"
        />
      </div>
    </div>
  );
}

function TaskAdder({ category, onSaved }: { category: string; onSaved: () => void }) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  const add = async () => {
    if (!title.trim()) return;
    setBusy(true);
    try {
      await api.addTask({ title: title.trim(), category });
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2 flex items-center gap-2">
      <input
        value={title} autoFocus
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") void add(); }}
        placeholder={`New task in ${category}…`}
        className="bg-bg-primary border border-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary flex-1"
      />
      <button
        onClick={() => void add()} disabled={busy}
        className="text-[11px] px-3 py-1.5 rounded-lg border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
      >
        {busy ? "Adding…" : "Add task"}
      </button>
    </div>
  );
}

function ExpenseLog({
  projectId, currency, onChanged,
}: { projectId: string; currency: string; onChanged: () => void }) {
  const [expenses, setExpenses] = useState<Expense[] | null>(null);
  const [credits, setCredits] = useState<BudgetEntry[]>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.all([api.listExpenses(projectId), api.listBudgetEntries(projectId)])
      .then(([ex, cr]) => { if (alive) { setExpenses(ex); setCredits(cr); } })
      .catch(() => { if (alive) { setExpenses([]); setCredits([]); } });
    return () => { alive = false; };
  }, [projectId, tick]);

  // Refetch this ledger when the user returns to the tab/window. A phone
  // top-up (or the agent) lands a new budget-entry credit row out-of-band, and
  // this log is otherwise a one-shot fetch that stays stale until a full
  // reload. Mirrors ExpensesView.
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

  // Poll while this tab is visible so a top-up's credit row (or a new expense)
  // shows up WITHOUT needing to refocus. Mirrors the Tasks page's 30s poll;
  // paused while hidden to avoid background-tab throttling.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((n) => n + 1);
    }, 30_000);
    return () => window.clearInterval(id);
  }, []);

  // Bumped after any mutation so the log refetches AND the parent panel
  // refreshes its budget bar (a delete/edit changes project.budget).
  const refresh = () => { setTick((n) => n + 1); onChanged(); };

  if (expenses === null) {
    return <p className="mt-2 text-[10px] text-text-muted">Loading…</p>;
  }
  if (expenses.length === 0 && credits.length === 0) {
    return <p className="mt-2 text-[10px] text-text-muted">Nothing logged yet — add budget or an expense.</p>;
  }

  // Merge into one ledger, newest first. Budget top-ups + audit-edits are
  // money-in / out-adjustments; expenses are debits (money out).
  type Row =
    | { rowKind: "credit"; id: string; date: string; entry: BudgetEntry }
    | { rowKind: "debit"; id: string; date: string; expense: Expense };
  const rows: Row[] = [
    ...credits.map((c): Row => ({ rowKind: "credit", id: c.id, date: (c.created_at || "").slice(0, 10), entry: c })),
    ...expenses.map((e): Row => ({ rowKind: "debit", id: e.id, date: e.spent_at || "", expense: e })),
  ].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));

  const totalIn = credits.reduce((s, c) => s + (c.amount || 0), 0);
  const totalOut = expenses.reduce((s, e) => s + (e.amount || 0), 0);

  return (
    <div className="mt-2 flex flex-col gap-1">
      {rows.map((r) =>
        r.rowKind === "credit" ? (
          <BudgetRow key={r.id} date={r.date} entry={r.entry} onChanged={refresh} />
        ) : (
          <ExpenseRow key={r.id} date={r.date} expense={r.expense} onChanged={refresh} />
        ),
      )}
      <div className="flex justify-between text-[10px] text-text-muted px-2 pt-1 border-t border-border/40">
        <span className="text-emerald-400/80">+ {fmtMoney(totalIn, currency)} net change</span>
        <span>− {fmtMoney(totalOut, currency)} spent</span>
      </div>
    </div>
  );
}

function BudgetRow({
  date, entry, onChanged,
}: { date: string; entry: BudgetEntry; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [amount, setAmount] = useState(String(entry.amount));
  const [source, setSource] = useState(entry.source ?? "");
  const [busy, setBusy] = useState(false);

  const isEdit = entry.kind === "edit";
  const isNeg = (entry.amount || 0) < 0;
  // Audit (Edit budget) rows render amber so they read differently from top-ups.
  const palette = isEdit
    ? "bg-amber/5 border-amber/20"
    : "bg-emerald-400/5 border-emerald-400/15";
  const amountTone = isEdit
    ? (isNeg ? "text-rose-300" : "text-amber")
    : "text-emerald-400";
  const label = isEdit ? "Budget edited" : "Budget added";

  const save = async () => {
    setBusy(true);
    try {
      const value = parseFloat(amount);
      await api.updateBudgetEntry(entry.id, {
        amount: Number.isFinite(value) ? value : entry.amount,
        source: source,
      });
      setEditing(false);
      onChanged();
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!window.confirm(`Delete this ${isEdit ? "budget edit" : "budget top-up"}? It will roll back the budget change.`)) return;
    setBusy(true);
    try {
      await api.deleteBudgetEntry(entry.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <div className={`flex items-center gap-2 text-[11px] px-2 py-1 rounded border ${palette}`}>
        <span className="text-text-muted w-20 shrink-0">{date}</span>
        <input
          type="number" value={amount}
          onChange={(e) => setAmount(e.target.value)}
          className="bg-bg-primary border border-border rounded px-1.5 py-0.5 text-text-primary w-24"
        />
        <input
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder="Source / reason"
          className="bg-bg-primary border border-border rounded px-1.5 py-0.5 text-text-primary flex-1"
        />
        <button
          onClick={() => void save()} disabled={busy}
          className="text-[10px] px-2 py-0.5 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
        >Save</button>
        <button
          onClick={() => { setEditing(false); setAmount(String(entry.amount)); setSource(entry.source ?? ""); }}
          className="text-text-muted hover:text-text-primary"
          title="Cancel"
        >×</button>
      </div>
    );
  }
  return (
    <div className={`flex items-center gap-2 text-[11px] text-text-secondary px-2 py-1 rounded border ${palette}`}>
      <span className="text-text-muted w-20 shrink-0">{date}</span>
      <span className={`flex-1 flex items-center gap-1 truncate ${isEdit ? "text-amber/90" : "text-emerald-300/90"}`}>
        {label}
        {entry.source && (
          <>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-muted shrink-0" aria-label="source">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <span className="text-text-muted truncate">{entry.source}</span>
          </>
        )}
      </span>
      <span className={amountTone}>
        {(entry.amount ?? 0) >= 0 ? "+ " : "− "}{fmtMoney(Math.abs(entry.amount ?? 0), entry.currency)}
      </span>
      <button
        onClick={() => setEditing(true)}
        className="text-text-muted hover:text-accent"
        title="Edit entry"
        disabled={busy}
      >✎</button>
      <button
        onClick={() => void remove()}
        className="text-text-muted hover:text-rose-400"
        title="Delete entry (rolls back the change)"
        disabled={busy}
      >×</button>
    </div>
  );
}

