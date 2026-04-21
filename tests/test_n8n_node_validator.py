"""Pillar A tests — n8n node validator, sanitizer, activation error enrichment.

These pin the fixes landed on 2026-04-21 for MiniMax-M2.7 stalling on
"create Google Sheet via n8n" tasks:

  * `_validate_workflow_nodes` must flag missing `title` on
    `googleSheets.spreadsheet.create`, missing `documentId` on
    `googleSheets.spreadsheet.delete`, and missing `columns.mappingMode`
    on `googleSheets.sheet.append|appendOrUpdate|update`.
  * `_sanitize_node` must strip unknown top-level node keys while
    preserving the known-good set.
  * `_enrich_activation_error` must surface the node name and current
    `parameters` dict when n8n's error body contains `Node "X":`, and
    re-run the validator on the refetched workflow to give a specific
    fix hint rather than relaying n8n's opaque "1 issue".
"""

from __future__ import annotations

import asyncio

from lazyclaw.skills.builtin import n8n_management as mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── _validate_workflow_nodes ─────────────────────────────────────────

def test_validator_flags_missing_title_on_spreadsheet_create():
    violations = mod._validate_workflow_nodes([
        {
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {"resource": "spreadsheet", "operation": "create"},
        },
    ])
    assert any("title" in v.lower() for v in violations)
    # Message should carry an example so the model can copy it.
    assert any("hirossa" in v or "example" in v.lower() for v in violations)


def test_validator_passes_when_title_present():
    violations = mod._validate_workflow_nodes([
        {
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {
                "resource": "spreadsheet",
                "operation": "create",
                "title": "hirossa keyword research",
                "sheetsUi": {"sheetValues": [{"title": "Sheet1"}]},
            },
        },
    ])
    assert violations == []


def test_validator_flags_missing_documentId_on_spreadsheet_delete():
    violations = mod._validate_workflow_nodes([
        {
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {"resource": "spreadsheet", "operation": "delete"},
        },
    ])
    assert any("documentId" in v for v in violations)


def test_validator_flags_missing_mappingMode_on_sheet_append():
    violations = mod._validate_workflow_nodes([
        {
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {
                "resource": "sheet",
                "operation": "append",
                "documentId": {"__rl": True, "value": "abc", "mode": "id"},
                "sheetName": {"__rl": True, "value": "gid=0", "mode": "list"},
            },
        },
    ])
    assert any("mappingMode" in v for v in violations)


def test_validator_passes_append_with_full_columns():
    violations = mod._validate_workflow_nodes([
        {
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {
                "resource": "sheet",
                "operation": "append",
                "documentId": {"__rl": True, "value": "abc", "mode": "id"},
                "sheetName": {"__rl": True, "value": "gid=0", "mode": "list"},
                "columns": {
                    "mappingMode": "autoMapInputData",
                    "matchingColumns": [],
                    "schema": [],
                },
            },
        },
    ])
    assert violations == []


def test_validator_flags_empty_documentId_value_on_sheet_append():
    # Templates and LLMs often emit `documentId.value=""` when they
    # don't have a spreadsheet id to hand. n8n accepts this on POST
    # but rejects on activate with an opaque "1 issue" — catch it
    # here so the model gets a specific fix hint.
    violations = mod._validate_workflow_nodes([
        {
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {
                "resource": "sheet",
                "operation": "append",
                "documentId": {"__rl": True, "value": "", "mode": "id"},
                "sheetName": {"__rl": True, "value": "gid=0", "mode": "list"},
                "columns": {
                    "mappingMode": "autoMapInputData",
                    "matchingColumns": [],
                    "schema": [],
                },
            },
        },
    ])
    assert any("documentId.value empty" in v for v in violations)
    # The hint should point to the create-spreadsheet recovery path.
    assert any("create_google_sheet" in v or "operation='create'" in v for v in violations)


def test_validator_allows_empty_documentId_on_spreadsheet_create():
    # When creating a new spreadsheet, documentId is irrelevant —
    # the NEW-ID check only applies to resource='sheet' operations.
    violations = mod._validate_workflow_nodes([
        {
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {
                "resource": "spreadsheet",
                "operation": "create",
                "title": "My Sheet",
            },
        },
    ])
    assert violations == []


def test_form_to_sheets_template_produces_schema_violation_without_sheet_id():
    # Canary for the post-build validation pass in n8n_create_workflow:
    # any template that uses `_google_sheets_append_node` with the default
    # sheet_id="" must produce a pre-flight violation so the caller can
    # fall back to LLM generation instead of committing a workflow that
    # will fail on activate.
    from lazyclaw.skills.builtin.n8n_templates import _form_to_sheets

    wf = _form_to_sheets({})
    violations = mod._validate_workflow_nodes(wf["nodes"])
    assert any("documentId.value empty" in v for v in violations)


def test_keyword_research_template_refuses_empty_sheet_id():
    # Pillar-A-v2: the keyword research template is append-only, so
    # instantiating with sheet_id="" would silently produce a broken
    # workflow. Builder must raise so `n8n_create_workflow` falls
    # through to the LLM path (which has the create cheat sheet).
    import pytest
    from lazyclaw.skills.builtin.n8n_templates import _keyword_research_to_sheet

    with pytest.raises(ValueError) as exc:
        _keyword_research_to_sheet({"rows": []})
    msg = str(exc.value)
    assert "sheet_id" in msg
    assert "create" in msg.lower()


def test_validator_still_catches_missing_resource_and_operation():
    # Pre-existing checks must keep firing even with the new branches.
    violations = mod._validate_workflow_nodes([
        {
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {},
        },
    ])
    assert any("resource" in v for v in violations)
    assert any("operation" in v for v in violations)


# ── _sanitize_node ───────────────────────────────────────────────────

def test_sanitize_node_strips_unknown_keys_but_preserves_known():
    dirty = {
        "parameters": {"resource": "sheet"},
        "id": "abc",
        "name": "Google Sheets",
        "type": "n8n-nodes-base.googleSheets",
        "typeVersion": 4.5,
        "position": [500, 300],
        "credentials": {"googleSheetsOAuth2Api": {"id": "", "name": "gs"}},
        # LLM hallucinated extras — must be stripped.
        "color": "red",
        "description": "Long prose",
        "executable": True,
        "tags": ["x"],
    }
    clean = mod._sanitize_node(dirty)
    assert clean["type"] == "n8n-nodes-base.googleSheets"
    assert clean["parameters"] == {"resource": "sheet"}
    assert clean["position"] == [500, 300]
    assert "color" not in clean
    assert "description" not in clean
    assert "executable" not in clean
    assert "tags" not in clean


def test_sanitize_nodes_preserves_list_shape():
    result = mod._sanitize_nodes([
        {"type": "x", "foo": 1},
        {"type": "y", "bar": 2},
    ])
    assert len(result) == 2
    assert all("foo" not in n and "bar" not in n for n in result)


def test_sanitize_node_passthrough_non_dict():
    # Guard: garbage input (shouldn't happen, but must not crash).
    assert mod._sanitize_node("not-a-dict") == "not-a-dict"
    assert mod._sanitize_nodes("not-a-list") == "not-a-list"


# ── _enrich_activation_error ─────────────────────────────────────────

class _FakeExc(Exception):
    """Mimics N8nHTTPError with body_text carrying n8n's node hint."""

    def __init__(self, message: str, body_text: str = "") -> None:
        super().__init__(message)
        self.body_text = body_text


def test_enrich_returns_specific_violation_when_title_still_missing(monkeypatch):
    async def fake_request(config, user_id, method, path, **kw):
        assert method == "GET"
        assert path.endswith("/abc")
        return {
            "id": "abc",
            "name": "My Sheet Workflow",
            "nodes": [
                {
                    "name": "Google Sheets",
                    "type": "n8n-nodes-base.googleSheets",
                    "parameters": {
                        "resource": "spreadsheet",
                        "operation": "create",
                        # title missing — enrichment should detect
                    },
                },
            ],
        }

    monkeypatch.setattr(mod, "_n8n_request", fake_request)

    msg = _run(mod._enrich_activation_error(
        config=None, user_id="u1", wf_id="abc",
        created_name="My Sheet Workflow",
        act_exc=_FakeExc("400: Cannot publish workflow: 1 node have configuration issues"),
    ))
    assert "schema issues" in msg.lower()
    assert "title" in msg.lower()
    assert "abc" in msg  # workflow id mentioned


def test_enrich_surfaces_node_name_when_validator_is_quiet(monkeypatch):
    # Validator passes (title is present); but n8n still rejected activation
    # — enrichment should regex the node name out of the error body and
    # expose current parameters so the model can diff.
    async def fake_request(config, user_id, method, path, **kw):
        return {
            "id": "xyz",
            "name": "Hirossa Sheet",
            "nodes": [
                {
                    "name": "Google Sheets",
                    "type": "n8n-nodes-base.googleSheets",
                    "parameters": {
                        "resource": "spreadsheet",
                        "operation": "create",
                        "title": "hirossa keyword research",
                    },
                },
            ],
        }

    monkeypatch.setattr(mod, "_n8n_request", fake_request)

    msg = _run(mod._enrich_activation_error(
        config=None, user_id="u1", wf_id="xyz",
        created_name="Hirossa Sheet",
        act_exc=_FakeExc(
            '400: Cannot publish workflow: 1 node have configuration issues: '
            'Node "Google Sheets": - Missing or invalid required parameters',
        ),
    ))
    assert "Google Sheets" in msg
    assert "n8n-nodes-base.googleSheets" in msg
    # Current params exposed so model sees what it sent.
    assert "hirossa keyword research" in msg


def test_enrich_falls_back_to_raw_error_when_refetch_fails(monkeypatch):
    async def fake_request(config, user_id, method, path, **kw):
        raise RuntimeError("n8n unreachable")

    monkeypatch.setattr(mod, "_n8n_request", fake_request)

    msg = _run(mod._enrich_activation_error(
        config=None, user_id="u1", wf_id="nope",
        created_name="Broken",
        act_exc=_FakeExc("500: internal error"),
    ))
    assert "activation failed" in msg.lower()
    assert "500" in msg
