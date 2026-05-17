# Dispatcher Unification Plan

**Cannot write the plan file** — my tool set in this session is Read/Grep/Glob only (no Write/Edit). The plan content below is what would have gone into `/Users/blckit/Desktop/Code_Projects/lazyclaw/docs/plans/dispatcher-unification.md`. Please save it manually or re-run with a write-capable harness.

## Executive Summary (200 words)

LazyClaw today has three overlapping delegation primitives — `run_background` (fire-and-forget bg agent), `dispatch_subagents` (fan-out 2-5 isolated workers), and `delegate` (named specialist, now also non-blocking after commit `01bcff9`). All three terminate on the same `background_done`/`background_failed` event bus, but they have separate skill classes, separate runtime wiring, separate concurrency caps, and three different ways to express "tool restriction". The brain often picks the wrong one, which is why `AUTO-PROMOTE` (4 separate forced-dispatch failsafes in `agent.py`) and `detect_inline_pivot` exist — they're scar tissue from this surface confusion.

The plan converges everything onto **ONE primitive: `task(description, subagent_type, prompt, tools=None)`** that mirrors Claude Code's Task tool. Multiple calls in one turn run in parallel via the existing `fanout_group_id` consolidator. The blocking variant of `delegate` disappears entirely; the inline-blocking pattern was already removed in commit `01bcff9`. `dispatcher.AgentDispatcher` becomes an internal implementation detail used by `task()`. The three legacy skill classes deprecate, then delete. AUTO-PROMOTE's hallucination-cap and text-only failsafes can be retired because the new contract eliminates the dodge surface; the 1-tool-then-force-bg nudge stays (it's about turn-length, not tool choice).

## Open Questions for the User (5)

1. **Hard cutover vs deprecation window?** A 2-week dual-primitive window costs brain confusion (two ways to do the same thing); a hard cutover means one big PR. Recommended: 2-week window with deprecation warnings + SOUL.md updated to ONLY mention `task`.

2. **Name of the unified primitive — `task`, `run`, or keep `run_background`?** Claude Code calls it `Task`. `run_background` is descriptive but implies "always async" — the new primitive subsumes inline-result use too. Recommended: `task`. (Back-compat alias `run_background → task` for one release.)

3. **Fold `delegate` away entirely, or keep specialist names as `subagent_type`?** Specialists carry useful system prompts (browser/research/code with rich domain instructions). Recommended: keep them as named `subagent_type` values — `"browser_specialist"`, `"research_specialist"`, `"code_specialist"`, plus the existing `"explore"`, `"general_purpose"`, `"specialist"` (custom tools). Three pre-built sub-agent types + custom.

4. **Tool-restriction interaction with `search_tools`?** If a sub-agent's `tools=["browser"]` is enforced, but the registry still exposes `search_tools` (a meta-tool that discovers more), the sub-agent can re-widen its allow-list at runtime. Two options: (a) `search_tools` is included by default in every sub-agent (lazyclaw "discover on demand" philosophy preserved) but the *executor* hard-filters tool calls against the original `tools=` list — discovery is allowed, execution isn't. (b) `tools=` is literally the full toolbox, no discovery. Recommended: (a). This preserves lazyclaw's identity.

5. **Single-call-with-`tasks=[...]` vs multiple `task()` calls per turn?** Claude Code uses multiple parallel calls (model emits N tool_use blocks in one assistant message). LazyClaw's `dispatch_subagents` already supports `tasks=[...]`. The brain-fanout group machinery in `task_runner` already handles BOTH (multiple `run_background` calls or one `dispatch_subagents`). Recommended: **multiple calls per turn, mirroring Claude Code** — drop the `tasks=[...]` parameter form. Easier for the brain, fewer parameters, native LLM behavior.

---

## Plan File Contents

```markdown
# Dispatcher Unification — Migration Plan
**Status**: Draft — pending user review
**Author**: planner agent (read-only analysis)
**Target branch**: feat/claude-agent-sdk
**Last updated**: 2026-05-17

## 1. Current-State Inventory

### 1.1 The three primitives at a glance

| Aspect | `run_background` | `dispatch_subagents` | `delegate` |
|---|---|---|---|
| Skill class | `RunBackgroundSkill` in `lazyclaw/skills/builtin/background.py` | `DispatchSubagentsSkill` in `lazyclaw/skills/builtin/dispatch.py` | `DelegateSkill` in `lazyclaw/skills/builtin/delegate.py` |
| Runtime | `TaskRunner.submit()` in `lazyclaw/runtime/task_runner.py:290` | `AgentDispatcher.submit_async()` in `lazyclaw/runtime/dispatcher.py:223` | `run_specialist()` in `lazyclaw/teams/runner.py:169` (via fire-and-forget asyncio.create_task — `lazyclaw/skills/builtin/delegate.py:380`) |
| Params | `instruction`, `name?`, `project_tag?` | `tasks=[{type, task, tool_names?, timeout?}]` (2-5 items) | `specialist`, `instruction`, `project_tag?`, `goal_id?` |
| Spawn shape | One independent `Agent.process_message` (full brain loop) | N parallel `run_specialist` (worker model loop) | One `run_specialist` (worker model loop) |
| Wait? | Async — returns task_id immediately | Async — returns N task_ids immediately | Async since commit `01bcff9` — returns "Started …" immediately |
| Tool restriction | None — sub-agent inherits FULL registry | `tool_names` parameter on each subtask (Specialist type required) | Hard-coded `SpecialistConfig.allowed_skills` (per built-in spec) |
| System prompt | None (uses default SOUL.md) | Three hardcoded: `_EXPLORE_SYSTEM_PROMPT`, `_GENERAL_PURPOSE_SYSTEM_PROMPT`, `_SPECIALIST_SYSTEM_PROMPT` (dispatcher.py:82-141) | Three hardcoded: BROWSER_SPECIALIST / CODE_SPECIALIST / RESEARCH_SPECIALIST (specialist.py:105-275) |
| Depth tracking | `caller_depth` threaded through `submit()` → `_execute` → inner Agent (`task_runner.py:282, 626-635`); MAX_TASK_DEPTH=2 (`task_runner.py:187`) | `_IS_SUBAGENT` contextvar (`dispatcher.py:44`) — single-depth only; nested calls return "single-depth limit enforced" (`dispatch.py:172`) | No depth tracking (specialists don't have `task_runner` plumbed, so they can't recurse via `run_background` either) |
| Concurrency cap | Per-user: 10, global: 10 (`task_runner.py:175-176`) | `LAZYCLAW_DISPATCH_CONCURRENCY`, default 4 (`dispatcher.py:53-62`) | Implicit via `max_concurrent_specialists` agent setting; no semaphore in delegate itself |
| Result delivery | `_execute()` fires `background_done`/`background_failed` on the callback + `task_event_bus` (`task_runner.py:786-820`). When `source="brain"` + `fanout_group_id`, results bucketed into `_BrainFanoutGroup` and ONE synthetic consolidation turn is enqueued on the lane queue when last sibling settles (`task_runner._consolidate`, line 1010) | `_run_and_publish()` fires same `background_done`/`background_failed` events on the bus (`dispatcher.py:267-282`) — **does NOT route through fanout consolidator**; brain absorbs as side-notes on next turn | Fire-and-forget `_run_delegate_bg()` fires `specialist_done` + `background_done` events on the callback (`delegate.py:341-378`) — **does NOT route through fanout consolidator**; brain absorbs as side-note on next turn |
| TeamLead integration | Yes — `register(... lane="background")` (`task_runner.py:427-434`) | Yes — `register(... lane="subagent")` (`dispatcher.py:312-326`) | Yes — `register(... lane="specialist")` (`delegate.py:175-186`) |
| LazyBrain mirror | Yes — `_mirror_background_result` (`task_runner.py:68-143`) | **No** | **No** |
| Workspace folder | Yes — `code_workspace_dir(...)` (`task_runner.py:363-377`, only relevant for Code Specialist runs) | **No** | Yes (only for code specialist via `run_specialist` itself) |
| Three-strikes / stuck handling | Stuck detection happens INSIDE the inner Agent (`agent.py`) | Stuck detection happens inside `run_specialist` (`teams/runner.py:481+`) | Stuck detection happens inside `run_specialist` |
| Brain consolidation | **Yes** — `task_runner._consolidate` enqueues ONE synthetic brain turn (`task_runner.py:1010-1129`) | **No** — silent on chat WS (`SilentSubagentCallback`, dispatcher.py:333-337); results land as bus side-notes only | **Partial** — events fire on callback; relies on chat_ws subagent-terminal pump to absorb as side-note on brain's next turn |

### 1.2 Per-primitive surface notes

**`run_background` — `lazyclaw/skills/builtin/background.py`** (158 lines)

Signature (skill side): `execute(user_id, params={"instruction", "name"?, "project_tag"?})` returns string "Background task 'X' started…".

Internal flow:
1. `RunBackgroundSkill._task_runner` injected per-turn in `agent.py:1842` along with `_caller_depth` (`agent.py:1843`) and `_fanout_group_id` minted fresh per turn (`agent.py:1835`).
2. Calls `TaskRunner.submit(user_id, instruction, name, callback, source="brain", fanout_group_id=..., chat_session_id, project_tag, caller_depth, goal_id)`.
3. `submit()` validates depth/concurrency, encrypts instruction, INSERTs `background_tasks` row, creates workspace dir, spawns `_execute()` task, registers with TeamLead, publishes `background_started` event.
4. `_execute()` creates fresh `Agent`, runs `process_message()`, captures `WorkSummary`, on completion:
   - Persists encrypted result + cost stats + transcript JSON + files_touched JSON to DB
   - Mirrors result into LazyBrain via `_mirror_background_result`
   - If `_is_brain_fanout`: records into `_BrainFanoutGroup`; ELSE: fires `background_done` callback + bus event
   - Cleans up provenance maps
5. When last sibling in fan-out group settles → `_consolidate()` enqueues synthetic `"[Background fan-out complete — N tasks finished]" + per-task results` instruction onto lane queue. The brain then writes ONE consolidated reply.

**`dispatch_subagents` — `lazyclaw/skills/builtin/dispatch.py` + `lazyclaw/runtime/dispatcher.py`** (274 + 498 lines)

Signature (skill side): `execute(user_id, params={"tasks": [{type, task, tool_names?, timeout?}]})` returns string "Dispatched N subagents…".

Internal flow:
1. Validates 2-5 tasks, type ∈ {explore, general_purpose, specialist}, requires `tool_names` for specialist type.
2. Single-depth check via `_IS_SUBAGENT.get()` (`dispatch.py:171`).
3. Builds `[SubagentConfig]` and calls `AgentDispatcher.submit_async(configs, user_id)`.
4. `submit_async` spawns N `asyncio.create_task(_run_and_publish(cfg, user_id, task_id))` (`dispatcher.py:245-250`), pins them in `_BACKGROUND_SUBAGENTS` so GC doesn't collect, returns task_ids immediately.
5. Each `_run_and_publish` → `_run_subagent` → `run_specialist(...)` with `SilentSubagentCallback` (`dispatcher.py:333`). `_IS_SUBAGENT.set(True)` blocks recursive dispatch inside the sub-agent.
6. `_make_specialist(cfg)` builds a `SpecialistConfig` per type:
   - EXPLORE: `_EXPLORE_TOOLS` + every connected mcp-scraper tool (dispatcher.py:436-464)
   - GENERAL_PURPOSE: all registered tools minus `_GENERAL_PURPOSE_EXCLUDED = {dispatch_subagents, delegate}` (dispatcher.py:466-483)
   - SPECIALIST: caller's `tool_names` (dispatcher.py:486-497)
7. Terminal event `background_done` fires on `task_event_bus`. **No callback fire on the user's chat callback** — explicit design choice. Brain learns results via bus side-notes on next turn.

**`delegate` — `lazyclaw/skills/builtin/delegate.py`** (460 lines)

Signature (skill side): `execute(user_id, params={"specialist", "instruction", "project_tag"?, "goal_id"?})` returns string "Started X in the background…".

Internal flow (post commit `01bcff9`):
1. `_SPECIALIST_MAP` resolves short name → BROWSER_SPECIALIST / RESEARCH_SPECIALIST / CODE_SPECIALIST (delegate.py:39-43).
2. `_get_cached_site_knowledge` opportunistically enriches the instruction with prior site memory (browser specialist only, delegate.py:155-163).
3. Registers task with TeamLead under `lane="specialist"`.
4. Wraps callback in `StepTrackingCallback` so per-tool events drive TeamLead step updates (delegate.py:213-217).
5. **Fire-and-forget**: `_run_delegate_bg()` scheduled as `asyncio.create_task` and pinned in module-level `_background_tasks` (delegate.py:380-385). Returns "Started …" immediately (delegate.py:390-395).
6. Inside `_run_delegate_bg`: awaits `run_specialist(...)`, handles browser-learning save (delegate.py:282-295), calls `team_lead.complete/fail`, fires `specialist_done` + `background_done`/`background_failed` events on the callback (delegate.py:340-378).

**Key behavioural difference**: `delegate` fires `background_done` ON THE CALLBACK directly (chat_ws will pump it to web UI as a bg-card), whereas `dispatch_subagents` fires on the EVENT BUS only (silent on chat WS, brain absorbs as side-note). `run_background` fires BOTH (callback + bus).

### 1.3 Specialist registry (`lazyclaw/teams/specialist.py`)

3 built-in specialists, 3 user-customizable. Each has:
- `name`, `display_name`, `system_prompt`
- `allowed_skills: tuple[str, ...]`
- `preferred_model: "brain" | "worker" | None`
- `include_scraper: bool` (auto-unions every mcp-scraper tool)
- `is_builtin: bool`

User-defined specialists stored encrypted in `specialists` table (specialist.py:292-423). CRUD: `save_specialist`, `load_specialists`, `get_specialist`, `delete_specialist`.

### 1.4 Specialist runner (`lazyclaw/teams/runner.py`, 536 lines)

`run_specialist(user_id, specialist, task, registry, eco_router, permission_checker, callback?, cancel_token?, tab_context?, project_tag?, goal_id?, task_id?)`:
- Filters tools via `_filter_tools(registry, allowed, include_scraper)` (teams/runner.py:135-166)
- Builds system prompt = `specialist.system_prompt + workspace_hint + task`
- Loops `MAX_ITERATIONS=200` (effective stop via stuck detector around iter 30 nudge)
- Uses `ROLE_WORKER` via `eco_router.chat()` (teams/runner.py:364)
- Returns `SpecialistResult` with `result`, `tools_used`, `transcript`, `workspace_dir`, `files_touched`, `prompt_sent`, `success`, `error`

### 1.5 Call-site count (excluding the skill classes themselves and tests)

Internal `task_runner.submit()` callers in `agent.py`:
- `agent.py:1607` — compound-task splitter (multi-task user message gets split + each dispatched)
- `agent.py:3582` — AUTO-PROMOTE failsafe (hallucination-cap path) — synthesizes a submit when brain loops on suppressed tool
- `agent.py:3716` — AUTO-PROMOTE failsafe (text-only path) — brain returned text instead of dispatching
- `agent.py:4017` — fast-dispatch (specialist tool detected, auto-routes to bg)
- `agent.py:4924` — AUTO-PROMOTE failsafe (iteration-passed path) — brain refused run_background even with narrowed tool list

External `task_runner.submit()` callers:
- `lazyclaw/skills/builtin/survival/gig_skill.py:105` — gig work submission

External `run_specialist()` direct callers:
- `lazyclaw/skills/builtin/delegate.py:236` (now wrapped in fire-and-forget)
- `lazyclaw/teams/executor.py:70` (parallel team execution — independent of these 3 primitives)
- `lazyclaw/runtime/dispatcher.py:342`

External `AgentDispatcher` constructors:
- `lazyclaw/skills/builtin/dispatch.py:246` (only in-tree caller — the skill itself)

External skill name occurrences in non-test code:
- `lazyclaw/runtime/agent.py` — many (see §1.6)
- `lazyclaw/runtime/personality.py:83` — SOUL.md mentions `run_background`
- `lazyclaw/runtime/taor.py:242` — plan prompt mentions `delegate(specialist="research")`
- `lazyclaw/mcp/manager.py:89` — `run_background(instruction="use claude-code to …")` hint
- `lazyclaw/skills/builtin/background_status.py:4` — docstring reference
- `lazyclaw/runtime/stuck_detector.py:33-34` — batch-op prefix awareness mentions both names

### 1.6 Runtime wiring in `agent.py`

Three skill classes are re-registered PER TURN (`process_message`, line 1796-1844):
- `DelegateSkill` injected with `(config, registry, eco_router, permission_checker, callback=cb, team_lead)`
- `DispatchSubagentsSkill` injected with same `(callback, team_lead)`
- `RunBackgroundSkill` re-instantiated with `(config, callback, fanout_group_id, chat_session_id)`, then `_task_runner` and `_caller_depth` set on instance after construction (the "set after construct" pattern is brittle — see §4.5).

Base tool set always includes all three (`_BASE_TOOL_NAMES` at agent.py:127-132). Local-model tool set (`_LOCAL_TOOL_NAMES` at line 136-138) only includes `delegate` — `run_background` and `dispatch_subagents` are dropped for small models. This is an asymmetry the unification must preserve or remove deliberately.

The brain's mid-turn AUTO-PROMOTE machinery in `agent.py:2876-2914`:
- `_PROMOTE_BG_AT_ITER = 1` — when foreground iter ≥ 1 and `run_background` not yet called and not pure read-only, set `_force_dispatch_only = True`
- Next iter: tool list HARD-narrowed to ONLY `run_background` (agent.py:3115-3135)
- If brain STILL doesn't call it: hallucination-cap / text-only / iteration-passed failsafes synthesize the call (agent.py:3548-3635, 3673-3756, 4907-4949)
- Once `run_background` actually fires: hard return "Continuing in background — will report back when done." (agent.py:4493-4514)

This is ~250 lines of scar tissue. Six places in `agent.py` reach for `_task_runner.submit()` because the brain has too many ways to dodge dispatching. Under the unified primitive this collapses to ONE — the primitive itself.

The `detect_inline_pivot` machinery at `agent.py:4994` fires a system nudge when the brain runs ≥5 same-shape inline tool calls in one turn ("brain dispatches, workers execute" message at 5006-5022). Stays useful post-unification — same nudge, just points at `task(...)` instead of `dispatch_subagents` + `run_background`.

### 1.7 Event flow (chat_ws side)

`/ws/chat` (`lazyclaw/gateway/routes/chat_ws.py`):
- Per-user `task_event_bus.subscribe(user.id)` pump (chat_ws.py:584-635) forwards `background_done`/`background_failed` events as `{type:"browser_event"}`-like frames.
- `bg_streaming` toggle (Fix J) suppresses intermediate `bg_*` frames; final terminal events always pass through (chat_ws.py:89-90).
- TeamLead lifecycle events bridged into the bus via `_wire_team_lead_to_event_bus(team_lead, _task_bus)` in cli.py:280.

All three primitives feed this surface; the unified `task` primitive must preserve the same frame kinds (`background_started`, `background_done`, `background_failed`) so no Web UI work is needed.

---

## 2. Target Design

### 2.1 The primitive

```python
# Skill signature (visible to the brain)
task(
    description: str,        # short label — appears in UI / TeamLead
    subagent_type: str,      # "general_purpose" | "explore" | "browser_specialist"
                             # | "research_specialist" | "code_specialist" | "specialist"
    prompt: str,             # full instruction for the sub-agent (self-contained)
    tools: list[str] | None = None,  # restrict the sub-agent's toolbox; None = inherit
    timeout: int = 300,      # seconds
    project_tag: str = "",   # for Code Specialist UI / workspace folder
    goal_id: str = "",       # for Goal Executor surface
) -> str  # returns "Task <task_id_short> started (description: …)"
```

Returns the task_id immediately; the actual result lands either as a fanout-consolidated brain turn (when multiple `task()` calls fired in the same TAOR turn) OR as a `background_done` event side-note on the brain's next turn (when only one fired).

### 2.2 Parallel-batch semantics

**Recommendation: multiple `task()` calls per turn, mirroring Claude Code.** The brain emits N `tool_use` blocks in one assistant message; the existing `fanout_group_id` machinery in `task_runner` already buckets them and produces ONE consolidation turn.

No new `tasks=[...]` array parameter. This eliminates one API shape, halves the schema, and aligns with native LLM behavior.

### 2.3 Wait/non-wait semantics

**Recommendation: always non-blocking.** All three legacy primitives are already non-blocking after commit `01bcff9`. There is no remaining caller that needs inline-blocking semantics:
- `delegate` used to block; the bug is the very thing `01bcff9` fixed.
- "I need the merged answer in this turn" is misleading guidance — even when `delegate` blocked, it took 30-60s; the user had a frozen chat. The fan-out consolidation turn pattern is strictly better UX.

If a caller in the future needs inline result merging (e.g. a future Goal Executor that needs the sub-result to compose its next step), add `await=True` as an opt-in flag — but ship without it. YAGNI.

### 2.4 Tool-restriction interaction with `search_tools`

**Recommendation: tools= is the "allow-list at execution time". `search_tools` is always available for discovery, but discovered tools fail at execution if they're not in the allow-list.**

Implementation:
- Sub-agent receives `tools = allowlist | {"search_tools", "recall_memories", "save_memory"}` at registration time (these three are always permitted; they're meta-tools and memory).
- `ToolExecutor.execute()` gains an `allowed: set[str] | None` filter. When set, calls outside the set return `Error: Tool 'X' is not in this sub-agent's allow-list. Available: …`.
- This preserves lazyclaw's "discover on demand" philosophy AND the Claude Code restriction guarantee.

When `tools=None` (default), the sub-agent inherits the parent's full registry — current `run_background` behavior, preserved.

### 2.5 `subagent_type` values

Six built-in types, mapping cleanly onto existing specialists:

| `subagent_type` | Effective allow-list | System prompt | Model | Notes |
|---|---|---|---|---|
| `general_purpose` (default) | all tools minus `task` (prevents recursion past MAX_DEPTH) | SOUL.md (inherits parent) | brain | Same as today's `run_background` |
| `explore` | `_EXPLORE_TOOLS` ∪ scraper tools | `_EXPLORE_SYSTEM_PROMPT` (dispatcher.py:82) | worker | Same as today's `dispatch_subagents type=explore` |
| `browser_specialist` | `BROWSER_SPECIALIST.allowed_skills` ∪ scraper | `BROWSER_SPECIALIST.system_prompt` | worker | Same as today's `delegate(specialist="browser")` |
| `research_specialist` | `RESEARCH_SPECIALIST.allowed_skills` ∪ scraper | `RESEARCH_SPECIALIST.system_prompt` | worker | Same as today's `delegate(specialist="research")` |
| `code_specialist` | `CODE_SPECIALIST.allowed_skills` | `CODE_SPECIALIST.system_prompt` | None (resolves) | Same as today's `delegate(specialist="code")` + workspace_dir wiring |
| `specialist` (custom) | caller's `tools=[...]` (REQUIRED for this type) | `_SPECIALIST_SYSTEM_PROMPT` | worker | Same as today's `dispatch_subagents type=specialist` |

Plus user-defined specialists from the `specialists` table are addressable by name (e.g. `subagent_type="my_research"`).

### 2.6 Result shape

Streaming events + final terminal event, identical to today:
- `background_started` (TaskEvent) — fired in `submit()`
- `tool_call` / `tool_result` (AgentEvent) — fired by inner agent, tagged with `bg_task_id`/`bg_task_name`
- `background_done` OR `background_failed` (terminal AgentEvent + TaskEvent)
- `bg_streaming` toggle (Fix J) controls whether intermediate events go to chat WS; terminal always does.

Brain consolidation: when N>1 `task()` calls share a `fanout_group_id` in one TAOR turn, the existing `_consolidate()` path produces ONE synthetic instruction the brain absorbs (`task_runner.py:1010`).

### 2.7 Depth tracking

Preserve MAX_TASK_DEPTH=2 from `task_runner.py:187`. The new `task()` skill threads `_caller_depth` exactly the way `RunBackgroundSkill` does today. `_IS_SUBAGENT` contextvar from `dispatcher.py:44` is REMOVED — depth check at `submit()` is the single source of truth.

### 2.8 What disappears

| Symbol | File | Disposition |
|---|---|---|
| `RunBackgroundSkill` | `lazyclaw/skills/builtin/background.py` | Delete (after deprecation window) |
| `DispatchSubagentsSkill` | `lazyclaw/skills/builtin/dispatch.py` | Delete |
| `DelegateSkill` | `lazyclaw/skills/builtin/delegate.py` | Delete (site-knowledge enrichment moves into `TaskSkill._enrich_browser_instruction`) |
| `AgentDispatcher.submit_async` | `lazyclaw/runtime/dispatcher.py:223` | Delete (no callers post-migration) |
| `AgentDispatcher.dispatch` (sync) | `lazyclaw/runtime/dispatcher.py:194` | Delete |
| `AgentDispatcher._run_and_publish` | `lazyclaw/runtime/dispatcher.py:257` | Delete |
| `AgentDispatcher` class itself | `lazyclaw/runtime/dispatcher.py:166` | Delete or collapse into `TaskRunner` |
| `_IS_SUBAGENT` contextvar | `lazyclaw/runtime/dispatcher.py:44` | Delete |
| `_BACKGROUND_SUBAGENTS` pin set | `lazyclaw/runtime/dispatcher.py:38` | Delete |
| `_force_dispatch_only` + `_promote_iter` | `lazyclaw/runtime/agent.py:2908-2914` | Keep (still useful for iteration-budget enforcement) but simplify — see §2.9 |
| AUTO-PROMOTE hallucination-cap failsafe | `lazyclaw/runtime/agent.py:3548-3635` | Delete (the brain can no longer dodge — there's only `task`) |
| AUTO-PROMOTE text-only failsafe | `lazyclaw/runtime/agent.py:3673-3756` | Keep, simplified (handles "brain returned text instead of any tool call" — independent of which primitive) |
| AUTO-PROMOTE iteration-passed failsafe | `lazyclaw/runtime/agent.py:4907-4949` | Keep, simplified |
| 1-tool-then-force-bg nudge | `lazyclaw/runtime/agent.py:4863-4900` | Keep — it's about turn-length, not tool choice; reword to mention `task(...)` |
| Hard-stop on AUTO-PROMOTE done | `lazyclaw/runtime/agent.py:4481-4514` | Keep (rename) |
| Delegate fast-path (parallel call skip) | `lazyclaw/runtime/agent.py:4069-4094` | Delete — `task` is always non-blocking; brain can mix `task` with read-only tool calls |

### 2.9 What stays (and why)

- **`fanout_group_id` + `_BrainFanoutGroup` + `_consolidate`** — this is the architectural win that makes "N parallel calls → one consolidated brain reply" work. Stays exactly as-is; `task` skill plugs into it the same way `RunBackgroundSkill` does today.
- **`TaskRunner.submit` signature** — extends with `subagent_type` + `tools` parameters, otherwise unchanged. Backwards compatible.
- **`run_specialist`** — the worker-loop implementation. `TaskRunner._execute` already chooses `Agent.process_message` (full brain loop) for `general_purpose`-shaped tasks; under the new design, `_execute` branches on `subagent_type`: full Agent for `general_purpose` (current path), `run_specialist` for everything else.
- **`SpecialistConfig`** — the data shape that carries system_prompt + allow-list. The `task` skill internally builds a `SpecialistConfig` from `(subagent_type, tools)` and passes it through.
- **All 3 built-in specialists** — addressable as `subagent_type` names.
- **TeamLead, LazyBrain mirror, workspace_dir, project_tag/goal_id** — all preserved.
- **`bg_streaming` toggle** — preserved exactly.
- **Event bus / chat_ws pump / Telegram consolidator** — preserved exactly.

---

## 3. Migration Plan (dependency-ordered, reversible)

### Phase 1: Add the unified primitive alongside existing ones

#### Step 1.1: Extend `TaskRunner.submit` with `subagent_type` and `tools`
- **Files**: `lazyclaw/runtime/task_runner.py`
- **Action**: Add `subagent_type: str = "general_purpose"` and `tools: tuple[str, ...] | None = None` parameters to `submit()` (line 290). Store on `self._task_subagent_type[task_id]` and `self._task_tools[task_id]`.
- **In `_execute()`**: branch on `subagent_type`. For `general_purpose` (default), preserve current behavior (`Agent(...)`). For any other type, resolve to a `SpecialistConfig` via a new helper `_resolve_subagent_type(subagent_type, tools, registry, eco_router) → SpecialistConfig` and call `run_specialist(...)` instead of `Agent.process_message()`. Wrap `run_specialist` so its `SpecialistResult` becomes the same `result` string and `WorkSummary` shape `_execute` expects today.
- **Why**: Atomic — adds capability without removing anything.
- **Dependencies**: None.
- **Risk**: Medium. The `Agent.process_message` vs `run_specialist` branch is the trickiest moment. Both must produce identical event streams + final result string for the existing `_execute` post-processing to work.
- **Rollback**: Revert `task_runner.py` — no other file touched.

#### Step 1.2: Implement `_resolve_subagent_type` helper
- **Files**: New `lazyclaw/runtime/subagent_types.py` (or in `task_runner.py`)
- **Action**: Lookup table:
  - `"general_purpose"` → None (means: use full Agent loop, not run_specialist)
  - `"explore"` → `SpecialistConfig(name="explore", system_prompt=_EXPLORE_SYSTEM_PROMPT, allowed_skills=_EXPLORE_TOOLS, preferred_model="worker", include_scraper=True)` (lifted from dispatcher.py)
  - `"browser_specialist"`, `"research_specialist"`, `"code_specialist"` → existing specs from `teams/specialist.py`
  - `"specialist"` → `SpecialistConfig(name="custom_specialist", system_prompt=_SPECIALIST_SYSTEM_PROMPT, allowed_skills=tuple(tools), preferred_model="worker", include_scraper=False)` — REQUIRES `tools=[...]`
  - Anything else → look up in user's `specialists` table via `get_specialist(config, user_id, subagent_type)`
- **Tools override**: If `tools is not None`, replace `allowed_skills` with caller-provided list (preserving system_prompt).
- **Tests**: `tests/runtime/test_subagent_types.py` — assert each value resolves to the right spec; assert tools= override works; assert custom specialist lookup works.
- **Dependencies**: 1.1.
- **Risk**: Low.

#### Step 1.3: Implement `TaskSkill` (the new unified primitive)
- **Files**: New `lazyclaw/skills/builtin/task_skill.py`
- **Action**:
  - Inherits `BaseSkill`. Name = `"task"`. Display = `"Task"`. Category = `"orchestration"`.
  - Description: copy/merge from existing three, emphasize "ONE primitive for all delegated work". Include parallel-fan-out usage example.
  - Schema: `{description, subagent_type, prompt, tools?, timeout?, project_tag?, goal_id?}`. Enum for `subagent_type` lists the 6 built-ins.
  - `execute()` calls `self._task_runner.submit(user_id, instruction=prompt, name=description, subagent_type=subagent_type, tools=tuple(tools) if tools else None, timeout=timeout, source="brain", fanout_group_id=self._fanout_group_id, chat_session_id=self._chat_session_id, project_tag=project_tag, caller_depth=self._caller_depth, goal_id=goal_id)`.
  - Constructor takes the same deps as `RunBackgroundSkill` (config, callback, fanout_group_id, chat_session_id). `_task_runner` and `_caller_depth` settable on instance for back-compat with current registration pattern.
- **Tests**: `tests/skills/test_task_skill.py` — schema validation, parameter passing to `TaskRunner.submit`, default values.
- **Dependencies**: 1.1, 1.2.
- **Risk**: Low.

#### Step 1.4: Register `TaskSkill` in `Agent.process_message`
- **Files**: `lazyclaw/runtime/agent.py` (around line 1796-1844)
- **Action**: Inside the per-turn skill registration block, add:
  ```python
  from lazyclaw.skills.builtin.task_skill import TaskSkill
  task_skill = TaskSkill(
      config=self.config,
      callback=cb,
      fanout_group_id=_bg_fanout_group_id,  # same group as run_background
      chat_session_id=chat_session_id,
  )
  task_skill._task_runner = self._task_runner
  task_skill._caller_depth = self._depth
  self.registry.register(task_skill)
  ```
  Add `"task"` to `_BASE_TOOL_NAMES` (line 128). Add `"task"` to `_LOCAL_TOOL_NAMES` (line 137) — local models also get it.
- **Why**: Brain can now SEE the new primitive. Legacy three still registered.
- **Dependencies**: 1.3.
- **Risk**: Low — additive.

#### Step 1.5: Wire `_BrainFanoutGroup` to share group across `task` and `run_background`
- **Files**: `lazyclaw/runtime/agent.py:1835`, `lazyclaw/skills/builtin/task_skill.py`
- **Action**: BOTH skills receive the same `_bg_fanout_group_id` so calls of mixed shape (`task` + `run_background` in one turn) still bucket together. Acceptable because both primitives have the same downstream semantics.
- **Why**: Smooths the dual-primitive phase — if the brain mixes the two during migration, consolidation still works.
- **Tests**: Extend `tests/test_brain_fanout_consolidation.py` with a mixed-call test.
- **Risk**: Low.

### Phase 2: Parity tests

#### Step 2.1: Parity tests for `task` ↔ `run_background`
- **Files**: New `tests/test_task_skill_parity.py`
- **Action**: For each call shape that `run_background` accepts, assert `task(subagent_type="general_purpose", …)` produces the same `TaskRunner.submit` call, same DB row, same callback events, same fanout consolidation.
- **Risk**: Low.

#### Step 2.2: Parity tests for `task` ↔ `dispatch_subagents`
- **Files**: New `tests/test_task_skill_dispatch_parity.py`
- **Action**: For each `(type, task, tool_names)` triple in dispatch_subagents, assert N parallel `task(subagent_type=type, prompt=task, tools=tool_names)` calls produce equivalent specialist runs, same TeamLead `lane="subagent"`-equivalent register, same terminal events. Note: under unification, `lane` value standardizes to one — pick `"background"` (the broader name); update any UI filters in `web/src/pages/Activity.tsx` to accept it.
- **Risk**: Medium — TeamLead `lane` field is a public UI concept. Document the change in CLAUDE.md.

#### Step 2.3: Parity tests for `task` ↔ `delegate`
- **Files**: New `tests/test_task_skill_delegate_parity.py`
- **Action**: Assert `task(subagent_type="browser_specialist", prompt="…")` produces same behavior as `delegate(specialist="browser", instruction="…")`. Include the site-knowledge enrichment branch (delegate.py:155-163) — move that logic into `TaskSkill._enrich_browser_instruction` or into `_resolve_subagent_type`.
- **Risk**: Low — pure refactor.

#### Step 2.4: Tool-restriction tests
- **Files**: New `tests/test_task_skill_tool_restriction.py`
- **Action**:
  1. `task(subagent_type="general_purpose", tools=["browser"])` — sub-agent can call browser, gets `Error: Tool 'X' is not in this sub-agent's allow-list` on anything else.
  2. `task(...)` with `tools=None` — sub-agent has full registry (current `run_background` behavior).
  3. `task(...)` with `tools=["browser"]` AND brain calls `search_tools`: search_tools IS allowed (it's always-on); but executing any tool not in `["browser", "search_tools", "recall_memories", "save_memory"]` returns the error.
  4. MAX_TASK_DEPTH=2 — nested `task()` at depth 2 raises clean error.
- **Risk**: Medium — depends on `ToolExecutor.execute` gaining the `allowed` filter param (Step 1.1 substep).

#### Step 2.5: Migrate existing tests
- **Files**: `tests/test_dispatcher_concurrency.py`, `tests/test_dispatch_upfront.py`, `tests/test_specialist_scraper_visibility.py`, `tests/test_brain_fanout_consolidation.py`, `tests/runtime/test_agent_force_dispatch.py`, `tests/test_auto_promote_meta_question_guard.py`, `tests/test_heartbeat_background_lane.py`
- **Action**: For each test that exercises `dispatch_subagents`/`delegate`/`run_background` directly, ADD a parallel test that exercises `task(...)`. Keep both green during the deprecation window.
- **Risk**: Low.

### Phase 3: Migrate internal callers

#### Step 3.1: Migrate `agent.py` internal `task_runner.submit` callers
- **Files**: `lazyclaw/runtime/agent.py:1607, 3582, 3716, 4017, 4924`
- **Action**: No actual call changes needed — these already call `_task_runner.submit()` directly. But the surrounding AUTO-PROMOTE prose changes from "must call run_background" to "must call task(...)" so brain sees a consistent vocabulary.
- **Risk**: Low.

#### Step 3.2: Migrate `gig_skill.py`
- **Files**: `lazyclaw/skills/builtin/survival/gig_skill.py:105`
- **Action**: Update call to pass `subagent_type="general_purpose"` (no-op default) — leave for clarity.
- **Risk**: Low.

#### Step 3.3: Migrate `contract_intake_executor.py` and `goal_executor.py`
- **Files**: `lazyclaw/runtime/contract_intake_executor.py`, `lazyclaw/runtime/goal_executor.py`
- **Action**: These don't currently call delegate/dispatch_subagents/run_background directly — they invoke the brain via `lane_queue.enqueue(...)`. No code changes. Audit instruction strings: any prose that says "use delegate to …" → "use task(subagent_type=…) to …".
- **Risk**: Low.

#### Step 3.4: Migrate heartbeat callers
- **Files**: `lazyclaw/heartbeat/daemon.py`
- **Action**: Heartbeat already goes through `lane_queue.enqueue`. Verify no direct dispatch calls. Confirmed via grep — no changes.
- **Risk**: None.

### Phase 4: Brain-facing migration (the dual-primitive window)

#### Step 4.1: Update SOUL.md
- **Files**: `lazyclaw/runtime/personality.py:83` and the SOUL.md markdown blob it loads
- **Action**: Replace all mentions of `run_background`, `dispatch_subagents`, `delegate` with the single `task(...)` primitive. Include the parallel-call example: "fire N task() calls in one turn → brain consolidates results in next turn."
- **Risk**: Medium — SOUL.md is the brain's primary behavioral document. Test by running the existing brain-fanout consolidation test suite + a smoke test that the brain still consolidates correctly.

#### Step 4.2: Update plan-mode prompts (`taor.py`)
- **Files**: `lazyclaw/runtime/taor.py:242`
- **Action**: Replace `delegate(specialist="research")` reference in the user-facing plan prompt with `task(subagent_type="research_specialist")`.
- **Tests**: Update `tests/test_dispatch_upfront.py` to assert the new name.
- **Risk**: Low.

#### Step 4.3: Update plan-gate auto-bg-plan prompt
- **Files**: `lazyclaw/runtime/agent.py:1404-1405`
- **Action**: Replace `run_background(instruction="…")` with `task(subagent_type="general_purpose", description="…", prompt="…")`.
- **Risk**: Low — but this is one of the critical paths the brain follows when the plan-gate fires.

#### Step 4.4: Update mcp/manager.py hint
- **Files**: `lazyclaw/mcp/manager.py:89`
- **Action**: `run_background(instruction="use claude-code to …")` → `task(subagent_type="code_specialist", description="…", prompt="…")`.
- **Risk**: Low.

#### Step 4.5: Add deprecation warnings to legacy three
- **Files**: `lazyclaw/skills/builtin/background.py`, `lazyclaw/skills/builtin/dispatch.py`, `lazyclaw/skills/builtin/delegate.py`
- **Action**: In each `execute()`, log a `WARNING` once per session: `"X is deprecated; use task(subagent_type=Y, …) instead."` Brain still sees the description (so it CAN call it) but the description gains a "DEPRECATED — use `task` instead" header.
- **Why**: 2-week deprecation window. New brain behavior is to call `task`; if it slips, telemetry shows it.
- **Risk**: Low.

#### Step 4.6: Update stuck_detector references
- **Files**: `lazyclaw/runtime/stuck_detector.py:33-34, 273, 416`
- **Action**: Comments / messages mentioning the three names → `task`.
- **Risk**: None (cosmetic).

### Phase 5: Remove legacy primitives

(Execute only after at least 2 weeks of dual-primitive running with telemetry showing brain reliably calls `task`.)

#### Step 5.1: Unregister legacy skills in `Agent.process_message`
- **Files**: `lazyclaw/runtime/agent.py:1796-1844, 4794-4796, 5268-5272, 5571-5574`
- **Action**: Remove `DelegateSkill`, `DispatchSubagentsSkill`, `RunBackgroundSkill` re-registration blocks. Remove unregister cleanups. Remove `_delegate_registered` / `_dispatch_registered` flags.
- **Risk**: Medium — brain still might emit a `delegate(...)` call if a long conversation primed it. Fallback: keep an alias in the skill registry that translates `delegate/dispatch_subagents/run_background` → `task` for one more release.

#### Step 5.2: Delete the AUTO-PROMOTE hallucination-cap failsafe
- **Files**: `lazyclaw/runtime/agent.py:3548-3635`
- **Action**: Delete the block. Under the new contract, the brain only has ONE delegation primitive — there's no "suppressed tool" to loop on.
- **Risk**: Medium. The 1-tool-then-force-bg nudge still narrows tools to ONLY `task` (formerly `run_background`); if the brain hallucinates an alternative, the existing `_HALLUC_MAX_RETRIES = 2` cap (agent.py:2873) handles it. The dedicated hallucination-cap failsafe is now redundant.

#### Step 5.3: Simplify the AUTO-PROMOTE text-only and iteration-passed failsafes
- **Files**: `lazyclaw/runtime/agent.py:3673-3756, 4907-4949`
- **Action**: Keep the failsafes — they're about "brain refuses to dispatch" which is orthogonal to tool name. Just replace `run_background` references with `task`.
- **Risk**: Low.

#### Step 5.4: Delete legacy skill files
- **Files**: `lazyclaw/skills/builtin/background.py`, `lazyclaw/skills/builtin/dispatch.py`, `lazyclaw/skills/builtin/delegate.py`
- **Action**: Delete files. Update `__init__.py` exports.
- **Risk**: Low — confirmed no external imports remain after Step 5.1.

#### Step 5.5: Collapse `AgentDispatcher` into `TaskRunner` (or delete)
- **Files**: `lazyclaw/runtime/dispatcher.py`
- **Action**: All `AgentDispatcher` logic now lives inside `TaskRunner._execute`'s branch for non-general_purpose subagent types. Delete the file. Move the system prompts (`_EXPLORE_SYSTEM_PROMPT`, `_GENERAL_PURPOSE_SYSTEM_PROMPT`, `_SPECIALIST_SYSTEM_PROMPT`) into the new `subagent_types.py` (Step 1.2). Delete `_IS_SUBAGENT` contextvar (replaced by depth check) and `_BACKGROUND_SUBAGENTS` (TaskRunner has its own running-task map).
- **Risk**: Medium — verify no test imports `AgentDispatcher`. Use grep before deletion.

#### Step 5.6: Remove the delegate fast-path
- **Files**: `lazyclaw/runtime/agent.py:4069-4094`
- **Action**: Delete the "If delegate is among tool calls, execute ONLY delegate" block. `task` is always non-blocking; parallel `task` + read-only tool calls are valid and useful.
- **Risk**: Low.

#### Step 5.7: Rename TeamLead `lane` field consistency
- **Files**: `lazyclaw/runtime/team_lead.py`, `lazyclaw/runtime/dispatcher.py` (in `register` calls), `web/src/pages/Activity.tsx`
- **Action**: Standardize on `lane="background"` for all `task()`-spawned work. UI may still filter by `subagent_type` if it wants finer-grain.
- **Risk**: Medium — UI ripples. Verify Activity.tsx renders correctly.

### Phase 6: Documentation

#### Step 6.1: Update CLAUDE.md
- **Files**: `/Users/blckit/Desktop/Code_Projects/lazyclaw/CLAUDE.md`
- **Action**: In the "Key Patterns" section, replace the bullets about `dispatch_subagents` / `run_background` / `delegate` with ONE bullet: "**Unified `task` primitive**: One delegation tool, six built-in `subagent_type` values, optional `tools=` allow-list. Multiple `task()` calls in one TAOR turn fan out and consolidate into ONE brain reply (`_BrainFanoutGroup`). MAX_TASK_DEPTH=2."

#### Step 6.2: Update DOCS.md
- **Action**: Add `lazyclaw/skills/builtin/task_skill.py` entry. Remove or mark deprecated the three legacy skill entries.

#### Step 6.3: Add Architecture Decision Record
- **Files**: New `docs/adr/0004-dispatcher-unification.md`
- **Action**: Capture why three became one, what failsafes survived, the parallel-call-with-fanout pattern as the official idiom.

---

## 4. Risks

### 4.1 Brain confusion during dual-primitive phase
Two-week window where brain sees `task` AND the legacy three. Mitigation:
- Description prose on the three legacy skills says "DEPRECATED — call `task(subagent_type=…)` instead". Some recent Claude versions skip deprecated tools when an alternative is described.
- SOUL.md mentions only `task` so the dominant signal points at the new primitive.
- Telemetry: count tool-call frequency per skill name, watch the legacy three drop to near-zero before Phase 5.

### 4.2 Specialist registry — do specialists subsume or live on?
**Live on as named `subagent_type` values.** They carry rich domain prompts (50-100 lines each) that are themselves the value. User-defined custom specialists addressable by their stored name. The Web UI Specialists page (if it exists; check) stays a registry of `subagent_type` definitions.

### 4.3 Tool-restriction allow-list ↔ `search_tools` discovery
The interaction is the single subtlest design point. The recommended approach (§2.4): `search_tools` always available; discovered tools must be in allow-list at execution time. This preserves lazyclaw's "discover on demand" identity while honoring Claude Code's restriction guarantee.

Alternative considered: hard-restrict — `tools=[…]` is literally everything. Rejected because it'd require the brain to know every sub-agent's exact tool needs upfront, which contradicts the entire MCP / dynamic registry design.

### 4.4 Result-consolidation semantics
Preserved exactly. The `_BrainFanoutGroup` machinery in `task_runner.py:208-1129` works on any primitive that calls `submit(source="brain", fanout_group_id=…)`. Both `RunBackgroundSkill` and the new `TaskSkill` will share the same fanout_group_id per turn (Step 1.5), so mixed calls during the dual window still consolidate.

A subtler concern: `dispatch_subagents` today does NOT route through the consolidator (it explicitly uses `SilentSubagentCallback` and bus-only delivery). Today's brain learns N independent results as bus side-notes on subsequent turns. Under unification, N `task()` calls consolidate into ONE brain turn. This is a UX change for callers that used `dispatch_subagents` — they'll now see ONE consolidated reply instead of N silent fan-outs. **This is the intended improvement** (consolidation is the architectural rule the user explicitly stated in `tests/test_brain_fanout_consolidation.py:6-8`). Document it loudly.

### 4.5 Channel-side reporters — N parallel pump paths?
All three primitives funnel through the same event surfaces:
1. `task_event_bus.publish(TaskEvent(...))` — chat_ws pump consumes
2. `callback.on_event(AgentEvent("background_done", ...))` — chat_ws + Telegram + CLI consumers

But there are subtle differences:
- `run_background`: fires BOTH 1 and 2
- `dispatch_subagents`: fires 1 only (silent on callback by design)
- `delegate`: fires 2 + 1 + `specialist_done` event

Mitigation: the unified `task` primitive ALWAYS fires both, with `bg_streaming=False` controlling whether the chat surface renders intermediate frames. The Telegram consolidator factory in `cli.py:440-443` already handles the "one consolidated push" pattern. This means the legacy `dispatch_subagents`-silent behavior goes away — Web UI users will see sub-agent activity for what used to be hidden subagents. This is a visibility WIN but document it: "Previously-silent subagents now stream their tool activity to chat (suppressible via `/streaming off`)".

### 4.6 Recent session fixes that must survive

From the recent commits (b8c9231, 6aac606, 01bcff9, 8e378e2, 96c1181) and the modified files (chat_ws.py, eco_router.py, agent.py, task_runner.py, background.py):

- **`caller_depth` threading** (`background.py:52, 144`, `agent.py:1612, 4023, 4932`, `task_runner.py:282, 626-635`) — preserved exactly. The new `TaskSkill` threads `_caller_depth` the same way.
- **Inner-Agent `task_runner`/`team_lead` wiring** (`task_runner.py:627-636`, `gateway/app.py:73-86`, `cli.py:269`) — preserved. The new primitive uses the same `Agent(... task_runner=self, team_lead=self._team_lead, depth=_caller_depth+1)` construction path.
- **`fanout_group_id` per-turn minting** (`agent.py:1835`) — preserved. New `TaskSkill` shares the same group as `RunBackgroundSkill` during the dual window.
- **`delegate is non-blocking`** (commit `01bcff9`) — the new `task` primitive is non-blocking by design, so this fix CARRIES OVER, it's not undone.
- **bg-streaming toggle** (commit `8e378e2`) — preserved; same callback wiring.
- **Block Claude Code built-ins** (commit `96c1181`) — preserved; orthogonal to dispatcher choice.
- **Contract intake pipeline + tab reaper + mcp-upwork fixes** (commits b8c9231, 6aac606) — orthogonal, not touched by this migration.
- **Brain fan-out consolidation** (`task_runner._consolidate`) — preserved exactly; this is the central architectural rule.
- **AUTO-PROMOTE iteration budget** (agent.py:4863-4900) — preserved (just rephrased to reference `task`).

### 4.7 Hidden coupling: per-task workspace_dir, LazyBrain mirror, transcript capture
All of these live on `TaskRunner._execute` (task_runner.py:680-820). The new primitive runs everything through `_execute`, so they're inherited automatically. The branching at "use Agent vs use run_specialist" in `_execute` must call the same post-processing afterward — care needed to preserve workspace + mirror + transcript capture for both branches.

### 4.8 Test-suite size
Approximately 30 test files touch the three primitives. Most are runtime-behavior tests that work against `TaskRunner.submit` directly and need no change. About 10 are skill-level tests that reference skill names — those need parallel test additions in Phase 2.

### 4.9 Brain training prior
Claude Sonnet has strong prior training on `Task(...)` as a primitive name from Claude Code. Naming the lazyclaw equivalent `task` (lowercase, matches Python convention) leverages that prior. Naming it `run_background` (keeping back-compat) does NOT leverage it. **Strong recommendation: name it `task`.**

---

## 5. Test Plan

### 5.1 Existing tests that must still pass
- `tests/test_brain_fanout_consolidation.py` — fanout group machinery untouched
- `tests/test_heartbeat_background_lane.py` — heartbeat path untouched
- `tests/test_claude_sdk_*` — orthogonal
- `tests/test_dispatcher_concurrency.py` — needs migration in Phase 5.5 (delete after `AgentDispatcher` removed) OR migrate assertions to TaskRunner internal semaphore
- `tests/test_dispatch_upfront.py` — assertion strings need update in Phase 4.2
- `tests/test_auto_promote_meta_question_guard.py` — preserve (failsafe survives, just renamed)
- `tests/runtime/test_agent_force_dispatch.py` — assertion strings need update in Phase 5.3
- `tests/test_specialist_scraper_visibility.py` — preserve (specialists live on)
- `tests/test_code_specialist_capture.py` — preserve (`run_specialist` path preserved)
- `tests/test_explore_specialist_has_scraper.py` — preserve (move assertion target from `_make_specialist(EXPLORE)` to `_resolve_subagent_type("explore")`)

### 5.2 NEW tests required

| Test file | What it covers |
|---|---|
| `tests/skills/test_task_skill.py` | Schema validation, parameter passing |
| `tests/runtime/test_subagent_types.py` | All 6 built-in types + user-defined lookup + `tools=` override |
| `tests/test_task_skill_parity.py` | task ↔ run_background equivalence |
| `tests/test_task_skill_dispatch_parity.py` | N parallel task() calls ≡ dispatch_subagents |
| `tests/test_task_skill_delegate_parity.py` | task(specialist) ≡ delegate(specialist) including site-knowledge enrichment |
| `tests/test_task_skill_tool_restriction.py` | Allow-list enforcement at execution time, `search_tools` always-available, depth bound |
| `tests/test_task_skill_consolidation.py` | N task() calls in one turn → one consolidated brain reply (extends existing fanout test) |
| `tests/test_task_skill_cancellation.py` | Cancel cascades to inner agent + cleans up brain fan-out group |

### 5.3 Integration smoke tests
- End-to-end: user types "research these 3 companies and apply to the top 1" → brain emits 3 parallel `task(subagent_type="research_specialist")` → results consolidate → brain picks top 1 → fires `task(subagent_type="browser_specialist", prompt="apply to …")` → user sees 2 turns in chat (one consolidation summary, one application status).
- Cancellation: cancel mid-fanout, verify pending tasks cancelled and group settles cleanly with partial results.

### 5.4 Coverage target
80% on `lazyclaw/skills/builtin/task_skill.py`, `lazyclaw/runtime/subagent_types.py`, and the modified `_execute` branch in `task_runner.py`.

---

## 6. Open Questions for the User

(Repeated here for the plan file's standalone record.)

1. **Hard cutover vs deprecation window?** Recommended: 2-week dual-primitive window.

2. **Name of the unified primitive — `task` / `run` / keep `run_background`?** Recommended: `task` (leverages Claude Code prior).

3. **Fold `delegate` away entirely, or keep specialist names as `subagent_type`?** Recommended: keep specialists as named subagent_type values; `delegate` skill class disappears but its specialists live on.

4. **Tool-restriction allow-list interaction with `search_tools`?** Recommended: `search_tools` always available; executor enforces allow-list at execution time.

5. **Single-call-with-`tasks=[...]` vs multiple `task()` calls per turn?** Recommended: multiple per turn (mirrors Claude Code, simpler schema, native LLM behavior, existing fanout machinery handles it).

---

## 7. Estimate

| Phase | LOC delta (rough) | Engineer-days |
|---|---|---|
| 1 — Add `task` primitive | +400 / -0 | 2 |
| 2 — Parity tests | +600 / -0 | 1.5 |
| 3 — Internal caller migration | +10 / -5 | 0.5 |
| 4 — Brain-facing prompts + deprecation | +20 / -20 | 1 (+2 weeks calendar wait) |
| 5 — Remove legacy | +40 / -1100 | 1.5 |
| 6 — Documentation | +200 / -100 | 0.5 |
| **Total** | **+1270 / -1225** (~net flat) | **~7 engineer-days + 2-week wait** |

The net LOC change is ~zero, but architecturally we go from 3 skills + 2 runtimes + 4 system prompts + 5 AUTO-PROMOTE failsafes → 1 skill + 1 runtime + 6 sub-agent types + 2 AUTO-PROMOTE failsafes. The brain sees ONE primitive, with the same fan-out + consolidation behavior it has today.
```

---

## Files referenced (absolute paths)

- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/skills/builtin/background.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/skills/builtin/dispatch.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/skills/builtin/delegate.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/task_runner.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/dispatcher.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/agent.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/task_event_bus.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/callbacks.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/stuck_detector.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/personality.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/taor.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/teams/specialist.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/teams/runner.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/teams/executor.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/gateway/app.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/gateway/routes/chat_ws.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/cli.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/skills/builtin/survival/gig_skill.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/skills/builtin/background_status.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/goal_executor.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/contract_intake_executor.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/mcp/manager.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/tests/test_brain_fanout_consolidation.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/tests/test_dispatch_upfront.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/tests/test_dispatcher_concurrency.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/tests/runtime/test_agent_force_dispatch.py`
- `/Users/blckit/Desktop/Code_Projects/lazyclaw/tests/test_auto_promote_meta_question_guard.py`