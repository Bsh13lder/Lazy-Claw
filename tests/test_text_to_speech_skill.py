"""Tests for the MiniMax text-to-speech skill (mocked — no key/network)."""

from __future__ import annotations

import asyncio

import httpx

from lazyclaw.skills.builtin import text_to_speech as tts


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.content = content
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, post_resp=None, get_resp=None):
        self._post_resp = post_resp
        self._get_resp = get_resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        return self._post_resp

    async def get(self, *a, **k):
        return self._get_resp


def _skill():
    return tts.TextToSpeechSkill(config=None)


# ── validation (pure, no network) ─────────────────────────────────────────

def test_validate_rejects_empty_text():
    clean, err = _skill()._validate({"text": "   "})
    assert clean is None and "text" in err.lower()


def test_validate_rejects_oversized_text():
    clean, err = _skill()._validate({"text": "x" * (tts._MAX_TEXT_CHARS + 1)})
    assert clean is None and "caps at" in err


def test_validate_rejects_bad_speed():
    clean, err = _skill()._validate({"text": "hi", "speed": 9})
    assert clean is None and "speed must be between" in err


def test_validate_rejects_non_numeric_speed():
    clean, err = _skill()._validate({"text": "hi", "speed": "fast"})
    assert clean is None and "speed must be a number" in err


def test_validate_rejects_bad_model():
    clean, err = _skill()._validate({"text": "hi", "model": "speech-9.9"})
    assert clean is None and "not supported" in err


def test_validate_accepts_and_defaults():
    clean, err = _skill()._validate({"text": "hello"})
    assert err is None
    assert clean["voice_id"] == tts._DEFAULT_VOICE_ID
    assert clean["model"] == tts._DEFAULT_MODEL
    assert clean["speed"] == tts._DEFAULT_SPEED


# ── execute (mocked httpx) ────────────────────────────────────────────────

def test_execute_missing_key(monkeypatch):
    monkeypatch.delenv(tts._API_KEY_ENV, raising=False)
    out = asyncio.run(_skill().execute("u1", {"text": "hi"}))
    assert "not configured" in out and tts._API_KEY_ENV in out


def test_execute_base_resp_error(monkeypatch):
    monkeypatch.setenv(tts._API_KEY_ENV, "test-key")
    bad = _FakeResp(json_data={"base_resp": {"status_code": 2049, "status_msg": "invalid key"}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(post_resp=bad))
    out = asyncio.run(_skill().execute("u1", {"text": "hi"}))
    assert "2049" in out and "invalid key" in out.lower()


def test_execute_happy_path_url_audio(monkeypatch):
    monkeypatch.setenv(tts._API_KEY_ENV, "test-key")
    post_ok = _FakeResp(json_data={
        "base_resp": {"status_code": 0, "status_msg": ""},
        "data": {"audio": "https://audio.example/x.mp3"},
    })
    get_ok = _FakeResp(content=b"ID3-mp3-bytes")
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: _FakeClient(post_resp=post_ok, get_resp=get_ok)
    )

    delivered = {}

    async def _fake_push(config, content, filename, *, caption=None):
        delivered["filename"] = filename
        delivered["bytes"] = len(content)
        return True

    monkeypatch.setattr(
        "lazyclaw.notifications.push.push_telegram_document", _fake_push
    )
    out = asyncio.run(_skill().execute("u1", {"text": "hello world"}))
    assert "Telegram" in out
    assert delivered["filename"] == "speech.mp3"
    assert delivered["bytes"] == len(b"ID3-mp3-bytes")


def test_execute_happy_path_hex_audio(monkeypatch):
    monkeypatch.setenv(tts._API_KEY_ENV, "test-key")
    hex_audio = b"raw-audio".hex()
    post_ok = _FakeResp(json_data={
        "base_resp": {"status_code": 0, "status_msg": ""},
        "data": {"audio": hex_audio},
    })
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(post_resp=post_ok))

    captured = {}

    async def _fake_push(config, content, filename, *, caption=None):
        captured["bytes"] = content
        return True

    monkeypatch.setattr(
        "lazyclaw.notifications.push.push_telegram_document", _fake_push
    )
    out = asyncio.run(_skill().execute("u1", {"text": "hi"}))
    assert "Telegram" in out
    assert captured["bytes"] == b"raw-audio"
