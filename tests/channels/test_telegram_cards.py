"""Unit tests for structured Telegram result cards (telegram_cards)."""
from lazyclaw.channels.telegram_cards import (
    render_contract_card,
    render_expense_header,
    render_permissions_card,
    render_reminder_card,
    render_status_card,
)


def test_reminder_card_has_title_and_due():
    out = render_reminder_card({"title": "Pay invoice", "due_human": "14:00"})
    assert "⏰ Reminder · Pay invoice" in out
    assert "14:00" in out


def test_reminder_card_handles_missing_due():
    out = render_reminder_card({"title": "Call James"})
    assert "Call James" in out  # must not crash on absent due


def test_contract_card_shows_budget():
    out = render_contract_card({"title": "Scraper build", "budget": "$120"})
    assert "\U0001f514 New contract" in out
    assert "Scraper build" in out
    assert "$120" in out


def test_permissions_card_groups_states():
    out = render_permissions_card({"allow": ["tasks", "notes"], "ask": ["payment"],
                                   "deny": []})
    assert "✅" in out and "tasks" in out
    assert "❓" in out and "payment" in out


def test_permissions_card_empty():
    out = render_permissions_card({"allow": [], "ask": [], "deny": []})
    assert out  # non-empty, no crash


def test_status_card_summary_and_elapsed():
    out = render_status_card({"summary": "searching jobs", "elapsed_s": 12.0})
    assert "searching jobs" in out
    assert "12s" in out


def test_expense_header_mentions_amount():
    out = render_expense_header({"amount": "12.50", "currency": "EUR"})
    assert "12.50" in out
