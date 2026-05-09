# ADR-0004: Code Tasks Always Route Through Claude Code (When Available)

**Date**: 2026-05-09
**Status**: accepted
**Deciders**: Vato (founder)

## Context

LazyClaw supports multiple brain models (Claude, MiniMax, future models)
and multiple worker models (Gemma 4 via Ollama, Claude Haiku, etc.). The
choice of brain/worker is user-configurable online and changes per ECO
mode.

But code-execution quality is uneven across these models. The Claude Code
agentic harness (write → run → test → fix loop, persistent session,
context retention across multi-file edits) is purpose-built for
engineering tasks and consistently outperforms generic LLM
"please-write-this-function" calls — regardless of how strong the brain
model itself is at reasoning.

The user is on a Claude Code subscription (MCP server + `claude -p` CLI,
both \$0 per call). Routing code-tagged work through any other model
wastes that subscription AND ships lower-quality code.

## Decision

**Whenever Claude Code MCP or `claude -p` CLI is available on the host,
ALL code-tagged tasks route through them first — regardless of which
brain or worker model the user has configured.**

The execution ladder for any code-tagged work is:

1. **Claude Code MCP** — PRIMARY. Persistent session, never loses
   context, full agentic loop. Used for multi-step coding (refactors,
   debugging, test-write-iterate, multi-file changes).
2. **`claude -p` CLI** — FALLBACK. One-shot subprocess, used only when
   MCP is unreachable or errors out. Slower spawn but reliable; ideal
   for short standalone tasks (single function, one bug fix, one
   proposal letter).
3. **Brain/worker model directly** — DEEP FALLBACK. Only when both
   above are unavailable on the host. Never short-circuit here when
   MCP or CLI is alive.
4. **Static template / hardcoded prose** — last-resort fallback when
   the entire LLM stack is offline.

"Code-tagged tasks" includes: code generation, debugging, refactoring,
proposal-letter drafting (treats text generation as a code task because
the proposal IS a contract artefact), skill creation, test writing, and
anything else that calls `start_gig` or the `code_specialist`.

## Alternatives Considered

### Alternative 1: Let each ECO mode pick its own code path
- **Pros**: Simple — no special-casing, just use the configured worker.
- **Cons**: When the configured worker is weaker at code than Claude
  Code (which is true of Gemma 4 E2B locally and of MiniMax M2.7 on
  longer iteration loops), code quality drops AND the Claude Code
  subscription is wasted.
- **Why not**: Wastes a paid resource AND ships worse code.

### Alternative 2: Force Claude Code as the worker in every mode
- **Pros**: Guarantees code quality.
- **Cons**: Breaks "worker" semantics — workers are also used for
  cheap NL polish, RAG retrieval, classification. Routing those
  through Claude Code is overkill.
- **Why not**: Wrong abstraction. Code-tagged work is a separate
  concern from "worker" calls.

### Alternative 3: User-toggleable per-task ("use Claude for code y/n?")
- **Pros**: Maximum control.
- **Cons**: One more setting to forget. The right answer is almost
  always "yes use Claude for code if it's available" — making it
  manual is just ceremony.
- **Why not**: Optimal default is universal; opt-out can come later
  if a use case emerges.

## Consequences

### Positive
- Code quality decoupled from brain/worker model choice. Switching
  the brain to MiniMax, GPT-5, or any future model leaves code
  output unchanged.
- Claude Code subscription utilized fully whenever code tasks happen.
- MCP→CLI ladder degrades gracefully on disconnect — never just dies.
- Existing `_LAZYCLAW_BRANDING_TEMPLATE` flow already routes through
  the ladder after the 2026-05-09 `apply_skill.py` patch.

### Negative
- One more code path to maintain when the Claude Code surface
  changes (new tool flags, deprecated `claude -p` syntax, etc.).
- When Claude Code MCP server crashes mid-gig, we drop to CLI mid-
  loop and lose session context. Mitigation: persistent MCP session
  is sticky — if it crashes the agent re-establishes a fresh session,
  not falls all the way to CLI for every call.

### Risks
- **MCP availability detection is unreliable** if the registry is
  slow to publish tool listings on boot. Mitigation: `apply_skill`
  already iterates `registry.list_mcp_tools()` and gracefully drops
  to CLI on any registry miss.
- **Claude Code subscription expiry** would silently break the
  MCP path. Mitigation: `claude_cli_provider.check_claude_cli_auth()`
  exists; surface auth failures to the Telegram admin user instead
  of silently falling all the way to template.

## Implementation Status

Done (2026-05-09):
- `apply_skill._generate_letter` — flipped to MCP → CLI → template
  ladder. CLI rung was missing before this ADR; it now exists.
- `CODE_SPECIALIST.system_prompt` — explicit ladder spelled out so
  the brain (whichever model it happens to be) follows the discipline.
- `start_gig` and `reddit_apply_skill` already MCP-first — they
  follow this ADR by construction.

Still TODO (next session):
- Add the same ladder to `draft_proposal_skill._draft` (currently
  goes brain → fallback). Should be brain → MCP → CLI → template
  for the proposal-letter case.
- Surface "Claude Code subscription auth failed" as a Telegram
  admin alert instead of silent template fallback.
- Audit other `start_gig`-adjacent skills (deliver_skill,
  review_skill, gig_skill) for the same ladder.

## Note on Model Configuration

This ADR is **brain/worker-agnostic**. The user configures models
online (`/mode`, `/brain`, `/worker` Telegram commands or Web UI
Settings). This routing rule does not change based on those configs —
it's a universal "if Claude Code is available, use it for code" guard.
