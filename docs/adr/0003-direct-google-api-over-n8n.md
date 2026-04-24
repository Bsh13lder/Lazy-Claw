# ADR-0003: Direct Google API over n8n, fork workspace-mcp for OAuth

**Date**: 2026-04-22
**Status**: accepted
**Deciders**: LazyClaw maintainer

## Context

Over four consecutive commits (`1a2d0bd`, `f0ebac8`, `1dac209`,
`2b64223`) we tried to keep atomic Google Workspace operations running
through n8n. The last of those "fixes" literally emitted an n8n
workflow whose only node was an `httpRequest` to Google's Sheets API —
i.e. the workflow was wrapping a single REST call in
create-activate-webhook-run-delete ceremony. That was the clean signal
that n8n was the wrong abstraction for these operations.

The same week exposed a second, independent pain point: the OAuth
consent flow. When a token needed refreshing (or a new account needed
linking) the consent page opened in the user's default browser profile,
which was logged into the wrong Google account — the account where the
Google Cloud project and API enablement live is
`lazzyclaw@gmail.com`, but the default browser profile was signed into
`blckitteam@gmail.com`. Google's consent page did not offer an obvious
account switcher. The user spent ~1 hour trying to solve this via GCP
Console (registering redirect URIs) and never succeeded. Direct
quote from session 2026-04-22: "I can't solve that problem without you."

The root causes are independent and need different fixes:
1. n8n is overkill for single-API-call Google operations.
2. The OAuth library of record — `taylorwilsdon/google_workspace_mcp`
   — is actively maintained, MIT-licensed, and file-format-compatible
   with our credential store, but its default consent URL does not
   include the parameters that force an account picker.

## Decision

Three decisions, one direction:

### 1. Atomic Google ops run through `lazyclaw/skills/builtin/google_direct.py` (already shipped)

Five operations — `create_drive_folder`, `create_google_sheet`,
`append_sheet_rows`, `send_gmail`, `create_calendar_event` — go through
`google-api-python-client` + `google-auth` directly. The `google_run_task`
skill exposes them with the same interface the agent had for
`n8n_run_task`, so the migration is invisible to the LLM. The
`project_planning_kickoff` composite is also ported (now
`google_project_planning_kickoff`) using the same atomic helpers +
`project_assets.register_asset` for LazyBrain integration.

### 2. n8n Google one-shot skills are unregistered, not deleted

`N8nRunTaskSkill` and `ProjectPlanningKickoffSkill` remain on disk in
`lazyclaw/skills/builtin/n8n_oneshot.py` but are no longer registered
in `lazyclaw/skills/registry.py`. The import + register lines are
commented out so re-enabling is a one-line diff if we ever need a
temporary fallback. n8n itself stays in the stack — for **multi-step
chains, scheduled workflows, and webhook ingress**. The user-visible
workflow editor is still n8n's unique value.

### 3. OAuth is a patched `taylorwilsdon/google_workspace_mcp`, not a new LazyClaw module

For the consent / account-picker problem we patch the upstream MCP
server (MIT, actively maintained) with one change: add
`login_hint=<email>` to the authorization URL construction. Upstream
already passes `prompt=select_account consent`, but that alone is not
enough — when the user's default browser has exactly one Google
session and it's the wrong account, Google skips the picker. Adding
`login_hint` makes Google pre-select the correct account (and offers
a "Use a different account" escape if that account isn't signed in).

The fork is registered in LazyClaw's MCP server list. Credentials
already live in the file format the fork writes (see
`~/.google_workspace_mcp/credentials/{email}.json`), so no adapter is
needed — `google_direct.py` and the fork read/write the same files.

### Patch strategy (2026-04-24)

We apply the fix as a `.patch` file inside the lazyclaw Docker image,
not as a public GitHub fork or vendored source tree. Concretely:

- `patches/workspace-mcp-login-hint.patch` — unified diff against
  `auth/google_auth.py`, ~15 lines.
- `Dockerfile` — after `pip install -r requirements.txt`, a
  `patch -p1` line applies the diff to the installed package inside
  site-packages. Build fails loudly if the patch doesn't apply
  (upstream line drift), so regressions are visible.
- `requirements.txt` + `pyproject.toml` — `workspace-mcp>=1.19.0,<1.21.0`
  upper-bounds the pin so a surprise major release can't silently break
  the patch target.

Why not a public GH fork: the diff is two lines. Fork overhead
(repository to maintain, rebasing on tags, releases) would dwarf the
change. If upstream PR `taylorwilsdon/google_workspace_mcp#556` merges
(open since March 2026, same fix), we revert the patch file + the
Dockerfile lines and bump the pin. Until then, the local patch gives
us the fix immediately without waiting for review.

Why not vendor the full source: workspace-mcp ships ~500KB of Python
across `auth/`, `core/`, `gsheets/`, `gmail/`, etc. Vendoring to change
two lines would bloat the repo and make upstream sync painful. The
patch file is the minimal correct representation of what we're doing.

## Alternatives Considered

### Alternative 1: Patch n8n's 6 Google credential blobs in SQLite

- **Pros**: Preserves existing n8n workflows that may be using Google
  creds today. No migration required for anything currently live.
- **Cons**: n8n uses its own AES cipher format (CTR/CBC + specific IV
  layout) that LazyClaw has zero existing code for. Writing fresh
  in-place re-encryption risks corrupting `refresh_token` blobs — the
  hard-to-recreate half of each credential. Single-bit format drift
  destroys the grant. Reviewed session 2026-04-22: LazyClaw has no
  decryption code for n8n's DB, no dependency on
  `n8n-sdk` crypto, and would need to port upstream TypeScript logic
  byte-for-byte.
- **Why not**: We no longer need n8n's creds to be correct. LazyClaw's
  Google ops read from `~/.google_workspace_mcp/credentials/` (already
  populated). n8n's broken blobs only affect n8n's own standalone
  workflows — of which none are currently business-critical. Accepted
  collateral.

### Alternative 2: Build LazyClaw-native Google OAuth (FastAPI routes on port 18789)

- **Pros**: Everything under one roof — no external MCP dependency,
  no fork to maintain, full control over the consent UX. Stable
  redirect URI (`http://localhost:18789/oauth/google/callback`) means
  the GCP redirect-URI registration fight happens once, forever.
- **Cons**: ~300 LOC of auth code we don't already own — PKCE state
  store, token exchange, userinfo resolution, credential file writer,
  Telegram `/connect` command, Web UI tab, vault mirror. Every line
  of that exists, maintained, in `taylorwilsdon/google_workspace_mcp`.
  Writing our own means owning the maintenance tail (scope changes,
  token refresh edge cases, Google API deprecations) forever. Explicit
  user feedback 2026-04-22: *"we can fork and edit oauth section for
  make selectable account easy. why we need write that bunch of
  code?"*
- **Why not**: Bad leverage. A 10-line patch to an upstream repo
  replaces a 300-line home-grown module, and the upstream absorbs
  future Google API drift.

### Alternative 3: Use `google_workspace_mcp` unpatched

- **Pros**: Zero fork maintenance. Upstream pulls flow directly.
- **Cons**: Upstream's default authorization URL does not pass
  `prompt=select_account` or `login_hint`. That is exactly the bug the
  user hits — "it opens other google profile and dont lets me select
  one where is api enabled." The account picker issue was the
  *primary* blocker, and unpatched upstream does not fix it.
- **Why not**: Ships the known bug. The one-line patch is trivial and
  high-value.

## Consequences

### Positive

- Atomic Google ops no longer pay n8n's create-activate-webhook-run-
  delete tax per call (typically 1.0–2.5s n8n overhead on a sub-second
  Google API call). Result latency on e.g. `append_sheet_rows` drops
  from ~3s to <400ms.
- The four-commit loop fighting n8n's `Google Sheets` node schema
  (`1a2d0bd` through `2b64223`) is permanently out of the hot path.
  New atomic Google ops extend `google_direct.py`, not `n8n_oneshot.py`.
- The GCP redirect-URI battle becomes one-time: whatever URI the
  forked `google_workspace_mcp` uses, we register it once in GCP
  Console. The memory fragment `project_google_direct_migration.md`
  captures the ~1 hour of unresolved GCP UI pain — this ADR commits us
  to fighting it exactly once more, then never again.
- Credential file format is unchanged, so the transition is seamless
  on the read side — `google_direct._load_credentials` keeps working
  exactly as it has been since the n8n → cached-file import.

### Negative

- n8n's 6 internal Google credential blobs still have the OLD
  `clientSecret` and will stop working on their next refresh
  (accepted, per Alternative 1). Any user-facing n8n workflow that
  relied on Google nodes will start failing silently from the n8n
  side. Mitigation: document in user-facing release notes, and if a
  specific workflow needs resurrection, rebuild it against
  `google_run_task` directly.
- Forking `google_workspace_mcp` adds a vendored dependency LazyClaw
  is responsible for keeping in sync with upstream. Mitigation: the
  patch is small and isolated to the consent URL builder; upstream
  changes elsewhere rebase cleanly. Re-evaluate in 6 months —
  upstream may accept the patch as a configuration option
  (`oauth_prompt` / `login_hint`), in which case we un-fork.

### Risks

- **Scope creep on the fork.** Fork-and-patch is a known attractor for
  "while we're in there, let's also change X." Mitigation: the fork
  commit delta stays ≤50 LOC net, patch is in exactly one function
  (the authorization URL builder), and any additional changes go
  upstream as PRs rather than fork-local.
- **Tokens encoded elsewhere.** If a future LazyClaw feature caches
  tokens outside `~/.google_workspace_mcp/credentials/` (e.g. in the
  vault alone), the rotation story forks. Mitigation: treat
  `credentials_dir` as the single source of truth for Google tokens;
  any vault copy is a mirror, not a master.
- **Upstream goes unmaintained.** Low-likelihood today (the repo is
  active), but possible in 2+ years. Mitigation: the patch is small
  enough that adopting it into a LazyClaw-native module later is
  a day of work, not a project.

## References

- Migration state + cached credentials: memory file
  `project_google_direct_migration.md`
- n8n direction memo: memory file `project_n8n_future.md`
- Shipped code: `lazyclaw/skills/builtin/google_direct.py` (atomic ops +
  `project_planning_kickoff` composite)
- Registry diff: `lazyclaw/skills/registry.py` (n8n one-shot skills
  commented out, google direct skills registered in their place)
- Related: ADR-0001 (n8n schema-in-interface) and ADR-0002 (outcome
  learning loop) — both improve n8n's usefulness for its remaining
  persistent-workflow role; this ADR scopes where that role ends.
- Upstream (fork target): `taylorwilsdon/google_workspace_mcp` — MIT
  license, Python, active.
