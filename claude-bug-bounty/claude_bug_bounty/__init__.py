"""LazyClaw fork of shuvonsec/claude-bug-bounty (MIT).

This shim package exposes the upstream `brain.py` + `agent.py` modules as a
proper Python package so LazyClaw can `import claude_bug_bounty` after
`pip install -e claude-bug-bounty/`. Upstream code lives at the repo root
(brain.py, agent.py, tools/, memory/, agents/) and is imported here without
modification — adding the parent dir to sys.path is the smallest change that
preserves the upstream layout and keeps `git pull` upgrades clean.
"""

from __future__ import annotations

import os
import sys

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_PKG_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Re-exports — keep the public surface focused on what LazyClaw actually
# orchestrates. Anything else stays accessible via `import brain`,
# `from tools.scope_checker import ScopeChecker`, etc.
from brain import Brain, LLMClient, BRAIN_SYSTEM, MODEL_PRIORITY  # noqa: E402

# Safety primitives we reuse verbatim from upstream — never reimplement.
from tools.scope_checker import ScopeChecker  # noqa: E402
from memory.audit_log import AutopilotGuard, RateLimiter, SafeMethodPolicy  # noqa: E402

__all__ = [
    "Brain",
    "LLMClient",
    "BRAIN_SYSTEM",
    "MODEL_PRIORITY",
    "ScopeChecker",
    "AutopilotGuard",
    "RateLimiter",
    "SafeMethodPolicy",
]

__version__ = "0.4.0+lazyclaw"
