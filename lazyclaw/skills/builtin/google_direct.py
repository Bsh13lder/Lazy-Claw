"""Direct Google Workspace integration — atomic ops without n8n.

Uses `google-api-python-client` + `google-auth` directly against Drive,
Sheets, Gmail, and Calendar REST APIs. Replaces the n8n_oneshot.py path
for the five atomic operations that never needed a workflow engine:

  * create_drive_folder
  * create_google_sheet
  * append_sheet_rows
  * send_gmail
  * create_calendar_event

Why not `workspace-mcp`? See ADR-0003. Short version: we will adopt
workspace-mcp once its OAuth consent UX is debugged with the user's
GCP config. Credential file format here is intentionally identical to
workspace-mcp's (`~/.google_workspace_mcp/credentials/{email}.json`) so
switching backends later is a config flag, not a rewrite.

Credentials come from n8n via the one-time import in
``tools/import_n8n_google_creds.py`` — n8n's refresh tokens are valid
for the same OAuth client that LazyClaw uses, so the port is seamless.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import pathlib
from email.mime.text import MIMEText
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


_CREDS_DIR = pathlib.Path.home() / ".google_workspace_mcp" / "credentials"


def _creds_path(user_email: str) -> pathlib.Path:
    # Sanitize identical to workspace-mcp's LocalDirectoryCredentialStore.
    import re
    safe = re.sub(r"[^a-zA-Z0-9@._-]", "_", user_email)
    return _CREDS_DIR / f"{safe}.json"


def _load_credentials(user_email: str):
    """Load cached OAuth credentials, refreshing the access token if needed."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise RuntimeError(
            "google-api-python-client + google-auth are required. "
            "Install: `pip install google-api-python-client google-auth`"
        ) from exc

    path = _creds_path(user_email)
    if not path.exists():
        raise RuntimeError(
            f"No Google credentials cached for {user_email!r}. "
            f"Run `python -m lazyclaw.tools.import_n8n_google_creds` to "
            f"seed from n8n, or complete an OAuth consent flow via "
            f"workspace-mcp."
        )

    data = json.loads(path.read_text())
    creds = Credentials(
        token=data.get("token") or None,
        refresh_token=data["refresh_token"],
        token_uri=data.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes") or [],
    )
    if not creds.valid:
        creds.refresh(Request())
        data["token"] = creds.token
        if creds.expiry:
            data["expiry"] = creds.expiry.isoformat()
        # Write back atomically with tight perms.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
    return creds


def _build(api: str, version: str, user_email: str):
    from googleapiclient.discovery import build
    return build(api, version, credentials=_load_credentials(user_email),
                 cache_discovery=False)


# ---------------------------------------------------------------------------
# Five atomic operations — each returns a dict with resource_id + url +
# raw API response for inspection.
# ---------------------------------------------------------------------------


def create_drive_folder(
    user_email: str, *, name: str, parent_id: str | None = None,
) -> dict[str, Any]:
    svc = _build("drive", "v3", user_email)
    body: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        body["parents"] = [parent_id]
    f = svc.files().create(body=body, fields="id,name,webViewLink").execute()
    return {
        "resource_type": "google_drive_folder",
        "resource_id": f["id"],
        "url": f.get("webViewLink")
               or f"https://drive.google.com/drive/folders/{f['id']}",
        "raw": f,
    }


def create_google_sheet(user_email: str, *, title: str) -> dict[str, Any]:
    svc = _build("sheets", "v4", user_email)
    sheet = svc.spreadsheets().create(
        body={"properties": {"title": title}},
        fields="spreadsheetId,spreadsheetUrl",
    ).execute()
    return {
        "resource_type": "google_sheet",
        "resource_id": sheet["spreadsheetId"],
        "url": sheet["spreadsheetUrl"],
        "raw": sheet,
    }


def _normalize_rows(values: Any) -> list[list[Any]]:
    """Accept rows in several LLM-friendly shapes, return a 2-D list."""
    if not values:
        return []
    if isinstance(values, (str, int, float, bool)):
        return [[values]]
    if isinstance(values, dict):
        # Single dict: emit its values in insertion order.
        return [list(values.values())]
    if isinstance(values, list):
        # Check first element to decide shape.
        first = values[0] if values else None
        if isinstance(first, list):
            return values  # already 2-D
        if isinstance(first, dict):
            return [list(r.values()) for r in values]
        # Flat list of scalars → one row.
        return [list(values)]
    return [[str(values)]]


def append_sheet_rows(
    user_email: str, *, sheet_id: str,
    values: Any, range_: str = "A:A",
) -> dict[str, Any]:
    svc = _build("sheets", "v4", user_email)
    rows = _normalize_rows(values)
    if not rows:
        raise ValueError("append_sheet_rows: `values` is empty")
    resp = svc.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=range_,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    updates = resp.get("updates") or {}
    return {
        "resource_type": "google_sheet_rows",
        "resource_id": sheet_id,
        "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}",
        "updated_rows": updates.get("updatedRows", 0),
        "updated_cells": updates.get("updatedCells", 0),
        "updated_range": updates.get("updatedRange", ""),
        "raw": resp,
    }


def send_gmail(
    user_email: str, *, to: str, subject: str, text: str,
) -> dict[str, Any]:
    msg = MIMEText(text)
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    svc = _build("gmail", "v1", user_email)
    sent = svc.users().messages().send(
        userId="me", body={"raw": raw},
    ).execute()
    return {
        "resource_type": "gmail_message",
        "resource_id": sent["id"],
        "url": f"https://mail.google.com/mail/u/0/#inbox/{sent['id']}",
        "thread_id": sent.get("threadId"),
        "raw": sent,
    }


def create_calendar_event(
    user_email: str, *, summary: str, start: str, end: str,
    description: str | None = None, calendar_id: str = "primary",
) -> dict[str, Any]:
    svc = _build("calendar", "v3", user_email)
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    if description:
        body["description"] = description
    ev = svc.events().insert(calendarId=calendar_id, body=body).execute()
    return {
        "resource_type": "calendar_event",
        "resource_id": ev["id"],
        "url": ev.get("htmlLink", ""),
        "raw": ev,
    }


# ---------------------------------------------------------------------------
# Task dispatch table — same shape as n8n_oneshot._ONESHOTS so the agent
# sees an identical interface whether we're on the direct-API or MCP path.
# ---------------------------------------------------------------------------


_TASKS = {
    "create_drive_folder": create_drive_folder,
    "create_google_sheet": create_google_sheet,
    "append_sheet_rows": append_sheet_rows,
    "send_gmail": send_gmail,
    "create_calendar_event": create_calendar_event,
}


def _default_email() -> str:
    # Prefer USER_GOOGLE_EMAIL (matches workspace-mcp convention). Falls
    # back to scanning the credentials dir for a single cached user.
    env = os.getenv("USER_GOOGLE_EMAIL", "").strip()
    if env:
        return env
    if _CREDS_DIR.exists():
        cached = list(_CREDS_DIR.glob("*.json"))
        if len(cached) == 1:
            return cached[0].stem
    raise RuntimeError(
        "No default Google account set. Export USER_GOOGLE_EMAIL or "
        "pass `user_email` explicitly."
    )


async def run_task(
    *, task_type: str, task: dict,
    user_email: str | None = None,
) -> dict[str, Any]:
    """Single entrypoint mirroring n8n_oneshot.run_oneshot signature."""
    if task_type not in _TASKS:
        raise ValueError(
            f"Unknown google_direct task_type '{task_type}'. "
            f"Valid: {', '.join(sorted(_TASKS))}"
        )
    if not isinstance(task, dict):
        raise ValueError(
            f"google_direct '{task_type}': `task` must be a dict, got "
            f"{type(task).__name__}"
        )
    email = user_email or _default_email()
    fn = _TASKS[task_type]

    # Each function takes different kwargs — unpack from task.
    if task_type == "create_drive_folder":
        return fn(email,
                  name=task.get("name") or task.get("folder_name") or "",
                  parent_id=task.get("parent_id"))
    if task_type == "create_google_sheet":
        return fn(email,
                  title=task.get("title") or task.get("name") or "")
    if task_type == "append_sheet_rows":
        sheet_id = task.get("sheet_id") or task.get("spreadsheet_id") or ""
        if not sheet_id:
            raise ValueError("append_sheet_rows requires `sheet_id`")
        values = (task.get("values")
                  or task.get("rows")
                  or (task.get("body") or {}).get("values")
                  or (task.get("body") or {}).get("rows")
                  or task.get("text")
                  or task.get("value"))
        return fn(email, sheet_id=sheet_id,
                  values=values,
                  range_=task.get("range") or "A:A")
    if task_type == "send_gmail":
        return fn(email,
                  to=task.get("to") or "",
                  subject=task.get("subject") or "(no subject)",
                  text=task.get("text") or task.get("body") or "")
    if task_type == "create_calendar_event":
        return fn(email,
                  summary=task.get("summary") or task.get("title") or "",
                  start=task.get("start") or "",
                  end=task.get("end") or "",
                  description=task.get("description"))
    raise RuntimeError(f"unreachable: {task_type}")


# ---------------------------------------------------------------------------
# Skill wrapper — mirrors n8n_oneshot.N8nRunTaskSkill so it slots into
# the registry identically.
# ---------------------------------------------------------------------------


class GoogleDirectTaskSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "google_run_task"

    @property
    def category(self) -> str:
        return "google"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Atomic Google Workspace operation via direct API (no n8n). "
            "task_type ∈ {create_drive_folder, create_google_sheet, "
            "append_sheet_rows, send_gmail, create_calendar_event}. "
            "Returns resource_id + url + updated_rows (for appends). "
            "Result is AUTHORITATIVE — if it succeeds, the operation "
            "happened. Do NOT open a browser to visually verify."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": list(_TASKS),
                    "description": (
                        "create_drive_folder: {name, parent_id?}. "
                        "create_google_sheet: {title}. "
                        "append_sheet_rows: {sheet_id, values|rows|text, range?}. "
                        "send_gmail: {to, subject, text}. "
                        "create_calendar_event: {summary, start, end, description?}."
                    ),
                },
                "task": {
                    "type": "object",
                    "description": "Task-specific parameters (see task_type).",
                },
                "user_email": {
                    "type": "string",
                    "description": (
                        "Google account to run as. Defaults to "
                        "USER_GOOGLE_EMAIL env var, or the single cached "
                        "account if only one is set up."
                    ),
                },
            },
            "required": ["task_type", "task"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            result = await run_task(
                task_type=params["task_type"],
                task=params.get("task") or {},
                user_email=params.get("user_email"),
            )
        except Exception as exc:
            logger.warning("google_run_task failed", exc_info=True)
            return f"Error: {exc}"

        lines = [f"Done ({result.get('resource_type','?')}):"]
        if result.get("resource_id"):
            lines.append(f"  id:  {result['resource_id']}")
        if result.get("url"):
            lines.append(f"  url: {result['url']}")
        if "updated_rows" in result:
            lines.append(
                f"  wrote: {result['updated_rows']} rows "
                f"({result.get('updated_cells',0)} cells) "
                f"at {result.get('updated_range','')}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Composite: project_planning_kickoff (direct-API port of
# n8n_oneshot.project_planning_kickoff at line 669).
#
# Bootstraps a new project: Drive folder + 4 seeded Sheets, all auto-
# registered under the `{project} Project` LazyBrain note so the agent
# can recover them via `lookup_project_asset` later.
# ---------------------------------------------------------------------------


_KICKOFF_SHEETS: list[tuple[str, str, list[str]]] = [
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


async def project_planning_kickoff(
    config: Any, user_id: str, *,
    project: str, description: str | None = None,
    user_email: str | None = None,
) -> dict[str, Any]:
    """Drive folder + 4 seeded Sheets via direct Google API.

    Each created resource is registered under the LazyBrain
    ``{project} Project`` note so it's recoverable later.
    Failures in any single sheet creation/seed are logged but never
    abort the kickoff — the user gets whatever did succeed.
    """
    from lazyclaw.skills.builtin import project_assets

    out: dict[str, Any] = {"project": project, "assets": []}

    async def _register(res: dict[str, Any], purpose: str) -> None:
        if not res.get("resource_id"):
            return
        try:
            await project_assets.register_asset(
                config, user_id,
                project=project,
                purpose=purpose,
                resource_type=res["resource_type"],
                resource_id=res["resource_id"],
                url=res.get("url") or "",
            )
        except Exception:
            logger.warning(
                "register_asset failed for %s/%s",
                project, purpose, exc_info=True,
            )

    # 1) Drive folder
    folder_res = await run_task(
        task_type="create_drive_folder",
        task={"name": f"{project} Project"},
        user_email=user_email,
    )
    folder_res["task_type"] = "create_drive_folder"
    folder_res["purpose"] = "Project Folder"
    out["assets"].append(folder_res)
    await _register(folder_res, "Project Folder")
    folder_id = folder_res.get("resource_id") or ""
    out["folder_id"] = folder_id

    # 2) Four seeded sheets
    for title_suffix, purpose, headers in _KICKOFF_SHEETS:
        try:
            sheet_res = await run_task(
                task_type="create_google_sheet",
                task={"title": f"{project} — {title_suffix}"},
                user_email=user_email,
            )
        except Exception as exc:
            logger.warning(
                "kickoff sheet '%s' creation failed: %s", purpose, exc,
            )
            continue
        sheet_res["task_type"] = "create_google_sheet"
        sheet_res["purpose"] = purpose
        out["assets"].append(sheet_res)
        await _register(sheet_res, purpose)

        # Header row — single 2-D row of strings.
        sid = sheet_res.get("resource_id")
        if sid and headers:
            try:
                await run_task(
                    task_type="append_sheet_rows",
                    task={"sheet_id": sid, "values": [headers]},
                    user_email=user_email,
                )
            except Exception:
                logger.warning(
                    "Failed to seed headers into %s/%s",
                    project, purpose, exc_info=True,
                )

    # 3) Description note (best-effort).
    if description:
        try:
            import uuid
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

    return out


class GoogleProjectPlanningKickoffSkill(BaseSkill):
    """Direct-API replacement for n8n's ProjectPlanningKickoffSkill.

    Identical user-facing behavior, no n8n round-trip per asset, fewer
    failure modes (no workflow create/run/delete dance).
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "google_project_planning_kickoff"

    @property
    def category(self) -> str:
        return "google"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Bootstrap a new project via direct Google API: creates a "
            "Drive folder + 4 seeded Sheets (Keywords, Content Calendar, "
            "Competitors, Tasks) and registers them all under the "
            "'<project> Project' LazyBrain note. Use when the user says "
            "'start a project', 'kickoff X', or 'plan X from scratch'. "
            "Replaces the n8n-backed `project_planning_kickoff`."
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
                    "description": (
                        "Optional short description stored on the project note."
                    ),
                },
                "user_email": {
                    "type": "string",
                    "description": (
                        "Google account to run as. Defaults to "
                        "USER_GOOGLE_EMAIL or single cached account."
                    ),
                },
            },
            "required": ["project"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            out = await project_planning_kickoff(
                self._config, user_id,
                project=params["project"],
                description=params.get("description"),
                user_email=params.get("user_email"),
            )
        except Exception as exc:
            logger.warning(
                "google_project_planning_kickoff failed", exc_info=True,
            )
            return f"Error: {exc}"

        lines = [f"Kickoff complete for [[{out['project']} Project]]:"]
        for a in out["assets"]:
            if a.get("url"):
                lines.append(
                    f"  - {a.get('purpose') or a.get('task_type')}"
                    f" → {a['url']}"
                )
        return "\n".join(lines)
