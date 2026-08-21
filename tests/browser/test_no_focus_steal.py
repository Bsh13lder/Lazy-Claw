"""Automation must never steal the user's screen focus (2026-08-21).

The user works in the same Brave the agents drive. Watchers already pass
``focus=False`` / ``background=True`` everywhere, but the AGENT paths
leaned on the focusing DEFAULTS: every ``browser open`` that reused a
tab called ``switch_tab(tab_id)`` (default focus=True → Page.bringToFront
/ Target.activateTarget) and the open-fallback created its tab in the
foreground — so the user's active tab was yanked away mid-typing.

Policy: foregrounding is OPT-IN. Both backends default to
``focus=False`` / ``background=True``; the only flow that legitimately
foregrounds is the OAuth login tab (the user must interact with it),
which now asks for it explicitly.
"""

from __future__ import annotations

import inspect
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _default(fn, param):
    return inspect.signature(fn).parameters[param].default


def test_cdp_switch_tab_defaults_to_no_focus() -> None:
    from lazyclaw.browser.cdp_backend import CDPBackend

    assert _default(CDPBackend.switch_tab, "focus") is False


def test_cdp_new_tab_defaults_to_background() -> None:
    from lazyclaw.browser.cdp_backend import CDPBackend

    assert _default(CDPBackend.new_tab, "background") is True


def test_browser_use_backend_mirrors_the_policy() -> None:
    from lazyclaw.browser.browser_use_backend import BrowserUseBackend

    assert _default(BrowserUseBackend.switch_tab, "focus") is False
    assert _default(BrowserUseBackend.new_tab, "background") is True


def test_oauth_login_tab_opts_into_the_foreground() -> None:
    """The ONE legitimate foreground flow: the user must see and touch
    the OAuth consent page. It must ask explicitly now that the default
    is background."""
    src = (_REPO / "lazyclaw" / "mcp" / "oauth.py").read_text(encoding="utf-8")
    assert "new_tab(url, background=False)" in src


def test_no_caller_relies_on_a_focusing_default() -> None:
    """Grep guard: no switch_tab/new_tab CALL passes focus=True /
    background=False except the allowlisted user-facing flows. A new
    foregrounding call site must be added here deliberately."""
    import re

    call_re = re.compile(
        r"\.(?:switch_tab|new_tab)\([^)]*(?:focus=True|background=False)",
    )
    allowed_foreground = {
        "lazyclaw/mcp/oauth.py",  # OAuth consent tab — user must interact
    }
    skip_dirs = {".venv", "_vendor", "__pycache__"}
    hits = []
    for py in (_REPO / "lazyclaw").rglob("*.py"):
        if skip_dirs & set(py.parts):
            continue
        rel = str(py.relative_to(_REPO))
        if rel in allowed_foreground:
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            if call_re.search(line):
                hits.append((rel, line.strip()))
    assert not hits, f"unexpected foregrounding call sites: {hits}"
