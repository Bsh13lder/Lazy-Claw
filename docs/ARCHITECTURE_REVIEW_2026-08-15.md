# Architecture Review Agenda — 2026-08-15

Prepared 2026-08-14 evening after the fragility-hardening week. Everything
here is backed by incidents from 08-09 → 08-14; nothing is speculative.

## 1. The two standing decisions (make them first)

### 1a. Thin-router: finish or soften (ADR-0005)
Evidence from this week:
- Router narration multiplies bubbles: one easy browser question produced
  8 bubbles across dispatch/timeout/retry generations (08-14 18:31).
- Background workers had to be *exempted* from router-only mode after the
  worker-spawns-worker loop (commit a4e3276).
- The router cannot do trivial things itself, so even "open one page and
  read it" pays full dispatch ceremony.

Options:
- **Commit fully**: tear down legacy delegate/dispatch paths, AND do a
  "quiet router" pass — at most ONE status bubble per dispatch, no
  re-narration on retries (SOUL already bans placeholder-failure talk).
- **Soften**: keep specialist-first for real fan-out/slow work, add a
  DIRECT LANE — small read-only browser/read tasks run the specialist
  synchronously with zero narration, reply comes back as if inline.
- Recommendation on file: soften. The router earns its keep on parallel
  and slow work; it taxes everything else.

### 1b. Feature surface: choose the core
Candidate core (earns money / daily use): chat+memory, Upwork pipeline,
tasks/reminders, browser+scraper, himap workflows, documents suite.
Candidates to freeze/park (not delete — stop maintaining): replay,
pipeline (CRM), survival extras beyond Upwork, n8n bridge, computer
connector, bounty, voice beyond STT. Every parked module = fewer seams.

## 2. Timeout & budget hierarchy audit (30 min, high value)
This week's loops were budget math, not logic. Build ONE table of every
timeout and make them strictly nested:
- per-browser-action `[toolexec]` cap: 60s (seen killing CF-slow pages)
- sync specialist dispatch: default 120s / browser floor 480s (6066c2c)
- background task budget: 600s via agent tool (was 300 — self-defeating)
- lane/foreground budget, AUTO-PROMOTE thresholds, MCP call timeouts
Rule to enforce: child budget < parent budget, always, verified by a test.

## 3. Memory follow-ups (foundation fixed 08-14, hygiene remains)
- Decide MEMORY_UNIFIED flip (now safe — fc08c80) or stay dual-write.
- Auto-capture volume: store is 93.5% auto notes; consider capture
  throttles, TTL/rollup for lesson cards, periodic prune job.
- Session titles can still be minted from cron prefixes (agent.py
  auto-title) — small fix.

## 4. UX debris (mobile)
- Collapse the interim "Checking now…" bubble into the final reply when
  both belong to one turn (client-side merge by created_at batch).
- Day-separator rows in chat (timestamps shipped in 1.32.0).
- Consider: show dispatch status as the *streaming indicator* instead of
  persisted text bubbles (kills narration residue permanently).

## 5. OSS upgrade evaluations (license-check EVERYTHING first — MIT repo)
Verify licenses at adoption time; do not trust this list blindly.
- **browser-use** (reportedly MIT, Python): agent-browser action layer —
  element selection, retries, anti-flake — on top of CDP. Could replace
  hand-rolled click/type/snapshot heuristics while KEEPING host-Brave
  profiles, cadence, checkpoints, event bus. Spike: 1 day, drive it
  against himap admin + Upwork inbox via connectOverCDP.
- **Playwright** (Apache-2.0): the "no Playwright" rule predates
  `connectOverCDP` familiarity — it can attach to the real Brave over
  CDP. Reconsider ONLY as plumbing under browser-use or for the
  specialist's action reliability; profiles stay ours.
- **crawl4ai** (Apache-2.0): already shipped in mcp-scraper — upgrade
  version rather than replace. **firecrawl is AGPL — never.**
- **mem0** (reportedly Apache-2.0): do NOT migrate memory now (recall
  stack just rebuilt); worth reading for scoring/decay ideas only.
- **Hermes Agent skills** (NousResearch): SKILL.md format ≈ our Agent
  Skills import; audit adapter effort + license, cherry-pick skills
  (Apple Notes/iMessage via host bridge).

## 6. Harness patterns worth stealing (read, don't fork)
Half-day reading list; extract at most 3 patterns:
- Claude Agent SDK / Claude Code dispatcher: subagent result contracts,
  single-writer transcript, task notifications (we converged on failure
  cards + contract gates this week — same family).
- smolagents (Apache-2.0): minimal agent loop — compare against TAOR for
  what we could delete.
- Hermes: skill self-creation loop vs our lesson store (ours needed
  flood control — theirs may too; learn from their dedup).
Rule: adopt PATTERNS as small mechanical guards, not frameworks.

## 7. Standing hygiene (now mechanical, keep green)
- /api/health build.sha check before any "why isn't my fix live" debugging.
- Specialist prompt↔allowlist sweep is CI (4a83b5d) — keep extending by
  capability (read+write pairs).
- Two stale runtime tests to fix or delete: test_fg_workcall_budget::
  test_dispatch_only_is_strict_handoff_subset, test_inline_mutation_cap::
  test_cap_and_anchor_use_the_mutation_predicate.
- MCP 2026-07-28 spec conformance plan (pinned <2; SSE→streamable HTTP,
  stateless client, OAuth iss) — schedule real work.
