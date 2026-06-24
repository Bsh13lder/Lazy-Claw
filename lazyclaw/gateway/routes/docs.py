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
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.docs.docx_io import docx_to_snapshot, snapshot_to_docx, snapshot_to_pdf
from lazyclaw.export_crypto import protect_export
from lazyclaw.docs.store import (
    DocConflictError,
    create_doc,
    delete_doc,
    get_doc,
    get_doc_changes,
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


def _validate_client_id(client_id: str | None) -> str | None:
    """Validate an optional client-supplied id is a real UUID, else 400.

    Offline-first clients mint UUIDs locally so create is idempotent on replay.
    A non-UUID id is rejected at the boundary rather than landing a junk row.
    """
    if client_id is None:
        return None
    try:
        UUID(client_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="id must be a valid UUID")
    return client_id


_config = load_config()

router = APIRouter(prefix="/api/docs", tags=["docs"])


class CreateDocBody(BaseModel):
    # Optional client-minted UUID for offline-first idempotent replay. When
    # provided and the id already exists for this user, the existing doc is
    # returned instead of creating a duplicate.
    id: str | None = Field(default=None, max_length=128)
    name: str = Field(default="Untitled doc", max_length=120)


class SaveDocBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    payload: dict[str, Any]
    base_updated_at: str | None = None
    tags: list[str] | None = None


class AiEditBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


class ExportBody(BaseModel):
    format: Literal["docx", "pdf"] = "docx"
    # Optional — when set, the export is wrapped in an AES-256 encrypted .zip.
    password: str | None = Field(default=None, max_length=256)


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
    """Create a new blank doc.

    Accepts an optional client-minted ``id`` (UUID) for offline-first replay:
    a second POST with the same id returns the existing doc (idempotent),
    never a duplicate.
    """
    client_id = _validate_client_id(body.id)
    doc = await create_doc(_config, user.id, body.name, doc_id=client_id)
    return {"doc": doc}


@router.get("/changes")
async def doc_changes_route(
    user: User = Depends(get_current_user),
    since: str | None = Query(
        default=None,
        description=(
            "ISO-8601 / server timestamp. Only docs changed after this are "
            "returned (live + tombstones). Omit for a full sync. Use the `now` "
            "field from the previous response as the next `since` value."
        ),
    ),
):
    """Delta feed for offline-first clients (index rows only, no payload).

    Returns ``{docs: [...live...], deleted: [...ids...], now: "<iso>"}``.
    """
    return await get_doc_changes(_config, user.id, since=since)


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
    """Persist the document snapshot from the editor (autosave).

    On conflict (stale ``base_updated_at``), 409 carries the full decrypted
    current snapshot so clients never need a second fetch.
    """
    try:
        row = await save_doc(
            _config, user.id, body.name, body.payload, doc_id=doc_id,
            base_updated_at=body.base_updated_at, tags=body.tags,
        )
    except DocConflictError as exc:
        return JSONResponse(status_code=409, content={"detail": "conflict", "current": exc.current})
    except LookupError:
        raise HTTPException(status_code=404, detail="Doc not found")
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


@router.post("/import")
async def import_doc_route(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Import a ``.docx`` upload as a new document (lists/headings preserved)."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    stem = (file.filename or "Imported").rsplit("/", 1)[-1]
    if stem.lower().endswith(".docx"):
        stem = stem[:-5]
    stem = stem or "Imported"
    try:
        snap = docx_to_snapshot(data, name=stem)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse .docx file")
    row = await save_doc(_config, user.id, stem, snap)
    return {"doc": row}


def _render_doc(snap: dict[str, Any], format: str) -> tuple[bytes, str, str]:
    """Render a doc snapshot to ``(bytes, ext, media)``. Raises 503 if PDF needs
    LibreOffice and it's unavailable."""
    if format == "pdf":
        content = snapshot_to_pdf(snap)
        if content is None:
            raise HTTPException(status_code=503, detail="PDF export needs LibreOffice")
        return content, "pdf", _PDF_MEDIA
    return snapshot_to_docx(snap), "docx", _DOCX_MEDIA


def _export_response(content: bytes, base: str, ext: str, media: str, password):
    data, fname, out_media = protect_export(content, base, ext, media, password)
    return Response(
        content=data,
        media_type=out_media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
    content, ext, media = _render_doc(doc["payload"], format)
    return _export_response(content, _safe_filename(doc["name"]), ext, media, None)


@router.post("/{doc_id}/export")
async def export_doc_post_route(
    doc_id: str,
    body: ExportBody,
    user: User = Depends(get_current_user),
):
    """Download a doc, optionally AES-256 encrypted in a password ``.zip``.

    Password travels in the body (never a query string). Empty/absent → plain.
    """
    doc = await get_doc(_config, user.id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Doc not found")
    content, ext, media = _render_doc(doc["payload"], body.format)
    return _export_response(
        content, _safe_filename(doc["name"]), ext, media, body.password
    )
