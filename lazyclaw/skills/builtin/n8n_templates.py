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

    if best_score >= 1:
        return best_match
    return None


def list_templates() -> list[dict[str, str]]:
    """Return a summary list of all available templates."""
    return [
        {"name": t["name"], "description": t["description"]}
        for t in TEMPLATES
    ]
