"""Email provider auto-detection."""

from __future__ import annotations

EMAIL_PROVIDERS: dict[str, dict] = {
    "gmail.com": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "note": "Needs App Password from Google Account > Security > App Passwords",
    },
    "googlemail.com": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "note": "Needs App Password from Google Account > Security > App Passwords",
    },
    "outlook.com": {
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
    },
    "hotmail.com": {
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
    },
    "live.com": {
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
    },
    "yahoo.com": {
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": 993,
    },
    "icloud.com": {
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
    },
    "me.com": {
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
    },
}


def detect_provider(email: str) -> dict | None:
    """Auto-detect SMTP/IMAP settings from email domain.

    Returns provider dict with smtp_host, smtp_port, imap_host, imap_port,
    and optional 'note' field. Returns None if domain not recognized.
    """
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    return EMAIL_PROVIDERS.get(domain)
