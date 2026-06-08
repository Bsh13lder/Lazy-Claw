# Thin-Router Brain + Mode-Aware Delegation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the brain a thin always-free team lead — it answers, makes at most one quick read, or delegates — while workers do the work and report back, with behavior governed by 4 modes (Ask/Plan/Action/Execute).

**Architecture:** Incremental, in-place, behind a flag. Phase 1 fixes the live triple-execution bug by teaching the three foreground guards in `agent.py` that `delegate` is a real dispatch. Phases 2-4 add the mechanical 1-action routing cap, mode-aware tool offering, and (last) the grounding migration that lets the brain be thinned.

**Tech Stack:** Python 3.11, asyncio, pytest. Edits centered on `lazyclaw/runtime/agent.py`, `lazyclaw/skills/builtin/delegate.py`, `lazyclaw/runtime/agent_mode.py`, `lazyclaw/teams/runner.py`.

**Spec:** `docs/superpowers/specs/2026-06-08-thin-router-brain-design.md`

**Plan structure note:** Phase 1 is written in full bite-sized TDD detail (executable now — exact edits verified against the current tree). Phases 2-4 are structured task breakdowns (files, exact change, tests, acceptance) to be expanded into bite-sized steps **at execution time, after re-reading the post-Phase-1 tree** — their line-level edits depend on Phase 1 having landed. Do not deep-detail 2-4 until Phase 1 is merged.

---

## Phase 1 — `delegate` = dispatch-and-exit (fixes the live bug)

**Background:** On 2026-06-08 14:18 a web turn called `delegate(freelance)`, then (a) didn't exit, (b) tripped the action-claim hallucination detector with a *truthful* "I've dispatched" reply, (c) ran `search_jobs` itself, (d) got AUTO-PROMOTED into a third `run_background` executor. Net: triple execution, the specialist's result orphaned. Root cause: the three guards recognize `run_background` and `dispatch_subagents` as dispatches but not `delegate` (which became fire-and-forget at `delegate.py:268`).

The tests follow the existing **source-inspection** convention (see `tests/runtime/test_dispatch_subagents_no_autopromote.py`): they read `agent.py` as text and assert the guard tokens are present. This is the idiomatic, reliable way to test these loop guards in this codebase.

### Task 1: AUTO-PROMOTE must exclude `delegate`

**Files:**
- Test: `tests/runtime/test_delegate_dispatch_exit.py` (create)
- Modify: `lazyclaw/runtime/agent.py` (AUTO-PROMOTE condition, the block ending in `_promoted_to_bg = True`)

- [ ] **Step 1: Write the failing test**

Create `tests/runtime/test_delegate_dispatch_exit.py`:

```python
"""`delegate` is a fire-and-forget async dispatch (delegate.py:268) — the
foreground guards must treat it exactly like `dispatch_subagents` /
`run_background`. 2026-06-08 14:18 incident: a delegate turn did not exit,
tripped the action-claim retry on a truthful 'I've dispatched', ran the
work itself, then AUTO-PROMOTE spawned a third run_background executor —
triple execution, specialist result orphaned.
"""

from __future__ import annotations

from pathlib import Path

_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


def test_auto_promote_excludes_delegate() -> None:
    """The AUTO-PROMOTE trigger must skip when `delegate` was already
    called this turn (mirrors the run_background / dispatch_subagents
    exclusions)."""
    idx = _AGENT_SRC.index("_promoted_to_bg = True")
    head = _AGENT_SRC.rindex("if (", 0, idx)
    condition = _AGENT_SRC[head:idx]
    assert '"run_background" not in _called_tool_names' in condition, (
        "regression: run_background exclusion must remain"
    )
    assert '"dispatch_subagents" not in _called_tool_names' in condition, (
        "regression: dispatch_subagents exclusion must remain"
    )
    assert '"delegate" not in _called_tool_names' in condition, (
        "AUTO-PROMOTE must not re-background a turn that already "
        "delegated — delegate is already a non-blocking dispatch"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/runtime/test_delegate_dispatch_exit.py::test_auto_promote_excludes_delegate -v`
Expected: FAIL on the `"delegate" not in _called_tool_names` assertion (token absent).

- [ ] **Step 3: Add the exclusion to the AUTO-PROMOTE condition**

In `lazyclaw/runtime/agent.py`, find the AUTO-PROMOTE condition (the line `and "dispatch_subagents" not in _called_tool_names` immediately above `and iteration >= _PROMOTE_BG_AT_ITER`). Add the delegate exclusion right after it:

```python
                    and "dispatch_subagents" not in _called_tool_names
                    # `delegate` is ALSO a fire-and-forget async dispatch
                    # (delegate.py:268 schedules run_specialist detached and
                    # returns immediately). Promoting a turn that delegated
                    # to a background worker spawns a THIRD redundant
                    # executor — the 2026-06-08 14:18 triple-execution bug.
                    and "delegate" not in _called_tool_names
                    and iteration >= _PROMOTE_BG_AT_ITER
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/runtime/test_delegate_dispatch_exit.py::test_auto_promote_excludes_delegate -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_delegate_dispatch_exit.py lazyclaw/runtime/agent.py
git commit -m "fix(agent): AUTO-PROMOTE excludes delegate (no triple-execution)"
```

### Task 2: action-claim guards must include `delegate` (both sites)

A post-`delegate` "I've dispatched the specialist" reply is **truthful**, not a hallucination. Both the action-claim retry guard and the exhausted-retry force-dispatch failsafe currently gate on `dispatch_subagents`/`run_background` only.

**Files:**
- Test: `tests/runtime/test_delegate_dispatch_exit.py` (append)
- Modify: `lazyclaw/runtime/agent.py` (action-claim retry block + force-dispatch failsafe block)

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime/test_delegate_dispatch_exit.py`:

```python
def test_action_claim_guards_include_delegate() -> None:
    """The action-claim retry AND the force-dispatch failsafe must skip
    when the brain already called `delegate` — else a truthful 'I've
    dispatched' is force-rolled into duplicate inline work. Together with
    the AUTO-PROMOTE exclusion that's 3 guard sites total."""
    assert _AGENT_SRC.count(
        '"delegate" not in _called_tool_names'
    ) >= 3, (
        "expected the delegate guard in AUTO-PROMOTE + the action-claim "
        "retry + the action-claim force-dispatch failsafe (3 sites)"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/runtime/test_delegate_dispatch_exit.py::test_action_claim_guards_include_delegate -v`
Expected: FAIL — only 1 occurrence so far (from Task 1), need ≥3.

- [ ] **Step 3: Add the guard to the action-claim retry block**

In `agent.py`, in the action-claim retry condition (the block containing `_halluc_retries < _HALLUC_MAX_RETRIES`), find:

```python
                        and "dispatch_subagents" not in _called_tool_names
                        and "run_background" not in _called_tool_names
                    ):
                        _halluc_retries += 1
```

Change the two guard lines to three:

```python
                        and "dispatch_subagents" not in _called_tool_names
                        and "run_background" not in _called_tool_names
                        # delegate is a real fire-and-forget dispatch too —
                        # a post-delegate "I've dispatched" is truthful.
                        and "delegate" not in _called_tool_names
                    ):
                        _halluc_retries += 1
```

- [ ] **Step 4: Add the guard to the force-dispatch failsafe block**

In `agent.py`, in the exhausted-retries failsafe (the block with `and _halluc_retries >= _HALLUC_MAX_RETRIES`), find:

```python
                        and "run_background" not in _called_tool_names
                        # Already dispatched subagents → the status is true,
                        # do NOT force a redundant background task (RC2).
                        and "dispatch_subagents" not in _called_tool_names
                        and not _is_meta_question(message)
```

Add the delegate guard:

```python
                        and "run_background" not in _called_tool_names
                        # Already dispatched subagents → the status is true,
                        # do NOT force a redundant background task (RC2).
                        and "dispatch_subagents" not in _called_tool_names
                        # Same for delegate — it already dispatched a worker.
                        and "delegate" not in _called_tool_names
                        and not _is_meta_question(message)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/runtime/test_delegate_dispatch_exit.py::test_action_claim_guards_include_delegate -v`
Expected: PASS (now 3 occurrences).

- [ ] **Step 6: Commit**

```bash
git add tests/runtime/test_delegate_dispatch_exit.py lazyclaw/runtime/agent.py
git commit -m "fix(agent): action-claim guards treat delegate as a real dispatch"
```

### Task 3: `delegate` exits the foreground turn

After a successful `delegate`, the loop must hand off and return — mirroring the existing `run_background` hard-stop (`agent.py` ~5403-5435), so the brain frees itself instead of iterating into duplication.

**Files:**
- Test: `tests/runtime/test_delegate_dispatch_exit.py` (append)
- Modify: `lazyclaw/runtime/agent.py` (add a hard-stop after the `run_background` hard-stop, inside the `for tc in _tool_calls_to_run:` loop)

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime/test_delegate_dispatch_exit.py`:

```python
def test_delegate_has_dispatch_and_exit_hardstop() -> None:
    """After a successful delegate, the loop must hand off + return (mirror
    the run_background hard-stop) so the brain frees itself."""
    assert 'tc.name == "delegate"' in _AGENT_SRC, (
        "delegate dispatch-and-exit hard-stop must key on the delegate "
        "tool call"
    )
    # The hard-stop sits near the run_background one and shares the
    # 'exiting foreground turn' handoff log line.
    didx = _AGENT_SRC.index('tc.name == "delegate"')
    window = _AGENT_SRC[didx:didx + 1200]
    assert "exiting foreground turn" in window, (
        "delegate hard-stop must log the foreground-turn handoff"
    )
    assert "return" in window, "delegate hard-stop must return from the loop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/runtime/test_delegate_dispatch_exit.py::test_delegate_has_dispatch_and_exit_hardstop -v`
Expected: FAIL — `tc.name == "delegate"` not yet in source.

- [ ] **Step 3: Add the delegate hard-stop**

In `agent.py`, immediately AFTER the `run_background` hard-stop block (the one that ends with `return ("Continuing in background — will report back when done.")`), add the delegate hard-stop. Insert before the `# ── Hard stop: OAuth credential not authorized ──` comment:

```python
                    # ── Hard stop: delegate dispatched — exit foreground ──
                    # delegate is fire-and-forget (delegate.py:268): it
                    # schedules run_specialist detached and returns a
                    # "started" string. Continuing the loop is what let the
                    # brain trip the action-claim retry, duplicate the work,
                    # and get AUTO-PROMOTED (2026-06-08 14:18 triple-exec).
                    # Hand off and return the delegate's own message, exactly
                    # like the run_background hard-stop above. Only the
                    # initial foreground turn exits — background sub-turns
                    # keep iterating. Skip on error results so the brain can
                    # correct (e.g. 'Unknown specialist').
                    if (
                        tc.name == "delegate"
                        and not getattr(self, "is_background", False)
                        and isinstance(result, str)
                        and not result.startswith(("Error", "Unknown"))
                    ):
                        logger.info(
                            "delegate dispatched — exiting foreground turn "
                            "(was iter=%d)",
                            iteration,
                        )
                        await cb.on_event(AgentEvent("done", "Delegated", {}))
                        if _delegate_registered and self.registry:
                            self.registry.unregister("delegate")
                        if self._team_lead and _fg_task_id:
                            self._team_lead.complete(
                                _fg_task_id,
                                "Delegated to a specialist — will report "
                                "back when done.",
                            )
                            _fg_task_id = None
                        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/runtime/test_delegate_dispatch_exit.py::test_delegate_has_dispatch_and_exit_hardstop -v`
Expected: PASS.

- [ ] **Step 5: Multi-delegate note (no code — document the limitation)**

The serialization at `agent.py` ~4945 already collapses a mixed `delegate + other` turn to delegate-only. A turn that emits *two* `delegate` calls will exit after the first (per-tc). Simultaneous multi-worker spawn is served by `dispatch_subagents`; single-turn multi-delegate is out of scope for Phase 1 (rare). No action — recorded for Phase 2 review.

- [ ] **Step 6: Commit**

```bash
git add tests/runtime/test_delegate_dispatch_exit.py lazyclaw/runtime/agent.py
git commit -m "fix(agent): delegate exits the foreground turn (dispatch-and-exit)"
```

### Task 4: full-suite regression gate

**Files:** none (verification only)

- [ ] **Step 1: Run the delegation/dispatch regression tests**

Run:
```bash
pytest tests/runtime/test_delegate_dispatch_exit.py \
       tests/runtime/test_dispatch_subagents_no_autopromote.py \
       tests/runtime/test_agent_force_dispatch.py \
       tests/test_auto_promote_meta_question_guard.py \
       tests/test_brain_fanout_consolidation.py \
       tests/test_subagent_fanout_consolidation.py -v
```
Expected: all PASS.

- [ ] **Step 2: Run the F1/grounding suite (must not regress)**

Run:
```bash
pytest tests/test_f1_grounding.py tests/runtime/test_f1_retry_recheck.py \
       tests/runtime/test_context_journal_filter.py -v
```
Expected: all PASS (Phase 1 doesn't touch grounding, so this is a tripwire).

- [ ] **Step 3: Verify against the live bug (manual, optional)**

After `make rebuild`, send a web message that delegates (e.g. "find my top 5 Upwork matches"). In `data/lazyclaw.log` confirm: exactly one `Delegating to …`, **no** `AUTO-PROMOTE` line, **no** second `Background task … started` for the same request, and the foreground exits with the delegate handoff message. Compare to the 14:18 episode in the spec.

- [ ] **Step 4: Commit (if any flake fixes were needed)**

```bash
git add -A && git commit -m "test(agent): delegation regression gate green"
```

---

## Phase 2 — mechanical 1-action routing cap (expand to bite-sized after Phase 1)

**Outcome:** the brain may make at most ONE non-meta tool call per turn; the moment it would make a 2nd, its tool list is narrowed to meta-tools only (`delegate`, `dispatch_subagents`, `search_tools`, `recall_memories`, `save_memory`, `get_agent_status`, `web_search`) so it MUST delegate.

**Tasks (to detail at execution time):**
1. Define `_META_TOOLS` constant (the set above) near the existing tool-list constants (`agent.py` ~150-190).
2. Track `_inline_domain_calls` count in the loop; increment when a non-meta, non-read tool executes.
3. After the count reaches 1, set a `_meta_only` flag that narrows the offered `tools` for subsequent iterations (reuse the `_force_dispatch_only and tools` narrowing mechanism at ~3640, generalized to "meta-only").
4. Flag-gate the whole behavior behind `LAZYCLAW_THIN_ROUTER` (default off) so it ships dark.
5. Tests (source-inspection + a behavioral test using the fanout harness in `tests/test_brain_fanout_consolidation.py`): after 1 domain call, offered tools ⊆ `_META_TOOLS ∪ already-running`.

**Acceptance:** a multi-step request results in a delegation, never inline grind; AUTO-PROMOTE no longer fires for the routing reason (it becomes dormant, deleted in Phase 4).

---

## Phase 3 — mode-aware brain (Ask / Plan / Action / Execute) (expand after Phase 2)

**Outcome:** the 4 modes are renamed canonical and drive the brain's offered tools + delegation posture.

**Tasks (to detail at execution time):**
1. `runtime/agent_mode.py`: rename `Chat/Ask/Plan/Auto` → `Ask/Plan/Action/Execute`; add a backward-compat reader that maps stored values (`chat→ask`, `ask→action`, `plan→plan`, `auto→execute`) when loading `users.settings.general.agent_mode`.
2. `permissions/checker.py` `check_effective`: keep it the single enforcement point; update the posture matrix to the new names.
3. `agent.py`: make the brain's offered tool set mode-aware — Ask → meta minus write-delegation; Plan → research specialists + plan gate; Action → delegation with checkpoints forced on; Execute → autonomous.
4. Web + mobile + Telegram `/act`: update the 4 mode labels.
5. Tests: per-mode offered-tools, Plan plan-gate, Action checkpoint enforcement, Execute autonomous; migration mapping test for old stored values.

**Acceptance:** each mode behaves per the spec table; old stored mode values keep working.

---

## Phase 4 — grounding migration, then thin the brain (expand after Phase 3)

**Outcome:** F1/grounding lives in the channel specialists; the now-redundant brain machinery is deleted.

**Tasks (to detail at execution time):**
1. Move F1 rules (quote-then-summarize, most-recent-wins, wikilink-leak detection) into `teams/specialists/{freelance,messaging,email}_specialist.md` + ensure `teams/runner.py` runs the F1 detector on specialist output.
2. **Fix the latent channel-tool allowlist bug** in `teams/runner.py` (~:505): let specialists reference bare channel tool names (`whatsapp_read`, `upwork_get_messages`, …) that resolve through the MCP bridge's `mcp_<id>_*` registration, so `messaging`/`email` specialists can actually read/send.
3. Run the full F1 suite with defenses in specialists — **must be green before any deletion.**
4. Only then: delete AUTO-PROMOTE, the inline pivot detector, the read-only-list dedup, and keyword-gating from `agent.py`. Remove the `LAZYCLAW_THIN_ROUTER` flag (behavior becomes default).
5. Tests: F1 suite green pre- and post-deletion; channel specialists can complete a read+send round-trip against a mocked MCP bridge.

**Acceptance:** no confabulation regression on the documented scenarios; channel specialists are functional; `agent.py` shrinks substantially.

---

## Self-review notes
- **Spec coverage:** Phase 1↔§4 Phase 1 + the live bug (§1); Phase 2↔routing rule (§3 + §4 Phase 2); Phase 3↔modes (§3 table + §4 Phase 3); Phase 4↔grounding migration + channel-allowlist bug (§4 Phase 4, §6, §7). Status/escalation (§5) lands in Phase 2 (`get_agent_status` meta-tool) and Phase 1 (return-and-decide is the default once delegate exits).
- **Placeholders:** Phase 1 has none (complete code + commands). Phases 2-4 are intentionally task-outlines, explicitly marked "expand at execution time after re-reading the tree" — per writing-plans scope guidance, each phase is its own working increment and its line-level edits depend on prior phases landing.
- **Type/name consistency:** guard token `"delegate" not in _called_tool_names` used identically across all 3 sites; meta-tool set named `_META_TOOLS` consistently in Phase 2/3.
