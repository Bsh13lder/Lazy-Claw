# Expense Inbox + Top-up Reads — Phase 1 (Server + Agent) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent fully budget-aware: fix the expense-PATCH `project_id` silent drop, add inbox (General) triage — move/bulk/auto-assign — and add top-up + project read skills.

**Architecture:** The `General` project IS the Inbox (detect by `name_key == "general"`). All assigns ride the single existing write path `store.update_expense` (generic `**fields`). Auto-assign mirrors `tasks/smart_intake.py` (worker LLM, 3s, never raises). New skills live in `skills/builtin/budget_manager.py`, registered in `skills/registry.py`, wired into `runtime/agent.py` frozensets + `teams/specialists/tasks_specialist.md` + `personality/SOUL.md`.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest(-asyncio), Pydantic v2.

## Global Constraints

- Repo root: `/Users/blckit/Desktop/Code_Projects/lazyclaw` (git root). Python package: `lazyclaw/`. Tests: `tests/`.
- **NEVER run the full pytest suite while the Docker container is up** (`./data` = live DB). Run targeted files only: `python -m pytest tests/<file>.py -v`.
- Deployed code runs from a BAKED image — after Phase 1 merges, `make rebuild` is required to see changes live (do NOT do this mid-plan; note it at the end).
- Commit style: `<type>: <description>`, NO AI attribution, NO `-a`/`-A`/`.` staging — `git add` explicit paths only.
- Immutability: frozen dataclasses for new value types. Type annotations on all new signatures.
- New skill descriptions are the discovery surface: `search_tools` is plain substring matching (`skills/builtin/tool_discovery.py:92-93`) — keep the mandated keyword strings verbatim.
- Skills return plain strings for the brain to relay. Read-only skills expose `read_only=True`.
- Existing fixtures to mirror: `tests/test_budget_expense_skill.py:26-42` (cfg) and `tests/test_budgets_routes.py:22-45` (client).

---

### Task 1: `PATCH /expenses/{id}` accepts + validates `project_id`

**Files:**
- Modify: `lazyclaw/gateway/routes/budgets.py:90-101` (`UpdateExpenseBody`), `:347-379` (`update_expense_route`)
- Test: `tests/test_budgets_routes.py` (append)

**Interfaces:**
- Produces: `PATCH /api/budgets/expenses/{id}` body may carry `project_id: str` → expense moves project; unknown/foreign/null project → 404/400. Later tasks (move_expense, web, mobile) rely on this persisting.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_budgets_routes.py`)

```python
@pytest.mark.asyncio
async def test_patch_expense_moves_project(client) -> None:
    tc = client
    a = tc.post("/api/budgets/projects", json={"name": "General"}).json()["project"]
    b = tc.post("/api/budgets/projects", json={"name": "ClubBay"}).json()["project"]
    e = tc.post(
        f"/api/budgets/projects/{a['id']}/expenses",
        json={"amount": 12, "description": "coffee"},
    ).json()["expense"]

    r = tc.patch(f"/api/budgets/expenses/{e['id']}", json={"project_id": b["id"]})
    assert r.status_code == 200, r.text

    # The move persisted server-side (this was silently dropped before).
    moved = tc.get(f"/api/budgets/projects/{b['id']}/expenses").json()["expenses"]
    assert [x["id"] for x in moved] == [e["id"]]
    assert tc.get(f"/api/budgets/projects/{a['id']}/expenses").json()["expenses"] == []


@pytest.mark.asyncio
async def test_patch_expense_rejects_bad_project(client) -> None:
    tc = client
    a = tc.post("/api/budgets/projects", json={"name": "General"}).json()["project"]
    e = tc.post(
        f"/api/budgets/projects/{a['id']}/expenses",
        json={"amount": 5, "description": "x"},
    ).json()["expense"]

    assert tc.patch(
        f"/api/budgets/expenses/{e['id']}", json={"project_id": "nope"}
    ).status_code == 404
    assert tc.patch(
        f"/api/budgets/expenses/{e['id']}", json={"project_id": None}
    ).status_code == 400
```

- [ ] **Step 2: Run to verify both fail**

Run: `cd /Users/blckit/Desktop/Code_Projects/lazyclaw && python -m pytest tests/test_budgets_routes.py -v -k "patch_expense"`
Expected: FAIL — first test's move assertion fails (project unchanged: Pydantic drops the unknown field); second returns 200 not 404.

- [ ] **Step 3: Implement**

In `UpdateExpenseBody` (after `notes`, before `task_id`):

```python
    project_id: str | None = None
```

In `update_expense_route`, after `fields = body.model_dump(exclude_unset=True)` and the existing NOT-NULL guard, add:

```python
    if "project_id" in fields:
        if not fields["project_id"]:
            raise HTTPException(400, "project_id cannot be null — every expense belongs to a project")
        target = await store.get_project(_config, user.id, fields["project_id"])
        if target is None:
            raise HTTPException(404, "project not found")
```

(Keep the guard ABOVE the empty-patch 400 check ordering as-is; `project_id` alone is a valid patch.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_budgets_routes.py -v -k "patch_expense"`
Expected: PASS. Also run the whole file: `python -m pytest tests/test_budgets_routes.py -v` (no regressions).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/gateway/routes/budgets.py tests/test_budgets_routes.py
git commit -m "fix: expense PATCH accepts and validates project_id (mobile move-revert bug)"
```

---

### Task 2: Note re-point on project move (store choke point)

**Files:**
- Modify: `lazyclaw/budgets/store.py:887-914` (`update_expense`)
- Test: `tests/test_budget_store.py` (append)

**Interfaces:**
- Consumes: `_write_expense_note` (`store.py:237`), `_delete_note` (`store.py:289`), expense rows incl. `lazybrain_note_id`.
- Produces: any `update_expense(..., project_id=...)` call (route, skills) re-mirrors the LazyBrain note under the new project — best-effort, never raises.

- [ ] **Step 1: Write the failing test** (append to `tests/test_budget_store.py`, reuse its `cfg` fixture)

```python
async def test_move_expense_repoints_note(cfg, monkeypatch):
    a = await store.create_project(cfg, "u1", "General")
    b = await store.create_project(cfg, "u1", "ClubBay")
    e = await store.create_expense(cfg, "u1", a["id"], amount=9, description="domain")

    calls = {}
    async def fake_write(config, user_id, **kw):
        calls["project_name"] = kw["project_name"]
        return "note-new"
    async def fake_delete(config, user_id, note_id):
        calls["deleted"] = note_id
    monkeypatch.setattr(store, "_write_expense_note", fake_write)
    monkeypatch.setattr(store, "_delete_note", fake_delete)

    ok = await store.update_expense(cfg, "u1", e["id"], project_id=b["id"])
    assert ok
    assert calls["project_name"] == "ClubBay"
    rows = await store.list_expenses(cfg, "u1", project_id=b["id"])
    assert rows[0]["lazybrain_note_id"] == "note-new"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_budget_store.py -v -k repoints_note`
Expected: FAIL — `calls` empty (no re-point logic exists).

- [ ] **Step 3: Implement** — at the END of `update_expense`, after the UPDATE succeeds (`rowcount > 0`), add (wrapped, never fails the move):

```python
    if moved and "project_id" in fields:
        try:
            rows = await list_expenses(config, user_id, project_id=fields["project_id"])
            exp = next((r for r in rows if r["id"] == expense_id), None)
            proj = await get_project(config, user_id, fields["project_id"])
            if exp and proj:
                await _delete_note(config, user_id, exp.get("lazybrain_note_id"))
                note_id = await _write_expense_note(
                    config, user_id,
                    project_name=proj["name"], amount=float(exp["amount"] or 0),
                    currency=exp.get("currency") or proj.get("currency") or "EUR",
                    description=exp.get("description"), vendor=exp.get("vendor"),
                    spent_at=exp.get("spent_at"), task_title=None,
                )
                async with db_session(config) as db:
                    await db.execute(
                        "UPDATE project_expenses SET lazybrain_note_id = ? WHERE id = ? AND user_id = ?",
                        (note_id, expense_id, user_id),
                    )
                    await db.commit()
        except Exception:
            logger.warning("expense note re-point failed (%s)", expense_id, exc_info=True)
```

(`moved` = the existing `rowcount > 0` result; rename/keep the local var accordingly and still return it. `list_expenses` must include `lazybrain_note_id` in its SELECT — verify `EXPENSE_COLUMNS` at `store.py:50-59` carries it; it does.)

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_budget_store.py -v` (whole file, no regressions).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/budgets/store.py tests/test_budget_store.py
git commit -m "feat: re-point expense LazyBrain note when the expense moves project"
```

---

### Task 3: `budgets/inbox_suggest.py` — worker-LLM project suggester

**Files:**
- Create: `lazyclaw/budgets/inbox_suggest.py`
- Test: `tests/budgets/test_inbox_suggest.py`

**Interfaces:**
- Consumes: `EcoRouter`/`ROLE_WORKER`/`LLMRouter` (same lazy import as `tasks/smart_intake.py:155-157`), `store.list_projects`, `store.list_all_expenses`.
- Produces: `suggest_expense_project(config, user_id, *, description, vendor, amount, currency, timeout_s=3.0) -> ExpenseSuggestion` where `ExpenseSuggestion` is `@dataclass(frozen=True)` with `project_name: str | None`, `confidence: str` (`high|medium|low|none`), `reason: str | None`, `source: str` (`llm|none`). **Never raises.** Suggests ONLY existing project names (never invents; never suggests General). Internal `_worker_chat(config, user_id, prompt) -> dict` isolated for monkeypatching.

- [ ] **Step 1: Write the failing tests** (`tests/budgets/test_inbox_suggest.py`)

```python
"""Worker-LLM inbox-expense project suggester tests (LLM always mocked)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lazyclaw.budgets import inbox_suggest, store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)", ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def test_valid_llm_json_maps_to_suggestion(cfg, monkeypatch):
    await store.create_project(cfg, "u1", "ClubBay")

    async def fake_chat(*a, **k):
        return {"content": '{"project_name": "ClubBay", "confidence": "high", "reason": "matches club spend"}'}
    monkeypatch.setattr(inbox_suggest, "_worker_chat", fake_chat)

    s = await inbox_suggest.suggest_expense_project(
        cfg, "u1", description="venue deposit", vendor=None, amount=50, currency="EUR",
    )
    assert s.project_name == "ClubBay"
    assert s.confidence == "high"
    assert s.source == "llm"


async def test_unknown_project_name_is_discarded(cfg, monkeypatch):
    await store.create_project(cfg, "u1", "ClubBay")

    async def fake_chat(*a, **k):
        return {"content": '{"project_name": "Invented", "confidence": "high", "reason": "?"}'}
    monkeypatch.setattr(inbox_suggest, "_worker_chat", fake_chat)

    s = await inbox_suggest.suggest_expense_project(
        cfg, "u1", description="x", vendor=None, amount=1, currency="EUR",
    )
    assert s.project_name is None
    assert s.confidence == "none"


async def test_timeout_and_garbage_never_raise(cfg, monkeypatch):
    await store.create_project(cfg, "u1", "ClubBay")

    async def slow_chat(*a, **k):
        raise asyncio.TimeoutError
    monkeypatch.setattr(inbox_suggest, "_worker_chat", slow_chat)
    s = await inbox_suggest.suggest_expense_project(
        cfg, "u1", description="x", vendor=None, amount=1, currency="EUR",
    )
    assert s.source == "none" and s.project_name is None

    async def garbage_chat(*a, **k):
        return {"content": "not json at all"}
    monkeypatch.setattr(inbox_suggest, "_worker_chat", garbage_chat)
    s = await inbox_suggest.suggest_expense_project(
        cfg, "u1", description="x", vendor=None, amount=1, currency="EUR",
    )
    assert s.source == "none"
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/budgets/test_inbox_suggest.py -v` → ImportError (module missing).

- [ ] **Step 3: Implement `lazyclaw/budgets/inbox_suggest.py`** (~140 lines):

```python
"""Suggest a project for an unassigned (Inbox/General) expense.

Mirrors lazyclaw/tasks/smart_intake.py: ROLE_WORKER model, hard timeout,
strict-JSON prompt, never raises — every failure returns an empty suggestion.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

_ALLOWED_CONFIDENCE = {"high", "medium", "low", "none"}


@dataclass(frozen=True)
class ExpenseSuggestion:
    project_name: str | None
    confidence: str  # high | medium | low | none
    reason: str | None
    source: str  # llm | none


def _empty() -> ExpenseSuggestion:
    return ExpenseSuggestion(None, "none", None, "none")


async def _worker_chat(config: Config, user_id: str, prompt: str) -> dict:
    # Isolated for test monkeypatching — lazy imports like smart_intake.py:155.
    from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
    from lazyclaw.llm.router import LLMRouter

    eco = EcoRouter(config, LLMRouter(config))
    return await eco.chat(
        messages=[
            {"role": "system", "content": "You output JSON only."},
            {"role": "user", "content": prompt},
        ],
        user_id=user_id,
        role=ROLE_WORKER,
    )


async def suggest_expense_project(
    config: Config,
    user_id: str,
    *,
    description: str | None,
    vendor: str | None,
    amount: float,
    currency: str,
    timeout_s: float = 3.0,
) -> ExpenseSuggestion:
    """Never raises. Suggests only EXISTING project names (never General)."""
    from lazyclaw.budgets import store

    try:
        projects = await store.list_projects(config, user_id, status="active")
        names = [p["name"] for p in projects if p.get("name_key") != "general"]
        if not names or not (description or vendor):
            return _empty()

        recents = await store.list_all_expenses(config, user_id)
        by_project: dict[str, list[str]] = {}
        for e in recents[:60]:
            pn = e.get("project_name")
            d = (e.get("description") or e.get("vendor") or "").strip()
            if pn and d and pn.casefold() != "general":
                by_project.setdefault(pn, [])
                if len(by_project[pn]) < 3:
                    by_project[pn].append(d[:60])

        context = "\n".join(
            f"- {n}: {', '.join(by_project.get(n, [])) or '(no expenses yet)'}"
            for n in names[:20]
        )
        prompt = (
            "An expense needs to be filed into one of the user's existing projects.\n"
            f"Expense: {amount} {currency} — {(description or '')[:200]}"
            + (f" (vendor: {vendor[:100]})" if vendor else "") + "\n"
            f"Projects (with recent expense examples):\n{context}\n\n"
            "Pick the best-matching project NAME from the list above, or null if none fits.\n"
            'Reply with STRICT JSON only, no prose, no fence:\n'
            '{"project_name": "<exact name from list or null>", '
            '"confidence": "high|medium|low", "reason": "<max 20 words>"}'
        )

        raw = await asyncio.wait_for(
            _worker_chat(config, user_id, prompt), timeout=timeout_s,
        )
        content = (raw.get("content") or "").strip().strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
        data = json.loads(content)
        if not isinstance(data, dict):
            return _empty()

        name = data.get("project_name")
        by_fold = {n.casefold(): n for n in names}
        resolved = by_fold.get(str(name).casefold()) if name else None
        if resolved is None:
            return _empty()

        conf = data.get("confidence")
        if conf not in _ALLOWED_CONFIDENCE:
            conf = "low"
        reason = (str(data.get("reason") or "")[:200]) or None
        # PII-free trace (booleans/enums only, like smart_intake.py:207).
        logger.debug("inbox_suggest: matched=%s confidence=%s", bool(resolved), conf)
        return ExpenseSuggestion(resolved, conf, reason, "llm")
    except (asyncio.TimeoutError, json.JSONDecodeError):
        return _empty()
    except Exception:
        logger.debug("inbox_suggest failed", exc_info=True)
        return _empty()
```

(If `eco.chat` returns a different shape than `{"content": ...}` — check `tasks/smart_intake.py:166-185` for how it extracts text — mirror THAT extraction exactly and adjust `_worker_chat`'s return + the tests' fakes to the same shape.)

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/budgets/test_inbox_suggest.py -v`

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/budgets/inbox_suggest.py tests/budgets/test_inbox_suggest.py
git commit -m "feat: worker-LLM project suggester for inbox expenses"
```

---

### Task 4: `POST /api/budgets/inbox/suggestions` + shared `GENERAL_PROJECT_NAME`

**Files:**
- Modify: `lazyclaw/budgets/store.py` (add constant near `_name_key`, `store.py:90`), `lazyclaw/skills/builtin/budget_manager.py:18-21` (re-import), `lazyclaw/gateway/routes/budgets.py` (new route)
- Test: `tests/test_budgets_routes.py` (append)

**Interfaces:**
- Consumes: `inbox_suggest.suggest_expense_project` (Task 3).
- Produces: `GENERAL_PROJECT_NAME = "General"` importable from `lazyclaw.budgets.store`; endpoint returns `{"suggestions": [{"expense_id", "project_id"|None, "project_name"|None, "confidence", "reason"}], "skipped": N}`. Cap 10 expenses/call.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_inbox_suggestions_endpoint(client, monkeypatch) -> None:
    tc = client
    tc.post("/api/budgets/projects", json={"name": "General"})
    club = tc.post("/api/budgets/projects", json={"name": "ClubBay"}).json()["project"]
    gen = next(p for p in tc.get("/api/budgets/projects").json()["projects"]
               if p["name_key"] == "general")
    e = tc.post(f"/api/budgets/projects/{gen['id']}/expenses",
                json={"amount": 50, "description": "venue deposit"}).json()["expense"]

    from lazyclaw.budgets.inbox_suggest import ExpenseSuggestion
    import lazyclaw.gateway.routes.budgets as routes_mod

    async def fake_suggest(config, user_id, **kw):
        return ExpenseSuggestion("ClubBay", "high", "club spend", "llm")
    monkeypatch.setattr(routes_mod.inbox_suggest, "suggest_expense_project", fake_suggest)

    r = tc.post("/api/budgets/inbox/suggestions", json={})
    assert r.status_code == 200, r.text
    assert r.json()["suggestions"] == [{
        "expense_id": e["id"], "project_id": club["id"], "project_name": "ClubBay",
        "confidence": "high", "reason": "club spend",
    }]
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_budgets_routes.py -v -k inbox_suggestions` → 404 (no route).

- [ ] **Step 3: Implement**

In `store.py` (beside `_name_key`):

```python
# Catch-all/Inbox project for expenses captured with no project named. Shared
# by skills, routes and clients; detect via name_key == "general".
GENERAL_PROJECT_NAME = "General"
```

In `budget_manager.py` replace the local constant (`:21`) with:

```python
from lazyclaw.budgets.store import GENERAL_PROJECT_NAME
```

(keep the explanatory comment; `grep -rn "GENERAL_PROJECT_NAME" lazyclaw/ tests/` — the re-import keeps other users working.)

In `routes/budgets.py` (add `from lazyclaw.budgets import inbox_suggest` at top, module-level so the test can monkeypatch `routes_mod.inbox_suggest`; add `import asyncio` if absent):

```python
class InboxSuggestionsBody(BaseModel):
    expense_ids: list[str] | None = None


@router.post("/inbox/suggestions")
async def inbox_suggestions_route(
    body: InboxSuggestionsBody, user: User = Depends(get_current_user),
):
    gen = await store.get_project_by_name(_config, user.id, store.GENERAL_PROJECT_NAME)
    if gen is None:
        return {"suggestions": [], "skipped": 0}
    expenses = await store.list_expenses(_config, user.id, project_id=gen["id"])
    if body.expense_ids:
        wanted = set(body.expense_ids)
        expenses = [e for e in expenses if e["id"] in wanted]
    skipped = max(0, len(expenses) - 10)
    expenses = expenses[:10]

    projects = await store.list_projects(_config, user.id)
    id_by_name = {p["name"]: p["id"] for p in projects}

    async def one(e: dict) -> dict:
        s = await inbox_suggest.suggest_expense_project(
            _config, user.id,
            description=e.get("description"), vendor=e.get("vendor"),
            amount=float(e.get("amount") or 0),
            currency=e.get("currency") or "EUR",
        )
        return {
            "expense_id": e["id"],
            "project_id": id_by_name.get(s.project_name) if s.project_name else None,
            "project_name": s.project_name,
            "confidence": s.confidence,
            "reason": s.reason,
        }

    suggestions = list(await asyncio.gather(*(one(e) for e in expenses)))
    return {"suggestions": suggestions, "skipped": skipped}
```

(Confirm `store.list_expenses` ordering — if not newest-first, sort by `created_at` desc before the cap.)

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_budgets_routes.py -v` (whole file).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/budgets/store.py lazyclaw/skills/builtin/budget_manager.py lazyclaw/gateway/routes/budgets.py tests/test_budgets_routes.py
git commit -m "feat: inbox suggestions endpoint + shared GENERAL_PROJECT_NAME"
```

---

### Task 5: `list_projects` + `list_budget_topups` read skills

**Files:**
- Modify: `lazyclaw/skills/builtin/budget_manager.py` (two new classes), `lazyclaw/skills/registry.py:231-243` (import + register)
- Test: `tests/test_budget_inbox_skills.py` (create; copy the `cfg` fixture from `tests/test_budget_expense_skill.py:26-42` verbatim, plus imports of the new skill classes)

**Interfaces:**
- Consumes: `store.list_projects`, `store.list_budget_entries`, `resolver.resolve_project`, `_fmt_money`.
- Produces: skills `list_projects(status?)` and `list_budget_topups(project?)` — both `read_only=True`, category `budgets`, classes `ListProjectsSkill` / `ListBudgetTopupsSkill`.

- [ ] **Step 1: Write the failing tests** (new file; fixture copied, then:)

```python
async def test_list_projects_lists_budget_spent_remaining(cfg):
    await store.create_project(cfg, "u1", "ClubBay", budget=500)
    await store.create_project(cfg, "u1", "Nima", budget=100)

    msg = await ListProjectsSkill(cfg).execute("u1", {})
    assert "ClubBay" in msg and "Nima" in msg
    assert "500" in msg and "100" in msg


async def test_list_budget_topups_shows_ledger(cfg):
    p = await store.create_project(cfg, "u1", "ClubBay", budget=100)
    await store.add_budget_entry(cfg, "u1", p["id"], amount=400, source="client deposit")

    msg = await ListBudgetTopupsSkill(cfg).execute("u1", {"project": "ClubBay"})
    assert "400" in msg and "client deposit" in msg and "ClubBay" in msg


async def test_list_budget_topups_empty(cfg):
    await store.create_project(cfg, "u1", "Nima", budget=50)
    msg = await ListBudgetTopupsSkill(cfg).execute("u1", {})
    assert "no top-ups" in msg.lower()
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_budget_inbox_skills.py -v` → ImportError.

- [ ] **Step 3: Implement** in `budget_manager.py`:

```python
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
            "ADDED to project budgets — when, how much, and the source (who/why). "
            "Answers 'show my top-ups', 'budget history', 'where did the budget "
            "come from', 'money added to X'. Omit project for all projects. "
            "Read-only."
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
```

Register in `registry.py`: extend the import at `:231-235` with `ListBudgetTopupsSkill, ListProjectsSkill`, then after `:243`:

```python
        self.register(ListProjectsSkill(config=config))
        self.register(ListBudgetTopupsSkill(config=config))
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_budget_inbox_skills.py -v`

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/skills/builtin/budget_manager.py lazyclaw/skills/registry.py tests/test_budget_inbox_skills.py
git commit -m "feat: list_projects + list_budget_topups read skills (agent can finally see top-ups)"
```

---

### Task 6: `list_expenses` cross-project mode + `expense_report` inbox line + `add_expense` inbox copy

**Files:**
- Modify: `lazyclaw/skills/builtin/budget_manager.py` (`ListExpensesSkill:566-621`, `ExpenseReportSkill:624-691`, `AddExpenseSkill` fallback message at `:410`)
- Test: `tests/test_budget_inbox_skills.py` (append)

**Interfaces:**
- Consumes: `store.list_all_expenses` (`store.py:853`, newest-first, carries `project_name`), `store.get_project_by_name`, `GENERAL_PROJECT_NAME`.
- Produces: `list_expenses` with NO `project` → cross-project list, each line `**{project_name}**`; `project="inbox"`/`"general"` → General. `expense_report` ends with `📥 Inbox: N unassigned (…)` when General has posted expenses. `add_expense` fallback reply contains `📥 Inbox`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_list_expenses_cross_project_shows_project_names(cfg):
    a = await store.create_project(cfg, "u1", "ClubBay")
    b = await store.create_project(cfg, "u1", "Nima")
    await store.create_expense(cfg, "u1", a["id"], amount=10, description="merch")
    await store.create_expense(cfg, "u1", b["id"], amount=20, description="ads")

    msg = await ListExpensesSkill(cfg).execute("u1", {})
    assert "ClubBay" in msg and "Nima" in msg
    assert "merch" in msg and "ads" in msg


async def test_list_expenses_inbox_alias(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_expense(cfg, "u1", g["id"], amount=7, description="coffee")
    msg = await ListExpensesSkill(cfg).execute("u1", {"project": "inbox"})
    assert "coffee" in msg


async def test_expense_report_shows_inbox_line(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_expense(cfg, "u1", g["id"], amount=7, description="coffee")
    msg = await ExpenseReportSkill(cfg).execute("u1", {})
    assert "📥 Inbox: 1 unassigned" in msg


async def test_add_expense_fallback_mentions_inbox(cfg):
    msg = await AddExpenseSkill(cfg).execute("u1", {"amount": 3, "description": "gum"})
    assert "📥 Inbox" in msg
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_budget_inbox_skills.py -v -k "cross_project or inbox"`

- [ ] **Step 3: Implement**

`ListExpensesSkill`: make `project` optional in `parameters_schema` (remove from `required`; description → "Project name, 'inbox' for unassigned, or omit for ALL projects"). At the top of `execute`:

```python
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
        # …existing per-project path continues below, driven by `query`…
```

`ExpenseReportSkill.execute` — after building the existing report string (find the variable holding the final text), append:

```python
        gen = await store.get_project_by_name(self._config, user_id, GENERAL_PROJECT_NAME)
        if gen:
            inbox = await store.list_expenses(self._config, user_id, project_id=gen["id"])
            if inbox:
                total = sum(float(e.get("amount") or 0) for e in inbox)
                report += (
                    f"\n📥 Inbox: {len(inbox)} unassigned "
                    f"({_fmt_money(total, gen.get('currency'))}) — "
                    "say 'assign …' or use auto_assign_inbox to file them."
                )
```

`AddExpenseSkill` — change the fallback match_note at `:410` to:

```python
            match_note = f" (logged to 📥 Inbox/**{proj['name']}** — tell me a project anytime to file it)"
```

Then `grep -rn "logged to" tests/` and update any assertion on the old copy.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_budget_inbox_skills.py tests/test_budget_expense_skill.py tests/test_budget_pending.py -v`

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/skills/builtin/budget_manager.py tests/test_budget_inbox_skills.py tests/test_budget_expense_skill.py
git commit -m "feat: cross-project list_expenses, inbox line in expense_report, inbox copy on fallback"
```

---

### Task 7: `move_expense` skill (manual single + bulk)

**Files:**
- Modify: `lazyclaw/skills/builtin/budget_manager.py` (new class `MoveExpenseSkill`), `lazyclaw/skills/registry.py` (register)
- Test: `tests/test_budget_inbox_skills.py` (append)

**Interfaces:**
- Consumes: `store.list_expenses`, `store.update_expense` (Task 2 re-points notes), `resolver.resolve_project`/`resolve_task`, `tasks.store.list_tasks`, `GENERAL_PROJECT_NAME`, `_fmt_money`.
- Produces: skill `move_expense(project, query?, task_name?, from_project?, all_inbox?)`. Precision-first: any ambiguity returns a clarification and moves NOTHING (text candidates, no pending state — user re-invokes with the exact words). `all_inbox=true` moves every General expense to `project`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_move_expense_from_inbox_to_project(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "ClubBay", budget=100)
    await store.create_expense(cfg, "u1", g["id"], amount=12, description="coffee beans")

    msg = await MoveExpenseSkill(cfg).execute("u1", {"query": "coffee", "project": "ClubBay"})
    assert "Moved" in msg and "ClubBay" in msg
    club = await store.get_project_by_name(cfg, "u1", "ClubBay")
    moved = await store.list_expenses(cfg, "u1", project_id=club["id"])
    assert len(moved) == 1 and moved[0]["description"] == "coffee beans"
    assert await store.list_expenses(cfg, "u1", project_id=g["id"]) == []


async def test_move_expense_ambiguous_query_asks(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "ClubBay")
    await store.create_expense(cfg, "u1", g["id"], amount=5, description="coffee small")
    await store.create_expense(cfg, "u1", g["id"], amount=9, description="coffee large")

    msg = await MoveExpenseSkill(cfg).execute("u1", {"query": "coffee", "project": "ClubBay"})
    assert "coffee small" in msg and "coffee large" in msg
    assert "No expense was moved" in msg
    assert len(await store.list_expenses(cfg, "u1", project_id=g["id"])) == 2


async def test_move_expense_all_inbox_bulk(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "Nima")
    for d in ("a", "b", "c"):
        await store.create_expense(cfg, "u1", g["id"], amount=1, description=d)

    msg = await MoveExpenseSkill(cfg).execute("u1", {"project": "Nima", "all_inbox": True})
    assert "3" in msg and "Nima" in msg
    assert await store.list_expenses(cfg, "u1", project_id=g["id"]) == []


async def test_move_expense_with_task_attach(cfg):
    from lazyclaw.tasks.store import create_task
    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "ClubBay")
    await create_task(cfg, "u1", "Merchandise", category="ClubBay")
    await store.create_expense(cfg, "u1", g["id"], amount=30, description="tshirts")

    msg = await MoveExpenseSkill(cfg).execute(
        "u1", {"query": "tshirts", "project": "ClubBay", "task_name": "Merch"},
    )
    assert "Merchandise" in msg
    club = await store.get_project_by_name(cfg, "u1", "ClubBay")
    moved = await store.list_expenses(cfg, "u1", project_id=club["id"])
    assert moved[0]["task_id"] is not None
```

- [ ] **Step 2: Run to verify fail** — ImportError on `MoveExpenseSkill`.

- [ ] **Step 3: Implement** (in `budget_manager.py`, after `AddRecurringExpenseSkill`):

```python
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

        for e in moving:
            await store.update_expense(
                self._config, user_id, e["id"], project_id=target["id"], task_id=task_id,
            )
        left = await store.list_expenses(self._config, user_id, project_id=source["id"])
        if len(moving) == 1:
            e = moving[0]
            head = (
                f"Moved {_fmt_money(e.get('amount'), e.get('currency'))} "
                f"({e.get('description') or e.get('vendor') or 'expense'}) → "
                f"**{target['name']}**{task_note}."
            )
        else:
            head = f"Moved {len(moving)} expenses → **{target['name']}**{task_note}."
        inbox_note = (
            f" 📥 Inbox now has {len(left)} unassigned."
            if source.get("name_key") == "general" else ""
        )
        return head + inbox_note
```

Register in `registry.py` (extend import, add `self.register(MoveExpenseSkill(config=config))`).

**Note:** passing `task_id=None` through `update_expense` for a plain move also CLEARS any previous task link — that is correct (a moved expense's old task belongs to the old project).

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_budget_inbox_skills.py -v`

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/skills/builtin/budget_manager.py lazyclaw/skills/registry.py tests/test_budget_inbox_skills.py
git commit -m "feat: move_expense skill — manual single + bulk inbox assignment"
```

---

### Task 8: `auto_assign_inbox` skill

**Files:**
- Modify: `lazyclaw/skills/builtin/budget_manager.py` (new class `AutoAssignInboxSkill`), `lazyclaw/skills/registry.py` (register)
- Test: `tests/test_budget_inbox_skills.py` (append)

**Interfaces:**
- Consumes: `inbox_suggest.suggest_expense_project` (Task 3), `store.update_expense`, `GENERAL_PROJECT_NAME`.
- Produces: skill `auto_assign_inbox()` (no params) — suggests for the 10 newest inbox expenses concurrently (`asyncio.gather`), **applies only `high`/`medium`** (same gate as `task_manager.py:396-400`), reports applied + uncertain leftovers.

- [ ] **Step 1: Write the failing tests**

```python
async def test_auto_assign_applies_confident_only(cfg, monkeypatch):
    from lazyclaw.budgets import inbox_suggest
    from lazyclaw.budgets.inbox_suggest import ExpenseSuggestion

    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "ClubBay")
    e1 = await store.create_expense(cfg, "u1", g["id"], amount=50, description="venue deposit")
    e2 = await store.create_expense(cfg, "u1", g["id"], amount=3, description="mystery")

    async def fake_suggest(config, user_id, *, description, **kw):
        if description == "venue deposit":
            return ExpenseSuggestion("ClubBay", "high", "club spend", "llm")
        return ExpenseSuggestion(None, "none", None, "none")
    monkeypatch.setattr(inbox_suggest, "suggest_expense_project", fake_suggest)

    msg = await AutoAssignInboxSkill(cfg).execute("u1", {})
    assert "venue deposit" in msg and "ClubBay" in msg
    assert "mystery" in msg  # surfaced as needs-you

    club = await store.get_project_by_name(cfg, "u1", "ClubBay")
    assert [x["id"] for x in await store.list_expenses(cfg, "u1", project_id=club["id"])] == [e1["id"]]
    assert [x["id"] for x in await store.list_expenses(cfg, "u1", project_id=g["id"])] == [e2["id"]]


async def test_auto_assign_empty_inbox(cfg):
    msg = await AutoAssignInboxSkill(cfg).execute("u1", {})
    assert "empty" in msg.lower() or "no unassigned" in msg.lower()
```

- [ ] **Step 2: Run to verify fail** — ImportError on `AutoAssignInboxSkill`.

- [ ] **Step 3: Implement**

```python
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
        import asyncio

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
        id_by_name = {p["name"]: p["id"] for p in projects}

        suggestions = await asyncio.gather(*(
            inbox_suggest.suggest_expense_project(
                self._config, user_id,
                description=e.get("description"), vendor=e.get("vendor"),
                amount=float(e.get("amount") or 0),
                currency=e.get("currency") or "EUR",
            )
            for e in batch
        ))

        applied, unsure = [], []
        for e, s in zip(batch, suggestions):
            label = e.get("description") or e.get("vendor") or "expense"
            target_id = id_by_name.get(s.project_name) if s.project_name else None
            if target_id and s.confidence in {"high", "medium"}:
                await store.update_expense(
                    self._config, user_id, e["id"], project_id=target_id,
                )
                applied.append(f"  ✓ {label} → **{s.project_name}**")
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
```

Register in `registry.py`.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_budget_inbox_skills.py -v`

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/skills/builtin/budget_manager.py lazyclaw/skills/registry.py tests/test_budget_inbox_skills.py
git commit -m "feat: auto_assign_inbox — confidence-gated AI filing of inbox expenses"
```

---

### Task 9: Runtime wiring (keywords, tool names, inline allowlists)

**Files:**
- Modify: `lazyclaw/runtime/agent.py:965-989` (`_BUDGET_KEYWORDS`, `_BUDGET_TOOL_NAMES`, `_QUICK_INLINE_BUDGET_WRITES`), `:1352-1359` (inline-read set inside `_is_readonly_inspection`)
- Test: `tests/runtime/test_budget_write_inline_autopromote.py` (append)

**Interfaces:**
- Produces: budget turns auto-inject the 4 new tools; "top up"/"inbox"/"assign" phrasing trips the additive gate; new reads + `move_expense`/`auto_assign_inbox` stay inline (no AUTO-PROMOTE).

- [ ] **Step 1: Write the failing test** (check `_is_readonly_inspection`'s exact signature at `agent.py:1305` first and mirror the file's existing import/call style)

```python
def test_new_budget_tools_and_keywords_wired():
    from lazyclaw.runtime import agent as agent_mod

    for name in ("move_expense", "auto_assign_inbox", "list_projects", "list_budget_topups"):
        assert name in agent_mod._BUDGET_TOOL_NAMES
    for kw in ("top up", "top-up", "topup", "inbox", "assign"):
        assert kw in agent_mod._BUDGET_KEYWORDS
    for name in ("move_expense", "auto_assign_inbox"):
        assert name in agent_mod._QUICK_INLINE_BUDGET_WRITES
    for name in ("list_projects", "list_budget_topups"):
        assert agent_mod._is_readonly_inspection(name)
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/runtime/test_budget_write_inline_autopromote.py -v -k wired`

- [ ] **Step 3: Implement** — replace the three frozensets:

```python
_BUDGET_KEYWORDS = frozenset({
    "budget", "budgets",
    "expense", "expenses", "spending",
    "spent", "cost", "costs", " spend",
    "how much have i spent", "how much did i spend", "how much spent",
    "recurring expense", "recurring charge", "monthly cost",
    "expense report", "spending report", "set budget",
    "top up", "top-up", "topup", "inbox", "assign",
})

_BUDGET_TOOL_NAMES = frozenset({
    "set_project_budget", "add_project_budget", "add_expense",
    "list_expenses", "expense_report", "add_recurring_expense",
    "set_default_expense_project",
    "move_expense", "auto_assign_inbox", "list_projects", "list_budget_topups",
})

_QUICK_INLINE_BUDGET_WRITES = frozenset({
    "add_expense", "set_project_budget", "add_project_budget",
    "add_recurring_expense", "set_default_expense_project",
    "move_expense", "auto_assign_inbox",
})
```

In the inline-read set at `:1352-1359`, add `"list_budget_topups"` after `"expense_report"` (keep `"list_projects"`/`"get_project"` — `list_projects` is now a real skill).

**Caution:** `"assign"` and `"inbox"` are broad keywords — the gate is purely additive (`agent.py:3378-3393`), so the blast radius is a few extra tool schemas on unrelated turns. Acceptable; do NOT add them anywhere that suppresses tools.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/runtime/test_budget_write_inline_autopromote.py -v`

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/runtime/agent.py tests/runtime/test_budget_write_inline_autopromote.py
git commit -m "feat: wire inbox + top-up skills into budget keyword gate and inline allowlists"
```

---

### Task 10: Specialist allowlist + SOUL.md discovery hint

**Files:**
- Modify: `lazyclaw/teams/specialists/tasks_specialist.md` (frontmatter `tools:` list + prompt body), `personality/SOUL.md` (the `search_tools("expense" | "budget" | …)` hint bullet)
- Test: `tests/test_budget_inbox_skills.py` (append)

**Interfaces:**
- Produces: Tasks & Budget Specialist can call the 4 new skills (allowlist is load-bearing — `teams/runner.py:743-757` blocks anything not listed); the SOUL hint names the complete budget toolset + inbox concept.

- [ ] **Step 1: Write the failing test**

```python
def test_tasks_specialist_allowlists_new_budget_skills():
    from pathlib import Path
    text = Path("lazyclaw/teams/specialists/tasks_specialist.md").read_text()
    for name in ("move_expense", "auto_assign_inbox", "list_projects", "list_budget_topups"):
        assert f"- {name}" in text, name
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement**

`tasks_specialist.md` — in the `tools:` frontmatter list, after `- set_project_budget`, add:

```yaml
  - move_expense
  - auto_assign_inbox
  - list_projects
  - list_budget_topups
```

`personality/SOUL.md` — update the budget hint bullet (the `search_tools("expense" | "budget" | "spent" | "cost")` line) to:

```markdown
- `search_tools("expense" | "budget" | "spent" | "cost" | "top up" | "inbox")` → budget/expense manager (add_expense, list_expenses, expense_report, list_projects, list_budget_topups, move_expense, auto_assign_inbox, set_project_budget, add_project_budget, add_recurring_expense). Unassigned expenses live in the 📥 Inbox (General project): `list_expenses(project="inbox")` shows them, `move_expense` files one, `auto_assign_inbox` files them by AI.
```

Also extend the specialist prompt body's review-routing lines (`tasks_specialist.md:48-50`) with: `Top-up questions → list_budget_topups. Unassigned expenses → the 📥 Inbox: list_expenses(project="inbox") / move_expense / auto_assign_inbox.`

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_budget_inbox_skills.py -v`. Then boot-check the loader for unknown-skill warnings: `python -c "from lazyclaw.teams import specialist_loader; print('loader import ok')"` and grep server logs on next boot for `startup_specialist_self_check` drift warnings.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/teams/specialists/tasks_specialist.md personality/SOUL.md tests/test_budget_inbox_skills.py
git commit -m "feat: specialist allowlist + SOUL discovery hints for inbox and top-up skills"
```

---

### Task 11: Full-surface verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Run every touched test file** (NOT the full suite — container may be up):

```bash
python -m pytest tests/test_budgets_routes.py tests/test_budget_store.py tests/budgets/test_inbox_suggest.py tests/test_budget_inbox_skills.py tests/test_budget_expense_skill.py tests/test_budget_pending.py tests/test_budget_recurring.py tests/test_budget_resolver.py tests/runtime/test_budget_write_inline_autopromote.py -v
```

Expected: ALL PASS.

- [ ] **Step 2: Registry smoke** — all four new skills register (adapt to the actual `SkillRegistry` constructor used in tests):

```bash
python -c "
import asyncio
from lazyclaw.skills.registry import SkillRegistry
r = SkillRegistry(config=None)
found = {n for n in ('move_expense','auto_assign_inbox','list_projects','list_budget_topups')
         if r.get(n) is not None} if hasattr(r, 'get') else set()
print(sorted(found))
"
```

Goal: all four names print (check how existing tests/registry consumers call it and mirror).

- [ ] **Step 3: Discovery smoke** — `python -c` asserting `"top up"` and `"topup"` both appear in `ListBudgetTopupsSkill` description (substring discovery guarantee).

- [ ] **Step 4: Report** — Phase 1 done. Remind the operator: prod picks this up only after `make rebuild`; Phase 2 (web) and Phase 3 (mobile) have their own plans.
