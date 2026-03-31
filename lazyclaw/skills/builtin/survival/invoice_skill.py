"""Create and send Stripe invoices for completed gigs."""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class InvoiceClientSkill(BaseSkill):
    """Create a Stripe invoice for a delivered gig."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "invoice_client"

    @property
    def description(self) -> str:
        return (
            "Create and send a Stripe invoice for a delivered gig. "
            "You (the founder) must approve before sending. "
            "Usage: 'invoice client for gig 1' or 'send invoice $500'"
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
                "amount": {
                    "type": "number",
                    "description": "Invoice amount in USD (defaults to gig budget)",
                },
                "client_email": {
                    "type": "string",
                    "description": "Client's email for the invoice",
                },
                "currency": {
                    "type": "string",
                    "description": "Currency code (default: usd)",
                    "default": "usd",
                },
            },
            "required": ["gig_reference", "client_email"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.survival.gig import update_gig_status

        ref = params.get("gig_reference", "")
        client_email = params.get("client_email", "")
        currency = params.get("currency", "usd")

        if not client_email:
            return "Client email is required to send an invoice."

        gig = await self._find_gig(user_id, ref)
        if gig is None:
            return f"Gig '{ref}' not found. Use 'survival status' to see your gigs."

        if gig.status not in ("delivered", "review"):
            return (
                f"Gig '{gig.title}' is in status '{gig.status}'. "
                f"Can only invoice gigs that are delivered or reviewed."
            )

        # Determine amount
        amount = params.get("amount")
        if not amount:
            amount = gig.budget_value if gig.budget_value > 0 else 0
        if not amount or amount <= 0:
            return (
                f"No amount specified and gig budget is unknown. "
                f"Please specify: 'invoice client $500 for gig {ref}'"
            )

        # Try Stripe MCP
        stripe_tool = self._find_stripe_tool()
        if stripe_tool is not None:
            return await self._invoice_via_stripe(
                user_id, gig, amount, client_email, currency, stripe_tool,
            )

        # Manual fallback
        await update_gig_status(
            self._config, user_id, gig.id, "invoiced",
            amount_earned=amount,
        )

        return (
            f"Invoice details for: **{gig.title}**\n\n"
            f"Amount: ${amount:.2f} {currency.upper()}\n"
            f"Client: {client_email}\n"
            f"Description: {gig.title}\n\n"
            f"Stripe MCP not connected — create invoice manually:\n"
            f"- Stripe Dashboard: https://dashboard.stripe.com/invoices/create\n"
            f"- Or use: 'connect mcp stripe' to enable automatic invoicing\n\n"
            f"Gig status updated to INVOICED."
        )

    async def _invoice_via_stripe(
        self, user_id: str, gig, amount: float,
        client_email: str, currency: str, stripe_tool,
    ) -> str:
        """Create and send invoice via Stripe MCP."""
        from lazyclaw.survival.gig import update_gig_status

        try:
            # Create invoice via Stripe MCP
            result = await stripe_tool.execute(user_id, {
                "customer_email": client_email,
                "amount": int(amount * 100),  # Stripe uses cents
                "currency": currency,
                "description": f"LazyClaw — {gig.title}",
            })

            result_str = result if isinstance(result, str) else str(result)

            # Extract invoice ID if present
            invoice_id = ""
            if "inv_" in result_str:
                import re
                match = re.search(r"(inv_\w+)", result_str)
                if match:
                    invoice_id = match.group(1)

            await update_gig_status(
                self._config, user_id, gig.id, "invoiced",
                invoice_id=invoice_id,
                amount_earned=amount,
            )

            return (
                f"Invoice sent for: **{gig.title}**\n\n"
                f"Amount: ${amount:.2f} {currency.upper()}\n"
                f"Sent to: {client_email}\n"
                f"{'Invoice ID: ' + invoice_id if invoice_id else ''}\n\n"
                f"Gig status: INVOICED\n"
                f"You'll be notified when payment is received."
            )

        except Exception as exc:
            logger.warning("Stripe invoice failed: %s", exc)
            return (
                f"Stripe invoice creation failed: {exc}\n\n"
                f"Create manually at: https://dashboard.stripe.com/invoices/create\n"
                f"Amount: ${amount:.2f} | Email: {client_email}"
            )

    def _find_stripe_tool(self):
        """Find Stripe MCP tool in registry."""
        if self._registry is None:
            return None
        for tool_info in self._registry.list_mcp_tools():
            func = tool_info.get("function", {})
            tname = func.get("name", "").lower()
            if "stripe" in tname and ("invoice" in tname or "create" in tname):
                tool = self._registry.get(func.get("name", ""))
                if tool is not None:
                    return tool
        return None

    async def _find_gig(self, user_id: str, ref: str):
        from lazyclaw.survival.gig import get_gig, list_gigs

        if len(ref) > 8:
            gig = await get_gig(self._config, user_id, ref)
            if gig:
                return gig

        if ref.isdigit():
            gigs = await list_gigs(self._config, user_id, limit=20)
            active = [g for g in gigs if g.status in ("delivered", "review")]
            idx = int(ref) - 1
            if 0 <= idx < len(active):
                return active[idx]

        gigs = await list_gigs(self._config, user_id, limit=50)
        ref_lower = ref.lower()
        for gig in gigs:
            if ref_lower in gig.title.lower():
                return gig

        return None
