import { useEffect, useMemo, useState } from "react";
import * as api from "../../api";
import type { Project } from "../../api";

/**
 * Projects rail section — lists budgeted projects with a budget-vs-spent bar
 * plus any task categories that don't have a budget yet (auto-detected). A
 * "+ New project" button creates one upfront. Clicking a project filters the
 * task list to that project; clicking the selected one clears the filter.
 *
 * The rail key for filtering is the project's ``name_key`` (casefold name),
 * which matches ``task.category`` casefolded — the same join the backend uses.
 */

export function fmtMoney(amount: number | null | undefined, currency?: string | null): string {
  const n = typeof amount === "number" ? amount : 0;
  return `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency || "EUR"}`;
}

interface RailEntry {
  key: string;
  name: string;
  project: Project | null; // null = category with no budget yet
}

interface Props {
  /** Distinct task categories from the current task list (for auto-detect). */
  categories: string[];
  /** Currently selected project name_key, or null for "all". */
  selectedKey: string | null;
  onSelect: (key: string | null) => void;
  /** Bumped by the parent to force a refetch (e.g. after an expense changes). */
  reloadKey?: number;
}

export function ProjectBudgetRail({ categories, selectedKey, onSelect, reloadKey = 0 }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [prefillName, setPrefillName] = useState<string>("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    api.listProjects("active")
      .then((p) => { if (alive) setProjects(p); })
      .catch(() => { if (alive) setProjects([]); });
    return () => { alive = false; };
  }, [reloadKey, tick]);

  const entries = useMemo<RailEntry[]>(() => {
    const byKey = new Map<string, RailEntry>();
    for (const p of projects) {
      byKey.set(p.name_key, { key: p.name_key, name: p.name, project: p });
    }
    for (const c of categories) {
      const k = c.trim().toLowerCase();
      if (!k || byKey.has(k)) continue;
      byKey.set(k, { key: k, name: c, project: null });
    }
    return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [projects, categories]);

  if (entries.length === 0 && !createOpen) {
    return (
      <div>
        <RailHeader onNew={() => { setPrefillName(""); setCreateOpen(true); }} />
        <p className="text-[10px] text-text-muted px-2.5 py-1.5">
          No projects yet. Tag a task with a category, or create one.
        </p>
        {createOpen && (
          <NewProjectModal
            prefillName={prefillName}
            onClose={() => setCreateOpen(false)}
            onCreated={() => { setCreateOpen(false); setTick((n) => n + 1); }}
          />
        )}
      </div>
    );
  }

  return (
    <div>
      <RailHeader onNew={() => { setPrefillName(""); setCreateOpen(true); }} />
      <div className="flex flex-col gap-1">
        {entries.map((e) => {
          const active = selectedKey === e.key;
          const proj = e.project;
          const budget = proj?.budget ?? 0;
          const spent = proj?.spent ?? 0;
          const pct = budget > 0 ? Math.min(100, (spent / budget) * 100) : 0;
          const tone =
            pct >= 100 ? "bg-red-400" : pct >= 80 ? "bg-amber" : "bg-accent";
          return (
            <button
              key={e.key}
              onClick={() => onSelect(active ? null : e.key)}
              className={`text-left px-2.5 py-1.5 rounded-md transition-colors border ${
                active
                  ? "border-accent/40 bg-accent-soft text-accent"
                  : "border-transparent text-text-secondary hover:bg-bg-hover"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-medium flex-1 truncate">{e.name}</span>
                {proj ? (
                  <span className="text-[9.5px] text-text-muted whitespace-nowrap">
                    {fmtMoney(spent, proj.currency)} / {fmtMoney(budget, proj.currency)}
                  </span>
                ) : (
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(ev) => { ev.stopPropagation(); setPrefillName(e.name); setCreateOpen(true); }}
                    className="text-[9.5px] text-accent/70 hover:text-accent whitespace-nowrap"
                  >
                    set budget
                  </span>
                )}
              </div>
              {proj && budget > 0 && (
                <div className="mt-1 h-1 rounded-full bg-bg-tertiary overflow-hidden">
                  <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
                </div>
              )}
            </button>
          );
        })}
      </div>

      {createOpen && (
        <NewProjectModal
          prefillName={prefillName}
          onClose={() => setCreateOpen(false)}
          onCreated={() => { setCreateOpen(false); setTick((n) => n + 1); }}
        />
      )}
    </div>
  );
}

function RailHeader({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex items-center justify-between mb-2">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
        Projects
      </p>
      <button
        onClick={onNew}
        className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20"
      >
        + New
      </button>
    </div>
  );
}

interface ModalProps {
  prefillName?: string;
  onClose: () => void;
  onCreated: () => void;
}

function NewProjectModal({ prefillName = "", onClose, onCreated }: ModalProps) {
  const [name, setName] = useState(prefillName);
  const [budget, setBudget] = useState("");
  const [currency, setCurrency] = useState("EUR");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    if (!name.trim()) {
      setErr("Project name is required.");
      return;
    }
    const amount = parseFloat(budget);
    setBusy(true);
    try {
      await api.createProject({
        name: name.trim(),
        budget: Number.isFinite(amount) ? amount : 0,
        currency: currency.trim() || "EUR",
      });
      onCreated();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md bg-bg-secondary border border-border rounded-lg shadow-xl p-5 flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-text-primary flex-1">New project</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary text-lg leading-none">×</button>
        </div>

        <label className="text-xs text-text-secondary flex flex-col gap-1">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. nima"
            className="bg-bg-primary border border-border rounded px-2 py-1.5 text-sm text-text-primary"
          />
        </label>

        <div className="flex gap-2">
          <label className="text-xs text-text-secondary flex flex-col gap-1 flex-1">
            Budget
            <input
              type="number"
              min={0}
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              placeholder="500"
              className="bg-bg-primary border border-border rounded px-2 py-1.5 text-sm text-text-primary"
            />
          </label>
          <label className="text-xs text-text-secondary flex flex-col gap-1 w-24">
            Currency
            <input
              value={currency}
              onChange={(e) => setCurrency(e.target.value.toUpperCase())}
              maxLength={8}
              className="bg-bg-primary border border-border rounded px-2 py-1.5 text-sm text-text-primary"
            />
          </label>
        </div>

        <p className="text-[10px] text-text-muted">
          Tasks tagged with this category roll up here. Expenses link to the
          project's note in LazyBrain.
        </p>

        {err && <div className="text-xs text-rose-400">{err}</div>}

        <div className="flex justify-end gap-2 mt-1">
          <button onClick={onClose} className="text-xs px-3 py-1.5 rounded border border-border text-text-secondary hover:bg-bg-hover">Cancel</button>
          <button
            onClick={() => void submit()}
            disabled={busy}
            className="text-xs px-3 py-1.5 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
          >
            {busy ? "Creating…" : "Create project"}
          </button>
        </div>
      </div>
    </div>
  );
}
