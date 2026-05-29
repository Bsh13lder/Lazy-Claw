/** Format a monetary amount with its currency, e.g. `1,200 EUR`. Shared across
 * the budget/expense components (kept in its own module so the component files
 * only export components — satisfies react-refresh/only-export-components). */
export function fmtMoney(amount: number | null | undefined, currency?: string | null): string {
  const n = typeof amount === "number" ? amount : 0;
  return `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency || "EUR"}`;
}
