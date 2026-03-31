"""Submit completed work to client."""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SubmitDeliverableSkill(BaseSkill):
    """Submit completed work to the client after founder approval."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "submit_deliverable"

    @property
    def description(self) -> str:
        return (
            "Submit completed work to the client. You (the founder) must approve first. "
            "For Upwork: fills the submission form via browser. "
            "For other platforms: provides formatted delivery message. "
            "Usage: 'submit deliverable for gig 1' or 'deliver my work'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "gig_reference": {
                    "type": "string",
                    "description": "Gig number, title, or ID",
                },
                "delivery_message": {
                    "type": "string",
                    "description": "Custom message to include with delivery",
                },
            },
            "required": ["gig_reference"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.survival.gig import get_gig, list_gigs, update_gig_status
        from lazyclaw.survival.profile import get_profile

        ref = params.get("gig_reference", "")
        custom_msg = params.get("delivery_message", "")

        gig = await self._find_gig(user_id, ref)
        if gig is None:
            return f"Gig '{ref}' not found. Use 'survival status' to see your gigs."

        if gig.status not in ("review", "working", "needs_work"):
            return (
                f"Gig '{gig.title}' is in status '{gig.status}'. "
                f"Can only submit gigs in review/working/needs_work status."
            )

        profile = await get_profile(self._config, user_id)

        # Build delivery message
        if profile.branding_mode == "lazyclaw":
            delivery_msg = self._lazyclaw_delivery_message(gig, custom_msg)
        else:
            delivery_msg = self._personal_delivery_message(gig, custom_msg)

        # Try browser submission for Upwork
        if gig.platform.lower() == "upwork" and gig.url:
            result = await self._submit_via_browser(user_id, gig, delivery_msg)
            if result:
                await update_gig_status(
                    self._config, user_id, gig.id, "delivered",
                    deliverable_summary=delivery_msg[:500],
                )
                return result

        # Manual submission path
        await update_gig_status(
            self._config, user_id, gig.id, "delivered",
            deliverable_summary=delivery_msg[:500],
        )

        return (
            f"Deliverable ready for: **{gig.title}**\n\n"
            f"Delivery message:\n---\n{delivery_msg}\n---\n\n"
            f"Platform: {gig.platform}\n"
            f"URL: {gig.url or 'N/A'}\n\n"
            f"Submit this message on the platform.\n"
            f"Gig status updated to DELIVERED.\n"
            f"Next: 'invoice client' to send a Stripe invoice."
        )

    def _lazyclaw_delivery_message(self, gig, custom_msg: str) -> str:
        base = (
            f"Hi! LazyClaw has completed your project: {gig.title}\n\n"
            f"All deliverables have been reviewed by our human founder "
            f"and passed our automated quality gate (code review + tests).\n\n"
        )
        if custom_msg:
            base += f"{custom_msg}\n\n"
        base += (
            "Summary of what was delivered:\n"
            f"- {gig.deliverable_summary or 'See attached files'}\n\n"
            "Please review and let me know if you need any adjustments.\n\n"
            "Best,\nLazyClaw"
        )
        return base

    def _personal_delivery_message(self, gig, custom_msg: str) -> str:
        base = f"Hi! I've completed the work on: {gig.title}\n\n"
        if custom_msg:
            base += f"{custom_msg}\n\n"
        base += (
            f"Summary: {gig.deliverable_summary or 'See attached files'}\n\n"
            "Please review and let me know if any changes are needed.\n\n"
            "Best regards"
        )
        return base

    async def _submit_via_browser(
        self, user_id: str, gig, delivery_msg: str,
    ) -> str | None:
        """Try to submit via Upwork browser. Returns None if browser unavailable."""
        browser = self._registry.get("browser") if self._registry else None
        if browser is None:
            return None

        try:
            await browser.execute(user_id, {"action": "open", "url": gig.url})
            await browser.execute(user_id, {"action": "read"})

            # Try to find and click submit/deliver button
            await browser.execute(user_id, {
                "action": "click",
                "target": "Submit Work",
            })
            await browser.execute(user_id, {"action": "read"})

            # Fill delivery message
            await browser.execute(user_id, {
                "action": "type",
                "target": "message",
                "text": delivery_msg,
            })

            return (
                f"Submission form filled for: **{gig.title}**\n\n"
                f"Message:\n---\n{delivery_msg}\n---\n\n"
                f"The form is ready in the browser — NOT submitted yet.\n"
                f"Say 'confirm submit' to click the Submit button."
            )
        except Exception as exc:
            logger.warning("Browser submission failed: %s", exc)
            return None

    async def _find_gig(self, user_id: str, ref: str):
        from lazyclaw.survival.gig import get_gig, list_gigs

        if len(ref) > 8:
            gig = await get_gig(self._config, user_id, ref)
            if gig:
                return gig

        if ref.isdigit():
            gigs = await list_gigs(self._config, user_id, limit=20)
            active = [g for g in gigs if g.status in (
                "working", "review", "needs_work", "delivered",
            )]
            idx = int(ref) - 1
            if 0 <= idx < len(active):
                return active[idx]

        gigs = await list_gigs(self._config, user_id, limit=50)
        ref_lower = ref.lower()
        for gig in gigs:
            if ref_lower in gig.title.lower():
                return gig

        return None
