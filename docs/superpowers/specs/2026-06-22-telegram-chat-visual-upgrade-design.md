# Telegram Chat Visual Upgrade + Reliability Fixes — Design

**Date:** 2026-06-22
**Status:** Approved (approach A), pending spec review
**Scope:** `lazyclaw/channels/telegram.py`, `lazyclaw/runtime/agent.py`, two new modules
**Out of scope:** the Claude SDK "error result: success" root cause (owner: user), web/mobile UI, platform-wide structured `view_hint` pipeline (approach B)

---

## 1. Background & current state

All Telegram replies flow through `_TelegramCallback` and the send block in
`telegram.py:2047-2125`. The current visual language is functional but
inconsistent, and three real bugs degrade it:

- **Bug 2 — raw internal error leaked to the user.** `agent.py:4147-4153`
  builds the user-facing reply as `f"Sorry, an error occurred: {exc}"`. When the
  brain call raises (e.g. the SDK `RuntimeError`), the user literally sees
  `Sorry, an error occurred: Claude SDK iterator error: Claude Code returned an
  error result: success` (the `content_len=98` rows in the log). Violates the
  project rule "user-friendly error messages / don't leak internals."
- **Bug 3 — failure loop.** That canned error string is stored as a normal
  `LLMResponse` (content set, `tool_calls=[]`), so the agentic loop treats it as
  a real reply, logs `Nudging tool use`, and iterates again — each iteration
  re-calling the brain. Observed as bursts of ~12 failed brain calls in 70s for a
  single user message (log 2026-06-22 17:20:19→17:21:18).
- **Footer overflow.** `telegram.py:2078-2083` chunks the body at 4000 chars,
  then appends `\n\n─────────\n{footer}` to the **last** chunk. A near-4000-char
  final chunk + a long footer can exceed Telegram's 4096 hard limit → Telegram
  rejects the message (400) → it falls to the error path. The footer length is
  never reserved.

Inconsistencies to clean up while we're here:

- **Parse mode is mixed.** The main reply uses `HTML` only when links are present
  (`_has_html_links`/`_prepare_html`, line 2054-2058), else plain. Six callback
  handlers send `parse_mode="Markdown"` (lines 853, 1182, 1194, 1203, 1330,
  1424). No consistent escaping; a stray `_` or `*` in a Markdown send can throw
  a Telegram `BadRequest` and silently drop the message (no plain-text fallback).
- **`_prepare_html` edge case** (line 185-199): the `<a>`-extraction regex
  matches `<a href="...">text</a>` and restores it verbatim, so an unescaped `&`
  *inside* an `href` (e.g. `?x=1&y=2`) is never escaped → Telegram HTML parser can
  reject the link.
- **File size.** `telegram.py` is 2,301 lines (project hard limit: 800). Extracting
  the view layer is overdue and directly serves this work.

Current renderers we keep and restyle (not replace): `_build_footer`
(line 417 — already carries a useful `⚠️ fallback → model (reason)` chip we must
preserve), `_build_status_text` (line 351 — edited in place via `_update_status`),
`_build_error_footer` (line 446), `_build_expense_keyboard` (line 142).

---

## 2. Goals & non-goals

**Goals**
1. One coherent, testable visual language for every outbound Telegram message.
2. Friendly error cards — never raw exception text in chat.
3. Break the failure loop: one clean error, not a spiral.
4. Correct long-message chunking (footer can never overflow).
5. Robust rendering: a parse failure falls back to plain text, never a dropped message.
6. Rich result cards where structured data already exists (tasks, reminders,
   contract-intake, permissions, `/status`, expenses).
7. Relieve the 800-line violation by extracting the view layer.

**Non-goals (YAGNI)**
- No structured `view_hint` emitted by the ~280 skills (approach B). Cards are
  pure functions over dicts so B can be layered on later with zero rewrite.
- No heuristic parsing of brain free-text into cards (approach C — rejected, fragile).
- No change to the SDK provider or the brain's failure trigger.

---

## 3. Visual design language (concrete)

Light emoji accents (the approved mockup). Monospace divider `─────────` (9× U+2500).
Telegram **HTML** parse mode everywhere (more robust than Markdown for our content),
with a plain-text fallback on `BadRequest`.

**Normal reply**
```
<body>

· Opus 4.8 · 3 tools · 2.1s
```
Footer is `·`-separated, leaner than today's `│`-separated line. It preserves the
fallback chip when `fallback_reason` is set:
```
· Opus 4.8 · 2 tools · 3.4s · ⚠️ fallback → Haiku (Sonnet overloaded)
```
Token/LLM counts move out of the default footer (kept only behind the existing
verbose/debug path) to reduce noise; tool count is shown instead (more meaningful).

**Error card** (replaces both the `agent.py` raw string and the `telegram.py`
`❌ Something went wrong\n{str(e)[:200]}` block):
```
⚠️ Quick hiccup
My brain stalled for a sec and couldn't finish that. Give it another go.
   ↳ /status
```
Variants by `kind`: `brain` (above), `rate_limit` (keep today's "rate-limited,
retry / set fallback" copy), `generic` (send/handler failure). No `{exc}` ever
reaches the card; the full exception stays in the logs.

**Live status** (edited in place, existing mechanism):
```
⚙️ Working…
  ✓ read inbox
  ⟳ searching jobs…
```
Restyle of `_build_status_text`; team-mode grid preserved.

**Result cards** (structured surfaces only):
- Task reminder / nag:
  ```
  ⏰ Reminder · Pay invoice
  Due 14:00 · was due 1h ago
  [ ✓ Done ] [ ⏰ Snooze 1h ] [ 📅 Tomorrow ]
  ```
- Contract intake: `🔔 New contract` + title/budget + `[ ✅ Accept ] [ ⏭ Skip ]`
  (existing keyboard, restyled body).
- `/permissions`: grouped `✅ allowed / ❓ ask / ⛔ denied` card.
- `/status`: `⚙️`-style card of the active request.
- Expense choice: existing keyboard, header restyled.

---

## 4. Architecture

Two new focused, pure, immutable modules. No domain imports; functions take plain
data and return strings/markup. This keeps them trivially unit-testable and pulls
~400 lines of formatting out of `telegram.py`.

### `lazyclaw/channels/telegram_view.py`
```python
PARSE_MODE = "HTML"
DIVIDER = "─────────"

def escape_html(text: str) -> str: ...
    # full escape incl. & inside hrefs (fixes _prepare_html edge case)

def render_footer(meta: FooterMeta) -> str: ...
    # · model · N tools · X.Xs  (+ fallback chip if meta.fallback_reason)

def render_reply(body: str, footer: str) -> str: ...
    # body + DIVIDER + footer, HTML-safe

def chunk_message(body: str, footer: str, limit: int = 4096) -> list[str]: ...
    # reserves len(footer)+len(divider)+separators; footer only on last chunk;
    # guarantees every chunk <= limit  (fixes overflow bug)

def render_error(kind: Literal["brain","rate_limit","generic"]) -> str: ...
    # friendly card; NEVER takes raw exception text

def render_status(header: str, steps: list[Step]) -> str: ...
```
`FooterMeta` / `Step` are frozen dataclasses (DTOs).

### `lazyclaw/channels/telegram_cards.py`
```python
def render_reminder_card(task: dict) -> str: ...
def render_contract_card(contract: dict) -> str: ...
def render_permissions_card(perms: dict) -> str: ...
def render_status_card(request: dict) -> str: ...
def render_expense_header(pending: dict) -> str: ...
```
Pure functions over the dicts those paths already hold. Keyboards stay where they
are (`_build_expense_keyboard`, daemon-built reminder/contract keyboards); only the
text bodies route through these functions.

### Integration points
- `telegram.py:2047-2096` send block → `render_reply` + `chunk_message`, single
  `PARSE_MODE="HTML"` path with a plain-text retry on `BadRequest`.
- `telegram.py:2098-2116` handler error path → `render_error("generic")`.
- `agent.py:4138-4153` → on a caught brain exception that is **not**
  rate-limit-escalatable (rate-limit escalation via `continue` is preserved),
  set the reply to `render_error("brain")`, log the full `exc`, and **terminate
  the agentic loop** (break out — do not fall through to another iteration). The
  SDK provider already retries internally and re-looping just re-sends identical
  messages for an identical failure (see the 12-calls-in-70s burst), so one caught
  failure is terminal for the turn. This kills the spiral (Bug 3).
- The 6 `parse_mode="Markdown"` callback sends → HTML via `escape_html`.
- `_build_footer`/`_build_status_text`/`_build_error_footer` become thin wrappers
  that delegate to the new render functions (keeps call sites, centralizes format).

---

## 5. Error handling

- Every outbound send goes through one helper that tries `PARSE_MODE="HTML"` and,
  on `telegram.error.BadRequest`, retries once as plain text (strip tags). A
  malformed render can degrade but never drop the message.
- `render_error` is the only user-facing error producer; it accepts a closed enum,
  not free text, so leaking internals is structurally impossible.
- Loop termination is per-turn and only triggers on a *caught brain exception*
  (not on a brain turn that legitimately returns content with no tools). The
  rate-limit escalation path (`continue` to a fallback model) is untouched, so a
  transient overload still re-routes rather than terminating.

---

## 6. Testing (TDD, write tests first)

New `tests/channels/test_telegram_view.py` + `test_telegram_cards.py`:
- `chunk_message`: body just under/over 4096; near-4000 final chunk + long footer
  → every chunk ≤ 4096; footer only on last chunk; empty body; single chunk.
- `render_error`: output contains none of `{"Traceback","Exception","SDK",
  "iterator","Claude Code returned"}`; each `kind` renders distinct friendly copy.
- `render_footer`: fallback chip present iff `fallback_reason` set; `·` separators;
  tool count rendered.
- `escape_html`: `&` inside href escaped; `<`/`>`/`&` in body escaped; existing
  `<a>` links preserved.
- Cards: golden-string tests for reminder/contract/permissions/status/expense.
- Loop fix: `agent.py` returns the error card and does **not** re-iterate after a
  caught brain exception (unit test around the loop with a router mocked to raise);
  a rate-limit exception still escalates to the fallback model (separate test).
- Regression: existing Telegram tests stay green.
Target ≥ 80% coverage on the two new modules.

---

## 7. File-size outcome

`telegram_view.py` (~250 lines) + `telegram_cards.py` (~200 lines) extract
formatting out of `telegram.py`, moving it toward the 800-line target. `agent.py`
(7,190 lines) is not refactored here — only the ~15-line error block changes
(out-of-scope refactor would balloon this work).

---

## 8. Risks

- **Markdown→HTML migration** could change how a few existing callback messages
  render. Mitigation: `escape_html` + plain-text fallback; manual spot-check of the
  6 converted handlers.
- **Footer content change** (dropping token/LLM counts from default) is a
  behavior change; kept available behind the existing verbose path.
- **Loop-break** must not suppress legitimate multi-iteration tool use. Mitigation:
  only break when a brain call yields neither content nor tool calls (true failure),
  and reset the counter on success.
