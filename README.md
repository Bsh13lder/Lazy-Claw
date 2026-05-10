<p align="center">
  <img src="docs/images/logo-brutalist.png" alt="LazyClaw" width="320" />
</p>

<h1 align="center">LazyClaw</h1>

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
  <a href="#parallel-agents">Parallel Agents</a> &bull;
  <a href="#goal-executor">Goal Executor</a> &bull;
  <a href="#browser">Browser</a> &bull;
  <a href="#mcp">MCP</a> &bull;
  <a href="#integrations">Integrations</a> &bull;
  <a href="#telegram">Telegram</a> &bull;
  <a href="#roadmap">Roadmap</a>
</p>

---

**LazyClaw** is an open-source AI agent platform where every piece of user data is encrypted with AES-256-GCM before it touches disk. Conversations, memories, skills, credentials, scheduled jobs — all encrypted. The server never sees plaintext.

Built in Python. Native MCP. **Parallel multi-agent runtime** — 3 in-turn specialists (Browser / Code / Research), fire-and-forget subagent dispatch (Explore / General-Purpose / Specialist), background tasks with auto-promote, and an optional adversarial Critic for HIGH/MAX-effort turns. Cost-aware brain/worker routing across 5 providers. Browser automation via CDP with a **live canvas, checkpoints, saved templates for recurring flows, multi-account profile isolation, per-domain human-cadence tuning, auto-close for idle tabs, and Apple Vision OCR**. **Goal Executor** for autonomous high-level objectives — drafts a plan, batch-asks every required answer upfront, then dispatches to the browser specialist. **Authenticated Bug Bounty toolkit** (login → probe → hunt over a forked claude-bug-bounty engine). Telegram + WhatsApp + Instagram + Email — text or **voice** (whisper.cpp on-device, 99 languages). **~206 builtin skills + ~85 MCP-bridged tools** discoverable at runtime. React Web UI with **19 pages** (incl. dedicated `/chat` page with collapsible AgentConsole dashboard, plus a Code Specialist live-run page) + persistent chat sidebar.

## Why LazyClaw?

Most AI agent platforms store everything in plaintext. Your conversations, API keys, browsing history, personal memories — sitting unencrypted on disk or in a database. [42,000 exposed instances](https://www.bitsight.com/blog/openclaw-ai-security-risks-exposed-instances) of the leading platform were found with no authentication and full data access.

LazyClaw takes a different approach:

| | LazyClaw | Others |
|---|---|---|
| **User data** | AES-256-GCM encrypted at rest | Plaintext files / DB |
| **API keys** | Encrypted credential vault | `.env` plaintext |
| **Conversations** | Encrypted per-user | Plaintext JSONL |
| **Memories** | Encrypted personal facts | Plaintext markdown |
| **Second brain** | Built-in Logseq-style PKM — encrypted notes, wikilinks, backlinks, journal, graph. Agent and user share one store. | Separate app, plaintext |
| **Tool selection** | Smart discovery via search_tools (4 base tools, ~195 discoverable) | All tools every turn (5K+ tokens) |
| **Cost routing** | 3-mode Brain/Worker split across 5 providers (Anthropic · MiniMax subscription · OpenAI · local Ollama · Claude CLI) | Manual model config |
| **Multi-agent** | Inline delegation to specialists | Fire-and-forget sub-agents |
| **MCP** | Native client + server + 8 active bundled servers | Community plugins |
| **Integrations** | Google Workspace direct API (Gmail/Drive/Calendar/Sheets/Docs) + n8n for visual workflows | Manual API wiring |
| **Channels** | Telegram + WhatsApp + Instagram + Email MCPs | Browser-only for most |
| **Browser control** | Live canvas + checkpoints + saved templates + noVNC takeover (zero extra tokens) | Screenshots or nothing |
| **Web UI** | React control panel (18 pages + chat sidebar + live BrowserCanvas) + WebSocket streaming | Varies |
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

### Docker on macOS — one extra step for browser tasks

If you run LazyClaw via `docker compose`, the agent can drive your **real Mac Brave** (with all your cookies, logins, and Cloudflare clearance) instead of a fresh containerised Chromium. Set it up once:

```bash
make host-bridge       # installs ~/Library/LaunchAgents/sh.lazyclaw.brave-bridge.plist
make rebuild           # container picks up the shared CDP token from .env
```

That installs a launchd agent that auto-launches Brave with `--remote-debugging-port=9222` on every login (and restarts on crash). After that, anytime you say "use my browser" / "work on my visible browser" in chat, the agent connects to your real Brave — you watch the page change in your normal window, no noVNC needed. Sites like Upwork, Reddit, Gmail see your live session, not a bot.

Ops:
```bash
make host-bridge-status     # is it installed + reachable?
make host-bridge-restart    # kick the launchd-managed Brave
make host-bridge-uninstall  # remove the plist + clean .env
```

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
                        128 skills       Brave/Chrome    6 MCP servers
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
| `skills/` | 196 builtin skills — Instruction, Code (sandboxed), Plugin, Survival, Browser templates |
| `channels/` | Telegram native adapter + WhatsApp/Instagram/Email via MCP |
| `browser/` | CDP browser control, page reader, site memory, DOM click engine |
| `computer/` | Native subprocess + remote WebSocket connector |
| `memory/` | Encrypted facts, history, compression, daily/weekly summaries |
| `lazybrain/` | Python-native Obsidian-grade PKM — encrypted notes, `[[wikilinks]]`, backlinks, force-directed graph, daily journal, **callouts** (`> [!info]`), **transclusion** (`![[note]]`), **YAML frontmatter** panel, and a **spatial canvas** (React Flow). **AI-native**: autolink suggestions, auto-tag/title, semantic search via local embeddings (`nomic-embed-text`), "Ask your notes" RAG with `[[citations]]`, topic rollups, morning briefings. 28 NL skills + 17 REST endpoints. ⌘K command palette, ⌘O quick switcher, Obsidian-Minimal-inspired violet theme. |
| `mcp/` | Native MCP client + server + skill bridge |
| `crypto/` | AES-256-GCM, PBKDF2, credential vault |
| `teams/` | Specialist delegation + parallel execution |
| `replay/` | Session trace recording + shareable tokens |
| `tasks/` | Encrypted task store, nagging reminders, recurring tasks |
| `notifications/` | Telegram push for background tasks |
| `pipeline/` | CRM-style pipeline store |
| `survival/` | Gig economy tools — JobSpy search, proposal drafter, freelance platform watchers (Upwork/Workana/PeoplePerHour/Reddit), invoices |

Supporting: `llm/` (multi-provider router, ECO mode, Ollama, Claude CLI, Anthropic, OpenAI), `heartbeat/` (cron daemon), `permissions/` (allow/ask/deny + audit), `db/` (aiosqlite + connection pool).

| | |
|---|---|
| `web/` | React 19 + TypeScript + Vite + Tailwind control panel (18 pages + chat sidebar with live BrowserCanvas) |

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

196 builtin skills + ~85 MCP-bridged tools registered, but the agent sends only 4 base tools (search_tools, recall_memories, save_memory, delegate). The LLM discovers additional tools on demand via `search_tools` — no upfront schema bloat. **~95% token savings** vs sending all tool schemas every message.

### Multi-Agent Delegation

LazyClaw runs four parallel-agent surfaces in one runtime — `delegate` for in-turn specialists, `dispatch_subagents` for fire-and-forget fan-out, `run_background` for one long-running worker, and an optional **Critic** that adversarially reviews replies on HIGH/MAX-effort turns. Concurrency capped at 4 in-flight subagents (override via `LAZYCLAW_DISPATCH_CONCURRENCY`) so per-account LLM rate limits never trigger 429 bursts. Full breakdown of titles, strengths, and when to reach for each one in the **[Parallel Agents](#parallel-agents)** section below.

### Background Tasks

`run_background` skill spawns independent agent instances for long-running work. Max 10 global, 10 per user. Results pushed to Telegram on completion. **Auto-promote**: when a foreground turn exceeds its budget the runtime promotes the work to a background runner so the chat lane stays responsive — the user gets a Telegram push when the bg agent finishes. Background tasks carry a free-text **`project_tag`** (e.g. `upwork:job-X`, `gig:Y`, `reddit:dm`) so the new Code Specialist Web UI page can group active runs by business workflow.

### Critic (HIGH/MAX effort)

Optional adversarial review pass — runs only when effort is HIGH/MAX and `critic_mode` is on (Settings → Teams). Loops a critic LLM up to 3 times against the assistant's draft reply; PASS ships as-is, FAIL has the brain rewrite text-only with `[CRITIC FEEDBACK: …]` and re-checks. **Fails open** on parse / rate-limit / auth errors — a flaky critic can never block a reply that might already be fine. Critic model is user-pickable per Team settings (any catalog id or `claude-cli`).

### Task Manager (Second Brain)

Encrypted tasks with nagging reminders and due-date escalation:

- **AI quick-add** — type `"pay electricity bill wednesday 3pm"` in the Tasks page input → LLM extracts title, due date, category, priority in one pass; single click to confirm.
- **Smart intake suggester** — when you add a task without a deadline, a worker LLM (3s hard timeout, graceful Ollama-down fallback) suggests a deadline + project (category) based on the title and your recent task buckets. Confident suggestions auto-fill the reminder; uncertain time-sensitive ones return a clarification prompt instead of guessing.
- **Live countdown** — every task card ticks the time-remaining label in real time (`in 2h 14m` → `in 2h 13m`) without re-fetching.
- **Markdown notes** — rich description field renders `[[wikilinks]]` into LazyBrain, checkboxes, code blocks, and links inline on the card.
- **Nag pattern** — 15min → 30min → 1hr, capped at 5 (no spam spiral)
- **Relative time parsing** — `remind me in +1h30m drink water`, parsed server-side so the LLM never does time math
- **User/agent separation** — the agent's own todos are tracked separately from yours
- **Telegram inline buttons** — Done / Snooze 1h / Tomorrow, one tap from the reminder message
- **Recurring tasks** — daily / weekly / monthly with auto-created next occurrences
- **AI enrichment** — auto-categorize on save via `mcp-taskai` (graceful degradation when the MCP is offline)

All task content (title, description, category, tags) is encrypted at rest. Only priority / status / due_date / timestamps stay plaintext for query efficiency.

### LazyBrain — Obsidian-grade PKM

A Python-native, E2E-encrypted knowledge base the user and the agent share. Open at `/lazybrain`:

- **Core Logseq surface** — `[[wikilinks]]`, `#tags`, backlinks panel, force-directed graph, daily journal with auto-naming, **21 NL skills** for notes / journal / graph / tags / pinning.
- **Obsidian-style markdown** — callouts (`> [!info|tip|warning|danger|question|quote|todo|bug|example|success|abstract|note]`), transclusion (`![[Note]]` renders inline collapsibly), YAML frontmatter parsed into a typed **Properties panel** (date picker, tag chips, status dropdown).
- **Spatial canvas** — React Flow board with text nodes + note-reference nodes + arrows. Autosaves. Encrypted JSON payload.
- **Galaxy graph + persistent positions** — two graph modes. *Categories* mode clusters by `kind/*` tag around a decorative sun with orbital spring layout. *Neural-links* mode is a classic force-directed wikilink graph. Node positions are saved per user + per mode — drag a note, reload, find it where you left it. Also follows you across devices (plaintext coords only; content stays encrypted).
- **AI-native** (7 skills Obsidian can't ship natively) — autolink suggestions, auto-title/tag on save, **semantic search** via local embeddings (`nomic-embed-text` over Ollama, encrypted 768-d vectors), **"Ask your notes"** RAG with `[[citations]]`, topic rollups, morning briefings. Every feature degrades to substring/offline when Ollama's down.
- **UX chrome** — ⌘K command palette, ⌘O quick switcher, outline pane, hover preview, importance-slider graph filter, violet Obsidian-Minimal-inspired theme scoped under `.lazybrain-root` (rest of app keeps emerald).
- **Single source of truth** — tasks, personal memory, daily logs, site memory, lessons all auto-mirror here with `owner/{user,agent}` + `kind/*` tags, so the graph grows while you work.
- **Lessons v2** — single-card upsert by `(topic, action, intent)` triple (no flooding), 5-state outcome machine (proposed / verified / contested / superseded / archived), verification pump auto-bumps confidence on tool success; Telegram `/confirm` and `/reject` for manual override. `kind/shape` (how-to) split from `kind/fact` (this-is). Skills vault toggle hides the noisy `#skill` namespace from the default graph.

All content encrypted per user (AES-256-GCM with AAD=`notes:{title,content,embedding}`). 28 NL skills + 19 REST endpoints total.

### Web Search

`web_search` chains **Brave Search API → mcp-scraper → DuckDuckGo**. Brave's 2k/mo free tier covers most queries with curated, low-spam results. Set `BRAVE_KEY` in `.env` or via the NL skill `set_brave_api_key` (stores it encrypted in the vault). Telegram `/search auto|brave|scraper|duckduckgo` to override the chain. **Price/flight/shopping queries auto-route to the browser** — `web_search` detects price intent and returns a structured `[PRICE_QUERY]` instruction so the agent reads the live booking page (Google Flights / Google Shopping / Google search) instead of trusting cached snippets that are hours stale.

### Watchers

Zero-token site / channel polling. `watch_site(url)` and `watch_messages(channel)` register a watcher that runs in the heartbeat daemon — no LLM calls per check. When a change is detected (new appointment slot, page diff, new message), the canvas fires an `alert` event AND Telegram pushes a notification. Web UI `/watchers` page lists active watchers, last-seen state, and lets you stop them. Used by saved browser templates (e.g. Cita Previa Spain) for slot-watch flows.

### Context Compression

Long conversations don't break. A sliding window keeps the last 15 messages full, older ones get summarized. Daily auto-summaries (via gpt-5-mini) and weekly rollups keep context rich without re-summarizing on every message.

### Session Replay

Every agent action is recorded: LLM calls, tool invocations, specialist delegations, results. View full replays step-by-step. Generate shareable URL tokens with optional expiration.

### Permissions

Allow/ask/deny per skill category. Inline approval flow — agent pauses, asks user, resumes on approval. Full audit log with 90-day retention. First registered user becomes admin.

### Bug Bounty Toolkit

Authenticated probing on top of a forked [`shuvonsec/claude-bug-bounty`](https://github.com/shuvonsec/claude-bug-bounty) (MIT) engine. 6 NL skills:

- **`bounty_login`** — opens the program login page in your real Brave via CDP, pauses with `request_checkpoint` while you clear CAPTCHA + sign in, then captures session cookies via `Network.getCookies` and saves them encrypted (AAD = `bounty:cookies:{user_id}`) on the program record.
- **`bounty_probe`** — single safe-method (GET/HEAD/OPTIONS) authenticated request with Domain-suffix + Path-prefix cookie filtering and a per-program token-bucket rate limiter.
- **`bounty_hunt`** — deterministic 13-path probe matrix (`robots`, `sitemap`, `openapi`, `api-docs`, `swagger`, `graphql`, `actuator`, `metrics`, `.git`, `.env`, …), reflection-marker XSS classifier, files findings via the program store. Seeds non-wildcard scope assets directly so exact-host scopes (e.g. `app.aikido.dev`) survive the CT-log enum step.
- **`bounty_register`** / **`bounty_list`** / **`bounty_recon`** / **`bounty_validate`** — program lifecycle.

Permissions: new `bounty:` ALLOW category — in-band safety is the deterministic ScopeChecker, not per-call approval prompts.

## ECO Mode

Four-mode cost routing with brain/worker model split:

| Mode | Brain | Worker | Fallback | Cost |
|------|-------|--------|----------|------|
| **HYBRID** (default) | Sonnet 4.6 | `gemma4:e2b` via Ollama ($0) | Haiku 4.5 | Low |
| **FULL** | Sonnet 4.6 | Haiku 4.5 | Sonnet 4.6 | Normal |
| **CLAUDE** | Haiku API (native tools) | Haiku API | Claude CLI ($0 via subscription) | Low |
| **MINIMAX** | MiniMax-M2.7 | MiniMax-M2.7 | Haiku 4.5 (auto-spill) | Flat subscription |

**MiniMax 5h quota counter + Haiku spillover** — `MINIMAX` mode is now first-class (no more hidden `full_brain_model` override). The router tracks rolling 5-hour Token Plan request caps (4,500 Plus / 15,000 Max / 30,000 Ultra). On 429 / quota signal mid-loop, `_route_brain` and `_route_worker` saturate the 5h window (so retry storms stop) and **auto-spill to Haiku** so the agent keeps moving instead of dying. Reset countdown is shown via `/quota` Telegram command and `/api/eco/quota`.

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

- **Host Brave bridge (Docker-aware)** — when LazyClaw runs inside a container, the agent auto-detects the host's running Brave/Chrome via the `host.docker.internal` CDP bridge. Same browser, same cookies, zero extra login. **One-time setup on macOS:** `make host-bridge` installs a launchd plist that auto-starts Brave with the debug port on every login (and restarts on crash). After running, say "use my browser" / "work on my visible browser" in chat — agent connects first try, you watch the page change in your real Brave window (no noVNC needed). Status: `make host-bridge-status`. Uninstall: `make host-bridge-uninstall`.
- **Live BrowserCanvas** — embedded in the chat sidebar. See the URL, action timeline (click / type / goto), and a thumbnail of the current page as the agent works. **Zero extra LLM tokens** — events flow UI-only, never enter the agent's context.
- **Live mode** — one-tap toggle on the canvas. Captures a fresh screenshot after every action for 5 minutes. Use it when the agent is stuck or you just want to watch.
- **Checkpoints** — the agent calls `request_user_approval` before risky actions (submit, pay, book, delete, sign). The canvas shows an inline Approve / Reject banner; agent blocks until you decide. Same name auto-approves on re-call.
- **Saved templates** — reusable recipes for recurring flows. `Templates` page lets you save a playbook + setup URLs + checkpoints + optional zero-token slot watcher. Ships seed examples for Cita Previa Spain (DGT), Doctoralia, and freelance gig feeds (Upwork · Workana · PeoplePerHour) with login + submit checkpoints.
- **Slot polling** — `watch_appointment_slots` hooks a template to the watcher daemon. Zero LLM tokens per check; Telegram + canvas alert fires when slots open.
- **Apple Vision OCR** — `browser(action="ocr")` and `ask_vision` route through `ocrmac` on macOS (~200ms native, multilingual, zero hallucination, zero RAM). Replaces the old local-VLM fallback (moondream invented buttons, gemma4:e2b was 37s warmup + 15s/inference on M2 16GB). Tesseract retained for cropped / non-Mac paths.
- **Auto-close idle tabs** — every successful `action="open"` runs `sweep_idle_tabs()`. When open-tab count exceeds `max_open_tabs` (default 8, bounded `[2, 50]`, lives in `users.settings.browser`) the oldest non-active tabs close from the MRU tail. System tabs (`chrome://`, `brave://`, `devtools://`, `about:blank`) are always preserved. New `browser(action="close_tab", target=…)` lets the LLM close one tab explicitly by 1-based index, URL substring, or title substring (refuses to close active or last tab).
- **Network inspector** — captures in-page XHR/fetch traffic so the agent can pull JSON out of any page that talks to an API, even when the DOM is hostile.
- **Frame access** — reads content from iframes (Stripe checkout, embedded calendars, reCAPTCHA, ad containers) with the same ref-ID model as top-level pages.
- **Structured error capture** — every failed action returns a typed error (`NavigationTimeout`, `SelectorMissing`, `DetachedFrame`, etc.) so the agent knows *why* something broke and can recover, not just retry blindly.
- **Remote takeover** — noVNC via the `share_browser_control` NL skill or the canvas `🎮 Take control` button. Works from Telegram, web chat, and CLI identically.
- **Shared profiles** — login once, all tools see it
- **Multi-account profiles** — register isolated Chromium profiles per account (e.g. two Reddit accounts for two businesses) via the `register_browser_account` skill. Cookies / local storage / extensions never collide. NL: *"register a reddit account called marketing"*, *"switch reddit to marketing"*. Single source of truth in `lazyclaw/browser/profile_resolver.py`.
- **Per-domain cadence** — tunable click / type / scroll / dwell timing in `lazyclaw/browser/cadence.py`. Slower defaults out of the box on bot-sensitive sites (Reddit, X, Instagram, Facebook, LinkedIn). NL: *"slow down reddit by 30%"* → `tune_browser_cadence` persists a per-domain factor; the next click samples from the widened range.
- **Brave auto-detect** — Brave > Chrome > Chromium (built-in ad blocking = cleaner pages for LLM)
- **Human-like delays** — random 0.1-0.4s between clicks, 0.03-0.10s typing (default cadence, fully tunable per-domain)
- **Ref-ID snapshots** — interactive elements with click refs (~1-4KB) instead of full accessibility tree (50KB)
- **DOM click engine** — real JavaScript clicks (works with Gmail, React, Angular SPAs)
- **Site memory** — encrypted per-domain learning, auto-saved from specialist experience

## Parallel Agents

LazyClaw doesn't have one "agent" — it has a runtime that ships work across **four parallel lanes**, each with a distinct job, model strategy, and concurrency budget. The brain picks the lane based on how the task is shaped: merge-back-now, fan-out, run-while-I-keep-chatting, or review-before-shipping.

### 1. In-turn Specialists — `delegate(specialist, instruction)`

The brain calls `delegate(...)` inline; the specialist runs to completion and its result merges into the same TAOR turn. Three built-ins, each with a hand-tuned tool ladder + system prompt.

| Specialist | Strong angles | Tool ladder | Model |
|---|---|---|---|
| **🌐 Browser Specialist** | PLAN → ACT → VALIDATE loop on every step; never opens the browser without research first; payment-page detection (stops + asks before entering card details); structured error recovery (`NavigationTimeout`, `SelectorMissing`, `DetachedFrame`) so it never blindly retries; site-memory hints from previous visits | `web_search` → `mcp-scraper` (`extract_entities` / `crawl_url` / `batch_crawl`) → browser as last resort | `worker` (Haiku 4.5 / Gemma 4 E2B / MiniMax) |
| **🔧 Code Specialist** | Decoupled from brain choice — code work always rides Claude regardless of which brain you picked. Hard ladder: **Claude Code MCP (primary)** → `claude -p` CLI (fallback, warm pool of 3) → template (deep fallback). Tracks live runs grouped by `project_tag` in the new Code Specialist Web UI page. Schema-driven tool calls (no more `[TOOL_CALL]` tag parsing). | `create_skill` / `calculate` / `list_skills` / `delete_skill` + Claude Code MCP | Claude Code MCP regardless of brain ([ADR-0004](docs/adr/0004-code-tasks-route-through-claude-code.md)) |
| **🔬 Research Specialist** | Hard 5-call budget — won't loop varying queries. Never reports numbers from memory. Auto-cites sources (URLs) for every fact. Skips Instagram / Facebook / LinkedIn (anti-bot wall). Falls back to "Not found" with a one-line note instead of fabricating. | `web_search` → `mcp-scraper` → `read_file` / `list_directory` → browser only when scraper genuinely can't | `worker` |

You can also save **custom specialists** (encrypted in the `specialists` table) with your own name, system prompt, and allowed-skill list — useful for niche workflows (e.g. "Reddit-DM Specialist" with a curated tool subset).

### 2. Fire-and-forget Subagents — `dispatch_subagents(tasks=[…])`

Non-blocking parallel fan-out. The brain submits 2–5 truly different tasks and returns **immediately** with task IDs; subagents stream their results back as `background_done` events on a later turn. Subagents appear in the Activity panel under `lane='subagent'`. Single-depth enforced (subagents cannot dispatch further subagents).

| Type | Strong angles | Tools |
|---|---|---|
| **🔍 Explore** | Read-only, cheap model, isolated context. Default for research / lookups. Hard cap of 5 tool calls per task. Cannot mutate state — no writes / sends / creates / deletes. | `web_search`, `search_tools`, `recall_memories`, `read_file`, `list_directory`, `browser` (last resort) |
| **🛠️ General Purpose** | Full tool access, primary brain model. Use for complex multi-step tasks that genuinely need state mutations. Excludes recursive `dispatch_subagents` / `delegate`. | All registered tools (minus dispatch surfaces) |
| **🎯 Specialist** | Caller-scoped tool set passed inline via `tool_names=[…]`. The brain decides exactly what one subagent can touch — no off-script behavior. | Whatever the brain hands it |

Hard rules baked into the skill description so the brain doesn't misuse it:
- 1 long task → use `run_background` instead (one worker, brain free, Telegram push on done).
- ≥6 similar lookups → use ONE `run_background` that calls a batch scraper (`mcp_scraper_batch_search_google`, `mcp_scraper_batch_crawl`) — not 20 subagents that all cold-start their own context.
- Need merged answer THIS turn → `delegate(...)` for one specialist.
- Concurrency capped at 4 in-flight subagents per account; the rest queue.

### 3. Background Workers — `run_background(instruction)`

Single fresh-Agent instance with **all tools** (browser, web_search, memory, MCP, …) running in its own asyncio task. Limits: **10 global, 10 per user**. Telegram push on completion (un-truncated for tasks). Three call sites:
- **Brain-spawned** (TAOR `run_background` tool call) — the chat lane stays responsive.
- **Auto-promote** — `task_runner` automatically promotes a stuck foreground turn that exceeds its budget so the user can keep chatting.
- **Cron / heartbeat** — slim heartbeat path with the `[GOAL_PROGRESS]` / `[REMINDER]` / etc. prefix routes through `_SLIM_HEARTBEAT_PREFIX_RE` so a daily fire costs ~5k tokens, not ~40k.

Each `TrackedTask` carries an immutable `project_tag` (`upwork:job-X`, `gig:Y`, `reddit:dm`, `user_request`) so the **Code Specialist** Web UI page groups runs by workflow with an expandable per-task drawer (current_tool + recent_tools timeline + result/error). Same data backs the brain-facing `background_status` skill — answers "how's the agent doing?" without an LLM call and without waiting for the Telegram completion ping.

### 4. The Critic — adversarial reply review

Optional; off by default. Runs **only** when:
- Effort ∈ {HIGH, MAX}, AND
- `critic_mode = true` in Team settings, AND
- Caller is not itself a subagent, AND
- The last new message is an assistant reply.

Up to 3 review cycles: PASS → ship as-is. FAIL → brain rewrites text-only with `[CRITIC FEEDBACK: …]`. After 3 failures, the last draft ships with a small footer note so the user sees the critic still flagged something. **Fails open** on any exception — a flaky critic never blocks a reply.

The critic model is user-pickable per Team setting (`critic_model` validates against the model registry — any catalog id, plus `claude-cli`).

### Brain-as-dispatcher routing

The brain itself is **not** an agent in this model — it dispatches. A mid-turn pivot detector in `runtime/agent.py` re-routes the brain back to dispatch when it starts doing work itself, and the TAOR loop carries a SCOPE ESTIMATE block telling it: *if the task needs ≥5 tools or has independent sub-targets, fire `dispatch_subagents` in the FIRST tool call instead of running 3-4 sequentially before realising it should have fanned out*. Parallel `run_background` results from the same TAOR turn are consolidated into ONE final reply via the `fanout_group_id` plumbing.

## Goal Executor

Take a high-level objective ("sell my product on hirossa.com", "post the same campaign across both Reddit accounts") and let LazyClaw run it autonomously. The wedge over Google's Chrome Auto Browse: every required answer surfaces in **one batch upfront**, not drip-asked turn-by-turn.

```
> "start a goal: sell my product on Hirossa"
   → LazyClaw pulls what LazyBrain knows about your business
   → drafts a 4-step plan (login → add product → set price → publish)
   → asks 3 questions in ONE card: account email, product name, price
   → on answer: dispatches to the browser specialist
   → /goal status any time
```

State machine: `DRAFTING → AWAITING_USER_INFO → EXECUTING → DONE / BLOCKED / FAILED / ABORTED`. Encrypted `goals` table (Fernet, AAD-bound). Built on existing `plan_research` + `fix_plan.build_fix_plan` + `dispatcher` + `lazybrain.semantic_search` — no new architecture, just thin orchestration.

**6 NL skills**: `start_goal`, `answer_goal_questions`, `goal_status`, `list_goals`, `abort_goal`, `goal_progress_report`. **No auto-cron** — wire your own daily progress digest with the existing `schedule_job` skill (`[GOAL_PROGRESS] all` rides the slim heartbeat path, ~5k tokens per fire). v1 is browser-only specialist; multi-channel goals (browser + WhatsApp + Instagram + Email in one plan) deferred to v1.1.

## MCP

First-class MCP support — both client and server.

**As client:** Connect to any MCP server (stdio, SSE, streamable HTTP). External tools automatically registered as first-class skills. Parallel startup via `asyncio.gather` (~2s for 10 servers instead of sequential). Auto-install from Telegram via `/mcp install`.

**As server:** Expose LazyClaw tools to any MCP-compatible client via SSE.

**Remote MCP with OAuth:** The agent can connect to OAuth-protected remote MCP servers (Canva, GitHub, Slack, Google Drive, Gmail) via a single natural-language command. Say *"connect to Canva"* → LazyClaw opens Brave for the OAuth login → catches the callback on localhost → stores tokens encrypted in the vault → the remote server's tools become first-class agent skills. Auto-refreshes on expiry without re-prompting.

**Bundled MCP servers (8 active):**

| Server | Purpose |
|--------|---------|
| `mcp-taskai` | Task intelligence — categorize, prioritize, detect duplicates |
| `mcp-lazydoctor` | Self-healing — lint, typecheck, test, auto-fix |
| `mcp-instagram` | Instagram DMs, feed, posting via private mobile API. No browser needed. |
| `mcp-whatsapp` | WhatsApp messaging via web protocol. QR auth, no API needed. |
| `mcp-email` | Send/read/search email via SMTP+IMAP. Gmail, Outlook, any provider. |
| `mcp-jobspy` | Job search across Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google. NaN/float-safe normalizer (`normalize.py`) shared with the in-tree direct path. |
| `mcp-scraper` | crawl4ai-backed crawl + extract + search bundle. Auto-dismisses Cookiebot/OneTrust/Iubenda banners. Single persistent subprocess. **`extract_business_info`** parses schema.org JSON-LD for high-confidence addresses (no more cookie-banner-pollution false positives). Optional Scrapling-style add-ons: TLS-fingerprint impersonation (`stealth_http`), proxy rotation, adaptive selector relocation. |
| `mcp-upwork` | Apache-2.0 fork of `vanooo/upwork-mcp` — 18 tools (search, proposals, messages, contracts, profile, work diary). CDP-driven; **shares your existing Brave profile + cookies** via `LAZYCLAW_BROWSER_PROFILE_DIR`, so one login is all the agent needs. |

**Coming soon (disabled, rebuild in progress):** `mcp-freeride` (free AI router), `mcp-healthcheck` (provider monitor), `mcp-apihunter` (API discovery), `mcp-vaultwhisper` (PII proxy).

## Integrations

### Google Workspace — Direct API (Native)

> **Gmail, Calendar, Drive, Sheets, Docs — called directly. No n8n round-trip for atomic ops.** (See [ADR-0003](docs/adr/0003-google-workspace-direct-api.md).)

Atomic Google operations go through `lazyclaw/integrations/google_direct.py` — a thin Python client that calls the Google APIs over HTTPS with the user's OAuth token from the encrypted vault. This is **~10× faster** than routing the same call through an n8n oneshot workflow.

- **Gmail** — send / reply / forward / search / read / delete / label / draft
- **Drive** — list / upload / download / create folder / share / **delete / trash / empty-trash** (new)
- **Calendar** — list events / create / update / delete / RSVP / find-free-slot
- **Sheets** — read range / append rows / update cells / create sheet / clear
- **Docs** — create / read / append text / replace-text

**OAuth flow:** forked upstream [`workspace-mcp`](https://github.com/taylorwilsdon/google_workspace_mcp) (MIT) handles the consent URL + callback. Patched to pass `login_hint` so users don't pick the wrong account, and to reuse an existing browser instead of opening a fresh tab. Refresh tokens live encrypted in the vault; access tokens auto-refresh without user action.

### n8n — Visual Workflow Editor

n8n is kept for **multi-step visual workflows** — anything where you want a drag-and-drop editor, branching, loops, or sub-workflows. The 6 management skills for workflow CRUD are still here:

- **Create workflows** from natural language ("every day at 9am, pull yesterday's sales, summarize, post to Slack")
- **Edit existing workflows** — add/remove nodes, change triggers, update credentials
- **Delete, activate, deactivate** workflows on command
- **Templates** — pre-built workflow patterns the agent can deploy instantly
- **Docker sidecar** — n8n runs alongside LazyClaw via `n8n-custom/`

```
You: every day at 9am pull yesterday's sales from Sheets, summarize, post to Slack
Bot: ✅ Created workflow "Daily Sales Summary" — cron 0 9 * * *, nodes: Sheets → LLM summarize → Slack
```

> **Rule of thumb** — if it's one API call (send email, create event, append row), the agent uses the direct Google path. If it's a chain of steps you want to edit visually later, it goes through n8n.

---

## Telegram

Send a message on Telegram, get AI responses back with full tool calling. Admin chat lock (first `/start` claims the bot). Screenshots auto-forwarded. Retry with exponential backoff. **Voice notes and forwarded audio** auto-transcribe via whisper.cpp and run through the same chat pipeline (transcript echoed back so you can correct mis-hearings) — installed by default (no extras flag needed), ~1 GB RAM on the default `small` model, Metal + Core ML ANE acceleration on Apple Silicon, 99 languages incl. Georgian.

```
You: check my WhatsApp messages
Bot: ⏳ On it...
Bot: [reads WhatsApp via CDP, extracts messages]
Bot: You have 3 unread messages from Alex, Mom, and the team group...
     ─────────
     ✅ 8.2s │ 2 LLM │ 1,847 tokens
```

While the agent works, type `/status` or "what's happening" to see live progress.

**Useful commands:** `/status` (live progress), `/tasks` (active + recent), `/quota` (MiniMax 5h window remaining), `/esc <id> <reply>` (back-channel for `escalate_to_human` — used by the Upwork client-comm bot for sensitive DMs), `/confirm` and `/reject` (manual override for LazyBrain Lessons-v2 verification), `/local on|off|worker|brain|restart`, `/search auto|brave|scraper|duckduckgo`, `/allow` / `/deny` / `/permissions`, `/mcp install`, `/ram`.

## Web UI

React 19 + TypeScript + Vite + Tailwind control panel with **19 pages**, a persistent chat sidebar with live BrowserCanvas, and real-time WebSocket streaming:

- **Chat** — Dedicated `/chat` route with full-width conversation + collapsible **AgentConsole** dashboard (agent status, queued items, active background tasks, BrowserCanvas). Mic button records via MediaRecorder → `/api/audio/transcribe` → whisper.cpp; transcript appended to the draft so multiple voice snippets stack before send.
- **Overview** — System dashboard with health stats and pending approvals
- **Activity** — Live agent and task monitor (active, background, recent)
- **Code Specialist** — Live view of every Claude-Code MCP background run grouped by `project_tag`, with expandable per-task drawer (current_tool + recent_tools timeline + result/error). Reuses `/api/agents/status` — no new backend endpoint.
- **Replay** — Session trace playback and debugging
- **Audit** — Action log with filtering and security review
- **Skill Hub** — Discover and install skills
- **Skills** — Browse, create, edit, delete skills
- **Templates** — Saved browser recipes (DGT cita previa, Doctoralia, custom) with one-click Run / Watch / Seed
- **Watchers** — Active site / channel polls with last-seen state and stop button
- **Tasks** — AI quick-add, live countdown, markdown notes, NL reschedule, 3-pane Smart-reschedule layout
- **Notes (LazyBrain)** — page list + ⌘K command palette + ⌘O quick switcher; companion `/lazybrain` route renders the full canvas + galaxy graph
- **Jobs** — Cron job management with type tabs (All / Recurring / One-off), inline editor (name/instruction/cron/context with live human-readable cron preview), and Ran-OK / Failed outcome chips after each run
- **MCP** — Server management (connect, disconnect, install)
- **Memory** — Personal memories + daily logs
- **Vault** — Encrypted credential management
- **Settings** — ECO mode, model config, team settings (incl. `critic_mode` + `critic_model` selector), permissions
- **Chat Sidebar** — Persistent agent conversation with WebSocket streaming, markdown rendering, tool call visualization, and live BrowserCanvas showing URL + action timeline + thumbnail + Refresh / Live / Help / Take control buttons (available on every other page)

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
- [x] 8 active bundled MCP servers (taskai, lazydoctor, instagram, whatsapp, email, jobspy, scraper, upwork) — 4 more (freeride, healthcheck, apihunter, vaultwhisper) parked pending source rebuild
- [ ] 4 MCP servers in progress (freeride, healthcheck, apihunter, vaultwhisper — source rebuild needed)
- [x] ECO mode — HYBRID (Sonnet brain + local Ollama worker), FULL (all-Claude), CLAUDE (Haiku API)
- [x] Multi-agent teams with inline delegation
- [x] Browser automation (CDP + shared profiles + DOM click engine)
- [x] ~280 skills discoverable at runtime (196 builtin + ~85 MCP-bridged)
- [x] React Web UI control panel (18 pages + chat sidebar + live BrowserCanvas) + WebSocket streaming
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
- [x] Direct Google Workspace API (Gmail/Calendar/Drive/Sheets/Docs) — atomic ops no longer round-trip through n8n (ADR-0003)
- [x] workspace-mcp OAuth fork — login_hint patching + consent flow + encrypted refresh tokens
- [x] Browser OCR + network inspector + frame access + structured error capture
- [x] Host Brave CDP bridge — container-side agent talks to host browser via `host.docker.internal`
- [x] Task Manager AI quick-add + live countdown + markdown notes
- [x] LazyBrain galaxy graph + persistent node positions (per-user, per-mode, cross-device)
- [x] WebSocket streaming (`/ws/chat`) for real-time Web UI chat
- [x] **Goal Executor** — autonomous high-level objectives. Encrypted `goals` table, state machine, batch-asks every required input upfront in ONE card (vs. drip-asking turn-by-turn the way most agents do), dispatches to the browser specialist, reports progress on demand. Built on existing `plan_research` + `fix_plan` + `dispatcher` — no new architecture.
- [x] **Multi-account browser identity** — full Chromium profile isolation per account (e.g. two Reddit accounts for two businesses), single-source-of-truth profile resolver. Cookies / local-storage / extensions never collide.
- [x] **Per-domain browser cadence** — tunable click/type/scroll/dwell timing, slower defaults on bot-sensitive sites (Reddit, X, Instagram, Facebook, LinkedIn), NL skill to retune ("slow down reddit by 30%").
- [x] **Critic mode** — adversarial reply review on HIGH/MAX-effort turns, up to 3 rewrite cycles, fails-open, user-pickable critic model (`critic_model` validates against the catalog).
- [x] **Upfront `dispatch_subagents` hint** — TAOR loop carries a SCOPE ESTIMATE block so the brain fans out in the first tool call instead of running 3-4 sequential calls before realizing it should have parallelized.
- [x] **MiniMax M2.7 first-class mode** — peer to HYBRID/FULL/CLAUDE in `MODE_MODELS`, 5-hour Token Plan rolling counter, automatic Haiku spillover on 429 / quota.
- [x] **Authenticated Bug Bounty toolkit** — forked claude-bug-bounty + 6 NL skills (`bounty_login` / `register` / `recon` / `probe` / `validate` / `hunt`), encrypted cookie jar, 13-path probe matrix, scope-aware rate limiter.
- [x] **Code Specialist Web UI page** — live runs grouped by `project_tag`, expandable drawers with tool timeline, no new backend.
- [x] **Auto-close idle browser tabs** — `sweep_idle_tabs()` after every `open`, configurable `max_open_tabs`, system tabs preserved, plus `browser(action="close_tab", target=…)` for explicit per-tab close.
- [x] **Apple Vision OCR** replaces local-VLM browser fallback — `ocrmac` ~200ms, multilingual, zero hallucination on macOS; Tesseract retained for cropped / non-Mac paths.
- [x] **Schema-driven Claude CLI tool calls** — `--json-schema` + structured_output (legacy `[TOOL_CALL]` tag parser kept as safety net), warm pool of 3 with parallel pre-warm refill.
- [x] **Upwork client-comm bot** — `upwork_inbox_check` cron-fired sweeper classifies 10 categories, auto-replies safe ones, escalates sensitive ones via generic `escalate_to_human` skill (Telegram `/esc <id> <reply>` back-channel).
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
├── skills/         # 196 builtin skills — Instruction, Code, Plugin, Survival, Templates
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

web/                # React 19 control panel (18 pages + chat sidebar + live BrowserCanvas)
n8n-custom/         # n8n Docker sidecar config
mcp-taskai/         # Task intelligence
mcp-lazydoctor/     # Self-healing agent
mcp-instagram/      # Instagram DMs, feed, posting (20 tools)
mcp-whatsapp/       # WhatsApp messaging + mute (12 tools)
mcp-email/          # Email via SMTP+IMAP (11 tools)
mcp-jobspy/         # Job search aggregation + NaN/float-safe normalizer
mcp-scraper/        # crawl4ai crawl/extract/search bundle (single subprocess)
mcp-upwork/         # Apache-2.0 fork of vanooo/upwork-mcp (CDP, shared Brave profile)
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
