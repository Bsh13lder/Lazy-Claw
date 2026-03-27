# ADR-001: LazyClaw Deep Audit — Findings, Competitor Analysis & Orchestration Plan

**Status:** Proposed
**Date:** 2026-03-27
**Deciders:** BLCK (project owner)

---

## Context

LazyClaw is a 17-phase, 83-skill, E2E encrypted AI agent platform competing with OpenClaw. Before moving into testing/debugging and TUI improvements, we ran a deep audit: 6 parallel research agents analyzed the entire codebase (runtime, teams, LLM routing, crypto/DB/security, browser/MCP/Telegram/TUI) plus online competitor research.

This document consolidates ALL findings into a single architecture decision record.

---

## 1. Competitor Landscape

### OpenClaw (Main Competitor)
- **Stars:** ~337K GitHub (fastest growth ever)
- **Stack:** TypeScript, 21+ messaging channels
- **Strengths:** Channel-first, local-first, heartbeat daemon
- **Weaknesses:** NO encryption (plaintext everything), NO MCP support, NO tool registry, TypeScript-only
- **Gap LazyClaw fills:** E2E encryption, Python-native, native MCP, smart tool selection

### Other Players

| Platform | Stack | Multi-Agent | MCP | Encryption | Stars |
|----------|-------|-------------|-----|------------|-------|
| CrewAI | Python | Yes (roles) | No | No | ~100K devs |
| AutoGen (MS) | Python | Yes | No | No | ~40K |
| LangGraph | Python | Yes (graph) | Partial | No | ~30K |
| OpenHands | Python | Yes | No | No | ~65K, $18.8M funded |
| Mastra | TypeScript | Yes | Yes | No | $13M YC funded |
| MetaGPT | Python | Yes (SOP) | No | No | ~45K |
| SWE-Agent | Python | Single | No | No | NeurIPS paper |

### LazyClaw's Unique Position
1. **ONLY platform with E2E encryption** (AES-256-GCM) — every competitor is plaintext
2. **Python-native WITH channels** — OpenClaw has channels but is TypeScript; Python frameworks have no channels
3. **Native MCP client + server** — first-class, not bolted on
4. **70-88% token savings** via smart tool selection — no competitor does this
5. **Unified platform** — channels + crypto + registry + browser + teams in one box

---

## 2. Audit Findings — All Systems

### Summary by Severity

| Severity | Runtime | Teams | LLM/ECO | Security | Browser/MCP/TG | Total |
|----------|---------|-------|---------|----------|----------------|-------|
| CRITICAL | 4 | 1 | 1 | 1 | 0 | **7** |
| HIGH | 6 | 2 | 1 | 6 | 5 | **20** |
| MEDIUM | 5 | 2 | 4 | 3 | 9 | **23** |
| LOW | 5 | 2 | 4 | 1 | 4 | **16** |
| **Total** | **20** | **7** | **10** | **11** | **18** | **66** |

---

### CRITICAL Issues (Fix Before Anything Else)

#### C1. Race Condition in Global Cache
- **File:** `runtime/context_builder.py:18-24, 83-92`
- **Problem:** `_capabilities_cache` and `_mcp_cache` accessed without asyncio.Lock. Concurrent requests cause stale/corrupt tool schemas
- **Fix:** Add `asyncio.Lock()` around all cache reads/writes

#### C2. Indentation Bug — Favorite MCP Tools Inside Wrong Block
- **File:** `runtime/agent.py:699-711`
- **Problem:** "Include favorite MCP tools" block is indented inside `if _matched_channels:` — favorite tools ONLY injected when message mentions WhatsApp/Instagram/Email
- **Fix:** Un-indent lines 699-711 to correct scope

#### C3. Unsafe `locals()` Call
- **File:** `runtime/agent.py:738`
- **Problem:** `locals().get("_wants_visible", False)` is unreliable in CPython
- **Fix:** Use explicit dict `_tool_flags = {"wants_visible": False}`

#### C4. Unhandled `asyncio.ensure_future()` for Cancellation
- **File:** `runtime/agent.py:322`
- **Problem:** Fire-and-forget cancel with no error callback — zombie tasks possible
- **Fix:** Use `create_task()` with `add_done_callback()` for error logging

#### C5. Duplicate Browser Learning Saves (Race Condition)
- **File:** `teams/executor.py:68-82` AND `teams/delegate.py:154-165`
- **Problem:** Same learnings written to DB twice per browser specialist, potential key collisions
- **Fix:** Remove learning save from delegate.py, keep only in executor

#### C6. Model Version Mismatch — Opus Pricing Wrong
- **File:** `llm/model_registry.py:48` vs `llm/pricing.py:16-21`
- **Problem:** `"claude-opus-4-6"` (bare name) doesn't match pricing table `"claude-opus-4-6-20250625"` — cost tracking falls back to gpt-5-mini rates (50x wrong)
- **Fix:** Align model IDs between registry and pricing

#### C7. Exposed Credentials in .env
- **File:** `.env` lines 1, 8
- **Problem:** SERVER_SECRET and Stripe API key in plaintext. If repo is accessible = complete system compromise
- **Fix:** Rotate secrets immediately, clean git history, enforce .gitignore

---

### HIGH Issues (Fix This Week)

| # | System | File | Problem |
|---|--------|------|---------|
| H1 | Runtime | agent.py:765 | Fire-and-forget lesson extraction, no error callback |
| H2 | Runtime | agent.py:693-696 | Tool deduplication happens too late, survival tools shadowed |
| H3 | Runtime | agent.py:1163 | Plaintext tool results in replay logs (encryption bypass) |
| H4 | Runtime | stuck_detector.py:160 | Division by zero in similarity check |
| H5 | Runtime | stuck_detector.py:165-197 | Fragile similarity detection for stuck loops |
| H6 | Runtime | context_builder.py:227 | Broad `except Exception` swallows real MCP errors |
| H7 | Teams | runner.py + executor.py | Orphaned tab context — tabs never released on timeout/cancel |
| H8 | Teams | teams/lead.py | 420 lines deprecated TeamLead class still in codebase |
| H9 | LLM | eco_router.py:438-469 | Brain role always calls paid API even in ECO mode |
| H10 | Security | gateway/auth.py:124 | 30-day session timeout too long for encrypted platform |
| H11 | Security | routes/vault.py | Missing permission enforcement on vault HTTP endpoints |
| H12 | Security | routes/vault.py | No rate limiting on vault operations |
| H13 | Security | gateway/app.py:52 | SERVER_SECRET only checks length, not entropy |
| H14 | Security | vault.py + routes | Missing `vault_get` skill and GET endpoint |
| H15 | Security | db/schema.sql | Missing indexes on job_queue, site_memory, channel_bindings |
| H16 | Browser | cdp_backend.py:55-105 | Chrome auto-launch race condition (multiple launches) |
| H17 | Browser | tab_manager.py:220-238 | Waiter timeout leaks futures, dead futures accumulate |
| H18 | MCP | client.py:243-257 | Zombie process prevention is best-effort only |
| H19 | MCP | manager.py:114-142 | Idle disconnect race condition (stale timer fires) |
| H20 | MCP | bridge.py:60-77 | OAuth refresh failure not caught, original error lost |

---

### MEDIUM Issues (Fix Next 2 Weeks)

| # | System | Problem |
|---|--------|---------|
| M1 | Runtime | `tool_call_id` stored in wrong column |
| M2 | Runtime | Task result truncation without indicator |
| M3 | Runtime | Non-atomic `_delegate_registered` flag |
| M4 | Runtime | Undocumented `is_background` flag, fragile recursion prevention |
| M5 | Runtime | Fragile approval response parsing with `.index()` |
| M6 | Teams | Timeout errors lose context (no step info) |
| M7 | Teams | Duplicate site knowledge queries (no cache) |
| M8 | LLM | Streaming fallback returns user message, not model output |
| M9 | LLM | Free provider cascade doesn't track rate limit hits |
| M10 | LLM | Model override bypasses budget enforcement |
| M11 | LLM | Single MLX server breaks dual-model setup |
| M12 | Security | Encryption keys use fixed salt instead of per-user salt |
| M13 | Security | Missing indexes on daily_logs table |
| M14 | Security | Legacy SHA256 password code still present |
| M15 | Browser | Release/acquire race in TabManager |
| M16 | Browser | CDP idle timeout defined but never implemented |
| M17 | Browser | Port conflict creates zombie Chrome processes |
| M18 | Browser | Silent fallback on JS extractor failure |
| M19 | MCP | Parallel startup doesn't handle partial failures |
| M20 | Telegram | Status message never deleted on agent errors |
| M21 | Telegram | Large file sends may hang (no timeout wrapper) |
| M22 | TUI | Widget state desync on fast events |
| M23 | MCP | OAuth token refresh failure masks original 401 |

---

### Dead Code to Remove

| Location | What | Lines |
|----------|------|-------|
| `teams/lead.py` | Deprecated TeamLead class | 420 lines |
| `teams/delegate.py:218-312` | `_maybe_research_site()` never called | 95 lines |
| `agent.py:455` | Unnecessary `import asyncio as _aio` rename | scattered |
| `llm/pricing.py` | Deprecated model entries (opus-4, opus-4.5) | ~10 lines |
| `llm/eco_router.py:73` | Unused `ROLE_FALLBACK` constant | 1 line |
| `config.py` | Playwright references (CDP replaced it) | scattered |

---

## 3. Orchestration Quality Assessment

### What Works Well
- Clean 3-role separation (brain/worker/fallback) in ECO routing
- Specialist isolation with filtered tool sets
- CancellationToken for cooperative cancellation
- Fire-and-forget recorder (non-blocking)
- Smart tool selection (70-88% token savings) is a genuine competitive advantage
- Delegate tool saves 1-2 LLM calls per delegation vs old TeamLead approach
- PBKDF2 LRU cache and DB connection pool are excellent optimizations

### What Needs Work
- **No unified resource lifecycle** — tabs, specialists, MCP connections all cleaned up differently
- **Race conditions everywhere** — global cache, Chrome launch, tab manager, idle timers
- **Fire-and-forget pattern overused** — lesson extraction, browser learning, cancel tasks all lack error callbacks
- **Brain always paid in ECO** — contradicts the $0 promise (Haiku is cheap but not free)
- **Encryption gaps** — tool results in replay logs, vault HTTP endpoints bypass permissions

---

## 4. Decision: Remediation Priority

### Phase A: Critical Fixes (Before Any Testing) — ~2 days

| # | Fix | Est. Time |
|---|-----|-----------|
| C7 | Rotate .env secrets, clean git | 30 min |
| C1 | Add asyncio.Lock to context_builder cache | 1 hour |
| C2 | Fix indentation on favorite MCP tools block | 15 min |
| C3 | Replace locals() with explicit flag dict | 15 min |
| C4 | Add error callbacks to all fire-and-forget tasks | 1 hour |
| C5 | Remove duplicate learning save from delegate.py | 15 min |
| C6 | Align model IDs in registry ↔ pricing | 30 min |

### Phase B: High Priority (This Week) — ~3 days

| # | Fix | Est. Time |
|---|-----|-----------|
| H7 | Tab release on timeout/cancel (finally blocks) | 2 hours |
| H8 | Delete deprecated teams/lead.py | 10 min |
| H3 | Encrypt tool results before replay recording | 1 hour |
| H4-5 | Fix stuck detector math + similarity | 1 hour |
| H9 | Allow local brain route in ECO mode | 2 hours |
| H10-14 | Security fixes (sessions, vault, rate limits) | 4 hours |
| H15 | Add missing DB indexes | 30 min |
| H16 | Add connection lock to CDPBackend | 1 hour |
| H18-19 | MCP zombie prevention + idle timer versioning | 2 hours |

### Phase C: Medium Priority (Next 2 Weeks)

All 23 MEDIUM issues, dead code cleanup, and architectural improvements.

### Phase D: TUI Improvements (After Stabilization)

Based on our mockup planning session:
- Grid card layout (2 wide for complex, 3 for simple)
- Started/finished/duration timestamps on cards
- Step number + name display
- Watcher details (interval, last check, next check, prompt)
- Settings panel toggle (key `3`)
- Cancel/stop controls (key `x` on card + `/cancel` command)
- Telegram mirror of all controls

---

## 5. Consequences

### What Becomes Easier
- Production deployment (after critical fixes)
- Debugging (with proper error callbacks and logging)
- Cost tracking (with correct model pricing)
- Resource management (with unified lifecycle)

### What Becomes Harder
- Nothing — all changes are fixes, not new features

### What We'll Need to Revisit
- ECO brain routing (local vs paid trade-off needs testing)
- Per-user encryption salt migration (existing data needs re-encryption)
- MCP parallel startup error handling across all server configs
- Tab manager concurrency model (may need redesign if 5+ specialists)

---

## 6. Action Items

1. [ ] **Phase A:** Fix all 7 CRITICAL issues (block everything else)
2. [ ] **Phase B:** Fix 20 HIGH issues (focus on security + race conditions)
3. [ ] **Phase C:** Fix 23 MEDIUM issues + remove dead code
4. [ ] **Phase D:** TUI improvements per mockup design
5. [ ] **Phase E:** Full integration testing after all fixes
6. [ ] Re-audit after Phase B to verify fixes didn't introduce new issues
