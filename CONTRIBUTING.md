# Contributing to LazyClaw

Thanks for thinking about contributing! LazyClaw is a solo-maintainer project in early beta — bug reports, ideas, and code are all welcome.

## TL;DR

- **Bug?** → [File an issue](https://github.com/Bsh13lder/Lazy-Claw/issues/new/choose) with logs + steps to reproduce.
- **Idea?** → [Start a discussion](https://github.com/Bsh13lder/Lazy-Claw/discussions) before writing a big PR.
- **Security?** → Use [GitHub Security Advisories](https://github.com/Bsh13lder/Lazy-Claw/security/advisories/new), not a public issue.
- **Small fix?** → PR directly. Follow the commit style below.

## Dev setup

```bash
git clone https://github.com/Bsh13lder/Lazy-Claw.git
cd Lazy-Claw

# Python side
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"    # includes pytest

# Web side
cd web && npm install && cd ..

# Run
lazyclaw setup   # first time only — generates SERVER_SECRET etc.
lazyclaw start   # full server (API + Telegram + TUI)
cd web && npm run dev   # Web UI on :5173 (proxies to :18789)
```

## Running tests

```bash
pytest tests/             # Python smoke tests
cd web && npx tsc -b      # TypeScript type-check
cd web && npm run lint    # ESLint
```

All three should stay green on every PR.

## Project shape

```
lazyclaw/            # Python package
  browser/           # CDP browser control + event bus + checkpoints + templates
  channels/          # Telegram native adapter
  crypto/            # AES-256-GCM + PBKDF2 + vault
  db/                # aiosqlite + schema.sql
  gateway/           # FastAPI routes + WebSocket chat
  heartbeat/         # Cron daemon + watchers
  llm/               # Multi-provider router (Anthropic, MiniMax, OpenAI, Ollama, Claude CLI)
  mcp/               # MCP client + server + OAuth + bridge
  memory/            # Encrypted facts + compression + daily logs
  permissions/       # Allow / ask / deny + audit log
  queue/             # Lane queue (serial per-user)
  replay/            # Session trace recording + sharing
  runtime/           # TAOR agent loop + context + tool dispatch
  skills/            # 128 builtin skills + MCP-bridged skills
  tasks/             # Encrypted task store + reminders
  teams/             # Specialist delegation + parallel execution
  watchers/          # Zero-token site monitor history

web/                 # React 19 + TS + Vite + Tailwind control panel
mcp-*/               # 10 bundled MCP servers
tests/               # pytest smoke tests
```

See `CLAUDE.md` for architecture details and `DOCS.md` for the full module reference.

## Commit style

Conventional commits, no AI attribution, imperative mood, 50-char title max:

```
feat: add DGT cita previa template
fix: mask password fields in browser event log
docs: clarify ECO worker model migration
refactor: extract template_synth helper
test: cover browser event bus isolation
chore: bump anthropic SDK to 0.30
```

Body (optional) wraps at 72 chars. Explain *why*, not *what* — the diff shows what.

## Things LazyClaw protects — don't break them

1. **E2E encryption** — all user content is encrypted before disk. Don't add logs or DB columns that store plaintext messages, vault entries, memory, tasks, etc.
2. **Per-user isolation** — every DB query scopes on `user_id`. Never add a query that reads across users.
3. **Zero-token UI events** — the browser event bus is UI-only. Don't route its events through the agent callback / LLM context.
4. **Smart tool selection** — 128 skills registered, but the LLM sees only 4 base tools per turn. Don't blast the full catalogue into the prompt.

## PR process

1. Fork + branch off `main`
2. Make your change + tests
3. `pytest tests/` + `cd web && npx tsc -b` green
4. Open PR with the template — filled in
5. Expect light review + iteration; solo-maintained means response times vary

## License

By contributing, you agree that your contributions are licensed under the MIT License (see `LICENSE`).
