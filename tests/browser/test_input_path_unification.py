"""Input-path unification — every click/keystroke goes through human_input.

Before this suite the browser stack had THREE parallel input
implementations and only ONE of them reached the anti-detect layer
(``lazyclaw.browser.human_input``: Bezier mouse paths + per-domain
cadence from ``lazyclaw.browser.cadence``):

  (a) ref-ID path  — ``browser_actions/interact.py`` → injected JS
      ``el.click()`` and a raw per-char ``Input.dispatchKeyEvent`` loop.
  (b) CSS path     — ``cdp_backend.click/type_text`` → human_click/human_type
      (the correct reference implementation).
  (c) chain path   — ``browser_actions/navigation.py::action_chain`` →
      its own inline copy of the raw keystroke loop.

These tests pin (a) and (c) onto the same shared helper so a future edit
can't quietly re-introduce a bypass:

  * ref clicks resolve viewport coords and await ``human_click``;
  * the injected JS click survives ONLY as an explicit fallback
    (unresolvable coords, off-screen, invisible, or iframe refs whose
    ``getBoundingClientRect`` is frame-relative);
  * ref typing and chain typing both await ``human_type``;
  * cadence flows from ``backend._active_cadence()`` and degrades to
    ``None`` (human_input's DEFAULT) on backends that lack it.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from lazyclaw.browser.cadence import DEFAULT as DEFAULT_CADENCE, CadenceProfile
from lazyclaw.browser.snapshot import SnapshotManager
from lazyclaw.skills.builtin.browser_actions import human_actions


def _cadence() -> CadenceProfile:
    """A distinct-but-equivalent profile so ``is`` identity checks are real."""
    return dataclasses.replace(DEFAULT_CADENCE)

# ── Fakes ────────────────────────────────────────────────────────────


class FakeConn:
    """Records every CDP call the input layer makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params or {}))
        return {}

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


class FakeEngineBackend:
    """Minimal backend exposing the ``window.__lazyclaw`` engine contract.

    ``evaluate`` answers the engine calls SnapshotManager makes; the rest
    of the surface is what human_actions needs (``_ensure_connected`` +
    ``_active_cadence``).
    """

    def __init__(
        self,
        box: dict | None = None,
        perform_click_ok: bool = True,
        cadence: CadenceProfile | None = None,
    ) -> None:
        self.conn = FakeConn()
        self._box = box
        self._perform_click_ok = perform_click_ok
        self._cadence = cadence if cadence is not None else _cadence()
        self.evaluated: list[str] = []
        self.perform_click_calls: list[str] = []

    async def evaluate(self, js: str):
        self.evaluated.append(js)
        if "typeof window.__lazyclaw" in js:
            return True
        if "performClick" in js:
            self.perform_click_calls.append(js)
            return self._perform_click_ok
        if "__lazyclaw.click" in js:
            return self._box
        if "getMeta" in js:
            return {"role": "button", "name": "Submit"}
        if "isDirty" in js:
            return False
        if "focus" in js:
            return True
        return None

    async def _ensure_connected(self) -> FakeConn:
        return self.conn

    def _active_cadence(self) -> CadenceProfile:
        return self._cadence


class CadencelessBackend:
    """Backend with NO ``_active_cadence`` (requirement 3 graceful path)."""

    def __init__(self, box: dict | None = None) -> None:
        self.conn = FakeConn()
        self._box = box

    async def evaluate(self, js: str):
        if "typeof window.__lazyclaw" in js:
            return True
        if "__lazyclaw.click" in js:
            return self._box
        return None

    async def _ensure_connected(self) -> FakeConn:
        return self.conn


class StubSnapshotMgr:
    """Snapshot manager stub for the call-site (action_*) tests."""

    def __init__(self, box=None, perform_click_ok: bool = True) -> None:
        self._box = box
        self._perform_click_ok = perform_click_ok
        self.perform_click_calls: list[str] = []
        self.focus_calls: list[str] = []

    async def resolve_ref_box(self, backend, ref_id: str):
        return self._box

    async def perform_click(self, backend, ref_id: str) -> bool:
        self.perform_click_calls.append(ref_id)
        return self._perform_click_ok

    async def focus_ref(self, backend, ref_id: str) -> bool:
        self.focus_calls.append(ref_id)
        return True

    async def get_ref_meta(self, backend, ref_id: str):
        return {"role": "button", "name": "Submit"}

    async def is_stale(self, backend) -> bool:
        return False

    async def take_snapshot(self, backend):
        return object()

    def format_snapshot(self, snapshot, task_hint=None) -> str:
        return "(snapshot)"


class StubVerifier:
    def verify(self, *args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("verifier should not run without before-state")


def _box(x=100.0, y=200.0, w=80.0, h=24.0, visible=True):
    return human_actions.RefBox(x=x, y=y, width=w, height=h, visible=visible)


class Spy:
    """Awaitable call recorder (replaces human_click / human_type)."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    @property
    def called(self) -> bool:
        return bool(self.calls)


@pytest.fixture
def spies(monkeypatch):
    click, type_ = Spy(), Spy()
    monkeypatch.setattr(human_actions, "human_click", click)
    monkeypatch.setattr(human_actions, "human_type", type_)
    return click, type_


# ── SnapshotManager.resolve_ref_box ──────────────────────────────────


class TestResolveRefBox:
    async def test_returns_center_and_size(self):
        backend = FakeEngineBackend(box={
            "x": 120.5, "y": 240.0, "width": 90.0, "height": 30.0,
            "visible": True, "vw": 1280, "vh": 800,
        })
        box = await SnapshotManager().resolve_ref_box(backend, "e5")
        assert box is not None
        assert (box.x, box.y) == (120.5, 240.0)
        assert (box.width, box.height) == (90.0, 30.0)
        assert box.visible is True
        # Fitts's-Law target = smaller box dimension.
        assert box.target_size == 30.0

    async def test_none_when_engine_returns_null(self):
        backend = FakeEngineBackend(box=None)
        assert await SnapshotManager().resolve_ref_box(backend, "e5") is None

    async def test_ref_id_is_sanitized_against_js_injection(self):
        backend = FakeEngineBackend(box={"x": 1, "y": 2, "vw": 100, "vh": 100})
        await SnapshotManager().resolve_ref_box(backend, "e5'); alert(1); //")
        joined = "\n".join(backend.evaluated)
        assert "alert(1)" not in joined


# ── human_click_ref ──────────────────────────────────────────────────


class TestHumanClickRef:
    async def test_routes_through_human_click_with_cadence(self, spies):
        click, _ = spies
        cadence = _cadence()
        backend = FakeEngineBackend(cadence=cadence)
        mgr = StubSnapshotMgr(box=_box(x=100.0, y=200.0, w=80.0, h=24.0))

        path = await human_actions.human_click_ref(backend, mgr, "e5")

        assert path == "human"
        assert click.called, "ref click must go through human_click (bezier)"
        args, kwargs = click.calls[0]
        assert args[0] is backend.conn
        assert (args[1], args[2]) == (100.0, 200.0)
        assert kwargs["target_size"] == 24.0
        assert kwargs["cadence"] is cadence
        assert not mgr.perform_click_calls, "JS click must not run on the happy path"

    async def test_falls_back_to_js_when_coords_unresolvable(self, spies):
        click, _ = spies
        backend = FakeEngineBackend()
        mgr = StubSnapshotMgr(box=None)

        path = await human_actions.human_click_ref(backend, mgr, "e5")

        assert path == "js"
        assert mgr.perform_click_calls == ["e5"]
        assert not click.called

    async def test_falls_back_to_js_for_invisible_element(self, spies):
        click, _ = spies
        mgr = StubSnapshotMgr(box=_box(visible=False))
        path = await human_actions.human_click_ref(FakeEngineBackend(), mgr, "e5")
        assert path == "js"
        assert not click.called

    async def test_falls_back_to_js_for_offscreen_coords(self, spies):
        click, _ = spies
        mgr = StubSnapshotMgr(box=_box(x=-40.0, y=200.0))
        path = await human_actions.human_click_ref(FakeEngineBackend(), mgr, "e5")
        assert path == "js"
        assert not click.called

    async def test_iframe_ref_keeps_js_click(self, spies):
        """iframe rects are FRAME-relative; CDP input is viewport-absolute."""
        click, _ = spies
        mgr = StubSnapshotMgr(box=_box())
        path = await human_actions.human_click_ref(FakeEngineBackend(), mgr, "f1_e3")
        assert path == "js"
        assert mgr.perform_click_calls == ["f1_e3"]
        assert not click.called

    async def test_returns_empty_when_element_is_gone(self, spies):
        mgr = StubSnapshotMgr(box=None, perform_click_ok=False)
        path = await human_actions.human_click_ref(FakeEngineBackend(), mgr, "e5")
        assert path == ""
        assert not path, "callers treat falsy as 'element gone'"

    async def test_logs_which_path_ran(self, spies, caplog):
        caplog.set_level("INFO", logger=human_actions.__name__)

        await human_actions.human_click_ref(
            FakeEngineBackend(), StubSnapshotMgr(box=_box()), "e5",
        )
        assert any("path=human" in r.getMessage() for r in caplog.records)

        caplog.clear()
        await human_actions.human_click_ref(
            FakeEngineBackend(), StubSnapshotMgr(box=None), "e5",
        )
        assert any("path=js" in r.getMessage() for r in caplog.records)


# ── human_type_text ──────────────────────────────────────────────────


class TestHumanTypeText:
    async def test_routes_through_human_type_with_cadence(self, spies):
        _, type_ = spies
        cadence = _cadence()
        backend = FakeEngineBackend(cadence=cadence)

        await human_actions.human_type_text(backend, "hello")

        assert type_.called, "typing must go through human_type"
        args, kwargs = type_.calls[0]
        assert args[0] is backend.conn
        assert args[1] == "hello"
        assert kwargs["cadence"] is cadence
        assert kwargs.get("field_x") is None
        assert backend.conn.calls == [], "no raw dispatchKeyEvent from the helper"

    async def test_forwards_field_coords_for_click_then_type(self, spies):
        _, type_ = spies
        backend = FakeEngineBackend()
        await human_actions.human_type_text(backend, "hi", field_x=10.0, field_y=20.0)
        _, kwargs = type_.calls[0]
        assert (kwargs["field_x"], kwargs["field_y"]) == (10.0, 20.0)

    async def test_backend_without_cadence_passes_none(self, spies):
        _, type_ = spies
        await human_actions.human_type_text(CadencelessBackend(), "hi")
        _, kwargs = type_.calls[0]
        assert kwargs["cadence"] is None, "human_input resolves its own DEFAULT"

    async def test_click_ref_without_cadence_passes_none(self, spies):
        click, _ = spies
        mgr = StubSnapshotMgr(box=_box())
        await human_actions.human_click_ref(CadencelessBackend(), mgr, "e5")
        _, kwargs = click.calls[0]
        assert kwargs["cadence"] is None


# ── Call sites: interact.action_click / action_type ──────────────────


class TestInteractCallSites:
    async def test_ref_click_uses_human_click(self, spies):
        from lazyclaw.skills.builtin.browser_actions import interact

        click, _ = spies
        backend = FakeEngineBackend()
        mgr = StubSnapshotMgr(box=_box())
        out = await interact.action_click(
            "u1", {"ref": "e5"}, backend, mgr, StubVerifier(),
        )
        assert click.called
        assert "Clicked" in out
        assert not mgr.perform_click_calls

    async def test_ref_click_stale_ref_still_reports_action_error(self, spies):
        from lazyclaw.skills.builtin.browser_actions import interact

        mgr = StubSnapshotMgr(box=None, perform_click_ok=False)
        out = await interact.action_click(
            "u1", {"ref": "e5"}, FakeEngineBackend(), mgr, StubVerifier(),
        )
        assert "not found" in out.lower()

    async def test_ref_type_uses_human_type(self, spies):
        from lazyclaw.skills.builtin.browser_actions import interact

        _, type_ = spies
        backend = FakeEngineBackend()
        mgr = StubSnapshotMgr(box=_box())
        out = await interact.action_type(
            "u1", {"ref": "e5", "text": "hello"}, backend, mgr, StubVerifier(),
        )
        assert mgr.focus_calls == ["e5"], "focus_ref must still run first"
        assert type_.called
        assert type_.calls[0][0][1] == "hello"
        assert "Typed" in out
        assert "Input.dispatchKeyEvent" not in backend.conn.methods()


# ── Call site: navigation.action_chain ───────────────────────────────


class TestChainCallSite:
    async def test_chain_click_and_type_use_human_input(self, spies):
        from lazyclaw.skills.builtin.browser_actions import navigation

        click, type_ = spies
        backend = FakeEngineBackend()
        mgr = StubSnapshotMgr(box=_box())
        out = await navigation.action_chain(
            "u1", {"steps": ["click e1", "type e2 hello world"]}, backend, mgr,
        )
        assert click.called, "chain ref-click must use human_click"
        assert type_.called, "chain type must use human_type"
        assert type_.calls[0][0][1] == "hello world"
        assert "Input.dispatchKeyEvent" not in backend.conn.methods()
        assert "2/2" in out

    async def test_chain_click_reports_failure_when_element_gone(self, spies):
        from lazyclaw.skills.builtin.browser_actions import navigation

        mgr = StubSnapshotMgr(box=None, perform_click_ok=False)
        out = await navigation.action_chain(
            "u1", {"steps": ["click e1"]}, FakeEngineBackend(), mgr,
        )
        assert "FAILED" in out


# ── Source-level regression guard ────────────────────────────────────


class TestNoParallelImplementations:
    @pytest.mark.parametrize("module_name", ["interact", "navigation"])
    def test_no_raw_keystroke_dispatch_in_action_modules(self, module_name):
        import importlib

        mod = importlib.import_module(
            f"lazyclaw.skills.builtin.browser_actions.{module_name}"
        )
        src = inspect.getsource(mod)
        assert "Input.dispatchKeyEvent" not in src, (
            f"{module_name}.py must not dispatch keystrokes directly — "
            "route through human_actions.human_type_text"
        )

    def test_template_replay_types_through_human_input(self):
        """path_compiler's role-based replay had a ZERO-delay char loop."""
        from lazyclaw.browser import path_compiler

        src = inspect.getsource(path_compiler._replay_type)
        assert "Input.dispatchKeyEvent" not in src
        assert "human_type(" in src
        assert "cadence_for(backend)" in src

    def test_cadence_for_is_the_single_resolver(self):
        """One rule for 'does this backend do cadence?' — in human_input."""
        from lazyclaw.browser import human_input

        assert human_input.cadence_for(object()) is None
        assert human_actions.cadence_for is human_input.cadence_for

        class Boom:
            def _active_cadence(self):
                raise RuntimeError("settings unavailable")

        assert human_input.cadence_for(Boom()) is None

    def test_no_raw_mouse_dispatch_in_interact(self):
        from lazyclaw.skills.builtin.browser_actions import interact

        src = inspect.getsource(interact)
        assert "Input.dispatchMouseEvent" not in src, (
            "interact.py must not dispatch mouse events directly — "
            "route through human_actions/human_input"
        )

    def test_helper_delegates_rather_than_reimplements(self):
        """The shared helper must call human_input, not copy it."""
        src = inspect.getsource(human_actions)
        assert "from lazyclaw.browser.human_input import" in src
        # No input dispatch of its own — only prose may name the CDP methods.
        assert '.send("Input.' not in src
        assert ".send('Input." not in src
