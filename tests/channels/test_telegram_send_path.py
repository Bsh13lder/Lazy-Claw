"""Tests for the telegram.py send path: HTML→plain fallback + error card."""
import pytest
import telegram.error

from lazyclaw.channels import telegram as tg
from lazyclaw.channels.telegram_view import render_error


@pytest.mark.asyncio
async def test_send_falls_back_to_plain_text_on_badrequest():
    """A BadRequest (malformed entities) retries via plain_factory instead of
    dropping the message."""
    sent: list[tuple[str, object]] = []

    async def _html_send():
        raise telegram.error.BadRequest("can't parse entities")

    async def _plain_send():
        sent.append(("plain", None))
        return "ok"

    out = await tg._telegram_send_with_retry(_html_send, plain_factory=_plain_send)
    assert out == "ok"
    assert sent == [("plain", None)]


@pytest.mark.asyncio
async def test_send_reraises_badrequest_without_fallback():
    """Without a plain_factory a BadRequest still surfaces (no silent swallow)."""
    async def _html_send():
        raise telegram.error.BadRequest("boom")

    with pytest.raises(telegram.error.BadRequest):
        await tg._telegram_send_with_retry(_html_send)


def test_handler_error_uses_generic_card():
    card = render_error("generic")
    assert "Something went wrong" in card
    assert "Traceback" not in card
