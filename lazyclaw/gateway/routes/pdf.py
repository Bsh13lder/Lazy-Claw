"""PDF API — private encrypted PDF files for the web UI.

Exposes :mod:`lazyclaw.pdf.store` to the embedded viewer + the agent skills.
The store is scoped by ``user_id`` and each PDF is AES-256-GCM encrypted at
rest; we hand the decrypted bytes back to the owner only.

Endpoint contract matches ``web/src/api.ts``:
- ``GET    /api/pdf``                → {"files": [...], "count": n}
- ``POST   /api/pdf/import``         → {"file": meta}   (UploadFile; %PDF only)
- ``GET    /api/pdf/{id}``           → meta json (no bytes)
- ``GET    /api/pdf/{id}/raw``       → application/pdf inline (viewer)
- ``GET    /api/pdf/{id}/download``  → application/pdf attachment
- ``GET    /api/pdf/{id}/extract``   → {"text": ..., "pages": n}
- ``DELETE /api/pdf/{id}``           → {"status": "deleted", "id": ...}

``/import`` is declared before the ``/{pdf_id}`` capture route so the literal
path wins.
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.export_crypto import protect_export
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.pdf import ops
from lazyclaw.pdf.store import (
    create_pdf,
    delete_pdf,
    get_pdf,
    get_pdf_changes,
    list_pdf_versions,
    list_pdfs,
    restore_pdf_version,
    update_pdf_meta,
)
from lazyclaw.runtime.doc_specialist import ai_edit_document

_config = load_config()

router = APIRouter(prefix="/api/pdf", tags=["pdf"])

_PDF_MEDIA = "application/pdf"


class AiEditBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


class PatchPdfBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    tags: list[str] | None = None


def _safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", name or "").strip() or "document"
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base[:120]


def _validate_client_id(client_id: str | None) -> str | None:
    """Validate an optional client-supplied id is a real UUID, else 400.

    Offline-first clients mint UUIDs locally so import is idempotent on replay.
    A non-UUID id is rejected at the boundary rather than landing a junk row.
    """
    if client_id is None or client_id == "":
        return None
    try:
        UUID(client_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="id must be a valid UUID")
    return client_id


def _meta(row: dict) -> dict:
    """Strip any bytes from a store row, returning only the index shape."""
    return {
        "id": row["id"],
        "name": row["name"],
        "pages": row.get("pages"),
        "tags": row.get("tags", []),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("")
async def list_pdfs_route(user: User = Depends(get_current_user)):
    """List the current user's PDFs (index only, no bytes)."""
    files = await list_pdfs(_config, user.id)
    return {"files": files, "count": len(files)}


@router.post("/import")
async def import_pdf_route(
    file: UploadFile = File(...),
    id: str | None = Form(default=None),
    user: User = Depends(get_current_user),
):
    """Import a PDF upload as a new encrypted file.

    Accepts an optional client-minted ``id`` (UUID form field) for offline-first
    replay: a second import with the same id returns the existing file
    (idempotent), never a duplicate.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if not ops.is_pdf(data):
        raise HTTPException(status_code=400, detail="File is not a PDF")
    client_id = _validate_client_id(id)
    stem = (file.filename or "document.pdf").rsplit("/", 1)[-1] or "document.pdf"
    row = await create_pdf(_config, user.id, stem, data, pdf_id=client_id)
    return {"file": _meta(row)}


@router.get("/changes")
async def pdf_changes_route(
    user: User = Depends(get_current_user),
    since: str | None = Query(
        default=None,
        description=(
            "ISO-8601 / server timestamp. Only PDFs changed after this are "
            "returned (live metadata + tombstones, no bytes). Omit for a full "
            "sync. Use the `now` field from the previous response as `since`."
        ),
    ),
):
    """Delta feed for offline-first clients (metadata only, never the bytes).

    Returns ``{files: [...live...], deleted: [...ids...], now: "<iso>"}``.
    The mobile client fetches each PDF's bytes lazily via ``/api/pdf/{id}/raw``.
    """
    return await get_pdf_changes(_config, user.id, since=since)


@router.get("/{pdf_id}")
async def get_pdf_route(
    pdf_id: str,
    user: User = Depends(get_current_user),
):
    """Fetch a PDF's metadata (no bytes)."""
    row = await get_pdf(_config, user.id, pdf_id)
    if not row:
        raise HTTPException(status_code=404, detail="PDF not found")
    return _meta(row)


@router.patch("/{pdf_id}")
async def patch_pdf_route(
    pdf_id: str,
    body: PatchPdfBody,
    user: User = Depends(get_current_user),
):
    """Update a PDF's name and/or tags without touching its payload."""
    row = await update_pdf_meta(_config, user.id, pdf_id, name=body.name, tags=body.tags)
    if not row:
        raise HTTPException(status_code=404, detail="PDF not found")
    return {"file": _meta(row)}


@router.get("/{pdf_id}/raw")
async def get_pdf_raw_route(
    pdf_id: str,
    user: User = Depends(get_current_user),
):
    """Stream the raw PDF bytes inline (for the embedded viewer)."""
    row = await get_pdf(_config, user.id, pdf_id)
    if not row:
        raise HTTPException(status_code=404, detail="PDF not found")
    fname = _safe_filename(row["name"])
    return Response(
        content=row["bytes"],
        media_type=_PDF_MEDIA,
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


class DownloadBody(BaseModel):
    # Optional — when set, the PDF is wrapped in an AES-256 encrypted .zip.
    password: str | None = Field(default=None, max_length=256)


@router.get("/{pdf_id}/download")
async def download_pdf_route(
    pdf_id: str,
    user: User = Depends(get_current_user),
):
    """Download the PDF as an attachment."""
    row = await get_pdf(_config, user.id, pdf_id)
    if not row:
        raise HTTPException(status_code=404, detail="PDF not found")
    fname = _safe_filename(row["name"])
    return Response(
        content=row["bytes"],
        media_type=_PDF_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/{pdf_id}/download")
async def download_pdf_post_route(
    pdf_id: str,
    body: DownloadBody,
    user: User = Depends(get_current_user),
):
    """Download the PDF, optionally AES-256 encrypted in a password ``.zip``.

    Password travels in the body (never a query string). Empty/absent → plain.
    """
    row = await get_pdf(_config, user.id, pdf_id)
    if not row:
        raise HTTPException(status_code=404, detail="PDF not found")
    full = _safe_filename(row["name"])
    base = full[:-4] if full.lower().endswith(".pdf") else full
    data, fname, media = protect_export(
        row["bytes"], base, "pdf", _PDF_MEDIA, body.password
    )
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/{pdf_id}/extract")
async def extract_pdf_route(
    pdf_id: str,
    user: User = Depends(get_current_user),
):
    """Extract the PDF's text (for preview / agent reasoning)."""
    row = await get_pdf(_config, user.id, pdf_id)
    if not row:
        raise HTTPException(status_code=404, detail="PDF not found")
    try:
        text = ops.extract_text(row["bytes"])
    except ops.PdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    pages = row.get("pages")
    if pages is None:
        try:
            pages = ops.page_count(row["bytes"])
        except ops.PdfError:
            pages = 0
    return {"text": text, "pages": pages}


@router.post("/{pdf_id}/ai")
async def ai_edit_pdf_route(
    pdf_id: str,
    body: AiEditBody,
    user: User = Depends(get_current_user),
):
    """Edit the open PDF from a natural-language instruction (✨ AI box).

    Single-output ops (sign/fill/rotate/merge) edit the open PDF IN PLACE —
    ``new_pdf_id`` comes back equal to ``pdf_id`` so the viewer just reloads
    (the prior bytes are stashed as a recoverable version). ``generate`` /
    ``split`` create NEW files, so ``new_pdf_id`` is a fresh id for the viewer
    to switch to. PDFs can't be reflow text-edited.
    """
    result = await ai_edit_document(_config, user.id, "pdf", pdf_id, body.instruction)
    return {
        "ok": result.ok,
        "summary": result.summary,
        "new_pdf_id": result.new_id,
        "error": result.error,
    }


@router.get("/{pdf_id}/versions")
async def list_pdf_versions_route(
    pdf_id: str,
    user: User = Depends(get_current_user),
):
    """List the recoverable pre-edit snapshots for a PDF (metadata, no bytes).

    Each entry's ``id`` fetches bytes via ``/api/pdf/{id}/raw`` (preview) and
    restores via ``POST /api/pdf/{pdf_id}/versions/{version_id}/restore``.
    """
    row = await get_pdf(_config, user.id, pdf_id)
    if not row:
        raise HTTPException(status_code=404, detail="PDF not found")
    versions = await list_pdf_versions(_config, user.id, pdf_id)
    return {"versions": versions, "count": len(versions)}


@router.post("/{pdf_id}/versions/{version_id}/restore")
async def restore_pdf_version_route(
    pdf_id: str,
    version_id: str,
    user: User = Depends(get_current_user),
):
    """Restore an archived snapshot back into the live PDF (itself undoable).

    ``pdf_id`` is the live parent (for a clean REST path); the restore is keyed
    on ``version_id`` and refuses a version that doesn't belong to this user OR
    to the ``pdf_id`` in the path.
    """
    row = await restore_pdf_version(
        _config, user.id, version_id, expected_parent=pdf_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"file": _meta(row)}


@router.delete("/{pdf_id}")
async def delete_pdf_route(
    pdf_id: str,
    user: User = Depends(get_current_user),
):
    """Delete a PDF entirely."""
    ok = await delete_pdf(_config, user.id, pdf_id)
    if not ok:
        raise HTTPException(status_code=404, detail="PDF not found")
    return {"status": "deleted", "id": pdf_id}
