"""Session auto-title must never be minted from a cron/watcher brief.

Heartbeat turns are persisted as ``user`` rows, and channel turns with no
explicit session land in the user's PRIMARY session — so an untitled
primary session could be permanently named
``[JOB:survival_contract_intake] check for new contracts`` by whichever
internal turn happened to fire first.
"""

from __future__ import annotations

import pytest

from lazyclaw.runtime.agent import auto_session_title
from lazyclaw.runtime.turn_markers import BACKGROUND_TURN_PREFIXES


@pytest.mark.parametrize("prefix", BACKGROUND_TURN_PREFIXES)
def test_background_prefixes_are_never_titled(prefix):
    assert auto_session_title(f"{prefix}nightly] do the thing") is None


def test_real_cron_brief_shape_is_skipped():
    assert auto_session_title(
        "[JOB:survival_contract_intake] poll upwork for new contracts"
    ) is None
    assert auto_session_title("[WATCHER:upwork_inbox] new message from James") is None
    assert auto_session_title("[REMINDER] pay the invoice") is None


def test_normal_user_message_still_titles():
    assert auto_session_title("what does James want?") == "what does James want?"


def test_title_is_truncated_to_limit():
    title = auto_session_title("x" * 200)
    assert title is not None
    assert len(title) == 50


def test_blank_and_none_yield_no_title():
    assert auto_session_title(None) is None
    assert auto_session_title("") is None
    assert auto_session_title("   ") is None


def test_prefix_only_matters_at_the_start():
    """A user genuinely writing about a job marker mid-sentence still gets a
    title — the guard is anchored, not a substring search."""
    assert auto_session_title("why did [JOB:foo] fail?") is not None
