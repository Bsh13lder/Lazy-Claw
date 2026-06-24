"""Tests for the MiniMax image-generation skill (mocked — no key/network)."""

from __future__ import annotations

import asyncio

import httpx

from lazyclaw.skills.builtin import generate_image as gi


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.content = content
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    """Minimal async-context-manager stand-in for httpx.AsyncClient."""

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
    return gi.GenerateImageSkill(config=None)


# ── validation (pure, no network) ─────────────────────────────────────────

def test_validate_rejects_empty_prompt():
    clean, err = _skill()._validate({"prompt": "  "})
    assert clean is None and "prompt" in err.lower()


def test_validate_rejects_oversized_prompt():
    clean, err = _skill()._validate({"prompt": "x" * (gi._MAX_PROMPT_CHARS + 1)})
    assert clean is None and "maximum" in err.lower()


def test_validate_rejects_bad_aspect_ratio():
    clean, err = _skill()._validate({"prompt": "a cat", "aspect_ratio": "5:5"})
    assert clean is None and "aspect_ratio" in err


def test_validate_rejects_n_out_of_range():
    clean, err = _skill()._validate({"prompt": "a cat", "n": 99})
    assert clean is None and "n must be" in err


def test_validate_accepts_and_normalizes():
    clean, err = _skill()._validate({"prompt": "a cat", "aspect_ratio": "16:9", "n": 2})
    assert err is None
    assert clean == {"prompt": "a cat", "aspect_ratio": "16:9", "n": 2}


def test_validate_defaults():
    clean, _ = _skill()._validate({"prompt": "a dog"})
    assert clean["aspect_ratio"] == gi._DEFAULT_ASPECT_RATIO
    assert clean["n"] == gi._MIN_N


# ── execute (mocked httpx) ────────────────────────────────────────────────

def test_execute_missing_key(monkeypatch):
    monkeypatch.delenv(gi._API_KEY_ENV, raising=False)
    out = asyncio.run(_skill().execute("u1", {"prompt": "a cat"}))
    assert "not configured" in out and gi._API_KEY_ENV in out


def test_execute_base_resp_error(monkeypatch):
    monkeypatch.setenv(gi._API_KEY_ENV, "test-key")
    bad = _FakeResp(json_data={"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(post_resp=bad))
    out = asyncio.run(_skill().execute("u1", {"prompt": "a cat"}))
    assert "1008" in out and "insufficient balance" in out.lower()


def test_execute_happy_path_delivers(monkeypatch):
    monkeypatch.setenv(gi._API_KEY_ENV, "test-key")
    post_ok = _FakeResp(json_data={
        "base_resp": {"status_code": 0, "status_msg": ""},
        "data": {"image_urls": ["https://img.example/x.png"]},
    })
    get_ok = _FakeResp(content=b"\x89PNG-bytes")
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
    out = asyncio.run(_skill().execute("u1", {"prompt": "a cat"}))
    assert "Sent 1 image" in out
    assert delivered["filename"] == "image.png"
    assert delivered["bytes"] == len(b"\x89PNG-bytes")
