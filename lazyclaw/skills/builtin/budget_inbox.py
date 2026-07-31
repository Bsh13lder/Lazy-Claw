"""Budget inbox skills — move/assign expenses, list projects and top-ups.

Extracted from ``budget_manager.py`` (pure move, zero behaviour change).
NL control over ``lazyclaw.budgets.store`` for the 📥 Inbox (General project)
workflow: filing unassigned expenses, listing projects with budget/spent, AI-
assisted inbox sorting, and reading the budget top-up ledger. Channel-agnostic
— identical behaviour in Telegram, Web UI chat, and CLI.
"""

from __future__ import annotations

from lazyclaw.budgets.store import GENERAL_PROJECT_NAME
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.budget_manager import _fmt_money


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
