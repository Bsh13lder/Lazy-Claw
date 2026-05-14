"""Tests for lazyclaw/runtime/streaming_setting.py (Fix J).

Covers the chat-command parser (pure function). DB round-trip is verified
manually via the live server `/streaming on|off|status` chat command and
the `GET /api/agent/streaming/settings` endpoint — exercising those in
unit tests would require spinning up the project's real config /
db_session pool which conflicts with pytest-async fixtures.
"""
from __future__ import annotations

import pytest

from lazyclaw.runtime.streaming_setting import parse_streaming_command


# ── parse_streaming_command — chat-command lexer ─────────────────────


@pytest.mark.parametrize("msg,expected", [
    # Status / introspection
    ("/streaming", ("status", None)),
    ("/streaming status", ("status", None)),
    ("/streaming ?", ("status", None)),
    # ON variants
    ("/streaming on", ("set", True)),
    ("/streaming 1", ("set", True)),
    ("/streaming true", ("set", True)),
    ("/streaming verbose", ("set", True)),
    ("/streaming yes", ("set", True)),
    # OFF variants
    ("/streaming off", ("set", False)),
    ("/streaming 0", ("set", False)),
    ("/streaming false", ("set", False)),
    ("/streaming silent", ("set", False)),
    ("/streaming quiet", ("set", False)),
    ("/streaming no", ("set", False)),
    # Case-insensitive + whitespace tolerance
    ("/STREAMING ON", ("set", True)),
    ("  /streaming   off  ", ("set", False)),
])
def test_parse_streaming_command_recognised(msg: str, expected) -> None:
    assert parse_streaming_command(msg) == expected


@pytest.mark.parametrize("msg", [
    "",
    "  ",
    "hello",
    "show jobs",
    "stream something",          # close miss — different prefix
    "/streamingg on",             # typo
    "/streaming maybe",           # unrecognised arg
    "/streaming on please",       # extra noise
    "please /streaming on",       # not at start
])
def test_parse_streaming_command_misses(msg: str) -> None:
    assert parse_streaming_command(msg) is None
