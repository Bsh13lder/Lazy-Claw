"""ask_brain — stuck-worker escalation chain (worker → brain → user).

Fable-style tactic (2026-08-16): a specialist stuck mid-task asks the BRAIN
for one decisive instruction instead of thrashing or failing. If the brain
decides the question is genuinely the user's (missing personal data,
irreversible choice, preference), it escalates to the USER via the existing
checkpoint plumbing — whose approve/reject already carries a free-text
``reason``, so the user's typed answer flows back into the worker's loop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from lazyclaw.llm.providers.base import LLMResponse
from lazyclaw.skills.builtin.ask_brain import AskBrainSkill


def _resp(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="claude-opus-5")


def _skill(brain_text: str | None = None) -> AskBrainSkill:
    router = AsyncMock()
    if brain_text is not None:
        router.chat = AsyncMock(return_value=_resp(brain_text))
    else:
        router.chat = AsyncMock(side_effect=RuntimeError("provider down"))
    return AskBrainSkill(config=None, eco_router=router)


class TestBrainConsult:
    async def test_brain_guidance_returned_to_worker(self):
        skill = _skill("Try the mobile version of the site — the desktop "
                       "form is A/B-gated. Use m.example.com/apply.")
        out = await skill.execute("u1", {
            "question": "Apply form 404s on every attempt",
            "context": "Tried /apply and /jobs/apply, both 404",
        })
        assert out.startswith("BRAIN GUIDANCE:")
        assert "m.example.com/apply" in out

    async def test_consult_includes_question_and_context(self):
        skill = _skill("Do X.")
        await skill.execute("u1", {"question": "Q?", "context": "tried A, B"})
        messages = skill._eco_router.chat.await_args.args[0]
        joined = " ".join(m.content for m in messages)
        assert "Q?" in joined
        assert "tried A, B" in joined
        # Brain role — no tools, decisive answer
        assert skill._eco_router.chat.await_args.kwargs.get("role") == "brain"

    async def test_missing_question_is_an_error(self):
        skill = _skill("irrelevant")
        out = await skill.execute("u1", {})
        assert out.startswith("Error")
        skill._eco_router.chat.assert_not_awaited()


class TestUserEscalation:
    async def test_ask_user_marker_escalates_via_checkpoint(self):
        skill = _skill("ASK_USER: Which of your two Upwork profiles should "
                       "this proposal be sent from?")
        with patch(
            "lazyclaw.browser.checkpoints.request_checkpoint",
            AsyncMock(return_value={"approved": True,
                                    "reason": "the developer profile"}),
        ) as mock_cp:
            out = await skill.execute("u1", {"question": "which profile?"})
        assert "USER ANSWERED" in out
        assert "the developer profile" in out
        detail = mock_cp.await_args.kwargs.get("detail") or ""
        assert "Which of your two Upwork profiles" in detail

    async def test_user_rejection_note_flows_back(self):
        skill = _skill("ASK_USER: Should I retry with the saved card?")
        with patch(
            "lazyclaw.browser.checkpoints.request_checkpoint",
            AsyncMock(return_value={"approved": False,
                                    "reason": "no, stop — I'll pay manually"}),
        ):
            out = await skill.execute("u1", {"question": "payment failed"})
        assert "USER SAYS" in out
        assert "pay manually" in out

    async def test_timeout_returns_safe_proceed_instruction(self):
        skill = _skill("ASK_USER: proceed?")
        with patch(
            "lazyclaw.browser.checkpoints.request_checkpoint",
            AsyncMock(return_value={"approved": False,
                                    "reason": "timed out waiting for user"}),
        ):
            out = await skill.execute("u1", {"question": "?"})
        assert "unavailable" in out.lower()
        assert "safest" in out.lower() or "non-destructive" in out.lower()

    async def test_brain_failure_falls_back_to_user(self):
        skill = _skill(None)  # router raises
        with patch(
            "lazyclaw.browser.checkpoints.request_checkpoint",
            AsyncMock(return_value={"approved": True, "reason": "go ahead"}),
        ) as mock_cp:
            out = await skill.execute("u1", {"question": "stuck on captcha"})
        assert mock_cp.await_count == 1
        assert "USER ANSWERED" in out

    async def test_no_router_goes_straight_to_user(self):
        skill = AskBrainSkill(config=None, eco_router=None)
        with patch(
            "lazyclaw.browser.checkpoints.request_checkpoint",
            AsyncMock(return_value={"approved": True, "reason": "yes"}),
        ) as mock_cp:
            out = await skill.execute("u1", {"question": "stuck"})
        assert mock_cp.await_count == 1
        assert "USER ANSWERED" in out
