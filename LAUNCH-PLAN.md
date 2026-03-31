# LazyClaw Launch Plan — Go Live & Grow Day by Day

## Strategy
Go live NOW with what works (CLI + TUI + Telegram + API). Add Web UI as first major feature. Fix bugs in parallel. Add channels and features day by day.

## Session Map — What Touches What (NO CONFLICTS)

| Session | Files Touched | Can Run In Parallel With |
|---------|--------------|--------------------------|
| **W1** Web UI Frontend | NEW: `web/` folder (React) | B1, B2 |
| **W2** WebSocket Streaming | `gateway/app.py`, NEW: `gateway/routes/streaming.py` | B1, B2 |
| **B1** Silent Exception Fix | Various `.py` files (only `except: pass` blocks) | W1, W2 |
| **B2** Config Extraction | `config.py`, various (only timeout/URL constants) | W1, W2 |

After launch (day by day — one per day):

| Session | What |
|---------|------|
| **D1** | Discord channel adapter |
| **D2** | WhatsApp channel adapter |
| **D3** | Skill marketplace / public registry |
| **D4** | Voice input (whisper) |
| **D5** | Web UI dashboard (settings, memory, jobs panels) |

---

## SESSION W1 — Web UI Chat Frontend
**Time: ~4-5 hours | Priority: HIGH**
**Files: Creates NEW `web/` directory — zero conflict with backend**

```
READ CLAUDE.md, DOCS.md, and lazyclaw/gateway/app.py first.

TASK: Build a minimal React web frontend for LazyClaw in a new `web/` directory at the project root.

THE API ALREADY EXISTS — you are just building the frontend that talks to it. Do NOT modify any backend files.

EXISTING API ENDPOINTS YOU MUST USE:
- POST /api/auth/register — body: {username, password}
- POST /api/auth/login — body: {username, password}
- POST /api/auth/logout
- GET /api/auth/me — returns current user (session cookie auth)
- POST /api/agent/chat — body: {message} — returns {response}
- GET /api/health — returns {status: "ok"}

AUTH: Session-based cookies. The API sets an httponly cookie called "session_id" on login/register. All subsequent requests include this cookie automatically (use credentials: "include" in fetch).

CORS: Backend default is http://localhost:3000, so run the dev server on port 3000.

WHAT TO BUILD:

1. Login/Register page
   - Simple form: username + password
   - Toggle between login and register
   - On success, redirect to chat
   - Show error messages from API

2. Chat page (main screen)
   - Full-screen chat interface
   - Message input at bottom
   - Messages displayed as conversation (user messages right, agent responses left)
   - Show loading spinner while agent is processing
   - Markdown rendering for agent responses (use react-markdown)
   - Auto-scroll to latest message
   - Logout button in header

3. Sidebar (collapsible)
   - "New Chat" button
   - Chat history (store locally in state for now — we'll add persistence later)

DESIGN:
- Dark theme (match the terminal aesthetic — dark backgrounds, green/cyan accents)
- Clean, minimal, modern
- Mobile responsive
- LazyClaw branding: show the name + "E2E Encrypted AI Agent" tagline
- Show a lock icon somewhere prominent — encryption is our selling point

TECH STACK:
- React 18+ with Vite
- TailwindCSS for styling
- react-markdown for rendering responses
- No state management library needed — useState/useContext is fine
- TypeScript preferred but not required

STRUCTURE:
web/
├── package.json
├── vite.config.ts
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api.ts          (all API calls centralized)
│   ├── context/
│   │   └── AuthContext.tsx
│   ├── pages/
│   │   ├── Login.tsx
│   │   └── Chat.tsx
│   ├── components/
│   │   ├── MessageBubble.tsx
│   │   ├── ChatInput.tsx
│   │   ├── Sidebar.tsx
│   │   └── Header.tsx
│   └── styles/
│       └── globals.css

DO NOT:
- Modify any backend files
- Add authentication logic to the backend (it already works)
- Use localStorage for auth tokens (cookies handle this)
- Over-engineer — this is v1, we'll iterate

VERIFY:
- npm run dev starts on port 3000
- Can register a new user
- Can login
- Can send a message and see the response
- Can logout
- Works on mobile viewport
```

---

## SESSION W2 — WebSocket Agent Streaming
**Time: ~2-3 hours | Priority: HIGH**
**Files: `gateway/app.py` (add 1 route), NEW `gateway/routes/streaming.py`**

```
READ CLAUDE.md and lazyclaw/gateway/app.py and lazyclaw/gateway/routes/connector.py (for WebSocket pattern reference).

TASK: Add a WebSocket endpoint for real-time agent response streaming. Currently /api/agent/chat is blocking HTTP POST — the user waits for the full response. We need streaming so the web UI can show tokens as they arrive.

WHAT EXISTS:
- /ws/connector in gateway/routes/connector.py — use this as a pattern for WebSocket auth
- /api/agent/chat in gateway/app.py line 145 — current blocking endpoint
- Session cookie auth in gateway/auth.py — get_current_user() validates session_id cookie
- Agent.process_message() in runtime/agent.py — the main agent loop
- LaneQueue in queue/lane.py — serial per-user queue

CREATE: gateway/routes/streaming.py

NEW ENDPOINT: WebSocket /ws/agent/chat

FLOW:
1. Client connects to /ws/agent/chat
2. Server validates session_id cookie from WebSocket headers (same as REST auth)
3. Client sends JSON: {"message": "user question here"}
4. Server streams back JSON chunks:
   - {"type": "token", "content": "partial text..."} — as tokens arrive
   - {"type": "tool_use", "name": "browser", "status": "running"} — when agent uses a tool
   - {"type": "tool_result", "name": "browser", "summary": "Opened google.com"} — tool finished
   - {"type": "done", "full_response": "complete text"} — final message
   - {"type": "error", "message": "what went wrong"} — on failure
5. Connection stays open for multiple messages in same session

IMPORTANT:
- Reuse the existing auth pattern from connector.py but adapt for cookies instead of Bearer tokens
- The existing Agent class may not support streaming yet — if process_message() returns a full string, that's fine for v1. Just send the full response as a single "done" message. We can add true token streaming later.
- Add the new router to app.py (streaming_ws_router) — this is the ONLY change to app.py
- Wire through CancellationToken so the user can cancel mid-generation via a {"type": "cancel"} message

DO NOT:
- Remove or modify the existing /api/agent/chat endpoint (keep it for backward compat)
- Modify agent.py or any runtime files
- Break the existing /ws/connector endpoint

VERIFY:
- WebSocket connects successfully with valid session cookie
- Rejects connection without valid session
- Sends back agent response when message is received
- Handles errors gracefully (returns error type, doesn't crash)
```

---

## SESSION B1 — Silent Exception Fix (130+ locations)
**Time: ~2-3 hours | Priority: HIGH**
**Files: Various .py files — ONLY changes `except: pass` to `except Exception: logger.debug()`**

```
READ CLAUDE.md first.

TASK: The codebase has 130+ instances where exceptions are silently swallowed with bare `except: pass` or `except Exception: pass`. This makes debugging impossible. Fix ALL of them.

RULES:
1. Search the entire lazyclaw/ directory (exclude .venv) for:
   - `except:` followed by `pass`
   - `except Exception:` followed by `pass`
   - `except Exception as e:` followed by `pass`
   - Any except block that does nothing

2. For EACH instance, replace with appropriate logging:
   - If in a cleanup/teardown path (finally, __del__, shutdown): use logger.debug()
   - If in a retry/fallback path: use logger.warning()
   - If in a main execution path: use logger.error()
   - If the except block is intentionally suppressing a known harmless error (like "connection already closed"), keep pass but add a comment explaining WHY

3. Make sure each file has `import logging` and `logger = logging.getLogger(__name__)` at the top (add if missing)

4. The most critical files to fix (highest impact):
   - lazyclaw/channels/telegram.py (10 instances)
   - lazyclaw/cli_tui.py (19 instances)
   - lazyclaw/skills/builtin/browser_skill.py (13 instances)
   - lazyclaw/browser/cdp_backend.py (multiple instances)
   - lazyclaw/runtime/ files

5. Also fix these 2 deprecated patterns:
   - connector/connector.py:70 — change asyncio.ensure_future() to asyncio.create_task()
   - lazyclaw/llm/mlx_manager.py:191 — change asyncio.ensure_future() to asyncio.create_task()

DO NOT:
- Change any business logic
- Restructure any code
- Add new features
- Touch anything that isn't an exception handling block (except the 2 ensure_future fixes)

VERIFY:
- grep -r "except.*pass" lazyclaw/ --include="*.py" | grep -v .venv — should return ZERO results (or only commented-out/intentional ones)
- grep -r "ensure_future" lazyclaw/ --include="*.py" | grep -v .venv — should return ZERO results
- Run: python -c "import lazyclaw" — no import errors
```

---

## SESSION B2 — Config Extraction (Timeouts & URLs)
**Time: ~2 hours | Priority: MEDIUM**
**Files: `config.py` + various (only constant values, no logic changes)**

```
READ CLAUDE.md and lazyclaw/config.py first.

TASK: Extract hardcoded timeouts, ports, and URLs into the central config system so they're configurable via environment variables.

WHAT TO EXTRACT:

1. TIMEOUTS (add to config.py with env var overrides):
   - MCP operation timeout: 120s (mcp/manager.py)
   - Local model inference timeout: 120s (llm/eco_router.py)
   - Browser automation timeout: 60s (browser/tab_manager.py)
   - Task delegation timeout: 300s (skills/builtin/delegate.py)
   - CLI exit cleanup timeout: 2s (cli.py) — increase default to 5s
   - MCP idle disconnect: 300s (mcp/manager.py)

2. PORTS (add to config.py with env var overrides):
   - MLX Brain port: 8080 (llm/mlx_manager.py)
   - MLX Worker port: 8081 (llm/mlx_manager.py)
   - Ollama port: 11434 (llm/providers/ollama_provider.py)

3. In config.py, add a new section with these fields and their env vars:
   - MCP_TIMEOUT=120
   - LOCAL_INFERENCE_TIMEOUT=120
   - BROWSER_TIMEOUT=60
   - DELEGATE_TIMEOUT=300
   - EXIT_CLEANUP_TIMEOUT=5
   - MCP_IDLE_TIMEOUT=300
   - MLX_BRAIN_PORT=8080
   - MLX_WORKER_PORT=8081
   - OLLAMA_PORT=11434

4. Update each file to read from config instead of hardcoded values.

DO NOT:
- Change any logic or behavior — only move constants to config
- Touch exception handling (that's Session B1)
- Touch the gateway or auth code
- Change default values (except CLI exit timeout: 2→5)

VERIFY:
- All timeouts still work with default values (behavior unchanged)
- Setting env vars actually overrides the defaults
- python -c "from lazyclaw.config import load_config; c = load_config(); print(c.mcp_timeout)" prints 120
```

---

## POST-LAUNCH: Day-by-Day Features

### DAY 1 — Discord Channel (Session D1)
```
READ CLAUDE.md and lazyclaw/channels/telegram.py and lazyclaw/channels/base.py for the channel pattern.

TASK: Add Discord as a second messaging channel. Follow the exact same pattern as the Telegram adapter.

Create lazyclaw/channels/discord.py following the base channel interface. Use discord.py (py-cord) library. Support: text messages, slash commands (/chat, /memory, /jobs, /settings), embed responses for formatted output, file uploads/downloads.

Register the Discord channel in the startup flow (cli.py) alongside Telegram. Add DISCORD_BOT_TOKEN to .env.example.

The goal is: if a user sets DISCORD_BOT_TOKEN in .env, Discord bot starts alongside Telegram automatically.

DO NOT modify telegram.py or any existing channel code.
```

### DAY 2 — WhatsApp Channel (Session D2)
```
READ CLAUDE.md and lazyclaw/channels/telegram.py for the channel pattern.

TASK: Add WhatsApp as a messaging channel via the WhatsApp Business Cloud API (official Meta API, not Baileys — we want stability and compliance).

Create lazyclaw/channels/whatsapp.py. Support: text messages, webhook verification, message status callbacks, media messages.

Add WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN to .env.example.

Create a webhook endpoint in gateway/routes/whatsapp.py: GET /api/webhooks/whatsapp (verification), POST /api/webhooks/whatsapp (incoming messages).

DO NOT modify telegram.py or discord.py.
```

### DAY 3 — Skill Hub: Universal Skill & MCP Registry (Session D3)
**THIS IS A PRIORITY FEATURE — moves up if ready before Discord/WhatsApp**
```
READ CLAUDE.md, lazyclaw/skills/registry.py, lazyclaw/mcp/manager.py, and DOCS.md first.

TASK: Build "Skill Hub" — a universal registry where ANY agent framework (LazyClaw, OpenClaw, NemoClaw, JiuwenClaw, CrewAI, etc.) can discover, install, and use skills and MCP servers. This is not just for LazyClaw users — it's a cross-platform marketplace.

VISION: LazyClaw becomes the "npm of AI agent skills" — other claws come to US for tools.

PART 1 — Skill Package Format (universal, framework-agnostic):

Create a standard package format that works across frameworks:

skillhub/
├── __init__.py
├── package.py          (SkillPackage dataclass: name, version, author, description, tags, framework_compat, mcp_server, skill_type, install_cmd, dependencies)
├── registry.py         (SkillHubRegistry: search, publish, install, update, rate, review)
├── formats/
│   ├── __init__.py
│   ├── lazyclaw.py     (export/import LazyClaw native skills)
│   ├── openclaw.py     (export/import OpenClaw SKILL.md format)
│   └── mcp.py          (export/import standalone MCP servers)
└── api.py              (FastAPI routes for the hub)

Package metadata (skill-hub.json):
{
  "name": "web-scraper",
  "version": "1.0.0",
  "author": "username",
  "description": "Scrape any website with anti-bot evasion",
  "tags": ["browser", "scraping", "data"],
  "type": "mcp_server" | "skill" | "plugin",
  "frameworks": ["lazyclaw", "openclaw", "any"],
  "mcp": {
    "transport": "stdio",
    "command": "python -m web_scraper_mcp",
    "tools": ["scrape_url", "extract_data", "screenshot"]
  },
  "install": "pip install web-scraper-mcp",
  "license": "MIT"
}

PART 2 — Gateway API endpoints:

POST   /api/hub/publish          — publish a skill/MCP package (auth required)
GET    /api/hub/search?q=...     — search skills by name/tag/framework
GET    /api/hub/packages         — list all packages (paginated, filterable by framework/type/tag)
GET    /api/hub/packages/{name}  — package detail + versions + reviews
POST   /api/hub/install/{name}   — install a package into local LazyClaw
DELETE /api/hub/uninstall/{name} — remove installed package
POST   /api/hub/rate/{name}      — rate 1-5 stars
GET    /api/hub/stats            — total packages, downloads, top rated

PART 3 — Install flow for LazyClaw users:

- Telegram: /hub search scraper → shows results → /hub install web-scraper → auto-registers in skill registry
- CLI: lazyclaw hub search scraper, lazyclaw hub install web-scraper
- Web UI (later): browse/search/one-click install
- MCP servers auto-added to mcp/manager.py on install
- Skills auto-registered in skills/registry.py on install

PART 4 — Cross-framework compatibility:

- OpenClaw format: SKILL.md files in directories → auto-convert to our format on import
- MCP servers: Universal — any framework can use them (MCP is a standard protocol)
- Publish both ways: LazyClaw skills can export as OpenClaw-compatible SKILL.md

PART 5 — Storage:

- v1: SQLite table (skill_hub_packages) for local registry + JSON metadata files
- Published packages stored as .tar.gz in a packages/ directory
- Future: GitHub-backed registry (each package = a repo or gist)

DATABASE TABLE:
skill_hub_packages: id, name, version, author, description, tags (JSON), type, frameworks (JSON), mcp_config (JSON encrypted), install_command, downloads, rating_avg, rating_count, created_at, updated_at

skill_hub_installed: id, user_id, package_name, version, installed_at, config (JSON encrypted)

skill_hub_reviews: id, package_name, user_id, rating, comment (encrypted), created_at

IMPORTANT RULES:
- All user-generated content (reviews, custom configs) encrypted with existing crypto system
- Package metadata is plaintext (needs to be searchable)
- MCP servers installed via the hub should auto-start with LazyClaw
- Skills installed via the hub should appear in the skill registry immediately
- Version management: can install specific versions, upgrade, rollback

DO NOT:
- Modify existing built-in skills
- Change the core skill registry interface (extend it, don't replace)
- Change the MCP manager interface (extend it)
- Build a web frontend for the hub yet (that's Session D5)

VERIFY:
- Can publish a test skill package
- Can search and find it
- Can install it and see it in skill registry
- Can install an MCP server and see it auto-connect
- OpenClaw SKILL.md format imports correctly
- Rating and review system works
```

### DAY 4 — Voice Input via Whisper (Session D4)
```
READ CLAUDE.md first.

TASK: Add voice message support. When users send voice messages via Telegram (or later WhatsApp/Discord), transcribe them with OpenAI Whisper API and process as text.

1. In channels/telegram.py: handle voice messages and audio files
2. Create lazyclaw/voice/transcriber.py: Whisper API client (OpenAI API, reuse existing API key)
3. Flow: voice message → download audio → Whisper API → text → process_message()
4. Add WHISPER_MODEL=whisper-1 to config (default: whisper-1)

DO NOT add TTS (text-to-speech) yet — that's a separate feature.
```

### DAY 5 — Web Dashboard Panels (Session D5)
```
READ the web/ directory structure from Session W1.

TASK: Extend the web UI with dashboard panels that use the existing API endpoints.

Add these pages/tabs to the web UI:
1. Memory Panel — GET /api/memory/personal, DELETE, daily logs
2. Skills Panel — GET /api/skills, create/edit/delete
3. Jobs Panel — GET /api/jobs, create/pause/resume/delete, cron scheduling
4. Settings Panel — GET /api/eco/settings, PATCH, provider status
5. Vault Panel — GET /api/vault, set/delete credentials (show keys only, not values)

Each panel is a separate React component/page. Add tab navigation in the header.

DO NOT modify any backend files.
```

---

## LAUNCH CHECKLIST (before pushing to GitHub)

- [ ] README.md rewritten (we do this together in Cowork)
- [ ] .env.example is complete and has no secrets
- [ ] .gitignore covers: .env, *.db, __pycache__, .venv/, node_modules/, web/dist/
- [ ] LICENSE file (MIT) exists
- [ ] CHANGELOG.md exists (even if short)
- [ ] install.sh works on fresh machine
- [ ] `lazyclaw setup` runs without errors
- [ ] `lazyclaw start` launches gateway + telegram
- [ ] Web UI builds with `cd web && npm run build`
- [ ] No hardcoded API keys anywhere
- [ ] Remove any personal data from DB/config
