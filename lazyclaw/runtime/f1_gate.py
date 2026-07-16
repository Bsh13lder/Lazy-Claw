"""F1 grounding gate for the specialist path — composes existing detectors.

Background
==========
The brain (``agent.py``) runs a mechanical F1/anti-confabulation backstop
on every channel-read turn (see the block around ``agent.py:4661``). The
specialist runner (``teams/runner.py``) historically ran NONE of these
checks — it shipped the worker's reply raw. Channel-reading specialists
(freelance/messaging/email read Upwork/WhatsApp/email) could therefore
confabulate quotes with no deterministic backstop.

This module gives the specialist path the SAME mechanical enforcement, by
composing the EXISTING detectors into one entry point. It does NOT
reimplement detection:

  * ``f1_confabulation_detector.detect_confabulation`` — failure-claim,
    made-up-quote, and wikilink-in-quote checks.
  * ``f1_content_verifier.phase2_enforcement_verdict`` — channel-gated
    unverified-quote enforcement (single-quote granularity that the
    majority-rule made-up-quote check can miss).
  * ``f1_confabulation_detector.build_raw_data_injection`` — the corrective
    system message that spoon-feeds the raw tool output verbatim.

Channel-read gating mirrors ``agent._is_channel_read_tool`` (the exact
function the brain's F1 block uses). On NON-channel-read turns the gate is
observe-only and always returns ``ok=True`` — never block research / code /
web specialists whose "quotes" are paraphrases of prior context.

Safety posture
==============
The gate is anti-confabulation tooling but must NEVER take down a
specialist run. Every code path is wrapped so a detector crash, a stale
import, or a malformed history degrades to ``ok=True`` (FAIL-OPEN) with a
logged warning. A buggy regex must not silence a specialist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroundingVerdict:
    """Outcome of running the specialist-path grounding gate on one reply.

    Immutable so the runner can log / forward it without defensive copies.

    Fields:
        ok: True iff the reply is grounded (or the turn is observe-only,
            or a detector failed open). The runner ships the reply as-is
            when ``ok`` is True.
        reason: Human-readable explanation of WHY the reply was blocked,
            for log context. ``None`` when ``ok`` is True.
        corrective_injection: The raw-data corrective system message
            (built via ``build_raw_data_injection``) that the runner
            appends to the conversation before re-rolling the worker.
            ``None`` when ``ok`` is True.
    """

    ok: bool
    reason: str | None = None
    corrective_injection: str | None = None


# A reusable clean verdict — the common case (most turns are fine).
_OK_VERDICT: GroundingVerdict = GroundingVerdict(ok=True)


def _any_channel_read_called(tool_history: list[str]) -> bool:
    """True iff any tool in ``tool_history`` is a live channel read.

    Mirrors the brain's F1 gate, which uses ``agent._is_channel_read_tool``
    (NOT the broader ``_is_channel_read_tool_name``). Importing it lazily
    keeps ``f1_gate`` import-light and avoids a circular dependency at
    module load. If the import fails (stale container / refactor), we fall
    back to a self-contained mirror rather than crash — the gate must
    still function.
    """
    if not tool_history:
        return False
    try:
        from lazyclaw.runtime.agent import _is_channel_read_tool

        return any(_is_channel_read_tool(n) for n in tool_history)
    except Exception as exc:
        # Fallback: reuse the detector module's channel-read patterns so
        # the gate keeps working even if agent.py can't be imported.
        logger.warning(
            "[f1] agent._is_channel_read_tool import failed; using "
            "verifier fallback error_type=%s: %s",
            type(exc).__name__, exc, exc_info=True,
        )
        try:
            from lazyclaw.runtime.f1_content_verifier import (
                _was_channel_read_tool_called,
            )

            return _was_channel_read_tool_called(tool_history)
        except Exception as exc2:
            logger.warning(
                "[f1] verifier fallback also failed; treating turn as "
                "non-channel-read error_type=%s: %s",
                type(exc2).__name__, exc2, exc_info=True,
            )
            return False


def evaluate_grounding(
    reply: str,
    tool_history: list[str],
    tool_results: list[str],
) -> GroundingVerdict:
    """Run the mechanical F1 grounding checks for the specialist path.

    Composition mirrors the brain's F1 backstop (``agent.py`` ~4661):

      1. If NO channel-read tool ran this turn → ``ok=True`` immediately.
         Non-channel turns are observe-only and never blocked.
      2. On a channel-read turn, run ``detect_confabulation`` and (if
         available) ``phase2_enforcement_verdict``. If EITHER flags a
         violation, build the raw-data corrective injection and return
         ``ok=False`` carrying it.
      3. Otherwise → ``ok=True``.

    FAIL-OPEN: any exception anywhere degrades to ``ok=True`` with a
    logged warning. A crashing detector must never break the specialist.

    Pure-ish: never mutates its inputs. Never raises.
    """
    try:
        # Step 1 — observe-only on non-channel-read turns.
        if not _any_channel_read_called(tool_history or []):
            logger.debug(
                "[f1] evaluate_grounding: non-channel-read turn "
                "(tool_calls=%d) — observe-only, ok=True",
                len(tool_history or []),
            )
            return _OK_VERDICT

        # Lazy imports keep this module import-light AND let the
        # fail-open wrapper catch a stale/missing detector module.
        from lazyclaw.runtime.f1_confabulation_detector import (
            build_raw_data_injection,
            detect_confabulation,
        )

        # phase-2 enforcement is best-effort — mirror agent.py's
        # try/except ImportError so a stale container still gates on the
        # primary detector.
        try:
            from lazyclaw.runtime.f1_content_verifier import (
                phase2_enforcement_verdict,
            )
        except ImportError:
            phase2_enforcement_verdict = None  # type: ignore[assignment]

        # Step 2a — primary confabulation detector.
        verdict = detect_confabulation(
            reply or "", tool_history, tool_results,
        )
        logger.debug(
            "[f1] evaluate_grounding: primary verdict kind=%r "
            "is_confabulation=%s",
            verdict.kind, verdict.is_confabulation,
        )

        # Step 2b — phase-2 channel-gated unverified-quote enforcement.
        phase2_block = False
        phase2_reason = ""
        if phase2_enforcement_verdict is not None:
            phase2_block, phase2_reason = phase2_enforcement_verdict(
                reply or "", tool_history, tool_results,
            )
            logger.debug(
                "[f1] evaluate_grounding: phase2_block=%s", phase2_block,
            )

        if not (verdict.is_confabulation or phase2_block):
            return _OK_VERDICT

        # Step 2c — a violation fired. Build the corrective injection that
        # spoon-feeds the raw tool data verbatim. ``build_raw_data_injection``
        # selects the most-recent successful read payload itself.
        if verdict.is_confabulation:
            # NEVER log the offending phrase verbatim — it can carry
            # reply-body text (failure_claim) or a quote-block wikilink
            # token (wikilink_in_quote). ``reason`` is log-context only
            # (consumed solely by logger.warning in teams/runner.py) —
            # length is enough to size the finding.
            reason = (
                f"confabulation kind={verdict.kind!r} "
                f"tool={verdict.tool_name!r} "
                f"phrase_len={len(verdict.offending_phrase or '')} "
                f"unverified_quotes={verdict.unverified_quote_count}"
            )
            inj_reason = "confabulation"
        else:
            # phase-2-only block — synthesize the wording via the
            # made-up-quote reason (raw data is still injected).
            reason = f"phase2 unverified quote: {phase2_reason}"
            inj_reason = "confabulation"

        corrective = build_raw_data_injection(
            verdict,
            tool_history,
            tool_results,
            reason=inj_reason,
        )
        logger.debug(
            "[f1] evaluate_grounding: blocking (ok=False) kind=%r "
            "corrective_len=%d",
            verdict.kind, len(corrective or ""),
        )
        return GroundingVerdict(
            ok=False,
            reason=reason,
            corrective_injection=corrective,
        )
    except Exception as exc:
        # FAIL-OPEN: a crashing detector must not break the specialist.
        logger.warning(
            "[f1] grounding evaluation raised — failing OPEN (shipping "
            "specialist reply un-gated) error_type=%s: %s",
            type(exc).__name__, exc, exc_info=True,
        )
        return _OK_VERDICT
