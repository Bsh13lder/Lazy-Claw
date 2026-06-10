"""Bridge: run the mcp-whatsapp Node test suite from pytest.

The WhatsApp MCP's media helpers (src/media.js) have a pure node:test suite
at mcp-whatsapp/tests/test_media.js. Surfacing it through pytest keeps one
test entrypoint for the whole repo — `pytest tests/` exercises the JS media
contract (describeMedia / extForMime / mimeForPath / buildSendPayload) that
the comms gateway and inbox media endpoint depend on.

Skips cleanly when node is not installed (e.g. a python-only CI shard).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MCP_DIR = _REPO_ROOT / "mcp-whatsapp"
_TEST_FILE = _MCP_DIR / "tests" / "test_media.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_media_js_syntax_clean():
    """`node --check` both JS entrypoints — catches syntax errors at PR time."""
    for src in ("src/index.js", "src/media.js"):
        proc = subprocess.run(
            ["node", "--check", str(_MCP_DIR / src)],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"{src} failed syntax check:\n{proc.stderr}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_media_js_suite_passes():
    """Run the node:test media suite; it must discover >0 tests and pass.

    The explicit file path matters: Node's test-runner directory discovery
    globs (`test-*`, `*[.-_]test`) do NOT match `tests/test_media.js`, so a
    bare `node --test tests/` would silently run zero tests and exit green.
    """
    proc = subprocess.run(
        ["node", "--test", str(_TEST_FILE)],
        capture_output=True, text=True, timeout=120,
        cwd=str(_MCP_DIR),
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"node test suite failed:\n{out}"
    # Guard against silent zero-discovery (the exact failure mode the
    # explicit path exists to prevent).
    assert "# pass 0" not in out, f"suite discovered zero tests:\n{out}"
