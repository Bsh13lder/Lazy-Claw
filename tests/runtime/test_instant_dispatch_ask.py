"""TDD: match_ask_conversation — NL trigger for autonomous conversation tasks.

Step 1: write the failing test.
Step 2: run it → FAIL (match_ask_conversation missing).
Step 3: implement in instant_dispatch.py.
Step 4: run again → PASS.
"""
import pytest
from lazyclaw.runtime.instant_dispatch import match_ask_conversation


def test_matches_ask_on_whatsapp():
    m = match_ask_conversation("ask Alice on WhatsApp if she's coming to my birthday")
    assert m is not None
    assert m["channel"] == "whatsapp"
    assert "birthday" in m["goal"]


def test_matches_via_email():
    m = match_ask_conversation("ask Bob via email whether the invoice was paid")
    assert m["channel"] == "email"
    assert m["who"] == "Bob"


def test_matches_instagram_and_telegram():
    assert match_ask_conversation("ask Sam on instagram if he saw my reel")["channel"] == "instagram"
    assert match_ask_conversation("ask Mia via telegram to send the file")["channel"] == "telegram"


def test_no_match_plain_message():
    assert match_ask_conversation("what's the weather") is None
    assert match_ask_conversation("ask me later") is None  # no channel anchor
