# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**LazyClaw** is an open-source (MIT) E2E-encrypted AI agent platform — Python (FastAPI + asyncio + aiosqlite) with a React web UI and a Flutter mobile app. Every byte of user content is AES-256-GCM encrypted at rest with a per-user key; the server stores only ciphertext. Tagline: *"OpenClaw, but encrypted and Python-native."* Solo-maintainer, early beta, daily updates.

## Documentation Map

- **[DOCS.md](DOCS.md)** — function/class reference + dated implementation deep-dives. Update it when adding modules.
- **[TODO.md](TODO.md)** — roadmap, phase plans, task tracking. All status lives here.
- **[mobile/CLAUDE.md](mobile/CLAUDE.md)** — Flutter app patterns (auto-loads when working under `mobile/`).
- **This file must stay under 40,000 characters.** It loads on every message. Never dump file maps, endpoint lists, DB schemas, or env-var tables here — put deep detail in DOCS.md, roadmap in TODO.md.

## Commands

### Dev setup & run (host)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # includes pytest
lazyclaw setup                 # first time — generates SERVER_SECRET etc.
lazyclaw start                 # full server (API + Telegram + Heartbeat) on :18789
lazyclaw                       # chat REPL only
```

### Docker (production deployment)
The container runs a **baked image — source is NOT mounted**. Any Python change requires `make rebuild` to take effect.
```bash
make up / down / logs
make rebuild                   # docker compose build lazyclaw && up -d — REQUIRED after code changes
make claude-login              # OAuth login for the in-container Claude CLI (persists in claude_creds volume)
make claude-status             # checks token EXPIRY, not just file presence
make host-bridge               # launchd: real Mac Brave with CDP :9222 for the container (then make rebuild)
make awake-bridge              # root LaunchDaemon :18791 — caffeinate + pmset wake alarms
```

### Tests
```bash
pytest tests/                          # full suite (asyncio_mode=auto)
pytest tests/runtime/test_x.py         # one file
pytest tests/test_agent_mode.py -k name  # one test
```
**`./data` is the LIVE production DB** (bind-mounted into the container). `tests/conftest.py` force-repoints `DATABASE_DIR` to a temp dir with a loud tripwire — never bypass or weaken it, and never point ad-hoc scripts at `./data` while the container is up. A killed mid-write run once corrupted the prod SQLite (2026-07-03).

### Web (`web/` — React 19 + TypeScript + Vite + Tailwind)
```bash
cd web && npm install
npm run dev       # :5173, proxies to :18789
npm run build     # tsc -b && vite build
npm run lint      # eslint
```
Python + `tsc -b` + eslint must stay green on every PR.

### Mobile (`mobile/` — Flutter)
```bash
scripts/build-mobile-apk.sh    # publishes mobile/dist/app-release.apk + version.json
```
Gateway serves it at `/api/mobile/{apk,version}`. See `mobile/CLAUDE.md` for app patterns.

## Architecture

### Request flow (the big picture)
Channel (Telegram native / web WS / mobile / WhatsApp-Instagram-Email via MCP) → `gateway/` (FastAPI, session auth) → `queue/` **lane queue** (strict FIFO per user; background work runs parallel via TaskRunner) → `runtime/` **TAOR loop** (Think-Act-Observe-Reflect, independent tool calls gathered concurrently) → tools resolved through the **skill registry** → LLM via `llm/eco_router.py`.

### Modules (`lazyclaw/`)
Core: `gateway` `runtime` `queue` `skills` (~280 builtin) `channels` `browser` `memory` `mcp` `crypto` `teams` `tasks` `heartbeat` (cron daemon) `permissions` `db` (shared aiosqlite connection pool) `llm`. Feature domains: `lazybrain` (encrypted Obsidian-grade PKM), `sheets`/`docs`/`pdf` (encrypted office suite), `comms` (unified cross-channel inbox), `survival` (gig-economy pipeline), `notifications`, `replay`, `pipeline`, `contacts`, `budgets`, `watchers`, `voice`/`audio`.

Repo root also hosts 12 standalone MCP servers (`mcp-upwork/`, `mcp-whatsapp/`, `mcp-email/`, `mcp-instagram/`, `mcp-scraper/`, `mcp-taskai/`, `mcp-lazydoctor/`, …) run as separate processes, plus `web/`, `mobile/`, `personality/SOUL.md` (the agent's system-prompt personality).

### E2E encryption (`crypto/`)
- Per-user DEK derived `PBKDF2(password, per-user salt, 600k, SHA-256)`; DEK stored envelope-encrypted under the server master key; BIP-39 recovery phrase re-derives it. Server-side daemon key: `PBKDF2(SERVER_SECRET + user_id, fixed salt, 600k)`.
- Storage format: `enc:v1:<b64-nonce>:<b64-ciphertext>`. PBKDF2 results are LRU-cached (420ms → 0).
- **Encrypted**: conversations, memories, skills, vault, jobs, channel configs, tool-call args, documents. **Plaintext** (queryability only): IDs, timestamps, status, cron expressions, domains.
- Documents follow **one encrypted blob per file**: Sheets/Docs persist the whole Univer snapshot as a single `enc:v1` JSON blob; PDFs store `enc:v1(base64(bytes))`. Never a per-cell schema.

### Skill registry — no hardcoded tools
Everything the agent can do is a skill (Instruction / Code / Plugin), including external MCP tools bridged in as first-class skills. Only 4 base tools ship per message (`search_tools`, `recall_memories`, `save_memory`, `agent`); the brain discovers the rest via `search_tools` (~95% token savings vs sending all tools).

### Specialist dispatch (`teams/`)
Brain = thin router; work is delegated via `agent(agent_type, task, run_in_background)` to declarative specialists — `.md` + YAML frontmatter in `teams/specialists/`, loaded by `teams/specialist_loader.py`; custom ones live encrypted in DB. **The `tools:` allowlist in each specialist file is the callability contract** — `search_tools` discovery does NOT grant callability, and a prompt-named tool missing from the allowlist causes a stuck loop. When adding a capability to a specialist prompt, add the tool to its allowlist in the same change; audit by capability (reads AND writes). `startup_specialist_self_check` warns on drift at boot.

### LLM routing (`llm/eco_router.py`)
4 modes × 3 roles (Brain/Worker/Fallback): **HYBRID** (default: Sonnet brain + local Ollama worker + Haiku fallback) · **FULL** · **CLAUDE** (everything through Claude subscription; SDK transport default, CLI legacy) · **MINIMAX**. Models come from `MODE_MODELS` in `model_registry.py` — never hardcode model IDs elsewhere.

### Browser (`browser/`)
CDP-only (no Playwright). Brave > Chrome > Chromium auto-detect; shared per-user profiles (`browser_profiles/{user_id}/`, optional per-account slugs) so one login serves all tools. Semantic accessibility-tree snapshots instead of screenshots. Event bus is UI-only — **browser events never enter LLM context**. Checkpoints block before risky actions (submit/pay/book/delete/sign/send). Cloudflare-protected sites (Upwork, LinkedIn) must go through the signed-in host Brave via `cdp_port` — fresh headless fails fingerprinting silently.

### Memory
5 layers merged in `runtime/context_builder.py`: live messages → sliding window → daily summary → weekly rollup → long-term encrypted facts. LazyBrain is the single home for knowledge (encrypted notes, wikilinks, graph, semantic search). **Memory isolation:** never bridge `~/.claude/plans/*` or `~/.claude/projects/*` into lazybrain — a past mirror leaked cross-project session content.

## Non-Negotiable Rules

- **License discipline**: MIT project — permissive deps only (Apache/MIT/BSD/MPL). NEVER PyMuPDF, borb, HyperFormula/Handsontable, `formulas`/`pycel` (AGPL/GPL/EUPL/commercial) — they poison the license. Check before adding any dependency.
- **Encrypt everything**: user content is always encrypted at rest, no exceptions. New tables follow the `enc:v1` pattern.
- **User isolation**: every query is scoped by `user_id`. No cross-user access, ever.
- **No optional extras for core features**: default-on features get base deps; `install.sh`/Dockerfile never pass `[extra]`.
- **SOUL.md before runtime nudges**: when the agent brain misbehaves, the first lever is `personality/SOUL.md`, not new code/regex/thresholds.
- **Never guess data**: no fabricated prices, stats, or version numbers. Look up real values or say you can't.
- **Simplicity & minimal impact**: root-cause fixes, smallest possible diff, no temporary hacks. Extract proven code, don't rewrite.
- **Upwork guardrails** (enforced in `mcp-upwork/`): DMs must contain no links (Upwork silently deletes them) and must never pitch LazyClaw by name — describe the work + stack as personal freelancing.

## Git

- Conventional commits: `<type>: <description>` (feat, fix, refactor, docs, test, chore, perf, ci).
- **No Co-Authored-By / AI attribution** in commits — keep messages clean and human-style.
- Never commit with `-a`/`-A`/`.` sweeps — stage files by name (protects unrelated WIP).
