# MiniMax Tool-Calling Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 4-layer failure chain that made the 2026-07-02 MiniMax Upwork-proposal turn bail ("Hallucination cap reached") instead of submitting: warn-only text-only path, exact-match-only tool-name validity, stored MiniMax-M3 pins defeating the M2.7 revert, and a self-referential fallback.

**Architecture:** Four independent, surgical fixes. (a) provider-level single retry with `tool_choice={"type":"any"}` when MiniMax narrates a fenced-JSON plan instead of emitting `tool_use`; (b) agent-level bare-suffix rescue of hallucinated tool names against the ATTACHED tool set only (never bypassing thin-router/specialist suppression) + delegate-steering correction text; (c) parse-time scrub coercing stored `MiniMax-M3` role pins to `MiniMax-M2.7` (same pattern as the existing `_CLAUDE_JUNK_MODEL_NAMES` scrub); (d) `_resolve_models` rejects `fallback == brain`.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio, unittest.mock. No new dependencies.

## Global Constraints

- Files touched are DISJOINT per task: Task 1 → `lazyclaw/llm/providers/anthropic_provider.py`; Task 2 → `lazyclaw/runtime/agent.py`; Tasks 3+4 → `lazyclaw/llm/eco_router.py`. Do not modify any other production file.
- ALL existing tests must stay green. Especially `tests/test_minimax_text_only_warning.py` (Task 1) and `tests/runtime/` suites (Task 2).
- Immutability: never mutate `ToolCall` / `EcoSettings` / response objects in place — construct new ones.
- No hardcoded model names inline — module-level constants.
- Implementers do NOT commit — the orchestrator commits per task after review.
- Run tests with: `python3 -m pytest <path> -x -q` from the repo root.

---

### Task 1: MiniMax JSON-plan text-only → single forced-tool_choice retry

**Files:**
- Modify: `lazyclaw/llm/providers/anthropic_provider.py:331-435` (the `chat()` method)
- Test: extend `tests/test_minimax_text_only_warning.py` (read it FIRST to reuse its mock-client pattern)

**Interfaces:**
- Produces: module-level helper `_looks_like_json_plan(text: str) -> bool`; behavioral guarantee that `chat()` issues AT MOST one extra `messages.create` call per invocation, with `create_kwargs["tool_choice"] == {"type": "any"}` on the retry.

**Why:** `anthropic_provider.py:397-412` currently logs "returned text-only despite N tool(s) attached" and ships the prose onward. On 2026-07-02 M3 answered 4 consecutive turns with ` ```json {"goal": ..., "steps": ...}` plans as text; nothing retried, nothing fell back (fallback only fires on exceptions/empty), the user received raw JSON plans.

**Design rules (encode as tests):**
1. Retry ONLY when ALL hold: `"MiniMax" in response.model`, tools were attached (`tools_payload` truthy), zero parsed/recovered tool_calls, text non-empty, `_looks_like_json_plan(text)` is True, and no retry has happened yet in this `chat()` call.
2. `_looks_like_json_plan(text)`: `t = text.strip()`; True if `t.startswith("```json")`, OR `t` starts with `{`/`[` AND `json.loads(t)` succeeds. Everything else False. (The legit "Proposal drafted — two questions before I submit" reply must NOT retry.)
3. The retry re-issues the SAME `create_kwargs` but with `create_kwargs["tool_choice"] = {"type": "any"}`. The retry's response replaces the first (whatever it contains — even if still text-only, return it; never loop).
4. Non-MiniMax models: behavior byte-identical to today.
5. Keep the existing per-response warning/counters (`_minimax_total_turns`, `_minimax_text_only_turns`) firing per actual API response.

- [ ] **Step 1: Read `tests/test_minimax_text_only_warning.py`** to learn the existing fake-client fixture (how `messages.create` is mocked and how responses are shaped).

- [ ] **Step 2: Write failing tests** (extend that file) — a fake client whose `create` returns, in order, (1st) a text-only ` ```json ` plan response with model `"MiniMax-M2.7"`, (2nd) a `tool_use` response. Assert:

```python
async def test_json_plan_text_only_triggers_one_forced_retry(...):
    # provider.chat(...) with tools attached
    assert fake_client.create_calls == 2
    assert fake_client.call_kwargs[1]["tool_choice"] == {"type": "any"}
    assert result.tool_calls and result.tool_calls[0].name == "upwork_get_job_details"

async def test_plain_prose_text_only_does_not_retry(...):
    # 1st response: "Proposal drafted... two questions before I submit"
    assert fake_client.create_calls == 1
    assert result.tool_calls is None

async def test_retry_happens_at_most_once(...):
    # both responses are ```json plans → 2 calls total, text returned
    assert fake_client.create_calls == 2
    assert result.tool_calls is None

async def test_non_minimax_never_retries(...):
    # model "claude-sonnet-4-6", ```json text-only → 1 call
    assert fake_client.create_calls == 1

def test_looks_like_json_plan_shapes():
    assert _looks_like_json_plan('```json\n{"goal": "x"}\n```')
    assert _looks_like_json_plan('{"goal": "x", "steps": []}')
    assert _looks_like_json_plan('["a", "b"]')
    assert not _looks_like_json_plan("Proposal drafted — two questions")
    assert not _looks_like_json_plan("")
    assert not _looks_like_json_plan("{not json")
```

- [ ] **Step 3: Run tests, verify the new ones FAIL** (`python3 -m pytest tests/test_minimax_text_only_warning.py -x -q`).

- [ ] **Step 4: Implement.** Add near the top of the module:

```python
def _looks_like_json_plan(text: str) -> bool:
    """True when a text-only reply is machine-plan narration, not an answer.

    MiniMax M3/M2.7 under pressure emit fenced ```json {goal, steps} plans
    as prose instead of tool_use (2026-07-02 Upwork bail). A fenced json
    block or a bare JSON document is never a legitimate user-facing reply.
    """
    t = text.strip()
    if not t:
        return False
    if t.startswith("```json"):
        return True
    if t[0] in "{[":
        try:
            json.loads(t)
            return True
        except (json.JSONDecodeError, ValueError):
            return False
    return False
```

Restructure `chat()`'s request+parse section into a bounded loop (request → parse blocks → minimax markup recovery → warning/counters → maybe retry):

```python
        _plan_retry_done = False
        while True:
            response = await self._client.messages.create(**create_kwargs)
            # ... existing block parsing into text_parts/parsed_tool_calls ...
            # ... existing _extract_minimax_tool_calls recovery ...
            # ... existing MiniMax counters + text-only warning ...
            if (
                response.model and "MiniMax" in response.model
                and not parsed_tool_calls and joined_text and tools_payload
                and not _plan_retry_done
                and _looks_like_json_plan(joined_text)
            ):
                _plan_retry_done = True
                _log.warning(
                    "MiniMax %s narrated a JSON plan instead of tool_use — "
                    "retrying once with tool_choice={'type': 'any'}",
                    response.model,
                )
                create_kwargs["tool_choice"] = {"type": "any"}
                continue
            break
```

- [ ] **Step 5: Run the full file + neighbors:** `python3 -m pytest tests/test_minimax_text_only_warning.py tests/test_minimax_via_anthropic.py tests/llm/ -q`. Expected: all PASS.

---

### Task 2: Brain-side bare-suffix tool-name rescue + delegate-steering correction

**Files:**
- Modify: `lazyclaw/runtime/agent.py:4656-4745` (drop-check block) and `lazyclaw/runtime/agent.py:1861-1901` (`_build_hallucination_correction`)
- Create: `tests/runtime/test_hallucination_suffix_rescue.py`

**Interfaces:**
- Consumes: `bare_tool_name` from `lazyclaw/skills/tool_namespace.py` (strips `mcp_<uuid>_` prefix, passes native names through); `ToolCall` from `lazyclaw/llm/providers/base.py` (`id`, `name`, `arguments`).
- Produces: `_build_hallucination_correction(bad_name, valid_names, registry)` signature UNCHANGED; new module-level helper `_suffix_rescue_tool_calls(tool_calls, valid_names) -> tuple[list, list[str]]` returning (new list of ToolCalls with unique-suffix matches rewritten, list of "old → new" strings for logging).

**Why:** On 2026-07-02 the brain called `mcp_489c8963-…_upwork_get_job_details` (a STALE server UUID leaked from another user's history) and then bare `upwork_get_job_details`. Every check (`agent.py:4660-4723`, `registry.get_tool_schema`) is byte-exact; the bare-suffix union exists only for specialists (`teams/runner.py:192-215`). 3 strikes → bail. Also, the correction message's hint (`search_tools('details')`) is useless when the tool exists but is deliberately suppressed — it must steer to `delegate`.

**Design rules (encode as tests):**
1. Rescue matches ONLY against `_valid_names` (tools attached THIS turn). A tool suppressed by thin-router/specialist-first is NOT in `_valid_names` and therefore NOT rescued — no policy bypass.
2. Rescue fires only on an UNAMBIGUOUS single match of `bare_tool_name(called) == bare_tool_name(valid)`. Two candidates → no rescue (existing drop path).
3. Rewritten calls preserve `id` and `arguments`; construct a NEW `ToolCall` (no mutation).
4. Correction text: when the bad name's bare form matches a REGISTERED mcp tool that is not attached this turn, the message must instruct `delegate` and forbid inline retry (breaks the 3-strike loop under thin-router).

- [ ] **Step 1: Write failing tests** in `tests/runtime/test_hallucination_suffix_rescue.py`. Test the two helpers DIRECTLY (pure functions — no agent loop spin-up):

```python
from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.runtime.agent import (
    _suffix_rescue_tool_calls,
    _build_hallucination_correction,
)

REAL = "mcp_d6efb25b-a85a-4b78-ad73-6fec833fef72_upwork_get_job_details"
STALE = "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_job_details"

def test_bare_name_rescued_to_attached_mcp_name():
    calls = [ToolCall(id="1", name="upwork_get_job_details", arguments={"url": "x"})]
    fixed, log = _suffix_rescue_tool_calls(calls, {REAL, "search_tools"})
    assert fixed[0].name == REAL
    assert fixed[0].arguments == {"url": "x"} and fixed[0].id == "1"
    assert log == [f"upwork_get_job_details → {REAL}"]

def test_stale_uuid_prefix_rescued_to_current_uuid():
    calls = [ToolCall(id="1", name=STALE, arguments={})]
    fixed, _ = _suffix_rescue_tool_calls(calls, {REAL})
    assert fixed[0].name == REAL

def test_ambiguous_suffix_not_rescued():
    other = "mcp_11111111-1111-1111-1111-111111111111_upwork_get_job_details"
    calls = [ToolCall(id="1", name="upwork_get_job_details", arguments={})]
    fixed, log = _suffix_rescue_tool_calls(calls, {REAL, other})
    assert fixed[0].name == "upwork_get_job_details" and log == []

def test_valid_and_invented_names_untouched():
    calls = [ToolCall(id="1", name=REAL, arguments={}),
             ToolCall(id="2", name="apply_job", arguments={})]
    fixed, log = _suffix_rescue_tool_calls(calls, {REAL})
    assert [c.name for c in fixed] == [REAL, "apply_job"] and log == []

def test_correction_steers_to_delegate_when_registered_but_not_attached():
    class FakeRegistry:
        def list_names_by_prefix(self, prefix):
            return [REAL] if prefix == "mcp_" else []
    msg = _build_hallucination_correction(
        "upwork_get_job_details", {"search_tools", "delegate"}, FakeRegistry(),
    )
    assert "delegate" in msg
    assert "search_tools('details')" not in msg
```

- [ ] **Step 2: Run to verify FAIL:** `python3 -m pytest tests/runtime/test_hallucination_suffix_rescue.py -x -q` → ImportError on `_suffix_rescue_tool_calls`.

- [ ] **Step 3: Implement.** Add module-level helper near `_build_hallucination_correction` (import `bare_tool_name` from `lazyclaw.skills.tool_namespace`, `ToolCall` from `lazyclaw.llm.providers.base` — check existing imports first):

```python
def _suffix_rescue_tool_calls(
    tool_calls: list, valid_names: set[str],
) -> tuple[list, list[str]]:
    """Rewrite hallucinated tool names that bare-suffix-match EXACTLY ONE
    attached tool. The model often remembers a tool's bare name
    (`upwork_get_job_details`) or a stale `mcp_<old-uuid>_` prefix from
    history; exact-match-only validity bailed the 2026-07-02 Upwork
    proposal turn after 3 strikes. Matching is restricted to the tools
    attached THIS turn, so thin-router / specialist-first suppression is
    never bypassed. Returns (new_calls, ["old → new", ...]).
    """
    bare_to_valid: dict[str, list[str]] = {}
    for vn in valid_names:
        bare_to_valid.setdefault(bare_tool_name(vn), []).append(vn)
    rescued: list[str] = []
    new_calls = []
    for tc in tool_calls:
        if tc.name in valid_names:
            new_calls.append(tc)
            continue
        matches = bare_to_valid.get(bare_tool_name(tc.name), [])
        if len(matches) == 1:
            new_calls.append(
                ToolCall(id=tc.id, name=matches[0], arguments=tc.arguments)
            )
            rescued.append(f"{tc.name} → {matches[0]}")
        else:
            new_calls.append(tc)
    return new_calls, rescued
```

Wire it into the drop-check block AFTER the late-inject loop (after line ~4718, before `_valid_calls`):

```python
                    _rescued_calls, _rescue_log = _suffix_rescue_tool_calls(
                        response.tool_calls, _valid_names,
                    )
                    if _rescue_log:
                        logger.info(
                            "Suffix-rescued %d hallucinated tool name(s): %s",
                            len(_rescue_log), _rescue_log,
                        )
                        response = _LLMResp(
                            content=response.content,
                            model=response.model,
                            usage=response.usage,
                            tool_calls=_rescued_calls,
                        )
```

In `_build_hallucination_correction`, before the plain-hallucination return (line ~1891), add the registered-but-not-attached steer:

```python
    # Registered-but-not-attached: the tool EXISTS on a connected MCP
    # server but was not sent this turn (thin-router narrowing, channel
    # suppression). search_tools discovery can't make it callable — the
    # ONLY productive move is delegate. Without this steer the model
    # retried the same inline call to the hallucination cap (2026-07-02).
    if registry is not None:
        bad_bare = bare_tool_name(bad_name)
        mcp_names = registry.list_names_by_prefix("mcp_")
        if any(bare_tool_name(n) == bad_bare for n in mcp_names):
            return (
                f"[SYSTEM: The tool '{bad_bare}' exists but is NOT active in "
                f"your current toolset this turn. Do NOT call it inline "
                f"again. Call delegate(...) so a specialist executes it, or "
                f"run_background(...) for long work.]"
            )
```

(Place it after the `_parse_mcp_name` branch so wrong-uuid names — whose bare form also matches — get the same steer when their own server-prefix lookup came back empty. Reorder: compute the uuid-branch sibling list first; if `siblings_bare` is empty, fall through to this registered-but-not-attached check instead of returning "(none)".)

- [ ] **Step 4: Run new + neighboring suites:** `python3 -m pytest tests/runtime/test_hallucination_suffix_rescue.py tests/runtime/test_agent_force_dispatch.py tests/runtime/test_context_journal_filter.py -q`. Expected: PASS.

---

### Task 3: Parse-time scrub of stored MiniMax-M3 role pins

**Files:**
- Modify: `lazyclaw/llm/eco_router.py:262-314` (`_parse_eco_settings`)
- Test: create `tests/llm/test_eco_minimax_pin_scrub.py`

**Interfaces:**
- Consumes: `_parse_eco_settings(settings_json: str) -> EcoSettings` (module-private, imported directly in tests like other eco tests do).
- Produces: module constants `_MINIMAX_JUNK_PIN = "MiniMax-M3"`, `_MINIMAX_PIN_REPLACEMENT = "MiniMax-M2.7"`.

**Why:** The 2026-07-01 M3→M2.7 default revert was silently defeated by stored per-mode pins (`full_brain_model` etc. = "MiniMax-M3" in users.settings.eco). M3 is off the Token-Plan quota and has regressed tool-calling. Same class as the existing `_CLAUDE_JUNK_MODEL_NAMES` scrub at `eco_router.py:275`.

- [ ] **Step 1: Write failing tests** in `tests/llm/test_eco_minimax_pin_scrub.py`:

```python
import json
from lazyclaw.llm.eco_router import _parse_eco_settings

def _settings(eco: dict) -> str:
    return json.dumps({"eco": eco})

def test_m3_pins_coerced_to_m27_all_roles_and_modes():
    s = _parse_eco_settings(_settings({
        "mode": "full",
        "brain_model": "MiniMax-M3",
        "full_brain_model": "MiniMax-M3",
        "full_worker_model": "MiniMax-M3",
        "full_fallback_model": "MiniMax-M3",
        "minimax_brain_model": "MiniMax-M3",
        "hybrid_fallback_model": "MiniMax-M3",
    }))
    assert s.brain_model == "MiniMax-M2.7"
    assert s.full_brain_model == "MiniMax-M2.7"
    assert s.full_worker_model == "MiniMax-M2.7"
    assert s.full_fallback_model == "MiniMax-M2.7"
    assert s.minimax_brain_model == "MiniMax-M2.7"
    assert s.hybrid_fallback_model == "MiniMax-M2.7"

def test_m27_and_other_models_pass_through():
    s = _parse_eco_settings(_settings({
        "mode": "full",
        "full_brain_model": "MiniMax-M2.7",
        "full_fallback_model": "claude-haiku-4-5-20251001",
    }))
    assert s.full_brain_model == "MiniMax-M2.7"
    assert s.full_fallback_model == "claude-haiku-4-5-20251001"

def test_none_pins_stay_none():
    s = _parse_eco_settings(_settings({"mode": "minimax"}))
    assert s.minimax_brain_model is None
```

- [ ] **Step 2: Run to verify FAIL:** `python3 -m pytest tests/llm/test_eco_minimax_pin_scrub.py -x -q`.

- [ ] **Step 3: Implement** in `_parse_eco_settings`. Module-level constants next to `_DISABLED_MODES`:

```python
# MiniMax-M3 as a ROLE pin is junk: off the Token-Plan 5h quota (covers
# M2.7 / m2.7-highspeed only) + regressed tool-calling (narrates JSON
# plans instead of tool_use — 2026-07-02 Upwork bail). Stored pins
# silently defeated the 2026-07-01 M3→M2.7 default revert, so scrub at
# parse time like _CLAUDE_JUNK_MODEL_NAMES. M3 stays in the catalog for
# explicit non-role use (vision experiments); it just can't be a
# brain/worker/fallback pin.
_MINIMAX_JUNK_PIN = "MiniMax-M3"
_MINIMAX_PIN_REPLACEMENT = "MiniMax-M2.7"
```

Inside `_parse_eco_settings`, add beside `_clean_claude_model`:

```python
    def _clean_minimax_pin(value: object) -> str | None:
        if value == _MINIMAX_JUNK_PIN:
            logger.warning(
                "Stored model pin 'MiniMax-M3' coerced to 'MiniMax-M2.7' "
                "(off Token-Plan quota; regressed tool-calling)",
            )
            return _MINIMAX_PIN_REPLACEMENT
        return value if isinstance(value, str) and value else None
```

Wrap EVERY pin field in the `EcoSettings(...)` constructor with it: `brain_model`, `worker_model` (both arms of the `or`), `fallback_model`, all `hybrid_*`, `full_*`, `minimax_*` fields. The `claude_*` fields keep their existing `_clean_claude_model` wrapper (claude pins can never be "MiniMax-M3" AND valid, so no double-wrap needed).

- [ ] **Step 4: Run:** `python3 -m pytest tests/llm/test_eco_minimax_pin_scrub.py tests/llm/test_eco_list_models_mcp.py -q`. Expected: PASS.

---

### Task 4: `_resolve_models` rejects fallback == brain

**Files:**
- Modify: `lazyclaw/llm/eco_router.py:620-636` (`_resolve_models`)
- Test: create `tests/llm/test_resolve_models_self_fallback.py`

**Interfaces:**
- Consumes: `EcoRouter._resolve_models(settings: EcoSettings) -> dict[str, str]`; `get_mode_models(mode)` defaults from `model_registry`.
- Produces: module constant `_SAFE_FALLBACK_MODEL = "claude-haiku-4-5-20251001"` (check `model_registry.py` first — if a Haiku fallback constant already exists there, import and reuse it instead of defining a new one).

**Why:** With `full_fallback_model == full_brain_model == MiniMax-M3`, every "fallback" retried the exact model that just failed. A self-fallback is always a configuration bug.

- [ ] **Step 1: Write failing tests** in `tests/llm/test_resolve_models_self_fallback.py`. Instantiate `EcoRouter` the same way existing eco tests do (read `tests/llm/test_eco_list_models_mcp.py` for the cheapest construction pattern — if the router needs a config, build `EcoSettings` directly and call `EcoRouter._resolve_models` via a minimally-constructed instance or `EcoRouter.__new__(EcoRouter)`):

```python
from lazyclaw.llm.eco_router import EcoRouter, EcoSettings

def _resolve(settings):
    router = EcoRouter.__new__(EcoRouter)  # _resolve_models uses no instance state
    return router._resolve_models(settings)

def test_self_fallback_replaced_with_mode_default():
    s = EcoSettings(mode="full",
                    full_brain_model="MiniMax-M2.7",
                    full_fallback_model="MiniMax-M2.7")
    models = _resolve(s)
    assert models["brain"] == "MiniMax-M2.7"
    assert models["fallback"] != "MiniMax-M2.7"

def test_self_fallback_when_default_also_matches_uses_safe_constant():
    # Pin brain to the FULL mode's own default fallback so defaults can't help.
    from lazyclaw.llm.model_registry import get_mode_models
    default_fb = get_mode_models("full")["fallback"]
    s = EcoSettings(mode="full",
                    full_brain_model=default_fb,
                    full_fallback_model=default_fb)
    models = _resolve(s)
    assert models["fallback"] != default_fb

def test_distinct_pins_unchanged():
    s = EcoSettings(mode="full",
                    full_brain_model="MiniMax-M2.7",
                    full_fallback_model="claude-haiku-4-5-20251001")
    models = _resolve(s)
    assert models["fallback"] == "claude-haiku-4-5-20251001"
```

(Adjust `EcoSettings` kwargs to the dataclass's actual field names/defaults — it is defined at `eco_router.py:205`.)

- [ ] **Step 2: Run to verify FAIL:** `python3 -m pytest tests/llm/test_resolve_models_self_fallback.py -x -q`.

- [ ] **Step 3: Implement** at the end of `_resolve_models` (immutable — build a new dict):

```python
        resolved = {
            "brain": mode_brain or settings.brain_model or defaults["brain"],
            "worker": mode_worker or settings.worker_model or defaults["worker"],
            "fallback": mode_fallback or settings.fallback_model or defaults["fallback"],
        }
        if resolved["fallback"] == resolved["brain"]:
            # A fallback equal to the brain is a no-op safety net — the
            # 2026-07-02 Upwork bail ran brain=fallback=MiniMax-M3 so no
            # rescue was ever possible. Prefer the mode default; if the
            # brain IS the mode default fallback, use the safe constant.
            alt = defaults["fallback"]
            if alt == resolved["brain"]:
                alt = _SAFE_FALLBACK_MODEL
            logger.warning(
                "fallback_model == brain_model (%s) — self-fallback is a "
                "no-op; using %s instead", resolved["brain"], alt,
            )
            resolved = {**resolved, "fallback": alt}
        return resolved
```

- [ ] **Step 4: Run:** `python3 -m pytest tests/llm/test_resolve_models_self_fallback.py tests/llm/ -q`. Expected: PASS.

---

## Verification (after all tasks merge)

1. Full suite: `python3 -m pytest tests/ -q` (plus `python3 -m pytest tests/llm tests/runtime -q` focused).
2. `python3 -m py_compile` on the 3 touched files (make-rebuild pre-flight habit).
3. Deploy: `make rebuild` (lazyclaw runs from the BAKED image — restart alone reloads OLD code).
4. Live e2e: re-ask MiniMax (FULL mode, now M2.7) to send the Upwork proposal; watch `docker logs lazyclaw -f | grep -E "text-only|hallucinated|Hallucination cap|Suffix-rescued|retrying once"`. Success = proposal reaches `upwork_submit_proposal` (or its ask-back checkpoint) with no bail.
