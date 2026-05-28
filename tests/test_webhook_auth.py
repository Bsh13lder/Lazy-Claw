"""Tests for webhook secret verification (fail-closed + constant-time).

Security regressions guarded here:
- BEFORE: ``_verify_secret`` returned early (ALLOWED) when no
  WEBHOOK_SECRET env var and no vault 'webhook_secret' credential were
  configured → unauthenticated agent execution as the default user.
  NOW: it fails closed with a 503 + operator-facing message.
- The secret comparison now uses ``secrets.compare_digest`` instead of a
  plain ``!=`` to avoid leaking the secret via timing.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, QueryParams


class _FakeRequest:
    """Minimal stand-in for ``fastapi.Request`` used by ``_verify_secret``.

    ``_verify_secret`` only reads ``request.headers`` and
    ``request.query_params``.
    """

    def __init__(self, headers: dict | None = None, query: dict | None = None):
        self.headers = Headers(headers or {})
        self.query_params = QueryParams(query or {})


@pytest.fixture
def webhook_mod(monkeypatch):
    import lazyclaw.gateway.routes.webhook as mod

    # Isolate from any ambient WEBHOOK_SECRET in the dev .env.
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    # No default user → vault path is skipped, keeping the test hermetic.
    monkeypatch.setattr(mod, "_default_user_id", None, raising=False)
    return mod


async def test_rejects_when_no_secret_configured(webhook_mod, caplog):
    """Fail closed: no env secret + no vault secret → 503, never allow."""
    req = _FakeRequest()
    with pytest.raises(HTTPException) as exc_info:
        await webhook_mod._verify_secret(req)
    assert exc_info.value.status_code == 503
    assert "WEBHOOK_SECRET" in exc_info.value.detail
    # Operator-facing warning explaining how to enable it.
    assert any(
        "WEBHOOK_SECRET" in r.getMessage() for r in caplog.records
    ), "expected a logger.warning naming WEBHOOK_SECRET"


async def test_accepts_with_correct_secret_via_header(webhook_mod, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cr3t-value")
    req = _FakeRequest(headers={"X-Webhook-Secret": "s3cr3t-value"})
    # No exception == accepted.
    await webhook_mod._verify_secret(req)


async def test_accepts_with_correct_secret_via_query(webhook_mod, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cr3t-value")
    req = _FakeRequest(query={"secret": "s3cr3t-value"})
    await webhook_mod._verify_secret(req)


async def test_rejects_wrong_secret(webhook_mod, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cr3t-value")
    req = _FakeRequest(headers={"X-Webhook-Secret": "wrong"})
    with pytest.raises(HTTPException) as exc_info:
        await webhook_mod._verify_secret(req)
    assert exc_info.value.status_code == 401


async def test_rejects_missing_secret_when_configured(webhook_mod, monkeypatch):
    """Secret configured but request provides none → 401 (not 503)."""
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cr3t-value")
    req = _FakeRequest()
    with pytest.raises(HTTPException) as exc_info:
        await webhook_mod._verify_secret(req)
    assert exc_info.value.status_code == 401


def test_uses_compare_digest():
    """The comparison must be constant-time, not a plain ``!=``."""
    import inspect

    import lazyclaw.gateway.routes.webhook as mod

    src = inspect.getsource(mod._verify_secret)
    assert "compare_digest" in src
