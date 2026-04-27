"""mcp-scraper vendoring + module-launch smoke test.

These tests run WITHOUT the runtime deps installed — they only check:
1. Vendored crawl4ai source has no remaining absolute `from crawl4ai.X` imports.
2. The mcp_scraper package + its `__main__.py` exist on disk and reference
   `mcp_scraper.server:main`.

Live behavior (FastMCP startup, tool count, network crawl) is left for a
post-rebuild manual check — it requires Playwright + crawl4ai's transitive
deps installed inside the lazyclaw container.
"""

from __future__ import annotations

from pathlib import Path

import re

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_ROOT = REPO_ROOT / "mcp-scraper" / "mcp_scraper"


def test_package_layout():
    assert PKG_ROOT.is_dir()
    assert (PKG_ROOT / "__init__.py").is_file()
    assert (PKG_ROOT / "__main__.py").is_file()
    assert (PKG_ROOT / "server.py").is_file()
    assert (PKG_ROOT / "_vendor" / "crawl4ai" / "__init__.py").is_file()
    assert (REPO_ROOT / "mcp-scraper" / "LICENSE").is_file()
    assert (REPO_ROOT / "mcp-scraper" / "LICENSE-CRAWL4AI").is_file()
    assert (REPO_ROOT / "mcp-scraper" / "NOTICE").is_file()
    assert (REPO_ROOT / "mcp-scraper" / "pyproject.toml").is_file()


def test_main_entry_calls_server_main():
    body = (PKG_ROOT / "__main__.py").read_text()
    assert "from mcp_scraper.server import main" in body
    assert "main()" in body


def test_no_residual_absolute_crawl4ai_imports():
    """The sed pass should have rewritten every `from crawl4ai.X` to
    `from mcp_scraper._vendor.crawl4ai.X`. If a future upgrade adds a new
    file with absolute imports, this test catches it.
    """
    pat = re.compile(r"^\s*(from|import)\s+crawl4ai(\.|_mcp|\b)", re.M)
    bad: list[str] = []
    for path in PKG_ROOT.rglob("*.py"):
        if pat.search(path.read_text(errors="ignore")):
            bad.append(str(path.relative_to(REPO_ROOT)))
    assert not bad, f"absolute crawl4ai imports still present in:\n  " + "\n  ".join(bad)


def test_bundled_mcp_registry_includes_scraper():
    """LazyClaw's MCP manager must register mcp-scraper so the bridge spawns it."""
    from lazyclaw.mcp.manager import BUNDLED_MCPS

    assert "mcp-scraper" in BUNDLED_MCPS
    cfg = BUNDLED_MCPS["mcp-scraper"]
    assert cfg["module"] == "mcp_scraper"
    assert cfg.get("optional") is True


def test_explore_prompt_mentions_scraper():
    from lazyclaw.runtime.dispatcher import _EXPLORE_SYSTEM_PROMPT

    # Prompt was tightened on 2026-04-26 to refer to scraper tools by suffix
    # (because the bridge prefixes them with `mcp_<uuid>_`). Match on suffix.
    assert "_extract_entities" in _EXPLORE_SYSTEM_PROMPT
    assert "_crawl_url" in _EXPLORE_SYSTEM_PROMPT
    # Browser must be demoted to last resort, after scraper
    pos_scraper = _EXPLORE_SYSTEM_PROMPT.index("_extract_entities")
    pos_last_resort = _EXPLORE_SYSTEM_PROMPT.index("LAST RESORT")
    assert pos_scraper < pos_last_resort
