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

Standalone MCP servers (6 active + 4 disabled): Active: `mcp-taskai/` (task intelligence), `mcp-lazydoctor/` (self-healing), `mcp-whatsapp/` (WhatsApp via WA-JS), `mcp-instagram/` (Instagram DMs/feed/stories), `mcp-email/` (Gmail/Outlook/IMAP), `mcp-jobspy/` (job search aggregator). Disabled (source rebuild needed): `mcp-freeride/`, `mcp-healthcheck/`, `mcp-apihunter/`, `mcp-vaultwhisper/`.

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
- **Search API key detection**: `/api/system/about` returns `search_keys: {serper, serpapi}` read directly from `os.environ`, so the Settings → Search tab shows accurate ✓ / missing state without waiting for a first query to bump the quota counter.

## Git Commit Rules

- **No Co-Authored-By**: Do NOT add "Co-Authored-By: Claude" or any AI attribution to commits
- Keep commit messages clean and human-style
