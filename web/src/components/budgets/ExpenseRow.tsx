import { useMemo, useState } from "react";
import * as api from "../../api";
import type { Expense, InboxSuggestion, Project, TaskItem } from "../../api";
import { fmtMoney } from "./money";

/**
 * One expense line in a ledger — date, description, amount, delete. Shared by
 * the per-project ExpenseLog and the global (grouped) Expenses view. Deleting
 * calls the parent's ``onChanged`` so both the list and any budget bar refresh.
 *
 * Inbox rows (the catch-all "General" project) additionally get an "Assign"
 * affordance — a compact inline panel to move the expense onto a real project
 * (and optionally one of that project's tasks). The parent only passes
 * ``assignable``/``onAssign`` for General-group rows.
 *
 * When the inbox is being multi-selected/bulk-assigned (the parent's
 * `inboxOnly` mode), the parent additionally passes a leading checkbox
 * (`selectable`/`selected`/`onToggleSelect`) and an inline AI auto-assign
 * suggestion (`suggestion`/`onApplySuggestion`) for this row's own id.
 */
export function ExpenseRow({
  date, expense, onChanged, assignable, onAssign, assignProjects, assignTasks, bulkLocked,
  selectable, selected, onToggleSelect,
  suggestion, onApplySuggestion, suggestionBusy, suggestionLocked,
}: {
  date: string;
  expense: Expense;
  onChanged: () => void;
  /** True only for rows the parent has decided are eligible to leave the
   *  General inbox. */
  assignable?: boolean;
  /** Commits the pick. The parent PATCHes the expense, refetches, and calls
   *  onChanged — this row disappears from the inbox group once it succeeds. */
  onAssign?: (projectId: string, taskId: string | null) => void;
  /** Target projects for the picker (every project except General). */
  assignProjects?: Project[];
  /** Every task, so the panel can narrow to the picked project's tasks by
   *  category (casefold-equal to the project's name_key — the same join the
   *  server uses). */
  assignTasks?: TaskItem[];
  /** True while a bulk-family write (bulk-assign, auto-assign fetch, or
   *  apply/apply-all) is running elsewhere in the parent. Those loops PATCH
   *  the same inbox rows, so the single-row Assign affordance must lock out
   *  too — otherwise a concurrent single-row PATCH races the bulk loop. */
  bulkLocked?: boolean;
  /** True only while the parent's bulk-select mode (inboxOnly) is active. */
  selectable?: boolean;
  /** Whether this row's id is in the parent's selection Set. */
  selected?: boolean;
  /** Toggles this row's id in the parent's selection Set. */
  onToggleSelect?: () => void;
  /** This row's AI auto-assign suggestion, if the parent has fetched one and
   *  it hasn't been pruned (row already left the inbox / already applied). */
  suggestion?: InboxSuggestion | null;
  /** Applies `suggestion` for this row only. Absent/no-op when the
   *  suggestion has no `project_id` ("no match" — nothing to apply). */
  onApplySuggestion?: () => void;
  /** True while THIS row's suggestion is mid-apply. */
  suggestionBusy?: boolean;
  /** True while some OTHER apply (a different row, or "Apply all confident")
   *  is in flight — blocks this row's Apply button too, so two loops can
   *  never race the same suggestion set. */
  suggestionLocked?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [pickedProjectId, setPickedProjectId] = useState("");
  const [pickedTaskId, setPickedTaskId] = useState("");

  const remove = async () => {
    setBusy(true);
    try { await api.deleteExpense(expense.id); onChanged(); }
    finally { setBusy(false); }
  };
  const toggleStar = async () => {
    setBusy(true);
    try { await api.setExpenseFavorite(expense.id, !expense.is_favorite); onChanged(); }
    finally { setBusy(false); }
  };

  const pickedProject = (assignProjects || []).find((p) => p.id === pickedProjectId) ?? null;

  // Same join the server uses: task.category casefold-equal to the target
  // project's name_key.
  const tasksForPickedProject = useMemo(() => {
    if (!pickedProject) return [];
    const key = pickedProject.name_key;
    return (assignTasks || []).filter(
      (t) => (t.category || "").trim().toLowerCase() === key,
    );
  }, [assignTasks, pickedProject]);

  const openAssign = () => {
    setPickedProjectId(assignProjects?.[0]?.id || "");
    setPickedTaskId("");
    setAssignOpen(true);
  };

  // Re-entrancy guard: `disabled={busy || bulkLocked}` already keeps a
  // second click from firing while a request is in flight (this row's own,
  // or the parent's bulk family), but this belt-and-braces check covers a
  // stray Enter-key submit racing the click handler.
  const confirmAssign = async () => {
    if (busy || bulkLocked || !onAssign || !pickedProjectId) return;
    setBusy(true);
    try {
      await onAssign(pickedProjectId, pickedTaskId || null);
      setAssignOpen(false);
    } catch {
      // The parent already surfaced the failure (e.g. a 404 when the target
      // project was deleted elsewhere) in its own error banner. Leave the
      // panel open so the user can pick a different target instead of
      // silently losing their in-progress pick.
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 text-[11px] text-text-secondary px-2 py-1 rounded bg-bg-tertiary/40">
        {selectable && (
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onToggleSelect?.()}
            disabled={busy}
            title="Select for bulk actions"
            className="accent-accent shrink-0"
          />
        )}
        <button
          onClick={() => void toggleStar()}
          className={expense.is_favorite ? "text-amber-400" : "text-text-muted hover:text-amber-400"}
          title={expense.is_favorite ? "Un-star expense" : "Star expense"}
          disabled={busy}
        >{expense.is_favorite ? "★" : "☆"}</button>
        <span className="text-text-muted w-20 shrink-0">{date}</span>
        <span className="flex-1 truncate">
          {expense.description || expense.vendor || "expense"}
          {expense.recurring_expense_id && <span className="text-accent/60"> · recurring</span>}
        </span>
        <span>− {fmtMoney(expense.amount, expense.currency)}</span>
        {assignable && onAssign && (
          <button
            onClick={() => (assignOpen ? setAssignOpen(false) : openAssign())}
            className={assignOpen ? "text-accent" : "text-text-muted hover:text-accent"}
            title={bulkLocked ? "A bulk operation is running — wait for it to finish" : "Assign to a project"}
            disabled={busy || bulkLocked}
          >
            Assign
          </button>
        )}
        <button
          onClick={() => void remove()}
          className="text-text-muted hover:text-rose-400"
          title="Delete expense"
          disabled={busy}
        >×</button>
      </div>

      {assignable && onAssign && assignOpen && (
        <div className="flex items-center gap-2 pl-2 pr-2 pb-1 text-[11px] flex-wrap">
          {(assignProjects || []).length === 0 ? (
            <span className="text-text-muted">No other projects yet — create one to assign this expense.</span>
          ) : (
            <>
              <select
                value={pickedProjectId}
                onChange={(e) => { setPickedProjectId(e.target.value); setPickedTaskId(""); }}
                disabled={busy || bulkLocked}
                className="bg-bg-primary border border-border rounded px-2 py-1 text-text-primary"
              >
                {(assignProjects || []).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <select
                value={pickedTaskId}
                onChange={(e) => setPickedTaskId(e.target.value)}
                disabled={busy || bulkLocked || !pickedProjectId}
                className="bg-bg-primary border border-border rounded px-2 py-1 text-text-primary"
              >
                <option value="">(no task)</option>
                {tasksForPickedProject.map((t) => (
                  <option key={t.id} value={t.id}>{t.title}</option>
                ))}
              </select>
              <button
                onClick={() => void confirmAssign()}
                disabled={busy || bulkLocked || !pickedProjectId}
                className="px-2.5 py-1 rounded border border-emerald-400/40 text-emerald-300 bg-emerald-400/10 hover:bg-emerald-400/20 disabled:opacity-40"
              >
                {busy ? "Assigning…" : "Confirm"}
              </button>
            </>
          )}
          <button
            onClick={() => setAssignOpen(false)}
            disabled={busy}
            className="text-text-muted hover:text-text-primary"
          >
            Cancel
          </button>
        </div>
      )}

      {suggestion && (
        <div className="flex items-center gap-2 pl-2 pr-2 pb-1 text-[11px] text-text-secondary flex-wrap">
          {suggestion.project_id ? (
            <>
              <span>
                → <span className="text-accent">{suggestion.project_name || "project"}</span>
                {" · "}{suggestion.confidence}
                {suggestion.reason && <span className="text-text-muted"> — {suggestion.reason}</span>}
              </span>
              <button
                onClick={() => onApplySuggestion?.()}
                disabled={suggestionBusy || suggestionLocked}
                className="text-emerald-300 hover:text-emerald-200 disabled:opacity-40"
              >
                {suggestionBusy ? "Applying…" : "Apply"}
              </button>
            </>
          ) : (
            <span className="text-text-muted">
              No match{suggestion.reason ? ` — ${suggestion.reason}` : ""}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
