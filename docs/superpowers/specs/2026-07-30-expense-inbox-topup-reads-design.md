# Expense Inbox + Budget Top-up Reads — Design

**Date:** 2026-07-30
**Status:** Approved (user, in-chat, section-by-section)
**Origin:** User report: "agent has no data about my top-ups on projects" + "make expense logging easy — unassigned expenses go to an Inbox, assign parent project/task later, manual and auto, with bulk assign."

## Problem

1. **Top-ups are write-only in the skill layer.** `budget_entries` (the top-up ledger) is written by `add_project_budget` (`lazyclaw/skills/builtin/budget_manager.py:236`) but no skill reads it — `store.list_budget_entries` (`lazyclaw/budgets/store.py:1117`) is consumed only by the web/mobile HTTP route. `expense_report` → `spending_report` (`store.py:1032`) returns only `budget/spent/remaining`. The agent cannot answer "show my top-ups".
2. **No triage loop for unassigned expenses.** `add_expense` with no project silently lands in the catch-all `General` project (`budget_manager.py:400-410`) — but nothing surfaces them as "needs a home", no skill can move an expense, and neither client has an Inbox view or bulk assignment.
3. **Latent production bug (found during research):** mobile already sends `project_id` in the expense PATCH body (`mobile/lib/local/budgets_dao.dart:644-651` → `budgets_sync.dart:254`), but `UpdateExpenseBody` (`lazyclaw/gateway/routes/budgets.py:90-101`) does not declare `project_id`, so Pydantic (`extra='ignore'`) silently drops it. A project move on mobile appears to work locally, then reverts on the next `/changes` pull.

## Decisions (user-approved)

- **Inbox model:** reuse the existing `General` project as the Inbox, detected by plaintext `name_key == "general"` (already delivered to web `api.ts:821` and mobile `project.dart:9` — no server change for detection). No schema change, no rename, no migration.
- **Surfaces:** agent (NL/chat) + web + mobile — full treatment, phased.
- **Assignment modes:** manual single, bulk multi-select, and auto (worker-LLM suggestions, smart-intake pattern).
- **Top-up reads included** in this pass. Audit-ledger side defects (`set_project_budget` ledger bypass, `budget=0` truthiness, other drift paths) explicitly **out of scope** — follow-up.

## Design

### B. Server

1. **`UpdateExpenseBody` gains `project_id: str | None`** (`gateway/routes/budgets.py:90-101`). Route validates the target project exists and belongs to the user (contrast `create_expense`'s check at `store.py:768`); invalid → 404. `store.update_expense` (`store.py:887`, generic `**fields`) already persists it. This fixes bug #3 and is the single write path every assign flow rides on.
2. **`GENERAL_PROJECT_NAME` moves to `lazyclaw/budgets/store.py`** (beside `_name_key`, `store.py:90`); `budget_manager.py` re-imports it.
3. **`lazyclaw/budgets/inbox_suggest.py`** — `suggest_expense_project(config, user_id, description, vendor, amount, timeout_s=3.0)` mirroring `tasks/smart_intake.py`: frozen dataclass result `{project_name, task_id, confidence: high|medium|low|none, reason, source}`, ROLE_WORKER model, `asyncio.wait_for` 3s, **never raises**, prompt includes existing project names + recent per-project expense descriptions so it reuses the user's buckets. PII-free debug trace.
4. **`POST /api/budgets/inbox/suggestions`** — body `{expense_ids?: [..]}` (default: all posted, non-deleted expenses in the General project). Returns per-expense suggestions. Read-only; applying a suggestion is always the normal `PATCH /expenses/{id}`. No bulk-write endpoint — clients loop PATCH (counts are small).
5. **LazyBrain note re-point (best-effort):** when a move changes `project_id`, update the mirrored expense note's project wikilink; wrapped in try/except + log, never fails the move.

### C. Agent skills (`skills/builtin/budget_manager.py` + wiring)

New skills:
- **`move_expense`** — assign/move expense(s) to a project, optionally a task. Params: `query` (fuzzy match on description/vendor/amount, most-recent-first), `project` (target, required), `task_name?`, `from_project?` (default: inbox/General), `all_matching?: bool`, `all_inbox?: bool` (bulk). Precision-first resolution identical to `add_expense` (`budget_manager.py:349-377`): exact → auto; single fuzzy → auto with note; multi-match → clarification via the `budget_pending` candidates pattern, **never guesses**. Task join: `category == project.name_key` + `resolver.resolve_task` (`budget_manager.py:415-434`).
- **`auto_assign_inbox`** (write) — runs `suggest_expense_project` over inbox expenses; **applies only `high`/`medium` confidence** (same gate as `task_manager.py:396-400`); returns applied moves + uncertain leftovers as a candidate list for the user to confirm.
- **`list_projects`** (read_only) — enumerate projects (name, budget, spent, remaining, status). Fixes the phantom allowlist entries at `runtime/agent.py:1357`.
- **`list_budget_topups`** (read_only) — top-up ledger per project or all projects (loops `list_projects` × `list_budget_entries`). Description must contain: "top up", "top-up", "topup", "ledger", "budget history", "money added", "funding" (substring discovery, `skills/builtin/tool_discovery.py:92-93`).

Changed skills:
- **`list_expenses`**: `project` becomes optional → cross-project mode via `store.list_all_expenses` (`store.py:808`, already enriches `project_name`); every line shows its project. `project="inbox"` maps to General.
- **`expense_report`**: appends `📥 Inbox: N unassigned (total X)` when General has posted expenses.
- **`add_expense`**: when the fallback lands in General, confirmation reads "Logged … to 📥 Inbox — tell me a project anytime to file it."

Wiring (all load-bearing, per the 2026-06-10 allowlist incident):
- `_BUDGET_TOOL_NAMES` (`runtime/agent.py:975`) += `move_expense, auto_assign_inbox, list_projects, list_budget_topups`.
- Inline-read allowlist (`runtime/agent.py:1355-1358`): add `list_budget_topups`; keep `list_projects` (now real); reads only — writes stay out.
- `_BUDGET_KEYWORDS` (`runtime/agent.py:965-973`) += `"top up", "top-up", "topup", "inbox", "assign"`.
- `teams/specialists/tasks_specialist.md` `tools:` += the four new skills.
- `personality/SOUL.md:85` hint line += new skill names.

### D. Web (`web/src/components/budgets/ExpensesView.tsx`)

- **📥 Inbox chip** beside "★ Starred only" (`:162-172`, same inline button pattern) with a count badge; active → only the General group renders.
- Inbox rows: **Assign** control (project select + optional task). Chip active → checkboxes per row + bulk bar: "Assign N to…" + "Auto-assign" (calls suggestions endpoint, previews, applies via PATCH loop).
- `api.ts`: add generic `updateExpense(id, patch)` (replaces hardcoded `setExpenseFavorite` internals; keep the old export delegating) + `getInboxSuggestions(expenseIds?)`.

### E. Mobile (`mobile/lib/screens/expenses_screen.dart` + friends)

- **Ledger filter Row 3** (`:1286-1345`): General's chip becomes a pinned **📥 Inbox** chip (after All/★ Favorites), `AppColors.warn`, count badge; General excluded from the regular per-project chips. Detection: `p.nameKey == 'general'` with fallback `p.name.toLowerCase() == 'general'` for legacy cached rows (nullable `nameKey`).
- **Multi-select** (first in app): with Inbox filter active, long-press enters selection mode → checkmarks on rows + bulk bar (Assign → project/task picker sheet; Auto → suggestions endpoint, preview, apply). Built from Lz kit only.
- **Expense detail sheet** (`expense_detail_sheet.dart`): add task picker (tasks filtered by `category == project.name_key`); thread `taskId` through `BudgetsNotifier.updateExpense` (`budgets_provider.dart:318`) → `applyLocalExpenseUpdate` patch map (`budgets_dao.dart:644-651`) → wire (server accepts `task_id` today, `budgets.py:96`).
- **No sync-engine changes**: moves are ordinary expense `update` outbox ops (coalesced, LWW). Server fix B1 makes them stick. No schema bump, no cursor touch.

### F. Testing & rollout

Phased; each phase verified before the next; **never run pytest against the live container DB**.

- **Phase 1 — server + agent:** pytest — PATCH `project_id` (persists, ownership-validated, cross-user 404, regression for the silent-drop bug), suggestions endpoint (LLM mocked with production exception shapes), `move_expense` resolution matrix (exact/fuzzy/multi/none × task variants), bulk paths, `auto_assign_inbox` confidence gate, `list_budget_topups`, `list_projects`, keyword/allowlist wiring. TDD per repo convention.
- **Phase 2 — web:** Inbox chip filter, assign control, bulk bar, api functions.
- **Phase 3 — mobile:** provider/DAO tests for `taskId` threading + project move (fake transports throwing `DioException(error: ApiError)` per sync-engine lesson); widget tests with fakes (sqflite + FakeAsync hangs).

## Out of scope (follow-ups)

- `set_project_budget` audit-ledger bypass; `budget=0` truthiness bug (`store.py:298`); `PATCH /projects/{id}` budget writes without ledger row; mobile offline `creditProjectBudget` ledger skip; `add_budget_entry` read-modify-write race; cross-project budget-entries endpoint.
- Dedicated Inbox push notifications / nagging.
