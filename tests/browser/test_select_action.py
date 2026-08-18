"""action='select' — native dropdown support (2026-08-18 himap Category).

Clicking a native <select> via CDP opens an OS-level menu the page cannot
see, so click-based attempts "failed verification" 3× and the stuck detector
killed the run one field short of Save. The fix is a first-class select
action: set the value in page context + fire input/change like a real pick.
"""

from __future__ import annotations

import asyncio

from lazyclaw.browser.snapshot import _FALLBACK_VERSION, _JS_INJECT_FALLBACK
from lazyclaw.skills.builtin.browser_actions.interact import action_select


class FakeSnapshotMgr:
    async def _ensure_engine(self, backend):
        pass

    async def get_ref_meta(self, backend, ref):
        return {"name": "Category"}


class FakeBackend:
    def __init__(self, result):
        self._result = result
        self.evaluated = []

    async def evaluate(self, js):
        self.evaluated.append(js)
        return self._result


def _run(result, params):
    backend = FakeBackend(result)

    async def go():
        import lazyclaw.skills.builtin.browser_actions.interact as mod
        orig = mod.get_backend

        async def fake_get_backend(user_id, tab_context):
            return backend

        mod.get_backend = fake_get_backend
        try:
            return await action_select("u1", params, None, FakeSnapshotMgr())
        finally:
            mod.get_backend = orig

    return asyncio.run(go()), backend


class TestActionSelect:
    def test_successful_selection(self):
        out, backend = _run(
            {"ok": True, "selected": "📖 Guides"},
            {"action": "select", "ref": "e22", "value": "Guides"},
        )
        assert 'Selected "📖 Guides"' in out
        assert '"Category"' in out
        assert "selectOption" in backend.evaluated[0]

    def test_value_alias_text(self):
        out, _ = _run(
            {"ok": True, "selected": "Guides"},
            {"action": "select", "ref": "e22", "text": "Guides"},
        )
        assert "Selected" in out

    def test_no_match_lists_available_options(self):
        out, _ = _run(
            {"ok": False, "err": 'no option matching "Gudes"',
             "options": ["📖 Guides", "📰 News"]},
            {"action": "select", "ref": "e22", "value": "Gudes"},
        )
        assert "no option matching" in out
        assert "📖 Guides" in out and "📰 News" in out

    def test_not_a_select_element(self):
        out, _ = _run(
            {"ok": False, "err": "not a <select> (tag=input)"},
            {"action": "select", "ref": "e5", "value": "x"},
        )
        assert "not a <select>" in out

    def test_missing_params(self):
        out, _ = _run({"ok": True}, {"action": "select", "ref": "e22"})
        assert "requires ref AND value" in out
        out2, _ = _run({"ok": True}, {"action": "select", "value": "x"})
        assert "requires ref AND value" in out2


class TestEngineContract:
    def test_engine_has_select_option_api(self):
        assert "selectOption(id, want)" in _JS_INJECT_FALLBACK
        assert "dispatchEvent(new Event('change',{bubbles:true}))" in _JS_INJECT_FALLBACK

    def test_version_bumped_for_new_api(self):
        assert _FALLBACK_VERSION >= 6
        assert f"EXPECTED_VERSION = {_FALLBACK_VERSION}" in _JS_INJECT_FALLBACK

    def test_skill_routes_select(self):
        import inspect
        from lazyclaw.skills.builtin import browser_skill
        src = inspect.getsource(browser_skill)
        assert 'elif action == "select":' in src
        assert '"select"' in src  # schema enum
