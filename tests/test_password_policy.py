"""Password-strength policy enforced by RegisterRequest (P0-4).

Strength is one of three independent layers (the others: PBKDF2 iterations in
the KDF, and per-IP/per-username rate limiting at the endpoints).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lazyclaw.gateway.auth import RegisterRequest


def _mk(pw: str) -> RegisterRequest:
    return RegisterRequest(username="alice", password=pw)


def test_rejects_too_short():
    with pytest.raises(ValidationError):
        _mk("Ab1!xyz")  # 7 chars — under the 12 minimum


def test_rejects_low_complexity_even_when_long():
    with pytest.raises(ValidationError):
        _mk("alllowercaseletters")  # long, but only 1 character class


def test_rejects_common_even_when_complex():
    # 4 classes (upper/lower/digit/symbol) but on the common-password denylist.
    with pytest.raises(ValidationError):
        _mk("Password1!")


def test_accepts_strong_four_class():
    req = _mk("Tr0ub4dour&3xtra")
    assert req.password == "Tr0ub4dour&3xtra"


def test_accepts_three_of_four_classes():
    # lower + upper + digit (no symbol), 14 chars → 3 classes → OK.
    req = _mk("MyPass1234word")
    assert req.password == "MyPass1234word"
