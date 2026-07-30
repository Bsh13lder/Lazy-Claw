"""Budget manager skills — set project budgets, log expenses, recurring spend.

NL control over ``lazyclaw.budgets.store``. A "project" is the task category;
these skills upsert the project by name so "set nima budget to 500" works
before any task exists. Every expense mirrors to a LazyBrain note wikilinking
the project page. Channel-agnostic — identical behaviour in Telegram, Web UI
chat, and CLI.
"""

from __future__ import annotations

import logging

from lazyclaw.budgets.store import GENERAL_PROJECT_NAME
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


def _fmt_money(amount, currency: str | None) -> str:
    try:
        return f"{float(amount):,.2f} {currency or 'EUR'}"
    except (TypeError, ValueError):
        return f"{amount} {currency or 'EUR'}"


class CreateProjectSkill(BaseSkill):
    """Create a first-class project (no budget required).

    This is the skill to call when the user says "create a project", "make a
    project for X", "start a private project", etc. It materializes a real
    ``projects`` row + its ``<Name> Project`` LazyBrain wikilink page so the
    project has a description, a graph node, and rename support — unlike a bare
    task category, which stays a lightweight phantom until it proves itself.

    Tasks tagged with this project's name (``category``) automatically roll up
    under it.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "create_project"

    @property
    def description(self) -> str:
        return (
            "Create a project the user explicitly asked for (e.g. 'create a "
            "private project', 'make a project for the kitchen reno'). Use the "
            "name the user gave (or an obvious one like 'Personal'/'Private'). "
            "Pass a short `description` explaining what the project is for — it "
            "becomes the project's wikilink page text. To then put a task under "
            "it, call add_task with category=<project name>. Don't use this for "
            "budgets ('set X budget') — that's set_project_budget."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, e.g. 'Private', 'Kitchen Reno'",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "1–2 sentence explanation of what the project is for. "
                        "Becomes the [[<Name> Project]] page body."
                    ),
                },
            },
            "required": ["project"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store

        project = (params.get("project") or "").strip()
        if not project:
            return "Project name is required."
        description = (params.get("description") or "").strip() or None

        existing = await store.get_project_by_name(self._config, user_id, project)
        proj = await store.create_project(
            self._config, user_id, project, description=description,
        )
        verb = "already exists" if existing else "created"
        suffix = " Add tasks to it with category=" + f"'{proj['name']}'."
        return f"Project **{proj['name']}** {verb}.{suffix}"


class SetProjectBudgetSkill(BaseSkill):
    """Create/update a project and set its budget."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_project_budget"

    @property
    def description(self) -> str:
        return (
            "Set or update the budget for a project (e.g. 'set nima budget to "
            "500 EUR'). Creates the project if it doesn't exist yet — the "
            "project name matches the task category, so tasks tagged with that "
            "category roll up under it. Use this for any 'budget for X' request."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (matches task category), e.g. 'nima'",
                },
                "budget": {
                    "type": "number",
                    "description": "Budget amount in the project's base currency",
                },
                "currency": {
                    "type": "string",
                    "description": "ISO currency code (default EUR)",
                },
            },
            "required": ["project", "budget"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store

        project = (params.get("project") or "").strip()
        if not project:
            return "Project name is required."
        try:
            budget = float(params.get("budget"))
        except (TypeError, ValueError):
            return "Budget must be a number."
        currency = (params.get("currency") or "EUR").strip() or "EUR"

        proj = await store.create_project(
            self._config, user_id, project, budget=budget, currency=currency,
        )
        return (
            f"Budget for **{proj['name']}** set to {_fmt_money(budget, currency)}."
        )


class AddProjectBudgetSkill(BaseSkill):
    """Add money to a project's budget (top-up), recording the source."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "add_project_budget"

    @property
    def description(self) -> str:
        return (
            "Add money to a project's budget — a top-up that INCREASES the "
            "budget (e.g. 'add 500 to nima budget'), distinct from "
            "set_project_budget which sets the total. ALWAYS capture the "
            "`source` of the money (where it came from, e.g. 'client deposit', "
            "'savings', 'invoice #12'). If the user adds budget without saying "
            "where it's from, ASK them for the source FIRST, then call this — "
            "the source is recorded as the first comment on the top-up."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name to top up, e.g. 'nima'",
                },
                "amount": {
                    "type": "number",
                    "description": "Amount to ADD to the existing budget",
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Where the money came from (client deposit, savings, "
                        "invoice, etc). Ask the user if not stated."
                    ),
                },
                "currency": {
                    "type": "string",
                    "description": "ISO currency code (defaults to project currency)",
                },
            },
            "required": ["project", "amount"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store

        project = (params.get("project") or "").strip()
        if not project:
            return "Project name is required."
        try:
            amount = float(params.get("amount"))
        except (TypeError, ValueError):
            return "Amount must be a number."
        source = (params.get("source") or "").strip() or None
        currency = (params.get("currency") or "").strip() or None

        proj = await store.create_project(self._config, user_id, project)
        entry = await store.add_budget_entry(
            self._config, user_id, proj["id"],
            amount=amount, source=source, currency=currency,
        )
        msg = f"Added {_fmt_money(amount, entry.get('currency'))} to **{proj['name']}** budget"
        if source:
            msg += f" (source: {source})"
        msg += f". New budget: {_fmt_money(entry.get('new_budget'), entry.get('currency'))}."
        return msg


class AddExpenseSkill(BaseSkill):
    """Log an expense against a project (optionally a specific task).

    Precision-first resolution:
      • exact (casefold) project match → auto-routes;
      • single substring/fuzzy match → auto-routes with a note;
      • multiple matches for a NAMED project → REFUSES to guess and returns a
        clarification listing candidates the brain relays to the user;
      • no project named (and no default set) → logs to the catch-all
        ``General`` project silently. Same disambiguation rules apply to
        ``task_name`` within the resolved project.

    Set ``set_default_expense_project`` to route lone-amount captures like
    'spent 12 on coffee' to a project of your choosing instead of ``General``.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "add_expense"

    @property
    def description(self) -> str:
        return (
            "Log a spent expense on a project (e.g. 'log 40 EUR hosting on "
            "nima'). If the user names a project that matches MULTIPLE, the "
            "skill returns a clarification listing candidates — relay that ask "
            "verbatim, then call add_expense again with their pick. If the user "
            "names NO project (e.g. 'spent 12 on coffee') it logs to the "
            "catch-all 'General' project automatically. Optionally attach to a "
            "specific task by passing `task_name` (fuzzy-matched within the "
            "project's tasks; multi-match returns a clarification too). "
            "Creates the project if needed; expense mirrors to a LazyBrain "
            "note wikilinking the project page."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name the expense belongs to, e.g. 'nima'. Omit to use the user's default project.",
                },
                "amount": {
                    "type": "number",
                    "description": "Amount spent",
                },
                "currency": {
                    "type": "string",
                    "description": "ISO currency code (defaults to the project's currency)",
                },
                "description": {
                    "type": "string",
                    "description": "What the money was spent on, e.g. 'monthly hosting'",
                },
                "vendor": {
                    "type": "string",
                    "description": "Optional vendor/payee, e.g. 'AWS'",
                },
                "task_name": {
                    "type": "string",
                    "description": "Optional task title (or partial) to attach this expense to within the chosen project. Fuzzy-matched.",
                },
                "spent_at": {
                    "type": "string",
                    "description": "Optional ISO date of the spend (YYYY-MM-DD); defaults to today",
                },
            },
            "required": ["amount"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import (
            pending as budget_pending,
            resolver,
            settings as budget_settings,
            store,
        )
        from lazyclaw.tasks.store import list_tasks

        try:
            amount = float(params.get("amount"))
        except (TypeError, ValueError):
            return "Amount must be a number."

        description = (params.get("description") or "").strip() or None
        vendor = (params.get("vendor") or "").strip() or None
        currency = (params.get("currency") or "").strip() or None
        spent_at = (params.get("spent_at") or "").strip() or None
        task_name = (params.get("task_name") or "").strip() or None
        project_query = (params.get("project") or "").strip()

        projects = await store.list_projects(self._config, user_id)

        # ── Resolve the project ─────────────────────────────────────────
        proj = None
        match_note = ""
        if project_query:
            res = resolver.resolve_project(project_query, projects)
            if res.resolved is not None:
                proj = await store.get_project(self._config, user_id, res.resolved.id)
                if res.reason in {"substring", "fuzzy"}:
                    match_note = f" (matched _{res.reason}_ to **{res.resolved.name}**)"
            elif res.reason == "multi":
                names = ", ".join(f"**{c.name}**" for c in res.candidates[:6])
                budget_pending.set_pending(budget_pending.PendingExpenseChoice(
                    user_id=user_id, amount=amount, currency=currency,
                    description=description, vendor=vendor, spent_at=spent_at,
                    task_name=task_name, kind="project",
                    candidates=tuple((c.id, c.name) for c in res.candidates[:6]),
                    project_id=None, project_name=None,
                    token=budget_pending.new_token(),
                ))
                return (
                    f"⚠️ Multiple projects match `{project_query}`: {names}. "
                    f"Tap one below or reply with the exact name to log "
                    f"{_fmt_money(amount, currency or 'EUR')}"
                    + (f" ({description})" if description else "")
                    + ". (No expense was logged.)"
                )
            else:
                # No match — fall through to default / suggestion.
                pass

        if proj is None:
            default = await budget_settings.get_default_expense_project(
                self._config, user_id,
            )
            if default:
                res = resolver.resolve_project(default, projects)
                if res.resolved is not None:
                    proj = await store.get_project(
                        self._config, user_id, res.resolved.id,
                    )
                    match_note = f" (default project **{res.resolved.name}**)"
                else:
                    # Default project name doesn't exist yet — create it.
                    proj = await store.create_project(self._config, user_id, default)
                    match_note = f" (default project **{proj['name']}**)"

        if proj is None and project_query:
            # User named a project but no match and no default — create it (their intent).
            proj = await store.create_project(self._config, user_id, project_query)
            match_note = f" (new project **{proj['name']}**)"

        if proj is None:
            # No project named and no default configured → log to the catch-all
            # "General" project (idempotent by name_key) rather than asking
            # back. Keeps lone-amount captures like "spent 12 on coffee"
            # frictionless; the expense still mirrors to the [[General Project]]
            # LazyBrain note. (An AMBIGUOUS named project — the "multi" branch
            # above — still asks back; only the truly-no-project path lands here.)
            proj = await store.create_project(
                self._config, user_id, GENERAL_PROJECT_NAME,
            )
            match_note = f" (logged to 📥 Inbox/**{proj['name']}** — tell me a project anytime to file it)"

        # ── Resolve the task (optional) ────────────────────────────────
        task_id: str | None = None
        task_note = ""
        if task_name:
            # Pull this user's tasks, filter to ones whose decrypted category
            # casefolds to the project's name_key. owner=None means "all
            # owners" — list_tasks treats a literal "all" as an owner value
            # and matches nothing, so task attachment silently never worked.
            all_tasks = await list_tasks(self._config, user_id)
            project_tasks = [
                t for t in all_tasks
                if (t.get("category") or "").strip().lower() == proj["name_key"]
            ]
            tres = resolver.resolve_task(task_name, project_tasks)
            if tres.resolved is not None:
                task_id = tres.resolved.id
                task_note = f" → task **{tres.resolved.title}**"
            elif tres.reason == "multi":
                names = ", ".join(f"**{c.title}**" for c in tres.candidates[:6])
                budget_pending.set_pending(budget_pending.PendingExpenseChoice(
                    user_id=user_id, amount=amount, currency=currency,
                    description=description, vendor=vendor, spent_at=spent_at,
                    task_name=task_name, kind="task",
                    candidates=tuple((c.id, c.title) for c in tres.candidates[:6]),
                    project_id=proj["id"], project_name=proj["name"],
                    token=budget_pending.new_token(),
                ))
                return (
                    f"⚠️ Multiple tasks on **{proj['name']}** match "
                    f"`{task_name}`: {names}. Tap one below or reply with "
                    "the exact title (or omit task_name to log on the project)."
                )
            elif any(t.get("id") and t.get("title") for t in project_tasks):
                # Task name given but nothing matched, yet the project HAS
                # tasks — ASK which one (don't silently drop the attachment or
                # guess). Offer the project's tasks as candidates; the Telegram
                # layer renders them as tap-buttons + an "on the project (no
                # task)" escape. No expense is logged until the user picks.
                cands = tuple(
                    (t["id"], t["title"])
                    for t in project_tasks
                    if t.get("id") and t.get("title")
                )[:6]
                names = ", ".join(f"**{title}**" for _id, title in cands)
                budget_pending.set_pending(budget_pending.PendingExpenseChoice(
                    user_id=user_id, amount=amount, currency=currency,
                    description=description, vendor=vendor, spent_at=spent_at,
                    task_name=task_name, kind="task",
                    candidates=cands,
                    project_id=proj["id"], project_name=proj["name"],
                    token=budget_pending.new_token(),
                ))
                more = "" if len(project_tasks) <= 6 else f" (+{len(project_tasks) - 6} more)"
                return (
                    f"⚠️ No task on **{proj['name']}** matches `{task_name}`. "
                    f"Which one? {names}{more}. Tap a button below, reply with "
                    f"the exact title, or say 'on the project' to log "
                    f"{_fmt_money(amount, currency or 'EUR')} on **{proj['name']}** "
                    "itself. (No expense logged yet.)"
                )
            else:
                # Project has no tasks at all — nothing to attach to.
                task_note = (
                    f" (no tasks on **{proj['name']}** yet — "
                    "logged on the project itself)"
                )

        # ── Create the expense ─────────────────────────────────────────
        expense = await store.create_expense(
            self._config, user_id, proj["id"],
            amount=amount, currency=currency,
            description=description, vendor=vendor,
            task_id=task_id, spent_at=spent_at,
        )
        # Resolved successfully — invalidate any older pending choice so a
        # stale button tap can't fire a duplicate expense.
        budget_pending.clear_pending(user_id)
        projects_after = await store.list_projects(self._config, user_id)
        current = next((p for p in projects_after if p["id"] == proj["id"]), None)
        cur = expense.get("currency")
        msg = f"Logged {_fmt_money(amount, cur)} on **{proj['name']}**{task_note}"
        if description:
            msg += f" ({description})"
        if current:
            msg += (
                f". Spent {_fmt_money(current['spent'], current.get('currency'))} "
                f"of {_fmt_money(current.get('budget'), current.get('currency'))} "
                f"— {_fmt_money(current['remaining'], current.get('currency'))} left."
            )
        return msg + match_note + "."


class SetDefaultExpenseProjectSkill(BaseSkill):
    """Set (or clear) the user's default project for unrouted expenses."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_default_expense_project"

    @property
    def description(self) -> str:
        return (
            "Set the project that unrouted expenses default to. After 'set "
            "default nima', a bare 'spent 12 on coffee' lands on **nima** "
            "without asking back. Pass an empty string to clear the default."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name to use as the default for new expenses. Empty string clears the default.",
                },
            },
            "required": ["project"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import settings as budget_settings, store

        name = (params.get("project") or "").strip()
        if not name:
            await budget_settings.set_default_expense_project(
                self._config, user_id, None,
            )
            return "Cleared the default expense project."

        # Resolve against existing projects so a typo doesn't pin a non-existent
        # default. If the user explicitly intends a new project, AddExpense will
        # create it on first use anyway.
        projects = await store.list_projects(self._config, user_id)
        from lazyclaw.budgets.resolver import resolve_project

        res = resolve_project(name, projects)
        chosen = res.resolved.name if res.resolved else name
        await budget_settings.set_default_expense_project(
            self._config, user_id, chosen,
        )
        return (
            f"Default expense project set to **{chosen}**. New 'spent X on Y' "
            "captures will route there unless you say otherwise."
        )


class ListExpensesSkill(BaseSkill):
    """List expenses for a project."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_expenses"

    @property
    def description(self) -> str:
        return (
            "List the logged expenses for a project (e.g. 'show nima "
            "expenses'), for the inbox ('show unassigned expenses'), or "
            "across ALL projects when no project is given (e.g. 'show my "
            "expenses'). Read-only."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, 'inbox' for unassigned, or omit for ALL projects",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store

        query = (params.get("project") or "").strip()
        if query.casefold() in {"inbox", "general"}:
            query = GENERAL_PROJECT_NAME
        if not query:
            expenses = await store.list_all_expenses(self._config, user_id)
            if not expenses:
                return "No expenses logged yet."
            lines = [
                f"• **{e.get('project_name') or '(unknown)'}** — "
                f"{_fmt_money(e.get('amount'), e.get('currency'))} "
                f"{e.get('description') or e.get('vendor') or 'expense'} "
                f"({(e.get('spent_at') or '')[:10]})"
                for e in expenses[:30]
            ]
            more = "" if len(expenses) <= 30 else f"\n(+{len(expenses) - 30} older)"
            return "\n".join(lines) + more

        proj = await store.get_project_by_name(self._config, user_id, query)
        if proj is None:
            return f"No project named '{query}' found."
        expenses = await store.list_expenses(self._config, user_id, project_id=proj["id"])
        if not expenses:
            return f"No expenses logged on **{proj['name']}** yet."

        lines = [f"Expenses on **{proj['name']}**:"]
        for e in expenses:
            label = e.get("description") or e.get("vendor") or "expense"
            lines.append(
                f"- {e.get('spent_at') or ''} · {_fmt_money(e['amount'], e.get('currency'))} — {label}"
            )
        return "\n".join(lines)


class ExpenseReportSkill(BaseSkill):
    """Spending report: budget vs spent per project."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "expense_report"

    @property
    def description(self) -> str:
        return (
            "Show a spending report — budget, spent, and remaining per project "
            "(e.g. 'nima expense report' or 'how much have I spent?'). "
            "Read-only. Pass a project name to scope it to one project."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Optional project name to scope the report to",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store

        project_id = None
        project = (params.get("project") or "").strip()
        if project:
            proj = await store.get_project_by_name(self._config, user_id, project)
            if proj is None:
                return f"No project named '{project}' found."
            project_id = proj["id"]

        report = await store.spending_report(self._config, user_id, project_id=project_id)
        if not report["projects"]:
            return "No projects with budgets yet."

        lines = ["**Spending report**"]
        for p in report["projects"]:
            warn = " ⚠️ mixed currency" if p.get("mixed_currency") else ""
            lines.append(
                f"- **{p['name']}**: {_fmt_money(p['spent'], p.get('currency'))} / "
                f"{_fmt_money(p.get('budget'), p.get('currency'))} "
                f"({_fmt_money(p['remaining'], p.get('currency'))} left){warn}"
            )
        if not project_id:
            lines.append(
                f"\nTotal: {_fmt_money(report['total_spent'], 'EUR')} spent / "
                f"{_fmt_money(report['total_budget'], 'EUR')} budgeted."
            )
            gen = await store.get_project_by_name(self._config, user_id, GENERAL_PROJECT_NAME)
            if gen:
                inbox = await store.list_expenses(self._config, user_id, project_id=gen["id"])
                if inbox:
                    inbox_total = sum(float(e.get("amount") or 0) for e in inbox)
                    lines.append(
                        f"\n📥 Inbox: {len(inbox)} unassigned "
                        f"({_fmt_money(inbox_total, gen.get('currency'))}) — "
                        "say 'assign …' or use auto_assign_inbox to file them."
                    )
        return "\n".join(lines)


class AddRecurringExpenseSkill(BaseSkill):
    """Schedule a recurring (auto-charged) expense."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "add_recurring_expense"

    @property
    def description(self) -> str:
        return (
            "Schedule a recurring expense that auto-charges on a cron schedule "
            "(e.g. 'add 12 EUR monthly hosting to nima' → cron '0 0 1 * *'). "
            "Each due tick inserts a new expense + sends a quiet notification. "
            "Use standard cron syntax: '0 0 1 * *' monthly, '0 0 * * 1' weekly."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name the recurring expense belongs to",
                },
                "amount": {
                    "type": "number",
                    "description": "Amount charged each period",
                },
                "cron_expression": {
                    "type": "string",
                    "description": (
                        "Cron schedule. '0 0 1 * *' = 1st of each month, "
                        "'0 0 * * 1' = every Monday, '0 0 1 1 *' = yearly."
                    ),
                },
                "currency": {
                    "type": "string",
                    "description": "ISO currency code (defaults to project currency)",
                },
                "description": {
                    "type": "string",
                    "description": "What the recurring charge is for, e.g. 'hosting'",
                },
            },
            "required": ["project", "amount", "cron_expression"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store

        project = (params.get("project") or "").strip()
        if not project:
            return "Project name is required."
        try:
            amount = float(params.get("amount"))
        except (TypeError, ValueError):
            return "Amount must be a number."
        cron = (params.get("cron_expression") or "").strip()

        proj = await store.create_project(self._config, user_id, project)
        try:
            rule = await store.create_recurring_expense(
                self._config, user_id, proj["id"],
                amount=amount, cron_expression=cron,
                currency=params.get("currency"),
                description=params.get("description"),
            )
        except ValueError as exc:
            return f"Couldn't schedule recurring expense: {exc}"

        return (
            f"Scheduled {_fmt_money(amount, rule.get('currency'))} recurring on "
            f"**{proj['name']}** (`{cron}`). Next charge: {rule.get('next_run')}."
        )


class MoveExpenseSkill(BaseSkill):
    """Assign/move logged expenses to a project (optionally a task).

    Precision-first like add_expense: never guesses. Ambiguity returns a
    clarification and moves NOTHING. Default source is the 📥 Inbox
    (General); pass from_project to move between named projects, or
    all_inbox=true to file every unassigned expense at once.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "move_expense"

    @property
    def description(self) -> str:
        return (
            "Assign or move a logged expense to a project — 'put the coffee "
            "expense on ClubBay', 'assign the 12 EUR one to Nima', 'file all "
            "inbox expenses to ClubBay' (all_inbox=true). Matches by "
            "description/vendor/amount; ambiguous matches return candidates "
            "and move nothing. Optionally attach to a task with task_name. "
            "Source defaults to the 📥 Inbox (General)."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Target project name (fuzzy-matched)"},
                "query": {"type": "string", "description": "Which expense: words from its description/vendor, or its amount"},
                "task_name": {"type": "string", "description": "Optional task in the target project to attach to"},
                "from_project": {"type": "string", "description": "Source project (default: the Inbox/General)"},
                "all_inbox": {"type": "boolean", "description": "Move EVERY inbox expense to the target project"},
            },
            "required": ["project"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import resolver, store
        from lazyclaw.tasks.store import list_tasks

        projects = await store.list_projects(self._config, user_id)
        tres = resolver.resolve_project((params.get("project") or "").strip(), projects)
        if tres.resolved is None:
            if getattr(tres, "reason", None) == "multi":
                names = ", ".join(f"**{c.name}**" for c in tres.candidates[:6])
                return f"⚠️ Multiple projects match: {names}. Repeat with the exact name. (Nothing moved.)"
            names = ", ".join(p["name"] for p in projects[:10])
            return f"No project matches `{params.get('project')}`. Projects: {names or '(none)'}. (Nothing moved.)"
        target = await store.get_project(self._config, user_id, tres.resolved.id)

        # Source: from_project > Inbox/General.
        src_query = (params.get("from_project") or "").strip()
        if src_query:
            sres = resolver.resolve_project(src_query, projects)
            if sres.resolved is None:
                return f"No source project matches `{src_query}`. (Nothing moved.)"
            source = await store.get_project(self._config, user_id, sres.resolved.id)
        else:
            source = await store.get_project_by_name(
                self._config, user_id, GENERAL_PROJECT_NAME,
            )
            if source is None:
                return "📥 Inbox is empty — no unassigned expenses."
        if source["id"] == target["id"]:
            return f"Source and target are both **{target['name']}** — nothing to move."

        candidates = await store.list_expenses(self._config, user_id, project_id=source["id"])
        if not candidates:
            return f"No expenses in **{source['name']}** to move."

        # Optional task resolution (same category==name_key join as add_expense).
        task_id, task_note = None, ""
        task_name = (params.get("task_name") or "").strip()
        if task_name:
            all_tasks = await list_tasks(self._config, user_id)
            project_tasks = [
                t for t in all_tasks
                if (t.get("category") or "").strip().lower() == target["name_key"]
            ]
            tr = resolver.resolve_task(task_name, project_tasks)
            if tr.resolved is None:
                titles = ", ".join(f"**{t['title']}**" for t in project_tasks[:6])
                return (
                    f"⚠️ No task on **{target['name']}** matches `{task_name}`. "
                    f"Tasks: {titles or '(none)'}. (Nothing moved.)"
                )
            task_id, task_note = tr.resolved.id, f" → task **{tr.resolved.title}**"

        # Select which expenses move.
        if params.get("all_inbox"):
            moving = candidates
        elif not (params.get("query") or "").strip():
            return (
                "Say which expense (words from its description, or the amount) — "
                "or pass all_inbox=true to move ALL "
                f"{len(candidates)} from **{source['name']}**. (Nothing moved.)"
            )
        else:
            q = str(params.get("query") or "").strip().casefold()

            def hit(e: dict) -> bool:
                hay = f"{e.get('description') or ''} {e.get('vendor') or ''}".casefold()
                if q in hay:
                    return True
                try:
                    return abs(float(e.get("amount") or 0) - float(q)) < 0.005
                except ValueError:
                    return False

            matches = [e for e in candidates if hit(e)]
            if not matches:
                return f"No expense in **{source['name']}** matches `{q}`. (Nothing moved.)"
            if len(matches) > 1:
                lines = [
                    f"  {i + 1}. {_fmt_money(e.get('amount'), e.get('currency'))} "
                    f"{e.get('description') or e.get('vendor') or 'expense'} "
                    f"({(e.get('spent_at') or '')[:10]})"
                    for i, e in enumerate(matches[:6])
                ]
                return (
                    f"⚠️ {len(matches)} expenses match `{q}`:\n" + "\n".join(lines)
                    + "\nBe more specific (exact description or amount). No expense was moved."
                )
            moving = matches

        # update_expense returns False (no exception) when the row no longer
        # matches — e.g. it was deleted/changed elsewhere between our
        # list_expenses read and this write. Track which moves actually
        # landed so we never report "Moved" for a silent no-op.
        moved_ok: list[dict] = []
        for e in moving:
            ok = await store.update_expense(
                self._config, user_id, e["id"], project_id=target["id"], task_id=task_id,
            )
            if ok:
                moved_ok.append(e)
        failed_count = len(moving) - len(moved_ok)
        left = await store.list_expenses(self._config, user_id, project_id=source["id"])

        if not moved_ok:
            if len(moving) == 1:
                e = moving[0]
                return (
                    f"Couldn't move {_fmt_money(e.get('amount'), e.get('currency'))} "
                    f"({e.get('description') or e.get('vendor') or 'expense'}) — it "
                    "could no longer be found (it may have just been changed or "
                    "deleted elsewhere). Nothing moved."
                )
            return (
                f"Couldn't move any of the {len(moving)} matched expenses — they "
                "could no longer be found (changed or deleted meanwhile). Nothing moved."
            )

        if len(moving) == 1:
            e = moved_ok[0]
            head = (
                f"Moved {_fmt_money(e.get('amount'), e.get('currency'))} "
                f"({e.get('description') or e.get('vendor') or 'expense'}) → "
                f"**{target['name']}**{task_note}."
            )
        elif failed_count:
            head = (
                f"Moved {len(moved_ok)} of {len(moving)} expenses → "
                f"**{target['name']}**{task_note} — {failed_count} could not be "
                "moved (changed or deleted meanwhile)."
            )
        else:
            head = f"Moved {len(moved_ok)} expenses → **{target['name']}**{task_note}."
        inbox_note = (
            f" 📥 Inbox now has {len(left)} unassigned."
            if source.get("name_key") == "general" else ""
        )
        return head + inbox_note


class ListProjectsSkill(BaseSkill):
    """Enumerate the user's projects with budget/spent/remaining."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_projects"

    @property
    def description(self) -> str:
        return (
            "List all the user's projects with budget, spent and remaining. "
            "Use to enumerate projects ('what projects do I have', 'show my "
            "project budgets') or before filing expenses. Read-only."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter: 'active' (default), 'archived' or 'all'",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store

        status = (params.get("status") or "active").strip().lower()
        projects = await store.list_projects(
            self._config, user_id, status=None if status == "all" else status,
        )
        if not projects:
            return "No projects yet. Create one with create_project or just log an expense."
        lines = [
            f"• **{p['name']}** — budget {_fmt_money(p.get('budget'), p.get('currency'))}, "
            f"spent {_fmt_money(p.get('spent', 0), p.get('currency'))}, "
            f"{_fmt_money(p.get('remaining', 0), p.get('currency'))} left"
            + (" _(archived)_" if p.get("status") == "archived" else "")
            for p in projects
        ]
        return "\n".join(lines)


class AutoAssignInboxSkill(BaseSkill):
    """AI-file inbox expenses into projects (worker LLM, confidence-gated)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "auto_assign_inbox"

    @property
    def description(self) -> str:
        return (
            "Automatically assign unassigned 📥 Inbox expenses to matching "
            "projects using AI suggestions — 'sort my inbox', 'auto assign my "
            "expenses'. Applies only confident matches; uncertain ones are "
            "listed for the user to decide (use move_expense for those)."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import inbox_suggest, store

        gen = await store.get_project_by_name(self._config, user_id, GENERAL_PROJECT_NAME)
        inbox = (
            await store.list_expenses(self._config, user_id, project_id=gen["id"])
            if gen else []
        )
        if not inbox:
            return "📥 Inbox is empty — no unassigned expenses."
        batch, overflow = inbox[:10], max(0, len(inbox) - 10)

        projects = await store.list_projects(self._config, user_id)
        # suggest_for_expenses bounds concurrency (semaphore(2)) — the
        # default HYBRID worker is local Ollama and serializes chat calls,
        # so an unbounded fan-out over up to 10 items would queue calls
        # 2..10 behind independent 3s timeouts and they'd all come back
        # empty. Shared with the /api/budgets/inbox/suggestions route.
        suggestions = await inbox_suggest.suggest_for_expenses(
            self._config, user_id, batch, projects,
        )

        applied, unsure = [], []
        for e, s in zip(batch, suggestions):
            label = e.get("description") or e.get("vendor") or "expense"
            target_id = s["project_id"]
            if target_id and s["confidence"] in {"high", "medium"}:
                ok = await store.update_expense(
                    self._config, user_id, e["id"], project_id=target_id,
                )
                if ok:
                    applied.append(f"  ✓ {label} → **{s['project_name']}**")
            else:
                unsure.append(f"  ? {label} ({_fmt_money(e.get('amount'), e.get('currency'))})")

        parts = []
        if applied:
            parts.append(f"Auto-assigned {len(applied)}:\n" + "\n".join(applied))
        if unsure:
            parts.append(
                f"Needs you ({len(unsure)}):\n" + "\n".join(unsure)
                + "\nUse move_expense to file these."
            )
        if overflow:
            parts.append(f"({overflow} more in the inbox — run auto_assign_inbox again.)")
        return "\n\n".join(parts) or "Nothing to do."


class ListBudgetTopupsSkill(BaseSkill):
    """Read the budget top-up ledger (budget_entries) — the money-IN side."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_budget_topups"

    @property
    def description(self) -> str:
        return (
            "Show budget top-ups (top up / topup history): the ledger of money "
            "ADDED to project budgets (funding history) — when, how much, and "
            "the source (who/why). Answers 'show my top-ups', 'budget history', "
            "'where did the budget come from', 'money added to X'. Omit project "
            "for all projects. Read-only."
        )

    @property
    def category(self) -> str:
        return "budgets"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (fuzzy-matched). Omit for ALL projects.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import resolver, store

        projects = await store.list_projects(self._config, user_id)
        query = (params.get("project") or "").strip()
        if query:
            res = resolver.resolve_project(query, projects)
            if res.resolved is None:
                names = ", ".join(p["name"] for p in projects[:10])
                return f"No project matches `{query}`. Projects: {names or '(none)'}."
            projects = [p for p in projects if p["id"] == res.resolved.id]

        sections: list[str] = []
        for p in projects:
            entries = await store.list_budget_entries(self._config, user_id, p["id"])
            if not entries:
                continue
            lines = [
                f"  {'＋' if float(e.get('amount') or 0) >= 0 else '−'}"
                f"{_fmt_money(abs(float(e.get('amount') or 0)), e.get('currency'))} "
                f"— {e.get('source') or ('budget edit' if e.get('kind') == 'edit' else 'top-up')} "
                f"({(e.get('created_at') or '')[:10]})"
                for e in entries
            ]
            total = sum(float(e.get("amount") or 0) for e in entries)
            sections.append(
                f"**{p['name']}** — {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}, "
                f"net {_fmt_money(total, p.get('currency'))}:\n" + "\n".join(lines)
            )
        if not sections:
            scope = f" on **{projects[0]['name']}**" if query and projects else ""
            return f"No top-ups recorded{scope} yet. Add one with add_project_budget."
        return "\n\n".join(sections)
