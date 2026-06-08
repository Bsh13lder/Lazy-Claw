# Thin-Router Brain + Mode-Aware Delegation — Design

**Date:** 2026-06-08
**Status:** Design (approved in brainstorming, pending spec review)
**Branch target:** `feat/flutter-mobile` (or a dedicated `feat/thin-router-brain`)
**Relates to:** ADR-0005 (Specialist-First Dispatch), Phase 5 (thin-router brain, previously deferred)

---

## 1. Problem

### 1a. The live bug (root cause, evidence-backed)
On a real web turn (2026-06-08 14:18→14:22, user `a7ac3e09`, *"Find my top 5 best-matching Upwork jobs"*) the brain:

1. Called `delegate(specialist="freelance")` — the specialist started correctly. ✅
2. **Did not free itself** — the foreground agentic loop kept iterating.
3. Emitted *"I've dispatched…"* with 0 tool calls → the **action-claim hallucination detector** fired a false positive and forced a correction retry.
4. Pushed by the correction, ran `search_jobs` **itself in the foreground** — duplicating the work the specialist was already doing.
5. Hit `iteration >= _PROMOTE_BG_AT_ITER (1)` and not-all-readonly → **AUTO-PROMOTE** narrowed tools 25→1 and forced `run_background`, spawning a **third** executor (`upwork_top5_matches`).
6. The foreground exited with a 54-char stub; only the `run_background` task delivered a reply. **The delegated specialist's result was orphaned** (zero `background_done` events in the entire log).

**Net:** one request → triple execution, a 4-minute wait, and the answer came from the wrong path while the real specialist's output was discarded.

### 1b. The root cause
`delegate` was converted to a fire-and-forget async dispatch (`skills/builtin/delegate.py:431`, "Started in background") — structurally identical to `dispatch_subagents`. But the three foreground guards in `runtime/agent.py` were never taught that `delegate` is a dispatch. They only recognize `run_background` and `dispatch_subagents`:

- **AUTO-PROMOTE guard** (`agent.py` ~5855-5870): excludes `dispatch_subagents` but **not** `delegate`.
- **Action-claim failsafes** (`agent.py` ~4308, ~4362): gate on `dispatch_subagents`/`run_background` in `_called_tool_names`, not `delegate`.
- **No turn-exit after `delegate`**: the loop keeps spinning, which is what lets the other two misfire.

### 1c. The deeper architectural gap
The bug is a symptom. The brain is still a *worker-that-sometimes-delegates*, propped up by reactive nudges (AUTO-PROMOTE, pivot detector, hallucination retries, dedup). The intended architecture is a **thin team-lead brain** (Claude Code model): the brain orchestrates and stays free; workers do the work and report back. This is ADR-0005 Phase 5, deferred because the brain currently *holds* the F1/grounding anti-confabulation defenses that must move into specialists first.

---

## 2. Goals / Non-goals

### Goals
- Brain never grinds: answer · 1 quick read · delegate. Always responsive.
- Delegation is dispatch-and-exit; no duplicate/triple execution; delegated results always delivered.
- Brain decides worker count, parallel-vs-single, specialist-vs-generic.
- Escalation = workers return a structured `blocked` result; brain decides (re-delegate or ask user).
- Behavior governed by 4 modes: **Ask / Plan / Action / Execute**.
- Ship incrementally: each phase tested, flagged, reversible. Phase 1 fixes the live bug immediately.

### Non-goals (this effort)
- Mid-run pause/resume escalation (workers asking the brain *while running*). Explicitly rejected in favor of return-and-decide.
- Rewriting the agent loop from scratch (rejected: too risky on the 6.6k-line hot path).
- A second parallel brain path (rejected: conflicts with `feedback_no_parallel_widgets`).
- Live token-streaming of every worker into chat.

---

## 3. The model

### Brain (the *brain* model in settings)
Team lead. Per turn, exactly one of:
- **Answer** directly (pure question / chat).
- **One** quick read/lookup inline (e.g. "check my tasks", "what's agent 2 doing").
- **Delegate** (named specialist or generic background worker; one or many; parallel allowed).

The moment a 2nd non-meta tool call would be needed, the runtime removes the brain's work-tools so it *must* delegate. Brain turns are therefore always short → the brain is always free.

**Meta-tools (always available to the brain):** `delegate`, `dispatch_subagents`, `search_tools`, `recall_memories`, `save_memory`, `get_agent_status` (new), `web_search`.

### Workers (the *worker* model in settings)
Specialists (15 declarative `.md` specialists in `teams/specialists/`) or generic background agents. Run to completion. Never interrupt. Escalate by **returning** a structured result:
```
{ status: "blocked", need: "<what's missing>", partial: <work done so far> }
```
The brain reads this when the worker settles and either re-delegates with the inferred missing info, or asks the user.

### Fallback (the *fallback* model in settings)
Unchanged: rate-limit / role fallback per ECO router.

### Modes (canonical names: Ask / Plan / Action / Execute)
Rename of the existing `agent_mode.py` set. Mapping:

| New (canonical) | Old (`agent_mode.py`) | Brain + delegation behavior |
|---|---|---|
| **Ask** | Chat | Answer + quick reads only. No side-effecting delegation. |
| **Plan** | Plan | Delegate to **read-only** research workers; synthesize a plan; gate before any execution. |
| **Action** | Ask (the old default) | Delegate; workers **checkpoint** for user approval before each risky action (`request_user_approval`). |
| **Execute** | Auto | Delegate; workers run autonomously to completion, report back. |

> Note: the old `agent_mode.py` default was `Ask` (ask-before-act). It becomes the new **Action**. The new **Ask** (answer-only) is the old `Chat`. Migration must remap stored values: old `ask` → new `action`, old `chat` → new `ask`, `plan`/`auto` unchanged in meaning (`auto` → `execute`).

Mode is enforced at `PermissionChecker.check_effective` (already the single enforcement point) plus, new in this design, in the brain's *offered tool set*.

---

## 4. Phased plan (incremental, in-place)

Each phase sits behind a flag (reuse `SPECIALIST_FIRST_BRAIN`, or add `LAZYCLAW_THIN_ROUTER`), is independently shippable, and has explicit acceptance criteria + tests.

### Phase 1 — `delegate` = dispatch-and-exit (fixes the live bug)
**Changes (`runtime/agent.py`, `skills/builtin/delegate.py`):**
- AUTO-PROMOTE guard (~5868): add `and "delegate" not in _called_tool_names`.
- Action-claim failsafes (~4308, ~4362): treat `delegate` ∈ `_called_tool_names` as a real dispatch — do **not** flag a post-delegate "dispatched" reply as hallucination, do **not** auto-submit to `task_runner`.
- After a successful `delegate`, **end the foreground turn** returning delegate's "started" message (mirror dispatch-and-return).
- Confirm `delegate`'s blocked/failed results surface via the existing `background_done`/consolidation pump.

**Acceptance:** the 14:18 episode, replayed, produces exactly one worker, no AUTO-PROMOTE, no `run_background`, turn exits immediately, specialist result delivered.

### Phase 2 — mechanical 1-action routing rule
**Changes (`runtime/agent.py`):**
- Track inline non-meta tool-call count per brain turn.
- After 1 non-meta call, narrow the brain's offered tools to **meta-tools only** for the remainder of the turn → a 2nd domain call is impossible; the brain must delegate.
- This supersedes AUTO-PROMOTE's force-delegate role (cleaner, earlier). AUTO-PROMOTE is *kept but dormant* until Phase 4 deletes it.

**Acceptance:** after the brain makes 1 domain tool call, the next LLM call is offered only meta-tools; a multi-step request results in a delegation, never inline grind.

### Phase 3 — mode-aware brain
**Changes (`runtime/agent_mode.py`, `permissions/checker.py`, `runtime/agent.py`):**
- Rename Chat/Ask/Plan/Auto → Ask/Plan/Action/Execute (keep backward-compat read of old stored values in `users.settings.general.agent_mode`).
- Make the brain's offered tool set mode-aware: Ask → no write-delegation; Plan → research specialists + plan gate; Action → checkpoints forced on; Execute → autonomous.

**Acceptance:** per-mode tests for offered tools, plan gate (Plan), checkpoint enforcement (Action), autonomous flow (Execute).

### Phase 4 — grounding migration, then thin the brain
**Changes (`teams/specialists/*.md`, `runtime/agent.py`):**
- Move F1/grounding (quote-then-summarize, most-recent-wins, wikilink-leak detection) into the channel specialists (`freelance`, `messaging`, `email`).
- Only after the **full F1 suite is green** running inside specialists: delete AUTO-PROMOTE, the inline pivot detector, dedup, and keyword-gating from `agent.py`. Brain collapses to the router.

**Acceptance:** F1 test suite passes with defenses in specialists *before* any deletion; after deletion, no confabulation regression on the documented scenarios.

---

## 5. Status & escalation
- **Status:** new `get_agent_status` meta-tool reads live `TaskRunner` state ("what's running, where is it"). Counts as the brain's 1 quick read. The brain stays free because every brain turn is short and workers live off the foreground lane in `TaskRunner`.
- **Escalation:** return-and-decide. No mid-run pause/resume, no inbound routing into a running worker.

---

## 6. Files touched
- `runtime/agent.py` — P1 (delegate recognition + turn-exit), P2 (inline-action cap), P4 (delete promotion machinery).
- `skills/builtin/delegate.py` — dispatch-and-exit contract + structured blocked-result handling.
- `runtime/agent_mode.py` + `permissions/checker.py` — P3 (rename + mode-aware offering).
- `teams/runner.py` — workers return structured `blocked` result; **fold in the latent channel-tool allowlist bug** (the `messaging`/`email` specialists currently cannot reach `mcp_<id>_*`-prefixed channel tools because their `allowed_skills` list bare names; runner `~:505` rejects every channel call).
- `skills/builtin/agent_status.py` — new `get_agent_status` meta-tool.
- `teams/specialists/*.md` — P4 grounding rules.

---

## 7. Risks & mitigations
- **Hot-path fragility** (`agent.py` is 6.6k lines, long regression history): mitigate via small per-phase diffs, flag-gating, and a regression test per phase before the next.
- **Grounding regression** if the brain is thinned before F1 moves to specialists: mitigate by ordering — Phase 4 migrates *then* deletes, gated on a green F1 suite.
- **Mode rename churn**: mitigate with a backward-compat read of old stored mode values.
- **Channel-tool allowlist bug** is separate but adjacent; fixing it in `teams/runner.py` during Phase 3 prevents `messaging`/`email` specialists from being dead-on-arrival once delegation works.

---

## 8. Open questions / future
- Final flag name (`SPECIALIST_FIRST_BRAIN` reuse vs new `LAZYCLAW_THIN_ROUTER`).
- Whether `dispatch_subagents` and `delegate` should merge into one delegation primitive long-term (out of scope here).
- Channel-tool allowlist fix: resolve `mcp_<id>_*` naming by allowing specialists to reference bare channel tool names that map through the MCP bridge (design detail for the implementation plan).
