"""Unit tests for the new Drive delete/trash/list ops in google_direct.

We stub `_build` so tests never touch real Google APIs — they only assert
the correct calls flow through `files().list()`, `files().update()`, and
`files().delete()` with the expected body shapes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lazyclaw.skills.builtin import google_direct


class _FakeFiles:
    """Captures the Drive `files()` call chain so tests can assert shapes."""

    def __init__(self) -> None:
        self.last_call: dict | None = None

    def list(self, **kwargs):  # noqa: A003 — mirrors google API
        self.last_call = {"op": "list", "kwargs": kwargs}
        resp = MagicMock()
        resp.execute.return_value = {
            "files": [
                {"id": "fid1", "name": "Sheet A",
                 "mimeType": "application/vnd.google-apps.spreadsheet",
                 "webViewLink": "https://x", "modifiedTime": "2026-04-24T10:00:00Z"},
                {"id": "fid2", "name": "Sheet B",
                 "mimeType": "application/vnd.google-apps.spreadsheet",
                 "webViewLink": "https://y", "modifiedTime": "2026-04-24T11:00:00Z"},
            ]
        }
        return resp

    def update(self, **kwargs):
        self.last_call = {"op": "update", "kwargs": kwargs}
        resp = MagicMock()
        resp.execute.return_value = {
            "id": kwargs.get("fileId"), "name": "Sheet A", "trashed": True,
        }
        return resp

    def delete(self, **kwargs):
        self.last_call = {"op": "delete", "kwargs": kwargs}
        resp = MagicMock()
        resp.execute.return_value = ""
        return resp


@pytest.fixture
def fake_drive(monkeypatch):
    fake_files = _FakeFiles()
    svc = MagicMock()
    svc.files.return_value = fake_files

    def _fake_build(api, version, user_email):  # noqa: ARG001
        assert api == "drive" and version == "v3"
        return svc

    monkeypatch.setattr(google_direct, "_build", _fake_build)
    return fake_files


def test_list_drive_items_filters_by_mime_type(fake_drive):
    out = google_direct.list_drive_items(
        "u@example.com",
        mime_type="application/vnd.google-apps.spreadsheet",
    )
    assert out["resource_type"] == "google_drive_item_list"
    assert out["count"] == 2
    assert fake_drive.last_call["op"] == "list"
    q = fake_drive.last_call["kwargs"]["q"]
    assert "trashed = false" in q
    assert "application/vnd.google-apps.spreadsheet" in q


def test_list_drive_items_substring_name_query(fake_drive):
    google_direct.list_drive_items("u@example.com", query="budget")
    q = fake_drive.last_call["kwargs"]["q"]
    assert "name contains 'budget'" in q


def test_trash_drive_item_sends_trashed_true(fake_drive):
    out = google_direct.trash_drive_item("u@example.com", file_id="fidX")
    assert fake_drive.last_call["op"] == "update"
    assert fake_drive.last_call["kwargs"]["fileId"] == "fidX"
    assert fake_drive.last_call["kwargs"]["body"] == {"trashed": True}
    assert out["resource_type"] == "google_drive_trash"
    assert out["trashed"] is True


def test_trash_drive_item_requires_file_id(fake_drive):
    with pytest.raises(ValueError):
        google_direct.trash_drive_item("u@example.com", file_id="")


def test_delete_drive_item_without_confirm_downgrades_to_trash(fake_drive):
    out = google_direct.delete_drive_item(
        "u@example.com", file_id="fidY", confirm=False,
    )
    # Should have hit update (trash), not delete.
    assert fake_drive.last_call["op"] == "update"
    assert out["resource_type"] == "google_drive_trash"
    assert "confirm: true" in (out.get("note") or "")


def test_delete_drive_item_with_confirm_calls_delete(fake_drive):
    out = google_direct.delete_drive_item(
        "u@example.com", file_id="fidZ", confirm=True,
    )
    assert fake_drive.last_call["op"] == "delete"
    assert fake_drive.last_call["kwargs"]["fileId"] == "fidZ"
    assert out["resource_type"] == "google_drive_delete"
    assert out["deleted"] is True


@pytest.mark.asyncio
async def test_run_task_trash_drive_item_accepts_id_alias(fake_drive, monkeypatch):
    # Bypass _default_email requirement.
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "u@example.com")
    out = await google_direct.run_task(
        task_type="trash_drive_item",
        task={"id": "aliased-id"},  # 'id' alias for file_id
    )
    assert fake_drive.last_call["kwargs"]["fileId"] == "aliased-id"
    assert out["trashed"] is True


@pytest.mark.asyncio
async def test_run_task_rejects_unknown_type(monkeypatch):
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "u@example.com")
    with pytest.raises(ValueError, match="Unknown google_direct task_type"):
        await google_direct.run_task(
            task_type="obliterate_universe", task={},
        )


def test_tasks_dispatch_exposes_new_ops():
    for name in ("list_drive_items", "trash_drive_item", "delete_drive_item"):
        assert name in google_direct._TASKS, f"missing dispatch for {name}"
