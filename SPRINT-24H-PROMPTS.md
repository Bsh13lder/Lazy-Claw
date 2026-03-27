# LazyClaw — 24-Hour Sprint to Public Release

Copy-paste each prompt into a FRESH Claude Code session. One task per session. No code examples — Claude Code reads the codebase.

**IMPORTANT:** Read CLAUDE.md first in every session. After each session, start the server (`lazyclaw start`) and test basic chat works before moving on.

**ECO mode is CUT from this sprint.** Ship only HYBRID + FULL. ECO (local-only) is a future feature for 32GB+ RAM machines.

---

## SESSION 1: Critical Fixes (All-in-One)

This is the biggest session. Do all critical fixes together since they're small and independent.

```
Read CLAUDE.md and ADR-001-LAZYCLAW-AUDIT.md for project context.

Fix these 5 critical issues. They are all independent — do them in order:

1. CACHE RACE CONDITION: In lazyclaw/runtime/context_builder.py, the module-level caches _capabilities_cache and _mcp_cache are read/written by async functions without any lock. Add an asyncio.Lock to protect all reads and writes to both caches. Wrap the check-and-rebuild logic in "async with lock". Grep the rest of the codebase for similar unprotected global caches and fix them too.

2. INDENTATION BUG: In lazyclaw/runtime/agent.py around line 698-711, the "Include favorite MCP tools" block is indented one level too deep — it sits inside an "if _matched_channels:" block. This means favorite MCP tools are ONLY added when the message mentions channel keywords. Un-indent that entire block by one level so favorite MCP tools are ALWAYS included.

3. MODEL PRICING MISMATCH: In lazyclaw/llm/model_registry.py the fallback model is "claude-opus-4-6" but in lazyclaw/llm/pricing.py the entry is "claude-opus-4-6-20250625" — different keys. Cost tracking is 33x wrong for Opus calls. Add the bare name as an entry in pricing.py. Check ALL models in both files for similar mismatches.

4. locals() BUG: In lazyclaw/runtime/agent.py around line 738, there's a locals().get("_wants_visible", False) call. This is fragile and CPython-dependent. Replace with an explicit variable or dict.

5. FIRE-AND-FORGET CLEANUP: Search the entire lazyclaw/ directory for asyncio.ensure_future() and asyncio.create_task() used in fire-and-forget mode (task not awaited or stored). Add error-logging callbacks to ALL of them. Create a small helper function like _fire_and_forget(coro, name) that wraps the pattern, then use it everywhere.

After all fixes, start the server and send a test message to verify nothing is broken.
```

---

## SESSION 2: ECO Mode — Cut to HYBRID + FULL Only

```
Read CLAUDE.md and ADR-001-LAZYCLAW-AUDIT.md for project context.

The ECO routing system needs simplification. We are shipping only 2 modes: HYBRID and FULL. The old ECO/LOCAL modes are cut for now (they require local models and 32GB+ RAM which most users don't have).

In lazyclaw/llm/eco_router.py:

1. The _route_brain method unconditionally calls _route_paid. This is fine for HYBRID (Haiku brain) and FULL (user-configured brain). But make sure it routes to the CORRECT model per mode:
   - HYBRID: always Haiku brain
   - FULL: reads brain model from user's eco_settings (user-configurable)

2. The _route_worker method should:
   - HYBRID: try Nanbeige local first (if Ollama available), fall back to Haiku if not
   - FULL: reads worker model from user's eco_settings

3. Update MODE_MODELS dict to only have "hybrid" and "full" entries. Remove "eco" and "local" modes or mark them as disabled/future.

4. In lazyclaw/llm/eco_settings.py:
   - Default mode should be "hybrid" (not "eco")
   - Add fields for FULL mode: full_brain_model, full_worker_model, full_fallback_model — these are user-settable
   - Keep eco_mode field but only allow values "hybrid" or "full"

5. Update the eco_set_mode skill to only accept "hybrid" and "full". If someone tries "eco" or "local", respond: "ECO mode (local-only) requires 32GB+ RAM and is coming in a future update. Use HYBRID for the best balance of cost and quality."

6. Make sure response attribution tags show [HYBRID haiku] or [FULL sonnet-4] etc.

7. Clean up any dead code related to the old eco/local modes — unused constants, unreachable branches, etc.

Start the server, set HYBRID mode, send a message. Then set FULL mode, send a message. Both should work.
```

---

## SESSION 3: Security Fixes

```
Read CLAUDE.md and ADR-001-LAZYCLAW-AUDIT.md for project context.

Fix these security issues for public release:

1. SESSION TIMEOUT: Keep at 30 days (720 hours). This is fine for personal use. DO NOT CHANGE.

2. VAULT RATE LIMITING: In lazyclaw/gateway/routes/vault.py there is zero rate limiting. Add rate limiting similar to how auth.py has _login_limiter. Use 30 requests per minute per user for all vault endpoints.

3. SERVER_SECRET VALIDATION: In lazyclaw/gateway/app.py line 52, SERVER_SECRET only checks length >= 32. Add entropy validation — reject secrets that are all the same character, all lowercase only, or have very low randomness. Use a simple Shannon entropy check. If entropy is too low, raise RuntimeError with a helpful message telling the user to run the setup wizard.

4. MISSING DB INDEXES: In lazyclaw/db/schema.sql, add these indexes:
   - CREATE INDEX IF NOT EXISTS idx_job_queue_user_status ON job_queue(user_id, status);
   - CREATE INDEX IF NOT EXISTS idx_site_memory_user_domain ON site_memory(user_id, domain);
   - CREATE INDEX IF NOT EXISTS idx_channel_bindings_user ON channel_bindings(user_id, channel);
   - CREATE INDEX IF NOT EXISTS idx_daily_logs_user_date ON daily_logs(user_id, date DESC);
   Use IF NOT EXISTS so existing databases don't break.

5. Create a .env.example file listing ALL required environment variables with descriptions but NO actual values. This helps new users set up the project.

Start the server and test login + chat to verify nothing broke.
```

---

## SESSION 4: Teams Dead Code + Tab Leaks + Race Conditions

```
Read CLAUDE.md and ADR-001-LAZYCLAW-AUDIT.md for project context.

Clean up the teams system and fix race conditions:

1. DELETE lazyclaw/teams/lead.py entirely — it's 419 lines of deprecated TeamLead class replaced by the delegate tool. Remove any imports of TeamLead from other files (grep for "from lazyclaw.teams.lead" and "TeamLead").

2. DUPLICATE SAVES: Both lazyclaw/teams/executor.py (around line 68-82) AND lazyclaw/skills/builtin/delegate.py (around line 154-165) save browser learnings to site_memory. Remove the save from delegate.py, keep only executor.py.

3. Remove the dead _maybe_research_site() function in delegate.py (marked as "No longer called automatically").

4. TAB LEAKS: In lazyclaw/teams/runner.py and executor.py, when a specialist times out or gets cancelled, tabs from TabManager are never released. Add try/finally blocks around ALL specialist execution to guarantee tab_manager.release() is called.

5. CHROME LAUNCH RACE: In lazyclaw/browser/cdp_backend.py, the _ensure_connected method can be entered by multiple coroutines simultaneously, causing duplicate Chrome processes. Add an asyncio.Lock as an instance attribute so only one coroutine can launch Chrome at a time.

6. MCP IDLE RACE: In lazyclaw/mcp/manager.py, the idle disconnect timer can fire while a tool call is in progress. Add a version counter — each tool call increments it, the timer callback checks if version matches before disconnecting.

7. Remove ROLE_FALLBACK from eco_router.py (unused constant) and task_overrides from eco_settings.py (unused field).

Start the server, send a few messages including one that triggers browser specialist, verify no errors in logs.
```

---

## SESSION 5: TUI — Grid Cards + Timestamps + Watcher Details

```
Read CLAUDE.md and the file tui-mockup.jsx for visual design reference.

Update lazyclaw/cli_tui.py with these TUI improvements:

1. GRID LAYOUT: Change the Activity panel from vertical stack to a flex grid. Cards with specialists or multiple tools take roughly half width (2 per row). Simple completed cards (single tool, done phase) are compact and fit 3 per row. Use Textual's Horizontal container or CSS grid.

2. TIMESTAMPS on each RequestCard:
   - "started HH:MM:SS" — wall-clock time when request was received
   - "→ finished HH:MM:SS" — only shown when done or error
   - Duration in bold: "1.2s" or "2m 14s"
   Add started_at (datetime) and finished_at (Optional[datetime]) to RequestSnapshot. Record started_at on RequestRegistered, finished_at on RequestCompleted.

3. STEP NAME: Show "step 3/7: analyzing job listings" instead of just "step 3". Add step_name field to RequestSnapshot. Populate from current tool name or specialist description.

4. WATCHER DETAILS: Update JobsBar to show for each watcher:
   - Title, interval (every 5m), last check (2m ago), next check (3m), and the watcher prompt in quotes
   - For cron jobs: title, interval, last run, next run
   Decrypt the watcher prompt and interval from the database (use existing server key derivation).

Look at tui-mockup.jsx for exactly how each element should be laid out.

Start the server with TUI (lazyclaw start) and verify the dashboard renders correctly.
```

---

## SESSION 6: TUI — Settings Panel + Cancel Controls

```
Read CLAUDE.md and the file tui-mockup.jsx for visual design reference.

Two features for the TUI:

SETTINGS PANEL (toggle with key 3):
- Press 3 to show settings, press 3 again to return to dashboard
- When settings open: hide Activity, Background Tasks, Jobs, Logs, Costs, AI Routing. Show Settings panel instead. SystemBar stays visible on top.
- Settings layout: columns for AI/Models (mode: hybrid/full, model selections for FULL mode, show badges, budget), Teams/Agent (team mode, critic, auto delegate, max specialists, RAM limit, timeout), Browser (browser, headless, delays, max tabs, CDP port), Permissions (default rule, browser/shell/file/vault access levels), Channels (Telegram/Discord/WhatsApp toggles)
- Navigation: Tab between settings, Enter to toggle/cycle values
- Changes save immediately to existing settings APIs

CANCEL CONTROLS:
- Key "x" on a focused RequestCard cancels that task
- Admin commands: /cancel <id>, /cancel bg, /cancel all (with confirmation)
- Cancel flow: find task → trigger CancellationToken → update card phase to cancelled → log it
- Add "x" to footer keybindings

The CancellationToken infrastructure already exists — find it and wire it into these controls.

Look at tui-mockup.jsx for the settings panel layout. Start the server, test key 3 toggle, test cancel on an active task.
```

---

## RUN ORDER (6 sessions, ~24 hours)

```
Session 1: Critical fixes (cache, indent, pricing, locals, fire-forget)  — 2h
Session 2: ECO mode cut → HYBRID + FULL only                            — 1.5h
Session 3: Security (sessions, vault, SECRET, indexes, .env.example)     — 1.5h
Session 4: Dead code + tab leaks + race conditions                       — 2h
Session 5: TUI grid cards + timestamps + watchers                        — 2h
Session 6: TUI settings + cancel                                         — 2h
                                                                   Total: ~11h
```

Remaining time: test everything end-to-end, write README, push to GitHub.

After Session 4, the backend is production-ready.
After Session 6, the TUI is polished.
Then: README + push = public.
