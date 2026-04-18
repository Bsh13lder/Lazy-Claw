"""Pre-built n8n workflow templates for common automation patterns.

Each template is a dict with:
  - name: human-readable name
  - keywords: list of strings for matching user descriptions
  - description: what the flow does
  - build(params) -> dict: returns n8n workflow JSON with user params applied
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template builders
# ---------------------------------------------------------------------------

def _webhook_to_telegram(params: dict[str, Any]) -> dict:
    """Webhook trigger -> Telegram send message."""
    return {
        "name": params.get("name", "Webhook to Telegram"),
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": params.get("webhook_path", "notify")},
                "id": "webhook-1",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [250, 300],
                "webhookId": "",
            },
            {
                "parameters": {
                    "chatId": params.get("chat_id", ""),
                    "text": params.get("message_template", "={{ $json.body.message }}"),
                },
                "id": "telegram-1",
                "name": "Telegram",
                "type": "n8n-nodes-base.telegram",
                "typeVersion": 1.2,
                "position": [500, 300],
                "credentials": {"telegramApi": {"id": "", "name": "Telegram"}},
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Telegram", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _schedule_telegram(params: dict[str, Any]) -> dict:
    """Cron schedule -> Telegram message."""
    cron = params.get("cron", "0 9 * * *")
    return {
        "name": params.get("name", "Scheduled Telegram Message"),
        "nodes": [
            {
                "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": cron}]}},
                "id": "schedule-1",
                "name": "Schedule Trigger",
                "type": "n8n-nodes-base.scheduleTrigger",
                "typeVersion": 1.2,
                "position": [250, 300],
            },
            {
                "parameters": {
                    "chatId": params.get("chat_id", ""),
                    "text": params.get("message", "Scheduled reminder"),
                },
                "id": "telegram-1",
                "name": "Telegram",
                "type": "n8n-nodes-base.telegram",
                "typeVersion": 1.2,
                "position": [500, 300],
                "credentials": {"telegramApi": {"id": "", "name": "Telegram"}},
            },
        ],
        "connections": {
            "Schedule Trigger": {"main": [[{"node": "Telegram", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _email_to_telegram(params: dict[str, Any]) -> dict:
    """IMAP email trigger -> Telegram notification."""
    return {
        "name": params.get("name", "Email to Telegram"),
        "nodes": [
            {
                "parameters": {
                    "mailbox": "INBOX",
                    "options": {"unseen": True},
                },
                "id": "imap-1",
                "name": "Email Trigger (IMAP)",
                "type": "n8n-nodes-base.emailReadImap",
                "typeVersion": 2,
                "position": [250, 300],
                "credentials": {"imap": {"id": "", "name": "IMAP"}},
            },
            {
                "parameters": {
                    "chatId": params.get("chat_id", ""),
                    "text": "New email from {{ $json.from }}\nSubject: {{ $json.subject }}",
                },
                "id": "telegram-1",
                "name": "Telegram",
                "type": "n8n-nodes-base.telegram",
                "typeVersion": 1.2,
                "position": [500, 300],
                "credentials": {"telegramApi": {"id": "", "name": "Telegram"}},
            },
        ],
        "connections": {
            "Email Trigger (IMAP)": {"main": [[{"node": "Telegram", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _rss_to_telegram(params: dict[str, Any]) -> dict:
    """RSS feed trigger -> Telegram message."""
    return {
        "name": params.get("name", "RSS to Telegram"),
        "nodes": [
            {
                "parameters": {"url": params.get("feed_url", ""), "options": {}},
                "id": "rss-1",
                "name": "RSS Feed Trigger",
                "type": "n8n-nodes-base.rssFeedReadTrigger",
                "typeVersion": 1,
                "position": [250, 300],
            },
            {
                "parameters": {
                    "chatId": params.get("chat_id", ""),
                    "text": "New article: {{ $json.title }}\n{{ $json.link }}",
                },
                "id": "telegram-1",
                "name": "Telegram",
                "type": "n8n-nodes-base.telegram",
                "typeVersion": 1.2,
                "position": [500, 300],
                "credentials": {"telegramApi": {"id": "", "name": "Telegram"}},
            },
        ],
        "connections": {
            "RSS Feed Trigger": {"main": [[{"node": "Telegram", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _form_to_sheets(params: dict[str, Any]) -> dict:
    """Webhook form submission -> Google Sheets append."""
    return {
        "name": params.get("name", "Form to Google Sheets"),
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": params.get("webhook_path", "form")},
                "id": "webhook-1",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [250, 300],
                "webhookId": "",
            },
            {
                "parameters": {
                    "operation": "append",
                    "documentId": {"value": params.get("sheet_id", "")},
                    "sheetName": {"value": params.get("sheet_name", "Sheet1")},
                    "columns": {"mappingMode": "autoMapInputData"},
                },
                "id": "sheets-1",
                "name": "Google Sheets",
                "type": "n8n-nodes-base.googleSheets",
                "typeVersion": 4.5,
                "position": [500, 300],
                "credentials": {"googleSheetsOAuth2Api": {"id": "", "name": "Google Sheets"}},
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Google Sheets", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _drive_to_instagram(params: dict[str, Any]) -> dict:
    """Google Drive new file trigger -> HTTP post to Instagram."""
    return {
        "name": params.get("name", "Google Drive to Instagram"),
        "nodes": [
            {
                "parameters": {
                    "triggerOn": "specificFolder",
                    "folderToWatch": {"value": params.get("folder_id", "")},
                    "event": "fileCreated",
                    "options": {},
                },
                "id": "drive-1",
                "name": "Google Drive Trigger",
                "type": "n8n-nodes-base.googleDriveTrigger",
                "typeVersion": 1,
                "position": [250, 300],
                "credentials": {"googleDriveOAuth2Api": {"id": "", "name": "Google Drive"}},
            },
            {
                "parameters": {
                    "operation": "download",
                    "fileId": {"value": "={{ $json.id }}"},
                },
                "id": "drive-dl-1",
                "name": "Download File",
                "type": "n8n-nodes-base.googleDrive",
                "typeVersion": 3,
                "position": [500, 300],
                "credentials": {"googleDriveOAuth2Api": {"id": "", "name": "Google Drive"}},
            },
            {
                "parameters": {
                    "requestMethod": "POST",
                    "url": "={{ $json.webhook_url || '' }}",
                    "options": {},
                },
                "id": "http-1",
                "name": "Post to Instagram",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [750, 300],
            },
        ],
        "connections": {
            "Google Drive Trigger": {"main": [[{"node": "Download File", "type": "main", "index": 0}]]},
            "Download File": {"main": [[{"node": "Post to Instagram", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _google_trends_check(params: dict[str, Any]) -> dict:
    """Schedule trigger -> HTTP Request to Google Trends RSS."""
    keyword = params.get("keyword", "AI")
    return {
        "name": params.get("name", f"Google Trends: {keyword}"),
        "nodes": [
            {
                "parameters": {
                    "rule": {"interval": [{"field": "cronExpression", "expression": params.get("cron", "0 */6 * * *")}]},
                },
                "id": "schedule-1",
                "name": "Schedule Trigger",
                "type": "n8n-nodes-base.scheduleTrigger",
                "typeVersion": 1.2,
                "position": [250, 300],
            },
            {
                "parameters": {
                    "url": f"https://trends.google.com/trending/rss?geo={params.get('geo', 'US')}",
                    "options": {"response": {"response": {"responseFormat": "text"}}},
                },
                "id": "http-1",
                "name": "Google Trends",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [500, 300],
            },
        ],
        "connections": {
            "Schedule Trigger": {"main": [[{"node": "Google Trends", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _webhook_to_sheets(params: dict[str, Any]) -> dict:
    """Webhook trigger -> Google Sheets append (data collection)."""
    return {
        "name": params.get("name", "Webhook to Google Sheets"),
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": params.get("webhook_path", "collect")},
                "id": "webhook-1",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [250, 300],
                "webhookId": "",
            },
            {
                "parameters": {
                    "operation": "append",
                    "documentId": {"value": params.get("sheet_id", "")},
                    "sheetName": {"value": params.get("sheet_name", "Sheet1")},
                    "columns": {"mappingMode": "autoMapInputData"},
                },
                "id": "sheets-1",
                "name": "Google Sheets",
                "type": "n8n-nodes-base.googleSheets",
                "typeVersion": 4.5,
                "position": [500, 300],
                "credentials": {"googleSheetsOAuth2Api": {"id": "", "name": "Google Sheets"}},
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Google Sheets", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _schedule_http_telegram(params: dict[str, Any]) -> dict:
    """Schedule -> HTTP Request -> Telegram (periodic API check + notify)."""
    return {
        "name": params.get("name", "Periodic API Check to Telegram"),
        "nodes": [
            {
                "parameters": {
                    "rule": {"interval": [{"field": "cronExpression", "expression": params.get("cron", "0 */1 * * *")}]},
                },
                "id": "schedule-1",
                "name": "Schedule Trigger",
                "type": "n8n-nodes-base.scheduleTrigger",
                "typeVersion": 1.2,
                "position": [250, 300],
            },
            {
                "parameters": {
                    "url": params.get("api_url", "https://api.example.com/status"),
                    "options": {},
                },
                "id": "http-1",
                "name": "HTTP Request",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [500, 300],
            },
            {
                "parameters": {
                    "chatId": params.get("chat_id", ""),
                    "text": params.get("message_template", "API Response: {{ $json }}"),
                },
                "id": "telegram-1",
                "name": "Telegram",
                "type": "n8n-nodes-base.telegram",
                "typeVersion": 1.2,
                "position": [750, 300],
                "credentials": {"telegramApi": {"id": "", "name": "Telegram"}},
            },
        ],
        "connections": {
            "Schedule Trigger": {"main": [[{"node": "HTTP Request", "type": "main", "index": 0}]]},
            "HTTP Request": {"main": [[{"node": "Telegram", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _keyword_research_to_sheet(params: dict[str, Any]) -> dict:
    """Manual trigger -> Code (build keyword list) -> Google Sheets append.

    This is the template the brain should reach for when the user asks
    to "research keywords for X and put them in a sheet". Instead of
    trying to orchestrate a Serper/SerpAPI call live, the template
    bakes the researched keyword list into a Code node up front — so
    running the workflow appends the rows the brain already vetted,
    no external API credential needed.

    Expected params:
      name: workflow name
      sheet_id: Google Sheets document ID (from the sheet URL)
      sheet_name: tab name inside the sheet (default "Keywords")
      rows: list of dicts, each with keys:
        category, keyword, search_volume, difficulty, content_opportunity,
        priority, status, notes, target_url

    If `rows` is missing/empty, the template still validates and
    produces a Code node that returns an empty list — the user can
    fill it in n8n UI. This keeps the flow valid for POST even if
    the brain hasn't finished the research yet.
    """
    import json as _json

    rows_raw = params.get("rows") or []
    safe_rows: list[dict] = []
    if isinstance(rows_raw, list):
        for row in rows_raw:
            if isinstance(row, dict):
                safe_rows.append({
                    "Category": row.get("category", "") or "",
                    "Keyword": row.get("keyword", "") or "",
                    "Search Volume": row.get("search_volume", "") or "",
                    "Difficulty": row.get("difficulty", "") or "",
                    "Content Opportunity": row.get("content_opportunity", "") or "",
                    "Priority": row.get("priority", "") or "",
                    "Status": row.get("status", "pending") or "pending",
                    "Notes": row.get("notes", "") or "",
                    "Target URL": row.get("target_url", "") or "",
                })

    code_body = (
        "// Keyword research rows prepared by LazyClaw.\n"
        "// Edit this array in the n8n UI to update the keyword list.\n"
        f"const rows = {_json.dumps(safe_rows, ensure_ascii=False, indent=2)};\n"
        "return rows.map(r => ({ json: r }));\n"
    )

    return {
        "name": params.get("name", "Keyword Research to Google Sheets"),
        "nodes": [
            {
                "parameters": {},
                "id": "manual-1",
                "name": "Manual Trigger",
                "type": "n8n-nodes-base.manualTrigger",
                "typeVersion": 1,
                "position": [250, 300],
            },
            {
                "parameters": {
                    "jsCode": code_body,
                },
                "id": "code-1",
                "name": "Build Keyword Rows",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [500, 300],
            },
            {
                "parameters": {
                    "operation": "append",
                    "documentId": {"value": params.get("sheet_id", "")},
                    "sheetName": {"value": params.get("sheet_name", "Keywords")},
                    "columns": {"mappingMode": "autoMapInputData"},
                },
                "id": "sheets-1",
                "name": "Google Sheets",
                "type": "n8n-nodes-base.googleSheets",
                "typeVersion": 4.5,
                "position": [750, 300],
                "credentials": {"googleSheetsOAuth2Api": {"id": "", "name": "Google Sheets"}},
            },
        ],
        "connections": {
            "Manual Trigger": {"main": [[{"node": "Build Keyword Rows", "type": "main", "index": 0}]]},
            "Build Keyword Rows": {"main": [[{"node": "Google Sheets", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def _webhook_to_gmail_send(params: dict[str, Any]) -> dict:
    """Webhook trigger -> Build MIME body -> HTTP POST to Gmail API (send).

    Extracted from n8n-custom/workflows/lazyclaw-send-email.json so it's
    callable the same way as other built-in templates. The user wires
    up a Gmail OAuth2 credential in n8n once; the workflow reuses it.
    """
    return {
        "name": params.get("name", "Webhook to Gmail Send"),
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": params.get("webhook_path", "send-email")},
                "id": "webhook-1",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [250, 300],
                "webhookId": "",
            },
            {
                "parameters": {
                    "jsCode": (
                        "const body = $input.first().json.body || $input.first().json;\n"
                        "const to = body.to || '';\n"
                        "const subject = body.subject || '(no subject)';\n"
                        "const text = body.text || body.message || '';\n"
                        "const raw = [\n"
                        "  `To: ${to}`,\n"
                        "  `Subject: ${subject}`,\n"
                        "  'Content-Type: text/plain; charset=UTF-8',\n"
                        "  '',\n"
                        "  text,\n"
                        "].join('\\r\\n');\n"
                        "const b64 = Buffer.from(raw).toString('base64')\n"
                        "  .replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');\n"
                        "return [{ json: { raw: b64, to, subject } }];\n"
                    ),
                },
                "id": "code-1",
                "name": "Build MIME",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [500, 300],
            },
            {
                "parameters": {
                    "method": "POST",
                    "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                    "authentication": "genericCredentialType",
                    "genericAuthType": "oAuth2Api",
                    "sendBody": True,
                    "specifyBody": "json",
                    "jsonBody": "={{ { raw: $json.raw } }}",
                    "options": {},
                },
                "id": "http-1",
                "name": "Gmail Send",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [750, 300],
                "credentials": {"oAuth2Api": {"id": "", "name": "Gmail OAuth2"}},
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Build MIME", "type": "main", "index": 0}]]},
            "Build MIME": {"main": [[{"node": "Gmail Send", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

TEMPLATES: list[dict] = [
    {
        "name": "Webhook to Telegram",
        "keywords": ["webhook", "notify", "notification", "alert", "ping"],
        "description": "Receive webhook POST and forward as Telegram message",
        "build": _webhook_to_telegram,
    },
    {
        "name": "Scheduled Telegram Message",
        "keywords": ["schedule", "cron", "every day", "every morning", "daily", "weekly", "recurring", "reminder"],
        "description": "Send a Telegram message on a schedule (cron)",
        "build": _schedule_telegram,
    },
    {
        "name": "Email to Telegram",
        "keywords": ["email", "gmail", "inbox", "imap", "new email", "mail"],
        "description": "Watch inbox via IMAP and forward new emails to Telegram",
        "build": _email_to_telegram,
    },
    {
        "name": "RSS to Telegram",
        "keywords": ["rss", "feed", "blog", "news", "articles"],
        "description": "Watch an RSS feed and send new articles to Telegram",
        "build": _rss_to_telegram,
    },
    {
        "name": "Form to Google Sheets",
        "keywords": ["form", "sheets", "spreadsheet", "google sheets", "collect", "submit"],
        "description": "Collect webhook form submissions into a Google Sheet",
        "build": _form_to_sheets,
    },
    {
        "name": "Google Drive to Instagram",
        "keywords": ["drive", "instagram", "auto post", "upload photo", "new photo"],
        "description": "When a new file appears in Google Drive, post it to Instagram",
        "build": _drive_to_instagram,
    },
    {
        "name": "Google Trends Check",
        "keywords": ["trends", "google trends", "trending", "keyword monitor"],
        "description": "Periodically check Google Trends RSS for trending topics",
        "build": _google_trends_check,
    },
    {
        "name": "Webhook to Google Sheets",
        "keywords": ["collect data", "log data", "data collection", "webhook sheets"],
        "description": "Receive webhook data and append it to a Google Sheet",
        "build": _webhook_to_sheets,
    },
    {
        "name": "Periodic API Check to Telegram",
        "keywords": ["api check", "monitor api", "periodic", "health check", "status check"],
        "description": "Periodically call an API and send results to Telegram",
        "build": _schedule_http_telegram,
    },
    {
        "name": "Keyword Research to Google Sheets",
        "keywords": [
            "keyword", "keywords", "keyword research", "research keywords",
            "seo", "search terms", "keyword list", "palabras clave",
            "keyword sheet", "sheets", "google sheets", "spreadsheet",
        ],
        "description": (
            "Manual trigger that appends a researched keyword list "
            "(category, keyword, volume, difficulty, priority, etc.) "
            "into a Google Sheet. Pass `rows` as a list of dicts with the "
            "keyword fields. Use this for SEO keyword planning."
        ),
        "build": _keyword_research_to_sheet,
    },
    {
        "name": "Webhook to Gmail Send",
        "keywords": [
            "gmail", "send email", "send gmail", "gmail send", "webhook email",
            "webhook gmail", "email notification", "mail send", "send mail",
            "gmail api", "webhook to gmail", "webhook to email",
        ],
        "description": (
            "Webhook POST → MIME builder → Gmail API send. Call the "
            "webhook with JSON {to, subject, text} to send an email via "
            "a Gmail OAuth2 credential."
        ),
        "build": _webhook_to_gmail_send,
    },
]


def match_template(description: str) -> dict | None:
    """Match a user description to a template by keyword overlap.

    Returns the template dict (with 'build' callable) or None.
    """
    desc_lower = description.lower()
    best_match: dict | None = None
    best_score = 0

    for tmpl in TEMPLATES:
        score = sum(1 for kw in tmpl["keywords"] if kw in desc_lower)
        if score > best_score:
            best_score = score
            best_match = tmpl

    if best_score >= 2:
        return best_match
    return None


def list_templates() -> list[dict[str, str]]:
    """Return a summary list of all available templates."""
    return [
        {"name": t["name"], "description": t["description"]}
        for t in TEMPLATES
    ]
