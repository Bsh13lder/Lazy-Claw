"""Unit tests for the save_memory credential guard.

The guard in ``lazyclaw.skills.builtin.memory_save`` has both branded-shape
patterns (JWT / sk- / GOCSPX- / …) and phrase-based catchers (``api_key:
<long>``, ``bearer <long>``, …). This suite exercises both paths so a
regression — e.g. someone tightening a regex and letting credentials slip
through — is caught early.
"""
from __future__ import annotations

import pytest

from lazyclaw.skills.builtin.memory_save import _looks_like_credential


# ── Positive cases (must be refused) ────────────────────────────────────

@pytest.mark.parametrize("content,expected_fragment", [
    # Branded shapes
    ("my secret is GOCSPX-abcDEF_1234567890xyz", "Google OAuth client_secret"),
    ("client_id: 618052191560-5gkevmpj4rfp66c3saqmfdhu02s7f90j.apps.googleusercontent.com",
     "Google OAuth client_id"),
    ("openai key sk-abcdefghijklmnopqrstuv1234567890", "API secret key"),
    ("AIzaSyD" + "a" * 32 + " my google key", "Google API key"),
    ("n8n api: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload_part.signature_part",
     "JWT token"),
    ("github ghp_" + "x" * 30, "GitHub personal access"),
    ("slack xoxb-123-456-abcdefghijklmnop", "Slack token"),
    ("raw hex " + "f" * 40, "long hex string"),
    # Phrase-based catchers
    ("api_key: abcdefghij1234567890xyz", "api_key assignment"),
    ("bearer AbCdEf0123456789.token-value-x", "Bearer token"),
    ("access_token = Z9.xvY_0123456789AbCdEf", "access_token assignment"),
    ("client_secret: customSecret123456_more", "client_secret / secret_key assignment"),
    ("n8n api key is customN8nToken0123456789", "n8n API key assignment"),
])
def test_refuses_credentials(content: str, expected_fragment: str) -> None:
    label = _looks_like_credential(content)
    assert label is not None, f"expected refusal for: {content[:60]}"
    # Label wording can drift; check the expected topic is present.
    assert expected_fragment.lower() in label.lower(), (
        f"wrong label for {content[:60]!r}: got {label!r}, "
        f"expected containing {expected_fragment!r}"
    )


# ── Negative cases (must NOT be refused) ────────────────────────────────

@pytest.mark.parametrize("content", [
    "User's name is Alex",
    "Prefers Python over JavaScript",
    "Lives in Madrid, UTC+1 winter",
    "Meeting tomorrow at 10am about project planning",
    "https://hirossa.com/privacy is the privacy page",
    # Mentions credential keywords but no actual value — should pass through.
    "Remember to rotate the api key every 90 days",
    "The bearer token concept is for HTTP auth headers",
    # Short alnum strings are benign
    "key:123",
    "code: xyz",
])
def test_allows_non_credentials(content: str) -> None:
    assert _looks_like_credential(content) is None, (
        f"false positive on: {content[:60]}"
    )
