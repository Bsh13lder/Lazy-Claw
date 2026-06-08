"""Tests for AES-256 encrypted-zip export protection."""

from __future__ import annotations

import io

import pyzipper
import pytest

from lazyclaw.export_crypto import ZIP_MEDIA, aes_zip_bytes, protect_export


def _read_zip(data: bytes, password: str) -> bytes:
    with pyzipper.AESZipFile(io.BytesIO(data)) as zf:
        zf.setpassword(password.encode("utf-8"))
        name = zf.namelist()[0]
        return zf.read(name)


def test_aes_zip_roundtrips_with_password():
    blob = b"hello spreadsheet bytes"
    data = aes_zip_bytes(blob, "Budget.xlsx", "s3cret")
    assert data[:2] == b"PK"
    assert _read_zip(data, "s3cret") == blob


def test_aes_zip_inner_filename_preserved():
    data = aes_zip_bytes(b"x", "My Report.docx", "pw")
    with pyzipper.AESZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["My Report.docx"]


def test_wrong_password_fails():
    data = aes_zip_bytes(b"secret", "a.csv", "right")
    with pytest.raises(Exception):
        _read_zip(data, "wrong")


def test_empty_password_rejected():
    with pytest.raises(ValueError):
        aes_zip_bytes(b"x", "a.txt", "")


def test_protect_export_wraps_when_password():
    content = b"PDFDATA"
    out, fname, media = protect_export(
        content, "Q3 Report", "pdf", "application/pdf", "pw123"
    )
    assert fname == "Q3 Report.zip"
    assert media == ZIP_MEDIA
    assert _read_zip(out, "pw123") == content


def test_protect_export_passthrough_when_no_password():
    content = b"PLAINXLSX"
    out, fname, media = protect_export(
        content, "Budget", "xlsx", "application/x-xlsx", None
    )
    assert out == content
    assert fname == "Budget.xlsx"
    assert media == "application/x-xlsx"


def test_protect_export_empty_password_is_passthrough():
    out, fname, _ = protect_export(b"x", "Doc", "docx", "m", "")
    assert out == b"x" and fname == "Doc.docx"
