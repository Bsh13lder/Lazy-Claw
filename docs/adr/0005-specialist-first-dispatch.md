# ADR-0005: Specialist-First Dispatch — Thin-Router Brain Over Declarative Specialists

**Date**: 2026-06-07
**Status**: accepted
**Deciders**: Vato (founder), Claude (Opus 4.8)

## Context

The brain is a generalist that holds 277 builtin skills + N bridged MCP tools
(195–400 total). Keeping that surface tractable forced ~2,600 lines of routing
glue into `runtime/agent.py`:

- keyword-gating of tool injection (`_CHANNEL_KEYWORDS`, browser/shell gates),
- the `search_tools` discovery meta-tool + dynamic schema re-injection,
- `_dedup_tool_calls` in the Claude SDK provider (Sonnet emits the *same* tool
  7–13× per turn; a read-only-listing collapse set of 25 suffixes exists purely
  to absorb this),
- AUTO-PROMOTE hallucination defense (tool-list narrowing → single tool →
  three-strikes failsafe that calls `run_background` directly),
- the stuck-detector + inline-pivot detector with per-tool bypass allowlists.

Every one of those is a *symptom* of a single model juggling too many tools. The
observed result matches the founder's complaint: dispatch is slow, inaccurate,
and double-uses tools.

Meanwhile the specialist infrastructure already exists and is underused:
`teams/specialist.py` (`SpecialistConfig`), `teams/runner.py`
(`run_specialist()` — isolated context, scoped tools, transcript capture),
`runtime/dispatcher.py` (parallel fan-out, semaphore cap 4, single-depth
guard), `runtime/task_runner.py` brain-fanout consolidation, and 3 builtin
specialists (browser / code / research). `runtime/plan_research.py` already
gathers zero-LLM pre-plan context in 4 parallel lanes.

Claude Code solves this exact problem with a thin router that delegates to
declarative, context-isolated specialist agents (`.md` + YAML frontmatter:
name / description / tools / model / system_prompt), permission modes
(plan / default / acceptEdits / auto), and parallel fan-out. The founder runs
LazyClaw in **Claude mode** on a €230 Max plan — every role routes through the
subscription via the Claude SDK at $0 marginal cost, and Opus 4.8 is available
to fan freely.

## Decision

Invert the architecture from **tools-first** to **specialists-first**, porting
Claude Code's pattern. Four pillars:

### A. Thin-router brain
The brain keeps only meta-tools (`plan`, `dispatch`/`delegate`,
`recall_memories`, `save_memory`). All domain work — browser, upwork, email,
docs, lazybrain, survival, tasks — is owned by specialists. Tools become
specialist *implementation details* that the brain never sees, which lets us
retire `_dedup_tool_calls`, AUTO-PROMOTE, keyword-gating, and most of the
inline-pivot machinery once specialists own their own tool surfaces.

### B. Specialists are the maintained unit
- **Declarative** `.md` + YAML frontmatter (`name`, `display_name`, `model`,
  `tools`, `include_scraper`) with the system prompt as the markdown body.
  Builtins live in-repo under `lazyclaw/teams/specialists/*.md` (git-versioned,
  diffable, editable without redeploy). User-defined specialists stay encrypted
  in the `specialists` DB table via the existing `save_specialist()` path. One
  loader merges both. Specialist *definitions* are not user content, so plaintext
  in-repo builtins do not violate "Encrypt Everything" (which governs user data).
- **Web-editable**: a Specialists CRUD surface (web + the mobile `/more`
  Power-tools hub) over the same loader — view/fork builtins, create/edit/delete
  custom ones.
- **Auto-improving**: each specialist accrues lessons (success/failure) through
  the existing Skill-Lesson Learning Loop (ADR-0002) → auto-recalled into its
  context next run → high-confidence lessons promoted into its definition,
  confidence-gated with a manual-review option so a bad lesson can't silently
  corrupt a specialist.
- **Grounding defenses migrate, not vanish**: F1 quote-then-summarize, the
  paraphrase sanitizer, most-recent-wins, and per-tool result caps move INTO the
  system prompts of channel-reading specialists (upwork / whatsapp / email /
  instagram / telegram). They are removed from the brain glue only after they
  exist in the specialist that now does the channel read.

### C. Parallel fan-out with deep research
- In **Claude mode**, specialists run Opus 4.8 (subscription = $0 marginal);
  other ECO modes stay mode-aware (Hybrid local worker, MiniMax M2.7, etc.).
  The dispatcher concurrency cap becomes configurable and is raised in Claude
  mode.
- Before acting on a non-trivial task, the router fans out a **code-research**
  specialist (repo grep/read) and a **web-research** specialist (online
  docs/sources) in parallel, synthesizes their findings, and only then plans.
  This builds on `plan_research.gather_plan_research`.
- Results consolidate into one reply via the existing brain-fanout
  (`register_subagent_fanout` / `record_subagent_result`).

### D. Operating modes (posture over the per-skill permission system)
Four switchable postures per chat (Telegram / web / mobile), layered *over* the
existing allow/ask/deny permission system rather than replacing it:

| Mode | Behavior | Permission effect |
|------|----------|-------------------|
| **Chat** | Conversation only; no tools fire. | deny all tools |
| **Ask** | Acts, but confirms each write/dispatch (one-tap ✅/❌). | force writes → ask |
| **Plan** | Read-only research fan-out → numbered plan → single **Execute** tap → autonomous run. | read-only allow + Execute gate |
| **Auto** | Fully autonomous; fans agents and works, no asking. | treat all as allow |

The Execute gate reuses the existing `request_user_approval` / checkpoint UX.

## Alternatives Considered

### Alternative 1: Keep tools-first generalist, keep tuning
- **Pros**: no refactor; the defenses are battle-tested.
- **Cons**: `_dedup_tool_calls` / AUTO-PROMOTE / keyword-gating become permanent
  crutches; every new tool adds routing glue; the brain context stays bloated.
- **Why not**: it treats symptoms forever and never fixes the root cause — one
  brain, too many tools.

### Alternative 2: Specialists as Python config only (expand `SpecialistConfig`)
- **Pros**: type-safe, no new loader.
- **Cons**: every specialist is a code change + redeploy; not file-editable, not
  web-editable, no self-improvement path.
- **Why not**: the founder wants Claude-Code-style declarative authoring +
  web editing + auto-improvement, which a frozen dataclass can't deliver.

### Alternative 3: Opus everywhere / full per-step plan approval
- **Pros**: maximum quality / maximum safety.
- **Cons**: Opus in the $0 Hybrid/MiniMax modes blows up cost; per-step approval
  nags on every task over chat.
- **Why not**: Opus is free via the subscription only in Claude mode, so
  fan-out stays mode-aware; and the founder wants one **Execute** tap then full
  autonomy, not per-action prompts.

## Consequences

### Positive
- Maintenance unit drops from 277 tools to a handful of specialists — the core
  goal. Adding capability becomes "write/edit a specialist", not "register a
  tool + tune its description + add keyword gates".
- The brain context shrinks dramatically, removing the *need* for dedup,
  AUTO-PROMOTE, and keyword-gating (the code is deleted as each is superseded).
- Parallel Opus specialists + research-first → faster, more accurate
  first-attempt success.
- Specialists are declarative, git-diffable, web-editable, and self-improving.
- Explicit modes give graduated control (talk → confirm → plan → autonomous).

### Negative
- A large `runtime/agent.py` teardown/refactor touching the live agent that runs
  the founder's daily Telegram/Upwork work.
- New surface: `.md` loader + schema/tool-name validation, a web CRUD UI, and
  mode plumbing across three clients.
- Some routing reasoning moves *into* per-domain specialists (duplicated across
  specialists rather than centralized).

### Risks
- **Losing hard-won grounding defenses.** Mitigation: embed F1 / paraphrase /
  most-recent-wins into channel-specialist prompts and keep the F1 test suite
  green BEFORE removing any brain-side defense. Pillar B makes this a hard
  precondition.
- **Refactor regression on a live system.** Mitigation: keep all existing
  dispatch primitives; migrate incrementally behind a feature flag,
  specialist-by-specialist; never big-bang.
- **Self-improvement corrupting a specialist.** Mitigation: lessons are additive
  context first; definition edits happen only on confidence-gated promotion with
  manual review (ADR-0002 machinery).
- **`.md` drift from real tools.** Mitigation: validate `tools:` against the live
  registry at load and fail loud.

## Implementation Status

Accepted + largely implemented 2026-06-07 (156 backend tests green). Phased
rollout tracked in `docs/superpowers/plans/2026-06-07-specialist-first-dispatch.md`:

- **Phase 1 — Declarative loader (DONE)**: `.md`+frontmatter format,
  `teams/specialist_loader.py` (reuses `lazybrain/frontmatter`), 3 builtins
  converted, inline defs deleted, package-data wired. Byte-identical.
- **Phase 2 — Validation + CRUD (DONE)**: `validate_specialist_tools` /
  `warn_on_unknown_tools`; `/api/specialists` CRUD (builtins read-only); web
  `Specialists.tsx` + mobile Specialists screen. Fixed latent `decrypt` import
  bug (custom specialists were silently failing to load).
- **Phase 2.5/2.6 — Full taxonomy + nav consolidation (DONE)**: 15 builtin
  specialists generated from the live registry (every tool validated, no
  duplicate domains) — 5 original + freelance/email/messaging (grounding),
  notes/tasks/documents/contacts, automation/bounty/system. `delegate`
  short-aliases auto-built from `BUILTIN_SPECIALISTS`. Web: the 3 skill-related
  Tools pages folded into one Specialists-led `SkillsHub` (tabs Specialists ·
  Skills · Discover); Tools nav = `["skills-hub","mcp"]`. The channel specialists
  carry the F1/grounding rules — this is **Phase 5a** (precondition for Phase 5).
- **Phase 3 — Operating modes (DONE)**: `runtime/agent_mode.py` enforced in
  `PermissionChecker.check_effective`; Telegram `/act` + web/mobile switch;
  default `ask` = no-op.
- **Phase 4 — Research-first fan-out (DONE)**: `runtime/research_fanout.py` +
  code/web research specialists, wired into the plan gate behind
  `LAZYCLAW_RESEARCH_FANOUT=1` (default off).
- **Phase 6 — Auto-improving specialists (DONE)**: `runtime/specialist_lessons.py`
  recall + confidence-gated promotion over the ADR-0002 loop.
- **Phase 5 — Thin-router brain (DEFERRED behind `SPECIALIST_FIRST_BRAIN`)**:
  migrate F1/grounding into channel specialists FIRST, then shrink the brain to
  meta-tools and DELETE dedup / AUTO-PROMOTE / keyword-gating as each is
  superseded. Not started — hard precondition is the grounding migration + a
  green F1 suite.

Follow-ups closed 2026-06-10 (branch `fix/dispatch-audit-hardening`):
`include_scraper` persisted for custom specialists (schema column + migration +
save/load; the same pass fixed a latent upsert bug — the encrypted-name
comparison never matched under AES-GCM's random nonce, so every save duplicated
the row); `startup_specialist_self_check(registry)` wired into both cli startup
paths (bare-MCP-suffix aware); the ModeSwitch/`PlanModeToggle` reconciliation is
obsolete (the legacy toggle no longer exists).

**Phase 5a flag shipped 2026-06-10**: `LAZYCLAW_SPECIALIST_FIRST_BRAIN=1`
(the ADR's `SPECIALIST_FIRST_BRAIN` flag, repo-convention env name). The
brain's toolset is filtered to meta tools + read-only inspections
(`_is_readonly_inspection`, hoisted to module level) on EVERY iteration
including iteration 0 — mutating/domain tools are never offered inline and
must go through `delegate`/`dispatch_subagents`. The late-inject-from-
registry door applies the same filter; AUTO-PROMOTE is excluded under the
flag (same as thin-router); composes with the thin-router cap (filter
first, cap after); one router-guidance system line injected per turn.
Default off = zero behavior change. Soaking in prod alongside
`LAZYCLAW_THIN_ROUTER=1`. The Phase 5 teardown (deleting AUTO-PROMOTE /
dedup / keyword-gating) stays DEFERRED — no defense was removed.

**Thin-router soak**: `LAZYCLAW_THIN_ROUTER=1` is live in prod since 2026-06-08.
First soak finding (2026-06-10 00:33): the brain delegated a raw upwork read to
the freelance specialist whose allowlist lacked the tools its own prompt
promises — `search_tools` discovery does NOT grant callability, so the worker
stuck-looped and died. Fixed (allowlist + regression test + the startup
self-check above). Phase 5 / 4c teardown stays deferred until the soak runs
clean. No phase removes a defense before its replacement exists.
