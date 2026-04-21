"""Auto-binding empty-id credential refs to existing n8n credentials.

Templates emit nodes like:
    credentials: {googleSheetsOAuth2Api: {id: "", name: "Google Sheets"}}
Without auto-bind, activation fails with "Credential not configured".
This test pins the binder so a future refactor can't silently drop it.
"""

from __future__ import annotations

import asyncio

from lazyclaw.skills.builtin import n8n_management as mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_auto_bind_fills_empty_ids(monkeypatch):
    workflow = {
        "nodes": [
            {
                "name": "Google Sheets",
                "type": "n8n-nodes-base.googleSheets",
                "credentials": {
                    "googleSheetsOAuth2Api": {"id": "", "name": "placeholder"},
                },
            },
            {
                "name": "Telegram",
                "type": "n8n-nodes-base.telegram",
                "credentials": {
                    "telegramApi": {"id": "", "name": "placeholder"},
                },
            },
        ],
    }

    fake_creds = {
        "data": [
            {"id": "7", "type": "googleSheetsOAuth2Api", "name": "User's Sheets",
             "createdAt": "2026-04-20T12:00:00Z"},
            {"id": "9", "type": "googleSheetsOAuth2Api", "name": "Old Sheets",
             "createdAt": "2026-01-01T12:00:00Z"},
            {"id": "42", "type": "telegramApi", "name": "Main Bot",
             "createdAt": "2026-03-10T12:00:00Z"},
        ],
    }

    async def fake_request(config, user_id, method, path, body=None, timeout=15.0):
        assert method == "GET" and path == "/api/v1/credentials"
        return fake_creds

    monkeypatch.setattr(mod, "_n8n_request", fake_request)

    bindings, missing = _run(mod._auto_bind_credentials(None, "u", workflow))

    assert len(bindings) == 2
    assert missing == []
    gs_ref = workflow["nodes"][0]["credentials"]["googleSheetsOAuth2Api"]
    assert gs_ref["id"] == "7"  # newest wins
    assert gs_ref["name"] == "User's Sheets"
    tg_ref = workflow["nodes"][1]["credentials"]["telegramApi"]
    assert tg_ref["id"] == "42"
    assert tg_ref["name"] == "Main Bot"


def test_auto_bind_falls_back_to_generic_google_oauth(monkeypatch):
    # User has only a multi-scope googleOAuth2Api — no service-specific
    # googleSheetsOAuth2Api. Binder should rewrite the node to use it.
    workflow = {
        "nodes": [{
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "credentials": {
                "googleSheetsOAuth2Api": {"id": "", "name": "placeholder"},
            },
        }],
    }

    async def fake_request(config, user_id, method, path, body=None, timeout=15.0):
        return {"data": [
            {"id": "99", "type": "googleOAuth2Api", "name": "Google (all scopes)"},
        ]}

    monkeypatch.setattr(mod, "_n8n_request", fake_request)

    bindings, missing = _run(mod._auto_bind_credentials(None, "u", workflow))
    assert missing == []
    assert len(bindings) == 1
    assert "fallback" in bindings[0]
    creds = workflow["nodes"][0]["credentials"]
    assert "googleSheetsOAuth2Api" not in creds
    assert creds["googleOAuth2Api"]["id"] == "99"


def test_auto_bind_skips_nodes_with_existing_ids(monkeypatch):
    workflow = {
        "nodes": [{
            "name": "Sheets",
            "credentials": {
                "googleSheetsOAuth2Api": {"id": "existing-id", "name": "Mine"},
            },
        }],
    }

    async def fake_request(config, user_id, method, path, body=None, timeout=15.0):
        return {"data": [{"id": "7", "type": "googleSheetsOAuth2Api", "name": "New"}]}

    monkeypatch.setattr(mod, "_n8n_request", fake_request)

    bindings, missing = _run(mod._auto_bind_credentials(None, "u", workflow))
    assert bindings == []
    assert missing == []  # already bound, so not "missing"
    assert workflow["nodes"][0]["credentials"]["googleSheetsOAuth2Api"]["id"] == "existing-id"


def test_auto_bind_noop_when_no_matching_type(monkeypatch):
    workflow = {
        "nodes": [{
            "name": "Sheets",
            "credentials": {
                "googleSheetsOAuth2Api": {"id": "", "name": ""},
            },
        }],
    }

    async def fake_request(config, user_id, method, path, body=None, timeout=15.0):
        return {"data": [{"id": "1", "type": "telegramApi", "name": "Bot"}]}

    monkeypatch.setattr(mod, "_n8n_request", fake_request)

    bindings, missing = _run(mod._auto_bind_credentials(None, "u", workflow))
    assert bindings == []
    assert missing == ["Sheets:googleSheetsOAuth2Api"]
    assert workflow["nodes"][0]["credentials"]["googleSheetsOAuth2Api"]["id"] == ""


def test_auto_bind_fails_silently_on_n8n_error(monkeypatch):
    workflow = {"nodes": [{"credentials": {"telegramApi": {"id": ""}}}]}

    async def boom(*a, **kw):
        raise RuntimeError("n8n unreachable")

    monkeypatch.setattr(mod, "_n8n_request", boom)

    bindings, missing = _run(mod._auto_bind_credentials(None, "u", workflow))
    assert bindings == []  # never raises
    assert missing == ["telegramApi"]  # reports what was needed
