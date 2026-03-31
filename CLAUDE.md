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

### Key Differentiators vs OpenClaw
- **E2E Encryption**: AES-256-GCM on all user content. OpenClaw stores everything in plaintext.
- **Python-native**: Full Python stack. Python AI ecosystem is 10x larger than TypeScript.
- **Native MCP**: First-class MCP client AND server. OpenClaw uses a hacky converter.
- **Encrypted Credential Vault**: API keys stored encrypted, not plaintext .env files.

## Architecture

12 core components in `lazyclaw/` + supporting modules:

| Component | Path | Purpose |
|-----------|------|---------|
| **Gateway** | `gateway/` | FastAPI HTTP+WS entry point. Session auth, CORS, routing |
| **Agent Runtime** | `runtime/` | System prompt (SOUL.md + memory + skills), LLM calls, tool dispatch |
| **Lane Queue** | `queue/` | FIFO serial execution per user session |
| **Skills** | `skills/` | Instruction (NL), Code (sandboxed Python), Plugin (pip). Unified registry |
| **Channels** | `channels/` | Telegram adapter (+ future Discord, WhatsApp, Signal, SimpleX) |
| **Browser** | `browser/` | CDP-only browser control, JS extractors, site memory |
| **Computer** | `computer/` | Native subprocess + WebSocket connector (remote) |
| **Memory** | `memory/` | Encrypted personal facts, conversation history, compression |
| **MCP** | `mcp/` | Native client + server + bridge to skill registry |
| **Crypto** | `crypto/` | AES-256-GCM, PBKDF2, credential vault |
| **Teams** | `teams/` | Specialists (browser, research, code) + delegate skill + parallel execution |
| **Replay** | `replay/` | Session trace recording, playback, shareable tokens |
| **Task Runner** | `runtime/task_runner.py` | Background parallel task execution with Telegram push notifications |

Supporting: `llm/` (multi-provider router + ECO mode + complexity routing), `heartbeat/` (cron daemon), `permissions/` (allow/ask/deny + audit), `db/` (aiosqlite + connection pool).

Standalone MCP servers: `mcp-freeride/` (free AI router), `mcp-healthcheck/` (provider monitor), `mcp-apihunter/` (API discovery), `mcp-vaultwhisper/` (PII proxy), `mcp-taskai/` (task intelligence), `mcp-lazydoctor/` (self-healing).

## Build & Run

```bash
./install.sh              # One-command install (Python + deps + setup)

# Or manually:
pipx install --editable . # Global install via pipx
lazyclaw setup            # First-time setup wizard
lazyclaw start            # Full server (FastAPI + Telegram + Heartbeat)
lazyclaw                  # Chat REPL only
```

Default port: **18789**. MCP servers run standalone via `python -m mcp_freeride` etc.

## E2E Encryption

All user content encrypted before storage. Server never sees plaintext.

- Registration generates random `encryption_salt` per user
- Key derivation: `PBKDF2(password, salt, 100k iterations, SHA-256)` -> AES-256
- Storage format: `enc:v1:<base64-nonce>:<base64-ciphertext>`
- Server-side key for daemon ops: `PBKDF2(SERVER_SECRET + user_id, fixed_salt, 100k)`
- **Encrypted**: conversations, memory, skills, vault, jobs, channel configs
- **Plaintext** (needed for queries): IDs, timestamps, status, cron expressions, domains

## Key Patterns

These are non-obvious architectural decisions -- read the code for implementation details:

- **User isolation**: ALL queries scoped by `user_id`. No cross-user data access.
- **No hardcoded tools**: All tools from skill registry. Agent discovers dynamically.
- **Smart tool selection**: Per-message category detection sends only relevant tools (8-17 instead of 71). 70-88% token savings.
- **Lane Queue**: Serial per-user foreground execution. Background tasks run in parallel via TaskRunner.
- **Background tasks**: `run_background` skill → TaskRunner spawns independent Agent → Telegram push on completion.
- **Delegate tool**: Agent calls `delegate(specialist, instruction)` inline — no separate team lead LLM call.
- **ECO v3 routing**: Three modes, 3 roles (Brain=Team Lead, Worker, Fallback). ECO ON: Haiku brain + Nanbeige worker ($0) + Sonnet fallback (ask permission). HYBRID: same models, auto-fallback. FULL: Sonnet brain + Haiku worker + Opus fallback. All models from `MODE_MODELS` dict in `model_registry.py`. `eco_router.py` routes by role (ROLE_BRAIN vs ROLE_WORKER).
- **MLX backend**: `mlx_provider.py` for Apple Silicon local inference. `mlx_manager.py` manages server lifecycle. Auto `/no_think` for Qwen models. `<think>` tag stripping for Nanbeige.
- **RAM monitor**: `ram_monitor.py` tracks system + AI model memory. `/ram` Telegram command. TUI status bar shows RAM %. Uses macOS `memory_pressure` for accurate free %.
- **Telegram /local command**: `/local on|off|worker|brain|restart` — start/stop MLX servers, auto-switches ECO mode.
- **Unified browser tool**: Single `browser` skill with 7 actions (read, open, click, type, screenshot, tabs, scroll). CDP-only, no Playwright.
- **Brave browser**: Auto-detected (Brave > Chrome > Chromium). Built-in ad/tracker blocking = cleaner pages for LLM.
- **Fast chat path**: Simple messages get last 6 messages, SOUL.md only (no capabilities/memories/tools).
- **Layered summaries**: Daily logs (auto, gpt-5-mini) + weekly + injected into agent context. Skips 90s LLM re-summarization.
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
- **ECO mode**: Three modes (eco_on/hybrid/off). ECO ON = Haiku brain + Nanbeige workers ($0). HYBRID = same, auto-fallback. FULL = Sonnet brain + Haiku workers + Opus fallback.
- **Token tracking**: OpenAI streaming reads usage chunk after finish_reason. Anthropic field names normalized.

## Git Commit Rules

- **No Co-Authored-By**: Do NOT add "Co-Authored-By: Claude" or any AI attribution to commits
- Keep commit messages clean and human-style
