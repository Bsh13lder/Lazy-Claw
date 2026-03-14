"""Security manager for LazyClaw Computer Connector.

Provides command blocklist, path blocklist, and interactive approval prompts.
Standalone version — includes prompt_approval() for the desktop connector.
"""
import os
import re
import sys
import platform


class SecurityManager:
    """Sandboxing and approval for connector commands."""

    BLOCKED_COMMANDS = [
        r'\brm\s+(-\w*\s+)*-rf\s+(/|/\*|~)',
        r'\bmkfs\b',
        r'\bformat\b\s+\w:',
        r'\bfdisk\b',
        r'\bshutdown\b',
        r'\breboot\b',
        r'\bhalt\b',
        r'\bpoweroff\b',
        r'\bdd\s+if=',
        r':\(\)\{\s*:\|:&\s*\};:',  # fork bomb
        r'\bchmod\s+(-\w*\s+)*-R\s+777\s+/',
        r'\bchown\s+(-\w*\s+)*-R\s+',
        r'>\s*/dev/(sd|disk|nvme)',
        r'\|\s*(curl|wget)\b.*\|\s*(sh|bash|zsh)',
        r'\bcurl\b.*\|\s*(sh|bash|zsh)',
        r'\bwget\b.*\|\s*(sh|bash|zsh)',
    ]

    BLOCKED_WRITE_PATHS = [
        '/etc/shadow',
        '/etc/passwd',
        '/etc/sudoers',
        '/boot/',
        '/sys/',
        '/proc/',
    ]

    BLOCKED_READ_PATHS = [
        '/etc/shadow',
    ]

    BLOCKED_HOME_PATTERNS = [
        '.ssh/id_*',
        '.ssh/authorized_keys',
        '.gnupg/',
        '.aws/credentials',
        '.env',
    ]

    def __init__(self, require_approval: bool = True):
        self.require_approval = require_approval
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.BLOCKED_COMMANDS
        ]
        self._home = os.path.expanduser('~')

    def is_command_allowed(self, cmd: str) -> tuple[bool, str]:
        """Check if a shell command is allowed.

        Returns (allowed, reason).
        """
        for pattern in self._compiled_patterns:
            if pattern.search(cmd):
                return False, "Blocked: command matches dangerous pattern"
        return True, ""

    def is_path_allowed(self, path: str, write: bool = False) -> tuple[bool, str]:
        """Check if a file path is allowed for read/write.

        Returns (allowed, reason).
        """
        abs_path = os.path.abspath(os.path.expanduser(path))

        blocked = self.BLOCKED_WRITE_PATHS if write else self.BLOCKED_READ_PATHS
        for blocked_path in blocked:
            if abs_path.startswith(blocked_path) or abs_path == blocked_path.rstrip('/'):
                return False, f"Blocked: access to {blocked_path} is not allowed"

        if abs_path.startswith(self._home):
            rel = abs_path[len(self._home):].lstrip('/')
            for pattern in self.BLOCKED_HOME_PATTERNS:
                if pattern.endswith('/'):
                    if rel.startswith(pattern) or rel + '/' == pattern:
                        return False, f"Blocked: access to ~/{pattern} is not allowed"
                elif '*' in pattern:
                    prefix = pattern.split('*')[0]
                    if rel.startswith(prefix):
                        if write or not rel.endswith('.pub'):
                            return False, f"Blocked: access to ~/{pattern} is not allowed"
                elif rel == pattern:
                    return False, f"Blocked: access to ~/{pattern} is not allowed"

        if write:
            system_dirs = ['/usr/', '/bin/', '/sbin/', '/lib/', '/System/']
            if platform.system() == 'Windows':
                system_dirs = ['C:\\Windows\\', 'C:\\Program Files\\']
            for sd in system_dirs:
                if abs_path.startswith(sd):
                    return False, f"Blocked: cannot write to system directory {sd}"

        return True, ""

    def prompt_approval(self, description: str) -> bool:
        """Ask user for approval in the terminal.

        Returns True if approved, False if denied.
        """
        if not self.require_approval:
            return True

        if not sys.stdin.isatty():
            return False

        print(f"\n\033[1;33m[LazyClaw]\033[0m Agent wants to execute:")
        print(f"  \033[1m{description}\033[0m")
        print()

        try:
            response = input("  Allow? [y/N] ").strip().lower()
            return response in ('y', 'yes')
        except (EOFError, KeyboardInterrupt):
            print()
            return False
