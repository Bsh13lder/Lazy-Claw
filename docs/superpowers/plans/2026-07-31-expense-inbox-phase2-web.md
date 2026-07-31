# Expense Inbox — Phase 2 (Web) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the web Expenses tab an 📥 Inbox chip with single assign, multi-select bulk assign, and AI auto-assign — all riding the Phase 1 server endpoints.

**Architecture:** Inbox = the project with `name_key === "general"` (already on the web `Project` interface, `api.ts:821`). All assigns are `PATCH /api/budgets/expenses/{id}` with `project_id` (+ optional `task_id`); suggestions come from `POST /api/budgets/inbox/suggestions`. UI work is confined to `web/src/components/budgets/ExpensesView.tsx` + `ExpenseRow.tsx` + `api.ts`.

**Tech Stack:** React 19 + TypeScript + Tailwind (inline classes, no chip component library), Vite.

## Global Constraints

- Web root: `/Users/blckit/Desktop/Code_Projects/lazyclaw/web`. NO test framework exists — verification per task = `npm run build` (tsc + vite, must be clean) and `npm run lint` (no NEW warnings/errors vs. pre-task baseline — run once before your first change to capture the baseline).
- Match existing patterns exactly: the "★ Starred only" inline-button chip (`ExpensesView.tsx:162-172`, active/inactive className pair at `:165-169`), the grouped ledger (`:78-99` buckets by `project_id`, headers at `:219-234`), `fmtMoney` from `money.ts`. Never hard-code colors outside the Tailwind tokens already used in the file.
- Line anchors in this plan were verified 2026-07-30 and the web files are unchanged since — they may still drift a few lines; read before editing.
- Commit style `<type>: <description>`, no AI attribution, explicit `git add` paths.
- The Phase 1 server contract (already on main): `UpdateExpenseBody` accepts `project_id` (404 unknown, 400 null); suggestions endpoint returns `{suggestions: [{expense_id, project_id|null, project_name|null, confidence, reason}], skipped}` — `expense_ids` omitted/null = all inbox (capped 10), `[]` = none.

---

### Task 1: `api.ts` — generic `updateExpense` + `getInboxSuggestions`

**Files:**
- Modify: `web/src/api.ts` (expense block around `:897-937`)

**Interfaces:**
- Produces: `updateExpense(expenseId: string, patch: ExpensePatch): Promise<void>` where `ExpensePatch = Partial<Pick<Expense, "amount" | "currency" | "description" | "vendor" | "notes" | "project_id" | "task_id" | "spent_at" | "is_favorite">>`; `interface InboxSuggestion { expense_id: string; project_id: string | null; project_name: string | null; confidence: string; reason: string | null }`; `getInboxSuggestions(expenseIds?: string[]): Promise<{ suggestions: InboxSuggestion[]; skipped: number }>`.

- [ ] **Step 1: Read the existing expense functions** (`listAllExpenses:905`, `setExpenseFavorite:933`) and mirror their exact request-helper idiom (same fetch wrapper, error handling, headers).

- [ ] **Step 2: Implement** — add `ExpensePatch` type + `updateExpense` (PATCH `/api/budgets/expenses/${id}` with the patch as JSON body); rewrite `setExpenseFavorite` as a one-line delegate: `return updateExpense(expenseId, { is_favorite: isFavorite })` (keep its export signature untouched). Add `InboxSuggestion` + `getInboxSuggestions` (POST `/api/budgets/inbox/suggestions`, body `{ expense_ids: expenseIds ?? null }` — pass `[]` through as `[]`, it means "none" server-side).

- [ ] **Step 3: Verify** — `cd web && npm run build` clean; `npm run lint` no new findings.

- [ ] **Step 4: Commit** — `git add web/src/api.ts && git commit -m "feat(web): generic updateExpense + inbox suggestions api"`

---

### Task 2: 📥 Inbox chip + filter in ExpensesView

**Files:**
- Modify: `web/src/components/budgets/ExpensesView.tsx`

**Interfaces:**
- Consumes: the component already loads projects for its add-form select (options built `:128-135`) and groups expenses `:78-99`.
- Produces: state `inboxOnly: boolean`; helper `const inboxProject = projects.find(p => p.name_key === "general")`; `const inboxCount = expenses.filter(e => e.project_id === inboxProject?.id).length`. Later tasks rely on `inboxOnly` + `inboxProject`.

- [ ] **Step 1: Implement the chip** — next to the Starred toggle (`:162-172`), same inline-button pattern, label `📥 Inbox` with the count when `inboxCount > 0` (e.g. `📥 Inbox (3)`). Render the chip only when `inboxProject` exists AND `inboxCount > 0`. Active state: the amber pair is taken by Starred — use the accent pair from `ExpenseAdder.tsx:75-87` (`border-accent/40 bg-accent-soft text-accent`) for active, the same muted inactive pair as Starred.
- [ ] **Step 2: Filter behavior** — when `inboxOnly`, render only the General group; compose with `starredOnly` (both filters AND together, same as starred applies at `:80`/`:104`). Totals keep their existing follow-the-visible-set semantics.
- [ ] **Step 3: Sort pin** — when NOT filtering, sort the General group first (before the alphabetical sort at `:98`) so the inbox backlog is always on top.
- [ ] **Step 4: Verify** — `npm run build` + `npm run lint` clean.
- [ ] **Step 5: Commit** — `git add web/src/components/budgets/ExpensesView.tsx && git commit -m "feat(web): inbox chip, filter and pinned inbox group in expenses view"`

---

### Task 3: Single-expense Assign control on inbox rows

**Files:**
- Modify: `web/src/components/budgets/ExpenseRow.tsx`, `web/src/components/budgets/ExpensesView.tsx`

**Interfaces:**
- Consumes: `updateExpense` (Task 1), `inboxProject` (Task 2). Tasks-by-project: find the task-list fetch that `TaskExpensePanel.tsx` or `Tasks.tsx` uses and filter client-side by `task.category` casefold-equal to the target project's `name_key` — the same join the server uses.
- Produces: `ExpenseRow` gains optional props `assignable?: boolean` and `onAssign?: (projectId: string, taskId: string | null) => void`; ExpensesView passes them only for rows in the General group.

- [ ] **Step 1: Row affordance** — when `assignable`, render an "Assign" button (same muted text-button style as the `×` delete at `ExpenseRow.tsx:39-44`) toggling a compact inline panel under the row: project `<select>` (all projects except General — reuse the option idiom from `ExpensesView.tsx:128-135`), optional task `<select>` for the chosen project ("(no task)" default), confirm button → `onAssign(projectId, taskId)`.
- [ ] **Step 2: Wire in ExpensesView** — `onAssign` = `await updateExpense(expense.id, { project_id, task_id })` then refetch (same reload path star/delete use) and call `onChanged()` (prop from `Tasks.tsx:854`) so the budget rail refreshes.
- [ ] **Step 3: Error handling** — a 404 (project deleted meanwhile) must surface: reuse the file's existing error display pattern (error state near the totals block with the server detail). Never silently swallow.
- [ ] **Step 4: Verify** — `npm run build` + `npm run lint` clean.
- [ ] **Step 5: Commit** — `git add web/src/components/budgets/ExpenseRow.tsx web/src/components/budgets/ExpensesView.tsx && git commit -m "feat(web): single-expense assign control on inbox rows"`

---

### Task 4: Multi-select + bulk bar + Auto-assign

**Files:**
- Modify: `web/src/components/budgets/ExpensesView.tsx` (and `ExpenseRow.tsx` if a checkbox prop is cleanest)

**Interfaces:**
- Consumes: `updateExpense`, `getInboxSuggestions`, `inboxOnly`, `inboxProject`.
- Produces: when `inboxOnly` — checkbox per inbox row (`selected: Set<string>`), bulk bar above the list: `Select all · N selected · [Assign to <project select> ✓] · [✨ Auto-assign]`.

- [ ] **Step 1: Selection state** — `Set<string>` of expense ids; checkbox per row only when `inboxOnly`; "Select all" toggles the visible inbox set. First multi-select in the web app — keep it local to this component.
- [ ] **Step 2: Bulk assign** — target project select (all except General) + confirm → sequential loop of `updateExpense(id, { project_id })` (counts ≤ dozens; sequential keeps failures attributable), collect failures, refetch + `onChanged()`. Inline summary "Moved X of Y" listing failures if any.
- [ ] **Step 3: Auto-assign** — `getInboxSuggestions(selected.size ? [...selected] : undefined)` → render each suggestion inline on its row ("→ ClubBay · high — club spend") with per-row Apply + one "Apply all confident" button looping `updateExpense` over suggestions with `project_id && (confidence === "high" || confidence === "medium")`. Show `skipped` when non-zero ("N more not analyzed — run again"). `project_id === null` renders "no match".
- [ ] **Step 4: Verify** — `npm run build` + `npm run lint` clean.
- [ ] **Step 5: Commit** — `git add web/src/components/budgets/ExpensesView.tsx web/src/components/budgets/ExpenseRow.tsx && git commit -m "feat(web): bulk select, bulk assign and AI auto-assign for the inbox"`

---

### Task 5: Verification sweep

- [ ] **Step 1:** `cd web && npm run build` clean; `npm run lint` — no new findings vs. the pre-Task-1 baseline.
- [ ] **Step 2:** `grep -n "name_key" web/src/components/budgets/ExpensesView.tsx` — inbox detection must use `name_key === "general"`, never the display name.
- [ ] **Step 3:** Confirm `setExpenseFavorite` is still exported (other components import it).
- [ ] **Step 4:** Report done. Note: the container serves the OLD web bundle until `make rebuild`.
