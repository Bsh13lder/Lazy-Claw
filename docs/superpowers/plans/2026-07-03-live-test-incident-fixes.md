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
