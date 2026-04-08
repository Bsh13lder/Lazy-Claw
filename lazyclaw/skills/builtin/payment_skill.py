"""Payment management skill — save/list/delete cards, check payment settings.

Provides vault operations for payment cards and settings.
The actual payment flow (detect → approve → fill) happens in the browser
specialist using these tools + the browser skill.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class PaymentSkill(BaseSkill):
    """Manage payment cards and settings in the encrypted vault."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "payment"

    @property
    def display_name(self) -> str:
        return "payment"

    @property
    def description(self) -> str:
        return (
            "Manage payment cards and settings. "
            "save_card: store a card in encrypted vault. "
            "list_cards: show saved cards (masked). "
            "delete_card: remove a saved card. "
            "get_card: retrieve full card details for payment form filling. "
            "settings: view/update payment preferences (auto-buy limit, CVC save). "
            "detect_payment: check if current browser page is a checkout page."
        )

    @property
    def category(self) -> str:
        return "payment"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "save_card", "list_cards", "delete_card",
                        "get_card", "settings", "detect_payment",
                    ],
                    "description": (
                        "save_card: save card to vault (cardholder, card_number, expiry, cvc optional). "
                        "list_cards: show saved cards (masked numbers). "
                        "delete_card: delete a card by card_id. "
                        "get_card: get full card details for form filling (requires card_id). "
                        "settings: view or update payment preferences. "
                        "detect_payment: check if current page is a checkout page."
                    ),
                },
                "card_id": {
                    "type": "string",
                    "description": "Card identifier (e.g. 'main', 'business'). For save/get/delete.",
                },
                "cardholder": {
                    "type": "string",
                    "description": "Name on card (for save_card).",
                },
                "card_number": {
                    "type": "string",
                    "description": "Full card number (for save_card). Stored encrypted.",
                },
                "expiry": {
                    "type": "string",
                    "description": "Expiry date MM/YY (for save_card).",
                },
                "cvc": {
                    "type": "string",
                    "description": "CVC/CVV code (for save_card). Optional — user can choose not to save.",
                },
                "auto_buy_limit": {
                    "type": "number",
                    "description": "Max amount for auto-buy without approval (for settings). 0 = always ask.",
                },
                "save_cvc_pref": {
                    "type": "boolean",
                    "description": "Default preference for saving CVC on new cards (for settings).",
                },
                "require_approval": {
                    "type": "boolean",
                    "description": "Always require approval for payments (for settings).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        action = params.get("action", "")

        if not self._config:
            from lazyclaw.config import load_config
            self._config = load_config()

        try:
            if action == "save_card":
                return await self._save_card(user_id, params)
            elif action == "list_cards":
                return await self._list_cards(user_id)
            elif action == "delete_card":
                return await self._delete_card(user_id, params)
            elif action == "get_card":
                return await self._get_card(user_id, params)
            elif action == "settings":
                return await self._settings(user_id, params)
            elif action == "detect_payment":
                return await self._detect_payment(user_id)
            else:
                return f"Unknown action: {action}"
        except Exception as e:
            logger.error("Payment skill %s failed: %s", action, e)
            return f"Error: {e}"

    async def _save_card(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser.payment import save_card

        card_id = params.get("card_id", "main")
        cardholder = params.get("cardholder", "")
        card_number = params.get("card_number", "")
        expiry = params.get("expiry", "")
        cvc = params.get("cvc", "")

        if not card_number or not expiry:
            return "card_number and expiry are required to save a card."

        card = await save_card(
            self._config, user_id,
            card_id=card_id,
            cardholder=cardholder,
            card_number=card_number,
            expiry=expiry,
            cvc=cvc,
        )

        cvc_status = "saved" if card.has_cvc else "not saved (will ask each time)"
        return (
            f"Card saved: {card_id}\n"
            f"  Cardholder: {card.cardholder}\n"
            f"  Number: ****{card.last_four}\n"
            f"  Expiry: {card.expiry}\n"
            f"  CVC: {cvc_status}\n"
            f"\nAll card data is encrypted (AES-256-GCM)."
        )

    async def _list_cards(self, user_id: str) -> str:
        from lazyclaw.browser.payment import list_cards

        cards = await list_cards(self._config, user_id)
        if not cards:
            return "No payment cards saved. Use payment(action='save_card', ...) to add one."

        lines = [f"Saved cards ({len(cards)}):"]
        for card in cards:
            cvc_icon = "yes" if card.has_cvc else "no"
            lines.append(
                f"  {card.card_id}: ****{card.last_four} "
                f"({card.cardholder}, exp {card.expiry}, CVC: {cvc_icon})"
            )
        return "\n".join(lines)

    async def _delete_card(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser.payment import delete_card

        card_id = params.get("card_id", "")
        if not card_id:
            return "card_id required to delete a card."

        deleted = await delete_card(self._config, user_id, card_id)
        return f"Card '{card_id}' deleted." if deleted else f"Card '{card_id}' not found."

    async def _get_card(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser.payment import get_card

        card_id = params.get("card_id", "main")
        card = await get_card(self._config, user_id, card_id)

        if not card:
            return f"Card '{card_id}' not found. Use payment(action='list_cards') to see saved cards."

        result = (
            f"Card: {card.card_id}\n"
            f"  Cardholder: {card.cardholder}\n"
            f"  Number: {card.card_number}\n"
            f"  Expiry: {card.expiry}\n"
        )
        if card.has_cvc:
            result += f"  CVC: {card.cvc}\n"
        else:
            result += "  CVC: not saved — ask user for CVC before filling payment form.\n"

        return result

    async def _settings(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser.payment import (
            get_payment_settings,
            save_payment_settings,
            PaymentSettings,
        )

        # If any setting params provided, update
        has_update = any(
            params.get(k) is not None
            for k in ("auto_buy_limit", "save_cvc_pref", "require_approval")
        )

        current = await get_payment_settings(self._config, user_id)

        if has_update:
            updated = PaymentSettings(
                auto_buy_limit=(
                    params["auto_buy_limit"]
                    if params.get("auto_buy_limit") is not None
                    else current.auto_buy_limit
                ),
                save_cvc=(
                    params["save_cvc_pref"]
                    if params.get("save_cvc_pref") is not None
                    else current.save_cvc
                ),
                require_approval=(
                    params["require_approval"]
                    if params.get("require_approval") is not None
                    else current.require_approval
                ),
            )
            await save_payment_settings(self._config, user_id, updated)
            current = updated

        limit_str = f"${current.auto_buy_limit:.2f}" if current.auto_buy_limit > 0 else "disabled"
        return (
            f"Payment settings:\n"
            f"  Auto-buy limit: {limit_str}\n"
            f"  Save CVC by default: {'yes' if current.save_cvc else 'no'}\n"
            f"  Always require approval: {'yes' if current.require_approval else 'no'}"
        )

    async def _detect_payment(self, user_id: str) -> str:
        from lazyclaw.browser.payment import detect_payment_page
        from lazyclaw.skills.builtin.browser_actions.backends import get_cdp_backend as _get_cdp_backend

        backend = await _get_cdp_backend(user_id)
        result = await detect_payment_page(backend)

        if not result:
            return "Not a payment page."

        lines = ["Payment page detected:"]
        if result.get("priceText"):
            lines.append(f"  Amount: {result['priceText']}")
        if result.get("merchant"):
            lines.append(f"  Merchant: {result['merchant']}")
        if result.get("payButton"):
            lines.append(f"  Pay button: \"{result['payButton']}\"")
        if result.get("hasStripeFrame"):
            lines.append("  Payment: Stripe (iframe — may need VNC for CVC)")
        if result.get("hasPaypal"):
            lines.append("  Payment: PayPal available")
        if result.get("hasApplePay"):
            lines.append("  Payment: Apple Pay available")
        if result.get("hasGooglePay"):
            lines.append("  Payment: Google Pay available")
        if result.get("hasCardInput"):
            lines.append("  Payment: Direct card input form")

        return "\n".join(lines)
