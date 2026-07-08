# Unified `agent` Dispatch Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One brain-facing `agent(agent_type, task, run_in_background, timeout)` tool — Claude Code dispatcher pattern: sync parallel fan-out with in-turn results, background opt-in — replacing the delegate/dispatch_subagents/run_background 3-way choice.

**Architecture:** New skill `AgentDispatchSkill` reuses `teams/runner.py:run_specialist` (sync path, throttled by a shared per-loop semaphore; TAOR's `asyncio.gather` gives parallelism for N calls in one message) and `TaskRunner.submit` (background path, existing consolidation). `explore` and `general_purpose` become declarative `.md` specialists; the runner gains a `tools: "*"` wildcard allowlist. `runtime/agent.py` registers the skill per turn and adds it to its gating sets.

**Tech Stack:** Python 3.11+, asyncio, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-07-unified-agent-dispatch-design.md`

## Global Constraints

- Fan-out width: max **15** `agent` calls per turn; in-flight concurrency default **6** (env `LAZYCLAW_DISPATCH_CONCURRENCY`).
- Sync result cap: **12,000 chars**, clipped with an explicit `[truncated N chars]` marker (2026-05-25 truncation-confabulation lesson).
- Single-depth: subagents can never call `agent` (`_IS_SUBAGENT` contextvar).
- Old tools (`delegate`, `dispatch_subagents`, `run_background`) stay registered during soak — descriptions demoted, nothing deleted.
- v1 scope: builtin agent types only (custom encrypted specialists = follow-up; matches current `delegate` parity).
- Immutability: frozen dataclasses, no mutation of shared state.
- **NEVER run full `pytest` while the prod container is up** (./data = live DB). Scoped test files only.
- Deploy requires `make rebuild` (source is baked into the Docker image).
- Working tree carries deployed-unmerged fixes — commit ONLY the files each task names (`git add <exact paths>`).

## File Structure

| File | Responsibility |
|---|---|
| Create `lazyclaw/teams/specialist_aliases.py` | Shared alias→SpecialistConfig resolution (delegate + agent) |
| Create `lazyclaw/teams/specialists/explore.md` | Declarative read-only research agent |
| Create `lazyclaw/teams/specialists/general_purpose.md` | Declarative all-tools executor (wildcard) |
| Create `lazyclaw/skills/builtin/agent_tool.py` | The unified `agent` skill (sync + background) |
| Modify `lazyclaw/skills/builtin/delegate.py` | Import shared aliases; demote description |
| Modify `lazyclaw/skills/builtin/dispatch.py` | Demote description |
| Modify `lazyclaw/skills/builtin/background.py` | Demote description |
| Modify `lazyclaw/teams/runner.py` | Wildcard allowlist in `_filter_tools` + execute-time check |
| Modify `lazyclaw/teams/specialist_loader.py` | `validate_specialist_tools` exempts `"*"` |
| Modify `lazyclaw/runtime/agent.py` | Register skill; gating sets; result cap; AUTO-PROMOTE/failsafe skips |
| Modify `personality/SOUL.md` | Dispatch sections rewritten around `agent` |
| Tests | `tests/teams/test_specialist_aliases.py`, `tests/teams/test_wildcard_allowlist.py`, `tests/teams/test_new_builtin_specialists.py`, `tests/runtime/test_agent_tool.py` |

---

### Task 1: Shared specialist alias resolver

**Files:**
- Create: `lazyclaw/teams/specialist_aliases.py`
- Modify: `lazyclaw/skills/builtin/delegate.py:36-77`
- Test: `tests/teams/test_specialist_aliases.py`

**Interfaces:**
- Consumes: `lazyclaw.teams.specialist.BUILTIN_SPECIALISTS`, `SpecialistConfig`
- Produces: `SHORT_ALIASES: dict[str, str]`, `SPECIALIST_MAP: dict[str, SpecialistConfig]`, `resolve_specialist(key: str) -> SpecialistConfig | None`, `specialist_choices() -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/teams/test_specialist_aliases.py
"""Shared alias resolver — one source of truth for delegate + agent."""
from lazyclaw.teams.specialist import SpecialistConfig
from lazyclaw.teams.specialist_aliases import (
    SPECIALIST_MAP,
    resolve_specialist,
    specialist_choices,
)


def test_short_alias_resolves_to_builtin():
    spec = resolve_specialist("browser")
    assert isinstance(spec, SpecialistConfig)
    assert spec.name == "browser_specialist"


def test_full_name_resolves():
    spec = resolve_specialist("freelance_specialist")
    assert spec is not None
    assert spec.name == "freelance_specialist"


def test_unknown_returns_none():
    assert resolve_specialist("nonexistent_agent_xyz") is None


def test_choices_cover_map_and_are_unique():
    choices = specialist_choices()
    assert len(choices) == len(set(choices))
    assert set(choices) == set(SPECIALIST_MAP.keys())
    assert "browser" in choices
    assert "upwork" in choices


def test_delegate_still_uses_same_map():
    # delegate.py must not keep a private fork of the map
    from lazyclaw.skills.builtin.delegate import _SPECIALIST_MAP
    assert _SPECIALIST_MAP is SPECIALIST_MAP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/teams/test_specialist_aliases.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lazyclaw.teams.specialist_aliases'`

- [ ] **Step 3: Create the module**

```python
# lazyclaw/teams/specialist_aliases.py
"""Shared short-alias → builtin specialist resolution.

Extracted from skills/builtin/delegate.py so `delegate` (legacy) and
`agent` (unified dispatch, ADR spec 2026-07-07) resolve agent types
identically. New builtin `.md` specialists auto-register here.
"""
from __future__ import annotations

from lazyclaw.teams.specialist import BUILTIN_SPECIALISTS, SpecialistConfig

# Intent word → full builtin specialist name.
SHORT_ALIASES: dict[str, str] = {
    "browser": "browser_specialist",
    "research": "research_specialist",
    "code": "code_specialist",
    "code_research": "code_research_specialist",
    "web_research": "web_research_specialist",
    "freelance": "freelance_specialist",
    "upwork": "freelance_specialist",
    "gig": "freelance_specialist",
    "email": "email_specialist",
    "messaging": "messaging_specialist",
    "whatsapp": "messaging_specialist",
    "instagram": "messaging_specialist",
    "telegram": "messaging_specialist",
    "notes": "notes_specialist",
    "memory": "notes_specialist",
    "lazybrain": "notes_specialist",
    "tasks": "tasks_specialist",
    "budget": "tasks_specialist",
    "documents": "documents_specialist",
    "docs": "documents_specialist",
    "contacts": "contacts_specialist",
    "pipeline": "contacts_specialist",
    "automation": "automation_specialist",
    "n8n": "automation_specialist",
    "bounty": "bounty_specialist",
    "system": "system_specialist",
}

_BUILTIN_BY_NAME: dict[str, SpecialistConfig] = {
    s.name: s for s in BUILTIN_SPECIALISTS
}

SPECIALIST_MAP: dict[str, SpecialistConfig] = {
    short: _BUILTIN_BY_NAME[full]
    for short, full in SHORT_ALIASES.items()
    if full in _BUILTIN_BY_NAME
}
# Every builtin is also addressable by its full name (aliases win ties).
for _s in BUILTIN_SPECIALISTS:
    SPECIALIST_MAP.setdefault(_s.name, _s)


def resolve_specialist(key: str) -> SpecialistConfig | None:
    """Resolve a short alias or full specialist name; None if unknown."""
    return SPECIALIST_MAP.get(key)


def specialist_choices() -> list[str]:
    """Stable schema-enum order — explore/general_purpose first (they are
    the Claude Code defaults), then everything else alphabetically."""
    front = [k for k in ("explore", "general_purpose") if k in SPECIALIST_MAP]
    rest = sorted(k for k in SPECIALIST_MAP if k not in front)
    return front + rest


__all__ = [
    "SHORT_ALIASES",
    "SPECIALIST_MAP",
    "resolve_specialist",
    "specialist_choices",
]
```

- [ ] **Step 4: Point delegate.py at the shared map**

In `lazyclaw/skills/builtin/delegate.py`, DELETE lines 36-77 (the `_BUILTIN_BY_NAME` dict, `_SHORT_ALIASES` dict, `_SPECIALIST_MAP` construction, and the `for _s in BUILTIN_SPECIALISTS: _SPECIALIST_MAP.setdefault(...)` loop) and replace with:

```python
# Single source of truth — shared with the unified `agent` tool.
from lazyclaw.teams.specialist_aliases import SPECIALIST_MAP as _SPECIALIST_MAP
```

Also remove the now-unused `BUILTIN_SPECIALISTS` import at the top of delegate.py (keep `SpecialistConfig` only if still referenced elsewhere in the file — check with `grep -n "SpecialistConfig\|BUILTIN_SPECIALISTS" lazyclaw/skills/builtin/delegate.py` and drop dead names from the import).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/teams/test_specialist_aliases.py -v`
Expected: 5 PASS

- [ ] **Step 6: Run existing delegate tests (regression)**

Run: `python -m pytest tests/ -k "delegate" -v --co -q 2>/dev/null | head -20` then run any files found, e.g. `python -m pytest tests/skills/test_delegate*.py -v` (skip if none exist).
Expected: PASS (or "no tests found" — fine)

- [ ] **Step 7: Commit**

```bash
git add lazyclaw/teams/specialist_aliases.py lazyclaw/skills/builtin/delegate.py tests/teams/test_specialist_aliases.py
git commit -m "refactor(teams): extract shared specialist alias resolver"
```

---

### Task 2: Wildcard `tools: "*"` allowlist support

**Files:**
- Modify: `lazyclaw/teams/runner.py:161-215` (`_filter_tools`) and `:682-692` (execute-time check)
- Modify: `lazyclaw/teams/specialist_loader.py:131-153` (`validate_specialist_tools`)
- Test: `tests/teams/test_wildcard_allowlist.py`

**Interfaces:**
- Consumes: `SpecialistConfig.allowed_skills`, `registry.list_tools()`, `registry.list_mcp_tools()`, `bare_tool_name`
- Produces: `runner.WILDCARD_TOOLS = "*"`, `runner.WILDCARD_DENYLIST: frozenset[str]` — later tasks (agent_tool tests) import these names exactly.

- [ ] **Step 1: Write the failing test**

```python
# tests/teams/test_wildcard_allowlist.py
"""tools: '*' gives a specialist every tool except dispatch tools."""
from lazyclaw.teams.runner import WILDCARD_DENYLIST, WILDCARD_TOOLS, _filter_tools
from lazyclaw.teams.specialist_loader import validate_specialist_tools
from lazyclaw.teams.specialist import SpecialistConfig


def _tool(name: str, desc: str = "") -> dict:
    return {"function": {"name": name, "description": desc}}


class FakeRegistry:
    def __init__(self, tools, mcp_tools=()):
        self._tools = list(tools)
        self._mcp = list(mcp_tools)

    def list_tools(self):
        return list(self._tools)

    def list_mcp_tools(self):
        return list(self._mcp)


def test_wildcard_includes_all_native_minus_denylist():
    reg = FakeRegistry([
        _tool("web_search"), _tool("browser"),
        _tool("agent"), _tool("delegate"),
        _tool("dispatch_subagents"), _tool("run_background"),
    ])
    out = _filter_tools(reg, (WILDCARD_TOOLS,))
    names = {t["function"]["name"] for t in out}
    assert names == {"web_search", "browser"}
    assert names.isdisjoint(WILDCARD_DENYLIST)


def test_wildcard_unions_mcp_tools():
    reg = FakeRegistry(
        [_tool("web_search")],
        mcp_tools=[_tool("mcp_abc123_upwork_get_messages")],
    )
    out = _filter_tools(reg, (WILDCARD_TOOLS,))
    names = {t["function"]["name"] for t in out}
    assert "mcp_abc123_upwork_get_messages" in names


def test_wildcard_no_duplicates():
    reg = FakeRegistry(
        [_tool("web_search")],
        mcp_tools=[_tool("web_search")],
    )
    out = _filter_tools(reg, (WILDCARD_TOOLS,))
    assert len(out) == 1


def test_exact_allowlist_unchanged():
    reg = FakeRegistry([_tool("web_search"), _tool("browser")])
    out = _filter_tools(reg, ("web_search",))
    assert [t["function"]["name"] for t in out] == ["web_search"]


def test_validator_exempts_wildcard():
    spec = SpecialistConfig(
        name="gp", display_name="GP", system_prompt="x",
        allowed_skills=(WILDCARD_TOOLS,),
    )
    assert validate_specialist_tools([spec], ["web_search"]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/teams/test_wildcard_allowlist.py -v`
Expected: FAIL with `ImportError: cannot import name 'WILDCARD_DENYLIST'`

- [ ] **Step 3: Implement wildcard in `runner.py`**

Add module constants right above `_filter_tools` (after the `_bare_tool_name` alias at line 158):

```python
# Wildcard allowlist: a specialist with `tools: "*"` gets EVERY registry
# tool except dispatch tools — single-depth means a subagent must never
# be able to re-dispatch (mirrors Claude Code's general-purpose agent).
WILDCARD_TOOLS = "*"
WILDCARD_DENYLIST: frozenset[str] = frozenset({
    "agent", "delegate", "dispatch_subagents", "run_background",
})
```

Inside `_filter_tools`, immediately after `allowed_set = set(allowed)` (line 177), add the wildcard branch:

```python
    if WILDCARD_TOOLS in allowed_set:
        seen: set[str] = set()
        out = []
        for t in all_tools:
            nm = t["function"]["name"]
            if nm not in WILDCARD_DENYLIST and nm not in seen:
                out.append(t)
                seen.add(nm)
        try:
            mcp_tools = registry.list_mcp_tools()
        except Exception:
            mcp_tools = []
        for t in mcp_tools:
            nm = t.get("function", {}).get("name", "")
            if (
                nm
                and nm not in seen
                and _bare_tool_name(nm) not in WILDCARD_DENYLIST
            ):
                out.append(t)
                seen.add(nm)
        return out
```

(Note: `all_tools = registry.list_tools()` already executes at line 176 before this branch.)

- [ ] **Step 4: Implement execute-time wildcard check in `runner.py`**

At the allowlist gate (line ~682, `if tc.name not in specialist.allowed_skills and not _is_scraper_tool and not _is_allowed_mcp:`), insert above the `if`:

```python
                _wildcard_ok = (
                    WILDCARD_TOOLS in specialist.allowed_skills
                    and tc.name not in WILDCARD_DENYLIST
                    and _bare_tool_name(tc.name) not in WILDCARD_DENYLIST
                )
```

and extend the condition to:

```python
                if (
                    tc.name not in specialist.allowed_skills
                    and not _wildcard_ok
                    and not _is_scraper_tool
                    and not _is_allowed_mcp
                ):
```

- [ ] **Step 5: Exempt `"*"` in the loader validator**

In `specialist_loader.py:validate_specialist_tools`, extend the `unknown` list-comprehension filter:

```python
        unknown = [
            t
            for t in s.allowed_skills
            if t not in known
            and t != "*"
            and not t.startswith("mcp_")
            and not t.startswith("mcp__")
        ]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/teams/test_wildcard_allowlist.py -v`
Expected: 5 PASS

- [ ] **Step 7: Commit**

```bash
git add lazyclaw/teams/runner.py lazyclaw/teams/specialist_loader.py tests/teams/test_wildcard_allowlist.py
git commit -m "feat(teams): wildcard tools allowlist for specialists"
```

---

### Task 3: Declarative `explore` + `general_purpose` builtin specialists

**Files:**
- Create: `lazyclaw/teams/specialists/explore.md`
- Create: `lazyclaw/teams/specialists/general_purpose.md`
- Test: `tests/teams/test_new_builtin_specialists.py`

**Interfaces:**
- Consumes: `specialist_loader.load_builtin_specialists`, dispatcher's `_EXPLORE_SYSTEM_PROMPT` (copied verbatim)
- Produces: builtin specialists named `explore` and `general_purpose` — Task 4's schema enum picks them up automatically via `specialist_choices()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/teams/test_new_builtin_specialists.py
"""explore + general_purpose are declarative builtin specialists."""
from lazyclaw.teams.specialist_loader import load_builtin_specialists


def _by_name():
    return {s.name: s for s in load_builtin_specialists()}


def test_explore_loads():
    spec = _by_name()["explore"]
    assert spec.include_scraper is True
    assert "web_search" in spec.allowed_skills
    assert "read_file" in spec.allowed_skills
    assert spec.is_builtin is True
    assert "read-only" in spec.system_prompt.lower()


def test_general_purpose_loads_with_wildcard():
    spec = _by_name()["general_purpose"]
    assert spec.allowed_skills == ("*",)
    assert spec.is_builtin is True


def test_aliases_pick_up_new_builtins():
    # specialist_aliases builds from BUILTIN_SPECIALISTS at import time;
    # a fresh import in a fresh process would include them. Here we only
    # assert the loader output, since BUILTIN_SPECIALISTS is import-cached.
    names = set(_by_name())
    assert {"explore", "general_purpose"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/teams/test_new_builtin_specialists.py -v`
Expected: FAIL with `KeyError: 'explore'`

- [ ] **Step 3: Create `explore.md` from the dispatcher prompt (verbatim)**

The body must be byte-identical to the proven `_EXPLORE_SYSTEM_PROMPT` in `runtime/dispatcher.py:82-129`. Generate the file:

```bash
python3 - <<'EOF'
from lazyclaw.runtime.dispatcher import _EXPLORE_SYSTEM_PROMPT
fm = """---
name: explore
display_name: Explore Agent
include_scraper: true
tools:
  - web_search
  - search_tools
  - recall_memories
  - read_file
  - list_directory
  - browser
---
"""
with open("lazyclaw/teams/specialists/explore.md", "w", encoding="utf-8") as f:
    f.write(fm + _EXPLORE_SYSTEM_PROMPT + "\n")
print("wrote explore.md")
EOF
```

(`include_scraper: true` replaces the dispatcher's manual `mcp_scraper_*` name union — `_filter_tools` unions scraper tools by description match.)

- [ ] **Step 4: Create `general_purpose.md`**

```markdown
---
name: general_purpose
display_name: General-Purpose Agent
tools: "*"
---
You are a general-purpose agent handling a delegated subtask. You have the
full tool set (except dispatch — you cannot spawn further agents).

- Complete the task FULLY before returning. Chain as many tool calls as the
  task needs; don't stop halfway to ask questions — nobody can answer them.
- You have NO conversation history. Everything you need is in the task text.
  If something essential is genuinely missing, say exactly what and stop.
- Never invent data. If a lookup fails, report the failure plainly.
- Return a clear, structured summary: what you did, what you found, exact
  values (IDs, URLs, amounts) the caller needs to act on your result.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/teams/test_new_builtin_specialists.py tests/teams/test_specialist_aliases.py -v`
Expected: all PASS (alias tests still green — new builtins join `SPECIALIST_MAP` via the setdefault loop)

- [ ] **Step 6: Sanity-check the startup self-check doesn't warn on `"*"`**

Run: `python3 -c "
from lazyclaw.teams.specialist_loader import load_builtin_specialists, validate_specialist_tools
specs = load_builtin_specialists()
print(validate_specialist_tools([s for s in specs if s.name=='general_purpose'], ['web_search']))"`
Expected output: `{}`

- [ ] **Step 7: Commit**

```bash
git add lazyclaw/teams/specialists/explore.md lazyclaw/teams/specialists/general_purpose.md tests/teams/test_new_builtin_specialists.py
git commit -m "feat(teams): declarative explore + general_purpose builtin specialists"
```

---

### Task 4: `agent` skill — sync path

**Files:**
- Create: `lazyclaw/skills/builtin/agent_tool.py`
- Test: `tests/runtime/test_agent_tool.py`

**Interfaces:**
- Consumes: `resolve_specialist`/`specialist_choices` (Task 1), `run_specialist` (existing, imported lazily inside the method), `_IS_SUBAGENT` (from `runtime/dispatcher.py`), `TeamLead.register/complete/fail`, `StepTrackingCallback`
- Produces: `AgentDispatchSkill(config, registry, eco_router, permission_checker, callback, team_lead, task_runner, chat_session_id, fanout_group_id)` with skill name `"agent"`; module constants `MAX_AGENTS_PER_TURN = 15`, `MAX_AGENT_RESULT_CHARS = 12000`; helper `clip_agent_result(text, cap) -> str`. Task 6 registers this class in `runtime/agent.py`.

- [ ] **Step 1: Write the failing tests (sync core)**

```python
# tests/runtime/test_agent_tool.py
"""Unified `agent` dispatch skill — sync + background paths."""
import asyncio

import pytest

from lazyclaw.runtime.dispatcher import _IS_SUBAGENT
from lazyclaw.skills.builtin.agent_tool import (
    MAX_AGENT_RESULT_CHARS,
    MAX_AGENTS_PER_TURN,
    AgentDispatchSkill,
    clip_agent_result,
)
from lazyclaw.teams.runner import SpecialistResult


def _make_skill(**overrides):
    kwargs = dict(
        config=None, registry=object(), eco_router=object(),
        permission_checker=None, callback=None, team_lead=None,
        task_runner=None, chat_session_id="sess-1", fanout_group_id="fg-1",
    )
    kwargs.update(overrides)
    return AgentDispatchSkill(**kwargs)


def _ok_result(text="all done"):
    return SpecialistResult(
        agent_name="explore", task="t", result=text,
        tools_used=("web_search",), model_used="worker", duration_ms=10,
    )


@pytest.fixture
def fake_run_specialist(monkeypatch):
    calls = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return _ok_result()

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _fake)
    return calls


def test_schema_shape():
    skill = _make_skill()
    schema = skill.parameters_schema
    props = schema["properties"]
    assert "explore" in props["agent_type"]["enum"]
    assert "general_purpose" in props["agent_type"]["enum"]
    assert "browser" in props["agent_type"]["enum"]
    assert props["run_in_background"]["default"] is False
    assert schema["required"] == ["agent_type", "task"]
    assert skill.name == "agent"


def test_sync_returns_result_in_turn(fake_run_specialist):
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "find X",
    }))
    assert "all done" in out
    assert "[agent:explore]" in out
    assert len(fake_run_specialist) == 1
    assert fake_run_specialist[0]["task"] == "find X"


def test_unknown_agent_type_lists_valid(fake_run_specialist):
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "wat", "task": "x",
    }))
    assert "Unknown agent_type" in out
    assert "explore" in out
    assert not fake_run_specialist


def test_missing_task_errors(fake_run_specialist):
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {"agent_type": "explore"}))
    assert out.startswith("Error")
    assert not fake_run_specialist


def test_depth_guard_blocks_nested(fake_run_specialist):
    skill = _make_skill()

    async def _run():
        token = _IS_SUBAGENT.set(True)
        try:
            return await skill.execute("u1", {
                "agent_type": "explore", "task": "x",
            })
        finally:
            _IS_SUBAGENT.reset(token)

    out = asyncio.run(_run())
    assert "single-depth" in out
    assert not fake_run_specialist


def test_per_turn_cap(fake_run_specialist):
    skill = _make_skill()
    skill._calls_this_turn = MAX_AGENTS_PER_TURN
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x",
    }))
    assert "fan-out cap" in out
    assert not fake_run_specialist


def test_timeout_returns_timeout_status(monkeypatch):
    import lazyclaw.skills.builtin.agent_tool as at
    monkeypatch.setattr(at, "_MIN_TIMEOUT_S", 0)

    async def _slow(**kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _slow)
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x", "timeout": 0,
    }))
    assert "TIMEOUT" in out


def test_crash_returns_failed_status(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _boom)
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x",
    }))
    assert "FAILED" in out
    assert "kaput" in out


def test_clip_appends_marker():
    text = "x" * (MAX_AGENT_RESULT_CHARS + 500)
    out = clip_agent_result(text)
    assert len(out) < len(text)
    assert "[truncated 500 chars]" in out


def test_clip_noop_under_cap():
    assert clip_agent_result("short") == "short"


def test_concurrency_semaphore(monkeypatch):
    """6 concurrent max by default; excess queue."""
    import lazyclaw.skills.builtin.agent_tool as at
    at._SEMAPHORES.clear()
    monkeypatch.setenv("LAZYCLAW_DISPATCH_CONCURRENCY", "2")

    active = {"now": 0, "peak": 0}

    async def _tracked(**kwargs):
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0.05)
        active["now"] -= 1
        return _ok_result()

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _tracked)
    skill = _make_skill()

    async def _fan():
        return await asyncio.gather(*[
            skill.execute("u1", {"agent_type": "explore", "task": f"t{i}"})
            for i in range(5)
        ])

    results = asyncio.run(_fan())
    at._SEMAPHORES.clear()
    assert len(results) == 5
    assert active["peak"] <= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/runtime/test_agent_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lazyclaw.skills.builtin.agent_tool'`

- [ ] **Step 3: Implement the skill (sync path + stub background)**

```python
# lazyclaw/skills/builtin/agent_tool.py
"""Unified `agent` dispatch skill — Claude Code dispatcher pattern.

ONE brain-facing tool replaces the delegate / dispatch_subagents /
run_background 3-way choice:

    agent(agent_type, task, run_in_background=false, timeout=120)

Sync (default): runs the specialist loop inline and returns its result
as THIS tool call's result. The TAOR loop executes independent tool
calls concurrently, so N `agent` calls in one assistant message run in
parallel (throttled by a shared per-loop semaphore) and the brain
synthesizes every result in the same turn — no consolidation turn.

Background: routes to TaskRunner (fresh full Agent, up to 10 concurrent)
with the per-turn fanout group so siblings consolidate into one reply.

Spec: docs/superpowers/specs/2026-07-07-unified-agent-dispatch-design.md
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING

from lazyclaw.runtime.callbacks import AgentEvent, StepTrackingCallback
from lazyclaw.runtime.dispatcher import _IS_SUBAGENT
from lazyclaw.skills.base import BaseSkill
from lazyclaw.teams.specialist_aliases import (
    resolve_specialist,
    specialist_choices,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.runtime.team_lead import TeamLead
    from lazyclaw.skills.registry import SkillRegistry
    from lazyclaw.teams.specialist import SpecialistConfig

logger = logging.getLogger(__name__)

# Fan-out width per turn — call N+1 gets a clear error, never a silent
# infinite queue. The skill is re-registered fresh each TAOR turn
# (runtime/agent.py), so the counter resets naturally.
MAX_AGENTS_PER_TURN = 15

# Sync-result cap. Subagent results are load-bearing synthesis input —
# per the 2026-05-25 truncation-confabulation incident the cap is
# generous and the cut is ALWAYS marked. runtime/agent.py mirrors this
# in _MAX_TOOL_RESULT_CHARS_AGENT so its generic capper never re-chops.
MAX_AGENT_RESULT_CHARS = 12000

_DEFAULT_TIMEOUT_S = 120
_MAX_TIMEOUT_S = 600
_MIN_TIMEOUT_S = 10


def _agent_concurrency() -> int:
    raw = os.environ.get("LAZYCLAW_DISPATCH_CONCURRENCY")
    if raw:
        try:
            n = int(raw)
            if n >= 1:
                return n
        except ValueError:
            pass
    return 6


# One semaphore per event loop (the server has one loop; tests spin
# fresh loops). Keyed by loop id so a dead loop's entry is just inert.
_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def _loop_semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    sem = _SEMAPHORES.get(loop_id)
    if sem is None:
        sem = asyncio.Semaphore(_agent_concurrency())
        _SEMAPHORES[loop_id] = sem
    return sem


def clip_agent_result(text: str, cap: int = MAX_AGENT_RESULT_CHARS) -> str:
    """Clip with an EXPLICIT marker so F1/triage tooling sees the cut."""
    if len(text) <= cap:
        return text
    dropped = len(text) - cap
    return f"{text[:cap]}\n[truncated {dropped} chars]"


class AgentDispatchSkill(BaseSkill):
    """Dispatch a task to a sub-agent (sync fan-out or background)."""

    def __init__(
        self,
        config: Config | None = None,
        registry: SkillRegistry | None = None,
        eco_router: EcoRouter | None = None,
        permission_checker=None,
        callback: AgentCallback | None = None,
        team_lead: TeamLead | None = None,
        task_runner=None,
        chat_session_id: str | None = None,
        fanout_group_id: str | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker
        self._callback = callback
        self._team_lead = team_lead
        self._task_runner = task_runner
        self._chat_session_id = chat_session_id
        self._fanout_group_id = fanout_group_id
        self._caller_depth = 0
        self._calls_this_turn = 0

    # Skill-executor ceiling — real budget is the per-call wait_for.
    timeout = _MAX_TIMEOUT_S + 60

    @property
    def name(self) -> str:
        return "agent"

    @property
    def display_name(self) -> str:
        return "Dispatch Agent"

    @property
    def description(self) -> str:
        return (
            "Dispatch a task to a sub-agent. DEFAULT (run_in_background="
            "false): the agent runs NOW and this call returns its result — "
            "call `agent` MULTIPLE TIMES IN ONE MESSAGE to fan out parallel "
            "agents; all results come back this turn and you synthesize ONE "
            "reply. Set run_in_background=true ONLY for slow work (>2 min: "
            "bulk scrapes, long browser flows) — you get a task id now and "
            "one consolidated report later. Agent types: explore (read-only "
            "research), general_purpose (all tools, multi-step), or a domain "
            "specialist (browser, upwork, email, notes, tasks, documents, "
            "...). Each agent has its own tools and NO chat history — write "
            "self-contained tasks with every name/URL/number it needs."
        )

    @property
    def category(self) -> str:
        return "orchestration"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "enum": specialist_choices(),
                    "description": (
                        "explore = read-only research/search. "
                        "general_purpose = multi-step task, full tools. "
                        "Domain names (browser, upwork, email, ...) = "
                        "scoped specialist."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Complete, self-contained instruction. The agent "
                        "has NO chat history — include every URL, name, "
                        "number, and the success criteria."
                    ),
                },
                "run_in_background": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "true = slow work; task id now, consolidated "
                        "report later. false (default) = result returns "
                        "in THIS turn."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "default": _DEFAULT_TIMEOUT_S,
                    "maximum": _MAX_TIMEOUT_S,
                    "description": "Sync budget in seconds (default 120).",
                },
            },
            "required": ["agent_type", "task"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        agent_type = (params.get("agent_type") or "").strip()
        task = (params.get("task") or "").strip()
        run_bg = bool(params.get("run_in_background", False))
        try:
            timeout_s = int(params.get("timeout", _DEFAULT_TIMEOUT_S))
        except (TypeError, ValueError):
            timeout_s = _DEFAULT_TIMEOUT_S
        timeout_s = max(_MIN_TIMEOUT_S, min(timeout_s, _MAX_TIMEOUT_S))

        if not task:
            return "Error: task is required"

        spec = resolve_specialist(agent_type)
        if spec is None:
            return (
                f"Unknown agent_type '{agent_type}'. "
                f"Valid: {', '.join(specialist_choices())}"
            )

        if _IS_SUBAGENT.get():
            return (
                "Error: sub-agents cannot spawn sub-agents (single-depth). "
                "Complete the task with your own tools."
            )

        if run_bg:
            return await self._execute_background(user_id, agent_type, task)

        self._calls_this_turn += 1
        if self._calls_this_turn > MAX_AGENTS_PER_TURN:
            return (
                f"Error: fan-out cap reached ({MAX_AGENTS_PER_TURN} agents "
                f"this turn). Synthesize what you have, or use "
                f"run_in_background=true for the remainder."
            )

        return await self._execute_sync(
            user_id, spec, agent_type, task, timeout_s,
        )

    # ── Sync path ────────────────────────────────────────────────────

    async def _execute_sync(
        self,
        user_id: str,
        spec: SpecialistConfig,
        agent_type: str,
        task: str,
        timeout_s: int,
    ) -> str:
        from lazyclaw.teams import runner as team_runner

        task_id = f"agent-{uuid.uuid4().hex[:8]}"
        if self._team_lead is not None:
            try:
                self._team_lead.register(
                    task_id=task_id,
                    name=spec.name,
                    description=task[:80],
                    lane="subagent",
                    instruction_full=task,
                    user_id=user_id,
                )
            except Exception:
                logger.debug(
                    "team_lead.register failed for %s", task_id, exc_info=True,
                )

        wrapped_callback = self._callback
        if self._team_lead is not None and self._callback is not None:
            wrapped_callback = StepTrackingCallback(
                inner=self._callback,
                team_lead=self._team_lead,
                task_id=task_id,
            )

        if self._callback:
            await self._callback.on_event(AgentEvent(
                "specialist_start",
                spec.name,
                {"specialist": spec.name, "task": task[:200]},
            ))

        started = time.monotonic()

        async def _acquire_and_run():
            async with _loop_semaphore():
                return await team_runner.run_specialist(
                    user_id=user_id,
                    specialist=spec,
                    task=task,
                    registry=self._registry,
                    eco_router=self._eco_router,
                    permission_checker=self._permission_checker,
                    callback=wrapped_callback,
                    task_id=task_id,
                )

        try:
            result = await asyncio.wait_for(
                _acquire_and_run(), timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            self._mark_failed(task_id, f"timeout after {timeout_s}s")
            return (
                f"[agent:{agent_type}] TIMEOUT after {timeout_s}s — the "
                f"agent was cancelled. For slow work retry with "
                f"run_in_background=true."
            )
        except Exception as exc:
            logger.exception("agent %s (%s) crashed", task_id, agent_type)
            self._mark_failed(task_id, str(exc))
            return f"[agent:{agent_type}] FAILED: {exc}"

        elapsed = int(time.monotonic() - started)
        body = clip_agent_result(result.result or "")

        if self._team_lead is not None:
            try:
                if result.success:
                    self._team_lead.complete(task_id, result_preview=body[:200])
                else:
                    self._team_lead.fail(task_id, error=result.error or "")
            except Exception:
                logger.debug("team_lead settle failed", exc_info=True)

        status = (
            "completed" if result.success
            else f"FAILED: {result.error or 'unknown error'}"
        )
        return f"[agent:{agent_type}] {status} in {elapsed}s\n{body}"

    def _mark_failed(self, task_id: str, error: str) -> None:
        if self._team_lead is not None:
            try:
                self._team_lead.fail(task_id, error=error)
            except Exception:
                logger.debug("team_lead.fail failed", exc_info=True)

    # ── Background path (Task 5 fills tests) ────────────────────────

    async def _execute_background(
        self, user_id: str, agent_type: str, task: str,
    ) -> str:
        if not self._task_runner:
            return "Error: background task runner not configured"
        try:
            task_id = await self._task_runner.submit(
                user_id=user_id,
                instruction=task,
                name=f"agent:{agent_type}",
                callback=self._callback,
                source="brain",
                fanout_group_id=self._fanout_group_id,
                chat_session_id=self._chat_session_id,
                caller_depth=self._caller_depth,
            )
        except RuntimeError as exc:
            return f"Cannot start background agent: {exc}"
        return (
            f"Background agent '{agent_type}' started (id: {task_id[:8]}). "
            f"One consolidated report will follow when it finishes."
        )
```

NOTE the import style in `_execute_sync`: `from lazyclaw.teams import runner as team_runner` then `team_runner.run_specialist(...)` — module-attribute access so `monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", ...)` works.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/runtime/test_agent_tool.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/skills/builtin/agent_tool.py tests/runtime/test_agent_tool.py
git commit -m "feat(skills): unified agent dispatch tool — sync parallel fan-out"
```

---

### Task 5: `agent` skill — background path tests

**Files:**
- Modify: `tests/runtime/test_agent_tool.py` (append)

**Interfaces:**
- Consumes: `AgentDispatchSkill._execute_background` (implemented in Task 4)
- Produces: verified background contract for Task 6's wiring

- [ ] **Step 1: Write the failing/verifying tests**

Append to `tests/runtime/test_agent_tool.py`:

```python
class FakeTaskRunner:
    def __init__(self, raise_exc: Exception | None = None):
        self.submits = []
        self._raise = raise_exc

    async def submit(self, **kwargs):
        if self._raise:
            raise self._raise
        self.submits.append(kwargs)
        return "task123456789"


def test_background_routes_to_task_runner(fake_run_specialist):
    tr = FakeTaskRunner()
    skill = _make_skill(task_runner=tr)
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "long scrape",
        "run_in_background": True,
    }))
    assert "Background agent 'explore' started" in out
    assert not fake_run_specialist          # sync path NOT taken
    sub = tr.submits[0]
    assert sub["instruction"] == "long scrape"
    assert sub["source"] == "brain"
    assert sub["fanout_group_id"] == "fg-1"
    assert sub["chat_session_id"] == "sess-1"
    assert sub["name"] == "agent:explore"


def test_background_without_runner_errors():
    skill = _make_skill(task_runner=None)
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x", "run_in_background": True,
    }))
    assert "not configured" in out


def test_background_submit_rejection_surfaces():
    tr = FakeTaskRunner(raise_exc=RuntimeError("per-user cap reached"))
    skill = _make_skill(task_runner=tr)
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x", "run_in_background": True,
    }))
    assert "Cannot start background agent" in out
    assert "per-user cap reached" in out


def test_background_does_not_consume_sync_cap():
    tr = FakeTaskRunner()
    skill = _make_skill(task_runner=tr)
    before = skill._calls_this_turn
    asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x", "run_in_background": True,
    }))
    assert skill._calls_this_turn == before
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/runtime/test_agent_tool.py -v`
Expected: all PASS (implementation already exists from Task 4; if any fail, fix `_execute_background` — TaskRunner's own 10-per-user cap governs background, so the sync counter must not increment on the bg path)

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_agent_tool.py
git commit -m "test(skills): agent tool background path contract"
```

---

### Task 6: Wire `agent` into `runtime/agent.py` + demote old tools

**Files:**
- Modify: `lazyclaw/runtime/agent.py` (7 edit sites, exact anchors below)
- Modify: `lazyclaw/skills/builtin/delegate.py` (description), `lazyclaw/skills/builtin/dispatch.py` (description), `lazyclaw/skills/builtin/background.py` (description)
- Test: append to `tests/runtime/test_agent_tool.py`

**Interfaces:**
- Consumes: `AgentDispatchSkill` (Task 4), `MAX_AGENT_RESULT_CHARS`
- Produces: `agent` in `_BASE_TOOL_NAMES`/`_META_TOOLS`/`_DISPATCH_ONLY_TOOLS`/`_LOCAL_TOOL_NAMES`; `_MAX_TOOL_RESULT_CHARS_AGENT = 12000`; AUTO-PROMOTE + action-claim failsafe treat `agent` as already-dispatched

- [ ] **Step 1: Write the failing test (gating sets + cap constant)**

Append to `tests/runtime/test_agent_tool.py`:

```python
def test_agent_in_brain_gating_sets():
    from lazyclaw.runtime import agent as agent_mod
    assert "agent" in agent_mod._BASE_TOOL_NAMES
    assert "agent" in agent_mod._META_TOOLS
    assert "agent" in agent_mod._DISPATCH_ONLY_TOOLS
    assert "agent" in agent_mod._LOCAL_TOOL_NAMES


def test_agent_result_cap_constant_matches_skill():
    from lazyclaw.runtime import agent as agent_mod
    assert agent_mod._MAX_TOOL_RESULT_CHARS_AGENT == MAX_AGENT_RESULT_CHARS
    assert agent_mod._MAX_TOOL_RESULT_CHARS_AGENT > agent_mod._MAX_TOOL_RESULT_CHARS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/runtime/test_agent_tool.py -k "gating or cap_constant" -v`
Expected: FAIL with `AssertionError` (agent not in sets) / `AttributeError` (`_MAX_TOOL_RESULT_CHARS_AGENT`)

- [ ] **Step 3: Edit the four tool-name sets** (`agent.py:161-197`)

Add `"agent"` to each frozenset literal:
- `_BASE_TOOL_NAMES` (line 161): `"search_tools", "web_search", ..., "agent", "delegate", ...`
- `_LOCAL_TOOL_NAMES` (line 170): add `"agent"`
- `_META_TOOLS` (line 183): add `"agent"`
- `_DISPATCH_ONLY_TOOLS` (line 195): add `"agent"`

- [ ] **Step 4: Add the result-cap constant + branch** (`agent.py:~1261` and `~1806`)

After `_MAX_TOOL_RESULT_CHARS_CHANNEL_READ = 50000` (line 1261) add:

```python
# Unified `agent` tool results are load-bearing synthesis input (the
# whole point of a sync sub-agent is that the brain reads its output).
# Mirrors skills/builtin/agent_tool.MAX_AGENT_RESULT_CHARS — the skill
# already clips with an explicit marker; this cap only guarantees the
# generic capper below never re-chops it to 4K.
_MAX_TOOL_RESULT_CHARS_AGENT = 12000
```

At the cap-selection site (~line 1806-1810), change:

```python
    cap = (
        _MAX_TOOL_RESULT_CHARS_CHANNEL_READ
        if _is_channel_read_tool_name(tool_name)
        else _MAX_TOOL_RESULT_CHARS
    )
```

to:

```python
    if _is_channel_read_tool_name(tool_name):
        cap = _MAX_TOOL_RESULT_CHARS_CHANNEL_READ
    elif tool_name == "agent":
        cap = _MAX_TOOL_RESULT_CHARS_AGENT
    else:
        cap = _MAX_TOOL_RESULT_CHARS
```

(Match the actual surrounding code shape at that line — it may be an inline conditional; preserve variable names.)

- [ ] **Step 5: Register the skill per turn** (`agent.py:~2900`, right after `self.registry.register(bg_skill)`)

```python
            # Unified `agent` dispatch tool (Claude Code dispatcher
            # pattern) — sync parallel fan-out with in-turn results,
            # background opt-in. Shares the turn's fanout group so
            # background siblings consolidate with run_background's.
            from lazyclaw.skills.builtin.agent_tool import AgentDispatchSkill

            agent_dispatch_skill = AgentDispatchSkill(
                config=self.config,
                registry=self.registry,
                eco_router=self.eco_router,
                permission_checker=self.executor._checker if self.executor else None,
                callback=cb,
                team_lead=self._team_lead,
                task_runner=self._task_runner,
                chat_session_id=chat_session_id,
                fanout_group_id=_bg_fanout_group_id,
            )
            agent_dispatch_skill._caller_depth = self._depth
            self.registry.register(agent_dispatch_skill)
```

- [ ] **Step 6: Treat `agent` as already-dispatched in both failsafes**

Site A — AUTO-PROMOTE condition (`agent.py:~6941`): alongside the existing lines, add:

```python
                    # `agent` (unified dispatch) — sync calls return their
                    # results in-turn and bg calls already went to the task
                    # runner; promoting either is redundant.
                    and "agent" not in _called_tool_names
```

Site B — action-claim failsafe (`agent.py:~5324`): after `and "delegate" not in _called_tool_names`, add:

```python
                        and "agent" not in _called_tool_names
```

Then verify no other site was missed:

Run: `grep -n '"dispatch_subagents" not in _called_tool_names' lazyclaw/runtime/agent.py`
Expected: every hit has an adjacent `"agent" not in _called_tool_names` line after this step.

- [ ] **Step 7: Demote old tool descriptions**

In `delegate.py` `description` property, prepend: `"LEGACY — prefer the `agent` tool. "`
In `dispatch.py` `description` property (line ~90-127), prepend the same.
In `background.py` `description` property (line 63), prepend: `"LEGACY — prefer `agent` with run_in_background=true. "`

- [ ] **Step 8: Run the full new-test surface**

Run: `python -m pytest tests/runtime/test_agent_tool.py tests/teams/test_specialist_aliases.py tests/teams/test_wildcard_allowlist.py tests/teams/test_new_builtin_specialists.py -v`
Expected: all PASS

- [ ] **Step 9: Import smoke test** (catches circulars — `agent_tool` imports `dispatcher` + `specialist_aliases` which imports `specialist` → `specialist_loader`)

Run: `python3 -c "import lazyclaw.runtime.agent; import lazyclaw.skills.builtin.agent_tool; print('ok')"`
Expected: `ok`

- [ ] **Step 10: Commit**

```bash
git add lazyclaw/runtime/agent.py lazyclaw/skills/builtin/delegate.py lazyclaw/skills/builtin/dispatch.py lazyclaw/skills/builtin/background.py tests/runtime/test_agent_tool.py
git commit -m "feat(runtime): wire unified agent tool into brain loop, demote legacy dispatch tools"
```

---

### Task 7: SOUL.md dispatch rewrite

**Files:**
- Modify: `personality/SOUL.md` (lines 22-28, 38, 51-56, 77, 105-107, 140-151)

Per `feedback_prompt_before_runtime`: SOUL is the first lever — this task makes `agent` the natural path so the runtime forcing machinery fires less.

- [ ] **Step 1: Rewrite the fan-out rules block (lines 22-24)**

Replace:

```markdown
- **1 long batch on ONE thing** → `run_background(instruction="…")`. Brain stays free; consolidator returns one merged reply.
- **2–5 chunks of similar work** → `dispatch_subagents([{type:"explore", task:"chunk 1 of N — handle items 1-7"}, …])`. Each worker batches its chunk via `mcp_scraper_batch_*` tools. Brain consolidates when ALL siblings settle.
- **Need merged answer in THIS turn** → `delegate(specialist="…")` once.
```

with:

```markdown
- **Need answers THIS turn (research, reads, multi-part questions)** → call `agent(agent_type=…, task=…)` — up to 15 calls IN ONE MESSAGE run in parallel; every result comes back to you this turn and you write ONE synthesized reply.
- **1 slow job (>2 min: bulk scrape, long browser flow)** → `agent(agent_type="general_purpose", task="…", run_in_background=true)`. Brain stays free; one consolidated report follows.
- **≥6 similar lookups** → ONE background `agent` whose task says to use `mcp_scraper_batch_*` tools. Never spawn N agents for N similar rows.
```

- [ ] **Step 2: Update the nudge sentence (line 28)**

Replace `emit a `dispatch_subagents` or `run_background` call` with `emit `agent` calls (run_in_background=true for slow work)`.

- [ ] **Step 3: Update the dispatch-decision block (line 38)**

Replace `run_background(instruction="<self-contained restatement…>", name="<short-name>")` with `agent(agent_type="general_purpose", task="<self-contained restatement: current state, what's done, what remains, success criteria>", run_in_background=true)`.

- [ ] **Step 4: Update the task-shape rules (lines 51-56)**

Replace the four numbered rules with:

```markdown
   - **2–15 *different* tasks** (research X, scrape Y, summarize Z) → parallel `agent(…)` calls in ONE message; sync results, one reply.
   - **≥6 *similar* lookups** ("find emails for 20 salons") → ONE `agent(agent_type="general_purpose", run_in_background=true)` using `mcp_scraper_batch_search_google` / `mcp_scraper_batch_crawl` inside. **Never** spawn 20 agents.
2. **Long-running concrete action on ONE thing?** (>2 min) → `agent(…, run_in_background=true)`. ONE worker, brain stays free, Telegram push when done.
3. **Complex multi-step flow on ONE site?** → `agent(agent_type="browser", task=…)` (sync — result this turn).
4. **Research question needing reading + synthesis?** → `agent(agent_type="research", task=…)` or fan out several `agent(agent_type="explore", …)` calls.
```

- [ ] **Step 5: Update the base-tools list (line 77)**

Add `agent` as the FIRST name in the backticked list.

- [ ] **Step 6: Update decision-tree rows (lines 105-107) and the tool table (lines 140-151)**

Rows 10-12: replace `delegate(specialist="…")` with `agent(agent_type="…")` (same domain names).
Table: replace the `dispatch_subagents` / `run_background` / `delegate` rows with:

```markdown
- **`agent`** — THE dispatch tool. Sync by default: each call returns its agent's result in THIS turn; fire up to 15 in one message for a parallel fan-out (concurrency-throttled, don't hold back on genuinely independent tasks). `run_in_background=true` for slow work → task id now, ONE consolidated report later. Legacy `delegate`/`dispatch_subagents`/`run_background` still exist — do not use them for new work.

| Situation | Call |
|---|---|
| 1 long-running task | `agent(…, run_in_background=true)` |
| 2–15 *different* tasks, answers needed this turn | parallel `agent(…)` calls in ONE message |
| ≥6 SIMILAR lookups | **ONE** background `agent` using a `mcp_scraper_batch_*` tool inside |
| Need one domain expert's answer this turn | `agent(agent_type="browser"/"research"/"upwork"/…)` |
```

Also update line 12's example tool list to include `agent`, and line 13 stays (code work still goes through Goals — `agent` must NOT be used for code work; append to that line: `The same ban applies to `agent` — never dispatch code work through it.`).

- [ ] **Step 7: Verify no stale primary references remain**

Run: `grep -n "dispatch_subagents\|run_background\|delegate(specialist" personality/SOUL.md`
Expected: hits only in the legacy-mention line, the code-work ban (line 13), and the NEVER-claim rule (line 12).

- [ ] **Step 8: Commit**

```bash
git add personality/SOUL.md
git commit -m "docs(soul): dispatch section rewritten around unified agent tool"
```

---

### Task 8: Live verification + docs

**Files:**
- Modify: `DOCS.md` (add pattern entry), `CLAUDE.md` (one-line pointer in Runtime & Routing)

- [ ] **Step 1: Scoped test sweep (never full pytest — prod DB)**

Run: `python -m pytest tests/runtime/test_agent_tool.py tests/teams/test_specialist_aliases.py tests/teams/test_wildcard_allowlist.py tests/teams/test_new_builtin_specialists.py -v`
Expected: all PASS

- [ ] **Step 2: Deploy**

Run: `make rebuild`
Expected: container rebuilds and boots; check `docker logs` for `specialist self-check` — no warnings about `explore`/`general_purpose`/`"*"`.

- [ ] **Step 3: Live sync fan-out test (web chat)**

Send: *"Compare three things in parallel: (1) what's in my tasks list this week, (2) the latest note in lazybrain about MiniMax, (3) search the web for Univer sheets license."*
Verify: `/api/agents/status` (or web Activity page) shows 2-3 `lane="subagent"` rows live; the reply arrives in the SAME turn synthesizing all three; decrypt the turn's tool rows and `grep '\[truncated'` → no unmarked truncation.

- [ ] **Step 4: Live background test**

Send: *"In the background, scrape the top 5 HN stories and summarize them."*
Verify: instant "Background agent … started" acknowledgment; one consolidated follow-up reply on the same channel.

- [ ] **Step 5: Update DOCS.md + CLAUDE.md**

DOCS.md → Implementation Patterns Reference, add dated entry "Unified `agent` dispatch (2026-07-07)": one paragraph — skill file, sync semaphore, 15/6 caps, 12K result cap, wildcard allowlist, explore/general_purpose .md, alias-and-soak status.
CLAUDE.md → Runtime & Routing bullet list, ONE line: `- **Unified agent tool (2026-07-07)**: `agent(agent_type, task, run_in_background)` = Claude Code dispatcher pattern — sync parallel fan-out (≤15/turn, concurrency 6, 12K result cap) via run_specialist; bg path = TaskRunner consolidation; legacy delegate/dispatch_subagents/run_background demoted during soak. See DOCS.md.`

- [ ] **Step 6: Commit**

```bash
git add DOCS.md CLAUDE.md
git commit -m "docs: unified agent dispatch pattern notes"
```

- [ ] **Step 7: Soak flag decision (follow-up, NOT this branch)**

After ~1 week of prod soak: remove old three from `_BASE_TOOL_NAMES`, then plan the brain-forcing-machinery teardown as its own spec.
