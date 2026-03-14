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

Supporting modules: `llm/` (multi-provider router), `heartbeat/` (proactive daemon + cron), `voice/` (STT/TTS), `db/` (aiosqlite + migrations).

### Key File Map

**Implemented** (working):
| File | Purpose |
|------|---------|
| `lazyclaw/cli.py` | CLI entry point: `lazyclaw setup` wizard + `lazyclaw start` |
| `lazyclaw/config.py` | Environment variable loading via python-dotenv, Config dataclass |
| `lazyclaw/crypto/encryption.py` | AES-256-GCM encrypt/decrypt, PBKDF2 key derivation, enc:v1: format |
| `lazyclaw/db/schema.sql` | Core database schema (22 tables) |
| `lazyclaw/db/connection.py` | aiosqlite connection, WAL mode, init_db, db_session context manager |
| `lazyclaw/llm/providers/base.py` | LLMMessage, LLMResponse, ToolCall dataclasses, BaseLLMProvider ABC |
| `lazyclaw/llm/providers/openai_provider.py` | OpenAI provider with tool calling support |
| `lazyclaw/llm/providers/anthropic_provider.py` | Anthropic provider with tool_use/tool_result support |
| `lazyclaw/llm/router.py` | Model-prefix-based provider routing |
| `lazyclaw/runtime/agent.py` | Core agent: multi-turn agentic loop with tool calling (max 10 iterations) |
| `lazyclaw/runtime/personality.py` | SOUL.md loader, system prompt builder |
| `lazyclaw/runtime/tool_executor.py` | Dispatches ToolCall to skill registry, error handling |
| `lazyclaw/skills/base.py` | BaseSkill ABC with to_openai_tool() conversion |
| `lazyclaw/skills/registry.py` | Unified skill registry, register_defaults() |
| `lazyclaw/skills/builtin/web_search.py` | DuckDuckGo web search (no API key needed) |
| `lazyclaw/skills/builtin/get_time.py` | Timezone-aware current time |
| `lazyclaw/skills/builtin/calculate.py` | Safe AST-based math calculator |
| `lazyclaw/channels/base.py` | ChannelAdapter ABC, InboundMessage/OutboundMessage |
| `lazyclaw/channels/telegram.py` | Telegram polling adapter (python-telegram-bot v21+) |
| `lazyclaw/gateway/app.py` | Minimal FastAPI: health check + agent chat endpoint |
| `lazyclaw/main.py` | Module entry point (python -m lazyclaw) |

| `lazyclaw/gateway/auth.py` | Session auth, user management, bcrypt passwords |
| `lazyclaw/gateway/routes/memory.py` | Memory + daily log REST endpoints |
| `lazyclaw/gateway/routes/skills.py` | Skill CRUD + AI generation endpoints |
| `lazyclaw/gateway/routes/vault.py` | Credential vault REST endpoints |
| `lazyclaw/gateway/routes/browser.py` | Browser task CRUD, takeover, live view, site memory (15 endpoints) |
| `lazyclaw/runtime/context_builder.py` | Assembles system prompt from personality + memory + skills |
| `lazyclaw/queue/lane.py` | FIFO per-user lane queue |
| `lazyclaw/skills/sandbox.py` | AST-validated restricted Python execution |
| `lazyclaw/skills/manager.py` | User skill CRUD (DB-backed, encrypted) |
| `lazyclaw/skills/writer.py` | LLM-powered code skill generation |
| `lazyclaw/skills/instruction.py` | Natural language template skills |
| `lazyclaw/skills/builtin/browser.py` | BrowseWebSkill, ReadPageSkill, SaveSiteLoginSkill |
| `lazyclaw/memory/personal.py` | Encrypted personal facts + keyword search |
| `lazyclaw/memory/daily_log.py` | Encrypted daily summaries + LLM generation |
| `lazyclaw/llm/model_manager.py` | Model catalog, per-user feature assignments |
| `lazyclaw/crypto/vault.py` | Encrypted credential store |
| `lazyclaw/browser/manager.py` | Persistent browser sessions per user, stealth, profiles |
| `lazyclaw/browser/agent.py` | Browser agent: agentic loop, human-in-the-loop, takeover |
| `lazyclaw/browser/page_reader.py` | Lightweight JS page extraction (5 extractors) + LLM analysis |
| `lazyclaw/browser/dom_optimizer.py` | DOM analysis, actionable elements, page summary |
| `lazyclaw/browser/site_memory.py` | Encrypted per-domain learning with auto-cleanup |
| `lazyclaw/computer/security.py` | Command/path blocklist validation |
| `lazyclaw/computer/native.py` | Local subprocess execution (exec, read/write, list, screenshot) |
| `lazyclaw/computer/connector_server.py` | Server-side WebSocket relay for remote connectors |
| `lazyclaw/computer/manager.py` | Unified facade: routes to local or remote execution |
| `lazyclaw/skills/builtin/computer.py` | RunCommand, ReadFile, WriteFile, ListDirectory, TakeScreenshot |
| `lazyclaw/gateway/routes/connector.py` | Connector REST API + WebSocket endpoint |
| `connector/` | Standalone desktop connector program (auto-reconnect, 6 handlers) |

**Planned** (not yet implemented):
| File | Purpose |
|------|---------|
| `lazyclaw/channels/router.py` | Inbound message -> queue routing |
| `lazyclaw/mcp/client.py` | MCP client (stdio/SSE/WebSocket transports) |
| `lazyclaw/mcp/server.py` | Expose LazyClaw tools as MCP server |

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

### CLI Commands
- `lazyclaw setup` — Interactive wizard: generates SERVER_SECRET, configures AI provider (OpenAI/Anthropic), sets up Telegram bot, initializes DB
- `lazyclaw start` — Starts FastAPI gateway + Telegram polling concurrently

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
- `DEFAULT_MODEL` — Default LLM model (default: `gpt-4o-mini`)
- `BROWSER_MODEL` — Default browser agent model (default: `gpt-4o-mini`)
- `BROWSER_TIMEOUT` — Browser task timeout in seconds (default: `300`)
- `COMPUTER_TIMEOUT` — Computer command timeout in seconds (default: `30`)
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
- `GET/POST/DELETE /api/mcp/servers`, `POST /api/mcp/servers/{id}/reconnect`

### Models & Vault
- `GET/PATCH /api/models/assignments`, `PUT /api/models/keys/{provider}`
- `GET/PUT/DELETE /api/vault/{key}`

### Jobs
- `GET/POST/PATCH/DELETE /api/jobs`, `POST /api/jobs/{id}/pause`, `/resume`

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
7. MCP + Heartbeat — Native MCP, proactive daemon, cron
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
