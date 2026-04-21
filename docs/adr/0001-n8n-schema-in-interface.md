# ADR-0001: Fix MiniMax n8n stall via schema-in-interface, not model swap

**Date**: 2026-04-21
**Status**: accepted
**Deciders**: LazyClaw maintainer

## Context

MiniMax-M2.7 stalled for two days on the prompt "create an n8n Google
Sheet named 'hirossa keyword research'." Claude Haiku did the same task
on the first try. Log analysis (`data/lazyclaw.log`) showed n8n's
activate endpoint rejecting the model's workflow with:

```
Cannot publish workflow: 1 node have configuration issues:
Node "Google Sheets":
  - Missing or invalid required parameters (1 issue)
```

Plus a variant on the install-template path:
`request/body/nodes/0 must NOT have additional properties`.

The initial hypothesis (user's) was the MiniMax subscription quota. It
was not — Plus-tier (4,500 req / 5h) was nowhere near exhausted. The
real cause: our `n8n_create_workflow` tool description contained zero
schema hints for node shapes, so the model had to emit correct n8n JSON
from training memory alone. Haiku had memorized the schema; MiniMax
had not. `_validate_workflow_nodes` covered `resource` / `operation`
/ `documentId` / `sheetName` shape but did **not** check `title` on the
create-spreadsheet op — the one required field the model was actually
missing. The activation 400 returned to the model relayed n8n's
deliberately-vague "1 issue" text with no hint which field was at
fault, so the model retried the same broken shape until the stuck
detector fired and it gave up telling the user "do it manually."

## Decision

Frame the bug as a **tool-interface gap, not a model capability gap**.
Make the schema visible to any model reading the tool definition, and
make the pre-flight validator tight enough that specific missing-field
errors surface before n8n's activate endpoint does. Four edits, all in
`lazyclaw/skills/builtin/n8n_management.py`:

1. **Operation-aware pre-flight** — extend `_validate_workflow_nodes`
   to require `title` on `googleSheets.spreadsheet.create`, `documentId`
   on `spreadsheet.delete`, and `columns.mappingMode` on
   `sheet.append|appendOrUpdate|update`.
2. **Node-key allowlist (`_sanitize_node`)** — strip any top-level node
   field outside `{parameters, id, name, type, typeVersion, position,
   credentials, disabled, notes, notesInFlow, continueOnFail, …}`.
   Mirror the workflow-level allowlist one level down.
3. **Activation-error enrichment (`_enrich_activation_error`)** — on
   any activate 400, refetch the workflow, re-run the validator on
   the server-side node set, regex `Node "X":` from the error body,
   and return the current `parameters` dict truncated to 400 chars
   so the model can diff.
4. **Google Sheets cheat sheet** — append a verbatim schema block for
   create / delete / append / read / update to
   `N8nCreateWorkflowSkill.description` so every model sees the shape
   in the tool definition itself, not just in training data.

## Alternatives Considered

### Alternative 1: Always route Google tasks through `n8n_run_task`
- **Pros**: `n8n_oneshot.py:_build_create_google_sheet` already knows
  the correct shape; sidesteps the LLM-generation path entirely.
- **Cons**: `n8n_run_task` create → run → **delete** is wrong for this
  user request ("leave workflow as example of sheet creation"). The
  model correctly chose the persistent path; forcing oneshot would be
  a regression for the most common recurring-workflow use-case.
- **Why not**: Breaks the user's stated intent. The fix has to work
  for the persistent workflow path, not around it.

### Alternative 2: Add an `n8n_describe_node(type, operation)` tool
- **Pros**: General — not specific to Google Sheets. The model can
  self-rescue on any node type.
- **Cons**: Extra tool call in the hot path, one more tool in the
  system-prompt budget, and n8n's `/credentials/schema/<type>` endpoint
  doesn't cover parameter requirements uniformly.
- **Why not**: Higher cost than the cheat-sheet-in-description path
  for the ~4 node types that cause 90% of failures. Revisit if
  Pillar B's lesson recall (ADR-0002) doesn't close the gap.

### Alternative 3: Swap MiniMax for Haiku on n8n requests
- **Pros**: Haiku's training data makes it reliable on n8n schemas
  today.
- **Cons**: Violates LazyClaw's "any model should work for any task"
  goal and defeats the MiniMax token-plan cost model (flat $20/mo vs
  per-token Haiku).
- **Why not**: Treats the symptom (one model gets it wrong) not the
  root cause (the interface hides the schema from every model). A
  local 0.6B worker will hit the same wall.

## Consequences

### Positive
- MiniMax and other weaker-memorization models can now create Google
  Sheets workflows without the 18-tool-call flail pattern — the
  required fields are visible in the tool description and the
  pre-flight catches violations before n8n does.
- Error messages on activation failures are actionable instead of
  opaque ("Node 'Google Sheets' missing required `title`" vs
  "1 issue"), which compounds with the stuck-detector's intent-flail
  check rather than tripping it.
- Single-file change (`n8n_management.py`) — no new modules, no new
  dependencies, no config changes.

### Negative
- Tool description grows by ~20 lines of Google Sheets schema. Adds
  ~400 tokens to every `n8n_create_workflow` call's prompt. Mitigated
  by the base4-tool pattern: `n8n_create_workflow` only gets loaded
  after a `search_tools` hit.
- Per-node-type validation is hand-maintained — Airtable, Slack,
  Notion, etc. nodes still need the same treatment. ADR-0002 reduces
  this burden by making successful runs teach the system, but
  high-frequency node types may still warrant explicit validator
  branches.

### Risks
- **Schema drift**: n8n 2.x could rename `sheetsUi` or `columns`. If
  the cheat sheet and validator go stale they'll teach the model the
  wrong shape. Mitigation: the activation enricher re-runs the
  validator on the server-side node after a failure, so n8n's own
  rejection still surfaces for any unvalidated field.
- **False positives**: If `_validate_workflow_nodes` flags a field as
  missing when n8n would actually accept it, we block valid workflows.
  Mitigation: test-suite covers the happy paths; validator falls back
  to passing through any op/resource combo not in its allowlist.

## References

- Plan: `/Users/blckit/.claude/plans/i-think-maybe-problem-soft-sketch.md` (Pillar A)
- Tests: `tests/test_n8n_node_validator.py` (12 assertions)
- Related: ADR-0002 makes this fix self-maintaining via learning loop.
