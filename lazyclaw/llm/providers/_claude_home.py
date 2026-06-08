"""Session isolation for the spawned ``claude`` process.

Both Claude transports (SDK and legacy CLI) launch a real ``claude``
subprocess as lazyclaw's LLM. Without an explicit working directory the
subprocess inherits the lazyclaw *server* process cwd — which on a native
``lazyclaw start`` is the lazyclaw repo dir, the SAME directory whose
``~/.claude/projects/<cwd-hash>`` bucket holds the USER's personal Claude
Code sessions. The subprocess then auto-persists the full DECRYPTED
conversation (Upwork DMs, WhatsApp, email, memory facts, SOUL.md) verbatim
in plaintext into that shared bucket — co-mingling agent traffic with the
user's personal sessions and violating lazyclaw's "encrypt everything" rule.

Pinning cwd to a lazyclaw-owned directory routes those transcripts into
their OWN project bucket instead. Credentials are unaffected — they live at
``~/.claude/.credentials.json`` and we never touch ``CLAUDE_CONFIG_DIR``.

Paired with ``setting_sources=[]`` (SDK) / ``--setting-sources=`` (CLI),
which stops the subprocess from loading the user's personal
``~/.claude/settings.json`` / ``CLAUDE.md`` / ``rules/*`` / hooks into the
agent context, this closes both directions of the Claude-Code <-> lazyclaw
session leak.
"""

from __future__ import annotations

import os
import re

# Subdir name under DATABASE_DIR for the agent's isolated Claude home/cwd.
_AGENT_HOME_SUBDIR = "claude_agent_home"
# Leaf used when no user_id is known (single-user / unscoped calls).
_SHARED_LEAF = "_shared"


def _sanitize_user_id(user_id: str) -> str:
    """Reduce a user_id to a filesystem-safe leaf name."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", user_id.strip())
    return safe or _SHARED_LEAF


def isolated_claude_cwd(user_id: str | None = None) -> str:
    """Resolve (and create) the lazyclaw-owned working directory for the
    spawned ``claude`` process.

    Lives under ``DATABASE_DIR`` (default ``./data``; the persistent /
    encrypted volume in Docker) so it travels with the rest of lazyclaw's
    state and is guaranteed writable. Returns an absolute, realpath-resolved
    string suitable for ``cwd=`` on ``asyncio.create_subprocess_exec`` /
    ``ClaudeAgentOptions(cwd=...)``.

    The cwd is scoped per ``user_id`` (``claude_agent_home/u-<id>``) so that
    multi-tenant deployments never co-mingle one user's agent transcripts
    with another's in a shared ``~/.claude/projects/<cwd-hash>`` bucket —
    upholding lazyclaw's per-user isolation guarantee. When ``user_id`` is
    not provided (single-user / unscoped calls) it falls back to a stable
    ``claude_agent_home/_shared`` leaf.
    """
    data_dir = os.getenv("DATABASE_DIR", "./data")
    leaf = f"u-{_sanitize_user_id(user_id)}" if user_id else _SHARED_LEAF
    cwd = os.path.realpath(os.path.join(data_dir, _AGENT_HOME_SUBDIR, leaf))
    os.makedirs(cwd, exist_ok=True)
    return cwd
