# TODO

## Phase 1: Foundation
- [x] **1.1 Crypto core** — `lazyclaw/crypto/encryption.py`: AES-256-GCM + PBKDF2, `enc:v1:` format, server-side key derivation.
- [x] **1.2 Crypto fields** — encrypt_field, decrypt_field, is_encrypted (in encryption.py).
- [x] **1.3 Database** — `lazyclaw/db/`: aiosqlite connection pool, schema.sql (7 tables), WAL mode.
- [x] **1.4 Config** — `lazyclaw/config.py`: Env var loading via python-dotenv, Config dataclass, save_env().
- [ ] **1.5 Auth** — `lazyclaw/gateway/auth.py`: Registration, login, sessions, encryption_salt. (MVP uses default user)
- [x] **1.6 LLM Router** — `lazyclaw/llm/router.py` + `providers/`: OpenAI + Anthropic with tool calling support.
- [ ] **1.7 Model Manager** — `lazyclaw/llm/model_manager.py`: Model catalog, per-user assignments.
- [x] **1.8 Basic Agent** — `lazyclaw/runtime/agent.py`: Multi-turn agentic loop with tool calling.
- [x] **1.9 Conversation Memory** — Messages stored encrypted in agent_messages, last 20 loaded as context.
- [x] **1.10 Gateway** — `lazyclaw/gateway/app.py`: FastAPI with health check + `/api/agent/chat`.
- [x] **1.11 Entry Point** — `lazyclaw/cli.py` (setup wizard + start), `main.py`, `__main__.py`, pyproject.toml scripts.

**Verification**: ✅ `lazyclaw setup` configures everything. `lazyclaw start` runs agent. Telegram + API both work.

## Phase 2: Skills + Tools
- [x] **2.1 BaseSkill ABC** — `lazyclaw/skills/base.py`: Abstract skill class with to_openai_tool() conversion.
- [x] **2.2 Skill Registry** — `lazyclaw/skills/registry.py`: Unified registry with register_defaults().
- [x] **2.3 Instruction Skills** — `lazyclaw/skills/manager.py`: NL template CRUD.
- [ ] **2.4 Code Skills** — `lazyclaw/skills/sandbox.py`: AST validation + restricted exec.
- [ ] **2.5 Skill Writer** — `lazyclaw/skills/writer.py`: AI-generated code skills.
- [x] **2.6 Built-in Skills** — `lazyclaw/skills/builtin/`: web_search (DuckDuckGo), get_time, calculate.
- [x] **2.7 Tool Executor** — `lazyclaw/runtime/tool_executor.py`: Dispatch tool calls to skill registry.
- [ ] **2.8 Skills API** — `lazyclaw/gateway/routes/skills.py`: CRUD endpoints.

**Verification**: ✅ Agent calls tools during chat (web_search, get_time, calculate). Multi-turn agentic loop works.

## Phase 3: Queue + Memory + Personality
- [x] **3.1 Lane Queue** — `lazyclaw/queue/lane.py`: FIFO per-user serial queue.
- [x] **3.2 Worker Pool** — Integrated into LaneQueue (per-user processor tasks).
- [x] **3.3 Personal Memory** — `lazyclaw/memory/personal.py`: Extract from LazyTasker. Encrypted facts/prefs.
- [x] **3.4 SOUL.md** — `lazyclaw/runtime/personality.py`: Load personality file, inject into system prompt.
- [x] **3.5 Context Builder** — `lazyclaw/runtime/context_builder.py`: Assemble personality + memory + skills.
- [ ] **3.6 Daily Logs** — `lazyclaw/memory/daily_log.py`: Auto-summarize sessions.
- [x] **3.7 Credential Vault** — `lazyclaw/crypto/vault.py`: Encrypted API key storage.
- [ ] **3.8 Memory API** — `lazyclaw/gateway/routes/memory.py` + `vault.py`: Endpoints.

**Verification**: Messages queue serially. Memory persists across sessions. SOUL.md customization works.

## Phase 4: Browser Automation
- [ ] **4.1 Browser Manager** — `lazyclaw/browser/manager.py`: Extract session management from LazyTasker.
- [ ] **4.2 Browser Agent** — `lazyclaw/browser/agent.py`: Extract LLM-driven browser from LazyTasker.
- [ ] **4.3 Semantic Snapshots** — `lazyclaw/browser/semantic.py`: NEW. Accessibility tree -> text (50KB vs 5MB).
- [ ] **4.4 Page Reader** — `lazyclaw/browser/page_reader.py`: Extract from LazyTasker. Lightweight extraction.
- [ ] **4.5 DOM Optimizer** — `lazyclaw/browser/dom_optimizer.py`: Extract from LazyTasker. Actionable elements.
- [ ] **4.6 Site Memory** — `lazyclaw/browser/site_memory.py`: Extract from LazyTasker. Encrypted per-domain.
- [ ] **4.7 Browser API** — `lazyclaw/gateway/routes/browser.py`: Task CRUD, live view, takeover.

**Verification**: Agent browses a website, reads pages, takes actions.

## Phase 5: Computer Control
- [ ] **5.1 Security** — `lazyclaw/computer/security.py`: Extract from LazyTasker connector. Blocklists.
- [ ] **5.2 Native Executor** — `lazyclaw/computer/native.py`: NEW. Local subprocess execution.
- [ ] **5.3 Connector Server** — `lazyclaw/computer/connector_server.py`: Extract WS manager from LazyTasker.
- [ ] **5.4 Standalone Connector** — `connector/`: Adapt from LazyTasker. Desktop program.
- [ ] **5.5 Connector API** — `lazyclaw/gateway/routes/connector.py` + WS endpoint.

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

## Phase 7: MCP + Heartbeat
- [ ] **7.1 MCP Client** — `lazyclaw/mcp/client.py`: NEW. Connect to external MCP servers (stdio/SSE/WS).
- [ ] **7.2 MCP Bridge** — `lazyclaw/mcp/bridge.py`: NEW. MCP tools <-> OpenAI function format.
- [ ] **7.3 MCP Server** — `lazyclaw/mcp/server.py`: NEW. Expose LazyClaw tools as MCP server.
- [ ] **7.4 Heartbeat Daemon** — `lazyclaw/heartbeat/daemon.py`: NEW. Proactive checks (HEARTBEAT.md).
- [ ] **7.5 Cron Jobs** — `lazyclaw/heartbeat/cron.py`: Extract from LazyTasker.
- [ ] **7.6 Orchestrator** — `lazyclaw/heartbeat/orchestrator.py`: Extract from LazyTasker. Monitor/worker.
- [ ] **7.7 MCP + Jobs API** — `lazyclaw/gateway/routes/mcp.py` + `jobs.py`.

**Verification**: Connect external MCP server, agent uses its tools. Heartbeat acts proactively.

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

## Done
- Phase 1 (Foundation): Crypto, DB, config, LLM router, agent, gateway, CLI wizard
- Phase 2 (Skills + Tools): BaseSkill, registry, 3 built-in skills, tool executor, agentic loop
- Phase 6 (Channels): Telegram polling adapter, channel base abstractions
- SOUL.md personality system (from Phase 3)
- Personal memory + context builder (from Phase 3)
- Encrypted credential vault with vault skills + LLM router fallback (from Phase 3)
- Instruction skills with encrypted NL templates (from Phase 2)
- Skill categories (research, utility, memory, security, skills, custom)
- Lane queue for per-user serial execution (from Phase 3)
