"""Docs API — private encrypted documents for the web UI.

Exposes :mod:`lazyclaw.docs.store` to the embedded Univer document editor. The
store is scoped by ``user_id`` and the document snapshot is AES-256-GCM
encrypted at rest; we hand the decrypted snapshot back to the owner only.
``PUT`` saves the snapshot the browser hands us (autosave); ``.docx`` / PDF
export lives in :mod:`lazyclaw.docs.docx_io`.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.docs.docx_io import snapshot_to_docx, snapshot_to_pdf
from lazyclaw.docs.store import (
    create_doc,
    delete_doc,
    get_doc,
    list_docs,
    save_doc,
)
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.runtime.doc_specialist import ai_edit_document

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MEDIA = "application/pdf"


def _safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", name or "").strip() or "document"
    return base[:80]


_config = load_config()

router = APIRouter(prefix="/api/docs", tags=["docs"])


class CreateDocBody(BaseModel):
    name: str = Field(default="Untitled doc", max_length=120)


class SaveDocBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]


class AiEditBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


@router.get("")
async def list_docs_route(user: User = Depends(get_current_user)):
    """List the current user's docs (index only, no payload)."""
    docs = await list_docs(_config, user.id)
    return {"docs": docs, "count": len(docs)}


@router.post("")
async def create_doc_route(
    body: CreateDocBody,
    user: User = Depends(get_current_user),
):
    """Create a new blank doc."""
    doc = await create_doc(_config, user.id, body.name)
    return {"doc": doc}


@router.get("/{doc_id}")
async def get_doc_route(
    doc_id: str,
    user: User = Depends(get_current_user),
):
    """Fetch a doc with its decrypted Univer snapshot payload."""
    doc = await get_doc(_config, user.id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Doc not found")
    return doc


@router.put("/{doc_id}")
async def save_doc_route(
    doc_id: str,
    body: SaveDocBody,
    user: User = Depends(get_current_user),
):
    """Persist the document snapshot from the editor (autosave)."""
    row = await save_doc(_config, user.id, body.name, body.payload, doc_id=doc_id)
    return {"doc": row}


@router.post("/{doc_id}/ai")
async def ai_edit_doc_route(
    doc_id: str,
    body: AiEditBody,
    user: User = Depends(get_current_user),
):
    """Edit the open document from a natural-language instruction (✨ AI box).

    Synchronous: runs one Document-Specialist turn and returns the fresh Univer
    snapshot so the editor reloads in place. Never routes through Telegram /
    background tasks.
    """
    result = await ai_edit_document(_config, user.id, "docs", doc_id, body.instruction)
    return {
        "ok": result.ok,
        "summary": result.summary,
        "snapshot": result.snapshot,
        "error": result.error,
    }


@router.delete("/{doc_id}")
async def delete_doc_route(
    doc_id: str,
    user: User = Depends(get_current_user),
):
    """Delete a doc entirely."""
    ok = await delete_doc(_config, user.id, doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Doc not found")
    return {"status": "deleted", "id": doc_id}


@router.get("/{doc_id}/export")
async def export_doc_route(
    doc_id: str,
    format: Literal["docx", "pdf"] = Query("docx"),
    user: User = Depends(get_current_user),
):
    """Download a doc as ``.docx`` or PDF (PDF needs LibreOffice on the host)."""
    doc = await get_doc(_config, user.id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Doc not found")
    snap = doc["payload"]
    fname = _safe_filename(doc["name"])
    if format == "pdf":
        content = snapshot_to_pdf(snap)
        if content is None:
            raise HTTPException(
                status_code=503, detail="PDF export needs LibreOffice"
            )
        media, ext = _PDF_MEDIA, "pdf"
    else:
        content = snapshot_to_docx(snap)
        media, ext = _DOCX_MEDIA, "docx"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}.{ext}"'},
    )
