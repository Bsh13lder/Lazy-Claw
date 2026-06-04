"""Sheets API — private encrypted spreadsheets for the web UI.

Exposes :mod:`lazyclaw.sheets.store` to the embedded Univer editor. The store
is scoped by ``user_id`` and the workbook snapshot is AES-256-GCM encrypted at
rest; we hand the decrypted snapshot back to the owner only. ``PUT`` saves the
snapshot the browser hands us (autosave); xlsx import/export lives in
:mod:`lazyclaw.sheets.xlsx_io` and is wired here in Phase 3.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.runtime.doc_specialist import ai_edit_document
from lazyclaw.sheets.store import (
    create_sheet,
    delete_sheet,
    get_sheet,
    list_sheets,
    save_sheet,
)
from lazyclaw.sheets.xlsx_io import (
    snapshot_to_csv,
    snapshot_to_xlsx,
    xlsx_to_snapshot,
)

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", name or "").strip() or "sheet"
    return base[:80]

_config = load_config()

router = APIRouter(prefix="/api/sheets", tags=["sheets"])


class CreateSheetBody(BaseModel):
    name: str = Field(default="Untitled sheet", max_length=120)


class SaveSheetBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]


class AiEditBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


@router.get("")
async def list_sheets_route(user: User = Depends(get_current_user)):
    """List the current user's sheets (index only, no payload)."""
    sheets = await list_sheets(_config, user.id)
    return {"sheets": sheets, "count": len(sheets)}


@router.post("")
async def create_sheet_route(
    body: CreateSheetBody,
    user: User = Depends(get_current_user),
):
    """Create a new blank sheet."""
    sheet = await create_sheet(_config, user.id, body.name)
    return {"sheet": sheet}


@router.get("/{sheet_id}")
async def get_sheet_route(
    sheet_id: str,
    user: User = Depends(get_current_user),
):
    """Fetch a sheet with its decrypted Univer snapshot payload."""
    sheet = await get_sheet(_config, user.id, sheet_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Sheet not found")
    return sheet


@router.put("/{sheet_id}")
async def save_sheet_route(
    sheet_id: str,
    body: SaveSheetBody,
    user: User = Depends(get_current_user),
):
    """Persist the workbook snapshot from the editor (autosave)."""
    row = await save_sheet(_config, user.id, body.name, body.payload, sheet_id=sheet_id)
    return {"sheet": row}


@router.post("/{sheet_id}/ai")
async def ai_edit_sheet_route(
    sheet_id: str,
    body: AiEditBody,
    user: User = Depends(get_current_user),
):
    """Edit the open sheet from a natural-language instruction (✨ AI box).

    Synchronous: runs one Document-Specialist turn (translating "add a total"
    into the right formula) and returns the recalculated Univer snapshot so the
    editor reloads in place.
    """
    result = await ai_edit_document(_config, user.id, "sheets", sheet_id, body.instruction)
    return {
        "ok": result.ok,
        "summary": result.summary,
        "snapshot": result.snapshot,
        "error": result.error,
    }


@router.delete("/{sheet_id}")
async def delete_sheet_route(
    sheet_id: str,
    user: User = Depends(get_current_user),
):
    """Delete a sheet entirely."""
    ok = await delete_sheet(_config, user.id, sheet_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sheet not found")
    return {"status": "deleted", "id": sheet_id}


@router.post("/import")
async def import_sheet_route(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Import a .xlsx upload as a new sheet (formulas preserved)."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    stem = (file.filename or "Imported").rsplit("/", 1)[-1]
    if stem.lower().endswith(".xlsx"):
        stem = stem[:-5]
    stem = stem or "Imported"
    try:
        snap = xlsx_to_snapshot(data, name=stem)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse .xlsx file")
    row = await save_sheet(_config, user.id, stem, snap)
    return {"sheet": row}


@router.get("/{sheet_id}/export")
async def export_sheet_route(
    sheet_id: str,
    format: Literal["xlsx", "csv"] = Query("xlsx"),
    user: User = Depends(get_current_user),
):
    """Download a sheet as .xlsx (formulas recompute on open) or .csv."""
    sheet = await get_sheet(_config, user.id, sheet_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Sheet not found")
    snap = sheet["payload"]
    fname = _safe_filename(sheet["name"])
    if format == "csv":
        content: bytes = snapshot_to_csv(snap).encode("utf-8")
        media, ext = "text/csv", "csv"
    else:
        content = snapshot_to_xlsx(snap)
        media, ext = _XLSX_MEDIA, "xlsx"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}.{ext}"'},
    )
