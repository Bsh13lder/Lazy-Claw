"""Budget manager skills — set project budgets, log expenses, recurring spend.

NL control over ``lazyclaw.budgets.store``. A "project" is the task category;
these skills upsert the project by name so "set nima budget to 500" works
before any task exists. Every expense mirrors to a LazyBrain note wikilinking
the project page. Channel-agnostic — identical behaviour in Telegram, Web UI
chat, and CLI.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


def _fmt_money(amount, currency: str | None) -> str:
    try:
        return f"{float(amount):,.2f} {currency or 'EUR'}"
    except (TypeError, ValueError):
        return f"{amount} {currency or 'EUR'}"


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
      • multiple matches OR no match (with no default project set) → REFUSES to
        guess and returns a clarification listing candidates the brain relays
        to the user. Same rules apply to ``task_name`` within the resolved
        project.

    Set ``set_default_expense_project`` once and lone-amount captures like
    'spent 12 on coffee' route there without a second turn.
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
            "nima'). NEVER guess the project silently — if the user didn't "
            "name one and no default is set, the skill returns a clarification "
            "listing candidates; relay that ask to the user verbatim, then "
            "call add_expense again with their pick. Optionally attach to a "
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
            recent = projects[:5]
            recent_list = (
                "\n".join(
                    f"- **{p['name']}** ({_fmt_money(p.get('spent', 0), p.get('currency'))}/"
                    f"{_fmt_money(p.get('budget'), p.get('currency'))})"
                    for p in recent
                )
                if recent else "(no projects yet — name one to create it)"
            )
            if recent:
                budget_pending.set_pending(budget_pending.PendingExpenseChoice(
                    user_id=user_id, amount=amount, currency=currency,
                    description=description, vendor=vendor, spent_at=spent_at,
                    task_name=task_name, kind="project",
                    candidates=tuple((p["id"], p["name"]) for p in recent),
                    project_id=None, project_name=None,
                    token=budget_pending.new_token(),
                ))
            return (
                f"Which project for {_fmt_money(amount, currency or 'EUR')}"
                + (f" ({description})" if description else "")
                + "?\n" + recent_list
                + "\n\nTap one below or reply with a project name. Tip: say "
                "_'set default <name>'_ once to skip this prompt next time."
            )

        # ── Resolve the task (optional) ────────────────────────────────
        task_id: str | None = None
        task_note = ""
        if task_name:
            # Pull this user's open tasks, filter to ones whose decrypted
            # category casefolds to the project's name_key.
            all_tasks = await list_tasks(self._config, user_id, owner="all")
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
            else:
                # No task match — log on project, mention it.
                task_note = (
                    f" (no task matched `{task_name}` on **{proj['name']}** — "
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
                    "description": "Project name to list expenses for, e.g. 'nima'",
                },
            },
            "required": ["project"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store

        project = (params.get("project") or "").strip()
        proj = await store.get_project_by_name(self._config, user_id, project)
        if proj is None:
            return f"No project named '{project}' found."
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
