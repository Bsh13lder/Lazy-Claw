"""Project asset registry — remembers which Drive folder / Sheet / Doc
belongs to which project so the agent never has to ask "which sheet?"
twice.

Backed by LazyBrain: each project gets a note titled `{Project} Project`
(e.g. `Hirossa Project`). Assets are appended as bullet lines that carry
the resource id + URL + purpose. Because it's LazyBrain, assets show up
in the graph, are editable by the user, and auto-link via [[wikilinks]].

The format of each asset line is stable so it can be re-parsed on
lookup:

    - [Keyword Tracker](https://docs.google.com/...) — google_sheet · id: `abc123` · keyword research tracker

Exported skills:
  * `register_project_asset` — write or update
  * `lookup_project_asset`  — read (returns best match for purpose)
  * `list_project_assets`   — all assets for a project
"""

from __future__ import annotations

import logging
import re
from typing import Any

from lazyclaw.lazybrain import store
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# Stable asset-line format so we can round-trip parse it.
_ASSET_LINE = re.compile(
    r"^\-\s+"
    r"\[(?P<purpose>[^\]]+)\]"
    r"\((?P<url>[^)]+)\)"
    r"\s+\u2014\s+"
    r"(?P<resource_type>[a-z_]+)"
    r"\s+\u00b7\s+id:\s+`(?P<resource_id>[^`]+)`"
    r"(?:\s+\u00b7\s+(?P<notes>.+))?\s*$",
)


def _project_title(project: str) -> str:
    """Canonical title for a project note — `Hirossa Project`."""
    name = (project or "").strip()
    if not name:
        raise ValueError("project is required")
    # Don't double-append "Project" if user already said it.
    if name.lower().endswith(" project"):
        return name
    return f"{name} Project"


def _format_asset_line(
    *,
    purpose: str,
    url: str,
    resource_type: str,
    resource_id: str,
    notes: str | None,
) -> str:
    """Human-readable, re-parseable markdown bullet."""
    line = (
        f"- [{purpose}]({url}) \u2014 {resource_type} "
        f"\u00b7 id: `{resource_id}`"
    )
    if notes:
        line += f" \u00b7 {notes.strip()}"
    return line


def _parse_asset_line(line: str) -> dict | None:
    m = _ASSET_LINE.match(line.strip())
    if not m:
        return None
    return {
        "purpose": m.group("purpose").strip(),
        "url": m.group("url").strip(),
        "resource_type": m.group("resource_type").strip(),
        "resource_id": m.group("resource_id").strip(),
        "notes": (m.group("notes") or "").strip() or None,
    }


async def register_asset(
    config: Any,
    user_id: str,
    *,
    project: str,
    purpose: str,
    resource_type: str,
    resource_id: str,
    url: str,
    notes: str | None = None,
) -> dict:
    """Append (or replace) an asset line under the project's LazyBrain note.

    Returns the stored record.
    """
    title = _project_title(project)
    new_line = _format_asset_line(
        purpose=purpose,
        url=url,
        resource_type=resource_type,
        resource_id=resource_id,
        notes=notes,
    )

    existing = await store.find_by_title(config, user_id, title)

    if existing is None:
        # First asset for this project — seed a fresh note.
        header = f"# {title}\n\nAssets tracked by LazyClaw.\n\n"
        content = header + new_line + "\n"
        await store.save_note(
            config,
            user_id,
            content=content,
            title=title,
            tags=["owner/agent", "kind/project", "project-registry"],
            importance=7,
        )
        return {
            "project": project,
            "project_note_title": title,
            "purpose": purpose,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "url": url,
            "notes": notes,
            "status": "created",
        }

    # Project note exists — replace any existing line with same resource_id
    # (so re-registering is idempotent), otherwise append.
    existing_content: str = existing.get("content") or ""
    lines = existing_content.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        parsed = _parse_asset_line(line)
        if parsed and parsed["resource_id"] == resource_id:
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        # Also dedupe by (purpose + resource_type) so the agent doesn't
        # accumulate stale copies when re-running a task.
        for i, line in enumerate(lines):
            parsed = _parse_asset_line(line)
            if (
                parsed
                and parsed["purpose"].lower() == purpose.lower()
                and parsed["resource_type"] == resource_type
            ):
                lines[i] = new_line
                replaced = True
                break
    if not replaced:
        lines.append(new_line)

    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"

    await store.update_note(
        config,
        user_id,
        existing["id"],
        content=new_content,
    )

    return {
        "project": project,
        "project_note_title": title,
        "purpose": purpose,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "url": url,
        "notes": notes,
        "status": "updated" if replaced else "appended",
    }


async def list_assets(
    config: Any,
    user_id: str,
    project: str,
) -> list[dict]:
    title = _project_title(project)
    note = await store.find_by_title(config, user_id, title)
    if note is None:
        return []
    out: list[dict] = []
    for raw in (note.get("content") or "").splitlines():
        parsed = _parse_asset_line(raw)
        if parsed:
            out.append(parsed)
    return out


async def lookup_asset(
    config: Any,
    user_id: str,
    project: str,
    *,
    purpose: str | None = None,
    resource_type: str | None = None,
) -> dict | None:
    """Best-match asset lookup: exact purpose → fuzzy → first of type."""
    assets = await list_assets(config, user_id, project)
    if not assets:
        return None

    if purpose:
        want = purpose.lower().strip()
        for a in assets:
            if a["purpose"].lower() == want:
                return a
        # Fuzzy: substring match on purpose
        for a in assets:
            if want in a["purpose"].lower() or a["purpose"].lower() in want:
                if (not resource_type) or a["resource_type"] == resource_type:
                    return a

    if resource_type:
        for a in assets:
            if a["resource_type"] == resource_type:
                return a

    return assets[0]


# ---------------------------------------------------------------------------
# Skills — exposed to the agent
# ---------------------------------------------------------------------------


class RegisterProjectAssetSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "register_project_asset"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def read_only(self) -> bool:
        return False

    @property
    def description(self) -> str:
        return (
            "Remember that a Drive folder / Google Sheet / Doc belongs "
            "to a project. Use this right after creating a Google resource "
            "the user might want to re-use (e.g. 'Hirossa keyword tracker'). "
            "Writes a bullet into the '<project> Project' LazyBrain note so "
            "future turns can look it up by purpose."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, e.g. 'Hirossa' or 'Hirossa.com'.",
                },
                "purpose": {
                    "type": "string",
                    "description": (
                        "What this asset is FOR, e.g. 'Keyword Tracker', "
                        "'Content Calendar', 'Client Folder'. User-facing."
                    ),
                },
                "resource_type": {
                    "type": "string",
                    "enum": [
                        "google_sheet",
                        "google_drive_folder",
                        "google_doc",
                        "google_slides",
                        "gmail_label",
                        "calendar_event",
                        "n8n_workflow",
                        "other",
                    ],
                },
                "resource_id": {
                    "type": "string",
                    "description": "Provider ID — e.g. Drive file ID, n8n workflow ID.",
                },
                "url": {
                    "type": "string",
                    "description": "Clickable URL to the resource.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional one-line note, e.g. 'SEO tracker, append-only'.",
                },
            },
            "required": [
                "project",
                "purpose",
                "resource_type",
                "resource_id",
                "url",
            ],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            rec = await register_asset(
                self._config,
                user_id,
                project=params["project"],
                purpose=params["purpose"],
                resource_type=params["resource_type"],
                resource_id=params["resource_id"],
                url=params["url"],
                notes=params.get("notes"),
            )
        except Exception as exc:
            logger.warning("register_project_asset failed", exc_info=True)
            return f"Error: {exc}"
        return (
            f"Registered {rec['status']}: '{rec['purpose']}' "
            f"({rec['resource_type']}) under [[{rec['project_note_title']}]]."
        )


class LookupProjectAssetSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lookup_project_asset"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Find the ID + URL of a previously-registered project asset "
            "(Drive folder, Google Sheet, Doc, etc.) by project + purpose. "
            "Call this BEFORE creating a new one if the user mentions an "
            "existing project like 'Hirossa keyword tracker'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "purpose": {
                    "type": "string",
                    "description": "Asset purpose to match (fuzzy OK).",
                },
                "resource_type": {"type": "string"},
            },
            "required": ["project"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        asset = await lookup_asset(
            self._config,
            user_id,
            params["project"],
            purpose=params.get("purpose"),
            resource_type=params.get("resource_type"),
        )
        if asset is None:
            all_assets = await list_assets(
                self._config, user_id, params["project"],
            )
            if not all_assets:
                return (
                    f"No project '{params['project']}' registered yet. "
                    "Create one by calling register_project_asset after "
                    "making the first resource."
                )
            lines = [
                f"No exact match. Assets on [[{_project_title(params['project'])}]]:",
            ]
            for a in all_assets:
                lines.append(
                    f"  - {a['purpose']} ({a['resource_type']}): {a['url']}"
                )
            return "\n".join(lines)
        return (
            f"{asset['purpose']} ({asset['resource_type']})\n"
            f"  id:  {asset['resource_id']}\n"
            f"  url: {asset['url']}"
            + (f"\n  notes: {asset['notes']}" if asset.get("notes") else "")
        )


class ListProjectAssetsSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_project_assets"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "List every registered asset (Drive folders, Sheets, Docs, etc.) "
            "for a project — e.g. 'show me everything under Hirossa'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        assets = await list_assets(
            self._config, user_id, params["project"],
        )
        if not assets:
            return f"No assets registered for '{params['project']}'."
        lines = [
            f"[[{_project_title(params['project'])}]] — {len(assets)} asset(s):",
        ]
        for a in assets:
            lines.append(
                f"  - {a['purpose']} ({a['resource_type']}) → {a['url']}"
            )
        return "\n".join(lines)
