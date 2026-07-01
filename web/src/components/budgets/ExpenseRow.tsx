import { useState } from "react";
import * as api from "../../api";
import type { Expense } from "../../api";
import { fmtMoney } from "./money";

/**
 * One expense line in a ledger — date, description, amount, delete. Shared by
 * the per-project ExpenseLog and the global (grouped) Expenses view. Deleting
 * calls the parent's ``onChanged`` so both the list and any budget bar refresh.
 */
export function ExpenseRow({
  date, expense, onChanged,
}: { date: string; expense: Expense; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
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
  return (
    <div className="flex items-center gap-2 text-[11px] text-text-secondary px-2 py-1 rounded bg-bg-tertiary/40">
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
      <button
        onClick={() => void remove()}
        className="text-text-muted hover:text-rose-400"
        title="Delete expense"
        disabled={busy}
      >×</button>
    </div>
  );
}
