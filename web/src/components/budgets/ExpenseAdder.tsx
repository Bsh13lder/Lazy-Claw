import { useState } from "react";
import * as api from "../../api";

/**
 * Add a one-off or recurring expense to a project (by name — the project is
 * upserted if it doesn't exist yet, so picking "General" reuses the catch-all).
 * Shared by the per-project ProjectPanel and the global Expenses view.
 */

const RECUR_PRESETS: { label: string; cron: string }[] = [
  { label: "One-off", cron: "" },
  { label: "Monthly", cron: "0 0 1 * *" },
  { label: "Weekly", cron: "0 0 * * 1" },
  { label: "Yearly", cron: "0 0 1 1 *" },
];

export function ProjectExpenseAdder({
  projectName, defaultCurrency, onSaved,
}: { projectName: string; defaultCurrency: string; onSaved: () => void }) {
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [currency, setCurrency] = useState(defaultCurrency);
  const [cron, setCron] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    const value = parseFloat(amount);
    if (!Number.isFinite(value) || value <= 0) { setErr("Enter an amount."); return; }
    setBusy(true);
    try {
      const proj = await api.createProject({ name: projectName, currency });
      if (cron) {
        await api.createRecurringExpense(proj.id, {
          amount: value, cron_expression: cron, currency,
          description: description.trim() || undefined,
        });
      } else {
        await api.createExpense(proj.id, {
          amount: value, currency,
          description: description.trim() || undefined,
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
    <div className="mt-2 flex flex-col gap-2 p-2 rounded-lg bg-bg-tertiary/40 border border-border/50">
      <div className="flex gap-2">
        <input
          type="number" min={0} value={amount} autoFocus
          onChange={(e) => setAmount(e.target.value)}
          placeholder="Amount"
          className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary w-24"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What for? (e.g. domain renewal)"
          className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary flex-1"
        />
        <input
          value={currency} maxLength={8}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
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
          onClick={() => void submit()} disabled={busy}
          className="ml-auto text-[10px] px-3 py-1 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
        >
          {busy ? "Saving…" : cron ? "Schedule" : "Add"}
        </button>
      </div>
      {err && <div className="text-[10px] text-rose-400">{err}</div>}
    </div>
  );
}
