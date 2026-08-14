"""GET /api/health must report which build the container is running.

Deploy-drift incident class: images are baked from the working tree, so
"committed but not deployed" and "deployed but not committed" both looked
identical from the outside. The `build` object closes that gap.

Two hard requirements are asserted here:
  1. every pre-existing health field stays byte-identical (monitors, the
     Docker healthcheck and the mobile reachability probe all read this), and
  2. the stamp fails SOFT — a missing/corrupt BUILD_INFO.json must never turn
     the health endpoint itself into a failure.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lazyclaw.gateway.app import app
from lazyclaw.gateway.build_info import (
    get_build_info,
    load_build_info,
    reset_build_info_cache,
    unknown_build_info,
)

_BUILD_FIELDS = {"sha", "short_sha", "branch", "describe", "dirty", "built_at"}

_FULL_STAMP = {
    "sha": "27933995f486b3b7e8f877f2da541698ccb0f66f",
    "short_sha": "27933995f486",
    "branch": "main",
    "describe": "2793399-dirty",
    "dirty": True,
    "dirty_files": ["Dockerfile", "lazyclaw/gateway/app.py"],
    "built_at": "2026-08-14T12:58:04Z",
    "generator": "scripts/write-build-info.sh",
}


@pytest.fixture(autouse=True)
def _clean_stamp_cache():
    """The stamp is cached for the process lifetime — isolate every test."""
    reset_build_info_cache()
    yield
    reset_build_info_cache()


@pytest.fixture
def stamp(tmp_path, monkeypatch):
    """Point the loader at a test-controlled stamp path (may not exist)."""
    def _use(content: str | dict | None) -> None:
        path = tmp_path / "BUILD_INFO.json"
        if content is not None:
            path.write_text(
                content if isinstance(content, str) else json.dumps(content),
                encoding="utf-8",
            )
        monkeypatch.setenv("LAZYCLAW_BUILD_INFO_PATH", str(path))
        reset_build_info_cache()
    return _use


def _health(client: TestClient) -> dict:
    response = client.get("/api/health")
    assert response.status_code == 200
    return response.json()


def test_health_reports_the_baked_stamp(stamp):
    stamp(_FULL_STAMP)
    body = _health(TestClient(app))

    assert body["build"] == {
        "sha": "27933995f486b3b7e8f877f2da541698ccb0f66f",
        "short_sha": "27933995f486",
        "branch": "main",
        "describe": "2793399-dirty",
        "dirty": True,
        "built_at": "2026-08-14T12:58:04Z",
    }


def test_existing_health_fields_are_unchanged(stamp):
    """`build` is purely additive — nothing else may shift."""
    stamp(_FULL_STAMP)
    body = _health(TestClient(app))

    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert isinstance(body["started_at"], float)
    assert set(body) == {"status", "version", "started_at", "build"}


def test_health_fails_soft_when_stamp_missing(stamp):
    """No BUILD_INFO.json (plain `docker compose build`) → 200 + unknown."""
    stamp(None)
    body = _health(TestClient(app))

    assert body["status"] == "ok"
    assert body["build"] == unknown_build_info()
    assert body["build"]["sha"] == "unknown"
    # Unknown must NOT masquerade as "clean": that is the exact lie this
    # feature exists to prevent.
    assert body["build"]["dirty"] is None


def test_health_fails_soft_on_malformed_json(stamp):
    """A truncated stamp (interrupted build) must not 500 the endpoint."""
    stamp('{"sha": "abc123", "dirty": tru')
    body = _health(TestClient(app))

    assert body["status"] == "ok"
    assert body["build"] == unknown_build_info()


def test_health_fails_soft_on_non_object_stamp(stamp):
    stamp('["not", "an", "object"]')
    body = _health(TestClient(app))

    assert body["status"] == "ok"
    assert body["build"]["sha"] == "unknown"


def test_dockerfile_fallback_stub_is_accepted(stamp):
    """The Dockerfile writes a bare stub when the script did not run."""
    stamp({"sha": "unknown", "dirty": None, "built_at": "unknown"})
    body = _health(TestClient(app))

    assert body["build"]["sha"] == "unknown"
    assert body["build"]["dirty"] is None
    assert set(body["build"]) == _BUILD_FIELDS  # shape stays stable


def test_partial_stamp_fills_only_known_fields(stamp):
    stamp({"sha": "deadbeef", "dirty": False})
    body = _health(TestClient(app))

    assert body["build"]["sha"] == "deadbeef"
    assert body["build"]["dirty"] is False
    assert body["build"]["branch"] == "unknown"
    assert body["build"]["built_at"] == "unknown"


def test_dirty_flag_only_trusts_real_booleans(stamp):
    """A string "false" must read as unknown, never as a clean-tree claim."""
    stamp({"sha": "deadbeef", "dirty": "false"})
    assert load_build_info()["dirty"] is None


def test_forensic_fields_stay_off_the_public_endpoint(stamp):
    """`dirty_files` lives in the on-disk stamp, not on unauthenticated health."""
    stamp(_FULL_STAMP)
    body = _health(TestClient(app))

    assert "dirty_files" not in body["build"]
    assert "generator" not in body["build"]


def test_stamp_is_cached_after_first_read(stamp, tmp_path):
    """Read once at startup — health must not re-hit the disk per request."""
    stamp(_FULL_STAMP)
    client = TestClient(app)
    first = _health(client)["build"]

    (tmp_path / "BUILD_INFO.json").unlink()
    assert _health(client)["build"] == first


def test_caller_cannot_poison_the_cache(stamp):
    """get_build_info() hands out a copy, not the cached dict itself."""
    stamp(_FULL_STAMP)
    first = get_build_info()
    first["sha"] = "tampered"

    assert get_build_info()["sha"] == _FULL_STAMP["sha"]
