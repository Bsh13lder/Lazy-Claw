"""Password-protect an exported document as an AES-256 encrypted ZIP.

The Documents suite can export Sheets/Docs/PDF. When the user supplies a
password, we wrap the rendered file (xlsx/csv/docx/pdf) in a single AES-256
(WinZip AE-2) encrypted ``.zip`` whose only entry is the real file. This is the
one uniform scheme that works for every format (native OOXML encryption isn't
available in pure Python).

``pyzipper`` (MIT) provides AES-256 — NOT the legacy/weak ZipCrypto. Permissive,
pure-Python; no external binary. This module is pure bytes→bytes shaping (no I/O,
no user-data access).
"""

from __future__ import annotations

import io

import pyzipper

ZIP_MEDIA = "application/zip"


def aes_zip_bytes(content: bytes, inner_filename: str, password: str) -> bytes:
    """Return AES-256 encrypted ``.zip`` bytes containing one entry.

    ``inner_filename`` is the name of the sole file inside the archive;
    ``password`` is required (raises ``ValueError`` if empty).
    """
    if not password:
        raise ValueError("password required for encrypted zip")
    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr(inner_filename, content)
    return buf.getvalue()


def protect_export(
    content: bytes,
    base_name: str,
    ext: str,
    media: str,
    password: str | None,
) -> tuple[bytes, str, str]:
    """Return ``(bytes, filename, media_type)`` for an export download.

    With a non-empty ``password``, wraps ``content`` in an AES-256 zip whose
    single entry is ``<base_name>.<ext>`` and returns the ``.zip`` (filename
    ``<base_name>.zip``, media ``application/zip``). Otherwise returns the
    content unchanged with its native filename and ``media`` type.
    """
    safe = (base_name or "document").strip() or "document"
    if password:
        inner = f"{safe}.{ext}"
        return aes_zip_bytes(content, inner, password), f"{safe}.zip", ZIP_MEDIA
    return content, f"{safe}.{ext}", media
