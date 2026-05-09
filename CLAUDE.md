# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Documentation

- **[DOCS.md](DOCS.md)** — Complete function & class reference. Keep updated when adding new modules.
- **[TODO.md](TODO.md)** — Phase plan with checkable items. All roadmap/status tracking lives here.
- **[MEMORY.md](/.claude/projects/.../memory/MEMORY.md)** — Persistent memory index (user prefs, feedback, project status, references).

## File Size Rules

- **CLAUDE.md must stay under 40,000 characters.** Currently ~8K. This file is loaded every message — keep it lean.
- **Never dump file maps, API endpoints, DB schemas, env vars, or CLI command lists here.** Those are derivable from the codebase or already in DOCS.md.
- **Use TODO.md** for roadmap, phase plans, task tracking, and implementation status.
- **Use MEMORY.md** for learned patterns, user corrections, project context, and references.
- If CLAUDE.md approaches 40K chars, move content to the appropriate file before adding more.

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately -- don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update MEMORY.md with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- Skip this for simple, obvious fixes -- don't over-engineer

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -- then resolve them
- Zero context switching required from the user

## Task Management

1. **Plan First**: Write plan to plan file with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Capture Lessons**: Update MEMORY.md after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.
- **No Hardcoded Tools**: Everything goes through the skill registry. Agent runtime discovers tools dynamically.
- **Encrypt Everything**: User content is always encrypted at rest. No exceptions.
- **Extract, Don't Rewrite**: Proven LazyTasker code is adapted, not rewritten from scratch.
- **Test Each Phase**: Each phase has a clear verification step before moving on.
- **Never Guess Data**: NEVER fabricate prices, stats, version numbers, or any factual data. Always look up real values from official sources. If you can't find it, say so.

---

## Project Overview

**LazyClaw** is an open-source (MIT) E2E encrypted AI agent platform written in Python (FastAPI + asyncio + aiosqlite). Competes with OpenClaw by offering AES-256-GCM encryption on all user data, native MCP support, a Python-native skill system, and multi-channel messaging.

> "OpenClaw, but encrypted and Python-native."

**Status**: Early beta (v0.1). Solo developer, daily updates. Optimized with Claude Code — architecture reviewed and iterated continuously.

### Key Differentiators vs OpenClaw
- **E2E Encryption**: AES-256-GCM on all user content. OpenClaw stores everything in plaintext.
- **Python-native**: Full Python stack. Python AI ecosystem is 10x larger than TypeScript.
- **Native MCP**: First-class MCP client AND server. OpenClaw uses a hacky converter.
- **Encrypted Credential Vault**: API keys stored encrypted, not plaintext .env files.

## Architecture

16 modules in `lazyclaw/` + supporting infrastructure:

| Component | Path | Purpose |
|-----------|------|---------|
| **Gateway** | `gateway/` | FastAPI HTTP+WS entry point (19 route files). Session auth, CORS, routing |
| **Agent Runtime** | `runtime/` | TAOR agent loop, context builder, tool dispatch, task runner, team lead |
| **Lane Queue** | `queue/` | FIFO serial execution per user session |
| **Skills** | `skills/` | Instruction (NL), Code (sandboxed), Plugin (pip). 37 builtin + 9 survival skills |
| **Channels** | `channels/` | Telegram native adapter + WhatsApp/Instagram/Email via MCP servers |
| **Browser** | `browser/` | CDP-only browser control, JS extractors, site memory |
| **Computer** | `computer/` | Native subprocess + WebSocket connector (remote) |
| **Memory** | `memory/` | Encrypted personal facts, conversation history, compression, daily/weekly logs |
| **MCP** | `mcp/` | Native client + server + bridge to skill registry |
| **Crypto** | `crypto/` | AES-256-GCM, PBKDF2, credential vault |
| **Teams** | `teams/` | Specialists (browser, research, code) + delegate skill + parallel execution |
| **Replay** | `replay/` | Session trace recording, playback, shareable tokens |
| **Tasks** | `tasks/` | Encrypted task store with CRUD, nagging reminders, recurring tasks |
| **Notifications** | `notifications/` | Telegram push notifications for background tasks |
| **Pipeline** | `pipeline/` | CRM-style pipeline store for workflow tracking |
| **Survival** | `survival/` | Gig economy tools — job matching, applications, invoices, profiles |
| **LazyBrain** | `lazybrain/` | Python-native Obsidian-grade PKM — encrypted notes + `[[wikilinks]]` + backlinks + force-directed graph + daily journal + auto-capture + **callouts** + **transclusion** (`![[note]]`) + **YAML frontmatter panel** + **canvas** (React Flow spatial boards). **Single home for every memory source**: tasks, personal_memory, daily_logs, site_memory, lessons, layers.py all auto-mirror here with `owner/{user,agent}` + kind tags. **AI-native**: `suggest_links` + `suggest_metadata` (auto-title/tag) + `semantic_search` + `ask` (RAG with `[[citations]]`) + `topic_rollup` + `morning_briefing` — all route through `EcoRouter(ROLE_WORKER)` with graceful offline fallback when Ollama's down. **28 NL skills**. Web UI ships ⌘K command palette, ⌘O quick switcher, outline pane, hover preview, and an Obsidian-Minimal-inspired violet theme scoped under `.lazybrain-root`. |

Supporting: `llm/` (multi-provider router + ECO mode + Claude CLI provider), `heartbeat/` (cron daemon), `permissions/` (allow/ask/deny + audit), `db/` (aiosqlite + connection pool), `web/` (React 19 + TypeScript + Vite + Tailwind — 12 pages: Overview, Activity, Replay, Audit, SkillHub, Skills, Templates, Jobs, MCP, Memory, Vault, Settings + persistent chat sidebar with live BrowserCanvas), `n8n-custom/` (n8n webhook integration + 6 management skills + templates).

Standalone MCP servers (8 active + 4 disabled): Active: `mcp-taskai/` (task intelligence), `mcp-lazydoctor/` (self-healing), `mcp-whatsapp/` (WhatsApp via WA-JS), `mcp-instagram/` (Instagram DMs/feed/stories), `mcp-email/` (Gmail/Outlook/IMAP), `mcp-jobspy/` (job search aggregator + NaN/float-safe normalizer), `mcp-scraper/` (crawl4ai-backed crawl/extract/search bundle), `mcp-upwork/` (Apache-2.0 fork of vanooo/upwork-mcp — 18 tools via CDP, shares Brave profile + cookies). Disabled (source rebuild needed): `mcp-freeride/`, `mcp-healthcheck/`, `mcp-apihunter/`, `mcp-vaultwhisper/`.

## Build & Run

```bash
./install.sh              # One-command install (Python + deps + setup)

# Or manually:
pipx install --editable . # Global install via pipx
lazyclaw setup            # First-time setup wizard
lazyclaw start            # Full server (FastAPI + Telegram + Heartbeat)
lazyclaw                  # Chat REPL only
```

Default port: **18789**. MCP servers run standalone via `python -m mcp_taskai` etc.

## E2E Encryption

All user content encrypted before storage. Server never sees plaintext.

- Registration generates random `encryption_salt` per user
- Key derivation: `PBKDF2(password, salt, 600k iterations, SHA-256)` → per-user DEK (Data Encryption Key)
- Envelope encryption: DEK itself stored encrypted with server master key
- Storage format: `enc:v1:<base64-nonce>:<base64-ciphertext>`
- Server-side key for daemon ops: `PBKDF2(SERVER_SECRET + user_id, fixed_salt, 600k)`
- **Recovery phrase**: BIP-39 mnemonic generated at registration — user can re-derive their key
- **Encrypted**: conversations, memory, skills, vault, jobs, channel configs
- **Plaintext** (needed for queries): IDs, timestamps, status, cron expressions, domains

## Key Patterns

These are non-obvious architectural decisions -- read the code for implementation details:

- **User isolation**: ALL queries scoped by `user_id`. No cross-user data access.
- **No hardcoded tools**: All tools from skill registry. Agent discovers dynamically.
- **Smart tool selection**: 128 builtin skills + ~67 MCP tools registered, but only 4 base tools sent per message (search_tools, recall_memories, save_memory, delegate). LLM discovers rest via search_tools(). ~95% token savings.
- **Lane Queue**: Serial per-user foreground execution. Background tasks run in parallel via TaskRunner.
- **Background tasks**: `run_background` skill → TaskRunner spawns independent Agent → Telegram push on completion.
- **Delegate tool**: Agent calls `delegate(specialist, instruction)` inline — no separate team lead LLM call.
- **ECO routing**: 3 modes, 3 roles (Brain, Worker, Fallback). HYBRID (default): Sonnet 4.6 brain + `gemma4:e2b` local worker via Ollama ($0) + Haiku fallback. FULL: Sonnet brain + Haiku workers + Sonnet fallback. CLAUDE: Haiku API brain (native tools) + Haiku workers + Claude CLI fallback ($0 via subscription). Old eco_on/local modes (Nanbeige/Qwen) removed in commit cf1e309 — replaced by Gemma 4 E2B. `eco_router.py` routes by role (ROLE_BRAIN vs ROLE_WORKER). Models from `MODE_MODELS` dict in `model_registry.py`.
- **MLX backend** (deprecated): `mlx_provider.py` kept for compatibility but unused. Ollama (`ollama_provider.py`) is the live path for local models. Current worker is `gemma4:e2b`. Nanbeige/Qwen references in the codebase are historical.
- **RAM monitor**: `ram_monitor.py` tracks system + AI model memory. `/ram` Telegram command. TUI status bar shows RAM %. Uses macOS `memory_pressure` for accurate free %.
- **Telegram /local command**: `/local on|off|worker|brain|restart` — start/stop MLX servers, auto-switches ECO mode.
- **Unified browser tool**: Single `browser` skill with 7 actions (read, open, click, type, screenshot, tabs, scroll). CDP-only, no Playwright.
- **Brave browser**: Auto-detected (Brave > Chrome > Chromium). Built-in ad/tracker blocking = cleaner pages for LLM.
- **Fast chat path**: Simple messages get last 6 messages, SOUL.md only (no capabilities/memories/tools).
- **Hybrid memory picker**: `context_builder.py` no longer injects the top-10 personal memories by importance alone — it fetches a pool of 40 and picks 5 by importance (stable facts) + 5 by keyword overlap with the current user message (context-relevant). Zero extra LLM cost, uses EN+ES stopword filter. Falls back to pure importance when no message or no overlap. Fixes the "memory exists but agent can't find it" loop (see `_pick_hybrid_memories` in context_builder.py).
- **Layered summaries**: Daily logs (auto, gpt-5-mini) + weekly + injected into agent context. Skips 90s LLM re-summarization.
- **Stuck detector batch-ops**: `lazybrain_*` tools added to `_BATCH_OP_PREFIXES` in `stuck_detector.py` alongside `email_` / `whatsapp_` / `instagram_` — limit 10 consecutive calls before stuck. Natural "search → fetch each hit" patterns no longer false-trigger at 3.
- **recall_memories vault hint**: on a miss, `recall_memories` now includes the list of vault key names (names only, never values) so the brain pivots to `vault_get(key=...)` instead of looping memory queries. Credentials live in the vault, never in memory.
- **Shared browser profiles**: CDP uses `browser_profiles/{user_id}/` with system browser. Login once → all tools see cookies.
- **Headless auto-launch**: Brave/Chrome launches headless automatically. `open` action launches visible for user-facing tasks.
- **Human-like delays**: Random 0.2-1.5s between clicks, 0.03-0.12s typing, 0.8-1.5s navigation.
- **Semantic Snapshots**: Accessibility tree text (50KB) instead of screenshots (5MB).
- **MCP bridge**: External MCP tools registered as first-class skills. No separate path.
- **MCP parallel startup**: `asyncio.gather` connects all MCP servers simultaneously (~2s instead of 12s).
- **PBKDF2 LRU cache**: Key derivation cached (420ms→0ms per message, 4+ calls per message).
- **DB connection pool**: Single shared aiosqlite connection (14ms→0.2ms per query).
- **Telegram security**: Admin chat lock (first /start claims). Unauthorized chats blocked. Screenshots auto-forwarded.
- **Telegram retry**: `_telegram_send_with_retry()` with exponential backoff on network errors.
- **CancellationToken**: Cooperative cancellation from CLI → agent → specialists. Double Ctrl+C support.
- **MODE_CLAUDE**: API brain (Haiku with native tool_use) + Claude CLI fallback ($0 via subscription). 529 resilience — auto-retries on overloaded.
- **Token tracking**: OpenAI streaming reads usage chunk after finish_reason. Anthropic field names normalized.
- **TAOR loop**: Think-Act-Observe-Reflect cycle in `taor.py`. Parallel tool execution via `asyncio.gather`. Tools run concurrently when independent; results merged before next think step.
- **Context compaction**: 5-layer memory stack — live messages → sliding window (15 msgs full) → daily summary → weekly rollup → long-term facts. Each layer injected into context at build time. Never re-summarizes mid-session.
- **5-layer memory**: Conversation history, compressed summaries, daily logs, weekly rollups, encrypted personal facts. All layers merged in `context_builder.py`.
- **TodoWrite widget**: TUI task list rendered live in status bar. Agent marks items complete via `todo_write` tool during execution. User sees progress without interrupting the agent.
- **WebSocket chat**: `/ws/chat` endpoint in `gateway/routes/chat_ws.py` for real-time streaming in Web UI. Separate from `/ws/connector` (computer control).
- **Browser event bus** (zero-token UI observability): `lazyclaw/browser/event_bus.py` — per-user pub/sub + ring buffer + URL-stamped thumbnail cache. `cdp_backend.py` emits `browser_event` on every user-visible action; `chat_ws.py` has a per-user pump that forwards events as `{type: "browser_event"}` frames. Events NEVER enter LLM context — UI-only, zero token cost. Passwords masked in typed detail lines.
- **Live mode**: `/api/browser/live-mode/start` flips a 5-min per-user flag that makes cdp_backend capture a fresh WebP thumbnail after every action (not just on URL change). Addresses stale-frame bug when the agent uses cheap accessibility-tree reads instead of `screenshot`. `🔄 Refresh` button in BrowserCanvas force-captures one frame on demand.
- **Checkpoints**: `lazyclaw/browser/checkpoints.py` + `request_user_approval` skill. Agent calls before risky actions (submit/pay/book/delete/sign/send); call blocks until user hits Approve/Reject on the canvas or `/api/browser/checkpoint/{approve,reject}`. Same name auto-approves on re-call. 10-min soft-reject timeout.
- **Saved browser templates**: `lazyclaw/browser/templates.py` + `browser_templates` table. Encrypted CRUD (playbook + system_prompt) with plaintext setup_urls, checkpoints, watch_extractor. Skills: `save_browser_template`, `list_browser_templates`, `run_browser_template`, `watch_appointment_slots` (hooks into existing watcher daemon for zero-token slot polling). Ships seed recipes (Cita Previa Spain, Doctoralia). Watcher fires → heartbeat publishes canvas `alert` event + Telegram push.
- **Remote takeover from any channel**: `share_browser_control` NL skill returns a noVNC URL; works identically in Telegram, web chat, CLI. Routes through `remote_takeover.start_remote_session` (Linux + Xvfb/x11vnc) or `start_macos_remote_session` (macOS Screen Sharing). `POST /api/browser/remote-session/start` exposes the same path to the Web UI.
- **n8n integration**: 6 management skills + workflow templates + Docker n8n sidecar. Webhook-triggered automations.
- **Agent Skills compatibility**: Skills authored in Claude Code agent format (YAML frontmatter + markdown body) are importable via `lazyclaw skill import`. LazyClaw parses the skill description and maps it to an Instruction skill automatically.
- **LazyBrain AI features (Phase 19)**: `autolink.py` proposes `[[wikilinks]]` via worker LLM + deterministic substring fallback. `metadata_suggest.py` proposes title + tags reusing vault's existing tags. `embeddings.py` encrypts 768d vectors (`nomic-embed-text` via Ollama, AAD=`notes:embedding`) in `note_embeddings` table; cosine search in-memory (no FAISS needed under 10k notes). `ask.py` RAG over the vault with `[[Note Title]]` citations. `topic_rollup.py` structured rollup (summary / decisions / open questions / sources). `recap.py` morning briefing as `[!tip]` callout appended to today's journal. Every AI feature degrades gracefully when Ollama is down — substring + "LLM unavailable" messaging, never hard-fails.
- **LazyBrain canvas**: `canvas.py` + `canvas_boards` table + React Flow UI (`web/src/components/lazybrain/Canvas.tsx`). Free-form spatial board with text + note-reference nodes, drag/drop, arrows, autosave every 2s. Payload = encrypted JSON blob (AAD=`canvas:payload`). Keyboard: `T` = text node, `N` = note node. Mode toggle alongside Notes / Graph.
- **Obsidian-style markdown**: `callout.ts` splits `> [!kind] title` blocks (info/tip/warning/danger/quote/question/success/todo/bug/example/abstract — 12 kinds) rendered by `CalloutBlock.tsx`. Transclusion `![[Note]]` detected in the wikilink regex and rendered recursively as a collapsible inline card. YAML frontmatter parsed by `frontmatter.ts` (minimal subset — flow & block arrays, scalars, booleans, dates) + rendered by `PropertiesPanel.tsx` as a typed form (date picker / tag chips / status dropdown / number / string).
- **LazyBrain theme scope**: Violet palette (`#a78bfa` + `#16141e` bg) + Inter UI / Source Serif 4 body is scoped under `.lazybrain-root` in `web/src/styles/globals.css`. Rest of the app keeps its emerald identity. Command palette (⌘K) + quick switcher (⌘O) live in `CommandModal.tsx` — zero-dep fuzzy match over actions + note titles + tags.
- **Web search chain (rewritten 2026-05-02)**: `web_search.py` now goes **Brave Search API → mcp-scraper → DuckDuckGo**. Serper + SerpAPI deleted (~250 lines, 14 surfaces) — Brave's 2k/mo free tier covers text and the curated index has way less spam-snippet hallucination than Google scrape. `BRAVE_KEY` env (https://api-dashboard.search.brave.com). `/api/system/about` exposes `search_keys: {brave: bool}` and `search_quota: {brave_used, brave_limit, scraper_used, reset_month}`. Stale `serper`/`serpapi` values in `users.settings.general.search_provider` are auto-coerced to `auto` on read so old rows don't break the Settings UI. Telegram `/search auto|brave|scraper|duckduckgo`.
- **Price/flight/shopping queries auto-route to browser**: `web_search.py` detects price intent (`flight`, `cheapest`, `price of`, `in stock`, `book hotel`, etc.) and returns a structured `[PRICE_QUERY]` instruction with a canonical Google Flights / Google Shopping / Google search URL. The brain then dispatches `browser(action="open", url=...)` and reads the LIVE price card. Never trust search-API snippets for prices — they're cached and hours stale.
- **JSON-LD-first business extraction (`extract_business_info`)**: New mcp-scraper tool at `mcp-scraper/mcp_scraper/extraction_business.py` (intentionally outside `core/` — no crawl4ai dep, pure stdlib `html.parser`). Solves the "8/10 wrong addresses" bug by parsing schema.org JSON-LD (`LocalBusiness`/`Restaurant`/`Store`/`Hotel`/`Dentist`/etc. + nested `PostalAddress`/`ContactPoint`/`openingHoursSpecification`/`geo`) instead of regex-ing the document body. Walks `@graph` wrappers (Yoast/WordPress) and array roots, picks the node with an actual address when chains expose multiple. Returns `confidence: high|medium|low|none` — the brain MUST refuse to report an address from `confidence='none'` and try the `/contact` or `/about` subpage instead. Approach inspired by Scrapling but implemented natively (W3C JSON-LD is a public spec — no copy needed). 12-fixture test suite covers @graph wrappers, contactPoint arrays, malformed JSON-LD recovery, and the cookie-banner-soup pollution bug we used to hit.
- **Scrapling reverse-engineering port (2026-05-02)**: Three new modules in mcp-scraper, ~700 lines total, zero new heavy deps. (1) `mcp_scraper/stealth_http.py`: TLS-fingerprint-impersonating fetcher with `curl_cffi` (`impersonate="chrome"` for browser-grade JA3/JA4) — silently falls back to httpx when curl_cffi is missing. Wired into `infra/fallback_strategies.py:static_fetch_content`. Optional dep: `pip install mcp-scraper[stealth]`. (2) `mcp_scraper/proxy_rotator.py`: thread-safe rotator with cyclic/random/sticky strategies + 3-strike health blacklist with 60s cooldown. (3) `mcp_scraper/adaptive_selector.py`: SQLite-backed element fingerprinting at `~/.lazyclaw/scraper_selectors.db` — saved selector misses → walk DOM, score by tokenized-Jaccard(attrs)+text_similarity with perfect-text-match boost, relocate above 0.7 threshold and update saved CSS path so future calls fast-path again. Statuses: `cold`/`hit`/`relocated`/`broken`. `broken` surfaces silent extractor breakage that today goes unnoticed. New MCP tool `extract_with_adaptive_selector(url, selector_id, initial_css)` for revisit-this-site flows (price-watch, batch business research). 52 new tests across 3 suites all green.
- **mcp-scraper bundle**: `mcp-scraper/` is a single-subprocess crawl4ai bundle exposing crawl/extract/search tools. `web_search` skill auto-falls-back through Brave → mcp-scraper → DuckDuckGo so unauth'd installs still get results. `_call_lock` removed from `mcp/client.py` — pool is one persistent subprocess, not per-call. Scraper auto-dismisses Cookiebot/OneTrust/Iubenda/Quantcast banners via injected JS so EU sites (toniandguy.it etc.) actually render before extraction.
- **Brain-as-dispatcher**: `runtime/agent.py` has a mid-turn pivot detector that re-routes the brain back to dispatch when it starts doing work itself. `dispatch_subagents` is non-blocking — queued user bubbles are visible in Web UI immediately; specialists report back when done. `81f189b` adds keyword-injection routing so "show / edit / delete jobs" surfaces cron-job tools without `search_tools` ping-pong. Parallel `run_background` results consolidated into ONE final reply (`5a71e95`).
- **Lessons v2 (LazyBrain)**: single-card upsert by `(topic, action, intent)` triple — never floods the graph. 5-state outcome machine (proposed / verified / contested / superseded / archived). `kind/shape` (how-to-do-X) split from `kind/fact` (this-is-X). Verification pump: skill outcomes auto-bump confidence, `/confirm` and `/reject` Telegram commands let the user override. Skills vault toggle hides the noisy `#skill` namespace from the default graph.
- **Default permissions**: `core`, `orchestration`, `browser_management`, and `tasks` categories default to `allow` (commits e8abc62 + ac80851). Telegram `/allow`, `/deny`, `/permissions` commands let admin user gate skills without Web UI. Cron jobs / reminders / watcher expiries push the agent's reply directly to Telegram (`e0b5e37`).
- **Task escalation**: `tasks/store.py` schedules advance reminders for important tasks (priority high/critical → 1h, 15m, due-now nags). Telegram replies un-truncated for tasks (`d5197d0`).
- **Auto-promote backgrounds**: `task_runner` auto-promotes a stuck foreground task to a background runner when it exceeds the foreground budget (a164149). Heartbeat NameError fixed (34d2f26).
- **Docker Claude CLI persistence**: `docker-compose.yml` mounts a named volume at `~/.claude` inside the lazyclaw container so `claude login` persists across `docker compose down/up`. Boot warning fires if the volume is empty and `MODE_CLAUDE` is selected (`0231301`).
- **Chat page + AgentConsole**: dedicated `/chat` route (`web/src/pages/ChatPage.tsx`) with a collapsible `AgentConsole.tsx` dashboard showing agent status, queued items, active background tasks, and live BrowserCanvas alongside the conversation. Persistent ChatSidebar still available on every other page.
- **LazyBrain UX polish**: `GraphView.tsx` photon-style animated wikilink edges (commit db2cc74), `PageListSidebar.tsx` richer per-note metadata + freeze-toggle persisted in localStorage (`98ef8d7` — never auto-derive a layout the user pinned), `FilterBar.tsx` collapsible (saves vertical room on dense graphs).
- **JobSpy NaN fixes**: `mcp-jobspy/normalize.py` is a pure unit-testable normalizer that handles `bool(NaN) == True` (3/8 real Indeed rows hit it), `str(NaN) == "nan"` leaks, and float salaries (`$50.0` → `$50`). Direct + MCP paths share the same shape via `normalize_row()`. Surfaces `date_posted`, `is_remote`, `currency`, `job_type`. Prefers `job_url_direct` over `job_url`.
- **Survival NL profile**: `SkillsProfile` now exposes `default_search_sites`, `default_results_per_search`, `default_hours_old`, `max_tiny_gig_budget`, `branding_mode`, `preferred_categories`, `work_hours`, `max_concurrent_jobs` — every search-affecting setting tunable via NL. Default profile ships Python-leaning starter (skills=python/fastapi/scraping/automation, platforms=upwork+indeed, $20 min, $100 tiny cap) so first-time users skip the "set profile first" wall.
- **Upwork MCP fork**: `mcp-upwork/` exact-copy fork of vanooo/upwork-mcp (Apache-2.0, NOTICE preserved). 3 surgical patches — `LAZYCLAW_BROWSER_PROFILE_DIR` env honored so MCP shares user's existing Brave profile + cookies (one login, no second account); `LAZYCLAW_CDP_PORT` env for port choice; no tool renames (collision-safe via `MCP_PREFIX` bridge). Bundled in `BUNDLED_MCPS` with `inject_user_context: True`. Mirror in `production/mcps/mcp-upwork/`. No Reddit MCP bundled — verified none exists in modelcontextprotocol/servers; `reddit_watch_skill` covers zero-auth public-JSON discovery.
- **Host Brave bridge** (Docker → host browser): `scripts/install-host-brave-bridge.sh` writes a launchd plist (macOS) that runs Brave with the user's profile + a CDP origin token, drops `data/.host_bridge_installed` for the container to detect, and bootstraps via `launchctl`. `host_browser_skill` branches messaging on the marker (helper installed → "kick it"; missing → install one-liner OR one-shot manual). `mcp/manager.py` injects `LAZYCLAW_CDP_HOST=host.docker.internal` for in-container MCPs when `is_docker_runtime()`. `cdp_backend._resolve_host_preference` prefers `LAZYCLAW_HOST_CDP_TOKEN` env over per-user DB token. Activate end-to-end with `make host-bridge && make rebuild`. Plist `KeepAlive={SuccessfulExit:false}` — restarts on crash, respects manual Cmd+Q. `make host-bridge-{status,restart,uninstall}` for lifecycle.
- **Container Chromium fallback**: `config.py:_detect_browser` globs Playwright's bundled chromium at `$PLAYWRIGHT_BROWSERS_PATH/chromium-*/chrome-linux/chrome` (+ macOS variant) when no system Brave/Chrome found — `browser_executable` was empty in slim images before, silently breaking `_browser_runtime_available()` and noVNC takeover.
- **Upwork-branded proposals**: `draft_proposal_skill.py` + `apply_skill.py` produce 6-block letters (warm opener + transparency bullets + numbered phase plan + GitHub link + discovery-call CTA + "— Bsh" sign-off). 150-word cap removed in lazyclaw branding mode; description excerpt 300→1500 chars so phases match the actual brief; `max_tokens` 500→1100. Offline fallback letter mirrors the structure.
- **Smart-intake task suggester**: `lazyclaw/tasks/smart_intake.py` — worker LLM (`ROLE_WORKER`, 3s hard timeout, graceful Ollama-down fallback) suggests deadline + project (category) for new tasks based on title + recent task buckets. `AddTaskSkill` calls it when both `due_date` and `reminder_at` are omitted; confident suggestions auto-fill `reminder_at`, time-sensitive uncertain ones return a clarification prompt. `_smart_enrich` is now a back-stop (only fires when intake didn't set a category). Confirmation reply shows `Project: <category>`.
- **Project lane in plan_research**: 4th parallel lane `_lane_project_tasks` groups active todo tasks by category, scores buckets by message-token overlap (existing context_builder tokenizer), surfaces top 2 buckets × 3 tasks under "### Other tasks in this project". No DB columns added — category is the de-facto project handle.
- **Quiet scheduled pushes**: `TelegramNotifier` + `PrefixedTelegramNotifier` accept `verbose` (True for foreground; False for cron / reminder / watcher). Quiet mode drops the stats footer + tools-used line + `<pre>` wrapping so scheduled messages read as normal text instead of engineering telemetry.
- **Jobs page UX (Phase 17.1)**: `web/src/pages/Jobs.tsx` — Type tabs (All / Recurring / One-off) with localStorage persistence + per-tab counts. Type badge per card (🔁 / 1× / 🔔). Click card → inline editor (name / instruction / cron / context) with live human-readable cron preview via `web/src/lib/cronReadable.ts`. `OutcomeChip` shows green "Ran OK" / red "Failed" + tooltip after first run. Backend: `agent_jobs` gains `last_status` + `last_error` (encrypted, idempotent migration); `orchestrator.mark_run_outcome()` records after each cron tick; `HeartbeatDaemon._check_due_jobs` captures lane result and detects raised exceptions OR `"Error processing message:"` prefix. New `EditJobSkill` (edit_job) fuzzy-matches by name and patches name/instruction/cron/context, validating cron up-front so the LLM can self-correct. Includes the `is_due(next_run)` fix so freshly-created jobs no longer fire on the next tick.
- **Slim heartbeat path** (commit 1d943e8): Tier-A reminders (simple due-now nags) skip SOUL.md + the 95% of tools they don't need — agent loads only the reminder-relevant tool set, cuts heartbeat-tick cost to a fraction of a normal turn.
- **Auto-save browser templates on success**: `cdp_backend` records the action sequence on a successful flow; the agent can promote it to a saved `browser_templates` row without an explicit "save this" instruction. Closes the teach-loop — once the agent successfully books a slot / files a form, that recipe is replayable.
- **LazyBrain force layout (collision-aware)**: `web/src/components/lazybrain/ForceSimulation.ts` — d3-style alpha decay (1.0 → 0 over ~120 ticks), per-node mass = `1 + sqrt(deg) * 0.4` (hubs push hubs ~30× harder than leaf-leaf), per-edge spring strength = `1/min(deg(a), deg(b))`, hard 2-iteration collision pass that physically pushes overlapping circles apart by half the overlap. `↻ Re-flow` button bumps alpha to 1.0 for full redistribution; auto-unfreezes if Locked. Pinned nodes stay put. Matches Obsidian / Logseq / d3-force — node circles can no longer visually intersect. CSS animation keyframes (sun-core pulse, corona breath, edge-flow) live entirely on the GPU compositor.
- **LazyBrain owner tags + shape icons**: `journal.py` + `cli_migrate_lazybrain.py` stamp `owner/user` on journals + personal facts (kind=fact); `owner/agent` on learned_preference / context / layers / daily_logs. Daily journals now show under the "You" tab instead of Unknown. 4 missing shape badges (`shape`, `shape-pending`, `shape-failed`, `shape-known-bad`) added to `BADGE_MAP` + `CATEGORY_ICONS` (Wrench / Hourglass / AlertTriangle / Ban). `survival` badge bumped to "Sv" to avoid collision with the new shape "S". FilterBar chips and MemoCard now render real icons instead of generic FileText.
- **LazyBrain daily timeline sidebar**: `PageListSidebar.tsx` — new "Past days" section groups every reachable note (recent + journal + tasks + pinned) by created-at day for the last 14 days, attaches watcher fires (price drops, slot openings) on the same day, and extracts each day's journal + rollup into distinct rows above the long note list. Today/Yesterday labels for the obvious cases, weekday labels for the current week. Open-day set persisted in `lazybrain-sidebar-days-open`; default = only today is open. Watcher trigger count rendered as ⚡ badge in section header.
- **Goal Executor (Phase 20)**: `lazyclaw/runtime/goal_executor.py` — autonomous high-level objectives. Composes existing `plan_research` + `fix_plan.build_fix_plan` + `dispatcher` + `team_lead` + `lazybrain.semantic_search` — no new architecture. State machine: `DRAFTING → AWAITING_USER_INFO → EXECUTING → DONE/BLOCKED/FAILED/ABORTED`. Encrypted `goals` table (Fernet, AAD `user:<id>:goals:title` and `…:goals:plan`). Key UX: questions surface in **one batch upfront** (the brain LLM's `questions[]` field), not drip-asked turn-by-turn — that's the actual win over Chrome Auto Browse. 6 skills: `start_goal`, `answer_goal_questions`, `goal_status`, `list_goals`, `abort_goal`, `goal_progress_report`. No auto-cron — `goal_progress_report` is callable from a user-wired `[GOAL_PROGRESS]` cron via existing `schedule_job` (and `_SLIM_HEARTBEAT_PREFIX_RE` includes the prefix so the daily fire costs ~5k tokens). v1 = browser-only specialist; multi-channel goals deferred to v1.1.
- **Multi-account browser identity** (Phase 20): `lazyclaw/browser/profile_resolver.py` — single source of truth replacing 15 inlined `config.database_dir / "browser_profiles" / user_id` callsites. `resolve_profile_dir(config, user_id, account_slug=None)` — default returns the legacy path (back-compat); with a slug returns `<db>/browser_profiles/<user_id>/accounts/<slug>/`, fully isolated Chromium profile. Validated slug regex `[a-z0-9][a-z0-9_-]{0,31}`. Account registry lives in `users.settings.browser.accounts` JSON (no new table). Skills: `register_browser_account`, `list_browser_accounts`, `switch_browser_account`. Solves the two-Reddit-accounts-for-two-businesses problem — cookies/storage never collide because each account is a real separate Chromium `--user-data-dir`. CDP backend accepts `account_slug` arg + late-bind setter.
- **Per-domain browser cadence** (Phase 20): `lazyclaw/browser/cadence.py` — frozen `CadenceProfile` lifted out of inline `random.uniform()` calls in `human_input.py`. Eight tunable axes (`click_pause_ms`, `type_speed_ms`, `word_boundary_ms`, `micro_pause_ms`, `scroll_step_ms`, `post_scroll_dwell_ms`, `dwell_after_load_ms`, `batch_action_throttle_s`). Lookup chain: user override → `DOMAIN_OVERRIDES` → `DEFAULT`. Bot-sensitive sites (`reddit.com`, `x.com`, `twitter.com`, `instagram.com`, `facebook.com`, `linkedin.com`) ship 1.5–1.6× slower defaults. Subdomain match automatic (`old.reddit.com` → `reddit.com`). NL skill `tune_browser_cadence(domain, factor, fields?)` persists per-user multipliers in `users.settings.browser.cadence_overrides` — no new table. `cdp_backend._set_current_domain_from_url` invoked on every navigation; `_active_cadence()` resolves on every click/type/scroll. The pre-Phase-A hardcoded ranges become DEFAULT — zero behavior change for any domain that isn't in DOMAIN_OVERRIDES or doesn't have a user override.

## Git Commit Rules

- **No Co-Authored-By**: Do NOT add "Co-Authored-By: Claude" or any AI attribution to commits
- Keep commit messages clean and human-style
