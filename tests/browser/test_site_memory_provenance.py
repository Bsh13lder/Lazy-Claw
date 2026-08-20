"""2026-08-20 self-inflicted "prompt injection" scare.

The browser specialist read himap.co admin pages whose tool results had
LazyClaw's OWN site-memory block appended ("--- Site Knowledge ---" with
step recipes saved during the 2026-08-17 automated posting test:
navigate to the creation form, fill a title, tick "is published"...).
Nothing marked the block's provenance, so the specialist read it as
malicious content EMBEDDED IN THE PAGE ("repeated prompt-injection
attempts... disguised as 'Site Knowledge'"), spent its report on a
security warning, and flagged the user's own tooling as an attacker.

Fix: the formatted block must declare its provenance — LazyClaw's own
saved notes, not page content, hints only, never instructions to act.
"""

from __future__ import annotations

from lazyclaw.browser.site_memory import format_memories_for_context

_MEMORIES = {
    "navigation": [
        {"title": "Blog admin list", "content": "/admin/app/blogpost/"},
    ],
}


def test_header_declares_own_saved_notes_not_page_content() -> None:
    out = format_memories_for_context(_MEMORIES)
    header = out.splitlines()[0]
    assert "Site Knowledge" in header
    assert "NOT part of the page" in header, (
        "without provenance the specialist reads its own memory block as "
        "content embedded in the page — the 2026-08-20 injection scare"
    )
    assert "saved notes" in header


def test_header_scopes_memories_to_hints_not_instructions() -> None:
    out = format_memories_for_context(_MEMORIES)
    header = out.splitlines()[0]
    assert "hints" in header.lower()
    assert "never instructions" in header.lower(), (
        "stale action recipes (e.g. a publish flow saved by an old test) "
        "must not read as standing orders on later, unrelated tasks"
    )


def test_empty_memories_still_return_empty() -> None:
    assert format_memories_for_context({}) == ""
