# SDK-Owns-The-Loop (Structured Tool I/O) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give MODE_CLAUDE a structured tool-calling transport — the Claude Agent SDK drives the agentic loop with native `tool_use`/`tool_result` blocks and in-process tool dispatch — so the brain *emits* tool calls instead of narrating them, and grounds on real tool results, while keeping the $0 subscription.

**Architecture:** A new transport sub-mode `claude_transport="sdk_loop"` (alongside existing `sdk` string-form and `cli`), behind a feature flag so production stays on `sdk` until validated. A new `ClaudeSDKLoopProvider` uses the bidirectional `ClaudeSDKClient`: it registers lazyclaw skills as in-process `@tool` wrappers whose handlers dispatch to lazyclaw's existing `ToolExecutor` (preserving permissions, result-capping, outcome-recording, callbacks, and stuck-tracking per tool). Each agent-loop iteration = one provider call that runs a full SDK sub-loop and returns the final assistant text + `session_id`. Turn-level guards (F1, action-claim, AUTO-PROMOTE) stay at the agent level and re-enter the **same** SDK session via `resume=<session_id>` for corrections. Cancellation maps to `client.interrupt()`.

**Tech Stack:** Python 3.13, `claude_agent_sdk==0.1.81` (`ClaudeSDKClient`, `tool`, `create_sdk_mcp_server`), aiosqlite, pytest + unittest.mock. The SDK is NOT importable on the dev host — all unit tests MUST mock `claude_agent_sdk`; real verification happens in Docker (`make rebuild`).

**Key SDK facts (verified from installed source, v0.1.81):**
- `ClaudeSDKClient.connect()`, `.query(prompt, session_id="default")` (send a user message mid-session), `.receive_response()` (async-iterates Messages until and including `ResultMessage`), `.interrupt()`, `.disconnect()`, `async with` lifecycle. (`client.py:99,283,567,313,608,619`)
- `@tool(name, description, input_schema)` → `async def handler(args: dict) -> {"content": [{"type":"text","text":...}], "is_error": bool}`; runs in-process when the SDK calls it. (`__init__.py:165`)
- `create_sdk_mcp_server(name, version, tools=[...])` + `ClaudeAgentOptions(mcp_servers={name: server}, allowed_tools=[f"mcp__{name}__{tool}"])`. (`__init__.py:306`)
- Streaming input accepts ONLY user-role messages — assistant `tool_use`/`tool_result` history CANNOT be injected inline. Prior tool context re-enters only via `ClaudeAgentOptions.resume=<session_id>` (disk-backed session). (`types.py:1642`, `_internal/client.py:64`)
- `ResultMessage` carries `session_id`, `usage`, `total_cost_usd`, `is_error`. (`types.py`)

---

## File Structure

- **Create** `lazyclaw/llm/providers/claude_sdk_loop_provider.py` — the SDK-owns-loop provider (`ClaudeSDKLoopProvider`). One responsibility: drive one user turn through `ClaudeSDKClient`, dispatch tools in-process, return final `LLMResponse` (+ `session_id` in usage).
- **Create** `lazyclaw/llm/providers/sdk_tool_dispatch.py` — `build_dispatch_tools(tools_spec, dispatcher, name_map)`: turns the neutral tool schemas into SDK `@tool` wrappers whose handlers call `dispatcher(short_name, args)` and translate the string/sentinel result into the SDK `{"content":[...]}` shape. Pure-ish, unit-testable with a fake `tool`/`create_sdk_mcp_server`.
- **Create** `lazyclaw/llm/providers/sdk_dispatch_bridge.py` — `ToolExecutorDispatcher`: adapts lazyclaw's `ToolExecutor` (+ permissions, `_cap_tool_result`, outcome recording, `on_event` callbacks, stuck counter) into the `async dispatch(name, args) -> str` callable the wrappers need. This is the guard-preservation seam.
- **Modify** `lazyclaw/llm/eco_router.py` — add `sdk_loop` branch in the MODE_CLAUDE dispatch; thread `resume` session id + correction follow-ups.
- **Modify** `lazyclaw/runtime/agent_settings.py` (or wherever `claude_transport` is read) — accept `"sdk_loop"` value.
- **Modify** `lazyclaw/runtime/agent.py` — when transport is `sdk_loop`, treat each provider call as a completed sub-loop: run F1/action-claim on final text; on violation, re-invoke with `resume=session_id` + correction; AUTO-PROMOTE reads the tool-count surfaced in `usage`.
- **Test** `tests/llm/test_sdk_tool_dispatch.py`, `tests/llm/test_sdk_dispatch_bridge.py`, `tests/llm/test_sdk_loop_provider.py`, `tests/llm/test_sdk_loop_eco_router.py` — all mock `claude_agent_sdk`.
- **Docs** `DOCS.md` — add an "SDK-owns-loop transport" subsection; `MEMORY` pointer.

---

## Phase 0 — Feature flag + skeleton (no behavior change)

### Task 0: Recognize `sdk_loop` transport, default OFF

**Files:**
- Modify: `lazyclaw/llm/eco_router.py` (the MODE_CLAUDE transport switch, ~line 680)
- Test: `tests/llm/test_sdk_loop_eco_router.py`

- [ ] **Step 1: Write the failing test** — unknown transport falls back to existing `sdk`; `sdk_loop` routes to the new path (stubbed).

```python
# tests/llm/test_sdk_loop_eco_router.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from lazyclaw.llm.eco_router import EcoRouter

@pytest.mark.asyncio
async def test_sdk_loop_transport_routes_to_loop_provider(monkeypatch):
    router = EcoRouter.__new__(EcoRouter)  # bypass heavy __init__
    router._route_claude_sdk_loop = AsyncMock(return_value="LOOP")
    router._route_claude_sdk = AsyncMock(return_value="STRING")
    settings = MagicMock(mode="claude", claude_transport="sdk_loop")
    out = await EcoRouter._dispatch_claude_transport(router, [], "u1", settings=settings, role="brain")
    assert out == "LOOP"
    router._route_claude_sdk.assert_not_called()
```

- [ ] **Step 2: Run test, expect FAIL** — `_dispatch_claude_transport`/`_route_claude_sdk_loop` not defined.
Run: `python3 -m pytest tests/llm/test_sdk_loop_eco_router.py -q`

- [ ] **Step 3: Implement minimal routing.** Extract the existing MODE_CLAUDE transport `if/elif` into `_dispatch_claude_transport(self, messages, user_id, *, settings, role, **kwargs)`. Add:
```python
transport = getattr(settings, "claude_transport", "sdk") or "sdk"
if transport == "sdk_loop":
    return await self._route_claude_sdk_loop(messages, user_id, settings=settings, role=role, **kwargs)
if transport == "sdk":
    return await self._route_claude_sdk(messages, user_id, settings=settings, role=role, **kwargs)
# ... existing cli fallback unchanged ...
```
Add a stub `async def _route_claude_sdk_loop(self, *a, **k): raise NotImplementedError`.

- [ ] **Step 4: Run test, expect PASS.**
- [ ] **Step 5: Commit** — `feat(llm): add sdk_loop transport routing (flag, no behavior change)`.

### Task 0b: Allow `"sdk_loop"` in settings validation

**Files:** Modify wherever `claude_transport` is validated (grep `claude_transport`); Test: same file.

- [ ] **Step 1:** Test that `claude_transport="sdk_loop"` is accepted (not coerced to `sdk`).
- [ ] **Step 2:** Run, expect FAIL if there's an allowlist.
- [ ] **Step 3:** Add `"sdk_loop"` to the allowed set (default stays `"sdk"`).
- [ ] **Step 4/5:** Pass + commit `feat(settings): accept claude_transport=sdk_loop`.

---

## Phase 1 — In-process tool dispatch wrapper (the integration seam)

### Task 1: `build_dispatch_tools` — wrappers that call a dispatcher

**Files:**
- Create: `lazyclaw/llm/providers/sdk_tool_dispatch.py`
- Test: `tests/llm/test_sdk_tool_dispatch.py`

Design: each neutral tool schema → an SDK `@tool` whose handler awaits `dispatcher(short_name, args)` (returns a plain `str` result from lazyclaw) and wraps it as `{"content":[{"type":"text","text": result}]}`. `short_name` strips the `mcp_<uuid>_` prefix (reuse `_shorten_tool_name`); `name_map` maps short→full for the dispatcher to look up the real registry name.

- [ ] **Step 1: Write failing test** (mock `claude_agent_sdk.tool` + `create_sdk_mcp_server` with identity fakes so we can call the handler directly):
```python
# tests/llm/test_sdk_tool_dispatch.py
import pytest, sys, types
from unittest.mock import AsyncMock

@pytest.fixture
def fake_sdk(monkeypatch):
    mod = types.ModuleType("claude_agent_sdk")
    def tool(name, desc, schema):
        def deco(fn):
            fn._tool_name = name
            return fn
        return deco
    mod.tool = tool
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return mod

@pytest.mark.asyncio
async def test_wrapper_dispatches_and_wraps_result(fake_sdk):
    from lazyclaw.llm.providers.sdk_tool_dispatch import build_dispatch_tools
    dispatcher = AsyncMock(return_value="HELLO RESULT")
    spec = [{"type":"function","function":{"name":"mcp_uuid_whatsapp_read","description":"d","parameters":{"type":"object","properties":{}}}}]
    tools, name_map = build_dispatch_tools(spec, dispatcher)
    assert name_map["whatsapp_read"] == "mcp_uuid_whatsapp_read"
    out = await tools[0]({"limit": 5})
    dispatcher.assert_awaited_once_with("whatsapp_read", {"limit": 5})
    assert out == {"content":[{"type":"text","text":"HELLO RESULT"}]}

@pytest.mark.asyncio
async def test_wrapper_marks_error_result(fake_sdk):
    from lazyclaw.llm.providers.sdk_tool_dispatch import build_dispatch_tools
    dispatcher = AsyncMock(return_value="Error: boom")
    spec = [{"type":"function","function":{"name":"save_memory","description":"d","parameters":{"type":"object","properties":{}}}}]
    tools, _ = build_dispatch_tools(spec, dispatcher)
    out = await tools[0]({})
    assert out["is_error"] is True
```

- [ ] **Step 2: Run, expect FAIL** (module missing).
- [ ] **Step 3: Implement** `build_dispatch_tools(tools_spec, dispatcher) -> (list, dict)`:
```python
from lazyclaw.llm.providers.claude_sdk_provider import _shorten_tool_name

def build_dispatch_tools(tools_spec, dispatcher):
    from claude_agent_sdk import tool as sdk_tool
    sdk_tools, name_map = [], {}
    for spec in tools_spec:
        fn = spec.get("function", {})
        full = fn.get("name")
        if not full:
            continue
        short = _shorten_tool_name(full)
        name_map[short] = full
        schema = fn.get("parameters") or {"type": "object", "properties": {}}
        def _make(short_name):
            @sdk_tool(short_name, fn.get("description", ""), schema)
            async def _wrapper(args):
                result = await dispatcher(short_name, args or {})
                text = result if isinstance(result, str) else str(result)
                payload = {"content": [{"type": "text", "text": text}]}
                if isinstance(text, str) and text.startswith(("Error:", "Error executing")):
                    payload["is_error"] = True
                return payload
            return _wrapper
        sdk_tools.append(_make(short))
    return sdk_tools, name_map
```
(Note: bind `short` via `_make` to avoid the closure late-binding bug.)

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(llm): in-process SDK tool wrappers that dispatch to a callable`.

### Task 2: `ToolExecutorDispatcher` — preserve permissions/capping/outcome/callbacks/stuck

**Files:**
- Create: `lazyclaw/llm/providers/sdk_dispatch_bridge.py`
- Test: `tests/llm/test_sdk_dispatch_bridge.py`

Design: `ToolExecutorDispatcher(executor, registry, name_map, user_id, callback, cap_fn, stuck_state)` exposes `async def dispatch(short_name, args) -> str`. It (1) resolves `short_name`→full registry name via `name_map`, (2) increments a per-name stuck counter and returns a short-circuit string if the cap for that tool family is exceeded (reuse the existing stuck thresholds), (3) builds a `ToolCall(id=uuid, name=full, arguments=args)`, (4) `await executor.execute(tc, user_id, callback)`, (5) applies `cap_fn(result, full)` (the 50KB-channel / 4KB-default cap), (6) returns the capped string. Permissions + outcome recording already live inside `executor.execute`, so they are preserved for free.

- [ ] **Step 1: Write failing test** — dispatch resolves the name, calls executor, applies cap, returns string; stuck cap short-circuits after N.
```python
@pytest.mark.asyncio
async def test_dispatch_executes_and_caps(monkeypatch):
    from lazyclaw.llm.providers.sdk_dispatch_bridge import ToolExecutorDispatcher
    executor = AsyncMock(); executor.execute = AsyncMock(return_value="X"*9000)
    disp = ToolExecutorDispatcher(executor=executor, registry=MagicMock(),
        name_map={"save_memory":"save_memory"}, user_id="u1", callback=None,
        cap_fn=lambda r, n: r[:4000], stuck_thresholds=lambda n: 3)
    out = await disp.dispatch("save_memory", {"k":"v"})
    assert len(out) == 4000
    tc = executor.execute.call_args.args[0]
    assert tc.name == "save_memory" and tc.arguments == {"k":"v"}

@pytest.mark.asyncio
async def test_dispatch_stuck_short_circuits():
    from lazyclaw.llm.providers.sdk_dispatch_bridge import ToolExecutorDispatcher
    executor = AsyncMock(); executor.execute = AsyncMock(return_value="ok")
    disp = ToolExecutorDispatcher(executor=executor, registry=MagicMock(),
        name_map={"web_search":"web_search"}, user_id="u1", callback=None,
        cap_fn=lambda r,n: r, stuck_thresholds=lambda n: 2)
    await disp.dispatch("web_search", {}); await disp.dispatch("web_search", {})
    out = await disp.dispatch("web_search", {})  # 3rd > cap 2
    assert "too many" in out.lower() or "stuck" in out.lower()
    assert executor.execute.await_count == 2
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `ToolExecutorDispatcher` per the design above (import `ToolCall` from `providers.base`; generate ids with `uuid4().hex`).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(llm): ToolExecutorDispatcher bridges SDK wrappers to lazyclaw guards`.

---

## Phase 2 — The provider: drive one turn via ClaudeSDKClient

### Task 3: `ClaudeSDKLoopProvider.chat()` — connect, send, receive, collect

**Files:**
- Create: `lazyclaw/llm/providers/claude_sdk_loop_provider.py`
- Test: `tests/llm/test_sdk_loop_provider.py`

Design: `chat(messages, *, tools, dispatcher, cancel_token=None, resume=None, **kw)`:
1. `_split_leading_system(messages)` → native `system_prompt`; serialize only the trailing NEW user turn(s) since the last assistant (the SDK holds prior context via `resume`, or it's turn 1).
2. `build_dispatch_tools(tools, dispatcher)` + `create_sdk_mcp_server("lazyclaw", tools=...)`; `ClaudeAgentOptions(system_prompt=..., mcp_servers=..., allowed_tools=[...], disallowed_tools=_DISALLOWED_BUILT_INS, strict_mcp_config=True, permission_mode="bypassPermissions", env={...}, resume=resume)`.
3. `async with ClaudeSDKClient(options) as client: await client.query(user_text); async for msg in client.receive_response(): accumulate TextBlock; on ResultMessage capture session_id+usage`. If `cancel_token` trips, `await client.interrupt()` and break.
4. Return `LLMResponse(content=final_text, model="claude-sdk-loop", usage={...,"session_id":sid,"tool_calls_executed":dispatcher.count}, tool_calls=None)` — tool_calls is None because the SDK already executed them in-loop.

- [ ] **Step 1: Write failing test** with a fake `ClaudeSDKClient` (async ctx mgr) that yields a TextBlock-bearing AssistantMessage then a ResultMessage; assert provider returns the text + session_id, and that the registered tool handler, when invoked by the fake, calls the dispatcher. (Mock the whole `claude_agent_sdk` module: `ClaudeSDKClient`, `ClaudeAgentOptions`, `create_sdk_mcp_server`, `AssistantMessage`, `TextBlock`, `ToolUseBlock`, `ResultMessage`, `tool`.)
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the provider per design (reuse `_split_leading_system`, `_DISALLOWED_BUILT_INS`, env-stripping from `claude_sdk_provider`).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(llm): ClaudeSDKLoopProvider drives one turn via ClaudeSDKClient`.

### Task 4: Cancellation via `interrupt()`

- [ ] **Step 1:** Test: a pre-tripped `cancel_token` makes the provider call `client.interrupt()` and return partial/empty content without error.
- [ ] **Step 2-4:** Implement the cancel check inside the receive loop; pass/commit `feat(llm): map cancel_token to SDK interrupt() in loop provider`.

---

## Phase 3 — Wire eco_router + agent-level guards (resume-based corrections)

### Task 5: `_route_claude_sdk_loop` builds the dispatcher and calls the provider

**Files:** Modify `lazyclaw/llm/eco_router.py`; Test: `tests/llm/test_sdk_loop_eco_router.py`.

- [ ] **Step 1:** Test that `_route_claude_sdk_loop` constructs a `ToolExecutorDispatcher` from the agent-provided executor/registry/callback (passed via kwargs) and forwards `resume` + `tools` to the provider; returns the provider's `LLMResponse`.
- [ ] **Step 2-4:** Implement: pull `executor`, `registry`, `callback`, `cancel_token`, `resume` from kwargs (agent must pass them — see Task 6); build dispatcher; instantiate/reuse `ClaudeSDKLoopProvider`; return its response. Map model via the existing `_resolve_claude_cli_model`. Commit `feat(llm): eco_router sdk_loop route wires dispatcher + resume`.

### Task 6: Agent loop integration (only when transport==sdk_loop)

**Files:** Modify `lazyclaw/runtime/agent.py`; Test: extend `tests/runtime/` with a mocked eco_router.

Design: when `sdk_loop` is active, the provider returns the FINAL text with `tool_calls=None`, so the existing outer loop naturally treats it as terminal (one iteration). The agent must (a) pass `executor`, `registry`, `callback`, `cancel_token` into `eco_router.chat(..., executor=self.executor, ...)`, (b) capture `response.usage["session_id"]`, (c) run the existing F1 / action-claim / phase-2 checks on `response.content` (already provider-agnostic — verified), and (d) on a violation, re-invoke `eco_router.chat` with `resume=session_id` + a correction user message instead of the current message-list retry. AUTO-PROMOTE reads `response.usage["tool_calls_executed"]` to decide promotion.

- [ ] **Step 1:** Test (mock eco_router): in sdk_loop mode, agent forwards `executor`/`registry`/`callback`; on an F1 violation it re-calls eco_router with `resume=<sid>`.
- [ ] **Step 2-4:** Implement behind `if _transport == "sdk_loop":` branches that DO NOT touch the existing path. Commit `feat(runtime): agent integrates sdk_loop (resume-based F1 retry, usage-based auto-promote)`.

---

## Phase 4 — Docker verification + flip default

### Task 7: Real-SDK integration smoke (Docker only)

- [ ] **Step 1:** Add `tests/integration/test_sdk_loop_smoke.py` guarded by `pytest.importorskip("claude_agent_sdk")` so it no-ops on the host and runs in Docker. It drives a real one-turn call with a trivial echo tool and asserts the tool handler fired in-process and the final text is non-empty.
- [ ] **Step 2:** `make rebuild` then `docker compose exec lazyclaw python -m pytest tests/integration/test_sdk_loop_smoke.py -q`. Expected: PASS, log shows the @tool handler dispatched.
- [ ] **Step 3:** Manual Docker session on `claude_transport="sdk_loop"`: "check my whatsapp" → confirm logs show `tool_calls_executed>=1`, a real `whatsapp_read` execution, a quote-block reply (F1 satisfied), and "fan agents"/dispatch requests actually emit `run_background`/`dispatch_subagents` (not narrated). Compare against the same prompts on `sdk`.
- [ ] **Step 4:** If clean, flip the default `claude_transport` to `sdk_loop` (keep `sdk` reachable as fallback). Commit `feat: default MODE_CLAUDE to sdk_loop transport after Docker validation`.
- [ ] **Step 5:** Update `DOCS.md` (transport subsection) + add a MEMORY pointer; supersede the string-form notes in [[feedback_sdk_dispatch_refusal]].

---

## Rollback / Safety

- The entire feature is gated by `claude_transport`. If `sdk_loop` misbehaves in Docker, set it back to `sdk` (no redeploy of code needed — it's a settings value) and the proven string-form path is intact.
- No existing file's behavior changes until Task 6, and even there the changes are inside `if _transport == "sdk_loop":` branches.
- `SDKUnavailable`/connection errors in the loop provider should fall back to `_route_claude_sdk` (string form) just like the current `sdk`→`cli` fallback, so a broken loop transport degrades rather than hard-fails.

## Self-Review notes
- Guards covered: permissions+outcome (inside `executor.execute`, Task 2), result-capping (`cap_fn`, Task 2), stuck (counter, Task 2), callbacks/tool-visibility (`executor.execute`'s `callback`, Task 2), cancellation (`interrupt`, Task 4), F1/action-claim/phase-2 (agent-level on final text + resume retry, Task 6), AUTO-PROMOTE (usage tool-count, Task 6). 
- Open risk to validate in Docker (Task 7): whether `resume` reliably reloads tool history mid-turn fast enough, and whether `receive_response()` surfaces partial text for streaming callbacks (if not, streaming UX regresses to per-turn — acceptable for v1).
