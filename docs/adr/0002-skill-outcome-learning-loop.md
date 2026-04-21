# ADR-0002: Cross-topic skill-outcome learning loop on LazyBrain

**Date**: 2026-04-21
**Status**: accepted
**Deciders**: LazyClaw maintainer

## Context

ADR-0001 closes the specific Google-Sheets gap by hand-coding schema
hints into `n8n_management.py`. That approach doesn't scale — every
new n8n node type, every new Instagram operation, every new email
flow would need its own validator branch. The user explicitly asked
for a general solution: *"not only N8N — every good success task
topic (instagram will be email or whatsapp), if it learns on its
mistakes that's important… after one successful run by any model,
even a 0.6 billion model can execute any hard task."*

LazyClaw already has lesson plumbing for *user corrections* —
`runtime/lesson_extractor.py` + `runtime/lesson_store.py` capture
types `site` and `preference` and mirror into LazyBrain with tag
`[lesson, auto, owner/agent]`. The embedding pipeline
(`lazybrain/embeddings.py`, `nomic-embed-text` via Ollama) provides
semantic search with graceful substring fallback. The write path
is already encrypted; the read path is already deployed.

What's missing is a **third lesson source** — skill outcomes — and
a **second read path** — automatic pre-execution recall that injects
past working shapes into the LLM's context without requiring the
model to know to ask for them.

## Decision

Introduce a cross-topic skill-outcome learning loop that routes
through the existing LazyBrain store. A successful skill call writes
a lesson; the next similar call reads that lesson as a few-shot
exemplar. Three topics initially: `n8n`, `instagram`, `email`,
`whatsapp` (frozen set `LEARNING_TOPICS` in
`lazyclaw/runtime/skill_lesson.py`).

**Write side**: `save_skill_lesson(topic, action, intent, params,
outcome, error?, fix_summary?)` persists a LazyBrain note with tags
`[lesson, auto, owner/agent, topic/<t>, outcome/<success|fail|fix>,
action/<a>, intent/<slug>]`. Secrets redacted at any depth by
`_redact` before persistence (drops `password`, `token`, `api_key`,
`secret`, `authorization`, `cookie`, `credentials`, `private_key`;
truncates strings > 200 chars).

**Writer wiring**:
- `n8n_create_workflow` + `n8n_manage_workflow(activate)` success
  paths in `n8n_management.py` call `save_skill_lesson` with
  topic=`n8n`, action derived by `_n8n_action_label` (e.g.
  `n8n_create_workflow:googleSheets.spreadsheet.create`), params
  compacted by `_n8n_lesson_params` (type + name + parameters per
  node, stripped of credentials/position/ids).
- `MCPToolSkill.execute` in `lazyclaw/mcp/bridge.py` records on
  success, filtered by `_mcp_topic_for(tool_name, server_name)`
  which maps `whatsapp_*` → `whatsapp`, `instagram_*` → `instagram`,
  `gmail_*` / `email_*` → `email`. Single chokepoint — no per-MCP
  wrapper changes.

**Read side (automatic)**:
- `n8n_workflow_builder.generate_workflow_json` prepends up to 3
  recalled exemplars as a `## Known-good past shapes for similar
  tasks` block before the user message. Formatted by
  `format_lessons_as_exemplars` — compact JSON in a fenced code block,
  ≤ 2 KB per invocation.
- `runtime/context_builder.build_context` scans the current user
  message for topic keywords (`n8n|workflow|webhook`, `instagram|
  insta|reel`, `email|gmail|inbox|imap`, `whatsapp`) and injects a
  `## Learned skill shapes` section with up to 2 exemplars per
  matching topic. Zero-cost on unrelated turns.

**Read side (explicit)**: New skill `recall_topic_lessons(topic,
intent, k)` in `skills/builtin/topic_lessons.py` (registered in
`skills/registry.py`). Read-only, `permission_hint="allow"`. Escape
hatch for small models that don't follow the auto-recall signal.

## Alternatives Considered

### Alternative 1: Hand-code validator for every node type
- **Pros**: Deterministic. No embedding cost. No PKM dependency.
- **Cons**: Doesn't generalize beyond n8n. Every new topic (Instagram,
  email, WhatsApp, future integrations) needs its own validator
  module. Maintenance burden grows with the skill surface.
- **Why not**: The user's explicit request was a system that **learns**
  across topics, not one that needs a code change per topic.

### Alternative 2: Standalone `skill_lessons` SQL table
- **Pros**: Simpler schema (`topic, action, intent, params, outcome`).
  Faster queries — no embedding index overhead.
- **Cons**: Parallel universe to LazyBrain. User can't browse or
  backlink lessons in the PKM UI. Duplicates encryption, tagging,
  indexing work LazyBrain already does. No semantic search fallback.
- **Why not**: Existing PKM already solves 90% of storage + retrieval;
  a separate store would be ~300 extra lines for zero user-visible
  benefit.

### Alternative 3: LLM-extract lessons from session logs nightly
- **Pros**: Richer context — pulls the user's intent, follow-ups,
  user corrections into the lesson body.
- **Cons**: Expensive (one LLM call per session per topic). Delayed —
  first-run teaching unavailable until the nightly pass. Lossy if
  session is compressed before extraction.
- **Why not**: Skill outcomes are already structured (success/fail
  signals, exact parameter dicts) — no LLM extraction needed on the
  happy path. `lesson_extractor.py`'s LLM pass stays for user
  *corrections* (which are unstructured prose).

### Alternative 4: Ship a canonical exemplar library in-repo
- **Pros**: Ships on day one, no warm-up needed.
- **Cons**: Static. Doesn't improve with use. Drifts when n8n /
  Instagram APIs change. Provides no personalization — one user's
  typical workflow shape may differ from another's.
- **Why not**: Pillar A (ADR-0001) ships the canonical first shape
  for Google Sheets. Beyond that, letting the system learn from the
  user's own successful runs is more accurate per-user.

## Consequences

### Positive
- Every successful skill call teaches the system something. After the
  first sheet-creation run (any model), every future run — even on a
  local 0.6B worker — gets the working shape injected automatically.
- Reuses existing LazyBrain storage, embedding, and UI — lessons show
  up in the PKM graph with their own tag filters, so the user can
  audit what the agent has learned.
- Cross-topic by design — extending to a new topic is adding a string
  to `LEARNING_TOPICS` + a keyword rule in `_mcp_topic_for` and
  `_TOPIC_KEYWORDS`. No new subsystems.
- Never blocks — every recall and save path is fire-and-forget with
  a debug-level exception trap. A broken Ollama / empty vault / PKM
  failure degrades to the pre-existing behavior.

### Negative
- Adds ~400 tokens to n8n workflow-generation prompts when exemplars
  are available. Bounded by the formatter (≤ 2 KB per invocation)
  and `k=3`.
- Context-builder read path adds one extra LazyBrain query per user
  turn on topic-keyword hit. Benchmark target < 80 ms median;
  Ollama-down fallback uses substring which is ~10 ms.
- Secrets sanitization is deny-list based — a future argument named
  `secret_foo` wouldn't match the `secret` key exactly. Mitigated
  by the length truncation (200 chars) and the fact that sensitive
  MCP args are typically vault-resolved, not passed inline.

### Risks
- **Stale lessons**: if n8n or Instagram APIs change, old success
  shapes become wrong. Mitigation: (a) validator still runs before
  send (ADR-0001), so a stale exemplar that violates schema gets
  rejected before activation; (b) lessons older than 180 days with
  outcome=`fail` should be pruned on a daily pass (planned; not yet
  implemented — tracked as follow-up).
- **Recall noise**: semantic match on short intents can return
  loosely-related lessons. Mitigation: strict topic filter in
  `recall_skill_lessons` (only notes tagged `topic/<t>` count) and
  outcome filter (only `success` + `fix` by default; `fail` stays
  in the store for audit but doesn't feed the prompt).
- **PKM pollution**: every successful call produces a note. At high
  volume this could dominate the LazyBrain graph. Mitigation:
  per-outcome importance (`success=6`, `fix=7`, `fail=3`) so the
  daily log and UI pickers still prioritize user-written notes.

## References

- Plan: `/Users/blckit/.claude/plans/i-think-maybe-problem-soft-sketch.md` (Pillar B)
- Tests: `tests/test_skill_lesson.py` (14 assertions — round-trip,
  redaction at every nesting depth, unknown-topic / unknown-outcome
  guards, outcome filtering, Ollama-down fallback, formatter output)
- Project memory: `project_skill_lesson_learning_loop.md`
- Supersedes the "just memorize n8n schemas in the validator"
  assumption implicit in ADR-0001. ADR-0001 remains accepted — it
  provides the canonical first-shape for the canary topic. ADR-0002
  is the general pattern.
