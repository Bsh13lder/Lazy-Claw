# HARD_TESTS.md — Pre-launch Hard Test Plan

> **Purpose**: track every silent-breakage / feature-correctness test that must pass before public launch.
> **Owner**: solo dev. **Status**: v0.1 beta.
> **Workflow**: small-by-small. Pick one row, add scaffolding under `tests/hard/`, run, mark ✅, commit.
>
> Last updated: 2026-05-02

---

## Stage snapshot

| Layer | State |
|---|---|
| Phases 1–10 (core) | ✅ shipped |
| Phase 17 Survival/Gig | ✅ shipped (3009e69) |
| Phase 18 LazyBrain | ✅ shipped |
| Phase 19 LazyBrain Obsidian-grade | ✅ shipped (2cf64d7) |
| Browser Canvas A/B/D | ✅ shipped (ae204c3) |
| MODE_CLAUDE | ✅ shipped (9e18a22) |
| Brain-as-dispatcher | ✅ shipped (834f1c7 / 81f189b / 5a71e95) |
| Phase 11 channels (Discord/Signal) | ❌ not started |
| Phase 12 Flutter | ❌ not started |
| Phase 14 Fast Dispatch + TabManager | ⚠ partial |
| Phase 15 Full ECO local UI | ⚠ partial |
| L.5 / L.6 / L.12 | ❌ launch blockers |

---

## Market gaps vs OpenClaw / competitors

| Gap | Who has it | Impact |
|---|---|---|
| Public Skill Hub (community marketplace) | OpenClaw, Sim.ai | High |
| Mobile app (Flutter) | OpenClaw web-mobile, Suna | High |
| Discord/Slack native | OpenClaw, most | Medium |
| Voice in/out | ChatGPT, Suna, OpenClaw | Medium |
| OAuth catalog UX (Google/GitHub one-click) | Anthropic Connectors, OpenClaw | High |
| Workflow Builder canvas (n8n replacement inside app) | n8n, Sim.ai, Latitude | Medium |
| Public eval / benchmark harness | OpenAI evals, AnyClaude | Medium |
| One-click deploy (Render/Fly button) | Suna, LibreChat | Medium |

**LazyClaw moat (lead with these)**: E2E encryption · native MCP · LazyBrain unified memory · zero-token browser canvas · Claude CLI $0 mode.

---

## Test priority levels

- **P0** — silent breakage, user-visible, no test exists. Block launch.
- **P1** — feature correctness on recently shipped paths. Should pass before any public demo.
- **P2** — environmental / deployment. Block Docker public release.
- **P3** — degradation paths (offline / rate-limit). Confirms graceful fallbacks.

---

## P0 — launch blockers

### P0.1 — E2E encryption round-trip
- **Why**: if broken, user data is unrecoverable. No tests exist for full lifecycle.
- **Steps**:
  1. Register user with password `Pw0rd!` → capture `encryption_salt` + recovery phrase.
  2. Save personal_memory `Madrid is home` via API.
  3. Restart server (kill+start, NOT same process).
  4. Login again → recall_memories → must return plaintext `Madrid is home`.
  5. New process: feed recovery phrase → re-derive DEK → decrypt same row.
  6. Check stored format is `enc:v1:<b64-nonce>:<b64-ciphertext>` for the content column.
  7. Negative: wrong password → DEK derivation fails cleanly, not corrupt-data crash.
- **Files**: `lazyclaw/crypto/encryption.py`, `lazyclaw/crypto/vault.py`, `lazyclaw/gateway/auth.py`.
- **Acceptance**: 7/7 steps pass. Recovery phrase round-trips. Wrong password gives clean error.
- **Test path**: `tests/hard/test_e2e_encryption_lifecycle.py`

### P0.2 — MCP stdio respawn on broken pipe
- **Why**: commit `98c830d` recently fixed but untested. If it regresses, MCP pool stays exhausted.
- **Steps**:
  1. Connect mcp-scraper.
  2. Issue 1 `web_search` call → success.
  3. `kill -9` the mcp-scraper subprocess from outside.
  4. Issue 2nd `web_search` → must not hang; pool detects broken pipe and respawns.
  5. Verify no `_call_lock` deadlock (commit `db7dd27` removed it).
- **Files**: `lazyclaw/mcp/client.py`.
- **Acceptance**: 2nd call succeeds within 5s of pipe break.
- **Test path**: `tests/hard/test_mcp_respawn.py`

### P0.3 — MiniMax M2.7 tool discipline + rate-limit fallback
- **Why**: commits `d7f1562` + `0513996`. Subscription is $20/mo Plus = 4,500 req/5h. Rate-limit needs fallback routing per memory.
- **Steps**:
  1. Set `MODEL_BRAIN=minimax-m2.7` in MODE_CLAUDE-equivalent route.
  2. Send 1 simple chat → verify Anthropic-compat tool call shape.
  3. Inject mock 429 from MiniMax provider → verify request flips to Haiku fallback (not paid Sonnet).
  4. Verify pricing.py marks MiniMax cost as $0 (subscription, not per-token).
  5. Verify suffix + tool cap + counter applied (no runaway tool spam).
- **Files**: `lazyclaw/llm/providers/`, `lazyclaw/llm/eco_router.py`, `lazyclaw/llm/pricing.py`.
- **Acceptance**: 429 → Haiku fallback. No $$ leak. Tool cap respected.
- **Test path**: `tests/hard/test_minimax_rate_limit.py`

### P0.4 — Permission inline approval flow
- **Why**: deny-bypass = security incident. Audit log is the only forensic record.
- **Steps**:
  1. Set `web_search` to `deny` → call returns blocked error, no execution, audit row written.
  2. Set `web_search` to `ask` → call yields APPROVAL_REQUIRED marker, agent waits, /approve unblocks, audit row written.
  3. Set `web_search` to `allow` → executes immediately, audit row written.
  4. First registered user is `admin` role.
  5. Default categories: `core`, `orchestration`, `browser_management`, `tasks` are `allow` (commits e8abc62, ac80851).
- **Files**: `lazyclaw/permissions/checker.py`, `permissions/approvals.py`, `permissions/audit.py`.
- **Acceptance**: 3 modes behave correctly + 3 audit rows recoverable.
- **Test path**: `tests/hard/test_permissions_flow.py`

---

## P1 — feature correctness

### P1.5 — Gig pipeline end-to-end
- **Why**: Phase 17 hardening (commit `3009e69`). Must work with shared Brave profile.
- **Steps**:
  1. `set_skills_profile(skills=python)` → encrypted in users.settings.
  2. `search_jobs(sites=[upwork,indeed], hours_old=72)` → JobSpy returns rows; `normalize_row` handles NaN/float salaries.
  3. `mcp-jobspy` direct path + MCP path return same shape.
  4. Matcher ranks: 0.6 skills + 0.2 budget + 0.1 category.
  5. `draft_freelance_proposal(job_url)` → opens via shared Brave profile (LAZYCLAW_BROWSER_PROFILE_DIR honored).
  6. `apply_job` opens template; **never** auto-submits.
  7. Negative: `auto_apply=true` is intentionally absent.
- **Files**: `mcp-jobspy/normalize.py`, `mcp-upwork/`, `lazyclaw/survival/`.
- **Acceptance**: end-to-end run on real Upwork without second login or auto-submit.
- **Test path**: `tests/hard/test_gig_pipeline_e2e.py`

### P1.6 — LazyBrain semantic search + RAG
- **Why**: Phase 19.2 just shipped, no real-volume test.
- **Steps**:
  1. Seed 200 fake notes via `lazyclaw cli_migrate_lazybrain --dry-run --all` rehearsal then real.
  2. `lazybrain_reindex_embeddings` → 200 rows in `note_embeddings` (768d, encrypted, AAD=`notes:embedding`).
  3. `lazybrain_ask("how does PBKDF2 caching work?")` → answer cites real `[[Note Title]]`s.
  4. Verify all citations resolve to existing notes (no hallucinated titles).
  5. `SEMANTIC` toggle off → substring fallback still returns results.
  6. `lazybrain_topic_rollup("encryption")` → markdown with summary / decisions / open questions / sources.
- **Files**: `lazyclaw/lazybrain/embeddings.py`, `ask.py`, `topic_rollup.py`.
- **Acceptance**: ≥3/3 citations real. RAG answer references actual stored content.
- **Test path**: `tests/hard/test_lazybrain_rag.py`

### P1.7 — Hybrid memory picker
- **Why**: regression risk on `_pick_hybrid_memories`. Fixes "memory exists but agent can't find it" loop.
- **Steps**:
  1. Seed 50 personal_memory rows: 40 high-importance unrelated + 10 medium-importance about "Madrid".
  2. Send message: "what do you know about Madrid?"
  3. Inspect injected context — must contain ≥3 of the 10 Madrid rows (keyword overlap path).
  4. Send empty/no-keyword message → falls back to pure importance (5 rows).
  5. EN+ES stopwords filtered (no "el / la / the" matching).
- **Files**: `lazyclaw/runtime/context_builder.py`.
- **Acceptance**: keyword path surfaces relevant facts, fallback path works on empty input.
- **Test path**: `tests/hard/test_hybrid_memory_picker.py`

### P1.8 — Brain-as-dispatcher pivot detector
- **Why**: commits `834f1c7` / `84cf5e3` / `5a71e95` / `81f189b`. Newest critical UX fix.
- **Steps**:
  1. Send "search jobs and apply to top 3" — brain must call `dispatch_subagents`, NOT do work itself.
  2. Send work-tempting prompt mid-turn; pivot detector catches and redirects.
  3. While dispatch active, send 2 more user bubbles → both visible immediately (non-blocking).
  4. Send "show / edit / delete jobs" → keyword-injection routing surfaces cron-job tools without `search_tools` ping-pong.
  5. Multi-`run_background` results consolidated into ONE final reply (5a71e95).
- **Files**: `lazyclaw/runtime/agent.py`.
- **Acceptance**: 5/5 behaviors confirmed.
- **Test path**: `tests/hard/test_brain_dispatcher.py`

### P1.9 — Background task auto-promote
- **Why**: commit `a164149`. Stuck foreground tasks must promote.
- **Steps**:
  1. Issue 6-step plan that exceeds foreground budget.
  2. Verify auto-promote to `run_background` triggers.
  3. Telegram push fires on completion.
  4. Heartbeat NameError fix (34d2f26) doesn't regress.
- **Files**: `lazyclaw/runtime/task_runner.py`, `lazyclaw/heartbeat/`.
- **Acceptance**: 6-step plan finishes in background with Telegram push.
- **Test path**: `tests/hard/test_bg_auto_promote.py`

---

## P2 — environmental / deployment

### P2.10 — Docker headless mode no-TUI
- **Why**: 100% CPU bug regression risk (memory: feedback_docker_tui_cpu.md).
- **Steps**:
  1. Run container with `LAZYCLAW_SERVER_MODE=1` and stdin piped (non-tty).
  2. Verify Textual TUI does NOT spawn (check process tree).
  3. CPU stays <5% at idle for 60s.
  4. API health endpoint returns 200.
- **Files**: `lazyclaw/cli.py`.
- **Acceptance**: <5% CPU + healthy API.
- **Test path**: `tests/hard/test_docker_headless.py`

### P2.11 — Claude CLI persistence in Docker
- **Why**: commit `0231301` + `435fac9`. Login must survive `down/up`.
- **Steps**:
  1. `docker compose up`, run `claude login` inside container.
  2. `docker compose down && docker compose up`.
  3. Verify login still valid → `claude -p "hi"` succeeds without re-auth.
  4. Boot warning fires when volume empty AND `MODE_CLAUDE` selected.
- **Acceptance**: login persists across restart.
- **Test path**: `tests/hard/test_docker_claude_persistence.py`

### P2.12 — Browser Canvas Live mode + checkpoints
- **Why**: stale-frame bug + checkpoint blocking flow.
- **Steps**:
  1. Trigger browser action → canvas appears with URL + thumbnail in chat.
  2. `🔄 Refresh` → fresh screenshot fetched.
  3. `👁 Live mode` → 5-min flag set; every action emits fresh thumb.
  4. Live mode auto-clears at 5min mark.
  5. Agent calls `request_user_approval` → CheckpointBanner blocks; Approve unblocks; Reject + reason logged.
  6. Same checkpoint name re-call → auto-approves.
  7. 10-min soft-reject timeout works.
  8. Zero `browser_event` frames enter LLM context (search agent_messages for them).
- **Files**: `lazyclaw/browser/event_bus.py`, `cdp_backend.py`, `checkpoints.py`.
- **Acceptance**: 8/8 behaviors confirmed.
- **Test path**: `tests/hard/test_browser_canvas.py`

### P2.13 — Telegram ↔ Web UI model unification
- **Why**: chat_id ↔ user_id binding, model badge consistency across channels.
- **Steps**:
  1. `/link` from Telegram → returns one-time code.
  2. Paste code in Web UI → binding written.
  3. `/whoami` from both surfaces returns same user_id.
  4. Switch model in Web UI → BrainBadge updates in Telegram next reply.
  5. Web UI dual-writes generic + per-mode model.
- **Acceptance**: 5/5 behaviors confirmed.
- **Test path**: `tests/hard/test_channel_unification.py`

---

## P3 — degradation paths

### P3.14 — Ollama-down graceful degradation
- **Why**: every Phase 19.2 AI feature must degrade, not crash.
- **Steps**:
  1. Stop Ollama (`ollama serve` killed).
  2. `lazybrain_suggest_links` → falls back to deterministic substring proposals.
  3. `lazybrain_suggest_metadata` → returns "LLM unavailable" message, not 500.
  4. `lazybrain_ask` → graceful "LLM unavailable" error.
  5. `lazybrain_reindex_embeddings` → fails clearly, doesn't hang.
  6. `lazybrain_topic_rollup` → graceful fallback.
  7. `lazybrain_morning_briefing` → graceful fallback.
  8. `auto_capture.capture_text_with_llm` skips when worker model unreachable.
- **Files**: `lazyclaw/lazybrain/autolink.py`, `metadata_suggest.py`, `ask.py`, `embeddings.py`, `topic_rollup.py`, `recap.py`.
- **Acceptance**: 8/8 features either degrade silently or return clear "LLM unavailable" messaging.
- **Test path**: `tests/hard/test_ollama_down_fallbacks.py`

### P3.15 — Worker rate-limit fallback chain
- **Why**: ECO HYBRID mode + MODE_CLAUDE depend on fallback ordering.
- **Steps**:
  1. HYBRID mode: force Gemma worker fail → Haiku fallback fires.
  2. MODE_CLAUDE: force Claude CLI fail (paid fallback dead) → clearer error per `af48581`.
  3. MiniMax 429 → Haiku fallback.
  4. Verify pricing accounting reflects which provider actually served the call.
- **Files**: `lazyclaw/llm/eco_router.py`, `model_registry.py`, `providers/claude_cli_provider.py`.
- **Acceptance**: fallback chain documented in each provider's failure path.
- **Test path**: `tests/hard/test_worker_fallback_chain.py`

---

## Execution order (small-by-small)

1. **P0.1** crypto round-trip — highest data-loss risk, easiest to write.
2. **P0.4** permission flow — safety net for everything else.
3. **P0.2** MCP respawn — recently fixed, regression-prone.
4. **P0.3** MiniMax — broken-think per memory, validate fallback.
5. **P1.7** hybrid memory picker — pure unit test, no infra needed.
6. **P1.8** brain dispatcher — observable in agent traces.
7. **P1.6** LazyBrain RAG — needs Ollama + 200 notes.
8. **P1.5** gig pipeline — needs real Upwork session.
9. **P1.9** background auto-promote — needs heartbeat fixture.
10. **P2.10–P2.13** Docker / canvas / channels — last, env-heavy.
11. **P3.14–P3.15** degradation — after everything else green.

After each test passes:
- Mark task `completed` via TaskUpdate.
- `git commit -m "test: <Pn.N> <name>"`.
- Move to next.

---

## Test scaffolding convention

```
tests/hard/
  conftest.py              # shared fixtures (encrypted DB, mock LLM, fake MCP)
  test_e2e_encryption_lifecycle.py
  test_mcp_respawn.py
  ...
```

- Use `pytest-asyncio` (already in project).
- Real DB at `data/test_hard.db`, fresh per test session.
- No mocks for crypto / encryption — must hit real `enc:v1:` path.
- Mocks acceptable for LLM provider responses (deterministic).
