# ADR-001: LazyClaw Deep Audit — Verified Findings, Competitor Analysis & Brain Orchestration Plan

**Status:** Proposed
**Date:** 2026-03-27
**Deciders:** BLCK (project owner)

---

## Context

LazyClaw is a 17-phase, 83-skill, E2E encrypted AI agent platform. Before testing/debugging and TUI improvements, we ran a comprehensive audit: 4 parallel verification agents analyzed every system (runtime, teams, LLM/ECO routing, security/crypto, browser/MCP/Telegram/TUI) plus competitor research online.

Every finding below was **verified against actual source code** — line numbers, exact code, confirmed behavior. False positives were removed.

---

## 1. Competitor Landscape

### OpenClaw (Main Competitor)
- **Stars:** ~337K GitHub (fastest-growing open-source AI agent)
- **Stack:** TypeScript, 21+ messaging channels, local-first
- **Strengths:** Channel-first design, heartbeat daemon, massive community
- **Critical Weaknesses:** NO encryption (all plaintext), NO MCP support, NO tool registry, TypeScript-only

### Market Overview

| Platform | Stack | Multi-Agent | MCP | E2E Encryption | Stars/Funding |
|----------|-------|-------------|-----|----------------|---------------|
| **OpenClaw** | TypeScript | No | No | **No** | ~337K stars |
| **CrewAI** | Python | Yes (roles) | No | **No** | ~100K devs |
| **AutoGen** (Microsoft) | Python | Yes | No | **No** | ~40K stars |
| **LangGraph** | Python | Yes (graph) | Partial | **No** | ~30K stars |
| **OpenHands** | Python | Yes | No | **No** | 65K stars, $18.8M funded |
| **Mastra** | TypeScript | Yes | Yes | **No** | $13M YC funded |
| **MetaGPT** | Python | Yes (SOP) | No | **No** | ~45K stars |
| **LazyClaw** | **Python** | **Yes (teams)** | **Native** | **AES-256-GCM** | Open-source |

### LazyClaw's Unique Advantages (No Competitor Has These)
1. **ONLY E2E encrypted platform** — AES-256-GCM on all user content. Every single competitor stores everything in plaintext.
2. **Python-native WITH channels** — OpenClaw has channels but is TypeScript. Python frameworks have zero channel support.
3. **Native MCP client + server** — First-class, not bolted on. Parallel startup, bridge to skill registry.
4. **70-88% token savings** via smart tool selection per message category — unique to LazyClaw.
5. **Unified platform** — channels + crypto + registry + browser + teams + replay in one box.

### Market Size
- AI agent market: $52.6B by 2030 (60% CAGR)
- LazyClaw TAM (privacy-sensitive segment): $2.6-5.26B (5-10%)

---

## 2. Verified Findings — All Systems

### Severity Summary (After Verification)

| Severity | Count | Status |
|----------|-------|--------|
| **CRITICAL** | 5 | Must fix before any deployment |
| **HIGH** | 14 | Fix this week |
| **MEDIUM** | 15 | Fix next 2 weeks |
| **LOW / Cleanup** | 10 | Nice to have |
| **False Positives Removed** | 3 | Telegram status cleanup was correct, vault GET absence is by design, replay encryption is working |
| **TOTAL VERIFIED** | **44** | |

---

## 3. CRITICAL Issues (Verified With Exact Code)

### C1. Race Condition in Global Cache — No asyncio.Lock
**File:** `runtime/context_builder.py` lines 18-24, 83-92
**Verified Code:**
```python
# Lines 18-24 — UNPROTECTED GLOBALS
_capabilities_cache: str = ""
_capabilities_time: float = 0.0
_mcp_cache: list[str] = []
_mcp_cache_time: float = 0.0

# Lines 83-92 — READ-CHECK-WRITE WITHOUT LOCK
async def _build_capabilities_cached(...):
    global _capabilities_cache, _capabilities_time
    now = time.monotonic()
    if _capabilities_cache and (now - _capabilities_time) < _CAPABILITIES_TTL:
        return _capabilities_cache
    result = await _build_capabilities_section(...)  # ← Concurrent calls race here
    _capabilities_cache = result
    _capabilities_time = now
```
**Impact:** Concurrent requests get stale/corrupt tool schemas. Two users hitting the agent simultaneously can get each other's tool lists.
**Fix:** Add `_cache_lock = asyncio.Lock()` and wrap the read-check-write in `async with _cache_lock`.

---

### C2. Wrong Indentation — Favorite MCP Tools Only Injected for Channel Messages
**File:** `runtime/agent.py` lines 698-711
**Verified Code:**
```python
698→            # Include favorite MCP tools
699→                _fav_prefixes = tuple(    # ← INDENTED 4 EXTRA SPACES
700→                    f"mcp_{sid}_" for sid in _favorite_server_ids
701→                    if sid in _active_clients
702→                )
703→                _existing_names = {t.get("function", {}).get("name") for t in tools}
704→                if _fav_prefixes:
705→                    for tool_info in self.registry.list_mcp_tools():
706→                        ...
```
**Impact:** Favorite MCP tools are ONLY injected when message mentions WhatsApp/Instagram/Email channels. For normal messages, favorite MCP servers are invisible to the agent.
**Fix:** Un-indent lines 699-711 by 4 spaces to correct scope level.

---

### C3. Model ID Mismatch — Opus Pricing 33x Wrong
**File:** `llm/model_registry.py` line 48 vs `llm/pricing.py` lines 16-21
**Verified:**
- Registry: `"fallback": "claude-opus-4-6"` (bare name)
- Pricing table: `"claude-opus-4-6-20250625"` (timestamped — **different key**)
- No entry for `"claude-opus-4-6"` exists in pricing
- Fallback pricing = gpt-5-mini rates ($0.00015/K) vs actual Opus ($0.005/K) = **33x underestimate**

**Impact:** Every Opus fallback call is tracked as costing $0.00015 instead of $0.005 per 1K tokens. Budget enforcement is broken. Users think they're spending $0.01 when they're actually spending $0.33.
**Fix:** Add `"claude-opus-4-6"` entry to pricing.py OR change registry to use timestamped ID.

---

### C4. Brain ALWAYS Routes to Paid API — Even in ECO Mode
**File:** `llm/eco_router.py` lines 455-469
**Verified Code:**
```python
async def _route_brain(self, messages, user_id, settings, models, **kwargs):
    """Brain: always paid API (Haiku in ECO/HYBRID, Sonnet in FULL)."""
    brain_name = models["brain"]
    return await self._route_paid(        # ← ALWAYS paid, never local
        messages, user_id, brain_name,
        reason=f"{settings.mode}: brain -> {brain_name}",
        **kwargs,
    )
```
**Impact:** ECO mode promises $0/day but the brain role (used for EVERY user message) unconditionally calls paid Haiku API. Users are billed for every single interaction in "ECO" mode.
**Fix:** Add local brain route — check if MLX/Ollama brain is available first, fall back to paid only if local unavailable.

---

### C5. Exposed Production Credentials in .env
**File:** `.env`
**Verified:** Contains plaintext: SERVER_SECRET, OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY, STRIPE_SECRET_KEY (live!), GROQ_API_KEY, ANTHROPIC_API_KEY
**Note:** .env IS in .gitignore (line 6), so it's not in git history. But the file on disk has live production keys.
**Impact:** If anyone accesses the machine, ALL API accounts are compromised. SERVER_SECRET leak means all vault encryption is reversible.
**Fix:** Rotate ALL keys immediately. Use a secrets manager for production.

---

## 4. HIGH Issues (Verified)

| # | System | File:Line | Problem | Verified |
|---|--------|-----------|---------|----------|
| H1 | Runtime | agent.py:738 | `locals().get("_wants_visible", False)` — fragile, CPython-dependent | ✓ Exact code confirmed |
| H2 | Runtime | agent.py:322 | `asyncio.ensure_future()` cancel with no error callback — zombie tasks | ✓ Confirmed |
| H3 | Runtime | agent.py:765 | Fire-and-forget lesson extraction, no error callback | ✓ Confirmed |
| H4 | Runtime | stuck_detector.py:160 | Division by zero risk if both strings empty (guard exists but fragile) | ✓ Confirmed |
| H5 | Teams | executor.py:68 + delegate.py:154 | DUPLICATE browser learning saves — same data written twice to DB | ✓ Exact duplicate confirmed |
| H6 | Teams | lead.py | 419 lines deprecated TeamLead class — dead code still importable | ✓ Confirmed "DEPRECATED" header |
| H7 | Teams | runner.py + executor.py | Orphaned tabs — never released on timeout/cancel (no finally block) | ✓ Confirmed |
| H8 | Security | auth.py:124 | 30-day session timeout (720 hours) — excessive for encrypted platform | ✓ `expires_hours=720` confirmed |
| H9 | Security | routes/vault.py | NO rate limiting on vault endpoints (auth routes have limiters, vault doesn't) | ✓ Zero limiter references |
| H10 | Security | routes/vault.py | NO permission checker integration — vault is always-allow for authenticated users | ✓ Zero permission references |
| H11 | Security | app.py:52 | SERVER_SECRET only checks `len() < 32` — no entropy validation | ✓ Exact code confirmed |
| H12 | Security | schema.sql | Missing indexes on job_queue, site_memory, channel_bindings, daily_logs | ✓ Tables have no indexes |
| H13 | Browser | cdp_backend.py:55-106 | Chrome auto-launch race — multiple concurrent launches possible | ✓ No lock on `_ensure_connected()` |
| H14 | MCP | manager.py:114-160 | Idle disconnect race — tool call during timeout window causes use-after-disconnect | ✓ Race scenario confirmed |

---

## 5. MEDIUM Issues (Verified)

| # | System | Problem |
|---|--------|---------|
| M1 | Runtime | Tool deduplication happens too late, survival tools can shadow MCP tools |
| M2 | Runtime | Non-atomic `_delegate_registered` flag |
| M3 | Runtime | Broad `except Exception` in context_builder swallows real MCP errors |
| M4 | LLM | `ROLE_FALLBACK` constant defined but never used (dead code) |
| M5 | LLM | `task_overrides` in eco_settings defined but never read (dead code) |
| M6 | LLM | No thinking mode user setting — hardcoded in MLX provider |
| M7 | LLM | Model override bypasses budget enforcement |
| M8 | Security | Fixed salt `b"lazyclaw-server-key-v1"` for ALL server-side encryption — acceptable only if SERVER_SECRET is strong |
| M9 | Browser | Tab waiter timeout leaks futures — `cancel()` never called on timeout |
| M10 | Browser | CDP idle timeout defined but never implemented |
| M11 | Browser | Port conflict creates zombie Chrome processes on restart |
| M12 | MCP | Zombie process prevention relies on frame inspection — brittle |
| M13 | MCP | OAuth refresh only catches 401, misses 403/500/connection errors |
| M14 | MCP | Parallel startup doesn't handle partial failures gracefully |
| M15 | TUI | RequestCard widget state desync on rapid concurrent events |

---

## 6. Dead Code to Remove

| Location | What | Lines | Verified |
|----------|------|-------|----------|
| `teams/lead.py` | Entire file — deprecated TeamLead class | 419 lines | ✓ "DEPRECATED" in docstring |
| `skills/builtin/delegate.py:218-312` | `_maybe_research_site()` — never called | ~95 lines | ✓ "No longer called automatically" in comment |
| `llm/eco_router.py:73` | `ROLE_FALLBACK` constant — defined, never used | 1 line | ✓ Grep confirms zero usage |
| `llm/eco_settings.py:49` | `task_overrides` field — defined, never read | 1 line | ✓ Never accessed |
| `config.py:108,137` | Playwright references in comments — CDP replaced it | 2 comments | ✓ Misleading comments |

---

## 7. What Works Well (Strengths to Preserve)

These architectural decisions are **correct and should not be changed:**

1. **Smart tool selection** (70-88% token savings) — genuine competitive advantage, no competitor has this
2. **Delegate tool** replacing TeamLead — saves 1-2 LLM calls per delegation, cleaner architecture
3. **PBKDF2 LRU cache** (420ms → 0ms per message) — excellent optimization
4. **DB connection pool** (14ms → 0.2ms per query) — critical for responsiveness
5. **CancellationToken** for cooperative cancellation — proper pattern
6. **Fire-and-forget trace recording** — non-blocking, doesn't slow agent
7. **Per-user data isolation** — ALL queries scoped by user_id, no cross-user leaks
8. **Tool results encrypted in replay** — verified, properly encrypted before DB storage
9. **Telegram status cleanup** — verified, correctly deleted in both success AND error paths
10. **Vault has no GET endpoint** — by design, credentials never exposed via HTTP

---

## 8. Decision: Brain Orchestration Improvements

### Current Architecture (What Needs Work)

```
User Message
    ↓
Main Agent (brain role — ALWAYS PAID, even in ECO)
    ↓
Smart Tool Selection (good — 70-88% savings)
    ↓
Delegate to Specialist? ──→ Specialist (worker role — local OK)
    ↓                              ↓
Direct Response              Result merging
    ↓                              ↓
Lane Queue (serial per user — good)
```

**Problems:**
1. Brain always paid — contradicts ECO $0 promise
2. No unified resource lifecycle — tabs, MCP, specialists cleaned up differently
3. Race conditions on shared state (cache, Chrome launch, idle timers)
4. Fire-and-forget overused — errors silently swallowed

### Proposed Architecture (Better Brain Orchestration)

**2 modes for v1.0 launch (ECO cut — needs 32GB+ RAM, future feature):**

```
┌─────────────────────────────────────────────────────────────┐
│                    ECO MODE ($0 always)                      │
│                                                             │
│  Brain:    Ollama/MLX local model (Qwen3, etc.)             │
│  Worker:   Ollama/MLX local model (Nanbeige, etc.)          │
│  Fallback: NONE — if local fails → tell user honestly       │
│  Cost:     $0 guaranteed. No paid API calls. Ever.          │
│                                                             │
│  If rate-limited → wait for local slot                      │
│  If too complex → "This needs HYBRID mode"                  │
│  Never sneaks in a paid call.                               │
├─────────────────────────────────────────────────────────────┤
│                  HYBRID MODE (cheap)                         │
│                                                             │
│  Brain:    Haiku (paid, cheap — the decision maker)         │
│  Worker:   Nanbeige local ($0 — does the heavy lifting)     │
│  Fallback: Haiku again if local worker fails                │
│  Cost:     Low — brain is cheap, workers are free            │
│                                                             │
│  Best balance of quality and cost.                          │
│  Brain makes smart decisions, workers execute locally.       │
├─────────────────────────────────────────────────────────────┤
│               FULL MODE (user controls)                      │
│                                                             │
│  Brain:    User-settable (Sonnet, GPT-5, Claude, etc.)      │
│  Worker:   User-settable (Haiku, local, any model)          │
│  Fallback: User-settable (Opus, GPT-5, etc.)               │
│  Cost:     Whatever the user's chosen models cost            │
│                                                             │
│  All 3 roles configurable via /settings, API, or TUI.       │
│  User picks exact models for brain, worker, fallback.        │
│  Maximum flexibility — power users choose their stack.       │
└─────────────────────────────────────────────────────────────┘
```

**Full request flow:**

```
User Message
    ↓
ECO Router (picks mode → picks models for each role)
    ↓
Main Agent (brain role — model depends on mode)
    ├─ ECO:    local brain only
    ├─ HYBRID: Haiku brain
    └─ FULL:   user-configured brain
    ↓
Smart Tool Selection (keep as-is — 70-88% savings)
    ↓
Resource Manager (NEW — unified lifecycle)
    ├─ Tab pool with proper acquire/release/finally
    ├─ MCP connection pool with versioned idle timers
    ├─ Specialist pool with error callbacks on all tasks
    └─ Chrome launch lock (single instance)
    ↓
Delegate to Specialist? (worker role — model depends on mode)
    ├─ ECO:    local worker only
    ├─ HYBRID: Nanbeige local (Haiku fallback)
    └─ FULL:   user-configured worker
    ↓
Cache with asyncio.Lock (no more race conditions)
    ↓
Response → User (tagged with model attribution)
```

### Key Changes

| Change | Current | Proposed | Impact |
|--------|---------|----------|--------|
| ECO mode | Brain calls paid Haiku | Brain uses local only, $0 guaranteed | ECO actually means $0 |
| HYBRID mode | Same as ECO but auto-fallback | Haiku brain + Nanbeige worker | Clear cheap tier |
| FULL mode | Sonnet brain hardcoded | User picks all 3 role models | Power user flexibility |
| Resource cleanup | Per-system, inconsistent | Unified Resource Manager | No orphaned tabs/processes |
| Cache access | Unprotected globals | asyncio.Lock on all caches | No race conditions |
| Fire-and-forget | No error callbacks | Error callbacks on ALL tasks | No silent failures |
| MCP idle timer | Race-prone | Versioned timer with lock | No use-after-disconnect |
| Chrome launch | Race-prone | Single launch lock | No duplicate processes |

---

## 9. Remediation Priority

### Phase A: Critical Fixes (~2 days, blocks everything)

| # | Fix | Est. Time | Risk if Skipped |
|---|-----|-----------|-----------------|
| C5 | Rotate ALL exposed API keys | 30 min | Total compromise |
| C1 | asyncio.Lock on context_builder cache | 1 hour | Corrupt tool schemas |
| C2 | Fix indentation on favorite MCP block | 15 min | MCP tools invisible |
| C3 | Align model IDs registry ↔ pricing | 30 min | 33x wrong cost tracking |
| C4 | Local brain route for ECO mode | 2 hours | ECO mode is a lie |

### Phase B: High Priority (~3 days)

| # | Fix | Est. Time |
|---|-----|-----------|
| H1 | Replace `locals().get()` with explicit dict | 15 min |
| H2-H3 | Add error callbacks to all fire-and-forget tasks | 2 hours |
| H5 | Remove duplicate browser learning save from delegate.py | 15 min |
| H6 | Delete deprecated teams/lead.py | 10 min |
| H7 | Add finally blocks for tab release on timeout/cancel | 1 hour |
| H8 | Reduce session timeout to 24 hours | 15 min |
| H9-H10 | Add rate limiting + permission checks to vault routes | 2 hours |
| H11 | Add entropy validation to SERVER_SECRET check | 30 min |
| H12 | Add missing DB indexes (4 tables) | 30 min |
| H13 | Add connection lock to CDPBackend._ensure_connected | 1 hour |
| H14 | Add versioned timer to MCP idle disconnect | 1 hour |

### Phase C: Medium Priority (~5 days)
All 15 MEDIUM issues + dead code cleanup.

### Phase D: TUI Improvements (After Stabilization)
Based on mockup planning: grid cards, timestamps, watcher details, settings toggle, cancel/stop controls.

---

## 10. Consequences

### What Becomes Easier After Fixes
- **Production deployment** — no more race conditions or silent failures
- **Cost tracking** — accurate pricing for budget enforcement
- **ECO mode** — actually delivers on $0 promise with local brain
- **Debugging** — error callbacks surface all hidden failures
- **Scale** — DB indexes + connection locks handle concurrent load

### What Becomes Harder
- Nothing — all changes are fixes and improvements, not new complexity

### What We'll Need to Revisit
- ECO brain routing needs real-world testing (local vs paid quality trade-off)
- Resource Manager abstraction may need iteration based on specialist patterns
- MCP idle timer versioning needs load testing under high concurrency

---

## 11. Action Items

1. [ ] **IMMEDIATE:** Rotate all exposed API keys in .env
2. [ ] **Phase A:** Fix 5 CRITICAL issues (block all other work)
3. [ ] **Phase B:** Fix 14 HIGH issues (security + race conditions + dead code)
4. [ ] **Phase C:** Fix 15 MEDIUM issues + remove dead code
5. [ ] **Phase D:** TUI improvements per mockup (grid cards, settings toggle, cancel controls)
6. [ ] **Phase E:** Re-audit after Phase B to verify no regressions
7. [ ] **Ongoing:** Add error callbacks to ANY new fire-and-forget code
