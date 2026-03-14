# TODO

## Phase 1: Foundation
- [x] **1.1 Crypto core** — `lazyclaw/crypto/encryption.py`: AES-256-GCM + PBKDF2, `enc:v1:` format, server-side key derivation.
- [x] **1.2 Crypto fields** — encrypt_field, decrypt_field, is_encrypted (in encryption.py).
- [x] **1.3 Database** — `lazyclaw/db/`: aiosqlite connection pool, schema.sql (7 tables), WAL mode.
- [x] **1.4 Config** — `lazyclaw/config.py`: Env var loading via python-dotenv, Config dataclass, save_env().
- [x] **1.5 Auth** — `lazyclaw/gateway/auth.py`: Registration, login, sessions, encryption_salt. bcrypt hashing, HTTP-only cookies, FastAPI dependency.
- [x] **1.6 LLM Router** — `lazyclaw/llm/router.py` + `providers/`: OpenAI + Anthropic with tool calling support.
- [x] **1.7 Model Manager** — `lazyclaw/llm/model_manager.py`: Model catalog, per-user assignments, auto-seeding.
- [x] **1.8 Basic Agent** — `lazyclaw/runtime/agent.py`: Multi-turn agentic loop with tool calling.
- [x] **1.9 Conversation Memory** — Messages stored encrypted in agent_messages, last 20 loaded as context.
- [x] **1.10 Gateway** — `lazyclaw/gateway/app.py`: FastAPI with health check + `/api/agent/chat`.
- [x] **1.11 Entry Point** — `lazyclaw/cli.py` (setup wizard + start), `main.py`, `__main__.py`, pyproject.toml scripts.

**Verification**: ✅ `lazyclaw setup` configures everything. `lazyclaw start` runs agent. Telegram + API both work.

## Phase 2: Skills + Tools
- [x] **2.1 BaseSkill ABC** — `lazyclaw/skills/base.py`: Abstract skill class with to_openai_tool() conversion.
- [x] **2.2 Skill Registry** — `lazyclaw/skills/registry.py`: Unified registry with register_defaults().
- [x] **2.3 Instruction Skills** — `lazyclaw/skills/manager.py`: NL template CRUD.
- [x] **2.4 Code Skills** — `lazyclaw/skills/sandbox.py`: AST validation + restricted exec. CodeSkill class.
- [x] **2.5 Skill Writer** — `lazyclaw/skills/writer.py`: AI-generated code skills with validation retry.
- [x] **2.6 Built-in Skills** — `lazyclaw/skills/builtin/`: web_search (DuckDuckGo), get_time, calculate.
- [x] **2.7 Tool Executor** — `lazyclaw/runtime/tool_executor.py`: Dispatch tool calls to skill registry.
- [x] **2.8 Skills API** — `lazyclaw/gateway/routes/skills.py`: CRUD + AI generation endpoints.

**Verification**: ✅ Agent calls tools during chat (web_search, get_time, calculate). Multi-turn agentic loop works.

## Phase 3: Queue + Memory + Personality
- [x] **3.1 Lane Queue** — `lazyclaw/queue/lane.py`: FIFO per-user serial queue.
- [x] **3.2 Worker Pool** — Integrated into LaneQueue (per-user processor tasks).
- [x] **3.3 Personal Memory** — `lazyclaw/memory/personal.py`: Extract from LazyTasker. Encrypted facts/prefs.
- [x] **3.4 SOUL.md** — `lazyclaw/runtime/personality.py`: Load personality file, inject into system prompt.
- [x] **3.5 Context Builder** — `lazyclaw/runtime/context_builder.py`: Assemble personality + memory + skills.
- [x] **3.6 Daily Logs** — `lazyclaw/memory/daily_log.py`: Auto-summarize sessions via LLM, encrypted storage.
- [x] **3.7 Credential Vault** — `lazyclaw/crypto/vault.py`: Encrypted API key storage.
- [x] **3.8 Memory API** — `lazyclaw/gateway/routes/memory.py` + `vault.py`: Full REST endpoints.

**Verification**: Messages queue serially. Memory persists across sessions. SOUL.md customization works.

## Phase 4: Browser Automation ✅ COMPLETE
- [x] **4.1 Browser Manager** — `lazyclaw/browser/manager.py`: PersistentBrowserManager + BrowserSessionPool.
- [x] **4.2 Browser Agent** — `lazyclaw/browser/agent.py`: BrowserAgentManager with human-in-the-loop + takeover.
- [x] **4.3 Semantic Snapshots** — Handled by DOM optimizer (`extract_actionable`) + page reader JS extractors.
- [x] **4.4 Page Reader** — `lazyclaw/browser/page_reader.py`: 5 JS extractors + LLM analysis + extractor generation.
- [x] **4.5 DOM Optimizer** — `lazyclaw/browser/dom_optimizer.py`: Actionable elements, page summary, change detection.
- [x] **4.6 Site Memory** — `lazyclaw/browser/site_memory.py`: Encrypted per-domain learning with auto-cleanup.
- [x] **4.7 Browser API** — `lazyclaw/gateway/routes/browser.py`: 15 endpoints (tasks, takeover, sessions, site-memory).
- [x] **4.8 Browser Skills** — `lazyclaw/skills/builtin/browser.py`: BrowseWebSkill + ReadPageSkill.

**Verification**: Agent browses a website, reads pages, takes actions.

## Phase 5: Computer Control ✅ COMPLETE
- [x] **5.1 Security** — `lazyclaw/computer/security.py`: Command/path blocklists, regex validation.
- [x] **5.2 Native Executor** — `lazyclaw/computer/native.py`: Local subprocess, file I/O, screenshots.
- [x] **5.3 Connector Server** — `lazyclaw/computer/connector_server.py`: Server-side WS relay + token mgmt.
- [x] **5.4 Standalone Connector** — `connector/`: Desktop program with auto-reconnect, 6 handlers.
- [x] **5.5 Connector API** — `lazyclaw/gateway/routes/connector.py` + WS endpoint + 5 agent skills.

**Verification**: Agent runs shell commands, reads files, takes screenshots.

## Phase 6: Channels
- [x] **6.1 Channel Base** — `lazyclaw/channels/base.py`: ChannelAdapter ABC, InboundMessage/OutboundMessage.
- [ ] **6.2 Channel Router** — `lazyclaw/channels/router.py`: Message -> queue routing.
- [x] **6.3 Telegram** — `lazyclaw/channels/telegram.py`: python-telegram-bot polling adapter. TODO: webhook mode.
- [ ] **6.4 Discord** — `lazyclaw/channels/discord.py`: discord.py adapter.
- [ ] **6.5 WhatsApp** — `lazyclaw/channels/whatsapp.py`: whatsapp-web.js sidecar adapter.
- [ ] **6.6 Signal** — `lazyclaw/channels/signal.py`: signal-cli adapter.
- [ ] **6.7 SimpleX** — `lazyclaw/channels/simplex.py`: WebSocket CLI adapter.
- [ ] **6.8 Channels API** — `lazyclaw/gateway/routes/channels.py`: Config, bind/unbind.

**Verification**: ✅ Send Telegram message, get AI response back (with tool calling).

## Phase 7: MCP + Heartbeat ✅ COMPLETE
- [x] **7.1 MCP Client** — `lazyclaw/mcp/client.py`: Connect to external MCP servers (stdio/SSE/streamable_http).
- [x] **7.2 MCP Bridge** — `lazyclaw/mcp/bridge.py`: MCP tools ↔ BaseSkill conversion + registry integration.
- [x] **7.3 MCP Server** — `lazyclaw/mcp/server.py`: Expose LazyClaw tools as MCP server via SSE.
- [x] **7.4 MCP Manager** — `lazyclaw/mcp/manager.py`: CRUD + lifecycle for MCP connections (encrypted).
- [x] **7.5 Cron Jobs** — `lazyclaw/heartbeat/cron.py`: croniter-based cron parser and scheduler.
- [x] **7.6 Orchestrator** — `lazyclaw/heartbeat/orchestrator.py`: Job CRUD with encrypted fields.
- [x] **7.7 Heartbeat Daemon** — `lazyclaw/heartbeat/daemon.py`: Background async daemon for cron jobs.
- [x] **7.8 MCP API** — `lazyclaw/gateway/routes/mcp.py`: 7 REST endpoints.
- [x] **7.9 Jobs API** — `lazyclaw/gateway/routes/jobs.py`: 7 REST endpoints.

**Verification**: ✅ Connect external MCP server, agent uses its tools. Heartbeat daemon checks cron jobs and enqueues due tasks.

## Phase 8: LazyTasker Plugin + Docker
- [ ] **8.1 LazyTasker Plugin** — `plugins/lazytasker/`: Optional integration (tasks, projects, expenses).
- [ ] **8.2 Plugin Loader** — `lazyclaw/skills/loader.py`: Load plugin packages from filesystem.
- [ ] **8.3 Docker** — `Dockerfile`, `docker-compose.yml`: Containerized deployment.
- [ ] **8.4 Documentation** — `README.md`: Setup guide, architecture, plugin development guide.
- [ ] **8.5 Example Plugin** — `plugins/example/`: Template for community plugin development.

**Verification**: `docker compose up` boots everything. LazyTasker plugin works.

## Phase 9: Flutter App
- [ ] **9.1 Project Setup** — Flutter project, theme, navigation.
- [ ] **9.2 Auth** — Login, registration, E2E key derivation (client-side PBKDF2).
- [ ] **9.3 Chat UI** — Agent chat, message history, archives.
- [ ] **9.4 Skills UI** — Browse, create, manage skills.
- [ ] **9.5 Browser UI** — Live view, takeover, checkpoints.
- [ ] **9.6 Memory UI** — View/delete memories, daily logs.
- [ ] **9.7 Channels UI** — Configure and bind messaging channels.
- [ ] **9.8 Settings** — Model assignments, API keys, SOUL.md editor.

**Verification**: Full mobile experience matching API capabilities.

## Future: Browser Enhancements
- [ ] **Real Chrome Mode** — Connect to user's actual Chrome via CDP (Chrome DevTools Protocol) instead of headless Playwright. Uses existing connector WebSocket pattern. Benefits: already logged in everywhere, real browser fingerprint, no bot detection, no CAPTCHAs.
- [ ] **Human-like Click Delays** — Add configurable random delays between automated actions (0.3-1.5s range) to mimic human interaction patterns. Especially important for real Chrome mode where there's no natural LLM thinking gap.
- [ ] **Credential Trust Levels** — Per-site trust config so AI never sees passwords:
  - `full` — Agent reads vault, types password (current behavior)
  - `browser_only` — Server injects password directly into input field via JS/CDP, never in LLM context
  - `user_types` — Agent navigates to login, pauses, user types password, agent continues
  - `session_only` — Real Chrome mode, already logged in, no password needed

## Future: MCP Ecosystem (Zero-Cost AI)

Standalone MCP servers that plug into LazyClaw (or any MCP-compatible client).

- [ ] **mcp-freeride** — Free AI router. Fallback chain across free API sources (Groq free tier, Mistral free, HuggingFace Inference, Ollama local, Cloudflare Workers AI, etc). Auto-detects which is alive, fast, and not rate-limited. Plug in as MCP and LazyClaw never pays for basic tasks.
- [ ] **mcp-apihunter** — Community-driven free API discovery engine. Users submit endpoints → auto-validates → adds to pool. LazyClaw pulls latest registry automatically. Crowdsourced free AI registry.
- [ ] **mcp-healthcheck** — Background pinger for all configured AI sources. Scores by speed/uptime/model quality, serves live leaderboard. mcp-freeride uses this for intelligent routing.
- [ ] **mcp-taskai** — Task intelligence via free AI. Auto-categorize tasks, suggest deadlines, detect duplicates, summarize overdue pile. Uses mcp-freeride so it costs $0.
- [ ] **mcp-vaultwhisper** — Privacy-safe AI proxy. Strips identifiable data before sending to any free API, re-injects context after response. E2E encryption + free AI = rare combo.

Dependency chain:
```
LazyClaw Core
    └── mcp-taskai         (smart task features)
         └── mcp-freeride  (routes to best free AI)
              ├── mcp-apihunter   (finds new sources)
              └── mcp-healthcheck (monitors them)
```

## Phase 10: Post-Quantum Cryptography (Future)
- [ ] **10.1 Hybrid Key Exchange** — Add ML-KEM (Kyber) + X25519 hybrid key exchange for Flutter app ↔ server communication. Use `liboqs-python` (FIPS 203).
- [ ] **10.2 PQC Signatures** — ML-DSA (Dilithium) for message signing if needed (FIPS 204).
- [ ] **10.3 Encryption Format v2** — `enc:v2:` format with PQC key encapsulation for client-side E2E encryption.

**Context**: Current stack (AES-256-GCM + PBKDF2-HMAC-SHA256 + bcrypt) is already quantum-resistant — symmetric/hash-based crypto only faces Grover's quadratic speedup (256→128-bit, still infeasible). PQC is only needed for key exchange when the Flutter app establishes encrypted channels. CRQC timeline: ~2031-2035. NIST standards finalized Aug 2024.

**Verification**: Flutter app uses hybrid PQC key exchange. Data-at-rest encryption remains AES-256-GCM (already quantum-safe).

## Done
- Phase 1 (Foundation): ✅ COMPLETE — Crypto, DB, config, LLM router, agent, gateway, CLI wizard, auth, model manager
- Phase 2 (Skills + Tools): ✅ COMPLETE — BaseSkill, registry, built-in skills, tool executor, agentic loop, code sandbox, skill writer, skills API
- Phase 3 (Queue + Memory + Personality): ✅ COMPLETE — Lane queue, personal memory, SOUL.md, context builder, credential vault, daily logs, memory/vault API
- Phase 5 (Computer Control): ✅ COMPLETE — Security manager, native executor, connector server, standalone connector, REST + WS API, 5 agent skills
- Phase 6 (Channels): Telegram polling adapter, channel base abstractions (partial)
- Phase 7 (MCP + Heartbeat): ✅ COMPLETE — MCP client/server/bridge, manager, heartbeat daemon, cron jobs, orchestrator, 14 API endpoints
