"""Android device control over ADB (no root) — sibling of `computer/`.

Lets the agent see and operate a USB/Wi-Fi-connected Android phone. See
README.md in this package for the architecture and integration TODOs.
"""
from __future__ import annotations

from lazyclaw.android.executor import AndroidExecutor
from lazyclaw.android.security import AndroidSecurity

__all__ = ["AndroidExecutor", "AndroidSecurity"]
