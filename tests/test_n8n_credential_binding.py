"""Pillar C — credential binding must prefer authorized over fresh shells.

The shell-explosion bug: every time n8n rejected a workflow with
`Credential not configured`, the agent would call `*_setup` which
POST'd a blank credential → auto-bind picked the newest blank → same
error → loop. Ate 3 days. These tests pin the fix.

Signals:
  * _credential_is_authorized: True iff updatedAt > createdAt (OAuth
    callback bumps updatedAt; a shell that was never signed into has
    updatedAt == createdAt to the millisecond).
  * _auto_bind_credentials sort: authorized first, newest updatedAt
    within authorized, empty shells last.
  * _find_authorized_credential: returns exact-type authorized cred
    preferred over fallback-type (googleOAuth2Api) authorized cred.
"""

from __future__ import annotations

import asyncio
import pytest

from lazyclaw.skills.builtin import n8n_management as mod


# ── _credential_is_authorized ──────────────────────────────────────

@pytest.mark.parametrize(
    "created, updated, expected",
    [
        # Empty shell — created and never signed into.
        ("2026-04-22T10:00:00.000Z", "2026-04-22T10:00:00.000Z", False),
        # OAuth callback happened — updatedAt bumped forward.
        ("2026-04-18T09:23:30.486Z", "2026-04-21T16:18:33.263Z", True),
        # Missing fields — conservative False.
        ("", "", False),
        ("2026-04-22T10:00:00.000Z", "", False),
        ("", "2026-04-22T10:00:00.000Z", False),
    ],
)
def test_credential_is_authorized(created, updated, expected):
    assert mod._credential_is_authorized({
        "createdAt": created, "updatedAt": updated,
    }) is expected


# ── _find_authorized_credential — monkeypatches the n8n listing ────

def _patched_list(monkeypatch, creds):
    async def fake(config, user_id, method, path, **kw):
        if method == "GET" and path == "/api/v1/credentials":
            return {"data": creds}
        raise AssertionError(f"unexpected call {method} {path}")
    monkeypatch.setattr(mod, "_n8n_request", fake)


def test_find_authorized_prefers_authorized_over_shell(monkeypatch):
    _patched_list(monkeypatch, [
        # Authorized (older but real).
        {"id": "auth-1", "type": "googleSheetsOAuth2Api",
         "createdAt": "2026-04-18T09:23:30.486Z",
         "updatedAt": "2026-04-21T16:18:33.263Z"},
        # Blank shell (newer — this is what the old buggy sort picked).
        {"id": "shell-1", "type": "googleSheetsOAuth2Api",
         "createdAt": "2026-04-22T11:26:24.000Z",
         "updatedAt": "2026-04-22T11:26:24.000Z"},
    ])
    found = asyncio.new_event_loop().run_until_complete(
        mod._find_authorized_credential(None, "u", "googleSheetsOAuth2Api"),
    )
    assert found is not None
    assert found["id"] == "auth-1"


def test_find_authorized_returns_none_when_only_shells(monkeypatch):
    _patched_list(monkeypatch, [
        {"id": "shell-a", "type": "googleSheetsOAuth2Api",
         "createdAt": "2026-04-22T11:00:00.000Z",
         "updatedAt": "2026-04-22T11:00:00.000Z"},
        {"id": "shell-b", "type": "googleSheetsOAuth2Api",
         "createdAt": "2026-04-20T11:00:00.000Z",
         "updatedAt": "2026-04-20T11:00:00.000Z"},
    ])
    found = asyncio.new_event_loop().run_until_complete(
        mod._find_authorized_credential(None, "u", "googleSheetsOAuth2Api"),
    )
    assert found is None


def test_find_authorized_prefers_exact_type_over_fallback(monkeypatch):
    _patched_list(monkeypatch, [
        # Generic multi-scope Google cred — authorized.
        {"id": "generic", "type": "googleOAuth2Api",
         "createdAt": "2026-04-18T09:00:00.000Z",
         "updatedAt": "2026-04-21T09:00:00.000Z"},
        # Service-specific Sheets cred — also authorized. Should win.
        {"id": "sheets-specific", "type": "googleSheetsOAuth2Api",
         "createdAt": "2026-04-18T09:00:00.000Z",
         "updatedAt": "2026-04-19T09:00:00.000Z"},
    ])
    found = asyncio.new_event_loop().run_until_complete(
        mod._find_authorized_credential(None, "u", "googleSheetsOAuth2Api"),
    )
    assert found["id"] == "sheets-specific"


def test_find_authorized_falls_back_when_no_exact_type(monkeypatch):
    _patched_list(monkeypatch, [
        # Only a generic Google cred is authorized.
        {"id": "generic", "type": "googleOAuth2Api",
         "createdAt": "2026-04-18T09:00:00.000Z",
         "updatedAt": "2026-04-21T09:00:00.000Z"},
    ])
    found = asyncio.new_event_loop().run_until_complete(
        mod._find_authorized_credential(None, "u", "googleSheetsOAuth2Api"),
    )
    assert found is not None
    assert found["id"] == "generic"


# ── _auto_bind_credentials sort ────────────────────────────────────

def test_auto_bind_picks_authorized_over_newest_shell(monkeypatch):
    async def fake(config, user_id, method, path, **kw):
        return {"data": [
            # Authorized cred from days ago.
            {"id": "auth", "type": "googleSheetsOAuth2Api",
             "name": "My Real Sheets",
             "createdAt": "2026-04-18T09:00:00.000Z",
             "updatedAt": "2026-04-21T16:00:00.000Z"},
            # Blank shell created 5 seconds ago.
            {"id": "shell", "type": "googleSheetsOAuth2Api",
             "name": "Google Sheets",
             "createdAt": "2026-04-22T11:26:24.000Z",
             "updatedAt": "2026-04-22T11:26:24.000Z"},
        ]}
    monkeypatch.setattr(mod, "_n8n_request", fake)

    workflow = {
        "nodes": [{
            "id": "n1", "name": "Create Spreadsheet",
            "type": "n8n-nodes-base.googleSheets",
            "credentials": {
                "googleSheetsOAuth2Api": {"id": "", "name": "Google Sheets"},
            },
        }],
    }
    bindings, missing = asyncio.new_event_loop().run_until_complete(
        mod._auto_bind_credentials(None, "u", workflow),
    )
    assert missing == []
    # Critical assertion — must bind to the authorized one, NOT the shell.
    cred = workflow["nodes"][0]["credentials"]["googleSheetsOAuth2Api"]
    assert cred["id"] == "auth"
    assert "auth" in bindings[0]
