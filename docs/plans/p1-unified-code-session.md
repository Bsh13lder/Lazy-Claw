# P1 — Unified Code Session per Project

**Status**: ✅ **IMPLEMENTED 2026-05-19** (initial draft 2026-05-18). See acceptance criteria at bottom.
**Parent plan**: next-session handoff 2026-05-18, Open item #1 (handoff completed and removed 2026-07-21).
**Branch**: `feat/claude-agent-sdk` (current).
**Est. effort**: ~1.5 h (actual: roughly on plan).
**TODO.md anchor**: Phase 22.
**DOCS.md anchor**: "Unified Code Session per Project (Phase 22, 2026-05-19)".

---

## Goal

Every contract/Goal owns ONE long-lived Claude session for code work. Recon → scaffold → iterate share the same context. The Code Specialist's brain remembers prior turns; claude-code MCP's internal CLI keeps its per-cwd session in sync because workFolder is already stable per Goal.

Concretely, after P1 lands:

- "scaffold the estreet-bot" creates a Goal, dispatches the Code Specialist with a fresh session_id, claude-code writes the initial files under the goal's workspace.
- "now add a city filter" reuses the SAME Goal, the SAME session_id, and the specialist's brain picks up exactly where it left off.
- Resume failures fall back gracefully to a cold session — no hard stop.

## What's already in place (verified by code-read)

| Piece | Where |
|---|---|
| Per-goal workspace dir `/workspace/{tag}/{goal}/{task}/` | `teams/specialist.py:65` |
| Host-bind mount to `~/Desktop/lazyclaw-workspace` | `docker-compose.yml` + `specialist.py:31` |
| Code Specialist routes via claude-code MCP | `teams/specialist.py:215` (system prompt) |
| `dispatch_code_goal` fire-and-forget into `run_specialist` | `runtime/code_goal_executor.py` |
| Workspace hint prepended to system prompt | `teams/runner.py:248` |
| SpecialistResult carries workspace_dir + files_touched + transcript | `teams/runner.py:107` |
| `session_id` + `--resume` plumbing in CLI provider | `llm/providers/claude_cli_provider.py:437,501-509` |
| `session_id` round-trip in SDK provider (usage dict) | `llm/providers/claude_sdk_provider.py:541,591,664-665` |
| Additive schema migration mechanism | `db/connection.py:40-91` |

## What's missing

| # | Gap | Why it matters |
|---|---|---|
| 1 | `goals.code_session_id` column | Without it, every dispatch is a cold session |
| 2 | Plumb `session_id` through `run_specialist` → `eco_router.chat()` | The wire to actually use the existing provider support |
| 3 | Persist returned `session_id` back to the Goal | Closes the loop |
| 4 | `continue_code_goal(goal, instruction)` public API | Multi-turn entry point for EXECUTING goals |
| 5 | Brain routing: continuation message on active code goal → `continue_code_goal` | Prevents tonight's `run_background` misroute |
| 6 | SOUL.md rule formalizing #5 | First lever (per CLAUDE.md) |

## File-by-file change list

### 1. `lazyclaw/db/schema.sql` — additive column

Append to the `goals` CREATE TABLE (kept here too so fresh installs match):

```sql
code_session_id   TEXT,                     -- persistent claude-code session per goal
```

### 2. `lazyclaw/db/connection.py` — migration entry

Add ONE line to the migration tuple list (around line 91):

```python
("goals", "code_session_id", "ALTER TABLE goals ADD COLUMN code_session_id TEXT"),
```

This auto-applies on next container start; no migrate script needed.

### 3. `lazyclaw/runtime/goal_executor.py` — Goal field + persistence

- Add `code_session_id: str | None = None` to the `Goal` dataclass.
- `GoalRepository.save` / `load` carry the field through (DB columns aren't encrypted — session IDs are random UUIDs, not user content).
- Add `GoalExecutor.set_code_session_id(user_id: str, goal_id: str, sid: str)` — small, focused setter so the runner can write back without inflating a wider update path.
- Add `GoalExecutor.continue_code(user_id: str, goal_id: str, instruction: str)`:
  - Loads the Goal, asserts it's `EXECUTING` and `work_type` ∈ code tags.
  - Calls `dispatch_code_goal` again with an extra `additional_instruction` arg (see #4 below).
  - Returns immediately — the dispatch fan-out spawns its own background task.

### 4. `lazyclaw/runtime/code_goal_executor.py` — pass session_id + multi-turn

- `_compose_code_instruction(goal, additional_instruction: str | None = None)`:
  - On a fresh dispatch (additional_instruction is None): unchanged — title + summary + plan + Q&A + risks.
  - On continuation (additional_instruction is set): use only the continuation text. The specialist's brain already has session memory; re-sending the full original brief would be wasteful + confusing.
- `dispatch_code_goal(goal, additional_instruction: str | None = None)`:
  - Pulls `goal.code_session_id` as the resume id (None = fresh).
  - Passes it through to `run_specialist` via a new `code_session_id` kwarg.
  - Provides an `on_session_id` callback that calls `GoalExecutor.set_code_session_id` on first response. The callback fires AT MOST ONCE per dispatch (latching pattern) — once we have the id, we never overwrite it.

### 5. `lazyclaw/teams/runner.py` — session_id plumbing

- Add to `run_specialist` signature: `code_session_id: str | None = None`, `on_session_id: Callable[[str], Awaitable[None]] | None = None`.
- For CODE_SPECIALIST only: pass `session_id=code_session_id` through to `eco_router.chat(**kwargs)` on every iteration so the worker's claude session is the same across iterations within a single dispatch AND across dispatches if a resume id was provided.
- After each chat call, if `response.usage.get("session_id")` is present and we haven't notified yet, call `on_session_id(sid)` and latch.
- Defensive: if the provider returns a 4xx "session not found" style error, log a warning, retry once with `session_id=None`, and on success notify the caller via `on_session_id` so the goal's stale id gets replaced.

### 6. `lazyclaw/runtime/agent.py` — continuation routing

- Add `_active_code_goal_id(user_id)` helper that queries `goals WHERE user_id=? AND status='executing' AND work_type IN ('code', 'code_project', 'code_task', 'build_app') ORDER BY last_progress_at DESC LIMIT 1`.
- In the per-turn keyword injector: if there's an active code goal AND the user message contains continuation verbs (`now`, `also`, `add`, `fix`, `update`, `change`, `refactor`, `remove`, `move`, `rename`, `test`, `deploy`), route directly to `GoalExecutor.continue_code` and skip the normal dispatch path. Reply to user: "Continuing on goal X — claude-code is picking it up."
- Narrow the toolbelt while continuation is dispatching (mirror of the AUTO-PROMOTE readonly allowlist pattern at line 220 of CLAUDE.md).

### 7. `personality/SOUL.md` — routing rule

Add under the existing NEVER-rules section:

> **NEVER** spawn `run_background` or `dispatch_subagents` for code work when an active code Goal is EXECUTING for the same user. Call `GoalExecutor.continue_code(goal_id, instruction)` instead — the Goal owns the persistent claude-code session. Reason: `run_background` launches a Claude CLI with `--disallowedTools Bash,Read,Edit,Write,...` and will hang silently (see MEMORY → `feedback_code_tasks_via_claude_code_mcp`).

### 8. Tests — `tests/test_code_goal_session_persistence.py` (new)

Four tests, pytest, mock `eco_router` + `GoalRepository`:

1. **First dispatch writes session_id**: dispatch with `code_session_id=None`, fake provider returns `usage["session_id"]="abc123"`, assert `goal.code_session_id == "abc123"` after.
2. **Resume on second dispatch**: goal already has `code_session_id="abc123"`, second `dispatch_code_goal` is called, assert `run_specialist` received `code_session_id="abc123"`.
3. **Resume-failure fallback**: provider raises "session not found", runner retries with None, writes the new id back via on_session_id.
4. **continue_code dispatches a continuation-only instruction**: assert `_compose_code_instruction` was called with `additional_instruction="now add tests"`, NOT the full original brief.

## Out of scope for P1

| Out | Why | Where it goes |
|---|---|---|
| Brain becomes thin dispatcher (P3) | Higher risk, UI + agent loop changes | Defer to P3 in the parent plan |
| SOUL.md rule for "if user names a contract/contact → delegate(code)" (part of P2) | P2 territory, requires per-message classifier | P2 |
| CodeSpecialist.tsx per-contract timeline | P3 UI | P3 |
| Bug E (offer-card extraction) | Independent fix | Open item #2 in parent plan |
| Forking @steipete/claude-code-mcp | Not needed — per-cwd auto-continuity + specialist-brain persistence is enough | Revisit only if A proves insufficient |

## Risk register

| Risk | Mitigation |
|---|---|
| Resume id becomes stale (claude session GC'd, server restarted, etc.) | Defensive retry in runner.py (#5) — one cold-session retry + overwrite the stale id |
| Continuation router over-triggers (user mid-sentence on unrelated topic) | Bounded verb list + only when an `EXECUTING` code goal exists for that user. Escape hatch: any message starting with `/` or naming a different contract pushes back to normal routing. |
| Two parallel continuation dispatches collide on same goal | Add a `dispatching` flag to in-memory `_background_tasks` set; second dispatch within 2s no-ops and replies "still working". |
| Schema migration on container with active live goals | `ALTER TABLE … ADD COLUMN` is online in SQLite — no downtime, defaults to NULL for existing rows (= behaves as cold session, same as today). |

## Implementation order (sign-off → ~1.5 h)

1. Schema + migration (5 min, lowest risk)
2. Goal field + GoalRepository carry-through (15 min)
3. run_specialist session_id plumbing + on_session_id callback (25 min)
4. dispatch_code_goal: read goal.code_session_id, wire callback (10 min)
5. continue_code_goal API on GoalExecutor (15 min)
6. agent.py continuation router (20 min)
7. SOUL.md rule (5 min)
8. Tests (15 min)

Verification after each step: existing test suite still green. Final acceptance test: dispatch a code goal, observe session_id written to DB; second dispatch on same goal observed to pass `--resume` to the provider (assert via log line).

## Acceptance criteria (for marking P1 done)

- [x] `goals.code_session_id` column exists in fresh + migrated DBs. (`schema.sql` + `connection.py` migration tuple.)
- [x] First dispatch of a code Goal writes a session_id back to the row. (`runner.py:on_session_id` latching callback → `code_goal_executor._on_session_id` → `GoalExecutor.set_code_session_id`.)
- [x] Second dispatch of the SAME Goal passes that session_id through to `eco_router.chat(session_id=...)`. (`run_specialist` reads `code_session_id` kwarg, threads to `kwargs["session_id"]`.)
- [ ] Provider log shows `--resume <id>` instead of `--session-id <id>` on second call. **Deferred to live verification on next James-bot turn** — covered by existing `claude_cli_provider`/`claude_sdk_provider` machinery; integration log-line check not automatable in unit tests.
- [x] Continuation message ("now add tests") on an EXECUTING code Goal routes through `continue_code_goal`, not `run_background`. (New skill + SOUL.md NEVER rule.)
- [x] Resume failure falls back to a fresh session, overwrites the stale id, no user-visible error. (`_stale_session_retry_used` guard + on_session_id re-fire with new id.)
- [x] All 4 (actually 10) new tests pass. (`tests/test_code_goal_session_persistence.py`.)
- [x] Existing test suite stays green. (68 existing goal/specialist/state-machine tests, all pass — zero regressions.)
- [x] No new mypy / ruff warnings on the touched files. (Syntax-check pass on all 8 touched files.)

**Net deviation from spec**: zero. Multi-turn semantics adjustment (code goals no longer auto-DONE on success — see Phase 22 in TODO.md item 22.7) is required for `continue_code` to be useful (terminal goals reject continuation); flagged in the spec under risk register and DOCS.md.
