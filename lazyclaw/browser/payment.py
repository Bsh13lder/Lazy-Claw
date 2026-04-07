"""Payment flow — saved cards, CVC vault, Telegram approval, auto-buy.

Three payment modes:
1. Auto: card + CVC saved in vault, user gave explicit buy command → just pay
2. Quick approve: agent fills form, sends Telegram "Pay $X? CVC?" → user replies
3. VNC: complex payment page → open VNC link for user to complete manually

Payment data is encrypted in the credential vault (AES-256-GCM).
Card info is stored per-user with configurable CVC save preference.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)

# Vault key prefixes for payment data
_CARD_PREFIX = "payment_card_"
_PAYMENT_SETTINGS_KEY = "payment_settings"


# ── Data models ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class PaymentCard:
    """Immutable payment card info (decrypted)."""

    card_id: str          # "card_1", "card_2"
    cardholder: str       # "JOHN DOE"
    last_four: str        # "4242"
    expiry: str           # "12/27"
    card_number: str      # Full number (only when needed for form fill)
    has_cvc: bool         # Whether CVC is saved
    cvc: str              # CVC value (empty if not saved)


@dataclass(frozen=True)
class PaymentSettings:
    """Immutable user payment preferences."""

    auto_buy_limit: float     # Max amount for no-approval purchases (0 = always ask)
    save_cvc: bool            # Default preference for saving CVC
    require_approval: bool    # Always require Telegram approval (even under limit)


@dataclass(frozen=True)
class PaymentRequest:
    """Immutable payment approval request."""

    merchant: str        # "amazon.com", "booking.com"
    amount: str          # "$149.00", "€50"
    description: str     # "Blue jacket, size M"
    card_id: str         # Which saved card to use
    needs_cvc: bool      # Whether CVC input is needed
    url: str             # Current checkout URL


# ── Card CRUD (encrypted vault) ─────────────────────────────────────

async def save_card(
    config: Config,
    user_id: str,
    card_id: str,
    cardholder: str,
    card_number: str,
    expiry: str,
    cvc: str = "",
) -> PaymentCard:
    """Save a payment card to the encrypted vault.

    Card number and CVC are encrypted. Only last 4 digits stored in
    the card_id metadata for display purposes.
    """
    from lazyclaw.crypto.vault import set_credential

    last_four = card_number[-4:] if len(card_number) >= 4 else card_number

    card_data = json.dumps({
        "cardholder": cardholder,
        "card_number": card_number,
        "last_four": last_four,
        "expiry": expiry,
        "cvc": cvc,
        "has_cvc": bool(cvc),
    })

    await set_credential(config, user_id, f"{_CARD_PREFIX}{card_id}", card_data)

    logger.info("Saved card %s (****%s) for user %s", card_id, last_four, user_id)

    return PaymentCard(
        card_id=card_id,
        cardholder=cardholder,
        last_four=last_four,
        expiry=expiry,
        card_number=card_number,
        has_cvc=bool(cvc),
        cvc=cvc,
    )


async def get_card(
    config: Config,
    user_id: str,
    card_id: str,
) -> PaymentCard | None:
    """Retrieve a saved payment card from the vault."""
    from lazyclaw.crypto.vault import get_credential

    raw = await get_credential(config, user_id, f"{_CARD_PREFIX}{card_id}")
    if not raw:
        return None

    try:
        data = json.loads(raw)
        return PaymentCard(
            card_id=card_id,
            cardholder=data.get("cardholder", ""),
            last_four=data.get("last_four", ""),
            expiry=data.get("expiry", ""),
            card_number=data.get("card_number", ""),
            has_cvc=data.get("has_cvc", False),
            cvc=data.get("cvc", ""),
        )
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Failed to parse card %s: %s", card_id, exc)
        return None


async def list_cards(
    config: Config,
    user_id: str,
) -> list[PaymentCard]:
    """List all saved payment cards (without full card numbers for display)."""
    from lazyclaw.crypto.vault import list_credentials, get_credential

    all_keys = await list_credentials(config, user_id)
    card_keys = [k for k in all_keys if k.startswith(_CARD_PREFIX)]

    cards: list[PaymentCard] = []
    for key in card_keys:
        card_id = key[len(_CARD_PREFIX):]
        card = await get_card(config, user_id, card_id)
        if card:
            # Return card without full number for listing
            cards.append(PaymentCard(
                card_id=card.card_id,
                cardholder=card.cardholder,
                last_four=card.last_four,
                expiry=card.expiry,
                card_number="",  # Hidden for listing
                has_cvc=card.has_cvc,
                cvc="",  # Hidden for listing
            ))

    return cards


async def delete_card(
    config: Config,
    user_id: str,
    card_id: str,
) -> bool:
    """Delete a saved payment card."""
    from lazyclaw.crypto.vault import delete_credential

    deleted = await delete_credential(config, user_id, f"{_CARD_PREFIX}{card_id}")
    if deleted:
        logger.info("Deleted card %s for user %s", card_id, user_id)
    return deleted


# ── Payment settings ─────────────────────────────────────────────────

async def get_payment_settings(
    config: Config,
    user_id: str,
) -> PaymentSettings:
    """Get user's payment preferences."""
    from lazyclaw.crypto.vault import get_credential

    raw = await get_credential(config, user_id, _PAYMENT_SETTINGS_KEY)
    if not raw:
        return PaymentSettings(
            auto_buy_limit=0.0,
            save_cvc=False,
            require_approval=True,
        )

    try:
        data = json.loads(raw)
        return PaymentSettings(
            auto_buy_limit=float(data.get("auto_buy_limit", 0)),
            save_cvc=bool(data.get("save_cvc", False)),
            require_approval=bool(data.get("require_approval", True)),
        )
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse payment settings, using defaults", exc_info=True)
        return PaymentSettings(
            auto_buy_limit=0.0,
            save_cvc=False,
            require_approval=True,
        )


async def save_payment_settings(
    config: Config,
    user_id: str,
    settings: PaymentSettings,
) -> None:
    """Save user's payment preferences."""
    from lazyclaw.crypto.vault import set_credential

    data = json.dumps({
        "auto_buy_limit": settings.auto_buy_limit,
        "save_cvc": settings.save_cvc,
        "require_approval": settings.require_approval,
    })
    await set_credential(config, user_id, _PAYMENT_SETTINGS_KEY, data)


# ── Payment detection ────────────────────────────────────────────────

# JS to detect if the current page is a checkout/payment page
DETECT_PAYMENT_JS = """
(() => {
    const indicators = {
        // Payment form fields
        hasCardInput: !!(
            document.querySelector('input[autocomplete*="cc-number"]') ||
            document.querySelector('input[name*="card"], input[name*="credit"]') ||
            document.querySelector('input[placeholder*="card number" i]') ||
            document.querySelector('[data-elements-stable-field-name="cardNumber"]')
        ),
        hasCvcInput: !!(
            document.querySelector('input[autocomplete="cc-csc"]') ||
            document.querySelector('input[name*="cvc"], input[name*="cvv"], input[name*="security"]') ||
            document.querySelector('input[placeholder*="CVC" i], input[placeholder*="CVV" i]')
        ),
        hasExpiryInput: !!(
            document.querySelector('input[autocomplete*="cc-exp"]') ||
            document.querySelector('input[name*="expir"], input[placeholder*="MM" i]')
        ),
        // Stripe iframes
        hasStripeFrame: !!(
            document.querySelector('iframe[src*="stripe.com"]') ||
            document.querySelector('iframe[name*="__privateStripe"]') ||
            document.querySelector('.StripeElement, [class*="stripe"]')
        ),
        // PayPal
        hasPaypal: !!(
            document.querySelector('iframe[src*="paypal.com"]') ||
            document.querySelector('[data-funding-source="paypal"]') ||
            document.querySelector('.paypal-button')
        ),
        // Apple Pay / Google Pay
        hasApplePay: !!(
            document.querySelector('[aria-label*="Apple Pay" i]') ||
            document.querySelector('.apple-pay-button')
        ),
        hasGooglePay: !!(
            document.querySelector('[aria-label*="Google Pay" i]') ||
            document.querySelector('.gpay-button')
        ),
        // Price detection
        priceText: (() => {
            const priceEls = document.querySelectorAll(
                '[class*="total" i], [class*="price" i], [class*="amount" i], ' +
                '[class*="sum" i], [data-testid*="total" i], [data-testid*="price" i]'
            );
            for (const el of priceEls) {
                const text = el.textContent.trim();
                const match = text.match(/[$€£¥₹]\\s*[\\d,.]+|[\\d,.]+\\s*[$€£¥₹]/);
                if (match) return match[0];
            }
            return null;
        })(),
        // Pay button
        payButton: (() => {
            const buttons = document.querySelectorAll('button, [role="button"], input[type="submit"]');
            for (const btn of buttons) {
                const text = (btn.textContent || btn.value || '').trim().toLowerCase();
                if (/pay|place order|complete purchase|buy now|checkout|confirm order/i.test(text)) {
                    return text.slice(0, 50);
                }
            }
            return null;
        })(),
        // Merchant name
        merchant: document.title || window.location.hostname,
    };

    const isPaymentPage = (
        indicators.hasCardInput ||
        indicators.hasStripeFrame ||
        indicators.hasPaypal ||
        indicators.payButton !== null
    );

    return JSON.stringify({
        isPaymentPage,
        ...indicators,
    });
})()
"""


async def detect_payment_page(backend) -> dict | None:
    """Detect if the current page is a checkout/payment page.

    Returns payment info dict if detected, None if not a payment page.
    """
    try:
        raw = await backend.evaluate(DETECT_PAYMENT_JS)
        if not raw:
            return None

        data = json.loads(raw) if isinstance(raw, str) else raw

        if not data.get("isPaymentPage"):
            return None

        return data

    except Exception as exc:
        logger.debug("Payment detection failed: %s", exc)
        return None


# ── Payment approval logic ───────────────────────────────────────────

async def should_auto_pay(
    config: Config,
    user_id: str,
    amount_str: str,
    explicit_buy_command: bool = False,
) -> bool:
    """Check if this payment should proceed without approval.

    Auto-pay conditions (ALL must be true):
    1. User gave explicit buy command ("buy me this jacket, blue, size M")
    2. A card with saved CVC exists in vault
    3. Amount is under user's auto_buy_limit
    4. require_approval is False
    """
    settings = await get_payment_settings(config, user_id)

    # Always require approval if setting is on
    if settings.require_approval:
        return False

    # Must have explicit buy command
    if not explicit_buy_command:
        return False

    # Check amount against limit
    if settings.auto_buy_limit <= 0:
        return False

    try:
        # Parse amount from string like "$149.00" or "€50"
        amount = float(
            amount_str.replace("$", "").replace("€", "")
            .replace("£", "").replace("¥", "").replace("₹", "")
            .replace(",", "").strip()
        )
        if amount > settings.auto_buy_limit:
            return False
    except (ValueError, AttributeError):
        logger.debug("Could not parse payment amount %r, requiring approval", amount_str, exc_info=True)
        return False  # Can't parse amount → require approval

    # Check if a card with CVC exists
    cards = await list_cards(config, user_id)
    has_full_card = any(c.has_cvc for c in cards)

    return has_full_card


def format_approval_message(request: PaymentRequest) -> str:
    """Format a Telegram-friendly payment approval message."""
    lines = [
        f"💳 Payment requested",
        f"",
        f"Merchant: {request.merchant}",
        f"Amount: {request.amount}",
    ]
    if request.description:
        lines.append(f"Item: {request.description}")

    lines.append(f"Card: ****{request.card_id[-4:]}" if request.card_id else "Card: (none saved)")

    if request.needs_cvc:
        lines.append("")
        lines.append("Reply with your CVC to approve, or 'cancel' to stop.")
    else:
        lines.append("")
        lines.append("Reply 'pay' to approve, or 'cancel' to stop.")

    return "\n".join(lines)
