# Changelog

All notable changes to LazyClaw will be documented in this file.

## [0.2.0] - 2026-04-17

### Added

**Live browser control — visual feedback + user override**
- BrowserCanvas: new pinned panel in the Web UI chat sidebar that shows the
  live browser URL, action timeline (last 8 clicks / types / gotos), and a
  640px WebP thumbnail. Zero LLM tokens added — events flow UI-only via a
  per-user pub/sub bus (`lazyclaw/browser/event_bus.py`).
- Live mode toggle: per-user 5-minute flag that captures a fresh screenshot
  after every browser action instead of only on URL change. Fixes the stale
  frame bug when the agent uses accessibility-tree reads.
- `🔄 Refresh` button: force-captures a fresh thumbnail on demand.
- `💬 Help` button: type a mid-task instruction that routes through the
  existing side-note channel to the running agent.
- `🎮 Take control` / `🔗 Open VNC`: noVNC takeover via
  `remote_takeover.start_remote_session` — now exposed from the canvas and
  from any channel via the `share_browser_control` NL skill.
- URL-stamped thumbnails so the UI knows when cache is stale.

**Checkpoints — pause before risky actions**
- `request_user_approval` NL skill: agent calls before submit / pay / book /
  delete / sign / send. Blocks on a per-user asyncio.Event until the user
  hits Approve or Reject on the canvas (or `/api/browser/checkpoint/*`).
- Same checkpoint name auto-approves on re-call (no loop re-prompts).
- Soft-reject on 10-minute timeout.

**Saved browser templates — reusable recipes**
- `browser_templates` table (encrypted `system_prompt`, `playbook`;
  plaintext setup_urls, checkpoints, watch_extractor).
- 5 NL skills: `save_browser_template`, `list_browser_templates`,
  `delete_browser_template`, `run_browser_template`,
  `watch_appointment_slots`.
- REST: `GET/POST/PATCH/DELETE /api/browser/templates` + `/seed` +
  `/{id}/run` + `/from-current-session` + `/from-prompt`.
- Web UI: new `Templates` page with list / edit / run / watch / seed.
- Auto-capture: `template_synth.py` distils a draft (setup_urls +
  checkpoints + LLM-drafted playbook) from the live event bus when the
  user says "save this as a template".
- AI draft: ✨ Create-with-AI dialog on the Templates page drafts a
  template from a one-line description.
- Post-turn suggest: TemplateSuggestBanner appears in chat after a
  multi-step browser flow offering to save it.
- Seed recipes: Cita Previa Spain (DGT) + Doctoralia.
- Slot polling: `watch_appointment_slots` hooks a template's
  `watch_url` + `watch_extractor` into the existing watcher daemon —
  zero LLM tokens per check, Telegram push + canvas alert on trigger.

**MiniMax provider integration**
- New `minimax_provider.py` — OpenAI-compatible API at `api.minimax.io/v1`.
- Models: MiniMax-M2.7, minimax-m2.5 (+ highspeed variants kept for
  completeness).
- Subscription-priced — cost display forced to `$0`.
- Empty-response escalation for rate-limit error 2013 auto-falls-back to
  Claude.
- Confirmed working as a drop-in brain replacement for the same workload.

**Remote MCP + OAuth browser auth**
- `lazyclaw/mcp/oauth.py`: OAuth 2.1 + PKCE implementation.
- Encrypted token storage in the vault, auto-refresh on expiry.
- Streamable HTTP transport alongside stdio + SSE.
- `connect_remote_mcp` NL skill: "connect to Canva" opens Brave for
  login, catches the callback, registers ~20 tools.

**Watchers management page**
- New `Watchers` page in the Web UI for managing zero-token site monitors.
- `lazyclaw/watchers/history.py` tracks hits/triggers per watcher for the UI.
- `lazyclaw/gateway/routes/watchers.py` CRUD + /history + /run-now.
- Heartbeat daemon records hit history so the UI can show activity.
- Overview page shows a WatchersStrip with live counts.
- `TestWatcherSkill` bug fix: was calling non-existent `CDPBrowserBackend`
  — now uses `CDPBackend`.

**Task Manager (Second Brain)**
- Encrypted tasks with nagging reminders (15min → 30min → 1hr escalation,
  capped at 5 nags).
- Relative time parsing (`+10m`, `+1h30m`, `+1d`) — server-side, no LLM
  time math.
- User/agent task separation.
- Telegram inline buttons: Done / Snooze 1h / Tomorrow.
- Recurring tasks (daily/weekly/monthly) with auto-created occurrences.
- AI enrichment via `mcp-taskai` with graceful degradation.

**Other**
- ECO HYBRID worker migrated from Nanbeige/Qwen3 to Gemma 4 E2B on
  Ollama (custom Modelfile with agent identity baked in).
- MLX backend deprecated.
- Tool call icons — per-action icons for browser events (click, type,
  goto, scroll, screenshot, press_key, close_tab, checkpoint, takeover).
- Fast Dispatch: team lead responds in <2s, heavy work offloaded to
  TaskRunner.
- TUI dashboard with cost bar, AI routing panel, agent cards.

### Changed
- Skill count: 101 → 128 builtin skills.
- Web UI pages: 8 → 12 (added Templates; the original 8 count was stale
  and missed SkillHub / Replay / Audit / Activity which already existed).
- Gateway route files: 17 → 19.
- HYBRID ECO default worker: local Ollama → `lazyclaw-e2b` (Gemma 4 E2B
  custom Modelfile).

### Fixed
- 130+ silent `except: pass` blocks replaced with
  `logger.debug(..., exc_info=True)` — makes in-production debugging
  actually possible.
- Stale BrowserCanvas events on WebSocket reconnect — dropped when
  older than 5 minutes so a long-idle ring buffer doesn't mount a stale
  canvas on page reload.
- Password fields now masked in the browser event detail line so
  secrets don't leak into the UI log.

### Security
- New `SECURITY.md` with threat model, key hierarchy, and operational
  procedures.
- Security audit fixes across 15 files (3 CRITICAL, 12 HIGH, 4 MEDIUM):
  `SERVER_SECRET` startup guard, sandbox hardening, shell-exec fix,
  rate limiting, security headers, vault-endpoint protection.

## [0.1.0] - 2026-04-07

### Added
- E2E encrypted AI agent platform (AES-256-GCM on all user content)
- 14 core components: Gateway, Agent Runtime, Lane Queue, Skills, Channels, Browser, Computer, Memory, MCP, Crypto, Teams, Replay, Task Runner, TAOR Loop
- 101 registered skills with smart tool selection (4 base tools, dynamic discovery)
- 6 active MCP servers: TaskAI, LazyDoctor, WhatsApp, Instagram, Email, JobSpy
- 4 MCP servers disabled pending rebuild: Freeride, Healthcheck, APIHunter, VaultWhisper
- 3 ECO routing modes: HYBRID, FULL, CLAUDE
- Telegram channel adapter with admin chat lock
- Web UI: React 19 + Vite + Tailwind (8 pages: Chat, Overview, Skills, Jobs, MCP, Memory, Vault, Settings)
- WebSocket streaming for real-time agent responses
- Background task execution with Telegram push notifications
- TAOR loop (Think-Act-Observe-Reflect) with parallel tool execution
- 5-layer memory system: conversation, compressed summaries, daily logs, weekly rollups, encrypted facts
- Browser automation via CDP (Brave > Chrome > Chromium auto-detection)
- Encrypted credential vault
- n8n integration (6 management skills + workflow templates)
- CLI + TUI with live task progress widget
- BIP-39 recovery phrase for encryption key recovery
