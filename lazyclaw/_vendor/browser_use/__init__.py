"""Vendored subset of browser-use (MIT) — see _vendor/README.md.

PATCHED: the upstream ``__init__.py`` runs ``setup_logging()`` (which
reconfigures global logging handlers) and lazily exposes the Agent/LLM
stack. This replacement exports only what the vendored subset needs —
``logger`` (used by ``actor/page.py``) — and never touches the host
application's logging configuration.
"""

import logging

logger = logging.getLogger("browser_use")

__version__ = "0.13.7"
