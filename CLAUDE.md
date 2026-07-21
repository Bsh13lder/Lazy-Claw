# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Documentation

- **[DOCS.md](DOCS.md)** — Complete function & class reference + detailed implementation patterns. Keep updated when adding new modules.
- **[TODO.md](TODO.md)** — Phase plan with checkable items. All roadmap/status tracking lives here.

> **Memory isolation (2026-05-21):** Claude Code's auto-memory at `~/.claude/projects/.../memory/` is DELIBERATELY NOT referenced as a lazyclaw data source — cross-project session content was leaking into lazybrain via the (now-removed) `plan_ingest` hourly mirror. Lazyclaw's own knowledge lives in lazybrain (encrypted, MCP-exposed). Do NOT re-introduce any bridge that pulls `~/.claude/plans/*` or `~/.claude/projects/*` into lazybrain.

## File Size Rules

- **CLAUDE.md must stay under 40,000 characters.** This file is loaded every message — keep it lean.
- **Never dump file maps, API endpoints, DB schemas, env vars, or CLI command lists here.** Those are derivable from the codebase or already in DOCS.md.
- **Use TODO.md** for roadmap, phase plans, task tracking, and implementation status.
- **Use MEMORY.md** for learned patterns, user corrections, project context, and references.
- **Use DOCS.md** for detailed implementation patterns, dated commit notes, and per-module deep dives.
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
- **Edit SOUL.md before adding runtime nudges**: When brain misbehaves, the FIRST lever is personality/SOUL.md, not new code/regex/thresholds.

---

## Project Overview

**LazyClaw** is an open-source (MIT) E2E encrypted AI agent platform written in Python (FastAPI + asyncio + aiosqlite). Competes with OpenClaw by offering AES-256-GCM encryption on all user data, native MCP support, a Python-native skill system, and multi-channel messaging.

> "OpenClaw, but encrypted and Python-native."

**Status**: Early beta (v0.1). Solo developer, daily updates. Optimized with Claude Code — architecture reviewed and iterated continuously.

### Key Differentiators vs OpenClaw
- **E2E Encryption**: AES-256-GCM on all user content. OpenClaw stores everything in plaintext.
- **Python-native**: Full Python stack. Python AI ecosystem is 10x larger than TypeScript.
- **Native MCP**: First-class MCP client AND server, bridged into the skill registry as the backbone (not an add-on). OpenClaw largely closed the raw-support gap in 2026.7.x (per-session MCP isolation) — don't lean on the old "hacky converter" claim.
- **Encrypted Credential Vault**: API keys stored encrypted, not plaintext .env files.

## Architecture

17 modules in `lazyclaw/` + supporting infrastructure:

| Component | Path | Purpose |
|-----------|------|---------|
| **Gateway** | `gateway/` | FastAPI HTTP+WS entry point. Session auth, CORS, routing |
| **Agent Runtime** | `runtime/` | TAOR agent loop, context builder, tool dispatch, task runner, team lead, goal executor |
| **Lane Queue** | `queue/` | FIFO serial execution per user session |
| **Skills** | `skills/` | Instruction (NL), Code (sandboxed), Plugin (pip). ~280 builtin skills (registry.py) incl. survival/bounty/goal/lazybrain/browser sub-packages |
| **Channels** | `channels/` | Telegram native adapter + WhatsApp/Instagram/Email via MCP servers |
| **Browser** | `browser/` | CDP-only browser control, JS extractors, site memory, multi-account profiles, per-domain cadence |
| **Computer** | `computer/` | Native subprocess + WebSocket connector (remote) |
| **Memory** | `memory/` | Encrypted personal facts, conversation history, compression, daily/weekly logs |
| **MCP** | `mcp/` | Native client + server + bridge to skill registry |
| **Crypto** | `crypto/` | AES-256-GCM, PBKDF2, credential vault |
| **Teams** | `teams/` | Specialists (browser, research, code) + delegate skill + parallel execution |
| **Replay** | `replay/` | Session trace recording, playback, shareable tokens |
| **Tasks** | `tasks/` | Encrypted task store with CRUD, nagging reminders, recurring tasks |
| **Notifications** | `notifications/` | Telegram push notifications for background tasks |
| **Comms** | `comms/` | Unified cross-channel inbox — ChannelGateway (read/send over MCP channel tools), encrypted `channel_threads` store (PII handles encrypted + HMAC dedup hash), `/api/inbox/*` routes, mobile inbox backend. Phase E (AI conversation runner) pending |
| **Pipeline** | `pipeline/` | CRM-style pipeline store for workflow tracking |
| **Survival** | `survival/` | Gig economy tools — job matching, applications, invoices, contract intake |
| **LazyBrain** | `lazybrain/` | Python-native Obsidian-grade PKM — encrypted notes + wikilinks + force-directed graph + daily journal + auto-capture + callouts + transclusion + canvas. Single home for every memory source with `owner/{user,agent}` + kind tags. AI-native (autolink, semantic_search, RAG ask, topic_rollup, morning_briefing). 28 NL skills. See DOCS.md for details. |
| **Documents** | `sheets/` `docs/` `pdf/` | Private encrypted office suite. Univer Sheets + Docs (one `enc:v1` Univer `IWorkbookData`/`IDocumentData` JSON blob per file — same pattern as `canvas.py`) + a permissive PDF toolkit (pypdf/reportlab/pdfplumber/pikepdf). One "Docs" web workspace (3 sub-tabs); agent skills create/read/edit/export/send. See DOCS.md. |

Supporting: `llm/` (multi-provider router + ECO mode + Claude SDK/CLI provider), `heartbeat/` (cron daemon), `permissions/` (allow/ask/deny + audit), `db/` (aiosqlite + connection pool), `web/` (React 19 + TypeScript + Vite + Tailwind), `mobile/` (Flutter Android/iOS client — offline-first, see Mobile App pattern below), `n8n-custom/` (n8n webhook integration).

Standalone MCP servers (7 active): `mcp-taskai/`, `mcp-lazydoctor/`, `mcp-whatsapp/`, `mcp-instagram/`, `mcp-email/`, `mcp-scraper/`, `mcp-upwork/` (Apache-2.0 fork of vanooo/upwork-mcp — shares Brave profile + cookies). `mcp-jobspy` retired 2026-05-14.

## Build & Run

```bash
./install.sh              # One-command install (Python + deps + setup)

# Or manually:
pipx install --editable . # Global install via pipx
lazyclaw setup            # First-time setup wizard
lazyclaw start            # Full server (FastAPI + Telegram + Heartbeat)
lazyclaw                  # Chat REPL only
```

Default port: **18789**. MCP servers run standalone via `python -m mcp_taskai` etc.

## E2E Encryption

All user content encrypted before storage. Server never sees plaintext.

- Registration generates random `encryption_salt` per user
- Key derivation: `PBKDF2(password, salt, 600k iterations, SHA-256)` → per-user DEK (Data Encryption Key)
- Envelope encryption: DEK itself stored encrypted with server master key
- Storage format: `enc:v1:<base64-nonce>:<base64-ciphertext>`
- Server-side key for daemon ops: `PBKDF2(SERVER_SECRET + user_id, fixed_salt, 600k)`
- **Recovery phrase**: BIP-39 mnemonic generated at registration — user can re-derive their key
- **Encrypted**: conversations, memory, skills, vault, jobs, channel configs, tool-call arguments (`memory/metadata_codec.py`, tolerant read + backfill, 2026-06-10)
- **Plaintext** (needed for queries): IDs, timestamps, status, cron expressions, domains

## Key Patterns

These are non-obvious architectural decisions that apply everywhere. For dated commits, per-module deep dives, and detailed implementation notes, see **DOCS.md → Implementation Patterns Reference**.

### Runtime & Routing
- **User isolation**: ALL queries scoped by `user_id`. No cross-user data access.
- **No hardcoded tools**: All tools from skill registry. Agent discovers dynamically via `search_tools()`. Only 4 base tools sent per message (search_tools, recall_memories, save_memory, delegate). ~95% token savings vs sending all 195+ tools.
- **Lane Queue**: Serial per-user foreground execution. Background tasks run in parallel via TaskRunner.
- **Brain-as-dispatcher**: Mid-turn pivot detector in `runtime/agent.py` re-routes the brain back to dispatch when it starts doing work itself. `dispatch_subagents` is non-blocking; both parallel `run_background` AND `dispatch_subagents` results now auto-consolidate into ONE final reply via TaskRunner brain-fanout (`register_subagent_fanout`/`record_subagent_result`, fired without needing a follow-up user turn). AUTO-PROMOTE skips turns that already dispatched. See DOCS.md → "dispatch_subagents consolidation (2026-05-29)".
- **Specialist-first dispatch (ADR-0005, in progress)**: shift from a 277-tool generalist toward a thin-router brain + declarative specialists. Builtin specialists are `.md`+YAML files in `teams/specialists/` loaded by `teams/specialist_loader.py` (reuses `lazybrain/frontmatter`); custom ones stay encrypted in DB. Web/mobile CRUD over `/api/specialists`. Auto-improving via `runtime/specialist_lessons.py` (ADR-0002 loop). Research-first fan-out (`runtime/research_fanout.py`) feeds the plan gate, opt-in `LAZYCLAW_RESEARCH_FANOUT=1`. **Thin-router (2026-06-08)**: `delegate` = dispatch-and-exit; 1-action routing cap behind `LAZYCLAW_THIN_ROUTER` (SOAKING in prod since 2026-06-08 — after one inline non-meta tool, brain narrows to meta-only so it MUST delegate; chat-responsive failsafe). Specialist allowlists are the load-bearing contract: `search_tools` discovery does NOT grant callability — a delegated tool missing from the `.md` allowlist = stuck-loop death (2026-06-10 freelance incident); `startup_specialist_self_check` warns on drift at boot; specialist allowlist now matches MCP tools by bare suffix (restored Upwork submit); mechanical F1 now runs in the specialist path (`runtime/f1_gate.py`). Brain teardown (delete only AUTO-PROMOTE trigger + inline pivot; keep dedup/keyword-gating/stuck/failsafes) deferred pending prod soak. See ADR-0005 + DOCS.md.
- **Operating modes (ADR-0005)**: per-user posture in `runtime/agent_mode.py` layered OVER permissions. Display labels (relabel 2026-06-08, no value migration) — **Ask** (no tools) · **Plan** (read-only+gate) · **Action** (default = status quo) · **Execute** (autonomous, ask→allow); internal enum values stay `chat/ask/plan/auto` unchanged, `parse_mode_label` accepts canonical+legacy names. Enforced ONLY in `PermissionChecker.check_effective` (hot path untouched); default = zero behavior change. Stored at `users.settings.general.agent_mode`; set via Telegram `/act`, web/mobile mode switch.
- **Unified `agent` tool (2026-07-07)**: `agent(agent_type, task, run_in_background)` = Claude Code dispatcher pattern — sync parallel fan-out (≤15/turn, concurrency 6, 12K result cap, results return in-turn as tool results) via `run_specialist`; bg path = TaskRunner consolidation; `explore`/`general_purpose` now declarative `.md` specialists (`tools: "*"` wildcard, dispatch-denylisted, native-primacy enforced); legacy delegate/dispatch_subagents/run_background demoted during soak. See DOCS.md → "Unified `agent` dispatch tool".
- **TAOR loop**: Think-Act-Observe-Reflect cycle in `taor.py`. Independent tools run concurrently via `asyncio.gather`.
- **Fast chat path**: Simple messages get last 6 messages + SOUL.md only (no capabilities/memories/tools).
- **Slim heartbeat path**: Tier-A reminders skip SOUL.md + 95% of tools — agent loads only reminder-relevant tools.
- **5-layer memory**: live messages → sliding window (15 full) → daily summary → weekly rollup → long-term encrypted facts. Merged in `context_builder.py`. Never re-summarizes mid-session.
- **Hybrid memory picker**: 5 by importance + 5 by keyword overlap with current message. Zero extra LLM cost.
- **Stuck detector**: Limits consecutive same-tool calls. `lazybrain_*`, `email_*`, `whatsapp_*`, `instagram_*` allowed 10 (batch ops); others 3.

### LLM Routing (ECO)
- **4 modes, 3 roles** (Brain/Worker/Fallback) in `llm/eco_router.py`:
  - **HYBRID** (default): Sonnet brain + `gemma4:e2b` local worker via Ollama ($0) + Haiku fallback
  - **FULL**: Sonnet brain + Haiku workers + Sonnet fallback
  - **CLAUDE**: every role through subscription ($0), `claude_transport` = `sdk` (default) | `cli`
  - **MINIMAX**: M2.7 brain+worker (Token Plan) + Haiku fallback
- Models from `MODE_MODELS` dict in `model_registry.py`.

### Browser
- **Unified browser tool**: Single `browser` skill with 7 actions (read, open, click, type, screenshot, tabs, scroll). CDP-only, no Playwright.
- **Brave > Chrome > Chromium**: auto-detected. Built-in ad/tracker blocking = cleaner pages.
- **Shared browser profiles**: CDP uses `browser_profiles/{user_id}/` with system browser. Login once → all tools see cookies.
- **Multi-account profiles**: `browser/profile_resolver.py` — `resolve_profile_dir(config, user_id, account_slug=None)`. Slug routes to isolated `<db>/browser_profiles/<user_id>/accounts/<slug>/`. Registry in `users.settings.browser.accounts`.
- **Per-domain cadence**: `browser/cadence.py` — frozen `CadenceProfile` with 8 axes. User override → DOMAIN_OVERRIDES → DEFAULT. Bot-sensitive sites 1.5–1.6× slower. NL skill `tune_browser_cadence`.
- **Semantic Snapshots**: Accessibility tree text (50KB) instead of screenshots (5MB).
- **Browser event bus** (zero-token UI observability): `browser/event_bus.py` — events NEVER enter LLM context. UI-only.
- **Checkpoints**: `browser/checkpoints.py` + `request_user_approval` skill blocks before risky actions (submit/pay/book/delete/sign/send).
- **Saved templates**: `browser/templates.py` — encrypted playbooks with checkpoints + watch extractors. Ships seed recipes.
- **Live mode**: `/api/browser/live-mode/start` — 5-min flag making cdp_backend capture fresh thumbnail after every action.
- **Tab reaper** (heartbeat every 5 ticks): closes idle tabs, enforces cap, refreshes white screens. Anchored watcher tabs always preserved.
- **Live-browser watcher routing**: Cloudflare-protected hosts (upwork.com, linkedin.com, + user extras) MUST poll through user's signed-in Brave on `cdp_port`. Fresh headless fails fingerprint silently. Watchers run passively — no focus-steal, no tab-steal.

### Storage & Performance
- **DB connection pool**: Single shared aiosqlite connection (14ms → 0.2ms per query).
- **PBKDF2 LRU cache**: Key derivation cached (420ms → 0ms per message).
- **MCP parallel startup**: `asyncio.gather` connects all MCP servers simultaneously (~2s vs 12s).

### Documents (Sheets / Docs / PDF)
- **One encrypted blob per file**: Sheets/Docs persist the Univer snapshot (`IWorkbookData` / `IDocumentData`) as a single `enc:v1` JSON blob — same atomic pattern as `lazybrain/canvas.py`, NOT a per-cell/per-glyph schema. PDFs store `enc:v1(base64(bytes))` in `pdf_files`. Stores live in `sheets/store.py`, `docs/store.py`, `pdf/store.py`.
- **Univer Pro sidestep**: Univer's native xlsx/docx import-export + charts + live-collab are Pro-gated. We keep the free Apache-2.0 editor and convert server-side — `sheets/xlsx_io.py` (openpyxl), `docs/docx_io.py` (python-docx; LibreOffice headless for doc→pdf, returns 503 if `soffice` absent). `sheets/recalc.py` evaluates agent-edited formulas via xlcalculator (Univer recalcs in-browser).
- **PDF reality**: reflow text-editing is unsupported by every permissive tool — `pdf/ops.py` does fill-form / overlay-text+sign / merge / split / rotate / extract / redact(visual-mask only) / generate. Web is view+manage only; the agent does the edits.
- **License discipline (CRITICAL)**: permissive only — Univer (Apache-2.0), openpyxl/xlcalculator/python-docx/pdfplumber (MIT), pypdf/reportlab (BSD), pikepdf (MPL). NEVER HyperFormula/Handsontable/PyMuPDF/borb (AGPL/commercial) — they'd poison the MIT licence. Every existing PDF MCP server secretly wraps PyMuPDF; we wrap pypdf/reportlab ourselves.
- **Agent skills**: `create_sheet/doc`, `read_*`, `set_cells`/`set_formula`/`append_to_doc`/`fill_pdf_form`/`merge_pdfs`/`generate_pdf`, `send_*` (deliver via `push_telegram_document` + web-download fallback). Categories `sheets`/`docs`/`pdf` default ALLOW.
- **Web**: one lazy "Docs" workspace (`web/src/pages/Documents.tsx`) with Sheets/Documents/PDF sub-tabs, each its OWN lazy chunk so Univer (~10 MB) and pdf.js load only when their sub-tab opens. The `univer-*` chunk is excluded from the PWA precache (`vite.config.ts` globIgnores). WS needs the browser Origin in `CORS_ORIGIN` (else "disconnected").
- **In-editor AI specialist (2026-06-04)**: ✨ popover in every editor header (`web/src/components/DocAiPopover.tsx`) → `POST /api/{sheets,docs,pdf}/{id}/ai` → `runtime/doc_specialist.py`. **Synchronous** (NO chat/background/consolidator — sidesteps the Telegram-leak class). Pattern = LLM→strict-JSON edit plan→deterministic apply via per-kind `{docs,sheets,pdf}/ai_edit.py` (Strategy; domain never imports runtime); worker model then ONE brain retry on parse fail. Sheets/Docs return the fresh snapshot (editor flushes pending edits, then remounts via a `reloadToken`, `dirtyRef=false` so dispose-flush can't clobber the agent edit); PDF returns `new_pdf_id` (ops are immutable). See DOCS.md → "In-editor AI Document Specialist".
- **Doc hyperlinks ("text with a link")**: `docs/snapshot.py` carries Univer `customRanges` (sentinel-bracketed `dataStream` + `properties.url`); `docx_io.py` round-trips `w:hyperlink`; `append_to_doc` takes `link_text`/`link_url` + `[md](url)`; Docs editor mounts `UniverDocsHyperLinkPreset`. See DOCS.md → "Documents Workspace".
- **Sheets next-level pass (2026-06-12)**: optimistic-concurrency saves (`base_updated_at`→409; no field = LWW so agent skills unaffected), sheet hyperlinks + converter, plaintext `tags`, web's free Univer preset set, mobile native power-grid. **Detail: DOCS.md.**

### Channels & Security
- **Telegram security**: Admin chat lock (first /start claims). Unauthorized chats blocked. Exponential backoff retry on network errors.
- **CancellationToken**: Cooperative cancellation from CLI → agent → specialists.
- **Default permissions**: 32 categories `allow`, 5 sensitive `ask` — canonical map in `permissions/models.py:DEFAULT_CATEGORY_PERMISSIONS`. Telegram `/allow` `/deny` `/permissions` gate skills.
- **Money-mover ask-back (2026-06-10)**: `SENSITIVE_SKILL_DEFAULTS` in `permissions/models.py` → `upwork_accept_offer`/`upwork_submit_milestone`/`payment` ASK despite blanket-ALLOW `mcp`; Telegram got a real fail-closed inline ask-back (was auto-approving every bare ASK); mcp-upwork `draft_only` defaults True; audit log wired; `vault_get` added. DOCS.md → "Money-mover hardening".
- **Quiet scheduled pushes**: `TelegramNotifier(verbose=False)` for cron/reminder/watcher — drops stats footer + tools-used line so scheduled messages read as normal text.

### Search & Extraction
- **Web search chain**: Brave Search API → mcp-scraper → DuckDuckGo. `BRAVE_KEY` env. Serper/SerpAPI deleted.
- **Price queries auto-route to browser**: `web_search.py` detects price intent → returns `[PRICE_QUERY]` with canonical URL → brain dispatches `browser(action="open", url=...)`. Never trust API snippets for prices.
- **JSON-LD-first business extraction**: `mcp-scraper/.../extraction_business.py` parses schema.org JSON-LD with `confidence: high|medium|low|none`. Brain MUST refuse `confidence='none'` and try `/contact` or `/about` subpage.
- **mcp-scraper bundle**: single-subprocess crawl4ai bundle. Auto-dismisses Cookiebot/OneTrust/Iubenda/Quantcast banners.

### Goal & Survival Pipeline
- **Goal Executor**: `runtime/goal_executor.py` — autonomous high-level objectives. States: `DRAFTING → AWAITING_USER_INFO → EXECUTING → DONE/BLOCKED/FAILED/ABORTED`. Encrypted `goals` table. Key UX: questions surface in ONE batch upfront. 7 skills.
- **Unified Code Session per project (P1)**: code-tagged goals own ONE persistent worker session. `goals.code_session_id` is set on the FIRST `eco_router.chat` response (via `runner.py:on_session_id` latching callback) and replayed as `--resume <id>` on every subsequent dispatch. Code goals stay EXECUTING across turns (no auto-DONE on success); user iterates via `continue_code_goal(goal_id, instruction)` which fires `code_goal_executor.dispatch_code_goal` again — same workspace dir, same worker session, slim continuation brief (no plan replay). Stale-resume defense: runner retries ONCE with a fresh session on session-not-found-style errors. `_CONTINUATION_INSTRUCTIONS` side-channel carries turn-scoped instruction to avoid mutating frozen Goal. **Route all code work through `start_goal(work_type='code')` or `continue_code_goal(...)` — NEVER `run_background` (its Claude CLI is `--disallowedTools Bash,Read,Edit,Write,...` and will hang silently).** See MEMORY → `feedback_code_tasks_via_claude_code_mcp`.
- **Contract intake pipeline**: `survival_contract_intake` cron (every 6h) → `upwork_contract_poll` dedups via `seen_contract_urls` → fires `new_contract_intake(contract_url)` → worker LLM classifies work_type → wraps `GoalExecutor.start()`. On answer completion, `contract_intake_executor.dispatch_contract_intake` provisions vault/live_host/account/watcher/template/setup-doc. Each work_type has its own checklist.
- **Telegram 1-tap-accept**: contract-intake watchers fire `🔔` + InlineKeyboard `✅ Accept` / `⏭ Skip`. Accept branch sets template active for next browser turn.

### Upwork-Specific Hardening
- **Upwork DMs forbid all links**: `mcp-upwork/.../messages.py:send_message` hard-blocks URLs + `\blazyclaw\b` BEFORE browser nav. Returns `{status: "blocked", offending_token}`. Upwork's chat filter deletes link-bearing messages bilaterally.
- **Never pitch lazyclaw as a product on Upwork**: Describe the WORK + STACK, never name LazyClaw. Force "personal" branding on Upwork regardless of stored mode.
- **Tiptap-safe typing**: `_type_with_soft_breaks(text)` splits on `\n`, uses `Shift+Enter`. Raw `keyboard.type(multi_line_text)` fires Enter as Send → 10-line draft = 10 separate bubbles.
- **mcp-upwork startup self-check**: `server.py:_self_check()` aborts with exit 2 if the deployed `messages.send_message` source doesn't reference `_type_with_soft_breaks`. Prevents stale Docker images silently re-introducing the 8-bubble Tiptap fragmentation incident (2026-05-17 14:01). Banner visible in stderr on every MCP start.
- **Sender-flip-aware bubble parser**: `_extract_message` in `mcp-upwork/.../messages.py` determines `is_mine` from the bubble's class hint FIRST, then refuses to carry forward the prior sender across a speaker flip (was wrongly tagging Vato's reply as James Blue, contaminating every downstream plan). Falls back to `me_name` / `contact_name` on flip.
- **Scroll-to-bottom before parse**: `get_conversation_messages` forces the virtualized chat list to load the newest bubbles (`scrollTop = scrollHeight`) + polls for render stability (count stable across 3 ticks, ~600ms) before extraction. Without this, a fresh-out-of-restart MCP read returns stale bubbles because Upwork restores prior scroll position and the bottom bubbles aren't mounted.
- **Cloudflare resilience**: `_NAV_LOCK` + `_pick_upwork_page()` (prefers existing on-upwork.com tab — cookies pass CF silently) + `safe_goto()` with 15s CF-pass retry.
- **Sonnet read-only-list dedup**: `_dedup_tool_calls` in `claude_sdk_provider.py` collapses 22 listing tools to FIRST occurrence regardless of args. Catches exploration loops.
- **`upwork_last_conversation` skill (with `contact_name`)**: zero-LLM deterministic fetch. Optional `contact_name="James"` finds a specific thread by fuzzy match (substring + first-name prefix), widens inbox scan to 50 entries when provided. Description flags it for planning/scoping/reply intents so the brain surfaces it on "what does X want / let's plan that job."

### Brain Grounding on Live Reads — CRITICAL
- **SOUL.md NEVER rule (line 13)**: when the user (or a background instruction) names a contact + a channel and asks to PLAN / SCOPE / ESTIMATE / REPLY / QUOTE / FIND / EXTRACT / FETCH / READ / CHECK / SHOW / SUMMARIZE / RECAP, the FIRST tool call MUST be the channel read (`upwork_last_conversation`, `whatsapp_read`, `email_read`, `instagram_read_dms`, or the raw `*_get_conversation` / `*_get_messages` MCP tool). Never plan from memory.
- **Quote-then-summarize (F1)**: AFTER the channel read returns, reply MUST begin with verbatim quotes of the 3 most recent contact-side messages (`> {sender} ({timestamp}): {exact content}`), character-for-character. Only THEN may you summarize. Every concrete claim (platform, dollar amount, deadline, scope item) must trace to a quoted line. Forbidden: speculating about platforms / services / industries the contact didn't write (no "DoorDash? Uber? TaskRabbit?" fabrication). Targets Opus 4.6/4.7 in-context-learning bias documented in [anthropic/claude-code#29230](https://github.com/anthropics/claude-code/issues/29230).
- **Most-recent-wins (F2)**: when the same contact contradicts themselves across messages, the MOST RECENT message is authoritative. Never silently merge contradictory facts. Surface the supersession explicitly: *"James narrowed the city list at 10:37 PM — the new 6-city scope overrides the earlier 20-city list."* Applies to every revisable fact: scope, deadline, budget, requirements, deliverables, target list.
- **AUTO-PROMOTE readonly allowlist** (`agent.py:_is_readonly_inspection`): `upwork_last_conversation`, `upwork_inbox_check`, `upwork_contract_poll`, `find_contact`, `list_contacts`, `list_memories`, `lookup_project_asset` are recognized as read-only fetches. AUTO-PROMOTE skips iter=2 force-promotion when only these have been called, so the foreground brain can synthesize a reply from the data it just fetched instead of being kicked into background.
- **Channel-tool injection covers upwork shape**: `_CHANNEL_CORE_SUFFIXES` in `agent.py:418` includes `_get_messages`, `_get_conversation`, `_get_unread_count`, `_check_session`, `_get_my_profile`, `_get_proposals`, `_get_contracts`. Without these, upwork read tools get silently dropped when no "action verb" is in the user message.

### Grounding & History — dated root-cause passes (2026-05-20 → 05-25)
Full narratives moved to DOCS.md ("Grounding root-cause pass (2026-05-25)" + the four "Hallucination defense" sections, 2026-05-20/23). Load-bearing rules that stay: per-tool result caps — channel reads get 50 KB vs 4 KB default; **before patching any brain-output detector on a "brain wrong" report, decrypt the `agent_messages` tool row + grep `[truncated`** (marker present → upstream bug, not the brain). Lazybrain recall is wrapped `[CACHED PARAPHRASE — NOT a live channel quote]` with sender/timestamp shapes mangled. Wikilink `[[X]]` inside a contact quote = memory-leak signal (humans never send wikilink syntax) → F1 forces rewrite; `quarantine_polluted_history` scrubs confabulated assistant rows at context build so history can't re-teach the leak.

### Mobile App (Flutter — `mobile/`)
- **Donor-harvested from `taskbot_flutter`** (sibling app), then rebuilt. Riverpod + go_router + Dio (cookie `session_id`) + `web_socket_channel`. **No client transport crypto** — the server decrypts; the app holds only the session cookie + a device-local SQLCipher key.
- **Premium design system at `mobile/lib/ui/`**: tokens (`AppColors`/`AppText`/`AppSpacing`/`AppRadii`/`AppMotion`, bundled Inter) + `buildAppTheme()` + an `Lz*` component kit + custom `LzBottomNav`. EVERY screen consumes only the kit — never hard-code colors/sizes/text styles. Build the kit FIRST, then fan parallel screen agents off it for coherence.
- **6 tabs** (Home · Chat · Tasks · Money · Notes · Settings) + a `/more` **Power-tools hub** (Skills/Vault/Memory/Jobs/Watchers/MCP/Audit). `StatefulShellRoute.indexedStack` keeps the chat WS + provider state alive across tabs.
- **Offline-first** (Tasks/Notes/Budgets): encrypted local SQLite (`sqflite_sqlcipher`, key in Android Keystore) + outbox + sync engine (push/pull, **last-write-wins by `updated_at`**, conflicts logged never dropped, client-minted UUIDs, dead-letter on 5xx). Backend sync primitives per domain: `deleted_at` tombstone + optional client `id` on create + `GET /api/<domain>/changes?since=`. Chat + power-surfaces are online-only. **Sync-engine lesson: make fake transports throw the PRODUCTION exception shape (`DioException(error: ApiError)`) or tests green-light data-loss bugs; re-review NOVEL multi-entity sync (Budgets) even when it "mirrors" a fixed engine.**
- **Gateway auto-discovery (2026-07-19/20)**: `core/config/lan_discovery.dart` — when every concrete candidate is dead, a bounded TCP /24 sweep of the phone's own interfaces (WiFi/hotspot-AP/USB-tether only, never cellular) finds the gateway wherever DHCP put it, identity-confirms via `/api/health` `status:ok`, persists it (cap 5) and registers it in `ServerAliasRegistry` so the URL switch can't force a re-login. Ladder (concrete FIRST, mDNS LAST): override → last-known-good → DuckDNS:8443 → LAN IP → discovered → `127.0.0.1:18789` (adb reverse) → **LAN sweep** → `.local` (mDNS is last-resort — Dart's HttpClient resolves `.local` only intermittently on Android; a flaky mDNS host must never suppress discovery or beat a real IP). Sweep wired ONLY in `main()` (tests can't fire real network).
- **"Reported reachable" ≠ "reachable" (2026-07-20)**: cold-start `checkSession`/reachability probes can fire before the network/URL settle and fail; if the seed already equals the resolved host the URL never "changes", so a change-gated re-check never re-runs → app stuck showing "Offline" on Home while the chat WS (reads `baseUrlProvider` fresh at mount) is connected. Fix: `main.dart:_reresolveAndRevalidate()` re-runs `checkSession` + `reachability.refresh` UNCONDITIONALLY after every reresolve (post-frame AND resume). Also `Reachability.refresh` no longer gates the `/api/health` ping behind `connectivity_plus` `hasLink()` (false-negatives on some ROMs) — the server's own answer is authoritative.
- **APK delivery**: `scripts/build-mobile-apk.sh` publishes `mobile/dist/app-release.apk` + `version.json`; gateway `routes/mobile.py` serves `/api/mobile/{apk,version}` (Docker mounts `./mobile/dist:/app/mobile/dist:ro`); web Settings "Mobile App" tab = Download + QR. **Self-hosted gotchas:** in-app server URL must be the host's LAN IP or `<host>.local:18789` (default `127.0.0.1` = the phone itself); release APK needs explicit `INTERNET` + `android:usesCleartextTraffic=true` + `isCoreLibraryDesugaringEnabled` (Flutter only adds these to DEBUG). Specs: `docs/superpowers/specs/2026-06-04-lazyclaw-flutter-design.md` + `2026-06-06-lazyclaw-flutter-ui-overhaul.md`. Deep dive: DOCS.md.

### Misc
- **Smart-intake task suggester**: `tasks/smart_intake.py` — worker LLM (3s hard timeout, graceful Ollama-down fallback) suggests deadline + project for new tasks.
- **Task escalation**: `tasks/store.py` schedules advance reminders for important tasks (priority high/critical → 1h, 15m, due-now nags).
- **Auto-promote backgrounds**: `task_runner` auto-promotes stuck foreground task to background runner when exceeding foreground budget.
- **MCP bridge**: External MCP tools registered as first-class skills. No separate path.
- **Agent Skills compatibility**: Skills in Claude Code agent format (YAML + markdown) importable via `lazyclaw skill import`.
- **Docker Claude CLI persistence**: `docker-compose.yml` mounts named volume at `~/.claude` so `claude login` persists across `down/up`.
- **Host Brave bridge**: `scripts/install-host-brave-bridge.sh` — launchd plist runs Brave with user profile + CDP token. `make host-bridge && make rebuild` to activate. Container Chromium fallback when no host browser found.
- **Host Awake bridge**: `scripts/install-host-awake-bridge.sh` — **root** LaunchDaemon (`/Library/LaunchDaemons/sh.lazyclaw.awake-bridge.plist`) on port **18791**. Controls `caffeinate` (lid-closed no-sleep) and `pmset` (hardware wake alarms). Root required for `pmset schedule/repeat`. `make awake-bridge` (one-time sudo). Container client: `lazyclaw/host/awake_client.py`. NL skill: `awake_mode`. Heartbeat self-heals every tick via `daemon._reconcile_awake_mode()`. Settings stored in `users.settings.general.awake`. Web UI: AwakeBadge in Header + Power tab in Settings.

## Git Commit Rules

- **No Co-Authored-By**: Do NOT add "Co-Authored-By: Claude" or any AI attribution to commits
- Keep commit messages clean and human-style
