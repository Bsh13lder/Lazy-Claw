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
  <a href="#telegram">Telegram</a> &bull;
  <a href="#roadmap">Roadmap</a>
</p>

---

**LazyClaw** is an open-source AI agent platform where every piece of user data is encrypted with AES-256-GCM before it touches disk. Conversations, memories, skills, credentials, scheduled jobs — all encrypted. The server never sees plaintext.

Built in Python. Native MCP. Multi-agent delegation. Cost-aware routing. Browser automation via CDP. Telegram + WhatsApp + Instagram + Email. 101 skills discoverable at runtime. Web UI included.

## Why LazyClaw?

Most AI agent platforms store everything in plaintext. Your conversations, API keys, browsing history, personal memories — sitting unencrypted on disk or in a database. [42,000 exposed instances](https://www.bitsight.com/blog/openclaw-ai-security-risks-exposed-instances) of the leading platform were found with no authentication and full data access.

LazyClaw takes a different approach:

| | LazyClaw | Others |
|---|---|---|
| **User data** | AES-256-GCM encrypted at rest | Plaintext files / DB |
| **API keys** | Encrypted credential vault | `.env` plaintext |
| **Conversations** | Encrypted per-user | Plaintext JSONL |
| **Memories** | Encrypted personal facts | Plaintext markdown |
| **Tool selection** | Smart discovery via search_tools (~400 tokens) | All tools every turn (5K+ tokens) |
| **Cost routing** | Brain/Worker model split (Sonnet + Haiku) | Manual model config |
| **Multi-agent** | Inline delegation to specialists | Fire-and-forget sub-agents |
| **MCP** | Native client + server + 10 bundled servers | Community plugins |
| **Channels** | Telegram + WhatsApp + Instagram + Email MCPs | Browser-only for most |
| **Web UI** | React control panel (8 pages) | Varies |
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

Requires: Python 3.11+, pipx, an AI provider key (OpenAI or Anthropic).
</details>

**Requirements:** Python 3.11+ (installed automatically on macOS), an AI provider key (OpenAI or Anthropic).

> **For best results, use Anthropic.** Claude Haiku 4.5 as default + Sonnet 4.6 for complex tasks gives the fastest responses (2-5s) with excellent tool use. LazyClaw auto-configures the optimal model routing when it detects an Anthropic API key.

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
                        101 skills       Brave/Chrome    10 MCP servers
                              │              │              │
                              ▼              ▼              ▼
                        Code Sandbox    Shared Profile   External Tools
                        (AST-validated) (cookies shared) (any MCP server)
```

12 core modules:

| Module | Purpose |
|--------|---------|
| `gateway/` | FastAPI HTTP + WebSocket entry point |
| `runtime/` | Agent loop, Team Lead, tool dispatch, context builder |
| `queue/` | FIFO serial execution per user |
| `skills/` | 101 skills — Instruction, Code (sandboxed), Plugin |
| `channels/` | Telegram adapter + WhatsApp/Instagram/Email via MCP |
| `browser/` | CDP browser control, page reader, site memory |
| `computer/` | Native subprocess + remote WebSocket connector |
| `memory/` | Encrypted facts, history, compression |
| `mcp/` | Native MCP client + server + skill bridge |
| `crypto/` | AES-256-GCM, PBKDF2, credential vault |
| `teams/` | Specialist delegation + parallel execution |
| `replay/` | Session trace recording + shareable tokens |

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

101 registered skills, but the agent sends only 4 base tools (search_tools, recall_memories, save_memory, delegate). The LLM discovers additional tools on demand via `search_tools` — no upfront schema bloat. **~95% token savings** vs sending all tool schemas every message.

### Multi-Agent Delegation

The agent calls `delegate(specialist, instruction)` inline — no separate orchestration LLM call. Three built-in specialists:

- **Browser Specialist** — web browsing, page reading, form filling
- **Research Specialist** — web search, data gathering, file access
- **Code Specialist** — code generation, skill writing, debugging

Specialists run in parallel via `asyncio.gather`. Results merge back into the conversation naturally.

### Background Tasks

`run_background` skill spawns independent agent instances for long-running work. Max 5 global, 2 per user. Results pushed to Telegram on completion.

### Context Compression

Long conversations don't break. A sliding window keeps the last 15 messages full, older ones get summarized. Daily auto-summaries (via gpt-5-mini) and weekly rollups keep context rich without re-summarizing on every message.

### Session Replay

Every agent action is recorded: LLM calls, tool invocations, specialist delegations, results. View full replays step-by-step. Generate shareable URL tokens with optional expiration.

### Permissions

Allow/ask/deny per skill category. Inline approval flow — agent pauses, asks user, resumes on approval. Full audit log with 90-day retention. First registered user becomes admin.

## ECO Mode

Two-tier cost routing with brain/worker model split:

| Mode | Brain | Worker | Fallback | Cost |
|------|-------|--------|----------|------|
| **HYBRID** (default) | Haiku 4.5 | Nanbeige 3B (Ollama MLX, $0) | Haiku 4.5 | Low |
| **FULL** | Sonnet 4.6 | Haiku 4.5 | Opus | Normal |

The brain handles orchestration, workers handle simple tasks. Complexity detection uses regex heuristics (no extra LLM call). User-configurable model assignments and monthly budget caps.

Also supports **Claude CLI mode** — route all LLM calls through `claude -p` for $0 cost (covered by Claude Code subscription).

**Agent Skills compatible** — skills written in Claude Code agent format (YAML + markdown) can be imported directly via `lazyclaw skill import`.

## Browser

CDP-based control of the user's real Brave/Chrome browser. No separate Chromium instance — the agent uses your actual browser with your logins, cookies, and sessions.

- **Shared profiles** — login once, all tools see it
- **Brave auto-detect** — Brave > Chrome > Chromium (built-in ad blocking = cleaner pages for LLM)
- **Human-like delays** — random 0.2-1.5s between clicks, 0.03-0.12s typing
- **Ref-ID snapshots** — interactive elements with click refs (~1-4KB) instead of full accessibility tree (50KB)
- **Orchestrated reading** — extractors for content understanding + snapshots for interaction
- **DOM click engine** — real JavaScript clicks (works with Gmail, React, Angular SPAs)
- **Site memory** — encrypted per-domain learning, auto-saved from specialist experience
- **Remote takeover** — noVNC link sent to Telegram for server-mode browser auth

## MCP

First-class MCP support — both client and server.

**As client:** Connect to any MCP server (stdio, SSE, streamable HTTP). External tools automatically registered as first-class skills. Parallel startup via `asyncio.gather` (~2s for 10 servers instead of sequential). Auto-install from Telegram via `/mcp install`. Works with n8n MCP Server Trigger — expose n8n workflows as agent tools ([integration guide](docs/integrations/n8n.md)).

**As server:** Expose LazyClaw tools to any MCP-compatible client via SSE.

**Bundled MCP servers:**

| Server | Purpose |
|--------|---------|
| `mcp-freeride` | Free AI router (Groq, Gemini, Ollama, OpenRouter, Together, Mistral, HuggingFace) |
| `mcp-healthcheck` | Background pinger for all AI sources, latency ranking |
| `mcp-apihunter` | Community-driven free API discovery + auto-scanner |
| `mcp-vaultwhisper` | Privacy proxy — strips PII before sending to free APIs |
| `mcp-taskai` | Task intelligence — categorize, prioritize, detect duplicates |
| `mcp-lazydoctor` | Self-healing — lint, typecheck, test, auto-fix |
| `mcp-instagram` | Instagram DMs, feed, posting via private mobile API. No browser needed. |
| `mcp-whatsapp` | WhatsApp messaging via web protocol. QR auth, no API needed. |
| `mcp-email` | Send/read/search email via SMTP+IMAP. Gmail, Outlook, any provider. |
| `mcp-jobspy` | Job search across Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google. |

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

React control panel at `localhost:3000` with 8 pages:

- **Chat** — Full agent conversation with markdown rendering
- **Overview** — System dashboard
- **Skills** — Browse, create, edit, delete skills
- **Jobs** — Cron job management (create, pause, resume)
- **MCP** — Server management (connect, disconnect, install)
- **Memory** — Personal memories + daily logs
- **Vault** — Encrypted credential management
- **Settings** — ECO mode, model config, system settings

```bash
cd web && npm install && npm run dev   # Development
cd web && npm run build                # Production build
```

Dark theme, mobile responsive. Connects to the same gateway API as Telegram and CLI.

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
| search_tools meta-tool | ~95% token reduction (4 tools upfront vs 101) |
| Ref-ID browser snapshots | 90-95% reduction on browser output |
| Tool result pruning | Old results compressed to 150 chars |
| Fast chat path | Simple messages skip full context build |
| Layered summaries | Skip 90s LLM re-summarization |
| Lazy MCP loading | 0 subprocesses at boot, connect on first use |
| MCP idle timeout | Auto-disconnect after 5 min inactivity |
| Brain/Worker routing | Haiku for 90% of tasks, Sonnet for complex only |
| Prompt caching | Static prefix first for max cache hits |

## Roadmap

- [x] Phases 1-10: Foundation through Session Replay
- [x] 10 bundled MCP servers (freeride, healthcheck, apihunter, vaultwhisper, taskai, lazydoctor, instagram, whatsapp, email, jobspy)
- [x] ECO mode — HYBRID (Haiku brain + Nanbeige worker) and FULL (user-settable)
- [x] Multi-agent teams with inline delegation
- [x] Browser automation (CDP + shared profiles + DOM click engine)
- [x] 101 skills discoverable at runtime
- [x] React Web UI control panel (8 pages)
- [x] Claude CLI provider ($0 routing via claude -p)
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
- [x] n8n integration via MCP Server Trigger
- [ ] WebSocket streaming for real-time Web UI chat
- [ ] Skill Hub — universal skill/MCP registry (cross-framework)
- [ ] More channels (Discord, Signal, SimpleX)
- [ ] Docker Compose (one-command deploy)
- [ ] LazyTasker mobile app integration
- [ ] Post-quantum key exchange (ML-KEM)

See [TODO.md](TODO.md) for the full phase plan.

## Project Structure

```
lazyclaw/
├── gateway/        # FastAPI HTTP + WS (60+ endpoints)
├── runtime/        # Agent loop, context, tools
├── queue/          # Lane-based FIFO queue
├── skills/         # 101 skills — Instruction, Code, Plugin
├── channels/       # Telegram adapter
├── browser/        # CDP browser control
├── computer/       # Native subprocess + connector
├── memory/         # Encrypted facts + compression
├── mcp/            # MCP client + server + bridge
├── crypto/         # AES-256-GCM + vault
├── teams/          # Specialist delegation
├── replay/         # Session traces
├── heartbeat/      # Cron daemon
├── permissions/    # Allow/ask/deny + audit
├── llm/            # Multi-provider router + ECO
└── db/             # aiosqlite + connection pool

web/                # React control panel (8 pages)
mcp-freeride/       # Free AI router
mcp-healthcheck/    # Provider health monitor
mcp-apihunter/      # API discovery engine
mcp-vaultwhisper/   # PII privacy proxy
mcp-taskai/         # Task intelligence
mcp-lazydoctor/     # Self-healing agent
mcp-instagram/      # Instagram DMs, feed, posting (20 tools)
mcp-whatsapp/       # WhatsApp messaging + mute (12 tools)
mcp-email/          # Email via SMTP+IMAP (11 tools)
mcp-jobspy/         # Job search aggregation
```

## Contributing

LazyClaw is MIT licensed. Contributions welcome.

```bash
# Install in dev mode
pip install -e ".[all]"

# Run
lazyclaw setup
lazyclaw
```

## License

[MIT](LICENSE)
