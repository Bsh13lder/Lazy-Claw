"""MCP server for sending/reading/searching email via SMTP+IMAP."""

from __future__ import annotations

import asyncio
import email as email_lib
import email.header
import email.utils
import imaplib
import json
import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_email.providers import detect_provider

logger = logging.getLogger(__name__)

app = Server("mcp-email")

# Active email configs keyed by email address
_configs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="email_setup",
            description=(
                "Configure email credentials. Auto-detects SMTP/IMAP settings for "
                "Gmail, Outlook, Yahoo, iCloud. Tests connection before confirming."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email address to configure",
                    },
                    "password": {
                        "type": "string",
                        "description": "Email password or app password",
                    },
                    "smtp_host": {
                        "type": "string",
                        "description": "SMTP server host (auto-detected if omitted)",
                    },
                    "smtp_port": {
                        "type": "integer",
                        "description": "SMTP server port (auto-detected if omitted)",
                    },
                    "imap_host": {
                        "type": "string",
                        "description": "IMAP server host (auto-detected if omitted)",
                    },
                    "imap_port": {
                        "type": "integer",
                        "description": "IMAP server port (auto-detected if omitted)",
                    },
                },
                "required": ["email", "password"],
            },
        ),
        Tool(
            name="email_status",
            description=(
                "Check email connection status. If email omitted, lists all configured accounts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email address to check (omit to list all)",
                    },
                },
            },
        ),
        Tool(
            name="email_send",
            description="Send an email from a configured account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Sender email (must be configured via email_setup)",
                    },
                    "to": {
                        "type": "string",
                        "description": "Recipient email address",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text",
                    },
                    "cc": {
                        "type": "string",
                        "description": "CC recipients (comma-separated)",
                    },
                    "bcc": {
                        "type": "string",
                        "description": "BCC recipients (comma-separated)",
                    },
                    "html": {
                        "type": "boolean",
                        "description": "Send body as HTML (default false)",
                        "default": False,
                    },
                },
                "required": ["email", "to", "subject", "body"],
            },
        ),
        Tool(
            name="email_read",
            description="Read recent emails from a configured account via IMAP.",
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email account to read from",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Mailbox folder (default INBOX)",
                        "default": "INBOX",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max emails to return (default 10)",
                        "default": 10,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only return unread emails (default true)",
                        "default": True,
                    },
                },
                "required": ["email"],
            },
        ),
        Tool(
            name="email_search",
            description="Search emails by query string via IMAP SEARCH.",
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email account to search",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (IMAP search term, e.g. 'FROM john subject invoice')",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Mailbox folder (default INBOX)",
                        "default": "INBOX",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                        "default": 10,
                    },
                },
                "required": ["email", "query"],
            },
        ),
        Tool(
            name="email_folders",
            description="List available mailbox folders for a configured account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email account to list folders for",
                    },
                },
                "required": ["email"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
        "email_setup": _handle_setup,
        "email_status": _handle_status,
        "email_send": _handle_send,
        "email_read": _handle_read,
        "email_search": _handle_search,
        "email_folders": _handle_folders,
    }
    handler = handlers.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        result = await handler(arguments)
        return [TextContent(type="text", text=result)]
    except Exception as exc:
        logger.error("Tool %s failed: %s", name, exc, exc_info=True)
        return [TextContent(type="text", text=f"Error in {name}: {exc}")]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _handle_setup(args: dict) -> str:
    addr = args["email"]
    password = args["password"]

    provider = detect_provider(addr)
    smtp_host = args.get("smtp_host") or (provider or {}).get("smtp_host")
    smtp_port = args.get("smtp_port") or (provider or {}).get("smtp_port")
    imap_host = args.get("imap_host") or (provider or {}).get("imap_host")
    imap_port = args.get("imap_port") or (provider or {}).get("imap_port")

    if not smtp_host or not imap_host:
        return (
            f"Cannot auto-detect provider for {addr}. "
            "Please provide smtp_host, smtp_port, imap_host, imap_port."
        )

    cfg = {
        "email": addr,
        "password": password,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port or 587,
        "imap_host": imap_host,
        "imap_port": imap_port or 993,
    }

    # Test SMTP connection
    try:
        smtp = aiosmtplib.SMTP(
            hostname=cfg["smtp_host"],
            port=cfg["smtp_port"],
            use_tls=(cfg["smtp_port"] == 465),
            start_tls=(cfg["smtp_port"] == 587),
        )
        await smtp.connect()
        await smtp.login(addr, password)
    except Exception as exc:
        return f"SMTP connection failed for {addr}: {exc}"
    finally:
        try:
            await smtp.quit()
        except Exception:
            pass

    _configs[addr] = cfg

    note = ""
    if provider and provider.get("note"):
        note = f"\nNote: {provider['note']}"
    return f"Connected as {addr} (SMTP: {smtp_host}:{smtp_port}, IMAP: {imap_host}:{imap_port}){note}"


async def _handle_status(args: dict) -> str:
    addr = args.get("email")

    if not addr:
        if not _configs:
            return "No email accounts configured. Use email_setup first."
        accounts = []
        for a in _configs:
            domain = a.rsplit("@", 1)[-1] if "@" in a else "unknown"
            accounts.append({"email": a, "provider": domain, "status": "configured"})
        return json.dumps(accounts, indent=2)

    cfg = _configs.get(addr)
    if not cfg:
        return json.dumps({"email": addr, "status": "not_configured"})

    domain = addr.rsplit("@", 1)[-1] if "@" in addr else "unknown"
    smtp = aiosmtplib.SMTP(
        hostname=cfg["smtp_host"],
        port=cfg["smtp_port"],
        use_tls=(cfg["smtp_port"] == 465),
        start_tls=(cfg["smtp_port"] == 587),
    )
    try:
        await smtp.connect()
        await smtp.login(cfg["email"], cfg["password"])
        return json.dumps({"email": addr, "status": "connected", "provider": domain})
    except Exception as exc:
        return json.dumps({"email": addr, "status": f"error: {exc}", "provider": domain})
    finally:
        try:
            await smtp.quit()
        except Exception:
            pass


async def _handle_send(args: dict) -> str:
    addr = args["email"]
    cfg = _configs.get(addr)
    if not cfg:
        return f"Account {addr} not configured. Use email_setup first."

    to = args["to"]
    subject = args["subject"]
    body = args["body"]
    cc = args.get("cc", "")
    bcc = args.get("bcc", "")
    is_html = args.get("html", False)

    if is_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "html"))
    else:
        msg = MIMEText(body)

    msg["Subject"] = subject
    msg["From"] = addr
    msg["To"] = to
    if cc:
        msg["Cc"] = cc

    all_recipients = [r.strip() for r in to.split(",")]
    if cc:
        all_recipients.extend(r.strip() for r in cc.split(","))
    if bcc:
        all_recipients.extend(r.strip() for r in bcc.split(","))

    smtp = aiosmtplib.SMTP(
        hostname=cfg["smtp_host"],
        port=cfg["smtp_port"],
        use_tls=(cfg["smtp_port"] == 465),
        start_tls=(cfg["smtp_port"] == 587),
    )
    try:
        await smtp.connect()
        await smtp.login(cfg["email"], cfg["password"])
        await smtp.send_message(msg, recipients=all_recipients)
    finally:
        try:
            await smtp.quit()
        except Exception:
            pass

    return f"Sent to {to}"


async def _handle_read(args: dict) -> str:
    addr = args["email"]
    cfg = _configs.get(addr)
    if not cfg:
        return f"Account {addr} not configured. Use email_setup first."

    folder = args.get("folder", "INBOX")
    limit = args.get("limit", 10)
    unread_only = args.get("unread_only", True)

    results = await asyncio.to_thread(
        _imap_read_sync, cfg, folder, limit, unread_only
    )
    return json.dumps(results, indent=2, ensure_ascii=False)


async def _handle_search(args: dict) -> str:
    addr = args["email"]
    cfg = _configs.get(addr)
    if not cfg:
        return f"Account {addr} not configured. Use email_setup first."

    query = args["query"]
    folder = args.get("folder", "INBOX")
    limit = args.get("limit", 10)

    results = await asyncio.to_thread(
        _imap_search_sync, cfg, query, folder, limit
    )
    return json.dumps(results, indent=2, ensure_ascii=False)


async def _handle_folders(args: dict) -> str:
    addr = args["email"]
    cfg = _configs.get(addr)
    if not cfg:
        return f"Account {addr} not configured. Use email_setup first."

    folders = await asyncio.to_thread(_imap_folders_sync, cfg)
    return json.dumps(folders, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Synchronous IMAP helpers (run in thread via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _decode_header_value(raw: str | None) -> str:
    """Decode an RFC 2047 encoded header value to a plain string."""
    if not raw:
        return ""
    parts = email_lib.header.decode_header(raw)
    decoded = []
    for content, charset in parts:
        if isinstance(content, bytes):
            decoded.append(content.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(content)
    return " ".join(decoded)


def _extract_body_snippet(msg: email_lib.message.Message, max_chars: int = 500) -> str:
    """Extract the first max_chars of plain text from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")[:max_chars]
        # Fallback to HTML if no plain text
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")[:max_chars]
        return ""
    payload = msg.get_payload(decode=True)
    if payload:
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")[:max_chars]
    return ""


def _parse_message(data: bytes) -> dict:
    """Parse raw email bytes into a structured dict."""
    msg = email_lib.message_from_bytes(data)
    return {
        "from": _decode_header_value(msg.get("From")),
        "subject": _decode_header_value(msg.get("Subject")),
        "date": msg.get("Date", ""),
        "snippet": _extract_body_snippet(msg),
    }


def _imap_read_sync(
    cfg: dict, folder: str, limit: int, unread_only: bool
) -> list[dict]:
    """Read recent emails via stdlib imaplib (synchronous)."""
    imap = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
    try:
        imap.login(cfg["email"], cfg["password"])
        imap.select(folder, readonly=True)

        criterion = "UNSEEN" if unread_only else "ALL"
        _, msg_data = imap.search(None, criterion)
        msg_ids = msg_data[0].split()
        if not msg_ids:
            return []

        # Take the last `limit` (most recent)
        selected = msg_ids[-limit:]
        results = []
        for mid in reversed(selected):
            _, fetch_data = imap.fetch(mid, "(FLAGS RFC822)")
            if not fetch_data or fetch_data[0] is None:
                continue
            raw = fetch_data[0][1] if isinstance(fetch_data[0], tuple) else None
            if not raw:
                continue
            flags_raw = fetch_data[0][0] if isinstance(fetch_data[0], tuple) else b""
            parsed = _parse_message(raw)
            parsed["uid"] = mid.decode("utf-8", errors="replace")
            parsed["is_read"] = b"\\Seen" in flags_raw
            results.append(parsed)

        return results
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _build_imap_search_criteria(query: str) -> str:
    """Build IMAP SEARCH criteria from a simple query string.

    Supports patterns like:
      'FROM john'          -> (FROM "john")
      'SUBJECT invoice'    -> (SUBJECT "invoice")
      'hello'              -> (OR FROM "hello" SUBJECT "hello")
    """
    q = query.strip()
    upper = q.upper()

    # Already looks like raw IMAP criteria
    imap_keywords = ("FROM", "TO", "SUBJECT", "BODY", "SINCE", "BEFORE", "ON",
                     "SEEN", "UNSEEN", "FLAGGED", "UNFLAGGED", "OR", "NOT")
    if any(upper.startswith(kw + " ") or upper == kw for kw in imap_keywords):
        return q

    # Simple keyword — search in FROM and SUBJECT
    q_escaped = q.replace('"', '\\"')
    return f'(OR FROM "{q_escaped}" SUBJECT "{q_escaped}")'


def _imap_search_sync(
    cfg: dict, query: str, folder: str, limit: int
) -> list[dict]:
    """Search emails via stdlib imaplib (synchronous)."""
    imap = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
    try:
        imap.login(cfg["email"], cfg["password"])
        imap.select(folder, readonly=True)

        criteria = _build_imap_search_criteria(query)
        _, msg_data = imap.search(None, criteria)
        msg_ids = msg_data[0].split()
        if not msg_ids:
            return []

        selected = msg_ids[-limit:]
        results = []
        for mid in reversed(selected):
            _, fetch_data = imap.fetch(mid, "(FLAGS RFC822)")
            if not fetch_data or fetch_data[0] is None:
                continue
            raw = fetch_data[0][1] if isinstance(fetch_data[0], tuple) else None
            if not raw:
                continue
            flags_raw = fetch_data[0][0] if isinstance(fetch_data[0], tuple) else b""
            parsed = _parse_message(raw)
            parsed["uid"] = mid.decode("utf-8", errors="replace")
            parsed["is_read"] = b"\\Seen" in flags_raw
            results.append(parsed)

        return results
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _imap_folders_sync(cfg: dict) -> list[str]:
    """List IMAP folders via stdlib imaplib (synchronous)."""
    imap = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
    try:
        imap.login(cfg["email"], cfg["password"])
        _, folder_data = imap.list()
        folders = []
        for item in folder_data:
            if isinstance(item, bytes):
                # Format: (\Flags) "delimiter" "FolderName"
                decoded = item.decode("utf-8", errors="replace")
                match = re.match(r'\([^)]*\)\s+(?:NIL|"[^"]*")\s+(.+)$', decoded)
                if match:
                    name = match.group(1).strip().strip('"')
                    folders.append(name)
        return folders
    finally:
        try:
            imap.logout()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    async def _run() -> None:
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    asyncio.run(_run())
