import json
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.gateway.routes.mobile import router, set_apk_dir


def _app():
    app = FastAPI()
    app.include_router(router)
    return app


def test_version_404_when_absent(tmp_path):
    set_apk_dir(tmp_path)
    client = TestClient(_app())
    assert client.get("/api/mobile/version").status_code == 404


def test_version_and_apk_served(tmp_path):
    set_apk_dir(tmp_path)
    (tmp_path / "app-release.apk").write_bytes(b"PK\x03\x04 fake apk")
    (tmp_path / "version.json").write_text(json.dumps(
        {"version": "0.1.0", "build": 1, "sha256": "abc", "built_at": "x"}))
    client = TestClient(_app())

    v = client.get("/api/mobile/version")
    assert v.status_code == 200
    assert v.json()["version"] == "0.1.0"

    a = client.get("/api/mobile/apk")
    assert a.status_code == 200
    assert a.headers["content-type"] == "application/vnd.android.package-archive"
    assert a.content.startswith(b"PK")


def test_keyboard_version_404_when_absent(tmp_path):
    set_apk_dir(tmp_path)
    client = TestClient(_app())
    assert client.get("/api/mobile/keyboard-version").status_code == 404


def test_keyboard_apk_404_when_absent(tmp_path):
    set_apk_dir(tmp_path)
    client = TestClient(_app())
    assert client.get("/api/mobile/keyboard-apk").status_code == 404


def test_keyboard_version_and_apk_served(tmp_path):
    set_apk_dir(tmp_path)
    (tmp_path / "keyboard.apk").write_bytes(b"PK\x03\x04 fake keyboard apk")
    (tmp_path / "keyboard-version.json").write_text(json.dumps(
        {"version": "3.8.7-lazyclaw1", "build": 3871, "sha256": "abc",
         "label": "AI Keyboard (offline)"}))
    client = TestClient(_app())

    v = client.get("/api/mobile/keyboard-version")
    assert v.status_code == 200
    assert v.json()["version"] == "3.8.7-lazyclaw1"
    assert v.json()["build"] == 3871

    a = client.get("/api/mobile/keyboard-apk")
    assert a.status_code == 200
    assert a.headers["content-type"] == "application/vnd.android.package-archive"
    assert a.content.startswith(b"PK")
