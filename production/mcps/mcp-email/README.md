# mcp-email

**Email MCP Server** -- Full email management via SMTP + IMAP. Works with Gmail, Outlook, Yahoo, iCloud, and any custom provider. No browser, no API keys -- just email credentials.

## What It Does

Gives your AI agent complete email capabilities through standard protocols:

- **Send emails** (text or HTML, with CC/BCC)
- **Read inbox** (unread or all, any folder)
- **Search emails** by sender, subject, or content
- **Organize** -- move, label, mark read/unread, flag
- **Delete emails** (moves to Trash on Gmail)
- **Create labels/folders**
- **Multi-account support** -- configure multiple email addresses
- **Auto-detection** -- knows Gmail, Outlook, Yahoo, iCloud settings automatically

## Architecture

```
AI Agent <--stdio--> mcp-email <--SMTP--> Send emails
                         |
                         +----<--IMAP--> Read/search/organize
```

- **Runtime**: Python 3.11+
- **Transport**: stdio (MCP standard)
- **Send**: aiosmtplib (async SMTP)
- **Read**: stdlib imaplib (sync, run in thread)
- **Persistence**: JSON config file for account credentials

## Setup

### Prerequisites
- Python 3.11+

### Install
```bash
cd production/mcps/mcp-email
pip install -e .
```

### For Gmail Users
1. Enable 2-Step Verification in Google Account
2. Generate an App Password: Google Account > Security > App Passwords
3. Use the App Password (not your regular password) when configuring

### Register with LazyClaw
```json
{
  "name": "mcp-email",
  "command": "python",
  "args": ["-m", "mcp_email"],
  "transport": "stdio"
}
```

## Available Tools (11)

| Tool | Description |
|------|-------------|
| `email_setup` | Configure email credentials (auto-detects provider settings) |
| `email_status` | Check connection status / list configured accounts |
| `email_send` | Send email (text/HTML, CC, BCC) |
| `email_read` | Read recent emails (unread_only flag, folder selection) |
| `email_search` | Search by IMAP criteria (FROM, SUBJECT, etc.) |
| `email_folders` | List available mailbox folders |
| `email_delete` | Delete emails by UID |
| `email_move` | Move emails to different folder (removes from source) |
| `email_mark` | Mark as read/unread/flagged/unflagged |
| `email_create_label` | Create new folder/label |
| `email_label` | Add label WITHOUT removing from current folder |

## Supported Providers (Auto-Detected)

| Provider | SMTP | IMAP | Notes |
|----------|------|------|-------|
| Gmail | smtp.gmail.com:587 | imap.gmail.com:993 | Needs App Password |
| Outlook/Hotmail/Live | smtp-mail.outlook.com:587 | outlook.office365.com:993 | |
| Yahoo | smtp.mail.yahoo.com:587 | imap.mail.yahoo.com:993 | |
| iCloud/me.com | smtp.mail.me.com:587 | imap.mail.me.com:993 | |
| Custom | User-provided | User-provided | Any SMTP+IMAP provider |

## Data Storage

Credentials persisted in `data/email_configs.json` (or `~/.lazyclaw/email_configs.json`).

---

## Analysis

### Viral Potential: HIGH

**Reasoning**: Email is universal. Every knowledge worker checks email multiple times a day. An AI that can read, triage, respond to, and organize your email is a killer app. "My AI organized my inbox into labels and drafted replies" is a demo that sells itself.

**Key viral moments**:
- AI triages 200 unread emails into categories
- AI drafts reply suggestions for each email
- AI searches across your inbox to find that one receipt from 3 months ago
- Bulk label/organize operations that would take humans 30+ minutes

### Known Bugs & Issues

1. **Credentials stored in plaintext JSON** -- `email_configs.json` has email + password in cleartext on disk. This is the biggest issue. Should use LazyClaw's crypto vault.
2. **No connection pooling** -- Every IMAP operation opens a new connection, logs in, does the work, and logs out. Extremely wasteful for bulk operations. Should maintain a persistent connection with keepalive.
3. **IMAP search is simplistic** -- The `_build_imap_search_criteria()` function handles basic patterns but doesn't support date ranges, body search, or complex boolean queries well.
4. **No attachment support** -- Can't read or send attachments. Major gap for a production email tool.
5. **Gmail IMAP quota risk** -- Rapid-fire IMAP operations can hit Gmail's rate limits (15 connections/account, 250 operations/minute). No rate limiting implemented.
6. **aiosmtplib connection test** -- The setup function tests SMTP but not IMAP. A bad IMAP password would pass setup and fail on first read.
7. **aioimaplib listed but unused** -- pyproject.toml lists `aioimaplib` as a dependency but the code uses stdlib `imaplib` with `asyncio.to_thread()`. Wasted dependency.

### Public vs Private Recommendation: PUBLIC

**Recommendation**: Open-source. Email MCP is table-stakes for any AI agent platform.

**Why public**:
- High viral potential -- everyone uses email
- Standard protocols (SMTP/IMAP) -- no proprietary API risk
- Feature-rich (11 tools) -- not a toy demo
- Gmail-specific handling shows polish

**Before release**:
- Encrypt stored credentials via vault
- Remove unused `aioimaplib` dependency
- Add attachment support (at minimum reading)
- Add IMAP connection test during setup
- Add rate limiting for Gmail
