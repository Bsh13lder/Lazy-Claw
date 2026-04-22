"""One-shot n8n tasks — n8n used as a Google-connector tool library.

The pattern: create a minimal n8n workflow, fire its webhook, collect
the JSON response (via a `Respond to Webhook` node), then DELETE the
workflow. The user never sees the workflow, there's no cron, no
persistent automation — just an atomic Google-side operation.

Use this instead of hand-wiring `n8n_create_workflow → n8n_run_workflow
→ n8n_manage_workflow(delete)` by hand. One call, one cleanup, one
auto-registration of the created resource into the LazyBrain project
registry (so the agent remembers what belongs to which project).

When to use:
  * Task needs a Google connector (Drive / Sheets / Gmail / Calendar).
  * It's a one-off (not a schedule, not a webhook receiver).
  * You want the resource ID back (folder_id / spreadsheet_id / ...).

When NOT to use:
  * User explicitly asks for a persistent n8n workflow (use
    n8n_create_workflow — "on demand" persistent path).
  * Cron / recurring without any Google tool (use schedule_job).
  * Messaging platforms (use the channel MCPs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

import httpx

from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin import project_assets
from lazyclaw.skills.builtin.n8n_management import (
    _n8n_request,
    _N8N_DEFAULT_BASE,
    _auto_bind_credentials,
)

logger = logging.getLogger(__name__)


# Tag that marks a workflow as an ephemeral one-shot — if cleanup
# fails, a nightly pass can safely delete anything with this tag.
_ONESHOT_WORKFLOW_NAME_PREFIX = "[oneshot]"


# ---------------------------------------------------------------------------
# Webhook resolution + sync execution
# ---------------------------------------------------------------------------

def _webhook_base_url() -> str:
    import os
    return (os.getenv("N8N_BASE_URL") or _N8N_DEFAULT_BASE).rstrip("/")


async def _post_webhook_sync(
    path: str,
    body: dict,
    timeout: float = 60.0,
) -> dict:
    """POST to `/webhook/<path>` and parse JSON response.

    Requires the workflow to end with a `Respond to Webhook` node in
    `firstJson` mode so the body carries the node output.
    """
    url = f"{_webhook_base_url()}/webhook/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=body or {})
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Webhook {path} returned HTTP {resp.status_code}: "
            f"{(resp.text or '')[:300]}"
        )
    try:
        return resp.json() if resp.content else {}
    except Exception:
        return {"_raw": resp.text[:2000]}


# ---------------------------------------------------------------------------
# Shared node factories for one-shot templates
# ---------------------------------------------------------------------------

def _webhook_node(path: str, position=(250, 300)) -> dict:
    # responseMode=responseNode routes the HTTP response through the
    # downstream Respond node. Without it, n8n returns default JSON
    # immediately and rejects the workflow at activation with
    # "Unused Respond to Webhook node found in the workflow".
    return {
        "parameters": {
            "httpMethod": "POST",
            "path": path,
            "responseMode": "responseNode",
            "options": {},
        },
        "id": "webhook-1",
        "name": "Webhook",
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 2,
        "position": list(position),
        "webhookId": "",
    }


def _respond_node(position=(1000, 300)) -> dict:
    return {
        "parameters": {
            "respondWith": "firstIncomingItem",
            "options": {},
        },
        "id": "respond-1",
        "name": "Respond",
        "type": "n8n-nodes-base.respondToWebhook",
        "typeVersion": 1.1,
        "position": list(position),
    }


# ---------------------------------------------------------------------------
# One-shot workflow builders
# ---------------------------------------------------------------------------

def _build_create_drive_folder(task: dict) -> tuple[dict, str]:
    """Create a Drive folder. Returns (workflow_json, webhook_path)."""
    path = f"oneshot-folder-{uuid.uuid4().hex[:8]}"
    folder_name = task.get("folder_name") or task.get("name") or "New Folder"
    parent_id = task.get("parent_id") or ""
    return (
        {
            "name": f"{_ONESHOT_WORKFLOW_NAME_PREFIX} Create Drive Folder",
            "nodes": [
                _webhook_node(path),
                {
                    "parameters": {
                        "resource": "folder",
                        "operation": "create",
                        "name": folder_name,
                        "driveId": {"__rl": True, "mode": "list", "value": "My Drive"},
                        "folderId": {
                            "__rl": True,
                            "mode": "id",
                            "value": parent_id or "root",
                        },
                        "options": {},
                    },
                    "id": "drive-1",
                    "name": "Create Folder",
                    "type": "n8n-nodes-base.googleDrive",
                    "typeVersion": 3,
                    "position": [500, 300],
                    "credentials": {
                        "googleDriveOAuth2Api": {"id": "", "name": "Google Drive"},
                    },
                },
                _respond_node(),
            ],
            "connections": {
                "Webhook": {"main": [[{"node": "Create Folder", "type": "main", "index": 0}]]},
                "Create Folder": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
            },
            "settings": {"executionOrder": "v1"},
        },
        path,
    )


def _build_create_google_sheet(task: dict) -> tuple[dict, str]:
    """Create a new Spreadsheet with a given title. Returns spreadsheetId."""
    path = f"oneshot-sheet-{uuid.uuid4().hex[:8]}"
    title = task.get("title") or task.get("name") or "New Sheet"
    return (
        {
            "name": f"{_ONESHOT_WORKFLOW_NAME_PREFIX} Create Google Sheet",
            "nodes": [
                _webhook_node(path),
                {
                    "parameters": {
                        "resource": "spreadsheet",
                        "operation": "create",
                        "title": title,
                        "sheetsUi": {
                            "sheetValues": [{"title": "Sheet1"}],
                        },
                    },
                    "id": "sheet-1",
                    "name": "Create Spreadsheet",
                    "type": "n8n-nodes-base.googleSheets",
                    "typeVersion": 4.5,
                    "position": [500, 300],
                    "credentials": {
                        "googleSheetsOAuth2Api": {"id": "", "name": "Google Sheets"},
                    },
                },
                _respond_node(),
            ],
            "connections": {
                "Webhook": {"main": [[{"node": "Create Spreadsheet", "type": "main", "index": 0}]]},
                "Create Spreadsheet": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
            },
            "settings": {"executionOrder": "v1"},
        },
        path,
    )


def _build_append_sheet_rows(task: dict) -> tuple[dict, str]:
    """Append rows to an existing Sheet. Rows come in via webhook body.

    Uses n8n's HTTP Request node against the raw Google Sheets API
    instead of the native Google Sheets node, because v4.5's
    resource-locator for `sheetName` rejects every format we've tried
    (``gid=0``, ``Sheet1``, ``"0"`` in ``list`` and ``id`` modes all
    fail with "Sheet with ID X not found" or "Missing required
    parameters"). Direct API call is deterministic and avoids the n8n
    schema entirely.
    """
    path = f"oneshot-append-{uuid.uuid4().hex[:8]}"
    sheet_id = task.get("sheet_id") or task.get("spreadsheet_id") or ""
    # Sheet range: user can override (e.g. "Sheet2!A:A"); default appends
    # into column A of the whole first sheet.
    sheet_range = task.get("range") or "A:A"
    if not sheet_id:
        raise ValueError("append_sheet_rows requires sheet_id")
    return (
        {
            "name": f"{_ONESHOT_WORKFLOW_NAME_PREFIX} Append Sheet Rows",
            "nodes": [
                _webhook_node(path),
                {
                    "parameters": {
                        # Extract the value(s) to write and emit one JSON
                        # item with a `values` 2-D array — the exact
                        # shape the Sheets API wants in its body.
                        "jsCode": (
                            "const b = $input.first().json.body || $input.first().json || {};\n"
                            "const rowsIn = Array.isArray(b) ? b "
                            ": Array.isArray(b && b.rows) ? b.rows "
                            ": Array.isArray(b && b.values) ? b.values : [b];\n"
                            "const toCells = r => Array.isArray(r) ? r\n"
                            "  : (r && typeof r === 'object') "
                            "? [r.text ?? r.value ?? r.message "
                            "?? JSON.stringify(r)]\n"
                            "  : [String(r)];\n"
                            "return [{ json: { values: rowsIn.map(toCells) } }];\n"
                        ),
                    },
                    "id": "code-1",
                    "name": "Extract Rows",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [500, 300],
                },
                {
                    # Direct call to Sheets API —
                    # POST /v4/spreadsheets/{id}/values/{range}:append
                    "parameters": {
                        "method": "POST",
                        "url": (
                            f"https://sheets.googleapis.com/v4/spreadsheets/"
                            f"{sheet_id}/values/{sheet_range}:append"
                        ),
                        "authentication": "predefinedCredentialType",
                        "nodeCredentialType": "googleSheetsOAuth2Api",
                        "sendQuery": True,
                        "queryParameters": {
                            "parameters": [
                                {"name": "valueInputOption", "value": "RAW"},
                                {"name": "insertDataOption", "value": "INSERT_ROWS"},
                            ],
                        },
                        "sendBody": True,
                        "specifyBody": "json",
                        "jsonBody": "={{ JSON.stringify({ values: $json.values }) }}",
                        "options": {},
                    },
                    "id": "sheet-1",
                    "name": "Append Rows",
                    "type": "n8n-nodes-base.httpRequest",
                    "typeVersion": 4.2,
                    "position": [750, 300],
                    "credentials": {
                        "googleSheetsOAuth2Api": {"id": "", "name": "Google Sheets"},
                    },
                },
                _respond_node(position=(1000, 300)),
            ],
            "connections": {
                "Webhook": {"main": [[{"node": "Extract Rows", "type": "main", "index": 0}]]},
                "Extract Rows": {"main": [[{"node": "Append Rows", "type": "main", "index": 0}]]},
                "Append Rows": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
            },
            "settings": {"executionOrder": "v1"},
        },
        path,
    )


def _build_send_gmail(task: dict) -> tuple[dict, str]:
    path = f"oneshot-gmail-{uuid.uuid4().hex[:8]}"
    to = task.get("to") or ""
    subject = task.get("subject") or "(no subject)"
    text = task.get("text") or task.get("body") or ""
    if not to:
        raise ValueError("send_gmail requires `to`")
    return (
        {
            "name": f"{_ONESHOT_WORKFLOW_NAME_PREFIX} Send Gmail",
            "nodes": [
                _webhook_node(path),
                {
                    "parameters": {
                        "resource": "message",
                        "operation": "send",
                        "sendTo": to,
                        "subject": subject,
                        "emailType": "text",
                        "message": text,
                        "options": {},
                    },
                    "id": "gmail-1",
                    "name": "Send Mail",
                    "type": "n8n-nodes-base.gmail",
                    "typeVersion": 2.1,
                    "position": [500, 300],
                    "credentials": {
                        "gmailOAuth2": {"id": "", "name": "Gmail"},
                    },
                },
                _respond_node(),
            ],
            "connections": {
                "Webhook": {"main": [[{"node": "Send Mail", "type": "main", "index": 0}]]},
                "Send Mail": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
            },
            "settings": {"executionOrder": "v1"},
        },
        path,
    )


def _build_create_calendar_event(task: dict) -> tuple[dict, str]:
    path = f"oneshot-cal-{uuid.uuid4().hex[:8]}"
    title = task.get("summary") or task.get("title") or "New Event"
    start = task.get("start")
    end = task.get("end")
    description = task.get("description") or ""
    if not (start and end):
        raise ValueError("create_calendar_event requires ISO `start` and `end`")
    return (
        {
            "name": f"{_ONESHOT_WORKFLOW_NAME_PREFIX} Create Calendar Event",
            "nodes": [
                _webhook_node(path),
                {
                    "parameters": {
                        "resource": "event",
                        "operation": "create",
                        "calendar": {"__rl": True, "value": "primary", "mode": "list"},
                        "start": start,
                        "end": end,
                        "additionalFields": {
                            "summary": title,
                            "description": description,
                        },
                    },
                    "id": "cal-1",
                    "name": "Create Event",
                    "type": "n8n-nodes-base.googleCalendar",
                    "typeVersion": 1.2,
                    "position": [500, 300],
                    "credentials": {
                        "googleCalendarOAuth2Api": {"id": "", "name": "Google Calendar"},
                    },
                },
                _respond_node(),
            ],
            "connections": {
                "Webhook": {"main": [[{"node": "Create Event", "type": "main", "index": 0}]]},
                "Create Event": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
            },
            "settings": {"executionOrder": "v1"},
        },
        path,
    )


# Task type -> (builder, interpret_result)
# The interpreter extracts the key resource id + url from the webhook's
# JSON response. Each n8n node returns slightly different fields.

def _result_drive_folder(payload: dict) -> dict:
    fid = payload.get("id") or payload.get("fileId") or ""
    return {
        "resource_type": "google_drive_folder",
        "resource_id": fid,
        "url": f"https://drive.google.com/drive/folders/{fid}" if fid else "",
        "raw": payload,
    }


def _result_google_sheet(payload: dict) -> dict:
    sid = (
        payload.get("spreadsheetId")
        or payload.get("id")
        or (payload.get("properties") or {}).get("spreadsheetId", "")
    )
    url = payload.get("spreadsheetUrl") or (
        f"https://docs.google.com/spreadsheets/d/{sid}" if sid else ""
    )
    return {
        "resource_type": "google_sheet",
        "resource_id": sid,
        "url": url,
        "raw": payload,
    }


def _result_append_rows(payload: dict) -> dict:
    # Guard against n8n Append returning success with zero updated rows.
    # Happens when mappingMode can't resolve column targets (e.g.
    # autoMapInputData on a sheet with no header row). Raising here stops
    # the brain from reporting "Done!" on an empty sheet.
    updates = (payload or {}).get("updates") or {}
    updated_rows = updates.get("updatedRows") or updates.get("updatedCells") or 0
    # Some n8n versions return the Sheets API response one level up.
    if not updated_rows and isinstance(payload, dict):
        updated_rows = payload.get("updatedRows") or payload.get("updatedCells") or 0
    if not updated_rows:
        raise RuntimeError(
            "append_sheet_rows completed but wrote 0 rows. Likely causes: "
            "(1) payload didn't include a 'text' / 'value' / 'message' "
            "field the template maps to column A, (2) n8n append response "
            "lacked `updates.updatedRows`, or (3) Sheets API silently "
            "ignored the write. Inspect the workflow execution in n8n for "
            f"the raw response. payload={payload!r}"
        )
    return {
        "resource_type": "google_sheet_rows",
        "resource_id": "",
        "url": "",
        "updated_rows": updated_rows,
        "raw": payload,
    }


def _result_gmail(payload: dict) -> dict:
    mid = payload.get("id") or payload.get("messageId") or ""
    return {"resource_type": "gmail_message", "resource_id": mid, "url": "", "raw": payload}


def _result_calendar_event(payload: dict) -> dict:
    eid = payload.get("id") or ""
    url = payload.get("htmlLink") or ""
    return {
        "resource_type": "calendar_event",
        "resource_id": eid,
        "url": url,
        "raw": payload,
    }


_ONESHOTS: dict[str, tuple[Any, Any]] = {
    "create_drive_folder": (_build_create_drive_folder, _result_drive_folder),
    "create_google_sheet": (_build_create_google_sheet, _result_google_sheet),
    "append_sheet_rows": (_build_append_sheet_rows, _result_append_rows),
    "send_gmail": (_build_send_gmail, _result_gmail),
    "create_calendar_event": (_build_create_calendar_event, _result_calendar_event),
}


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


async def run_oneshot(
    config: Any,
    user_id: str,
    *,
    task_type: str,
    task: dict,
    project: str | None = None,
    purpose: str | None = None,
) -> dict:
    """Create -> run -> delete a one-shot workflow. Auto-register created
    resources to the project note if `project` + `purpose` were given.

    Returns a dict with keys: `task_type`, `resource_type`, `resource_id`,
    `url`, `raw`, `registered` (bool), and `workflow_id` for debugging.
    """
    if task_type not in _ONESHOTS:
        raise ValueError(
            f"Unknown oneshot task '{task_type}'. Valid: "
            + ", ".join(sorted(_ONESHOTS))
        )
    # LLMs occasionally pass `task` as a JSON-encoded string or a plain
    # description string instead of an object. Parse or wrap so the
    # builder always sees a dict and the agent gets a clear message
    # instead of `AttributeError: 'str' object has no attribute 'get'`.
    if isinstance(task, str):
        stripped = task.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                task = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"oneshot '{task_type}': `task` was a string that "
                    f"looked like JSON but couldn't be parsed ({exc}). "
                    "Pass `task` as an object, e.g. "
                    '`task={"sheet_id": "...", "body": {"rows": [...]}}`.'
                ) from exc
        else:
            raise ValueError(
                f"oneshot '{task_type}': `task` must be an object, got "
                f"string {task!r}. Required fields per task_type — "
                "create_drive_folder: name, optional parent_id; "
                "create_google_sheet: title; "
                "append_sheet_rows: sheet_id, body; "
                "send_gmail: to, subject, text; "
                "create_calendar_event: summary, start, end."
            )
    if not isinstance(task, dict):
        raise ValueError(
            f"oneshot '{task_type}': `task` must be a dict, got "
            f"{type(task).__name__}."
        )
    builder, interpret = _ONESHOTS[task_type]
    workflow_json, webhook_path = builder(task)

    # Oneshot templates emit nodes with `credentials: {<type>: {"id": ""}}`.
    # Without this pre-bind, n8n rejects activation with
    # "Credential not configured" and leaves an orphan workflow behind.
    # `_auto_bind_credentials` mutates the workflow in place, preferring
    # authorized creds (updatedAt > createdAt) over fresh blank shells.
    bindings, missing = await _auto_bind_credentials(
        config, user_id, workflow_json,
    )
    if bindings:
        logger.info("oneshot '%s' auto-bound: %s", task_type, "; ".join(bindings))
    if missing:
        # No authorized credential of the required type(s). Raise the
        # STOP_OAUTH_CREDENTIAL marker BEFORE creating the workflow, so
        # we don't leak a debug artifact n8n can't activate.
        raise RuntimeError(
            f"STOP_OAUTH_CREDENTIAL: oneshot '{task_type}' needs an "
            f"authorized credential of type `{', '.join(missing)}` but "
            "none was found. Call `n8n_google_services_setup` to get a "
            "consent URL and STOP. Do NOT create another credential shell."
        )

    # Create the workflow (inactive).
    create_body = {
        "name": workflow_json["name"],
        "nodes": workflow_json["nodes"],
        "connections": workflow_json["connections"],
        "settings": workflow_json["settings"],
    }
    created = await _n8n_request(
        config, user_id, "POST", "/api/v1/workflows",
        body=create_body, timeout=30.0,
    )
    wf_id = created.get("id") or ""
    if not wf_id:
        raise RuntimeError(f"n8n did not return an id for the new workflow: {created}")

    try:
        # Activate (webhook triggers need active=true to accept test
        # POSTs at /webhook/<path>, not just /webhook-test/<path>).
        await _n8n_request(
            config, user_id, "POST",
            f"/api/v1/workflows/{wf_id}/activate",
            timeout=20.0,
        )

        # Give n8n a beat to register the webhook route.
        await asyncio.sleep(1.0)

        payload = await _post_webhook_sync(webhook_path, task.get("body") or {})
    except Exception as exc:
        # Keep the workflow for debugging if firing failed — user can
        # inspect in n8n UI.
        logger.exception(
            "oneshot '%s' failed; keeping workflow id=%s for debug",
            task_type, wf_id,
        )
        # Recognise n8n's "Credential not configured: <type>" activation
        # failure and raise a hard-stop marker the brain can't paper over
        # by spawning another credential shell. SOUL.md's
        # `STOP_OAUTH_CREDENTIAL:` prefix makes the stuck detector +
        # brain instructions route straight to "tell user to authorize"
        # instead of the 3-day shell-spawning loop.
        text = str(exc)
        m = re.search(
            r"Credential not configured:\s*([A-Za-z0-9_]+)", text,
        )
        if m:
            cred_type = m.group(1)
            raise RuntimeError(
                f"STOP_OAUTH_CREDENTIAL: n8n rejected activation because "
                f"no authorized credential of type `{cred_type}` is "
                "connected. Before doing anything else, call "
                "`n8n_list_credentials` — if any entry of that type has "
                "`updatedAt > createdAt`, bind to it explicitly (don't "
                "create a new one). If none does, paste the consent URL "
                "from `n8n_google_services_setup` and STOP. "
                "Do NOT create another credential shell."
            ) from exc
        raise

    logger.info(
        "oneshot '%s' webhook payload: %r",
        task_type, payload,
    )
    result = interpret(payload)
    result["task_type"] = task_type
    result["workflow_id"] = wf_id
    result["registered"] = False

    # Auto-register if the user scoped to a project.
    if project and purpose and result.get("resource_id"):
        try:
            await project_assets.register_asset(
                config, user_id,
                project=project,
                purpose=purpose,
                resource_type=result["resource_type"],
                resource_id=result["resource_id"],
                url=result.get("url") or "",
                notes=task.get("notes"),
            )
            result["registered"] = True
        except Exception:
            logger.warning(
                "Failed to auto-register one-shot asset under '%s'",
                project, exc_info=True,
            )

    # Success — delete the ephemeral workflow so the user's n8n UI
    # stays clean.
    try:
        await _n8n_request(
            config, user_id, "POST",
            f"/api/v1/workflows/{wf_id}/deactivate",
            timeout=15.0,
        )
        await _n8n_request(
            config, user_id, "DELETE",
            f"/api/v1/workflows/{wf_id}", timeout=15.0,
        )
    except Exception:
        logger.warning(
            "Failed to clean up one-shot workflow %s — will need manual delete",
            wf_id, exc_info=True,
        )

    return result


# ---------------------------------------------------------------------------
# Composite: project_planning_kickoff
# ---------------------------------------------------------------------------


async def project_planning_kickoff(
    config: Any, user_id: str, *, project: str,
    description: str | None = None,
) -> dict:
    """Creates a Drive folder + 4 seeded Sheets for a new project.

    Sheets: Keywords, Content Calendar, Competitors, Tasks. Each gets
    starter header rows. Every resource is auto-registered under the
    `{project} Project` LazyBrain note.
    """
    out: dict[str, Any] = {"project": project, "assets": []}

    # 1) Drive folder
    folder_res = await run_oneshot(
        config, user_id,
        task_type="create_drive_folder",
        task={"folder_name": f"{project} Project"},
        project=project,
        purpose="Project Folder",
    )
    out["assets"].append(folder_res)
    folder_id = folder_res.get("resource_id") or ""

    seeded = [
        (
            "Keyword Research",
            "Keyword Tracker",
            ["Keyword", "Volume", "Difficulty", "Intent", "Priority", "Status"],
        ),
        (
            "Content Calendar",
            "Content Calendar",
            ["Week", "Topic", "Keywords", "Owner", "Status"],
        ),
        (
            "Competitors",
            "Competitor Map",
            ["Domain", "Strengths", "Top Keywords", "Notes"],
        ),
        (
            "Tasks",
            "Task Tracker",
            ["Task", "Owner", "Due Date", "Status", "Notes"],
        ),
    ]

    for title_suffix, purpose, headers in seeded:
        sheet_res = await run_oneshot(
            config, user_id,
            task_type="create_google_sheet",
            task={"title": f"{project} — {title_suffix}"},
            project=project,
            purpose=purpose,
        )
        out["assets"].append(sheet_res)
        sid = sheet_res.get("resource_id")
        if sid and headers:
            # Append header row so the sheet is visibly seeded.
            try:
                await run_oneshot(
                    config, user_id,
                    task_type="append_sheet_rows",
                    task={
                        "sheet_id": sid,
                        "body": {"rows": [
                            {h: h for h in headers}
                        ]},
                    },
                )
            except Exception:
                logger.warning(
                    "Failed to seed headers into %s/%s",
                    project, purpose, exc_info=True,
                )

    if description:
        # Drop a note under the project with the initial description.
        try:
            await project_assets.register_asset(
                config, user_id,
                project=project,
                purpose="Description",
                resource_type="other",
                resource_id=f"proj-desc-{uuid.uuid4().hex[:8]}",
                url="",
                notes=description[:200],
            )
        except Exception:
            logger.debug("project description registration failed", exc_info=True)

    out["folder_id"] = folder_id
    return out


# ---------------------------------------------------------------------------
# Skills exposed to the agent
# ---------------------------------------------------------------------------


class N8nRunTaskSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "n8n_run_task"

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "ONE-SHOT Google task via n8n (create folder / create sheet / "
            "append rows / send gmail / create calendar event). Creates "
            "a temporary n8n workflow, runs it, deletes it, and returns "
            "the result. Auto-registers created resources under a "
            "LazyBrain project note so you can look them up later "
            "(`lookup_project_asset`). Use this instead of n8n_create_workflow "
            "+ n8n_run_workflow + delete for any one-off Google operation. "
            "The result it returns is AUTHORITATIVE — if it succeeds "
            "(`updated_rows > 0`, `resource_id` set, or no error), the "
            "operation happened. Do NOT open a browser afterwards to "
            "visually verify a Google Sheets / Drive / Gmail write; the "
            "Google API is the source of truth, not Chrome rendering."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": list(_ONESHOTS.keys()),
                    "description": (
                        "create_drive_folder: name, optional parent_id. "
                        "create_google_sheet: title. "
                        "append_sheet_rows: sheet_id, body={rows:[{...}]}. "
                        "send_gmail: to, subject, text. "
                        "create_calendar_event: summary, start (ISO), end (ISO)."
                    ),
                },
                "task": {
                    "type": "object",
                    "description": "Task-specific parameters (see task_type).",
                },
                "project": {
                    "type": "string",
                    "description": (
                        "Optional — if set, the created resource is "
                        "registered under the '<project> Project' "
                        "LazyBrain note so it's recoverable later."
                    ),
                },
                "purpose": {
                    "type": "string",
                    "description": (
                        "Human-readable purpose, e.g. 'Keyword Tracker'. "
                        "Required if `project` is set."
                    ),
                },
            },
            "required": ["task_type", "task"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            result = await run_oneshot(
                self._config,
                user_id,
                task_type=params["task_type"],
                task=params.get("task") or {},
                project=params.get("project"),
                purpose=params.get("purpose"),
            )
        except Exception as exc:
            logger.warning("n8n_run_task failed", exc_info=True)
            return f"Error: {exc}"

        lines = [
            f"Done ({result['task_type']}):",
        ]
        if result.get("resource_id"):
            lines.append(f"  id:  {result['resource_id']}")
        if result.get("url"):
            lines.append(f"  url: {result['url']}")
        if result.get("registered"):
            lines.append(
                f"  → registered under [[{params['project']} Project]] "
                f"as '{params.get('purpose')}'."
            )
        elif params.get("project") and not result.get("registered"):
            lines.append("  (registration skipped — no resource id returned)")
        return "\n".join(lines)


class ProjectPlanningKickoffSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "project_planning_kickoff"

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Bootstrap a new project: creates a Drive folder + 4 seeded "
            "Google Sheets (Keywords, Content Calendar, Competitors, "
            "Tasks) and registers them all under the '<project> Project' "
            "LazyBrain note. Use when the user says 'start a project', "
            "'kickoff X', or 'plan X from scratch'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, e.g. 'Hirossa.com'.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional short description stored on the project note.",
                },
            },
            "required": ["project"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            out = await project_planning_kickoff(
                self._config,
                user_id,
                project=params["project"],
                description=params.get("description"),
            )
        except Exception as exc:
            logger.warning("project_planning_kickoff failed", exc_info=True)
            return f"Error: {exc}"

        lines = [
            f"Kickoff complete for [[{out['project']} Project]]:",
        ]
        for a in out["assets"]:
            if a.get("url"):
                lines.append(
                    f"  - {a.get('task_type')} → {a['url']}"
                )
        return "\n".join(lines)
