# Specialist-First Dispatch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 277-tool generalist brain with a thin router that delegates
to declarative, context-isolated, self-improving specialists running parallel
Opus 4.8 in Claude mode, with Claude-Code-style operating modes (Chat / Ask /
Plan / Execute).

**Architecture:** See `docs/adr/0005-specialist-first-dispatch.md`. Incremental,
feature-flagged, specialist-by-specialist. No phase removes a brain-side defense
before its replacement exists in a specialist. Dead code is deleted as it is
superseded — nothing left commented-out or orphaned.

**Tech Stack:** Python 3.11 / asyncio / aiosqlite / pytest. Reuses
`lazybrain/frontmatter.parse_frontmatter`, `teams/runner.run_specialist`,
`runtime/dispatcher`, `runtime/task_runner` brain-fanout, `runtime/plan_research`,
ADR-0002 skill-lesson loop, `permissions/checker`, `request_user_approval`.

> **Status (2026-06-07):** Phases 1–4 + 6 implemented, 156 backend tests green.
> Phase 5 (thin-router brain teardown) DEFERRED behind `SPECIALIST_FIRST_BRAIN`
> until the grounding-defense migration (5a) lands. Not committed; not yet
> `make rebuild`. Web/mobile need build verification on a real toolchain.

---

## Phase 1 — Declarative specialist loader (DONE 2026-06-07)

**Why first:** purely additive, no behavior change, smallest blast radius. Proves
the `.md` format and gives every later phase a file to read/edit.

**Files:**
- Create: `lazyclaw/teams/specialists/browser_specialist.md`
- Create: `lazyclaw/teams/specialists/code_specialist.md`
- Create: `lazyclaw/teams/specialists/research_specialist.md`
- Create: `lazyclaw/teams/specialist_loader.py`
- Modify: `lazyclaw/teams/specialist.py` (BUILTIN_SPECIALISTS now loader-driven;
  inline string defs deleted; public interface unchanged)
- Modify: `pyproject.toml` (`[tool.setuptools.package-data]` ships the `.md` files)
- Test: `tests/test_specialist_loader.py`

**`.md` format (frontmatter via `parse_frontmatter`, body = system_prompt):**
```markdown
---
name: browser_specialist
display_name: Browser Specialist
model: smart
include_scraper: true
tools: [browser, web_search, save_site_login, payment, search_tools, google_run_task]
---
You are a browser automation specialist ...
```

**Frontmatter → `SpecialistConfig` mapping:**
- `name` (required, non-empty) → `name`
- `display_name` (optional, default `name`) → `display_name`
- `model` (optional, default `None`) → `preferred_model`
- `include_scraper` (optional bool, default `False`) → `include_scraper`
- `tools` (optional list, default `()`) → `allowed_skills`
- body (required, non-empty) → `system_prompt`
- `is_builtin=True` for everything loaded from the in-repo dir.

**Invariants the tests lock (behavior must be byte-identical to the old inline defs):**
- `BUILTIN_SPECIALISTS` still has exactly browser / code / research, in that order.
- `browser_specialist`: `preferred_model == "smart"`, `include_scraper is True`,
  `allowed_skills == ("browser","web_search","save_site_login","payment","search_tools","google_run_task")`.
- `code_specialist`: `preferred_model is None`, `include_scraper is False`,
  `allowed_skills == ("calculate","create_skill","list_skills","delete_skill")`.
- `research_specialist`: `preferred_model is None`, `include_scraper is True`,
  `allowed_skills == ("web_search","browser","read_file","list_directory","run_command","search_tools","google_run_task")`.
- Malformed `.md` (no frontmatter / empty body / missing name) → `ValueError`.
- Existing `tests/teams/test_explore_specialist_has_scraper.py` and
  `tests/teams/test_specialist_scraper_visibility.py` stay green unchanged.

**Status:** ✅ implemented + tests green this session.

---

## Phase 2 — Load-time tool validation + Specialists CRUD (web + mobile)

**Goal:** every `tools:` entry must resolve against the live registry (fail loud);
expose specialists for editing.

- `specialist_loader.validate_tools(config, registry_tool_names) -> list[str]`
  returns unknown tool names; loader logs a loud WARNING per builtin with unknowns,
  startup self-check (mirrors `mcp-upwork` self-check pattern) for builtins.
- Backend: `GET/POST/PUT/DELETE /api/specialists` over `save/load/delete_specialist`.
  Builtins are read-only (forkable); custom ones encrypted in DB (existing path).
- Web: `web/src/pages/Specialists.tsx` (lazy chunk) — list, view builtin (read-only),
  fork-to-custom, create/edit/delete custom with a `tools` multi-select fed by the
  registry, a `model` picker, and a system-prompt editor.
- Mobile: entry in the `/more` Power-tools hub → list + view (edit = web for v1).
- Tests: validation unit tests; CRUD route tests; web component smoke test.

---

## Phase 3 — Operating modes (Chat / Ask / Plan / Execute)

**Goal:** a per-chat posture layered over `permissions/checker`, NOT a replacement.

- `runtime/agent_mode.py`: `AgentMode` enum + `effective_permission(mode, skill_hint)`
  that maps Chat→deny-all-tools, Ask→force-writes-to-ask, Plan→read-only+gate,
  Auto→allow-all, then defers to the existing per-skill allow/ask/deny.
- Stored in `users.settings.general.agent_mode`; default `Ask`.
- Plan mode: brain produces a numbered plan, surfaces an **Execute** action via the
  existing `request_user_approval` / checkpoint UX (Telegram inline buttons, web
  button, mobile). On Execute → run fan-out under Auto for that task only.
- Surfaces: Telegram `/mode`, web Settings + chat-header switch, mobile Settings.
- Tests: permission-resolution matrix; plan→Execute gate flow; per-surface wiring.

---

## Phase 4 — Research-first fan-out

**Goal:** never plan from memory — fan code + web research before acting.

- Add builtin specialists `code_research_specialist.md` (repo grep/read, read-only)
  and `web_research_specialist.md` (web_search + scraper, deep-research style).
- Router (Plan mode) dispatches both in parallel via `dispatcher`, synthesizes
  their results plus `plan_research.gather_plan_research` into the plan.
- Consolidate via existing brain-fanout. Raise dispatcher concurrency cap
  (configurable; higher in Claude mode).
- Tests: parallel dispatch + synthesis; cap respected; Claude-mode model = Opus.

---

## Phase 5 — Thin-router brain (the teardown)

**Goal:** shrink the brain to meta-tools; delete the glue that only existed to
manage 277 tools. **Hard precondition: every grounding defense already lives in a
channel specialist (Phase 5a) before its brain-side copy is deleted (Phase 5b).**

- 5a: create channel specialists (`upwork`, `whatsapp`, `email`, `instagram`,
  `telegram`) whose system prompts carry F1 quote-then-summarize, paraphrase
  sanitizer guidance, most-recent-wins, per-tool result caps. Route channel reads
  through them. Keep the F1 test suite green.
- 5b: behind feature flag `SPECIALIST_FIRST_BRAIN`, strip domain tools from the
  brain base set so it sees only `plan` / `dispatch` / `delegate` /
  `recall_memories` / `save_memory`. Then DELETE, as each is rendered unreachable:
  `_dedup_tool_calls` + `_READ_ONLY_LIST_SUFFIXES`, AUTO-PROMOTE machinery, the
  keyword-gating tables, inline-pivot detector bypass lists. Each deletion in its
  own commit with the superseding specialist named in the message.
- Tests: flag-on routes every domain ask through a specialist; flag-off preserves
  current behavior; F1 suite green throughout; confab regression tests green.

---

## Phase 6 — Auto-improving specialists

**Goal:** specialists sharpen themselves (ADR-0002 loop, per-specialist).

- Tag skill-lessons with `specialist` scope; recall a specialist's lessons into its
  context at `run_specialist` time.
- Promotion: confidence-gated lessons append to the specialist's definition
  (custom = DB row; builtin = a `learned:` frontmatter block surfaced in the web
  editor for one-tap accept) with manual-review fallback so a bad lesson can't
  silently corrupt a specialist.
- Tests: lesson recall into specialist context; promotion gate; corruption guard.

---

## Self-Review

- **Spec coverage:** ADR-0005's four pillars map to Phases — A→5, B→1/2/6,
  C→4, D→3. ✓
- **Placeholder scan:** Phase 1 (the only executable-now phase) has concrete files,
  mappings, and invariants. Phases 2–6 are scoped future plans, not
  tasks-with-placeholders. ✓
- **Type consistency:** `SpecialistConfig` field names (`name`, `display_name`,
  `system_prompt`, `allowed_skills`, `preferred_model`, `is_builtin`,
  `include_scraper`) used consistently across phases. ✓
