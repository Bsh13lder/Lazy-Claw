"""Security manager for Android (ADB) control.

Sibling of `computer/security.py`. Runs over `adb shell` as the unprivileged
`shell` user (uid 2000) — so the blast radius is already limited (no root, no
access to other apps' private data). The two real risks we guard against:

  1. Uninstalling a core system package and bricking / soft-bricking the phone.
  2. Destructive device commands (factory wipe, recovery/bootloader reboot, dd).

Validation is advisory at the executor boundary — same contract as
`SecurityManager.is_command_allowed`: return (allowed, reason).
"""
from __future__ import annotations

import re

# Packages that must NEVER be uninstalled — removing any of these can soft-brick
# the phone, kill notifications, or break the launcher/settings. Matched by exact
# name OR prefix (e.g. "com.android.providers." covers every provider).
PROTECTED_PACKAGES: tuple[str, ...] = (
    "android",
    "com.android.systemui",
    "com.android.settings",
    "com.android.phone",
    "com.android.providers.",          # telephony, contacts, settings, media…
    "com.android.permissioncontroller",
    "com.android.vending",             # Play Store
    "com.google.android.gms",          # Play Services — most apps break without it
    "com.google.android.gsf",          # Google Services Framework
    "com.google.android.permissioncontroller",
    "com.miui.core",
    "com.miui.system",
    "com.miui.securitycore",
    "com.miui.home",                   # launcher
    "com.miui.guardprovider",
    "com.lbe.security.miui",           # permission manager
    "com.miui.rom",
)

# Destructive shell verbs we refuse to pass through, even unrooted.
BLOCKED_SHELL_PATTERNS: tuple[str, ...] = (
    r"\breboot\s+(bootloader|recovery|fastboot)\b",
    r"\bfastboot\b",
    r"\bwipe\b",
    r"--wipe-data",
    r"\bmaster[_-]?clear\b",
    r"\brecovery\b.*--",
    r"\bdd\s+if=",
    r"\brm\s+-rf\s+/",
    r"\bsvc\s+power\s+(shutdown|reboot)\b",
    r"\bmkfs\b",
    r"\bformat\b",
)


class AndroidSecurity:
    """Immutable validator for ADB uninstall + shell operations."""

    def __init__(self) -> None:
        self._blocked_shell = tuple(
            re.compile(p, re.IGNORECASE) for p in BLOCKED_SHELL_PATTERNS
        )

    def is_uninstall_allowed(self, package: str) -> tuple[bool, str]:
        """Block uninstalling protected (core) packages. Returns (allowed, reason)."""
        pkg = (package or "").strip()
        if not pkg:
            return False, "No package specified"
        for protected in PROTECTED_PACKAGES:
            hit = pkg == protected or (
                protected.endswith(".") and pkg.startswith(protected)
            )
            if hit:
                return False, f"Blocked: {pkg} is a protected system package"
        return True, ""

    def is_shell_allowed(self, cmd: str) -> tuple[bool, str]:
        """Block destructive device commands. Returns (allowed, reason)."""
        text = cmd or ""
        for pattern in self._blocked_shell:
            if pattern.search(text):
                return False, "Blocked: command matches a destructive device pattern"
        return True, ""
