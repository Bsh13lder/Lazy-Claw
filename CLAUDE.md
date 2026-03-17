# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Documentation

- **[DOCS.md](DOCS.md)** — Complete function & class reference for the entire codebase. Lists all modules, classes, functions with signatures and descriptions. Keep updated when adding new modules.

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
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes -- don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -- then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to plan file with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section after completion
6. **Capture Lessons**: Update MEMORY.md after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.
- **No Hardcoded Tools**: Everything goes through the skill registry. Agent runtime discovers tools dynamically.
- **Encrypt Everything**: User content is always encrypted at rest. No exceptions.
- **Extract, Don't Rewrite**: Proven LazyTasker code is adapted, not rewritten from scratch.
- **Test Each Phase**: Each phase has a clear verification step before moving on.
- **Never Guess Data**: NEVER fabricate prices, stats, version numbers, or any factual data. Always look up real values from official sources (docs, APIs, web search). If you can't find it, say so — don't make it up.

---

## Project Overview

**LazyClaw** is an open-source (MIT) E2E encrypted AI agent platform written in Python. It competes with OpenClaw by offering AES-256-GCM encryption on all user data, native MCP support, a Python-native skill system, and multi-channel messaging — all backed by a clean FastAPI gateway.

### Tagline
> "OpenClaw, but encrypted and Python-native."

### Key Differentiators vs OpenClaw
- **E2E Encryption**: AES-256-GCM on all user content (memory, conversations, credentials, skills). OpenClaw stores everything in plaintext.
- **Python**: Full Python stack (FastAPI, asyncio, aiosqlite). Python AI ecosystem is 10x larger than TypeScript.
- **Native MCP**: First-class MCP client AND server. OpenClaw uses a hacky converter (mcporter).
- **Encrypted Credential Vault**: API keys stored encrypted in SQLite, not plaintext .env files.
- **Mobile Ready**: Architecture supports Flutter mobile app (LazyTasker-proven).

### Heritage
LazyClaw extracts and generalizes proven components from [LazyTasker](https://github.com/...) — an E2E encrypted task management app with AI agent capabilities. LazyTasker becomes an optional plugin for LazyClaw.

## Architecture

10 core components, all in `lazyclaw/` Python package:

| Component | Path | Purpose |
|-----------|------|---------|
| **Gateway** | `gateway/` | FastAPI HTTP+WS entry point. Session auth, CORS, routing |
| **Agent Runtime** | `runtime/` | Builds system prompt (SOUL.md + memory + skills), LLM calls, tool dispatch |
| **Lane Queue** | `queue/` | FIFO serial execution per user session. Prevents race conditions |
| **Skills** | `skills/` | 3 types: Instruction (NL), Code (sandboxed Python), Plugin (pip packages). Unified registry |
| **Channels** | `channels/` | Telegram, WhatsApp, Signal, Discord, SimpleX adapters. Unified InboundMessage |
| **Browser** | `browser/` | Playwright CDP, Semantic Snapshots (accessibility tree), page reader, site memory |
| **Computer** | `computer/` | Dual mode: native subprocess (local) + WebSocket connector (remote) |
| **Memory** | `memory/` | Encrypted personal facts, conversation history, daily logs. Phase 2: vector search |
| **MCP** | `mcp/` | Native client (connect to MCP servers) + server (expose tools as MCP) |
| **Crypto** | `crypto/` | AES-256-GCM, PBKDF2, encrypted credential vault, DB field helpers |
| **Teams** | `teams/` | Multi-agent teams: team lead, specialists, parallel execution, critic review |
| **Replay** | `replay/` | Session replay: trace recording, playback, shareable tokens |

Supporting modules: `llm/` (multi-provider router), `heartbeat/` (proactive daemon + cron), `voice/` (STT/TTS), `db/` (aiosqlite + migrations).

### Key File Map

**Implemented** (working):
| File | Purpose |
|------|---------|
| `lazyclaw/cli.py` | CLI entry point: chat REPL (default), setup wizard, full server start |
| `lazyclaw/cli_admin.py` | Async admin functions called by chat REPL slash commands |
| `lazyclaw/config.py` | Environment variable loading via python-dotenv, Config dataclass (log_level, tool_timeout) |
| `lazyclaw/crypto/encryption.py` | AES-256-GCM encrypt/decrypt, PBKDF2 key derivation, enc:v1: format |
| `lazyclaw/db/schema.sql` | Core database schema (22 tables) |
| `lazyclaw/db/connection.py` | aiosqlite connection, WAL mode, init_db, db_session context manager |
| `lazyclaw/llm/providers/base.py` | LLMMessage, LLMResponse, ToolCall, StreamChunk dataclasses, BaseLLMProvider ABC |
| `lazyclaw/llm/providers/openai_provider.py` | OpenAI provider with tool calling + streaming support |
| `lazyclaw/llm/providers/anthropic_provider.py` | Anthropic provider with tool_use/tool_result + streaming support |
| `lazyclaw/llm/router.py` | Model-prefix-based provider routing |
| `lazyclaw/logging_config.py` | Logging setup: suppress noisy libs, rotating file handler, configurable level |
| `lazyclaw/runtime/agent.py` | Core agent: multi-turn agentic loop with tool calling, smart tool selection |
| `lazyclaw/runtime/callbacks.py` | AgentEvent + AgentCallback protocol (on_event, on_approval_request) |
| `lazyclaw/runtime/personality.py` | SOUL.md loader, system prompt builder |
| `lazyclaw/runtime/tool_executor.py` | Dispatches ToolCall to skill registry, execute_allowed, timeout protection |
| `lazyclaw/skills/base.py` | BaseSkill ABC with to_openai_tool(), display_name property |
| `lazyclaw/skills/registry.py` | Unified skill registry, register_defaults(), get_display_name(), core/MCP tool filtering |
| `lazyclaw/skills/builtin/web_search.py` | DuckDuckGo web search via ddgs (no API key needed) |
| `lazyclaw/skills/builtin/get_time.py` | Timezone-aware current time |
| `lazyclaw/skills/builtin/calculate.py` | Safe AST-based math calculator |
| `lazyclaw/channels/base.py` | ChannelAdapter ABC, InboundMessage/OutboundMessage |
| `lazyclaw/channels/telegram.py` | Telegram adapter: rich specialist grid, completion summaries, edit throttling |
| `lazyclaw/gateway/app.py` | Minimal FastAPI: health check + agent chat endpoint |
| `lazyclaw/main.py` | Module entry point (python -m lazyclaw) |

| `lazyclaw/gateway/auth.py` | Session auth, user management, bcrypt passwords |
| `lazyclaw/gateway/routes/memory.py` | Memory + daily log REST endpoints |
| `lazyclaw/gateway/routes/skills.py` | Skill CRUD + AI generation endpoints |
| `lazyclaw/gateway/routes/vault.py` | Credential vault REST endpoints |
| `lazyclaw/gateway/routes/browser.py` | Browser task CRUD, takeover, live view, site memory (15 endpoints) |
| `lazyclaw/runtime/context_builder.py` | Assembles system prompt: personality + capabilities (skills, MCP, config) + memory |
| `lazyclaw/runtime/events.py` | Event kind constants (17 types) + WorkSummary frozen dataclass |
| `lazyclaw/runtime/summary.py` | Work summary builder + CLI/Telegram formatters |
| `lazyclaw/cli_chat.py` | Extracted chat loop: inline events, compact approvals, spinner, polling |
| `lazyclaw/cli_dashboard.py` | Rich dashboard panel for /? status queries during agent work |
| `lazyclaw/queue/lane.py` | FIFO per-user lane queue |
| `lazyclaw/skills/sandbox.py` | AST-validated restricted Python execution |
| `lazyclaw/skills/manager.py` | User skill CRUD (DB-backed, encrypted) |
| `lazyclaw/skills/writer.py` | LLM-powered code skill generation |
| `lazyclaw/skills/instruction.py` | Natural language template skills |
| `lazyclaw/skills/builtin/browser.py` | BrowseWebSkill, ReadPageSkill, SaveSiteLoginSkill |
| `lazyclaw/skills/builtin/real_browser.py` | SeeBrowser, ListTabs, ReadTab, SwitchTab, BrowserAction (CDP real Chrome) |
| `lazyclaw/skills/builtin/jobs.py` | ScheduleJob, SetReminder, ListJobs, ManageJob (NL scheduling) |
| `lazyclaw/memory/personal.py` | Encrypted personal facts + keyword search |
| `lazyclaw/memory/daily_log.py` | Encrypted daily summaries + LLM generation |
| `lazyclaw/llm/model_manager.py` | Model catalog, per-user feature assignments |
| `lazyclaw/crypto/vault.py` | Encrypted credential store |
| `lazyclaw/browser/manager.py` | Persistent browser sessions per user, stealth, profiles |
| `lazyclaw/browser/agent.py` | Browser agent: agentic loop, human-in-the-loop, takeover |
| `lazyclaw/browser/page_reader.py` | Lightweight JS page extraction (5 extractors) + LLM analysis |
| `lazyclaw/browser/dom_optimizer.py` | DOM analysis, actionable elements, page summary |
| `lazyclaw/browser/site_memory.py` | Encrypted per-domain learning with auto-cleanup |
| `lazyclaw/browser/backend.py` | BrowserBackend ABC: Playwright + CDP coexist |
| `lazyclaw/browser/cdp.py` | Thin async CDP client: WebSocket protocol + Chrome discovery |
| `lazyclaw/browser/cdp_backend.py` | CDP BrowserBackend impl: real Chrome control via DevTools Protocol |
| `lazyclaw/computer/security.py` | Command/path blocklist validation |
| `lazyclaw/computer/native.py` | Local subprocess execution (exec, read/write, list, screenshot) |
| `lazyclaw/computer/connector_server.py` | Server-side WebSocket relay for remote connectors |
| `lazyclaw/computer/manager.py` | Unified facade: routes to local or remote execution |
| `lazyclaw/skills/builtin/computer.py` | RunCommand, ReadFile, WriteFile, ListDirectory, TakeScreenshot |
| `lazyclaw/gateway/routes/connector.py` | Connector REST API + WebSocket endpoint |
| `connector/` | Standalone desktop connector program (auto-reconnect, 6 handlers) |
| `lazyclaw/mcp/client.py` | MCP client: stdio/SSE/streamable_http transport connections |
| `lazyclaw/mcp/bridge.py` | MCP tools ↔ BaseSkill conversion, display_name, registry integration |
| `lazyclaw/mcp/manager.py` | MCP connection CRUD + lifecycle (encrypted configs), bundled MCP auto-register |
| `lazyclaw/mcp/server.py` | Expose skill registry as MCP server via SSE |
| `lazyclaw/heartbeat/cron.py` | Cron expression parser using croniter |
| `lazyclaw/heartbeat/orchestrator.py` | Job CRUD for agent_jobs table (encrypted) |
| `lazyclaw/heartbeat/daemon.py` | Background heartbeat daemon, cron job executor, one-time reminder support |
| `lazyclaw/gateway/routes/mcp.py` | MCP server management REST API (7 endpoints) |
| `lazyclaw/gateway/routes/jobs.py` | Job management REST API (7 endpoints) |
| `lazyclaw/llm/eco_router.py` | ECO mode: routes between free (mcp-freeride) and paid AI |
| `lazyclaw/llm/eco_settings.py` | ECO settings CRUD (stored in users.settings JSON) |
| `lazyclaw/llm/rate_limiter.py` | Per-provider sliding window rate limit tracker |
| `lazyclaw/gateway/routes/eco.py` | ECO mode REST API (settings, usage, rate limits, providers) |
| `lazyclaw/permissions/models.py` | Frozen dataclasses: ResolvedPermission, ApprovalRequest, AuditEntry |
| `lazyclaw/permissions/settings.py` | Permission settings CRUD from users.settings JSON |
| `lazyclaw/permissions/checker.py` | PermissionChecker: resolve skill → allow/ask/deny |
| `lazyclaw/permissions/approvals.py` | Approval request CRUD + auto-expiration (encrypted args) |
| `lazyclaw/permissions/audit.py` | Fire-and-forget audit logger + query + cleanup |
| `lazyclaw/gateway/routes/permissions.py` | Permissions REST API (settings, skills, approvals, audit) |
| `lazyclaw/teams/specialist.py` | SpecialistConfig dataclass, built-in specialists, DB CRUD |
| `lazyclaw/teams/runner.py` | Run single specialist as mini agent loop with filtered tools |
| `lazyclaw/teams/executor.py` | Parallel specialist execution via asyncio.gather + semaphore |
| `lazyclaw/teams/lead.py` | Team lead: analyze complexity, delegate, merge + critic, simple-message pre-filter |
| `lazyclaw/teams/conversation.py` | Encrypted team message storage (agent_team_messages table) |
| `lazyclaw/teams/settings.py` | Team settings CRUD from users.settings JSON |
| `lazyclaw/gateway/routes/teams.py` | Teams REST API (settings, specialists, sessions — 8 endpoints) |
| `lazyclaw/memory/classifier.py` | Heuristic message priority classification (high/medium/low) |
| `lazyclaw/memory/summarizer.py` | LLM-powered conversation chunk summarization |
| `lazyclaw/memory/compressor.py` | Sliding window + persistent summary compression engine |
| `lazyclaw/gateway/routes/compression.py` | Compression stats + force re-summarize API (2 endpoints) |
| `lazyclaw/replay/models.py` | TraceEntry, TraceSession frozen dataclasses, 9 entry types |
| `lazyclaw/replay/recorder.py` | Fire-and-forget trace recorder, captures all agent actions encrypted |
| `lazyclaw/replay/engine.py` | Load/list/delete traces, view by session or share token |
| `lazyclaw/replay/sharing.py` | Shareable URL-safe tokens with expiration, revoke |
| `lazyclaw/gateway/routes/replay.py` | Replay REST API (traces, shares, public view — 7 endpoints) |

**Standalone: mcp-freeride** (free AI router MCP server):
| File | Purpose |
|------|---------|
| `mcp-freeride/pyproject.toml` | Standalone package, deps: mcp, httpx |
| `mcp-freeride/mcp_freeride/config.py` | FreeRideConfig: 7 API key env vars |
| `mcp-freeride/mcp_freeride/providers/base.py` | OpenAICompatibleProvider: httpx /v1/chat/completions |
| `mcp-freeride/mcp_freeride/providers/` | groq, gemini, openrouter, together, mistral, huggingface, ollama |
| `mcp-freeride/mcp_freeride/health.py` | HealthChecker: latency tracking, ranked fallback |
| `mcp-freeride/mcp_freeride/router.py` | FreeRideRouter: provider-hint parsing, fallback chain |
| `mcp-freeride/mcp_freeride/server.py` | MCP tools: freeride_chat, freeride_models, freeride_status |
| `mcp-freeride/mcp_freeride/main.py` | Entry point: stdio MCP server |

**Standalone: mcp-healthcheck** (AI source monitor MCP server):
| File | Purpose |
|------|---------|
| `mcp-healthcheck/pyproject.toml` | Standalone package, deps: mcp, httpx |
| `mcp-healthcheck/mcp_healthcheck/config.py` | HealthCheckConfig: 7 API keys + interval/weights |
| `mcp-healthcheck/mcp_healthcheck/providers.py` | ProviderEndpoint, KNOWN_PROVIDERS, ping_provider() |
| `mcp-healthcheck/mcp_healthcheck/history.py` | CheckHistory: per-provider deque of PingResults, summaries |
| `mcp-healthcheck/mcp_healthcheck/scorer.py` | Composite scoring: speed/uptime/quality, leaderboard |
| `mcp-healthcheck/mcp_healthcheck/monitor.py` | Background ping loop, facade for status/leaderboard |
| `mcp-healthcheck/mcp_healthcheck/server.py` | MCP tools: healthcheck_status, leaderboard, ping, history |
| `mcp-healthcheck/mcp_healthcheck/main.py` | Entry point: stdio MCP server with background pinger |

**Standalone: mcp-apihunter** (free API discovery MCP server):
| File | Purpose |
|------|---------|
| `mcp-apihunter/pyproject.toml` | Standalone package, deps: mcp, httpx, aiosqlite |
| `mcp-apihunter/mcp_apihunter/config.py` | ApiHunterConfig: db_path, validation_timeout |
| `mcp-apihunter/mcp_apihunter/models.py` | RegistryEntry, ValidationResult frozen dataclasses |
| `mcp-apihunter/mcp_apihunter/registry.py` | SQLite CRUD for endpoints table |
| `mcp-apihunter/mcp_apihunter/validator.py` | Endpoint validation via /v1/chat/completions |
| `mcp-apihunter/mcp_apihunter/server.py` | MCP tools: submit, validate, list, search, remove |
| `mcp-apihunter/mcp_apihunter/main.py` | Entry point: stdio MCP server |

**Standalone: mcp-vaultwhisper** (privacy-safe AI proxy MCP server):
| File | Purpose |
|------|---------|
| `mcp-vaultwhisper/pyproject.toml` | Standalone package, deps: mcp, httpx |
| `mcp-vaultwhisper/mcp_vaultwhisper/config.py` | VaultWhisperConfig: mode (strict/relaxed), 7 API keys |
| `mcp-vaultwhisper/mcp_vaultwhisper/patterns.py` | PIIType enum, 7 default regex patterns |
| `mcp-vaultwhisper/mcp_vaultwhisper/detector.py` | detect_pii(): regex scanning with sequential placeholders |
| `mcp-vaultwhisper/mcp_vaultwhisper/scrubber.py` | scrub(): PII replacement → ScrubResult with mapping |
| `mcp-vaultwhisper/mcp_vaultwhisper/restorer.py` | restore(): re-inject original values from mapping |
| `mcp-vaultwhisper/mcp_vaultwhisper/server.py` | MCP tools: scrub, restore, chat (proxy), detect, patterns |
| `mcp-vaultwhisper/mcp_vaultwhisper/main.py` | Entry point: stdio MCP server |

**Standalone: mcp-taskai** (task intelligence MCP server):
| File | Purpose |
|------|---------|
| `mcp-taskai/pyproject.toml` | Standalone package, deps: mcp, httpx |
| `mcp-taskai/mcp_taskai/config.py` | TaskAIConfig: 7 API keys + preferred_provider |
| `mcp-taskai/mcp_taskai/ai_client.py` | Lightweight free AI caller with provider fallback |
| `mcp-taskai/mcp_taskai/prompts.py` | Prompt templates for categorize, deadline, dedup, summarize, prioritize |
| `mcp-taskai/mcp_taskai/intelligence.py` | TaskIntelligence: 5 AI-powered task analysis methods |
| `mcp-taskai/mcp_taskai/server.py` | MCP tools: categorize, suggest_deadline, detect_duplicates, summarize, prioritize |
| `mcp-taskai/mcp_taskai/main.py` | Entry point: stdio MCP server |

**Standalone: mcp-lazydoctor** (self-healing doctor MCP server):
| File | Purpose |
|------|---------|
| `mcp-lazydoctor/pyproject.toml` | Standalone package, deps: mcp |
| `mcp-lazydoctor/mcp_lazydoctor/config.py` | DoctorConfig: project root, tool paths, safety limits, git settings |
| `mcp-lazydoctor/mcp_lazydoctor/runner.py` | Safe subprocess runner with command allowlist and timeout |
| `mcp-lazydoctor/mcp_lazydoctor/diagnostics.py` | Diagnostic engine: ruff lint, pytest, mypy, ruff format parsers |
| `mcp-lazydoctor/mcp_lazydoctor/git_ops.py` | Git operations: status, branch, commit, diff for safe auto-fix |
| `mcp-lazydoctor/mcp_lazydoctor/fixer.py` | Auto-fix engine: apply safe fixes, verify improvement, optionally commit |
| `mcp-lazydoctor/mcp_lazydoctor/server.py` | MCP tools: doctor_checkup, doctor_lint, doctor_test, doctor_typecheck, doctor_fix, doctor_format, doctor_git_status, doctor_heal |
| `mcp-lazydoctor/mcp_lazydoctor/main.py` | Entry point: stdio MCP server |

**Planned** (not yet implemented):
| File | Purpose |
|------|---------|
| `lazyclaw/channels/router.py` | Inbound message -> queue routing |
| `lazyclaw/voice/` | STT/TTS voice processing |

## Build & Run

```bash
# Install
pip install -e .

# First-time setup (interactive wizard)
lazyclaw setup

# Start the agent (Telegram + API)
lazyclaw start

# Run gateway only (development)
uvicorn lazyclaw.gateway.app:app --host 0.0.0.0 --port 18789 --reload

# Module entry point
python -m lazyclaw

# Docker (future)
docker compose up --build
```

Default port: **18789** (same as OpenClaw for familiarity).

### mcp-freeride (standalone)

```bash
# Set API keys (any combination)
export GROQ_API_KEY=gsk_...
export GEMINI_API_KEY=...
export OPENROUTER_API_KEY=...

# Run as MCP server
uv run mcp-freeride
# or
python -m mcp_freeride

# MCP inspector
npx @modelcontextprotocol/inspector python -m mcp_freeride
```

### mcp-healthcheck (standalone)

```bash
# Set same API keys as mcp-freeride
export GROQ_API_KEY=gsk_...
export HEALTHCHECK_INTERVAL=60  # seconds between pings

# Run as MCP server
python -m mcp_healthcheck
npx @modelcontextprotocol/inspector python -m mcp_healthcheck
```

### mcp-apihunter (standalone)

```bash
# Optional config
export APIHUNTER_DB_PATH=./apihunter.db
export APIHUNTER_VALIDATION_TIMEOUT=15

# Run as MCP server
python -m mcp_apihunter
npx @modelcontextprotocol/inspector python -m mcp_apihunter
```

### mcp-vaultwhisper (standalone)

```bash
# Set API keys + mode
export GROQ_API_KEY=gsk_...
export VAULTWHISPER_MODE=strict  # or relaxed

# Run as MCP server
python -m mcp_vaultwhisper
npx @modelcontextprotocol/inspector python -m mcp_vaultwhisper
```

### mcp-taskai (standalone)

```bash
# Set API keys
export GROQ_API_KEY=gsk_...
export TASKAI_PROVIDER=groq  # optional: lock to specific provider

# Run as MCP server
python -m mcp_taskai
npx @modelcontextprotocol/inspector python -m mcp_taskai
```

### mcp-lazydoctor (standalone)

```bash
# Point at project root (defaults to cwd)
export LAZYDOCTOR_PROJECT_ROOT=/path/to/lazyclaw

# Optional: safety settings
export LAZYDOCTOR_AUTO_FIX=true       # enable ruff --fix (default: true)
export LAZYDOCTOR_DRY_RUN=false       # report only, no changes (default: false)
export LAZYDOCTOR_REQUIRE_CLEAN_GIT=true  # refuse fixes on dirty worktree (default: true)
export LAZYDOCTOR_AUTO_COMMIT=false   # auto-commit fixes (default: false)

# Run as MCP server
python -m mcp_lazydoctor
npx @modelcontextprotocol/inspector python -m mcp_lazydoctor
```

### CLI Commands
- `lazyclaw` — Drops straight into interactive chat REPL (the main experience)
- `lazyclaw setup` — Interactive wizard: generates SERVER_SECRET, configures AI provider (OpenAI/Anthropic), sets up Telegram bot, initializes DB
- `lazyclaw start` — Starts full server (FastAPI gateway + Telegram polling + Heartbeat)

### Chat REPL Slash Commands
Inside the chat REPL (`lazyclaw`), all admin/monitoring is available as slash commands:
- `/status` — System dashboard (config, DB stats, agent modes)
- `/users` — List all users with role and message count
- `/skills` — List all skills with permission levels
- `/traces` — Show recent session traces
- `/teams` — Team config and specialist list
- `/mcp` — MCP server connections and status
- `/compression` — Context compression stats
- `/history` — Recent conversation messages
- `/logs` — Recent agent activity (tool calls, LLM calls from traces)
- `/usage` — Session token usage + cost estimate in EUR
- `/doctor` — Health check (DB, AI, MCP, encryption, Telegram)
- `/clear` — Start fresh chat session
- `/wipe` — Clear all conversation history (with confirmation)
- `/critic off|on|auto` — Set critic mode
- `/team off|on|auto` — Set team mode (default: never, opt-in with on/auto)
- `/eco eco|hybrid|full` — Set ECO mode
- `/model <name>` — Change default model
- `/permissions` — Show permission levels for all categories and skills
- `/allow <name>` — Allow a category or skill (e.g. `/allow computer`)
- `/deny <name>` — Deny a category or skill (e.g. `/deny vault`)
- `/update` — Pull latest code from git + reinstall deps
- `/version` — Show current version
- `/help` — Show all available commands
- `/exit` — Quit (also `/quit`, `/q`)

## E2E Encryption

All user content encrypted before storage. Server never sees plaintext.

**How it works:**
1. On registration, server generates random `encryption_salt` per user
2. On login, client derives AES-256 key: `PBKDF2(password, salt, 100k iterations, SHA-256)`
3. Client key stored in sessionStorage (web) or secure memory (mobile)
4. On write: content encrypted -> stored as `enc:v1:<base64-nonce>:<base64-ciphertext>`
5. On read: `enc:v1:` prefix detected -> auto-decrypted

**Server-side key** (for daemon operations like heartbeat, cron):
- `server_key = PBKDF2(SERVER_SECRET + user_id, fixed_salt, 100k iterations)`
- Encrypts: memory, site knowledge, skill instructions, job configs, credential vault
- Server can act autonomously but data is still encrypted at rest

**Encrypted fields**: conversations, memory content, skill instructions, credential vault values, job instructions/context, channel configs
**Plaintext fields** (needed for queries): IDs, timestamps, status flags, cron expressions, domains, importance scores

## Key Patterns

- **User isolation**: ALL queries scoped by `user_id`. No user ever sees another's data.
- **Async SQLite**: `aiosqlite` throughout for non-blocking DB operations.
- **Session auth**: HTTP-only cookies, not JWT tokens.
- **No hardcoded tools**: All tools come from skill registry (built-in skills, user skills, MCP tools, plugin skills). Agent runtime discovers tools dynamically.
- **Lane Queue**: Serial execution per user prevents race conditions. Messages from any source (API, channels, heartbeat) become Jobs in the user's lane.
- **Semantic Snapshots**: Browser uses accessibility tree text (50KB) instead of screenshots (5MB) for LLM context. Screenshots available as fallback for visual-heavy pages.
- **Encrypted credential vault**: User API keys stored in `credential_vault` table (encrypted), not in plaintext .env. Server-level config (ports, paths) stays in .env.
- **SOUL.md personality**: Each user has a customizable personality file that defines the agent's name, tone, and instructions. Loaded on every interaction.
- **Channel normalization**: All channels convert to unified `InboundMessage` dataclass before entering the queue. Agent doesn't know which channel the message came from.
- **MCP bridge**: External MCP tools registered as first-class skills in the registry. No separate tool path needed.
- **Browser auto-login**: Cookies persist in `browser_profiles/{user_id}/cookies.json`. When cookies expire, PageReader detects login form and pulls credentials from vault (`site:{domain}` key). Expensive Agent only needed for first login or CAPTCHA.
- **Browser cost tiers**: PageReader (JS extraction, ~$0.001/page) for reading. Full browser Agent (~$0.30/page) for interaction. Agent learns, PageReader reuses.
- **Streaming responses**: LLM responses stream token-by-token to the CLI via `StreamChunk` async generators through provider → router → eco_router → agent → callback chain.
- **Compact CLI approval**: When a tool needs approval, CLI shows one-line `⚡ display_name  args` + `Allow? [Y/n]` instead of verbose panels. Display names resolve MCP UUIDs to friendly names (e.g., `claude-code:claude_code`).
- **Parallel initialization**: Agent loads history, skills, and context concurrently via `asyncio.gather()` to reduce latency before the first LLM call.
- **Smart tool routing**: Agent only sends tools to LLM when the message suggests tool usage (`_wants_any_tools()`). Simple chat (hello, questions) gets zero tools → fast direct response. Prevents GPT-5 from running random `run_command` calls on greetings.
- **Tool-free history stripping**: When no tools are needed, `_strip_tool_messages()` converts tool-call history to plain text so the LLM doesn't hallucinate tool calls from seeing old patterns.
- **Fast chat path**: Simple messages get only last 6 history messages (no full summary). Complex tool requests get full compressed history. Reduces GPT-5 response time from ~60s to ~5s for chat.
- **Flexible summary cache**: `compress_history()` reuses existing summaries that cover 80%+ of older messages, avoiding expensive LLM re-summarization on every message.
- **CancellationToken**: Cooperative cancellation flows from CLI (Ctrl+C) → agent → team lead → specialists. Signal handler sets flag, polling loop checks it.
- **Team event propagation**: Specialist events (`specialist_start`, `specialist_tool`, `specialist_done`, `team_start`, `team_merge`) flow from teams/runner → teams/executor → teams/lead → agent → CLI callback for real-time dashboard.
- **Claude Code MCP OAuth**: The claude-code MCP server launches with `ANTHROPIC_API_KEY=""` so the CLI uses Max subscription (OAuth) instead of the API key which may have no credits. Configured via `strip_env` in BUNDLED_MCPS.
- **Agent self-awareness**: System prompt includes available skills list, connected MCP servers with descriptions/tool counts, and current config (model, ECO, team). Built dynamically by `context_builder.py` on every message so the agent knows what it can do.
- **Inline activity stream**: CLI events print as permanent log lines (not flickering spinner). Spinner only during LLM thinking waits. Each tool call shows display name + compact args + duration.
- **Friendly MCP names**: `MCPToolSkill.display_name` returns `"server:tool"` (e.g., `claude-code:claude_code`) instead of UUID-based `mcp_8f64bc83-..._claude_code`. Used in events, approvals, summaries.
- **Work summary**: After every agent task, a `WorkSummary` event fires with duration, LLM calls, tools used, specialist list. Displayed as Rich panel in CLI and plain text in Telegram.
- **Real Chrome mode (CDP)**: On-demand connection to user's actual Chrome browser via Chrome DevTools Protocol. Coexists with Playwright (headless) — agent picks the right backend per task.
- **NL job scheduling**: Agent creates cron jobs and one-time reminders through natural language. Heartbeat daemon fires cron jobs recurring and auto-deletes one-time reminders after delivery.

## Database

Single SQLite file: `lazyclaw.db`

### Core Tables
- `users` — id, username, password_hash, encryption_salt, display_name, personality_file, settings
- `sessions` — id, user_id, expires_at
- `agent_messages` — id, user_id, session_id, role, content (encrypted), tool_name, metadata
- `agent_chat_sessions` — id, user_id, title (encrypted), message_count, archived_at
- `personal_memory` — id, user_id, memory_type, content (encrypted), importance, embedding
- `site_memory` — id, user_id, domain, title (encrypted), content (encrypted), success/fail counts
- `daily_logs` — id, user_id, date, summary (encrypted), key_events (encrypted)
- `skills` — id, user_id, skill_type, name (encrypted), instruction (encrypted), code (encrypted), parameters_schema
- `browser_tasks` — id, user_id, instruction (encrypted), status, result (encrypted), steps_completed
- `browser_task_logs` — id, task_id, step_number, action, thinking, url
- `channel_bindings` — id, user_id, channel, external_id, config (encrypted)
- `channel_configs` — channel, config (encrypted), enabled
- `mcp_connections` — id, user_id, name, transport, config (encrypted), enabled
- `credential_vault` — id, user_id, key, value (encrypted)
- `ai_models` — model_id, display_name, provider, pricing, context_window
- `user_model_assignments` — user_id, feature, model_id
- `agent_jobs` — id, user_id, name (encrypted), job_type, instruction (encrypted), cron_expression, status
- `connector_tokens` — id, user_id, token
- `job_queue` — id, user_id, source, payload (encrypted), status

## Environment Variables

Key variables (see `.env.example`):
- `SERVER_SECRET` — Secret for server-side encryption key derivation (REQUIRED)
- `OPENAI_API_KEY` — Default OpenAI key (optional, users can set their own in vault)
- `CORS_ORIGIN` — CORS origin for web clients
- `DATABASE_DIR` — Directory for SQLite DB and browser profiles (default: `./data`)
- `DEFAULT_MODEL` — Main LLM model (default: `gpt-5`)
- `WORKER_MODEL` — Worker/specialist model (default: `gpt-5-mini`)
- `BROWSER_MODEL` — Browser agent model (default: `gpt-5-mini`)
- `BROWSER_TIMEOUT` — Browser task timeout in seconds (default: `300`)
- `COMPUTER_TIMEOUT` — Computer command timeout in seconds (default: `30`)
- `HEARTBEAT_INTERVAL` — Heartbeat daemon check interval in seconds (default: `60`)
- `PORT` — Gateway port (default: `18789`)

## Skill System

### Skill Types

**Instruction Skills** — Natural language templates the LLM follows
```
Name: "Daily Standup"
Instruction: "Ask the user what they accomplished yesterday, what they're working on today, and any blockers. Format as bullet points."
```

**Code Skills** — Sandboxed Python functions
```python
async def run(user_id, params, call_tool):
    result = await call_tool("web_search", {"query": params["topic"]})
    return f"Found: {result}"
```

**Plugin Skills** — External packages with manifest.json
```json
{
  "id": "lazytasker",
  "name": "LazyTasker",
  "skills": [{"name": "create_task", "handler": "skill.create_task", ...}]
}
```

All types unified in the skill registry and converted to OpenAI function-calling format for the LLM.

## API Endpoints

### Auth
- `POST /api/auth/register`, `/login`, `/logout`, `GET /api/auth/me`

### Agent Chat
- `POST /api/agent/chat` — Send message, get AI response
- `GET/DELETE /api/agent/messages` — Conversation history
- `POST /api/agent/messages/archive` — Archive session

### Memory
- `GET/DELETE /api/memory/personal`, `/api/memory/site`, `/api/memory/daily-logs`

### Skills
- `GET/POST/PATCH/DELETE /api/skills`, `POST /api/skills/generate`

### Browser
- `POST /api/browser/tasks` — Start browser task
- `GET /api/browser/tasks` — List tasks
- `GET /api/browser/tasks/{id}` — Task details
- `GET /api/browser/tasks/{id}/logs` — Step-by-step logs
- `GET /api/browser/tasks/{id}/live` — Live screenshot (PNG)
- `POST /api/browser/tasks/{id}/help` — Provide help response
- `POST /api/browser/tasks/{id}/continue` — Continue completed/failed task
- `POST /api/browser/tasks/{id}/cancel` — Cancel running task
- `POST /api/browser/tasks/{id}/takeover` — Take manual control
- `POST /api/browser/tasks/{id}/release` — Release control to agent
- `POST /api/browser/tasks/{id}/action` — Execute user action (click/type/scroll/key)
- `POST /api/browser/sessions/close` — Close browser session
- `GET /api/browser/site-memory` — List site memories
- `DELETE /api/browser/site-memory/{id}` — Delete site memory
- `DELETE /api/browser/site-memory/domain/{domain}` — Delete domain memories

### Channels
- `GET /api/channels`, `POST /api/channels/{name}/config`, `POST/DELETE /api/channels/{name}/bind`

### MCP
- `GET /api/mcp/servers`, `POST /api/mcp/servers`, `GET /api/mcp/servers/{id}`, `DELETE /api/mcp/servers/{id}`
- `POST /api/mcp/servers/{id}/connect`, `POST /api/mcp/servers/{id}/disconnect`, `POST /api/mcp/servers/{id}/reconnect`

### Models & Vault
- `GET/PATCH /api/models/assignments`, `PUT /api/models/keys/{provider}`
- `GET/PUT/DELETE /api/vault/{key}`

### Jobs
- `GET /api/jobs`, `POST /api/jobs`, `GET /api/jobs/{id}`, `PATCH /api/jobs/{id}`, `DELETE /api/jobs/{id}`
- `POST /api/jobs/{id}/pause`, `POST /api/jobs/{id}/resume`

### ECO Mode
- `GET/PATCH /api/eco/settings` — ECO mode config (eco/hybrid/full, provider locks, badges)
- `GET /api/eco/usage` — Free vs paid usage stats
- `GET /api/eco/rate-limits` — Known rate limits for all free providers
- `GET /api/eco/providers` — List configured/available free AI providers

### Permissions
- `GET/PATCH /api/permissions/settings` — Permission config (category defaults, skill overrides)
- `GET /api/permissions/skills` — All skills with resolved permission level
- `PATCH/DELETE /api/permissions/skills/{name}` — Set or remove skill permission override
- `GET /api/permissions/approvals` — List pending approval requests
- `POST /api/permissions/approvals/{id}/approve` — Approve pending request
- `POST /api/permissions/approvals/{id}/deny` — Deny pending request
- `GET /api/permissions/audit` — Query audit log entries

### Replay
- `GET /api/replay/traces` — List recent trace sessions
- `GET /api/replay/traces/{id}` — View full trace timeline (decrypted)
- `DELETE /api/replay/traces/{id}` — Delete a trace and its shares
- `POST /api/replay/share` — Generate shareable token for a trace
- `GET /api/replay/share/{token}` — View trace via share token (no auth)
- `GET /api/replay/shares` — List user's share tokens
- `DELETE /api/replay/shares/{id}` — Revoke a share token

### Compression
- `GET /api/compression/stats` — Summary count, compression ratio, window size
- `POST /api/compression/force` — Delete summaries (regenerate on next chat)

### Teams
- `GET/PATCH /api/teams/settings` — Team mode config (auto/always/never, critic, parallel, timeout)
- `GET /api/teams/specialists` — List all specialists (built-in + custom)
- `POST /api/teams/specialists` — Create custom specialist
- `PATCH/DELETE /api/teams/specialists/{name}` — Update or delete custom specialist
- `GET /api/teams/sessions` — List team sessions
- `GET /api/teams/sessions/{id}` — View team conversation (decrypted)

### Connector
- `POST /api/connector/token`, `GET /api/connector/status`, `DELETE /api/connector/token`
- `WS /ws/connector` — WebSocket for remote desktop connectors

## Extracted from LazyTasker

These modules are adapted from the proven LazyTasker codebase:
- **LLM Router** (`llm/router.py`) — Multi-provider routing (OpenAI, Anthropic, Google, Mistral, Ollama, local GGUF)
- **Model Manager** (`llm/model_manager.py`) — Model catalog, per-user API keys, feature assignments
- **Personal Memory** (`memory/personal.py`) — Encrypted facts/preferences, importance ranking, system prompt injection
- **Site Memory** (`browser/site_memory.py`) — Per-domain browser learning, encrypted
- **Skill System** (`skills/manager.py`, `sandbox.py`, `writer.py`) — Instruction + code skills with AST validation
- **Page Reader** (`browser/page_reader.py`) — Lightweight page extraction with JS extractors
- **Browser Agent** (`browser/manager.py`, `agent.py`) — Playwright CDP automation, takeover, checkpoints
- **Computer Connector** (`computer/connector_server.py`, `connector/`) — WebSocket relay + standalone desktop program
- **Security** (`computer/security.py`) — Command/path blocklists
- **Orchestrator** (`heartbeat/orchestrator.py`) — Monitor/worker job execution
- **Auth** (`gateway/auth.py`) — Session auth, invite codes, user management
- **Encryption** (`crypto/encryption.py`) — AES-256-GCM, PBKDF2, enc:v1: format

## Development Workflow

### Phase Plan
1. ~~Foundation~~ — **DONE**: Crypto, DB, config, LLM router, auth, model manager, agent, CLI wizard, gateway
2. ~~Skills + Tools~~ — **DONE**: BaseSkill ABC, registry, 14 built-in skills, instruction/code skills, skill writer, tool executor
3. ~~Queue + Memory~~ — **DONE**: Lane queue, personal memory, daily logs, SOUL.md, context builder, vault
4. ~~Browser~~ — **DONE**: Playwright manager, browser agent (human-in-the-loop, takeover), page reader (5 JS extractors), DOM optimizer, site memory, auto-login with vault credentials, 15 API endpoints
5. ~~Computer Control~~ — **DONE**: Security manager, native executor, connector server, standalone connector, REST+WS API, 5 agent skills
6. ~~Channels (partial)~~ — **DONE**: Telegram polling. TODO: Discord, WhatsApp, Signal, SimpleX
7. ~~MCP + Heartbeat~~ — **DONE**: MCP client/server/bridge, heartbeat daemon, cron jobs, orchestrator, 14 API endpoints
8. LazyTasker Plugin + Docker — Optional integration, deployment
9. Flutter App — Mobile client

### Principles
- **Simplicity First**: Make every change as simple as possible
- **No Hardcoded Tools**: Everything goes through the skill registry
- **Encrypt Everything**: User content is always encrypted at rest
- **Extract, Don't Rewrite**: Proven LazyTasker code is adapted, not rewritten from scratch
- **Test Each Phase**: Each phase has a clear verification step before moving on

### Git Commit Rules
- **No Co-Authored-By**: Do NOT add "Co-Authored-By: Claude" or any AI attribution to commits
- Keep commit messages clean and human-style
