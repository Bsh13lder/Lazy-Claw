# Unified `agent` Dispatch Tool — Claude Code Dispatcher Pattern

**Date:** 2026-07-07
**Status:** Approved (Approach A)
**Branch:** fix/minimax-web-confabulation (implementation continues on this working tree — it carries deployed-unmerged runtime fixes that touch the same files)

## Problem

The brain has three overlapping dispatch tools (`delegate`, `dispatch_subagents`, `run_background`) with different semantics, schemas, and caps. All three are fire-and-forget: the dispatch call returns only task IDs, and the merged answer arrives on a *later* synthetic consolidation turn. In practice the brain (especially MiniMax-class models) picks wrong, stalls, or never dispatches — perceived agent functionality is ~0. A large share of `runtime/agent.py` complexity (thin-router caps, AUTO-PROMOTE, pivot detector, three failsafes) exists solely to *force* the brain into these tools.

Claude Code solved the same problem with ONE `Agent` tool: `subagent_type` + `prompt` + optional `run_in_background`, sync results returned **in-turn as tool results**, agent types defined declaratively. We adopt that pattern on top of LazyClaw's existing, proven primitives.

## Goals

1. One brain-facing tool — `agent(agent_type, task, run_in_background=false, timeout?)` — replaces the 3-way choice.
2. **Sync-by-default**: N `agent` calls in one assistant message run in parallel (TAOR already `asyncio.gather`s independent tool calls) and each returns its result as a normal tool result. The brain synthesizes in the same turn. No consolidation turn needed.
3. **Background opt-in**: `run_in_background=true` routes to the existing TaskRunner + brain-fanout consolidation path (unchanged).
4. All agent types declarative: convert hardcoded `explore` / `general_purpose` dispatcher types to `.md` specialist files.
5. Raise fan-out width: up to **15** `agent` calls per turn (was 5), in-flight concurrency semaphore default **6** (env `LAZYCLAW_DISPATCH_CONCURRENCY`, was 4).
6. Old tools alias-and-soak: `delegate` / `dispatch_subagents` / `run_background` stay registered but demoted; removed from base toolset after soak.

## Non-Goals (later phases)

- Tearing down AUTO-PROMOTE / thin-router / pivot machinery (deferred until `agent` soaks in prod).
- Nested dispatch (subagents spawning subagents) — single-depth guard stays.
- Workflow/DAG scripting, per-agent model pinning beyond existing role routing.
- SendMessage-style continuation of a finished subagent.

## Architecture

### New skill: `agent` (`lazyclaw/skills/builtin/agent_tool.py`)

Registered fresh per turn in `runtime/agent.py` (same pattern as the current three — needs live eco_router, team_lead, task_runner, fanout_group_id, chat_session_id, and a per-turn dispatch counter).

**Schema** (deliberately minimal for small-brain models):

```json
{
  "agent_type": {"type": "string", "enum": ["explore", "general_purpose", "browser", "research", "code", "email", "upwork", "notes", "tasks", "documents", "messaging", "contacts", "system", "automation", "bounty", "web_research", "code_research", ...custom specialist names]},
  "task":       {"type": "string", "description": "Complete, self-contained instruction. The agent has NO chat history."},
  "run_in_background": {"type": "boolean", "default": false},
  "timeout":    {"type": "integer", "default": 120, "maximum": 600}
}
```

Short aliases reuse `delegate.py:_SPECIALIST_MAP` (extracted to a shared module so both tools import it during soak).

**Sync path (`run_in_background=false`, the new capability):**

1. Depth guard: reject if `_IS_SUBAGENT.get()` (contextvar from `runtime/dispatcher.py`).
2. Per-turn cap: reject call #16+ with a clear error naming the cap.
3. Resolve `agent_type` → `SpecialistConfig` (builtin, custom-encrypted, or the two new declarative types).
4. Acquire the module-level dispatch semaphore (concurrency 6), then `asyncio.wait_for(run_specialist(...), timeout)`.
5. Register with `team_lead` under `lane="subagent"` + per-agent `CancellationToken` (existing dispatcher behavior, reused) so the Activity UI shows live agents.
6. Return the specialist's final text as the tool result: `{status, agent_type, result, files_touched?, elapsed_s}`.

Parallelism comes free: the brain emits N `agent` tool calls in one message → TAOR's `asyncio.gather` runs them concurrently → the semaphore throttles actual in-flight LLM loops.

**Background path (`run_in_background=true`):** thin wrapper over today's `RunBackgroundSkill.execute` internals — `task_runner.submit(source="brain", fanout_group_id=...)`. Consolidation, re-delegation budget, quiet-mode handling all unchanged.

**Timeout behavior (sync):** on `asyncio.TimeoutError`, cancel the agent's token and return `{status: "timeout", partial: <last transcript tail if available>}` as the tool result — the brain decides whether to retry in background. Never raise through TAOR.

### Tool-result cap — CRITICAL

Subagent results are load-bearing synthesis input. Per the 2026-05-25 truncation-confabulation incident: `agent` results get a generous explicit cap of **12,000 chars** (added alongside `_MAX_TOOL_RESULT_CHARS_CHANNEL_READ` in `agent.py` — new `_MAX_TOOL_RESULT_CHARS_AGENT = 12000`, keyed on tool name `agent`). Clipping inside the skill also appends an explicit `[truncated N chars]` marker so F1/triage tooling sees it.

### Declarative `explore` + `general_purpose`

Two new files in `teams/specialists/`:

- `explore.md` — read-only fan-out searcher. Tools: `read_file`, `list_directory`, `run_command` (read-only guidance in prompt), `web_search`, `search_tools`. Worker model (default role routing).
- `general_purpose.md` — multi-step executor. Needs "all tools except dispatch/meta". Loader extension: frontmatter `tools: "*"` (string wildcard) → allowlist = every registry tool at run time minus `{agent, delegate, dispatch_subagents, run_background, search_tools-exempt meta}`. `runner._filter_tools` gains the wildcard branch; execute-time allowlist check treats wildcard configs as allow-all-minus-denylist.

`runtime/dispatcher.py`'s hardcoded `AgentType` prompts stay for backward compat during soak (dispatch_subagents still uses them) but the `agent` tool resolves only via the specialist loader.

### Brain integration (`runtime/agent.py`)

- Add `agent` to `_BASE_TOOL_NAMES`, `_META_TOOLS`, `_DISPATCH_ONLY_TOOLS`, and the async-dispatch-already-called set that AUTO-PROMOTE checks — a turn that called `agent` is never force-promoted and never thin-router-capped mid-fan-out.
- Register per-turn with a turn-scoped counter object (max 15) shared across all `agent` invocations in the turn.
- Sync `agent` calls count as dispatch, not as inline work, for `_FG_WORK_CALL_BUDGET`.
- SOUL.md: rewrite the dispatch section to describe ONE tool — when to fan out sync (reads/research/multi-part questions) vs background (slow mutating work, >2 min). Per feedback_prompt_before_runtime: SOUL is the first lever; the runtime nudges are not extended.

### Alias-and-soak

- `delegate`, `dispatch_subagents`, `run_background` remain registered (cron/heartbeat/specialist-lesson paths reference them) but their descriptions gain one line: "Prefer the `agent` tool."
- They come OUT of `_BASE_TOOL_NAMES` once `agent` passes live verification (same-PR flag flip is fine; keep a one-line revert path).
- Thin-router failsafe (`agent.py` submit-and-exit) is left untouched — it operates on TaskRunner directly.

## Error handling

- Unknown `agent_type` → tool error listing valid types (never silent).
- Semaphore starvation is invisible to the brain (calls queue); per-call `timeout` still bounds total wait because `wait_for` wraps acquisition + run.
- Sync agent crash → `{status: "error", error: <message>}` tool result; TAOR continues; brain sees the failure and can re-dispatch in background.
- Background path errors: existing TaskRunner failure consolidation (unchanged).
- All user-facing failure text goes through existing skill error envelopes; details logged server-side.

## Testing

New `tests/runtime/test_agent_tool.py` + `tests/teams/test_wildcard_allowlist.py` (pytest, scoped — never full-suite while container is up):

1. Schema: enum contains all builtin + explore/general_purpose; defaults correct.
2. Sync path returns specialist result as tool result (mock `run_specialist`).
3. N sync calls run concurrently, semaphore caps in-flight at 6 (instrumented fake).
4. Per-turn cap: call 16 rejected with clear error; counter is per-turn (fresh registration resets).
5. Depth guard: `_IS_SUBAGENT` set → rejected.
6. Timeout → `{status: "timeout"}` result, token cancelled, no raise.
7. Background path routes to `task_runner.submit` with `fanout_group_id`.
8. Result clipping at 12,000 chars appends `[truncated N chars]`.
9. Wildcard loader: `tools: "*"` parses; `_filter_tools` returns registry-minus-denylist; execute-time check allows non-denylisted tool, blocks `agent` itself.
10. `explore.md` / `general_purpose.md` load via `load_builtin_specialists` and pass `startup_specialist_self_check`.
11. AUTO-PROMOTE skip: turn that called `agent` is not force-promoted (unit test on the skip-set logic).

Verification (live): `make rebuild` → send a multi-part research question on web chat → confirm N subagent lanes appear in Activity → single same-turn reply synthesizing all results → no `[truncated` in decrypted tool rows.

## Rollout

1. Land skill + loader + tests (this branch, deployed via `make rebuild`).
2. SOUL.md dispatch-section rewrite.
3. Flip `_BASE_TOOL_NAMES` to include `agent`; keep old three for one soak week.
4. After soak: remove old three from base toolset; brain-forcing machinery teardown becomes its own follow-up spec.
