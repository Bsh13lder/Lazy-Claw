"""Vendored third-party packages (see README.md for provenance).

``browser_use`` here uses absolute internal imports (``from browser_use.dom
import ...``), so the vendor directory must be importable as a top-level
package root. ``ensure_vendor_path()`` prepends this directory to
``sys.path`` — call it before importing any vendored package. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

_VENDOR_DIR = str(Path(__file__).parent)


def ensure_vendor_path() -> None:
    """Make vendored packages importable by their canonical names.

    Prepended (not appended) so the vendored copy deterministically wins
    over any same-named package that might exist in the environment.
    """
    if _VENDOR_DIR not in sys.path:
        sys.path.insert(0, _VENDOR_DIR)
