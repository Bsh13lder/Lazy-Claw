# Telegram Chat Visual Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Telegram chat a unified, testable visual language and fix three reliability bugs (raw-error leak, agentic failure loop, long-message footer overflow).

**Architecture:** Two new pure, dependency-free modules — `telegram_view.py` (reply/footer/error/status rendering + correct chunking) and `telegram_cards.py` (structured result cards) — own all outbound formatting. `telegram.py` and `agent.py` call into them. Pure functions ⇒ trivial unit tests. This also pulls ~400 lines of formatting out of the 2,301-line `telegram.py`.

**Tech Stack:** Python 3.11, pytest, python-telegram-bot (HTML parse mode), frozen dataclasses as DTOs.

**Spec:** `docs/superpowers/specs/2026-06-22-telegram-chat-visual-upgrade-design.md`

**Conventions for every task:** run tests with `python -m pytest <path> -v` (do not pipe to `tail` — see MEMORY: pytest can hang at exit here; redirect to a file if needed). Commit messages: conventional, no AI attribution. Branch is already `feat/telegram-visual-upgrade`.

---

### Task 1: `telegram_view.py` — DTOs + `escape_html`

**Files:**
- Create: `lazyclaw/channels/telegram_view.py`
- Test: `tests/channels/test_telegram_view.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/test_telegram_view.py
from lazyclaw.channels.telegram_view import escape_html, FooterMeta, Step


def test_escape_html_escapes_entities():
    assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_escape_html_preserves_anchor_but_escapes_ampersand_in_href():
    # the _prepare_html bug: & inside href was left raw → Telegram rejected it
    out = escape_html('see <a href="http://x/?a=1&b=2">link</a> now')
    assert '<a href="http://x/?a=1&amp;b=2">link</a>' in out
    assert out.startswith("see ")


def test_footermeta_and_step_are_frozen():
    import dataclasses
    m = FooterMeta(model_label="Opus 4.8", tool_count=3, elapsed_s=2.1)
    s = Step(label="read inbox", state="done")
    assert dataclasses.is_dataclass(m) and dataclasses.is_dataclass(s)
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.tool_count = 9  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/channels/test_telegram_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lazyclaw.channels.telegram_view'`

- [ ] **Step 3: Write minimal implementation**

```python
# lazyclaw/channels/telegram_view.py
"""Pure, dependency-free renderers for the Telegram outbound visual language.

Every function takes plain data and returns strings. No telegram / lazyclaw
domain imports — keeps this trivially unit-testable and lets the same renderers
back a future structured `view_hint` pipeline (spec approach B).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

PARSE_MODE = "HTML"
DIVIDER = "─" * 9  # ─────────

_ANCHOR_RE = re.compile(r'<a\s+href="([^"]*)">([^<]*)</a>')


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_html(text: str) -> str:
    """HTML-escape `text` for Telegram HTML mode, preserving <a href> links.

    Unlike the old _prepare_html, the href value AND the link text are escaped
    (an unescaped & inside an href made Telegram reject the message).
    """
    placeholders: list[str] = []

    def _save(m: re.Match) -> str:
        href = _esc(m.group(1))
        label = _esc(m.group(2))
        placeholders.append(f'<a href="{href}">{label}</a>')
        return f"\x00LINK{len(placeholders) - 1}\x00"

    text = _ANCHOR_RE.sub(_save, text)
    text = _esc(text)
    for i, link in enumerate(placeholders):
        text = text.replace(f"\x00LINK{i}\x00", link)
    return text


@dataclass(frozen=True)
class FooterMeta:
    model_label: str | None = None
    tool_count: int = 0
    elapsed_s: float = 0.0
    fallback_reason: str | None = None


@dataclass(frozen=True)
class Step:
    label: str
    state: Literal["done", "active", "pending"] = "active"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/channels/test_telegram_view.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/channels/telegram_view.py tests/channels/test_telegram_view.py
git commit -m "feat(telegram): add telegram_view DTOs + html escaper"
```

---

### Task 2: `telegram_view.py` — `chunk_message` (footer-overflow fix)

**Files:**
- Modify: `lazyclaw/channels/telegram_view.py`
- Test: `tests/channels/test_telegram_view.py`

- [ ] **Step 1: Write the failing test**

```python
def test_chunk_message_short_single_chunk():
    from lazyclaw.channels.telegram_view import chunk_message, DIVIDER
    out = chunk_message("hello", "footer", limit=4096)
    assert out == [f"hello\n\n{DIVIDER}\nfooter"]


def test_chunk_message_never_exceeds_limit_with_long_footer():
    from lazyclaw.channels.telegram_view import chunk_message
    body = "x" * 9000
    footer = "f" * 90
    chunks = chunk_message(body, footer, limit=4096)
    assert all(len(c) <= 4096 for c in chunks), [len(c) for c in chunks]
    # footer appears only on the last chunk
    assert footer in chunks[-1]
    assert not any(footer in c for c in chunks[:-1])
    # body fully preserved (strip the footer block off the last chunk)
    from lazyclaw.channels.telegram_view import DIVIDER
    last_body = chunks[-1].split(f"\n\n{DIVIDER}\n")[0]
    assert "".join(chunks[:-1]) + last_body == body


def test_chunk_message_boundary_near_4000_final_chunk():
    # regression: old code put a near-4000 chunk + footer over 4096
    from lazyclaw.channels.telegram_view import chunk_message
    body = "y" * 4000
    chunks = chunk_message(body, "f" * 80, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/channels/test_telegram_view.py -k chunk -v`
Expected: FAIL — `ImportError: cannot import name 'chunk_message'`

- [ ] **Step 3: Write minimal implementation** (append to `telegram_view.py`)

```python
def chunk_message(body: str, footer: str, limit: int = 4096) -> list[str]:
    """Split `body` into Telegram-safe chunks; append footer to the LAST chunk.

    Reserves the footer block length so the final chunk can never exceed
    `limit` (fixes the old telegram.py overflow where a ~4000-char last chunk
    plus footer blew past 4096 and Telegram rejected the message).
    """
    suffix = f"\n\n{DIVIDER}\n{footer}" if footer else ""
    if len(body) + len(suffix) <= limit:
        return [body + suffix]

    # Body must be chunked. The LAST chunk has to fit body-slice + suffix.
    last_budget = max(1, limit - len(suffix))
    chunks: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        remaining = n - i
        # If what's left fits in a final chunk WITH the suffix, take it all.
        if remaining <= last_budget:
            chunks.append(body[i:] + suffix)
            return chunks
        # Otherwise emit a full-size intermediate chunk (no footer).
        chunks.append(body[i : i + limit])
        i += limit
    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/channels/test_telegram_view.py -k chunk -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/channels/telegram_view.py tests/channels/test_telegram_view.py
git commit -m "feat(telegram): chunk_message reserves footer length (fixes 4096 overflow)"
```

---

### Task 3: `telegram_view.py` — `render_footer` + `render_reply`

**Files:**
- Modify: `lazyclaw/channels/telegram_view.py`
- Test: `tests/channels/test_telegram_view.py`

- [ ] **Step 1: Write the failing test**

```python
def test_render_footer_basic():
    from lazyclaw.channels.telegram_view import render_footer, FooterMeta
    out = render_footer(FooterMeta(model_label="Opus 4.8", tool_count=3, elapsed_s=2.14))
    assert out == "· Opus 4.8 · 3 tools · 2.1s"


def test_render_footer_singular_tool_and_no_model():
    from lazyclaw.channels.telegram_view import render_footer, FooterMeta
    out = render_footer(FooterMeta(model_label=None, tool_count=1, elapsed_s=0.9))
    assert out == "· 1 tool · 0.9s"


def test_render_footer_fallback_chip():
    from lazyclaw.channels.telegram_view import render_footer, FooterMeta
    out = render_footer(FooterMeta(model_label="Haiku", tool_count=0, elapsed_s=3.0,
                                   fallback_reason="overloaded"))
    assert "⚠️ fallback → Haiku (Sonnet overloaded)" in out


def test_render_reply_assembles_body_divider_footer():
    from lazyclaw.channels.telegram_view import render_reply, DIVIDER
    out = render_reply("the answer", "· Opus 4.8 · 2.1s")
    assert out == f"the answer\n\n{DIVIDER}\n· Opus 4.8 · 2.1s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/channels/test_telegram_view.py -k "footer or reply" -v`
Expected: FAIL — `ImportError: cannot import name 'render_footer'`

- [ ] **Step 3: Write minimal implementation** (append)

```python
_FALLBACK_REASONS = {
    "overloaded": "Sonnet overloaded",
    "auth": "auth error",
    "cli_failed": "CLI failed",
    "local_failed": "local model failed",
    "worker_failed": "worker failed",
}


def render_footer(meta: FooterMeta) -> str:
    """Lean ` · `-separated footer. Preserves the fallback chip signal."""
    parts: list[str] = []
    if meta.tool_count:
        unit = "tool" if meta.tool_count == 1 else "tools"
        parts.append(f"{meta.tool_count} {unit}")
    parts.append(f"{meta.elapsed_s:.1f}s")
    if meta.fallback_reason:
        label = meta.model_label or "?"
        reason = _FALLBACK_REASONS.get(meta.fallback_reason, meta.fallback_reason)
        parts.append(f"⚠️ fallback → {label} ({reason})")
    elif meta.model_label:
        parts.insert(0, meta.model_label)
    return "· " + " · ".join(parts)


def render_reply(body: str, footer: str) -> str:
    """Body + divider + footer. Caller is responsible for HTML-escaping body."""
    if not footer:
        return body
    return f"{body}\n\n{DIVIDER}\n{footer}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/channels/test_telegram_view.py -k "footer or reply" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/channels/telegram_view.py tests/channels/test_telegram_view.py
git commit -m "feat(telegram): render_footer + render_reply"
```

---

### Task 4: `telegram_view.py` — `render_error` + `render_status`

**Files:**
- Modify: `lazyclaw/channels/telegram_view.py`
- Test: `tests/channels/test_telegram_view.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

_LEAK_TOKENS = ["Traceback", "Exception", "SDK", "iterator",
                "Claude Code returned", "error result: success"]


@pytest.mark.parametrize("kind", ["brain", "rate_limit", "generic"])
def test_render_error_never_leaks_internals(kind):
    from lazyclaw.channels.telegram_view import render_error
    out = render_error(kind)
    for tok in _LEAK_TOKENS:
        assert tok not in out
    assert out  # non-empty


def test_render_error_kinds_are_distinct():
    from lazyclaw.channels.telegram_view import render_error
    assert len({render_error("brain"), render_error("rate_limit"),
                render_error("generic")}) == 3


def test_render_status_lists_steps_with_glyphs():
    from lazyclaw.channels.telegram_view import render_status, Step
    out = render_status("Working…", [Step("read inbox", "done"),
                                          Step("searching jobs", "active")])
    assert "⚙️ Working…" in out
    assert "✓ read inbox" in out
    assert "⟳ searching jobs" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/channels/test_telegram_view.py -k "error or status" -v`
Expected: FAIL — `ImportError: cannot import name 'render_error'`

- [ ] **Step 3: Write minimal implementation** (append)

```python
_ERROR_CARDS = {
    "brain": (
        "⚠️ Quick hiccup\n"
        "My brain stalled for a sec and couldn't finish that. "
        "Give it another go.\n"
        "   ↳ /status"
    ),
    "rate_limit": (
        "⚠️ I'm rate-limited right now\n"
        "Retry in a minute, or set a fallback model with /mode so this "
        "re-routes automatically."
    ),
    "generic": (
        "⚠️ Something went wrong on my end.\n"
        "Try again in a moment.\n"
        "   ↳ /status for details"
    ),
}

_STEP_GLYPH = {"done": "✓", "active": "⟳", "pending": "·"}


def render_error(kind: Literal["brain", "rate_limit", "generic"]) -> str:
    """Friendly error card. Accepts a closed enum — never raw exception text,
    so leaking internals into chat is structurally impossible."""
    return _ERROR_CARDS.get(kind, _ERROR_CARDS["generic"])


def render_status(header: str, steps: list[Step]) -> str:
    lines = [f"⚙️ {header}"]
    for s in steps:
        glyph = _STEP_GLYPH.get(s.state, "·")
        suffix = "…" if s.state == "active" else ""
        lines.append(f"  {glyph} {s.label}{suffix}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/channels/test_telegram_view.py -k "error or status" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/channels/telegram_view.py tests/channels/test_telegram_view.py
git commit -m "feat(telegram): render_error (closed-enum, no leak) + render_status"
```

---

### Task 5: `telegram_cards.py` — structured result cards

**Files:**
- Create: `lazyclaw/channels/telegram_cards.py`
- Test: `tests/channels/test_telegram_cards.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/test_telegram_cards.py
from lazyclaw.channels.telegram_cards import (
    render_reminder_card, render_contract_card,
    render_permissions_card, render_expense_header,
)


def test_reminder_card_has_title_and_due():
    out = render_reminder_card({"title": "Pay invoice", "due_human": "14:00"})
    assert "⏰ Reminder · Pay invoice" in out
    assert "14:00" in out


def test_reminder_card_handles_missing_due():
    out = render_reminder_card({"title": "Call James"})
    assert "Call James" in out  # must not crash on absent due


def test_contract_card_shows_budget():
    out = render_contract_card({"title": "Scraper build", "budget": "$120"})
    assert "\U0001f514 New contract" in out
    assert "Scraper build" in out
    assert "$120" in out


def test_permissions_card_groups_states():
    out = render_permissions_card({"allow": ["tasks", "notes"], "ask": ["payment"],
                                   "deny": []})
    assert "✅" in out and "tasks" in out
    assert "❓" in out and "payment" in out


def test_expense_header_mentions_amount():
    out = render_expense_header({"amount": "12.50", "currency": "EUR"})
    assert "12.50" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/channels/test_telegram_cards.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lazyclaw.channels.telegram_cards'`

- [ ] **Step 3: Write minimal implementation**

```python
# lazyclaw/channels/telegram_cards.py
"""Pure renderers for structured Telegram result cards.

Functions take the dicts the calling paths already hold (task rows, contract
dicts, permission maps) and return the message BODY only. Inline keyboards stay
at their existing call sites — these never build markup.
"""
from __future__ import annotations


def render_reminder_card(task: dict) -> str:
    title = task.get("title", "Task")
    lines = [f"⏰ Reminder · {title}"]
    due = task.get("due_human") or task.get("reminder_human")
    if due:
        lines.append(f"Due {due}")
    return "\n".join(lines)


def render_contract_card(contract: dict) -> str:
    title = contract.get("title", "Contract")
    lines = [f"\U0001f514 New contract", title]
    budget = contract.get("budget")
    if budget:
        lines.append(f"Budget {budget}")
    return "\n".join(lines)


def render_permissions_card(perms: dict) -> str:
    rows = [
        ("✅ Allowed", perms.get("allow", [])),
        ("❓ Ask", perms.get("ask", [])),
        ("⛔ Denied", perms.get("deny", [])),
    ]
    lines: list[str] = []
    for header, items in rows:
        if items:
            lines.append(f"{header}: {', '.join(items)}")
    return "\n".join(lines) if lines else "No permission overrides set."


def render_status_card(request: dict) -> str:
    what = request.get("summary", "working")
    elapsed = request.get("elapsed_s", 0)
    return f"⚙️ Active · {what} · {elapsed:.0f}s"


def render_expense_header(pending: dict) -> str:
    amount = pending.get("amount", "?")
    currency = pending.get("currency", "")
    return f"\U0001f4b8 Which project for {amount} {currency}?".rstrip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/channels/test_telegram_cards.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/channels/telegram_cards.py tests/channels/test_telegram_cards.py
git commit -m "feat(telegram): telegram_cards renderers for structured surfaces"
```

---

### Task 6: agent.py — Bug 2 (kill the raw-error leak)

**Files:**
- Modify: `lazyclaw/runtime/agent.py:4147-4153` (the non-rate-limit branch of the brain-exception handler)
- Test: `tests/runtime/test_brain_error_card.py`

**Context:** Today this branch sets `_user_msg = f"Sorry, an error occurred: {exc}"`, so the user sees the raw `Claude SDK iterator error: …` text. Replace the user-facing string with `render_error("brain")`; keep `render_error("rate_limit")` for the rate-limit branch; keep `logger.error("Chat failed: %s", exc, exc_info=True)` so the full error stays in logs.

- [ ] **Step 1: Write the failing test**

```python
# tests/runtime/test_brain_error_card.py
from lazyclaw.channels.telegram_view import render_error


def test_brain_error_card_is_used_for_brain_failures():
    # the exact string the agent must emit on a caught brain exception
    card = render_error("brain")
    assert "Quick hiccup" in card
    for tok in ["SDK", "iterator", "Claude Code returned", "Traceback"]:
        assert tok not in card
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/runtime/test_brain_error_card.py -v`
Expected: PASS already (this pins the helper). Then make the agent.py edit below and rely on Task 8's loop test for behavior.

- [ ] **Step 3: Edit agent.py**

At the top of `agent.py`, add to the imports block (near the other `from lazyclaw...` imports):
```python
from lazyclaw.channels.telegram_view import render_error as _render_error
```
Replace lines 4141-4148 (`if _is_rate_limit_exception(exc): … else: _user_msg = f"Sorry, an error occurred: {exc}"`) with:
```python
                        if _is_rate_limit_exception(exc):
                            _user_msg = _render_error("rate_limit")
                        else:
                            _user_msg = _render_error("brain")
```
Leave the `logger.error("Chat failed: %s", exc, exc_info=True)` line above it untouched.

- [ ] **Step 4: Run the test + a smoke import**

Run: `python -m pytest tests/runtime/test_brain_error_card.py -v`
Run: `python -c "import lazyclaw.runtime.agent"`
Expected: PASS; import succeeds (no circular-import error from the new top-level import).

> If the top-level import causes a circular import, move it to a local import inside the `except` block instead.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/runtime/agent.py tests/runtime/test_brain_error_card.py
git commit -m "fix(agent): friendly error card instead of raw exception text (Bug 2)"
```

---

### Task 7: agent.py — Bug 3 (break the failure loop)

**Files:**
- Modify: `lazyclaw/runtime/agent.py` — the brain-exception handler (~4115-4153) and just after it (~4154)
- Test: `tests/runtime/test_brain_failure_no_spiral.py`

**Context:** After Task 6 the error card is friendly, but the loop still continues: the canned response (content set, `tool_calls=[]`) hits the channel-tool nudge (line 4831) → `continue` → re-call brain → re-fail. Fix: set a flag in the `except` block and, immediately after the handler, emit the card as the final answer and `break` — mirroring the normal final-answer exit at lines 5251-5258.

- [ ] **Step 1: Write the failing test**

```python
# tests/runtime/test_brain_failure_no_spiral.py
import pytest

# This is a focused behavioral test. It constructs the agent with a fake
# eco_router whose .chat() always raises a non-rate-limit RuntimeError, runs
# one message, and asserts the brain was called exactly ONCE (no spiral) and
# the reply is the friendly card.
#
# Use the existing agent test harness/fixtures in tests/runtime/ for
# constructing AgentRuntime + a callback double. Follow the pattern in the
# nearest existing tests/runtime/test_*.py that already builds an AgentRuntime.

from lazyclaw.channels.telegram_view import render_error


@pytest.mark.asyncio
async def test_brain_failure_does_not_spiral(make_agent_runtime):  # fixture TBD-by-harness
    calls = {"n": 0}

    class _RaisingRouter:
        last_routing = None
        async def chat(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("Claude SDK iterator error: ... error result: success")
        async def get_fallback_model(self, user_id):
            return None

    agent = make_agent_runtime(eco_router=_RaisingRouter())
    reply = await agent.process_message("hi", user_id="u1")  # adapt to real entrypoint

    assert calls["n"] == 1, f"brain re-called {calls['n']}x — spiral not broken"
    assert reply.strip() == render_error("brain").strip()
```

> The implementing engineer must adapt `make_agent_runtime` / `process_message` to the real test harness in `tests/runtime/`. If no reusable fixture exists, build the smallest router-double test that exercises the `for iteration` loop and asserts the call count.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/runtime/test_brain_failure_no_spiral.py -v`
Expected: FAIL — `calls['n']` is > 1 (spiral) before the fix.

- [ ] **Step 3: Edit agent.py**

In the `except Exception as exc:` handler (around 4115), after the block that builds `response = _LLMResp(content=_user_msg, model="unknown", tool_calls=[])`, set a flag:
```python
                        response = _LLMResp(
                            content=_user_msg, model="unknown", tool_calls=[],
                        )
                        _brain_errored = True
```
Initialize `_brain_errored = False` next to `_escalated = False` (line 3577).

Immediately AFTER the `try/except` that wraps the brain call (i.e. before the channel-tool nudge logic ~line 4828), insert:
```python
                if _brain_errored:
                    # Caught brain failure — emit the friendly error card as the
                    # final answer and stop. Re-looping re-sends identical
                    # messages for an identical failure (the 12-calls-in-70s
                    # spiral). Mirror the normal final-answer exit (≈5251-5258):
                    # stream the card, mark stream done, record history, break.
                    await cb.on_event(AgentEvent("token", response.content,
                                                 {"model": "error"}))
                    await cb.on_event(AgentEvent("stream_done", "", {}))
                    all_new_messages.append(
                        LLMMessage(role="assistant", content=response.content)
                    )
                    break
```

> Verify the exact insertion point: it must be after the `else:` streaming branch rejoins (both the `if tools:` and `else:` paths set `response`), and before the nudge/F1/dispatch logic. Read the live code around 4154-4828 and place the guard at the first point where both branches have produced `response`. The rate-limit escalation path (`continue`, line 4137) runs BEFORE the flag is set, so escalation is unaffected.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime/test_brain_failure_no_spiral.py -v`
Expected: PASS — `calls['n'] == 1`, reply is the brain error card.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/runtime/agent.py tests/runtime/test_brain_failure_no_spiral.py
git commit -m "fix(agent): terminate loop on caught brain failure (Bug 3, no spiral)"
```

---

### Task 8: telegram.py — route the send path through the view layer

**Files:**
- Modify: `lazyclaw/channels/telegram.py:2052-2096` (the done/send block)
- Modify: `lazyclaw/channels/telegram.py:417-444` (`_build_footer` delegates to `render_footer`)
- Test: `tests/channels/test_telegram_send_path.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/test_telegram_send_path.py
# Verify the send helper falls back to plain text when HTML parse fails, and
# that chunking is driven by telegram_view.chunk_message.
import pytest
import telegram.error
from lazyclaw.channels import telegram as tg


@pytest.mark.asyncio
async def test_send_falls_back_to_plain_text_on_badrequest(monkeypatch):
    sent = []

    class _Msg:
        async def reply_text(self, text, parse_mode=None, reply_markup=None):
            if parse_mode == "HTML":
                raise telegram.error.BadRequest("can't parse entities")
            sent.append((text, parse_mode))

    # _telegram_send_with_retry must retry once without parse_mode on BadRequest
    await tg._telegram_send_with_retry(
        lambda pm=None: _Msg().reply_text("<b>x", parse_mode=pm),
        html_fallback=True,
    )
    assert sent and sent[0][1] is None
```

> Adapt the exact signature of `_telegram_send_with_retry` to the real one (lines ~100-135). The test pins the new behavior: on `telegram.error.BadRequest`, retry once with `parse_mode=None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/channels/test_telegram_send_path.py -v`
Expected: FAIL — `_telegram_send_with_retry` has no `html_fallback` behavior yet.

- [ ] **Step 3: Edit telegram.py**

1. Add import near the top:
```python
from lazyclaw.channels import telegram_view as _view
```
2. In `_telegram_send_with_retry` (≈100-135), add an `html_fallback: bool = False` param; on `telegram.error.BadRequest`, retry the send once passing `parse_mode=None` (strip HTML). Keep the existing network-retry/backoff loop.
3. Replace the send block at 2052-2096:
```python
            footer = render_footer_meta_to_text(callback)  # see step note
            body = _view.escape_html(response)
            chunks = _view.chunk_message(body, footer, limit=4096)
            for i, chunk in enumerate(chunks):
                markup = expense_markup if i == len(chunks) - 1 else None
                await _telegram_send_with_retry(
                    lambda c=chunk, m=markup: update.message.reply_text(
                        c, parse_mode=_view.PARSE_MODE, reply_markup=m,
                    ),
                    html_fallback=True,
                )
```
4. Refactor `_build_footer` (417-444) to build a `FooterMeta` and return `_view.render_footer(meta)`; expose a small helper `render_footer_meta_to_text(callback)` or just call `callback._build_footer()` (keep the existing name). Map `callback.final_model→model_label` (via `_friendly_model_name`), distinct tool count→`tool_count`, elapsed→`elapsed_s`, `callback.fallback_reason→fallback_reason`.

> Distinct tool count: derive from the callback's tool tracking (the same data feeding `tools_used`). If only an LLM count is readily available, use the number of distinct tool names observed this turn.

- [ ] **Step 4: Run the test + existing telegram tests**

Run: `python -m pytest tests/channels/ -v`
Expected: PASS (new send-path test + existing `test_telegram_sensitive_approval.py` stay green).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/channels/telegram.py tests/channels/test_telegram_send_path.py
git commit -m "feat(telegram): route reply send through view layer + html→plain fallback"
```

---

### Task 9: telegram.py — error path + Markdown→HTML unification + status restyle

**Files:**
- Modify: `lazyclaw/channels/telegram.py:2098-2116` (handler error path)
- Modify: `lazyclaw/channels/telegram.py:351-378` (`_build_status_text` → `render_status`)
- Modify: the 6 `parse_mode="Markdown"` sends (lines 853, 1182, 1194, 1203, 1330, 1424)
- Test: extend `tests/channels/test_telegram_send_path.py`

- [ ] **Step 1: Write the failing test**

```python
def test_handler_error_uses_generic_card():
    from lazyclaw.channels.telegram_view import render_error
    card = render_error("generic")
    assert "Something went wrong" in card
    assert "Traceback" not in card
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/channels/test_telegram_send_path.py -k error -v`
Expected: PASS for the helper; then make edits below.

- [ ] **Step 3: Edit telegram.py**

1. Error path (2098-2116): replace the `❌ Something went wrong\n{str(e)[:200]}` body with `_view.render_error("generic")`; keep `logger.error(... exc_info=True)`; keep sending via `_telegram_send_with_retry(..., html_fallback=True)`.
2. `_build_status_text` (351-371, simple mode): build `[Step(...)]` from `current_phase`/`current_tool` and return `_view.render_status("Working", steps)`. Keep team-mode grid as-is for now (out of scope to restyle the grid).
3. For the 6 `parse_mode="Markdown"` callback sends, switch to `parse_mode=_view.PARSE_MODE` and wrap the text in `_view.escape_html(...)` (the messages are short status strings; escaping is safe).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/channels/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/channels/telegram.py tests/channels/test_telegram_send_path.py
git commit -m "feat(telegram): generic error card + HTML unification + status restyle"
```

---

### Task 10: Wire result cards into structured surfaces

**Files:**
- Modify: reminder/nag push path (search `_build...`/notifier for the reminder body), contract-intake accept message, `/permissions` handler in `telegram_commands.py`, expense keyboard header in `telegram.py`
- Test: covered by Task 5 unit tests; add one integration assertion per surface if a harness exists

- [ ] **Step 1: Locate the surfaces**

Run:
```bash
grep -rn "Reminder\|nag\|due" lazyclaw/notifications/ lazyclaw/channels/telegram_commands.py | head
grep -rn "permissions" lazyclaw/channels/telegram_commands.py | head
```

- [ ] **Step 2: Replace inline body strings with card renderers**

For each surface, import `from lazyclaw.channels import telegram_cards as _cards` and replace the hand-built body string with the matching `_cards.render_*` call, passing the dict already in scope. Keyboards stay untouched.

- [ ] **Step 3: Run the card tests + smoke imports**

Run: `python -m pytest tests/channels/test_telegram_cards.py -v`
Run: `python -c "import lazyclaw.channels.telegram_commands, lazyclaw.channels.telegram"`
Expected: PASS; imports succeed.

- [ ] **Step 4: Commit**

```bash
git add lazyclaw/channels/ lazyclaw/notifications/
git commit -m "feat(telegram): render structured cards for reminders/contracts/permissions/expense"
```

---

### Task 11: Full-suite verification

- [ ] **Step 1: Run the channel + runtime suites**

Run: `python -m pytest tests/channels/ tests/runtime/test_brain_error_card.py tests/runtime/test_brain_failure_no_spiral.py -v > /tmp/lazyclaw_test_out.txt 2>&1; tail -40 /tmp/lazyclaw_test_out.txt`
Expected: all PASS. (Redirect to a file — pytest can hang at exit in this repo when piped.)

- [ ] **Step 2: Run the broader suite touched by the change**

Run: `python -m pytest tests/ -k "telegram or agent" -q > /tmp/lazyclaw_test_out2.txt 2>&1; tail -40 /tmp/lazyclaw_test_out2.txt`
Expected: no new failures vs. baseline.

- [ ] **Step 3: Update DOCS.md + CLAUDE.md key-patterns**

Add a "Telegram visual layer" note to DOCS.md (the two new modules + the three fixes) and one bullet under Key Patterns → Channels in CLAUDE.md. Keep CLAUDE.md under 40K chars.

- [ ] **Step 4: Commit + summarize**

```bash
git add DOCS.md CLAUDE.md
git commit -m "docs: record Telegram visual layer + reliability fixes"
```

---

## Self-Review

**Spec coverage:**
- Bug 2 (raw-error leak) → Task 6. Bug 3 (loop) → Task 7. Footer overflow → Task 2. HTML fallback → Task 8. `&`-in-href → Task 1. Markdown→HTML unify → Task 9. Visual language (footer/error/status/reply) → Tasks 3-4, 8-9. Cards → Tasks 5, 10. File-size relief → new modules (Tasks 1-5). ✔ all spec sections mapped.

**Placeholder scan:** The only deferred specifics are the agent.py insertion point (Task 7) and the test-harness fixture (`make_agent_runtime`), both explicitly flagged for the engineer to resolve against live code — not silent TODOs. The card-wiring surfaces (Task 10) are located via grep in-step. Acceptable for an existing-codebase integration.

**Type consistency:** `FooterMeta`/`Step` defined in Task 1, used in Tasks 3-4, 8. `render_error(kind)` enum `"brain"|"rate_limit"|"generic"` consistent across Tasks 4, 6, 9. `chunk_message(body, footer, limit)` signature consistent Tasks 2, 8. `render_*` card names consistent Tasks 5, 10.

**Risk note:** Task 7 (loop break) is the highest-risk change — it touches the intricate agentic loop. Recommend executing Tasks 6-7 inline (live context) rather than via a cold subagent.
