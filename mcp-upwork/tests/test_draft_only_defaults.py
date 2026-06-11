"""Guard test: money-moving tools must default to draft_only=True.

``upwork_accept_offer`` creates a binding contract; ``upwork_submit_milestone``
starts Upwork's 14-day payment-release timer. With ``draft_only=False`` as
the default, a brain that omits the argument moves real money with zero
human eyes-on (2026-06-10 security audit). Defaulting to True stages the
confirm modal in the user's live Brave tab — the caller must OPT IN to the
final click with an explicit ``draft_only=False``.

Defense-in-depth twin of lazyclaw's SENSITIVE_SKILL_DEFAULTS permission
overlay: even if the permission layer is bypassed, the default here still
keeps a human on the final button.
"""

from __future__ import annotations

import inspect

from upwork_mcp.server import upwork_accept_offer, upwork_submit_milestone
from upwork_mcp.tools.contracts import SubmitMilestoneParams
from upwork_mcp.tools.offers import AcceptOfferParams


# ── Param models ──────────────────────────────────────────────────────


def test_accept_offer_params_default_to_draft_only():
    params = AcceptOfferParams(offer_url="https://www.upwork.com/nx/wm/offers/123")
    assert params.draft_only is True


def test_submit_milestone_params_default_to_draft_only():
    params = SubmitMilestoneParams(
        contract_url="https://www.upwork.com/ab/c/contracts/456"
    )
    assert params.draft_only is True


# ── MCP tool wrapper signatures (what every external caller hits) ─────


def test_accept_offer_tool_signature_defaults_to_draft_only():
    sig = inspect.signature(upwork_accept_offer)
    assert sig.parameters["draft_only"].default is True


def test_submit_milestone_tool_signature_defaults_to_draft_only():
    sig = inspect.signature(upwork_submit_milestone)
    assert sig.parameters["draft_only"].default is True
