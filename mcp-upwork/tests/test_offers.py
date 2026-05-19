"""Unit tests for the offer tools (accept / decline / list).

These cover the pre-browser refusal layer + Pydantic param validation —
the parts we can verify without spinning up a real browser. The actual
selector flows are validated via integration tests when ``UPWORK_TEST_LIVE=1``
is set.
"""

from __future__ import annotations

import pytest

from upwork_mcp.tools.offers import (
    AcceptOfferParams,
    DeclineOfferParams,
    OffersParams,
    accept_offer,
    decline_offer,
)


# ── OffersParams validation ─────────────────────────────────────────


def test_offers_params_defaults():
    p = OffersParams()
    assert p.status == "pending"
    assert p.limit == 20


def test_offers_params_clamps_limit_at_validation():
    with pytest.raises(Exception):
        OffersParams(limit=51)
    with pytest.raises(Exception):
        OffersParams(limit=0)


def test_offers_params_rejects_unknown_status():
    # Literal type → Pydantic validation error
    with pytest.raises(Exception):
        OffersParams(status="bogus")


# ── AcceptOfferParams ───────────────────────────────────────────────


def test_accept_offer_params_defaults():
    p = AcceptOfferParams(offer_url="https://www.upwork.com/nx/offers/123")
    assert p.draft_only is False


# ── accept_offer pre-flight guards ──────────────────────────────────


@pytest.mark.asyncio
async def test_accept_offer_refuses_non_http_url():
    p = AcceptOfferParams(offer_url="not-a-url")
    out = await accept_offer(p)
    assert out["status"] == "error"
    assert "full URL" in out["message"]


@pytest.mark.asyncio
async def test_accept_offer_refuses_non_upwork_url():
    p = AcceptOfferParams(offer_url="https://example.com/offers/123")
    out = await accept_offer(p)
    assert out["status"] == "error"
    assert "upwork.com" in out["message"]


# ── DeclineOfferParams ──────────────────────────────────────────────


def test_decline_offer_params_defaults():
    p = DeclineOfferParams(offer_url="https://www.upwork.com/nx/offers/123")
    assert p.reason == "Not the right fit"
    assert p.draft_only is False


# ── decline_offer URL guard ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_decline_offer_refuses_url_in_reason():
    p = DeclineOfferParams(
        offer_url="https://www.upwork.com/nx/offers/123",
        reason="see my work at https://example.com",
    )
    out = await decline_offer(p)
    assert out["status"] == "blocked"
    assert out["offending_token"]


@pytest.mark.asyncio
async def test_decline_offer_refuses_lazyclaw_in_reason():
    p = DeclineOfferParams(
        offer_url="https://www.upwork.com/nx/offers/123",
        reason="LazyClaw isn't a fit here",
    )
    out = await decline_offer(p)
    assert out["status"] == "blocked"
    assert "lazyclaw" in out["offending_token"].lower()


@pytest.mark.asyncio
async def test_decline_offer_refuses_non_upwork_url():
    p = DeclineOfferParams(
        offer_url="https://malicious.example.com/offers/123",
        reason="ok",
    )
    out = await decline_offer(p)
    assert out["status"] == "error"
    assert "upwork.com" in out["message"]
