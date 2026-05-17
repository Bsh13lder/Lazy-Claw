# Next-session handoff — 2026-05-18

Paste the **"Prompt for next session"** block at the bottom into a fresh Claude Code session in this repo and it'll pick up exactly where we left off.

---

## State at session close

**Branch**: `feat/claude-agent-sdk` — fast-forwarded into `origin/main` at `db81820`.

**Working tree**: clean.

**Latest commits on main**:
- `db81820` — F2: SOUL.md most-recent-wins rule for contact contradictions
- `e23e2dc` — Six-bug grounding pass (A–F + F1) + carried-along work

**Docker state**: image was rebuilt this session, last container restart confirmed mcp-upwork self-check banner. F2 (SOUL.md only) does NOT require a rebuild — SOUL.md is read fresh per turn.

---

## What was solved this session

| Bug | What it was | Fix landed |
|---|---|---|
| A | AUTO-PROMOTE fired after a useful read-only tool result, killing the foreground synthesis | `_is_readonly_inspection` allowlist expanded in `agent.py` |
| B | Upwork tools silently dropped from channel-tool injection because `_CHANNEL_CORE_SUFFIXES` didn't include `_get_*`/`_check_*` shape | Suffix list extended in `agent.py:418` |
| C | SOUL.md NEVER rule only triggered on plan/scope verbs — missed find/extract/fetch/read/summarize | Trigger phrase list broadened |
| D | MCP `_extract_message` carried prior bubble's sender across speaker flip — Vato's reply tagged as James | Flip-aware carry-forward in `mcp-upwork/.../messages.py` + 5 new tests |
| F | MCP read stale virtualized DOM right after container restart — newest bubbles unmounted | Scroll-to-bottom + render-stability poll before parse |
| F1 | Brain hallucinated platform names from in-context-learning bias | SOUL.md: quote-then-summarize coda — verbatim quotes precede every summary, every claim must trace to a quote |
| F2 | Brain merged contradictory contact facts (20 cities vs 6 cities) | SOUL.md: most-recent-wins rule + explicit supersession flag |

Plus: `upwork_last_conversation` skill now takes `contact_name`, mcp-upwork has a startup self-check that aborts on stale images.

End-to-end verified: at 00:51 the brain correctly fetched James's 9:12 PM AND 10:37 PM messages, quoted them verbatim, identified the platform (eStreet AMC → BPO appraisal orders), no fabrication.

---

## Open items — ranked

### 1. Unified Code Session per project (HIGH — architectural)

**What**: Each contract/goal owns ONE long-lived Claude Code MCP session. Recon + scaffold + iterate share context. Brain becomes a thin dispatcher that appends turns to the project's session instead of choosing between `browser`/`research`/`code` specialists.

**Why it matters**: today's recon work for James (estreetamc.spurams.com login page) ran in `browser_specialist` — ephemeral, no workspace, no memory. When we move to scaffolding, the Code Specialist will start fresh with no recon context. Wasted tokens, lost continuity.

**Three-phase plan**:

| Phase | Scope | Est. effort | Risk |
|---|---|---|---|
| **P1** | Session persistence layer. Add `code_session_id` column to `goals` (or `contracts`) table. Modify `lazyclaw/teams/runner.py` to check for existing session + resume + append. Workspace dir becomes goal-scoped: `~/Desktop/lazyclaw-workspace/{project_tag}/{goal_id}/`. | ~1.5 h | Low — additive schema, falls back to new session if resume fails. |
| **P2** | Routing. SOUL.md rule: "if user names a contact/contract or message is a continuation of a prior contract turn → `delegate(specialist='code', goal_id=...)`, never `browser`/`research`." Plus runtime detection in `agent.py`. | ~1.5 h | Medium — risk of over-routing; add `force_browser` escape hatch. |
| **P3** | Brain becomes thin dispatcher. CodeSpecialist.tsx shows per-contract session timelines. | ~2 h | Higher — UI + agent loop. Defer until P1+P2 prove out. |

**Start with P1.** After P1 lands, James's project resumes the same Claude Code MCP session every time — 80% of the architectural win for 25% of the effort.

### 2. Bug E — MCP drops offer-card structured fields (MEDIUM)

**What**: `upwork_get_conversation` extracts only the prose body of an offer message. The structured fields are dropped:
- `Est. Budget: $120.00`
- `Milestone 1: 1. Login automation 2. Real-time order monitoring`
- `Due: Tuesday, May 19, 2026`
- `Project funds: $20.00`
- `Vato Tchipa accepted an offer at 10:55 AM Saturday May 16` (system event)

**Why it matters**: brain doesn't know the contract is already accepted with a Tuesday May 19 deadline. Planning misses the time pressure.

**Fix shape**: in `mcp-upwork/src/upwork_mcp/tools/messages.py`, parse offer-card selectors and emit them as structured fields (`offer_budget`, `offer_milestone`, `offer_due_date`, `offer_funds`, `event_type` for system events). Add tests against a snapshot HTML fixture.

### 3. James-side action items (BUSINESS)

- Draft Upwork DM asking James for eStreet AMC credentials (username + password — vault-stored). Tuesday May 19 is the deadline so this is urgent.
- Decide auto-accept vs alert-only-with-1-tap default. Recommendation: alert-only first, auto-accept opt-in after observing the order flow.
- Confirm Telegram is the alert channel (LazyClaw is already wired to Telegram chat ID 8127631458 per the 1:07 turn).

---

## City-list discrepancy reminder

James gave **20 cities** at 9:12 PM and **narrowed to 6** at 10:37 PM (Oakland, Hayward, San Leandro, Newark, San Jose, Cupertino). After F2 the brain SHOULD use the 6-city list and flag the supersession in any future summary. Verify this happens on the next "check James" query.

---

## Verification checklist for next session

Before any new work:

1. `git log --oneline -3` should show `db81820` on `main`.
2. Ask LazyClaw "check james last messages on upwork and tell me what he needs" — expected: quotes James verbatim, lists 6 cities (with supersession note), names eStreet AMC, surfaces $120 budget. If it still shows 20 cities or misses Tuesday May 19 deadline, F2/Bug E gaps remain.
3. `docker ps` shows `lazyclaw` container up; `tail -10 data/mcp-mcp-upwork.stderr.log` shows the startup self-check banner.

---

## Prompt for next session

```
Resume work on lazyclaw at /Users/blckit/Desktop/Code_Projects/lazyclaw.
Read docs/plans/next-session-handoff-2026-05-18.md first — it has the full
state, what was solved 2026-05-17/18, and the ranked open items.

Top priority is the "Unified Code Session per project" refactor — P1
(session persistence) is the next chunk, estimated ~1.5 h. After P1
lands, James's BPO appraisal-bot project should resume the same Claude
Code MCP session across recon + scaffold + iterate turns.

Before coding P1, verify the deployed state: container is up, mcp-upwork
self-check passes, and a quick "check James last messages" query returns
the 6-city list with a supersession note (proving F2 is live).

Then plan P1 file-by-file before touching code — schema migration,
runner.py changes, fallback logic. Get my sign-off on the plan before
implementing. After P1 ships, retest by asking LazyClaw to do the next
piece of James's project — it should reuse the existing session.

Open items 2 and 3 (Bug E offer-extraction, James DM for credentials)
can wait until P1 is in.
```
