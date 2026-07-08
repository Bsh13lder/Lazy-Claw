import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import * as api from "../../api";
import type { Expense, Project } from "../../api";
import { fmtMoney } from "./money";

const PANEL_OPEN_KEY = "lazyclaw.task.budget.open";
function readPanelOpen(def = false): boolean {
  try { const v = localStorage.getItem(PANEL_OPEN_KEY); return v === null ? def : v === "1"; } catch { return def; }
}
function writePanelOpen(val: boolean) {
  try { localStorage.setItem(PANEL_OPEN_KEY, val ? "1" : "0"); } catch { /* ignore */ }
}

/**
 * Budget / Cost section for the task detail pane.
 *
 * Step 1 — connect the task to a business: a Project picker writes the task's
 * ``category`` (existing project, or a brand-new name). This is the link the
 * whole budget feature hangs on — without a category a task belongs to no
 * project.
 *
 * Step 2 — once connected, show the project's budget-vs-spent bar, the
 * expenses logged against THIS task, and an inline add-expense form (one-off
 * or recurring). Each expense mirrors to a LazyBrain note wikilinking the
 * project page.
 */
export function TaskExpensePanel({
  taskId,
  category,
  allocatedBudget,
  onChanged,
}: {
  taskId: string;
  category: string | null;
  allocatedBudget?: number | null;
  onChanged?: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [tick, setTick] = useState(0);
  const [adding, setAdding] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [pickingNew, setPickingNew] = useState(false);
  const [savingCat, setSavingCat] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => readPanelOpen(false));
  useEffect(() => { writePanelOpen(panelOpen); }, [panelOpen]);

  const cat = (category || "").trim();
  const catKey = cat.toLowerCase();

  useEffect(() => {
    let alive = true;
    api.listProjects("all")
      .then(async (ps) => {
        if (!alive) return;
        setProjects(ps);
        const proj = ps.find((p) => p.name_key === catKey) ?? null;
        if (proj && cat) {
          const ex = await api.listExpenses(proj.id, taskId);
          if (alive) setExpenses(ex);
        } else {
          setExpenses([]);
        }
      })
      .catch(() => { if (alive) { setProjects([]); setExpenses([]); } });
    return () => { alive = false; };
  }, [cat, catKey, taskId, tick]);

  // Refetch when the user returns to this tab/window. The project budget bar
  // and this task's expenses change out-of-band — a phone-added expense (or
  // the agent) lands without a web reload — and this panel is otherwise a
  // one-shot fetch that stays stale. Mirrors ExpensesView.
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

  // Poll while this tab is visible so an expense or top-up on another client
  // shows up WITHOUT needing to refocus. Mirrors the Tasks page's 30s poll;
  // paused while hidden to avoid background-tab throttling.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((n) => n + 1);
    }, 30_000);
    return () => window.clearInterval(id);
  }, []);

  const project = projects.find((p) => p.name_key === catKey) ?? null;
  const refresh = () => { setTick((n) => n + 1); onChanged?.(); };

  // Re-categorize the task to a (possibly new) business name.
  const assignProject = async (name: string) => {
    const value = name.trim();
    if (!value) return;
    setSavingCat(true);
    try {
      await api.updateTask(taskId, { category: value });
      setPickingNew(false);
      setNewProjectName("");
      refresh();
    } finally {
      setSavingCat(false);
    }
  };

  const budget = project?.budget ?? 0;
  const spent = project?.spent ?? 0;
  const pct = budget > 0 ? Math.min(100, (spent / budget) * 100) : 0;
  const tone = pct >= 100 ? "bg-red-400" : pct >= 80 ? "bg-amber" : "bg-accent";
  const taskTotal = expenses.reduce((s, e) => s + (e.amount || 0), 0);

  // Compact summary text rendered when the panel is collapsed.
  const summary = (() => {
    if (!cat) return "no project";
    if (!project) return `${cat} · no budget`;
    const allocPart = (allocatedBudget ?? 0) > 0
      ? ` · task ${fmtMoney(allocatedBudget || 0, project.currency)}`
      : "";
    if ((project.budget ?? 0) > 0) {
      return `${project.name} · ${fmtMoney(project.spent ?? 0, project.currency)}/${fmtMoney(project.budget ?? 0, project.currency)}${allocPart}`;
    }
    return `${project.name} · no budget${allocPart}`;
  })();

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={() => setPanelOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPanelOpen((o) => !o); } }}
        className="flex items-center gap-2 mb-1.5 cursor-pointer select-none"
        aria-expanded={panelOpen}
      >
        <svg
          width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"
          className={`text-text-muted shrink-0 transition-transform ${panelOpen ? "rotate-90" : ""}`}
        >
          <polyline points="9 6 15 12 9 18" />
        </svg>
        <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary shrink-0">
          Project / Budget
        </h3>
        {!panelOpen && (
          <span className="text-[10px] text-text-muted truncate flex-1 text-left">{summary}</span>
        )}
      </div>

      {/* Collapsed mini-bar: visible at-a-glance budget pulse when panel is shut */}
      {!panelOpen && project && (project.budget ?? 0) > 0 && (() => {
        const budget = project.budget || 0;
        const spent = project.spent || 0;
        const pct = Math.min(100, (spent / budget) * 100);
        const tone = pct >= 100 ? "bg-red-400" : pct >= 80 ? "bg-amber" : "bg-accent";
        return (
          <div className="mb-1 h-1 rounded-full bg-bg-tertiary overflow-hidden">
            <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
          </div>
        );
      })()}

      {!panelOpen ? null : (<>

      {/* Project picker — the task↔business link */}
      <div className="mb-2">
        {pickingNew ? (
          <div className="flex items-center gap-2">
            <input
              autoFocus
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void assignProject(newProjectName);
                if (e.key === "Escape") { setPickingNew(false); setNewProjectName(""); }
              }}
              placeholder="New business name…"
              className="flex-1 bg-bg-tertiary border border-accent/40 rounded-lg px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none"
            />
            <button
              onClick={() => void assignProject(newProjectName)}
              disabled={savingCat}
              className="text-[11px] px-2.5 py-1.5 rounded-lg border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
            >
              Set
            </button>
            <button
              onClick={() => { setPickingNew(false); setNewProjectName(""); }}
              className="w-6 h-6 grid place-items-center rounded-md text-text-muted hover:text-text-primary hover:bg-bg-hover"
              title="Cancel"
            >
              ×
            </button>
          </div>
        ) : (
          <ProjectDropdown
            projects={projects}
            currentKey={catKey}
            currentLabel={cat}
            onPickProject={(p) => void assignProject(p.name)}
            onKeepCurrent={() => void assignProject(cat)}
            onClear={() => void api.updateTask(taskId, { category: null }).then(refresh)}
            onNew={() => setPickingNew(true)}
          />
        )}
      </div>

      {!cat ? (
        <p className="text-[10px] text-text-muted">
          Pick a business to connect this task to a budget. All tasks with the
          same business share one budget.
        </p>
      ) : (
        <>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10px] text-text-muted">
              {project ? "Budget" : "No budget set — add an expense or set one in the Projects rail"}
            </span>
            {project && (
              <button
                onClick={() => setAdding((o) => !o)}
                className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20"
              >
                {adding ? "Cancel" : "+ Expense"}
              </button>
            )}
          </div>

          {project && (
            <div className="mb-2">
              <div className="flex items-center justify-between text-[10px] text-text-muted mb-1">
                <span>{fmtMoney(spent, project.currency)} spent</span>
                <span>of {fmtMoney(budget, project.currency)}</span>
              </div>
              {budget > 0 && (
                <div className="h-1.5 rounded-full bg-bg-tertiary overflow-hidden">
                  <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
                </div>
              )}
            </div>
          )}

          {/* Per-task budget allocation — a slice of the project budget.
              "If the project has 950, allocate 500 here and the task tracks
              its own spent vs that 500." Independent of project.budget — the
              parent project total isn't decremented, but the task gets its
              own spent/allocated bar. */}
          <TaskBudgetBlock
            taskId={taskId}
            allocated={allocatedBudget ?? null}
            spent={expenses.reduce((s, e) => s + (e.amount || 0), 0)}
            currency={project?.currency || "EUR"}
            onChanged={refresh}
          />

          {/* When the project has no budget row yet, offer to add an expense too
              (it auto-creates the project). */}
          {!project && (
            <button
              onClick={() => setAdding((o) => !o)}
              className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 mb-2"
            >
              {adding ? "Cancel" : "+ Expense"}
            </button>
          )}

          {adding && (
            <ExpenseForm
              projectName={project?.name || cat}
              taskId={taskId}
              defaultCurrency={project?.currency || "EUR"}
              onSaved={() => { setAdding(false); refresh(); }}
            />
          )}

          {expenses.length > 0 && (
            <div className="mt-2 flex flex-col gap-1">
              <p className="text-[10px] text-text-muted">
                On this task: {fmtMoney(taskTotal, project?.currency)}
              </p>
              {expenses.map((e) => (
                <div
                  key={e.id}
                  className="flex items-center gap-2 text-[11px] text-text-secondary px-2 py-1 rounded bg-bg-tertiary/40"
                >
                  <span className="text-text-muted">{e.spent_at || ""}</span>
                  <span className="flex-1 truncate">
                    {e.description || e.vendor || "expense"}
                    {e.recurring_expense_id && <span className="text-accent/60"> · recurring</span>}
                  </span>
                  <span>{fmtMoney(e.amount, e.currency)}</span>
                  <button
                    onClick={async () => { await api.deleteExpense(e.id); refresh(); }}
                    className="text-text-muted hover:text-rose-400"
                    title="Delete expense"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
      </>)}
    </div>
  );
}

/** Per-task budget allocation block. Renders a "spent vs allocated" bar with
 *  edit/clear controls, OR an "Allocate budget" button when nothing's set. */
function TaskBudgetBlock({
  taskId, allocated, spent, currency, onChanged,
}: {
  taskId: string;
  allocated: number | null;
  spent: number;
  currency: string;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(allocated ? String(allocated) : "");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) setValue(allocated ? String(allocated) : "");
  }, [allocated, editing]);

  const save = async () => {
    const amount = parseFloat(value);
    if (!Number.isFinite(amount) || amount < 0) return;
    setBusy(true);
    try {
      await api.updateTask(taskId, { allocated_budget: amount });
      setEditing(false);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    try {
      await api.updateTask(taskId, { allocated_budget: 0 });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const has = (allocated ?? 0) > 0;
  const pct = has ? Math.min(100, (spent / (allocated || 1)) * 100) : 0;
  const tone = pct >= 100 ? "bg-red-400" : pct >= 80 ? "bg-amber" : "bg-accent";
  const remaining = has ? (allocated || 0) - spent : 0;

  if (editing) {
    return (
      <div className="mb-2 p-2 rounded-lg bg-bg-tertiary/40 border border-border/50">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-text-muted shrink-0">Task budget</span>
          <input
            type="number" min={0} value={value} autoFocus
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void save(); }}
            placeholder="e.g. 500"
            className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary flex-1"
          />
          <button
            onClick={() => void save()} disabled={busy}
            className="text-[10px] px-2 py-1 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
          >Save</button>
          <button
            onClick={() => setEditing(false)}
            className="text-text-muted hover:text-text-primary"
          >×</button>
        </div>
      </div>
    );
  }

  if (!has) {
    return (
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-text-muted">Task budget</span>
        <span className="text-[10px] text-text-muted flex-1">not allocated</span>
        <button
          onClick={() => setEditing(true)}
          className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20"
        >Allocate budget</button>
      </div>
    );
  }

  return (
    <div className="mb-2">
      <div className="flex items-center justify-between text-[10px] text-text-muted mb-1">
        <span>Task budget</span>
        <span className="flex items-center gap-1.5">
          <span>{fmtMoney(spent, currency)} of {fmtMoney(allocated || 0, currency)}</span>
          <button
            onClick={() => setEditing(true)}
            className="text-text-muted hover:text-accent"
            title="Edit task budget"
          >✎</button>
          <button
            onClick={() => void clear()}
            disabled={busy}
            className="text-text-muted hover:text-rose-400"
            title="Clear task budget"
          >×</button>
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-bg-tertiary overflow-hidden">
        <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex justify-between text-[10px] text-text-muted mt-1">
        <span>{pct.toFixed(0)}% used</span>
        <span className={remaining < 0 ? "text-red-400" : ""}>
          {fmtMoney(remaining, currency)} left
        </span>
      </div>
    </div>
  );
}

/** Cozy themed dropdown — replaces the native <select> chrome. */
function ProjectDropdown({
  projects,
  currentKey,
  currentLabel,
  onPickProject,
  onKeepCurrent,
  onClear,
  onNew,
}: {
  projects: Project[];
  currentKey: string;
  currentLabel: string;
  onPickProject: (p: Project) => void;
  onKeepCurrent: () => void;
  onClear: () => void;
  onNew: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const current = projects.find((p) => p.name_key === currentKey) ?? null;
  const unsavedCurrent = !!currentLabel && !current;
  const label = current ? current.name : unsavedCurrent ? `${currentLabel}` : "No project";

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`w-full flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors ${
          currentLabel
            ? "border-accent/30 bg-accent-soft/40 text-text-primary"
            : "border-border bg-bg-tertiary text-text-secondary hover:border-accent/30"
        }`}
      >
        <span
          className={`w-1.5 h-1.5 rounded-full shrink-0 ${
            current ? "bg-accent" : currentLabel ? "bg-amber" : "bg-text-muted/40"
          }`}
        />
        <span className="flex-1 text-[13px] font-medium truncate">{label}</span>
        {unsavedCurrent && (
          <span className="text-[9.5px] text-amber/80 whitespace-nowrap">no budget</span>
        )}
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
          className={`text-text-muted transition-transform ${open ? "rotate-180" : ""}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="absolute z-30 mt-1 w-full rounded-lg border border-border bg-bg-secondary shadow-xl p-1 max-h-64 overflow-y-auto">
          {unsavedCurrent && (
            <DropdownRow active onClick={() => { onKeepCurrent(); setOpen(false); }}>
              <span className="w-1.5 h-1.5 rounded-full bg-amber shrink-0" />
              <span className="flex-1 truncate">{currentLabel}</span>
              <span className="text-[9.5px] text-amber/80">no budget</span>
            </DropdownRow>
          )}
          {projects.map((p) => {
            const spent = p.spent ?? 0;
            const budget = p.budget ?? 0;
            return (
              <DropdownRow
                key={p.id}
                active={p.name_key === currentKey}
                onClick={() => { onPickProject(p); setOpen(false); }}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />
                <span className="flex-1 truncate">{p.name}</span>
                {budget > 0 && (
                  <span className="text-[9.5px] text-text-muted whitespace-nowrap">
                    {fmtMoney(spent, p.currency)}/{fmtMoney(budget, p.currency)}
                  </span>
                )}
              </DropdownRow>
            );
          })}

          <div className="my-1 h-px bg-border/60" />

          <DropdownRow onClick={() => { onNew(); setOpen(false); }}>
            <span className="text-accent text-[13px] leading-none">＋</span>
            <span className="flex-1 text-accent">New business…</span>
          </DropdownRow>
          {currentLabel && (
            <DropdownRow onClick={() => { onClear(); setOpen(false); }}>
              <span className="w-1.5 h-1.5 rounded-full bg-text-muted/40 shrink-0" />
              <span className="flex-1 text-text-muted">No project</span>
            </DropdownRow>
          )}
        </div>
      )}
    </div>
  );
}

function DropdownRow({
  active = false,
  onClick,
  children,
}: {
  active?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-left transition-colors ${
        active ? "bg-accent-soft text-accent" : "text-text-secondary hover:bg-bg-hover"
      }`}
    >
      {children}
    </button>
  );
}

const RECUR_PRESETS: { label: string; cron: string }[] = [
  { label: "One-off", cron: "" },
  { label: "Monthly", cron: "0 0 1 * *" },
  { label: "Weekly", cron: "0 0 * * 1" },
  { label: "Yearly", cron: "0 0 1 1 *" },
];

function ExpenseForm({
  projectName,
  taskId,
  defaultCurrency,
  onSaved,
}: {
  projectName: string;
  taskId: string;
  defaultCurrency: string;
  onSaved: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [currency, setCurrency] = useState(defaultCurrency);
  const [cron, setCron] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    const value = parseFloat(amount);
    if (!Number.isFinite(value) || value <= 0) {
      setErr("Enter an amount.");
      return;
    }
    setBusy(true);
    try {
      // Ensure the project exists (auto-create from the task category).
      const proj = await api.createProject({ name: projectName, currency });
      if (cron) {
        await api.createRecurringExpense(proj.id, {
          amount: value,
          cron_expression: cron,
          currency,
          description: description.trim() || undefined,
          task_id: taskId,
        });
      } else {
        await api.createExpense(proj.id, {
          amount: value,
          currency,
          description: description.trim() || undefined,
          task_id: taskId,
        });
      }
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 p-2 rounded bg-bg-tertiary/40 border border-border/50">
      <div className="flex gap-2">
        <input
          type="number"
          min={0}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="Amount"
          className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary w-24"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What for? (e.g. hosting)"
          className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary flex-1"
        />
        <input
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          maxLength={8}
          className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary w-16"
        />
      </div>
      <div className="flex items-center gap-1">
        {RECUR_PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => setCron(p.cron)}
            className={`text-[10px] px-2 py-0.5 rounded border ${
              cron === p.cron
                ? "border-accent/40 bg-accent-soft text-accent"
                : "border-border text-text-muted hover:bg-bg-hover"
            }`}
          >
            {p.label}
          </button>
        ))}
        <button
          onClick={() => void submit()}
          disabled={busy}
          className="ml-auto text-[10px] px-3 py-1 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
        >
          {busy ? "Saving…" : cron ? "Schedule" : "Add"}
        </button>
      </div>
      {err && <div className="text-[10px] text-rose-400">{err}</div>}
    </div>
  );
}
