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

Built in Python. Native MCP. Multi-agent delegation. Cost-aware routing. Browser automation via CDP. Telegram integration. 72+ skills discoverable at runtime.

## Why LazyClaw?

Most AI agent platforms store everything in plaintext. Your conversations, API keys, browsing history, personal memories — sitting unencrypted on disk or in a database. [42,000 exposed instances](https://www.bitsight.com/blog/openclaw-ai-security-risks-exposed-instances) of the leading platform were found with no authentication and full data access.

LazyClaw takes a different approach:

| | LazyClaw | Others |
|---|---|---|
| **User data** | AES-256-GCM encrypted at rest | Plaintext files / DB |
| **API keys** | Encrypted credential vault | `.env` plaintext |
| **Conversations** | Encrypted per-user | Plaintext JSONL |
| **Memories** | Encrypted personal facts | Plaintext markdown |
| **Tool selection** | Per-message filtering (8-17 of 72+ tools) | All tools every turn |
| **Cost routing** | Automatic complexity detection | Manual model config |
| **Multi-agent** | Inline delegation to specialists | Fire-and-forget sub-agents |
| **MCP** | Native client + server | Community plugins |
| **Language** | Python (largest AI ecosystem) | TypeScript |

## Quickstart

```bash
# Clone
git clone https://github.com/Bsh13lder/Lazy-Claw.git
cd Lazy-Claw

# Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Setup (interactive wizard — configures AI provider, encryption, Telegram)
lazyclaw setup

# Chat (CLI REPL)
lazyclaw

# Full server (API + Telegram + Heartbeat + Dashboard)
lazyclaw start
```

**Requirements:** Python 3.11+, an AI provider key (OpenAI or Anthropic).

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
                        72+ skills       Brave/Chrome    6 MCP servers
                              │              │              │
                              ▼              ▼              ▼
                        Code Sandbox    Shared Profile   External Tools
                        (AST-validated) (cookies shared) (any MCP server)
```

12 core modules:

| Module | Purpose |
|--------|---------|
| `gateway/` | FastAPI HTTP + WebSocket entry point |
| `runtime/` | Agent loop, tool dispatch, context builder |
| `queue/` | FIFO serial execution per user |
| `skills/` | Instruction, Code (sandboxed), Plugin skills |
| `channels/` | Telegram adapter (Discord, WhatsApp planned) |
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
Registration → random salt per user
Key derivation → PBKDF2(password, salt, 100K iterations, SHA-256) → AES-256 key
Storage format → enc:v1:<base64-nonce>:<base64-ciphertext>
```

**Encrypted:** conversations, memories, skills, vault credentials, scheduled jobs, channel configs, session traces.

**Plaintext** (needed for queries): IDs, timestamps, status flags, cron expressions, domain names.

Server-side operations (cron jobs, background tasks) derive keys from `PBKDF2(SERVER_SECRET + user_id, fixed_salt)` — the server secret never leaves memory.

## Features

### Smart Tool Selection

The agent doesn't send all 72+ tools to the LLM every message. Per-message category detection identifies what's relevant (browser, computer, skills, vault, jobs, admin) and sends only 8-17 tools. **70-88% token savings** per request.

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

Three-tier cost routing — the agent automatically picks the right model per message:

| Mode | Behavior | Cost |
|------|----------|------|
| **ECO** | Free providers only (Groq, Gemini, Ollama). Never touches paid. | $0 |
| **HYBRID** | Simple tasks → free, complex → paid. Automatic per-message. | Low |
| **FULL** | Always paid. Maximum quality. | Normal |

Complexity detection uses regex heuristics (no extra LLM call). Default: `gpt-5-mini` for everything, `gpt-5` only for analyze/compare/debug tasks. **~80% cost reduction** vs always-paid.

User-configurable provider pools, per-task AI assignment, and monthly budget caps.

## Browser

CDP-based control of the user's real Brave/Chrome browser. No separate Chromium instance — the agent uses your actual browser with your logins, cookies, and sessions.

- **Shared profiles** — login once, all tools see it
- **Brave auto-detect** — Brave > Chrome > Chromium (built-in ad blocking = cleaner pages for LLM)
- **Human-like delays** — random 0.2-1.5s between clicks, 0.03-0.12s typing
- **Semantic snapshots** — accessibility tree text (50KB) instead of screenshots (5MB)
- **Page reader** — JS extractors for WhatsApp, Gmail, and generic sites
- **Site memory** — encrypted per-domain learning with auto-cleanup

## MCP

First-class MCP support — both client and server.

**As client:** Connect to any MCP server (stdio, SSE, streamable HTTP). External tools automatically registered as first-class skills. Parallel startup via `asyncio.gather` (~2s for 6 servers instead of 12s sequential).

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
| Smart tool filter | 70-88% token reduction per request |
| Fast chat path | Simple messages skip full context build |
| Layered summaries | Skip 90s LLM re-summarization |
| MCP parallel startup | 12s → ~2s for 6 servers |
| Cost-aware routing | ~80% cost reduction (gpt-5-mini default) |

## Roadmap

- [x] Phases 1-10: Foundation through Session Replay
- [x] MCP ecosystem (6 servers)
- [x] ECO mode with complexity routing
- [x] Multi-agent teams with inline delegation
- [x] Browser automation (CDP + shared profiles)
- [x] 72+ natural language skills
- [ ] More channels (Discord, WhatsApp, Signal)
- [ ] Docker Compose (one-command deploy)
- [ ] Flutter mobile app
- [ ] Plugin system (LazyTasker integration)
- [ ] Workflow builder UI
- [ ] Post-quantum key exchange (ML-KEM)

See [TODO.md](TODO.md) for the full phase plan.

## Project Structure

```
lazyclaw/
├── gateway/        # FastAPI HTTP + WS
├── runtime/        # Agent loop, context, tools
├── queue/          # Lane-based FIFO queue
├── skills/         # Skill system + 72+ builtins
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

mcp-freeride/       # Free AI router MCP server
mcp-healthcheck/    # Provider health monitor
mcp-apihunter/      # API discovery engine
mcp-vaultwhisper/   # PII privacy proxy
mcp-taskai/         # Task intelligence
mcp-lazydoctor/     # Self-healing agent
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
