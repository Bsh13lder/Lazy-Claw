"""Unit tests for the structured Telegram reminder card (telegram_cards)."""
from lazyclaw.channels.telegram_cards import render_reminder_card


def test_reminder_card_has_title_and_due():
    out = render_reminder_card({"title": "Pay invoice", "due_human": "14:00"})
    assert "⏰ Reminder · Pay invoice" in out
    assert "14:00" in out


def test_reminder_card_handles_missing_fields():
    out = render_reminder_card({"title": "Call James"})
    assert out == "⏰ Reminder · Call James"  # title-only, no meta line


def test_reminder_card_defaults_title():
    out = render_reminder_card({})
    assert "Task" in out


def test_reminder_card_preserves_priority_category_nag():
    out = render_reminder_card({
        "title": "Ship build",
        "priority": "high",
        "category": "work",
        "due_human": "17:30",
        "nag_count": 1,
    })
    assert "Ship build" in out
    assert "high" in out          # priority preserved
    assert "work" in out          # category preserved
    assert "17:30" in out         # time preserved
    assert "reminder #2" in out   # nag_count + 1


def test_reminder_card_medium_priority_has_no_icon_noise():
    out = render_reminder_card({"title": "x", "priority": "medium"})
    # medium maps to no icon and is not surfaced as a noisy chip
    assert out == "⏰ Reminder · x"
