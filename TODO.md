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

## Phase 6: Channels (Telegram — partial)
- [x] **6.1 Channel Base** — `lazyclaw/channels/base.py`: ChannelAdapter ABC, InboundMessage/OutboundMessage.
- [x] **6.2 Telegram** — `lazyclaw/channels/telegram.py`: python-telegram-bot polling adapter.

**Verification**: ✅ Send Telegram message, get AI response back (with tool calling). Remaining channels moved to Phase 11.

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

## Permissions & Approval System ✅ COMPLETE
- [x] **P.1 Permission Models** — `lazyclaw/permissions/models.py`: ResolvedPermission, ApprovalRequest, AuditEntry frozen dataclasses.
- [x] **P.2 Permission Settings** — `lazyclaw/permissions/settings.py`: CRUD from users.settings JSON (follows eco_settings pattern).
- [x] **P.3 Permission Checker** — `lazyclaw/permissions/checker.py`: Resolves skill → allow/ask/deny (overrides → category → hint → fallback).
- [x] **P.4 Approval System** — `lazyclaw/permissions/approvals.py`: Create/approve/deny/expire requests, encrypted arguments.
- [x] **P.5 Audit Log** — `lazyclaw/permissions/audit.py`: Fire-and-forget logger, query, cleanup (90-day retention).
- [x] **P.6 Permissions API** — `lazyclaw/gateway/routes/permissions.py`: 8 REST endpoints (settings, skills, approvals, audit).
- [x] **P.7 DB Schema** — Added `role` column to users, `approval_requests` + `audit_log` tables.
- [x] **P.8 Admin Role** — First registered user = admin. `require_admin()` dependency.
- [x] **P.9 Inline Approval Flow** — Agent loop detects APPROVAL_REQUIRED marker, creates DB request, asks user.
- [x] **P.10 Tool Executor Integration** — Permission check before execution (deny blocks, ask requires approval, allow passes).

**Verification**: Permission checker resolves all skills. Deny blocks execution. Ask triggers inline approval flow. Admin role assigned to first user. Audit log records all actions.

## Future: LazyTasker Plugin + Docker
- [ ] **LazyTasker Plugin** — `plugins/lazytasker/`: Optional integration (tasks, projects, expenses).
- [ ] **Plugin Loader** — `lazyclaw/skills/loader.py`: Load plugin packages from filesystem.
- [ ] **Docker** — `Dockerfile`, `docker-compose.yml`: Containerized deployment.
- [ ] **Documentation** — `README.md`: Setup guide, architecture, plugin development guide.
- [ ] **Example Plugin** — `plugins/example/`: Template for community plugin development.

## Future: Browser Enhancements
- [x] **Real Chrome Mode** — Connect to user's actual Chrome via CDP (Chrome DevTools Protocol). On-demand connection, 5 skills (see_browser, list_tabs, read_tab, switch_tab, browser_action). Coexists with Playwright headless.
- [ ] **Human-like Click Delays** — Add configurable random delays between automated actions (0.3-1.5s range) to mimic human interaction patterns. Especially important for real Chrome mode where there's no natural LLM thinking gap.
- [ ] **Credential Trust Levels** — Per-site trust config so AI never sees passwords:
  - `full` — Agent reads vault, types password (current behavior)
  - `browser_only` — Server injects password directly into input field via JS/CDP, never in LLM context
  - `user_types` — Agent navigates to login, pauses, user types password, agent continues
  - `session_only` — Real Chrome mode, already logged in, no password needed

## Future: MCP Ecosystem (Zero-Cost AI)

Standalone MCP servers that plug into LazyClaw (or any MCP-compatible client).

- [x] **mcp-freeride** — Free AI router. 7 providers (Groq, Gemini, OpenRouter, Together, Mistral, HuggingFace, Ollama). Health tracking, latency ranking, auto-fallback. Standalone in `mcp-freeride/`.
- [x] **mcp-apihunter** — Community-driven free API discovery engine. Users submit endpoints → auto-validates → adds to pool. Auto-scanner discovers OpenRouter free models, local Ollama, and known free-tier APIs. LazyClaw pulls latest registry automatically. Standalone in `mcp-apihunter/`.
- [x] **mcp-healthcheck** — Background pinger for all configured AI sources. Scores by speed/uptime/model quality, serves live leaderboard. mcp-freeride uses this for intelligent routing. Standalone in `mcp-healthcheck/`.
- [x] **mcp-taskai** — Task intelligence via free AI. Auto-categorize tasks, suggest deadlines, detect duplicates, summarize overdue pile. Uses free AI directly ($0). Standalone in `mcp-taskai/`.
- [x] **mcp-vaultwhisper** — Privacy-safe AI proxy. Strips identifiable data before sending to any free API, re-injects context after response. E2E encryption + free AI = rare combo. Standalone in `mcp-vaultwhisper/`.

Dependency chain:
```
LazyClaw Core
    └── mcp-taskai         (smart task features)
         └── mcp-freeride  ✅ (routes to best free AI)
              ├── mcp-apihunter   (finds new sources)
              └── mcp-healthcheck (monitors them)
```

## Future: ECO Mode — Smart Token Routing (Needs Planning)

Three-tier cost mode for the agent. Needs detailed planning before implementation.

### The 3 Modes

| Mode | Rule | Cost |
|------|------|------|
| **ECO** | Free only. Never touches paid. If rate-limited → wait. If too complex → tell user. $0 always. | $0 |
| **HYBRID** | Agent brain auto-decides per task. Simple → free, complex → paid. Seamless. | Low |
| **FULL** | Always paid. Maximum quality, no routing. | Normal |

### ECO Mode Behavior (strict $0)
- Rate-limited? → Wait for free slot (queue with countdown)
- Still limited? → Try smaller free model (8b instead of 70b)
- All providers down? → "All free APIs busy, retrying in 30s..."
- Task too complex for free? → "This needs paid model. Switch to HYBRID or simplify your request?"
- **Never** sneaks in a paid call. ECO means ECO.

### HYBRID Mode — Agent Brain Decides

| Signal | Routes to |
|--------|-----------|
| Summarize / translate / classify | Free |
| Single-turn, no history needed | Free |
| Short reply expected (<200 tokens) | Free |
| Cron job / background task | Free |
| Browser page reading | Free |
| Code generation | Paid |
| Multi-step reasoning / planning | Paid |
| Tool calling chains | Paid |
| Follow-up needing context | Paid |
| Browser complex navigation | Paid |

Simple heuristic first (pattern matching on task type), no LLM classifier needed.

### User Control Over Routing

Users have full control over which AIs handle which tasks:

**Provider Selection:**
- "Use only Groq" → locks all ECO tasks to Groq, waits if rate-limited
- "Use Groq + Gemini" → custom mix, user picks which providers are in their pool
- "Use all free" → default, mcp-freeride picks the fastest available
- Per-provider toggle: enable/disable any provider from the UI

**Per-Task AI Assignment:**
- User assigns specific AI per task type from the UI:
  ```
  Customer service bot  → groq/llama-3.3-70b (fast responses)
  Price monitoring       → gemini/flash (good at structured data)
  Blog post drafting     → openrouter/deepseek (good at writing)
  Translation            → mistral/small (EU-based, multilingual)
  Background cron jobs   → ollama/llama3.2 (local, unlimited)
  ```
- Different tasks executed by different AIs simultaneously
- Each response tagged with which AI handled it: `[🌿 groq/llama3]`

**Post-Execution Feedback:**
- User sees which AI handled each task after the fact
- Don't like a provider's quality? → disable it for that task type
- See response quality per provider over time → adjust assignments
- One-click "never use this AI again" per provider

**Zero-Cost Use Cases (all ECO mode):**
- Free customer service chatbot (Groq for speed)
- Free price/stock monitoring (cron job → Gemini for data extraction)
- Free blog post drafting (OpenRouter/DeepSeek for writing)
- Free email classification/summarization
- Free social media content generation
- Free document translation (Mistral for multilingual)
- Free code review assistant (local Ollama for privacy)

### Implementation Items

- [x] **ECO Router** — `lazyclaw/llm/eco_router.py`: Sits between agent and LLM router. 3 modes (eco/hybrid/full), task classifier, provider locking, badge tagging.
- [x] **Rate Limit Tracker** — `lazyclaw/llm/rate_limiter.py`: Per-provider sliding window counters. Pre-emptive switching. Known limits for all 7 providers.
- [x] **Provider Pool Manager** — User-configurable provider pools via ECO settings. Lock to specific provider, custom mixes, allowed_providers list.
- [ ] **Task → AI Assignment Table** — New DB table `eco_task_assignments`: maps task_type → provider/model per user. UI lets user drag-and-drop assign AIs to task types.
- [ ] **Per-Role Rate Budgets** — Isolated rate limits per role so roles don't starve each other.
- [x] **Task Classifier** — Heuristic regex patterns in eco_router: free/paid keyword matching + message length.
- [x] **Response Attribution** — Responses tagged with `[ECO provider/model]` or `[PAID model]` badges when show_badges enabled.
- [ ] **Provider Feedback Loop** — Track user satisfaction per provider (thumbs up/down, disable actions). Auto-deprioritize providers user doesn't like.
- [ ] **Context Handoff** — When HYBRID switches free→paid: send compressed summary, not full history. When paid→free: include only conclusion, not reasoning chain. Saves tokens on both sides.
- [x] **Token Budget Dashboard** — In-memory usage tracking (free vs paid counts per user). Basic stats via eco_router.get_usage().
- [x] **ECO Settings** — `lazyclaw/llm/eco_settings.py`: Stored in users.settings JSON under "eco" key:
  ```
  eco_mode: eco | hybrid | full
  eco_show_badges: true               # show [ECO groq/llama3] tags
  eco_monthly_paid_budget: 5.00       # max paid spend then force ECO
  eco_allowed_providers: [groq, gemini, openrouter]  # user's active pool
  eco_locked_provider: null            # "groq" = use only groq
  eco_task_overrides: {}               # per-task-type provider assignments
  ```
- [x] **ECO API** — `lazyclaw/gateway/routes/eco.py`: 5 endpoints — settings CRUD, usage stats, rate limits, provider list.

## Phase 8: Multi-Agent Teams (Inspired by CAMEL)

Internal agent-to-agent collaboration. A **team lead** (stronger AI) manages **specialist workers** — each with their own system prompt, tools, and expertise. Not about cost — about better results through specialization. Inspired by [CAMEL](https://github.com/camel-ai/camel) role-playing concept.

- [x] **8.1 Specialist Definition** — `lazyclaw/teams/specialist.py`: SpecialistConfig frozen dataclass (name, system_prompt, allowed_skills, model). Registry of built-in specialists.
- [x] **8.2 Specialist Runner** — `lazyclaw/teams/runner.py`: Run a specialist as an independent agent loop with its own tool subset. Returns structured result.
- [x] **8.3 Team Lead Agent** — `lazyclaw/teams/lead.py`: Receives user request, analyzes complexity, breaks into sub-tasks, delegates to specialists, merges results into final answer.
- [x] **8.4 Parallel Execution** — `lazyclaw/teams/executor.py`: Run multiple specialists concurrently via asyncio.gather(). Results collected and fed back to team lead.
- [x] **8.5 Critic Agent** — Integrated into team lead merge step (single LLM call for merge + critic when 2+ specialists). Auto-activates based on critic_mode setting.
- [x] **8.6 Agent Conversations** — `lazyclaw/teams/conversation.py`: Internal message queue between agents (not user-visible). Stored encrypted in `agent_team_messages` table for debugging.
- [x] **8.7 Dynamic Team Composition** — Team lead decides which specialists to involve based on the task. Simple question → no team, answer directly. Complex task → assemble the right team.
- [x] **8.8 Built-in Specialists** — 4 default specialists:
  - `browser_specialist` — Web browsing, page reading, form filling. Has browser tools only.
  - `code_specialist` — Code generation, skill writing, debugging. Has code sandbox.
  - `research_specialist` — Web search, data gathering, summarization. Has search tools.
  - `memory_specialist` — Context recall, fact checking against stored memories.
- [x] **8.9 Teams API** — `lazyclaw/gateway/routes/teams.py`: 8 REST endpoints (settings, specialists, sessions).
- [x] **8.10 Agent Integration** — Wire team mode into main agent loop. Agent detects complex tasks and activates team mode automatically.
- [ ] **8.11 Exclusive Skills** — Specialist-only skills not available to solo agent or other specialists. (Future)

**Verification**: User sends complex request → team lead splits into sub-tasks → specialists run in parallel → critic reviews → merged answer returned. Simple requests bypass team mode.

## Phase 9: Context Compression

Smart context window management for long conversations. Compress older messages into summaries instead of dropping them.

- [x] **9.1 Message Classifier** — `lazyclaw/memory/classifier.py`: Heuristic priority classification (high/medium/low). Tool results + code = high, greetings = low.
- [x] **9.2 Rolling Summarizer** — `lazyclaw/memory/summarizer.py`: LLM-powered summarization with priority guidance. Keeps high items verbatim, compresses medium, drops low.
- [x] **9.3 Compression Engine** — `lazyclaw/memory/compressor.py`: Sliding window (last 15 full, older summarized). Persistent summaries in `message_summaries` table.
- [x] **9.4 Agent Integration** — Updated `agent.py`: loads all messages, compresses via compressor, passes to agent loop.
- [ ] **9.5 Team Context Handoff** — When multi-agent teams hand off between specialists: send compressed summary, not full history. Saves tokens. (Future)
- [x] **9.6 Compression API** — `lazyclaw/gateway/routes/compression.py`: Stats + force re-summarize (2 endpoints).

**Verification**: Long conversation stays coherent past 50+ messages. Agent recalls facts from compressed history. Token usage drops significantly vs raw loading.

## Phase 10: Session Replay

Record full agent sessions as replayable traces. Every LLM call, tool invocation, result = recorded step-by-step.

- [x] **10.1 Trace Recorder** — `lazyclaw/replay/recorder.py`: Fire-and-forget recorder capturing every agent action (LLM call, tool call, tool result, team delegation, final response) into `agent_traces` table. Encrypted.
- [x] **10.2 Trace Models** — `lazyclaw/replay/models.py`: TraceEntry, TraceSession frozen dataclasses. 9 entry types.
- [x] **10.3 Trace Storage** — DB schema: `agent_traces` table (session_id, sequence, entry_type, content encrypted, metadata) + `trace_shares` table.
- [x] **10.4 Replay Engine** — `lazyclaw/replay/engine.py`: Load trace by session or share token, step through entries as timeline. Delete traces.
- [x] **10.5 Share Tokens** — `lazyclaw/replay/sharing.py`: Generate shareable URL-safe tokens with optional expiration. Revoke shares.
- [x] **10.6 Replay API** — `lazyclaw/gateway/routes/replay.py`: 7 REST endpoints (traces CRUD, share CRUD, public view).
- [x] **10.7 Agent Integration** — Recorder wired into agent loop: records user message, LLM calls, LLM responses, tool calls, tool results, team delegations, final response.

**Verification**: Run agent task → view full replay step-by-step → share via token → recipient sees the same trace. Team conversations visible in replay.

## Future: Workflow Builder UI

Visual drag-and-drop editor (React Flow style) for composing multi-step agent workflows. Requires web frontend — deferred until web UI exists.

- [ ] **Workflow Graph Editor** — React Flow canvas, skill blocks as nodes, data flow as edges.
- [ ] **Workflow Compiler** — Graph → executable workflow stored in DB.
- [ ] **Workflow Runner** — Execute compiled workflows via agent runtime.

## Future: Skill Benchmarks

Eval-driven skill development. Define standard tasks per skill with expected outcomes. Run benchmarks after changes to measure agent quality.

- [ ] **Benchmark Definitions** — Standard test cases per skill with expected results.
- [ ] **Benchmark Runner** — Execute benchmarks, compare actual vs expected.
- [ ] **Regression Detection** — Flag quality drops after code changes.

## Phase 11: Channels (Remaining)
- [ ] **11.1 Channel Router** — `lazyclaw/channels/router.py`: Message -> queue routing.
- [ ] **11.2 Discord** — `lazyclaw/channels/discord.py`: discord.py adapter.
- [ ] **11.3 WhatsApp** — `lazyclaw/channels/whatsapp.py`: whatsapp-web.js sidecar adapter.
- [ ] **11.4 Signal** — `lazyclaw/channels/signal.py`: signal-cli adapter.
- [ ] **11.5 SimpleX** — `lazyclaw/channels/simplex.py`: WebSocket CLI adapter.
- [ ] **11.6 Channels API** — `lazyclaw/gateway/routes/channels.py`: Config, bind/unbind.

**Verification**: Messages from Discord/WhatsApp/Signal/SimpleX route through queue and get AI responses.

## Phase 12: Flutter App
- [ ] **12.1 Project Setup** — Flutter project, theme, navigation.
- [ ] **12.2 Auth** — Login, registration, E2E key derivation (client-side PBKDF2).
- [ ] **12.3 Chat UI** — Agent chat, message history, archives.
- [ ] **12.4 Skills UI** — Browse, create, manage skills.
- [ ] **12.5 Browser UI** — Live view, takeover, checkpoints.
- [ ] **12.6 Memory UI** — View/delete memories, daily logs.
- [ ] **12.7 Channels UI** — Configure and bind messaging channels.
- [ ] **12.8 Settings** — Model assignments, API keys, SOUL.md editor.

**Verification**: Full mobile experience matching API capabilities.

## Phase 13: Post-Quantum Cryptography (Future)
- [ ] **13.1 Hybrid Key Exchange** — Add ML-KEM (Kyber) + X25519 hybrid key exchange for Flutter app ↔ server communication. Use `liboqs-python` (FIPS 203).
- [ ] **13.2 PQC Signatures** — ML-DSA (Dilithium) for message signing if needed (FIPS 204).
- [ ] **13.3 Encryption Format v2** — `enc:v2:` format with PQC key encapsulation for client-side E2E encryption.

**Context**: Current stack (AES-256-GCM + PBKDF2-HMAC-SHA256 + bcrypt) is already quantum-resistant — symmetric/hash-based crypto only faces Grover's quadratic speedup (256→128-bit, still infeasible). PQC is only needed for key exchange when the Flutter app establishes encrypted channels. CRQC timeline: ~2031-2035. NIST standards finalized Aug 2024.

**Verification**: Flutter app uses hybrid PQC key exchange. Data-at-rest encryption remains AES-256-GCM (already quantum-safe).

## Done
- Phase 1 (Foundation): ✅ COMPLETE — Crypto, DB, config, LLM router, agent, gateway, CLI wizard, auth, model manager
- Phase 2 (Skills + Tools): ✅ COMPLETE — BaseSkill, registry, built-in skills, tool executor, agentic loop, code sandbox, skill writer, skills API
- Phase 3 (Queue + Memory + Personality): ✅ COMPLETE — Lane queue, personal memory, SOUL.md, context builder, credential vault, daily logs, memory/vault API
- Phase 4 (Browser Automation): ✅ COMPLETE — Playwright manager, browser agent, page reader, DOM optimizer, site memory, 15 API endpoints
- Phase 5 (Computer Control): ✅ COMPLETE — Security manager, native executor, connector server, standalone connector, REST + WS API, 5 agent skills
- Phase 6 (Channels — partial): ✅ Telegram polling adapter, channel base abstractions
- Phase 7 (MCP + Heartbeat): ✅ COMPLETE — MCP client/server/bridge, manager, heartbeat daemon, cron jobs, orchestrator, 14 API endpoints
- MCP Ecosystem: ✅ COMPLETE — mcp-freeride, mcp-healthcheck, mcp-apihunter, mcp-vaultwhisper, mcp-taskai
- ECO Mode (core): ✅ COMPLETE — eco_router, rate_limiter, eco_settings, task classifier, response badges, 5 API endpoints
- Permissions & Approval System: ✅ COMPLETE — Permission checker (allow/ask/deny), inline approval flow, admin role, audit log, 8 API endpoints
- Phase 8 (Multi-Agent Teams): ✅ COMPLETE — Team lead, 4 built-in specialists, parallel executor, specialist runner, critic (merged), team conversations, settings, 8 API endpoints
- Phase 9 (Context Compression): ✅ COMPLETE — Message classifier, LLM summarizer, sliding window compressor, persistent summaries, agent integration, 2 API endpoints
- Phase 10 (Session Replay): ✅ COMPLETE — Trace recorder, models, engine, share tokens, agent integration, 7 API endpoints
- Agent Observability: ✅ COMPLETE — Inline activity stream, work summaries, specialist thinking events, Rich dashboard (/? query), friendly MCP display names, compact approvals, Telegram rich notifications with specialist grid + edit throttling
- Agent Self-Awareness: ✅ COMPLETE — Context builder injects capabilities (skills, MCP servers, config) into system prompt dynamically. SOUL.md allows proactive tool use. Smart tool routing keywords expanded.
- Real Chrome Mode: ✅ COMPLETE — CDP client, BrowserBackend ABC (Playwright + CDP coexist), 5 real browser skills, on-demand connection, /connect-browser CLI command
- NL Job Scheduling: ✅ COMPLETE — 4 job skills (schedule_job, set_reminder, list_jobs, manage_job), one-time reminder support in heartbeat daemon with auto-delete
- Server Dashboard: ✅ COMPLETE — Rich Live dashboard for `lazyclaw start`, MultiCallback forwarding, activity log, active request tracking
- CLI Side-Channel: ✅ COMPLETE — prompt_toolkit async input while agents work, side messages injected into team merge
- Browser-Use Compat: ✅ COMPLETE — _BrowserChatOpenAI with __getattr__/__setattr__ for browser-use 0.12 + langchain-openai 1.1.9
- Timezone Fix: ✅ COMPLETE — get_time defaults to system local timezone, deprecated utcnow() replaced
- Research Specialist: ✅ Updated — now has read_file, list_directory, run_command for local file access
- Free AI Auto-Discovery: ✅ COMPLETE — mcp-apihunter scanner (OpenRouter free models, Ollama local, known free tiers), startup scan, `apihunter_scan` MCP tool
- Dynamic Ollama Models: ✅ COMPLETE — OllamaProvider.refresh_models() from /api/tags, pull/delete/show helpers, FreeRideRouter.refresh_ollama()
- ECO NL Skills: ✅ COMPLETE — eco_set_mode, eco_show_status, eco_set_provider (3 skills)
- Provider NL Skills: ✅ COMPLETE — provider_list, provider_add, provider_scan (3 skills)
- Performance Optimization: ✅ COMPLETE — PBKDF2 LRU cache (420ms→0ms), DB connection pool (14ms→0.2ms), SOUL.md mtime cache, batch DB inserts (executemany), fast chat path skips full context build, DB indexes on hot queries
- Complexity Model Routing: ✅ COMPLETE — NanoClaw-inspired tier routing (simple→fast_model, standard→default, complex→smart_model), regex classifier in eco_router, no extra LLM calls
- Delegate Tool: ✅ COMPLETE — Replaces team lead LLM analysis call, agent calls delegate(specialist, instruction) naturally, parallel dispatch via asyncio.gather, saves 1-2 LLM calls per delegation
- Specialists Streamlined: ✅ Updated — Dropped memory_specialist (redundant), 3 built-ins: browser, research, code
- Browser Architecture: ✅ COMPLETE — Headless Chrome auto-launch, shared cookie profile (data/browser_profiles/default), visible=true param for user-facing tasks, human-like random delays (0.2-1.5s), auto-tab creation
- Telegram Security: ✅ COMPLETE — Admin chat lock (first /start claims admin), unauthorized chats blocked, channel context injected for screenshots
- Telegram Screenshots: ✅ COMPLETE — ToolResult+Attachment dataclass, see_browser returns PNG, _TelegramCallback sends photos via send_photo, retry on network errors
- Telegram UI: ✅ Updated — Permanent messages for tool/specialist completions, stats footer, retry logic with backoff
- CLI Fixes: ✅ Updated — Ctrl+C double-press (graceful then force), handle_sigint=False for side input, 0.1s poll, tool errors shown in red
- Token Tracking: ✅ Fixed — OpenAI streaming reads usage chunk after finish_reason, Anthropic field names normalized (prompt_tokens/completion_tokens/total_tokens)
- MCP Log Suppression: ✅ COMPLETE — mcp.server.lowlevel.server set to WARNING in all 6 MCP servers, child env LOG_LEVEL=ERROR
- MCP Parallel Startup: ✅ COMPLETE — connect_and_register_bundled_mcps uses asyncio.gather (12s→~2s)
- Clean Shutdown: ✅ COMPLETE — disconnect_all() called before event loop closes in both CLI and server modes, no more BaseSubprocessTransport errors
- Layered Summaries: ✅ COMPLETE — Daily auto-summary (gpt-5-mini, fire-and-forget on first msg of new day), weekly summary (every Sunday), daily logs injected into agent context, compressor uses daily logs to skip 90s LLM re-summarization
- Ollama NL Skills: ✅ COMPLETE — ollama_list, ollama_install, ollama_delete, ollama_show (4 skills)
- Full NL Control: ✅ COMPLETE — 34 new skills covering ALL features via natural language:
  - System: show_status, run_doctor, show_usage, show_logs, set_model (5 skills)
  - Permissions: show_permissions, set_permission, list_pending_approvals, decide_approval, query_audit_log (5 skills)
  - MCP: list_mcp_servers, add_mcp_server, remove_mcp_server, connect_mcp_server, disconnect_mcp_server (5 skills)
  - Teams: show_team_settings, set_team_mode, set_critic_mode, list_specialists, manage_specialist (5 skills)
  - Memory: list_memories, delete_memory, list_daily_logs, view_daily_log, delete_daily_log (5 skills)
  - Replay: list_traces, view_trace, delete_trace, share_trace, manage_shares (5 skills)
  - Session: clear_history, show_compression (2 skills)
  - Browser: list_site_memories, delete_site_memory (2 skills)
- ECO Pipeline Wiring: ✅ COMPLETE — _ensure_free_router() async loads apihunter providers + refreshes Ollama models, dynamic valid_providers in eco_settings
- Total registered skills: 72+ (up from ~38)
