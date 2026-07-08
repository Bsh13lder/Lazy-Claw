# Live-Test Incident Fixes — 2026-07-03

Branch: `fix/live-test-incidents` (off `fix/minimax-tool-hardening`). Deploy: one `make rebuild` covers scraper/upwork/agent/db/gateway (all baked into the lazyclaw image). Web + mobile client changes deferred (separate builds).

Evidence ledger: `<scratchpad>/monitoring/findings.md`.

## Root causes (verified in code before dispatch)

| # | Sev | Issue | Root cause (confirmed) | Owner |
|---|-----|-------|------------------------|-------|
| 1 | CRIT | Container OOM → API unresponsive; web_search + scraper timeouts | mcp-scraper headless-Chromium escalation launches container Chromium that fails CDP handshake (`CDP not responding after 3s`); failed procs not reaped → mem climbs to 3.8GiB cgroup limit → OOM-kill → event-loop starve. Each dead launch also burns the full 60s. | Agent A |
| 2 | HIGH | Upwork reads all 60s-timeout | Upwork changed messages DOM; `rooms-panel` + `[data-test="room-item"]` selectors match nothing (scraper stderr: "likely 2026 layout drift"); no fail-fast so every call waits the full timeout. | Agent B |
| 3 | HIGH | Task create/delete from web brain fail / fabricated dispatch | THIN=1 + SPEC=1 in prod. Task tools ARE injected (agent.py:3089) but specialist-first strips domain tools from the brain to force delegation; MiniMax brain doesn't reliably `delegate`, calls `search_tools`/`add_task` inline → thin-router narrows to meta-only → `add_task`/`delete_task` dropped as hallucinated. `tasks_specialist.md` allowlist is COMPLETE (not the allowlist class). T7 eventually created the task via the failsafe (52→53) but with a confusing "Continuing in background" msg; T8 delete had no failsafe and dead-ended with "delete manually". | Agent C |
| 4 | MED | Morning DB corruption | Host `pytest tests/` opened the bind-mounted prod `./data/lazyclaw.db` (default DATABASE_DIR=./data); killed mid-write → malformed → `init_db` crash-loop, no guard. | Agent D |
| 5 | MED | Mobile Notes never sync down (404) | `/api/lazybrain/notes/changes` (lazybrain.py:187) is shadowed by `/notes/{note_id}` (line 143) — FastAPI matches `changes` as a note_id → 404. Route-ordering bug. | ME (direct) |

## Deferred (note to user, not wave 1)
- LOW web `/api/agents/status` 3s×76KB polling (needs web build) · MED T3 internal-guidance leak in reply (consolidator) · LOW eco_router warn-once dedup (already silenced at source via DB settings re-apply) · LOW wikilink cosmetic in recall · mobile-client side of notes sync (needs APK build).

## Already done (live, no deploy)
- Monitoring cron stopped. DB settings re-applied: blck full_brain/worker=MiniMax-M2.7, full_fallback=claude-haiku-4-5-20251001 (kills scrub+guard log spam at source).

## Progress
- [x] #5 Notes route ordering — committed 282d7dc (17 tests)
- [x] #4 DB isolation + startup guard — committed 1f2c116 (62 tests green incl. explicit-Config coexistence)
- [ ] #1 scraper leak — agent running
- [ ] #2 upwork selectors — agent running (editing messages.py)
- [ ] #3 task routing — agent running

## Cross-scope landmine (flagged by DB agent, for review pass)
`tests/test_claude_provider_isolation.py` may itself write to cwd / real paths — relates to [[project_sdk_session_isolation_leak]]. Verify it respects the new conftest tmp-dir isolation; fix in a follow-up if it hardcodes a path. NOT a wave-1 blocker.

## ALL 5 COMMITTED (branch fix/live-test-incidents)
- 282d7dc #5 notes route ordering (17 tests)
- 1f2c116 #4 DB isolation + startup guard (62 tests)
- 763c506 #3 task routing exempt (447 runtime tests, 1 known pre-existing fail)
- e058b60 #2 upwork fail-fast nav (27 tests; widened room selector needs live-DOM confirm)
- ceb2588 #1 scraper OOM reap (agent-verified live 0-leak; host can't run scraper deps)

## Deeper root causes surfaced by agents (follow-ups, NOT in wave 1)
- SCRAPER: (a) AsyncWebCrawler gets **kwargs not config=BrowserConfig(...) → stage browser settings silently dropped + failed launches doubled; (b) webkit tried FIRST in escalation but never installed in the image → every webkit stage = guaranteed failed launch (a big chunk of the leak feeder). Fixing these would cut most failed launches at the source.
- UPWORK: widened room-item selector is defensive/unverified vs live DOM — confirm with a live capture; the networkidle→domcontentloaded timeout fix stands alone.

## Deploy + verify
make rebuild (docker compose build lazyclaw && up -d). Live checks: (1) boot healthy + startup guard passes on recovered DB; (2) GET /api/lazybrain/notes/changes → 200; (3) probe task create+delete → real create + real delete (no bail/fabrication); (4) upwork read fails fast <10s not 60s; (5) web_search: watch mem stays flat + no orphaned Chromium + no OOM.
