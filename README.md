<p align="center">
  <pre>
   _                     _____ _
  | |    __ _ _____   _ / ____| |
  | |   / _` |_  / | | | |    | | __ ___      __
  | |  | (_| |/ /| |_| | |    | |/ _` \ \ /\ / /
  | |___\__,_/___|\__, | |____| | (_| |\ V  V /
  |______\        |___/ \_____|_|\__,_| \_/\_/
  </pre>
</p>

<h3 align="center">E2E Encrypted AI Agent Platform</h3>

<p align="center">
  <em>What if your AI agent couldn't read your data, even if the server was compromised?</em>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> &bull;
  <a href="#architecture">Architecture</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#encryption">Encryption</a> &bull;
  <a href="#eco-mode">ECO Mode</a> &bull;
  <a href="#browser">Browser</a> &bull;
  <a href="#mcp">MCP</a> &bull;
  <a href="#integrations">Integrations</a> &bull;
  <a href="#telegram">Telegram</a> &bull;
  <a href="#roadmap">Roadmap</a>
</p>

---

**LazyClaw** is an open-source AI agent platform where every piece of user data is encrypted with AES-256-GCM before it touches disk. Conversations, memories, skills, credentials, scheduled jobs — all encrypted. The server never sees plaintext.

Built in Python. Native MCP. Multi-agent delegation. Cost-aware routing. Browser automation via CDP with a **live canvas, checkpoints, and saved templates for recurring flows like government appointments**. Telegram + WhatsApp + Instagram + Email. 128 builtin skills + ~67 MCP tools discoverable at runtime. React Web UI with 12 pages + persistent chat sidebar.

## Why LazyClaw?

Most AI agent platforms store everything in plaintext. Your conversations, API keys, browsing history, personal memories — sitting unencrypted on disk or in a database. [42,000 exposed instances](https://www.bitsight.com/blog/openclaw-ai-security-risks-exposed-instances) of the leading platform were found with no authentication and full data access.

LazyClaw takes a different approach:

| | LazyClaw | Others |
|---|---|---|
| **User data** | AES-256-GCM encrypted at rest | Plaintext files / DB |
| **API keys** | Encrypted credential vault | `.env` plaintext |
| **Conversations** | Encrypted per-user | Plaintext JSONL |
| **Memories** | Encrypted personal facts | Plaintext markdown |
| **Tool selection** | Smart discovery via search_tools (4 base tools, ~195 discoverable) | All tools every turn (5K+ tokens) |
| **Cost routing** | 3-mode Brain/Worker split across 5 providers (Anthropic · MiniMax subscription · OpenAI · local Ollama · Claude CLI) | Manual model config |
| **Multi-agent** | Inline delegation to specialists | Fire-and-forget sub-agents |
| **MCP** | Native client + server + 6 bundled servers | Community plugins |
| **Integrations** | n8n native (full workflow CRUD, 6 skills, templates, Docker) | Manual API wiring |
| **Channels** | Telegram + WhatsApp + Instagram + Email MCPs | Browser-only for most |
| **Browser control** | Live canvas + checkpoints + saved templates + noVNC takeover (zero extra tokens) | Screenshots or nothing |
| **Web UI** | React control panel (12 pages + chat sidebar + live BrowserCanvas) + WebSocket streaming | Varies |
| **Language** | Python (largest AI ecosystem) | TypeScript |

## Quickstart

```bash
git clone https://github.com/Bsh13lder/Lazy-Claw.git
cd Lazy-Claw
./install.sh
```

That's it. The installer handles Python, dependencies, and setup automatically.

```bash
lazyclaw        # Chat REPL
lazyclaw start  # Full server (API + Telegram + TUI Dashboard)
lazyclaw setup  # Re-run setup wizard
```

<details>
<summary><strong>Manual install</strong></summary>

```bash
git clone https://github.com/Bsh13lder/Lazy-Claw.git
cd Lazy-Claw
pip install pipx && pipx install --editable .
lazyclaw setup
```

Requires: Python 3.11+, pipx, and at least one LLM path — Anthropic API key (recommended), MiniMax API key, OpenAI key, local Ollama, or the Anthropic Claude CLI.
</details>

**Requirements:** Python 3.11+ (installed automatically on macOS) and at least one LLM path — Anthropic API key (recommended), MiniMax API key, OpenAI key, local Ollama, or the Anthropic Claude CLI. Any one works.

> **LazyClaw is optimized for Claude.** Sonnet 4.6 as brain + Haiku 4.5 as workers gives the fastest responses (2–5s) with excellent tool use, and everything is tuned around this pairing. **MiniMax M2.7 is also tested and works really well** as an alternative brain for users who prefer a flat subscription over per-token billing. LazyClaw auto-configures optimal model routing when it detects any supported provider key.

## Architecture

```
User ──→ Channel (Telegram/CLI/API) ──→ Lane Queue (serial per-user)
                                              │
                                              ▼
                                        Agent Runtime
                                     ┌────────────────┐
                                     │ SOUL.md persona │
                                     │ Memory (encrypted)│
                                     │ Smart tool filter │
                                     │ ECO cost router  │
                                     └───────┬─────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                        Skill Registry   Browser (CDP)   MCP Bridge
                        ~110 skills      Brave/Chrome    6 MCP servers
                              │              │              │
                              ▼              ▼              ▼
                        Code Sandbox    Shared Profile   External Tools
                        (AST-validated) (cookies shared) (any MCP server)
```

16 modules in `lazyclaw/`:

| Module | Purpose |
|--------|---------|
| `gateway/` | FastAPI HTTP + WebSocket entry point (19 route files) |
| `runtime/` | TAOR agent loop, context builder, tool dispatch, task runner |
| `queue/` | FIFO serial execution per user |
| `skills/` | 128 builtin skills — Instruction, Code (sandboxed), Plugin, Survival, Browser templates |
| `channels/` | Telegram native adapter + WhatsApp/Instagram/Email via MCP |
| `browser/` | CDP browser control, page reader, site memory, DOM click engine |
| `computer/` | Native subprocess + remote WebSocket connector |
| `memory/` | Encrypted facts, history, compression, daily/weekly summaries |
| `mcp/` | Native MCP client + server + skill bridge |
| `crypto/` | AES-256-GCM, PBKDF2, credential vault |
| `teams/` | Specialist delegation + parallel execution |
| `replay/` | Session trace recording + shareable tokens |
| `tasks/` | Encrypted task store, nagging reminders, recurring tasks |
| `notifications/` | Telegram push for background tasks |
| `pipeline/` | CRM-style pipeline store |
| `survival/` | Gig economy tools — job matching, applications, invoices |

Supporting: `llm/` (multi-provider router, ECO mode, Ollama, Claude CLI, Anthropic, OpenAI), `heartbeat/` (cron daemon), `permissions/` (allow/ask/deny + audit), `db/` (aiosqlite + connection pool).

| | |
|---|---|
| `web/` | React 19 + TypeScript + Vite + Tailwind control panel (12 pages + chat sidebar with live BrowserCanvas) |

## Encryption

Every piece of user content is encrypted before storage. The server never holds plaintext.

```
Registration → random salt + BIP-39 recovery phrase per user
Key derivation → PBKDF2(password, salt, 600K iterations, SHA-256) → per-user DEK
Envelope encryption → DEK stored encrypted with server master key
Storage format → enc:v1:<base64-nonce>:<base64-ciphertext>
```

**Encrypted:** conversations, memories, skills, vault credentials, scheduled jobs, channel configs, session traces.

**Plaintext** (needed for queries): IDs, timestamps, status flags, cron expressions, domain names.

**Recovery phrase:** A BIP-39 mnemonic is generated at registration. Users can re-derive their key from the phrase if they forget their password — the server never stores the plaintext key.

Server-side operations (cron jobs, background tasks) derive keys from `PBKDF2(SERVER_SECRET + user_id, fixed_salt, 600K)` — the server secret never leaves memory.

## Features

### Smart Tool Selection

128 builtin skills + ~67 MCP tools registered, but the agent sends only 4 base tools (search_tools, recall_memories, save_memory, delegate). The LLM discovers additional tools on demand via `search_tools` — no upfront schema bloat. **~95% token savings** vs sending all tool schemas every message.

### Multi-Agent Delegation

The agent calls `delegate(specialist, instruction)` inline — no separate orchestration LLM call. Three built-in specialists:

- **Browser Specialist** — web browsing, page reading, form filling
- **Research Specialist** — web search, data gathering, file access
- **Code Specialist** — code generation, skill writing, debugging

Specialists run in parallel via `asyncio.gather`. Results merge back into the conversation naturally.

### Background Tasks

`run_background` skill spawns independent agent instances for long-running work. Max 5 global, 2 per user. Results pushed to Telegram on completion.

### Task Manager (Second Brain)

Encrypted tasks with nagging reminders and due-date escalation:

- **Nag pattern** — 15min → 30min → 1hr, capped at 5 (no spam spiral)
- **Relative time parsing** — `remind me in +1h30m drink water`, parsed server-side so the LLM never does time math
- **User/agent separation** — the agent's own todos are tracked separately from yours
- **Telegram inline buttons** — Done / Snooze 1h / Tomorrow, one tap from the reminder message
- **Recurring tasks** — daily / weekly / monthly with auto-created next occurrences
- **AI enrichment** — auto-categorize on save via `mcp-taskai` (graceful degradation when the MCP is offline)

All task content (title, description, category, tags) is encrypted at rest. Only priority / status / due_date / timestamps stay plaintext for query efficiency.

### Context Compression

Long conversations don't break. A sliding window keeps the last 15 messages full, older ones get summarized. Daily auto-summaries (via gpt-5-mini) and weekly rollups keep context rich without re-summarizing on every message.

### Session Replay

Every agent action is recorded: LLM calls, tool invocations, specialist delegations, results. View full replays step-by-step. Generate shareable URL tokens with optional expiration.

### Permissions

Allow/ask/deny per skill category. Inline approval flow — agent pauses, asks user, resumes on approval. Full audit log with 90-day retention. First registered user becomes admin.

## ECO Mode

Three-mode cost routing with brain/worker model split:

| Mode | Brain | Worker | Fallback | Cost |
|------|-------|--------|----------|------|
| **HYBRID** (default) | Sonnet 4.6 | `gemma4:e2b` via Ollama ($0) | Haiku 4.5 | Low |
| **FULL** | Sonnet 4.6 | Haiku 4.5 | Sonnet 4.6 | Normal |
| **CLAUDE** | Haiku API (native tools) | Haiku API | Claude CLI ($0 via subscription) | Low |

The brain handles orchestration, workers handle simple tasks. Complexity detection uses regex heuristics (no extra LLM call). User-configurable model assignments per mode and monthly budget caps.

HYBRID mode uses any local model you run via Ollama as the worker — $0 cost for most tasks, with Haiku fallback when local fails. FULL mode uses all-Claude paid models for maximum quality. CLAUDE mode uses Haiku API with native tool calling.

**Agent Skills compatible** — skills written in Claude Code agent format (YAML + markdown) can be imported directly via `lazyclaw skill import`.

### Supported LLM providers

LazyClaw routes through a single `LLMRouter` that speaks five provider dialects. Set any one of the API keys (or install Ollama locally, or log in to the Claude CLI) and the agent will pick it up automatically.

| Provider | Models | How it bills | Good for |
|----------|--------|--------------|----------|
| **Anthropic** | Sonnet 4.6, Haiku 4.5, Opus 4.6 | Per-token API | Best tool use, best-in-class quality. **LazyClaw is optimized around Claude.** |
| **MiniMax** | MiniMax-M2.7, minimax-m2.5 | Subscription-priced (flat-rate), OpenAI-compatible API at `api.minimax.io/v1` | **Tested and works really well** as an alternative brain — 204K context, strong tool calling, predictable monthly cost. Auto-falls-back to Claude on rate-limit. |
| **OpenAI** | GPT-5, GPT-5-mini | Per-token API | Legacy fallback; kept for users with existing OpenAI keys. |
| **Ollama** (local) | Gemma 4 E2B / E4B (`lazyclaw-e2b` / `lazyclaw-e4b` custom Modelfiles with agent identity baked in) | Free (runs on your machine) | Default HYBRID worker. Great for tool-call-heavy tasks when you don't want to pay per token. |
| **Claude CLI** | `claude -p` subprocess | Free for Anthropic Max subscribers | CLAUDE-mode fallback: run the whole agent for $0 if you already pay for Max. |

**Which should I use?**
LazyClaw was built and tuned against Claude Sonnet 4.6 + Haiku 4.5 — that's the recommended default and what ECO HYBRID mode ships with. **MiniMax M2.7 has been tested as a drop-in brain replacement and works well** for the same workload at a flat monthly cost. OpenAI works but isn't the focus. Ollama is the zero-cost local worker. Claude CLI lets Max subscribers run everything through the CLI at no extra cost.

Set any of these in `.env`:

```
ANTHROPIC_API_KEY=...
MINIMAX_API_KEY=...         # optional; MINIMAX_BASE_URL defaults to api.minimax.io/v1
OPENAI_API_KEY=...          # optional legacy
```

Or install Ollama and `ollama pull` one of the bundled models. Or `claude login` for the CLI path. Any one is enough to get started.

## Browser

CDP-based control of the user's real Brave/Chrome browser. No separate Chromium instance — the agent uses your actual browser with your logins, cookies, and sessions.

- **Live BrowserCanvas** — embedded in the chat sidebar. See the URL, action timeline (click / type / goto), and a thumbnail of the current page as the agent works. **Zero extra LLM tokens** — events flow UI-only, never enter the agent's context.
- **Live mode** — one-tap toggle on the canvas. Captures a fresh screenshot after every action for 5 minutes. Use it when the agent is stuck or you just want to watch.
- **Checkpoints** — the agent calls `request_user_approval` before risky actions (submit, pay, book, delete, sign). The canvas shows an inline Approve / Reject banner; agent blocks until you decide. Same name auto-approves on re-call.
- **Saved templates** — reusable recipes for recurring flows. `Templates` page lets you save a playbook + setup URLs + checkpoints + optional zero-token slot watcher. Ships seed examples for Cita Previa Spain (DGT) and Doctoralia.
- **Slot polling** — `watch_appointment_slots` hooks a template to the watcher daemon. Zero LLM tokens per check; Telegram + canvas alert fires when slots open.
- **Remote takeover** — noVNC via the `share_browser_control` NL skill or the canvas `🎮 Take control` button. Works from Telegram, web chat, and CLI identically.
- **Shared profiles** — login once, all tools see it
- **Brave auto-detect** — Brave > Chrome > Chromium (built-in ad blocking = cleaner pages for LLM)
- **Human-like delays** — random 0.2-1.5s between clicks, 0.03-0.12s typing
- **Ref-ID snapshots** — interactive elements with click refs (~1-4KB) instead of full accessibility tree (50KB)
- **DOM click engine** — real JavaScript clicks (works with Gmail, React, Angular SPAs)
- **Site memory** — encrypted per-domain learning, auto-saved from specialist experience

## MCP

First-class MCP support — both client and server.

**As client:** Connect to any MCP server (stdio, SSE, streamable HTTP). External tools automatically registered as first-class skills. Parallel startup via `asyncio.gather` (~2s for 10 servers instead of sequential). Auto-install from Telegram via `/mcp install`.

**As server:** Expose LazyClaw tools to any MCP-compatible client via SSE.

**Remote MCP with OAuth:** The agent can connect to OAuth-protected remote MCP servers (Canva, GitHub, Slack, Google Drive, Gmail) via a single natural-language command. Say *"connect to Canva"* → LazyClaw opens Brave for the OAuth login → catches the callback on localhost → stores tokens encrypted in the vault → the remote server's tools become first-class agent skills. Auto-refreshes on expiry without re-prompting.

**Bundled MCP servers (6 active):**

| Server | Purpose |
|--------|---------|
| `mcp-taskai` | Task intelligence — categorize, prioritize, detect duplicates |
| `mcp-lazydoctor` | Self-healing — lint, typecheck, test, auto-fix |
| `mcp-instagram` | Instagram DMs, feed, posting via private mobile API. No browser needed. |
| `mcp-whatsapp` | WhatsApp messaging via web protocol. QR auth, no API needed. |
| `mcp-email` | Send/read/search email via SMTP+IMAP. Gmail, Outlook, any provider. |
| `mcp-jobspy` | Job search across Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google. |

**Coming soon (disabled, rebuild in progress):** `mcp-freeride` (free AI router), `mcp-healthcheck` (provider monitor), `mcp-apihunter` (API discovery), `mcp-vaultwhisper` (PII proxy).

## Integrations

### n8n — Full Workflow Automation (Native)

> **LazyClaw + n8n = AI agent that creates, edits, and manages automation workflows by voice.**

Deep native integration — not just a connection. The agent has 6 management skills for full workflow CRUD:

- **Create workflows** from natural language ("automate daily sales report to Slack")
- **Edit existing workflows** — add/remove nodes, change triggers, update credentials
- **Delete, activate, deactivate** workflows on command
- **Templates** — pre-built workflow patterns the agent can deploy instantly
- **Docker sidecar** — n8n runs alongside LazyClaw via `n8n-custom/`

```
You: create a workflow that checks my email every hour and sends a summary to Telegram
Bot: ✅ Created workflow "Email Hourly Summary" — trigger: every 60min, nodes: Gmail → summarize → Telegram
```

This is a first-class integration with 6 dedicated skills, not a generic MCP passthrough.

---

## Telegram

Send a message on Telegram, get AI responses back with full tool calling. Admin chat lock (first `/start` claims the bot). Screenshots auto-forwarded. Retry with exponential backoff.

```
You: check my WhatsApp messages
Bot: ⏳ On it...
Bot: [reads WhatsApp via CDP, extracts messages]
Bot: You have 3 unread messages from Alex, Mom, and the team group...
     ─────────
     ✅ 8.2s │ 2 LLM │ 1,847 tokens
```

While the agent works, type `/status` or "what's happening" to see live progress.

## Web UI

React 19 + TypeScript + Vite + Tailwind control panel with 12 pages, a persistent chat sidebar with live BrowserCanvas, and real-time WebSocket streaming:

- **Overview** — System dashboard with health stats and pending approvals
- **Activity** — Live agent and task monitor (active, background, recent)
- **Replay** — Session trace playback and debugging
- **Audit** — Action log with filtering and security review
- **Skill Hub** — Discover and install skills
- **Skills** — Browse, create, edit, delete skills
- **Templates** — Saved browser recipes (DGT cita previa, Doctoralia, custom) with one-click Run / Watch / Seed
- **Jobs** — Cron job management (create, pause, resume)
- **MCP** — Server management (connect, disconnect, install)
- **Memory** — Personal memories + daily logs
- **Vault** — Encrypted credential management
- **Settings** — ECO mode, model config, team settings, permissions
- **Chat Sidebar** — Persistent agent conversation with WebSocket streaming, markdown rendering, tool call visualization, and live BrowserCanvas showing URL + action timeline + thumbnail + Refresh / Live / Help / Take control buttons (available on every page)

```bash
cd web && npm install && npm run dev   # Development (port 5173)
cd web && npm run build                # Production build
```

Dark theme, mobile responsive. WebSocket chat with token-by-token streaming, tool call indicators, and specialist delegation tracking. Connects to the same gateway API as Telegram and CLI.

## CLI

Interactive REPL with rich formatting, history, and 30+ slash commands:

```
lazyclaw              # Chat REPL
lazyclaw setup        # First-time wizard
lazyclaw start        # Full server with TUI dashboard
```

Type while the agent works — messages get queued. Double Ctrl+C for force quit. `/help` for all commands.

## Performance

| Optimization | Impact |
|-------------|--------|
| PBKDF2 LRU cache | 420ms → 0ms per message (4+ derivations/msg) |
| DB connection pool | 14ms → 0.2ms per query |
| search_tools meta-tool | ~95% token reduction (4 tools upfront vs 120) |
| Ref-ID browser snapshots | 90-95% reduction on browser output |
| Tool result pruning | Old results compressed to 150 chars |
| Fast chat path | Simple messages skip full context build |
| Layered summaries | Skip 90s LLM re-summarization |
| Lazy MCP loading | 0 subprocesses at boot, connect on first use |
| MCP idle timeout | Auto-disconnect after 5 min inactivity |
| Brain/Worker routing | Sonnet brain + local Ollama workers for simple tasks |
| Prompt caching | Static prefix first for max cache hits |

## Roadmap

- [x] Phases 1-10: Foundation through Session Replay
- [x] 6 bundled MCP servers (taskai, lazydoctor, instagram, whatsapp, email, jobspy)
- [ ] 4 MCP servers in progress (freeride, healthcheck, apihunter, vaultwhisper — source rebuild needed)
- [x] ECO mode — HYBRID (Sonnet brain + local Ollama worker), FULL (all-Claude), CLAUDE (Haiku API)
- [x] Multi-agent teams with inline delegation
- [x] Browser automation (CDP + shared profiles + DOM click engine)
- [x] ~195 skills discoverable at runtime (128 builtin + ~67 MCP)
- [x] React Web UI control panel (12 pages + chat sidebar + live BrowserCanvas) + WebSocket streaming
- [x] Live browser canvas — URL + action timeline + thumbnail + takeover (zero LLM tokens)
- [x] Saved browser templates (govt appointments, recurring flows) with zero-token slot polling
- [x] Checkpoints — agent pauses for user approval before submit/pay/book/delete/sign/send
- [x] MiniMax provider integration (subscription-priced, OpenAI-compatible, tested as a Claude alternative brain)
- [x] Remote MCP OAuth flow (Canva, GitHub, Google — auto browser login, encrypted token storage, auto-refresh)
- [x] Task Manager (second brain) with encrypted storage + nag escalation + Telegram inline buttons
- [x] Instagram, WhatsApp, Email MCP servers (no browser needed)
- [x] WhatsApp mute from Telegram (reply "mute")
- [x] MCP auto-install from Telegram (/mcp install)
- [x] Brain/Worker model routing (Sonnet + Haiku)
- [x] Ref-ID browser snapshots (95% token reduction)
- [x] Lazy MCP loading with favorites + idle timeout
- [x] Survival mode (job hunting, browser automation)
- [x] TAOR loop with parallel tool execution
- [x] Per-user DEK with envelope encryption (600K PBKDF2 iterations)
- [x] BIP-39 recovery phrase at registration
- [x] Agent Skills compatibility (import Claude Code skills)
- [x] n8n native integration (6 skills — full workflow CRUD, templates, Docker sidecar)
- [x] WebSocket streaming (`/ws/chat`) for real-time Web UI chat
- [ ] Skill Hub — universal skill/MCP registry (cross-framework, works with OpenClaw and others)
- [ ] More channels (Discord, Signal, SimpleX)
- [x] Docker + Docker Compose (Dockerfile, docker-compose.yml, web/Dockerfile)
- [ ] LazyTasker mobile app integration
- [ ] Post-quantum key exchange (ML-KEM)

> **Actively maintained** — this project ships daily updates and improvements. Star the repo to follow along.

See [TODO.md](TODO.md) for the full phase plan.

## Project Structure

```
lazyclaw/
├── gateway/        # FastAPI HTTP + WS (19 route files)
├── runtime/        # TAOR agent loop, context, tool dispatch, task runner
├── queue/          # Lane-based FIFO queue
├── skills/         # 128 builtin skills — Instruction, Code, Plugin, Survival, Templates
├── channels/       # Telegram adapter
├── browser/        # CDP control + event bus + checkpoints + saved templates
├── computer/       # Native subprocess + connector
├── memory/         # Encrypted facts + compression
├── mcp/            # MCP client + server + bridge
├── crypto/         # AES-256-GCM + vault
├── teams/          # Specialist delegation
├── replay/         # Session traces
├── tasks/          # Encrypted task store + reminders
├── notifications/  # Telegram push notifications
├── pipeline/       # CRM pipeline store
├── survival/       # Gig economy tools
├── heartbeat/      # Cron daemon (watchers → canvas alert + Telegram push)
├── permissions/    # Allow/ask/deny + audit
├── llm/            # Multi-provider router + ECO (Gemma 4 E2B worker)
└── db/             # aiosqlite + connection pool

web/                # React 19 control panel (12 pages + chat sidebar + live BrowserCanvas)
n8n-custom/         # n8n Docker sidecar config
mcp-taskai/         # Task intelligence
mcp-lazydoctor/     # Self-healing agent
mcp-instagram/      # Instagram DMs, feed, posting (20 tools)
mcp-whatsapp/       # WhatsApp messaging + mute (12 tools)
mcp-email/          # Email via SMTP+IMAP (11 tools)
mcp-jobspy/         # Job search aggregation
mcp-freeride/       # Free AI router (disabled — rebuild in progress)
mcp-healthcheck/    # Provider health monitor (disabled — rebuild in progress)
mcp-apihunter/      # API discovery engine (disabled — rebuild in progress)
mcp-vaultwhisper/   # PII privacy proxy (disabled — rebuild in progress)
```

## Contributing & Feedback

LazyClaw is in early beta — built by a solo developer, shipped daily. Bugs are expected. Your feedback makes it better.

**Found a bug?** Open a [GitHub Issue](../../issues) — include steps to reproduce and any error logs.

**Have an idea?** Start a [GitHub Discussion](../../discussions) — feature requests, integration ideas, or just say hi.

**Want to contribute?** PRs welcome. Pick any open issue or suggest your own improvement.

```bash
# Install in dev mode
pip install -e ".[all]"

# Run
lazyclaw setup
lazyclaw
```

## Status

Early beta (v0.1). Core features work. Actively maintained with daily updates. Encryption is solid, UI is functional, some edge cases still being ironed out. Star the repo to track progress.

## License

[MIT](LICENSE)
