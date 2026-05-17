"""Per-process shared secret for MCP → gateway internal calls.

The token is generated at import time and exposed via :func:`get_internal_token`.
MCP subprocesses (e.g. mcp-whatsapp) receive it through the
``LAZYCLAW_INTERNAL_TOKEN`` environment variable and pass it back as the
``X-Internal-Token`` header. The gateway accepts requests on
``/api/internal/*`` routes only when the header matches.

Rationale: the contact store is encrypted with the user's DEK and lives behind
gateway routes. MCP subprocesses sit on the same host but have no session
cookie, so we can't reuse :func:`get_current_user`. A per-process random token
gives us request authentication without needing TLS or shared session state.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status

# Allow override via env (Docker / multi-process layouts), else generate one
# per Python interpreter at module load. ``secrets.token_hex(32)`` = 256 bits
# of entropy — same strength the rest of the project uses for session tokens.
_INTERNAL_TOKEN: str = (os.environ.get("LAZYCLAW_INTERNAL_TOKEN") or "").strip() or secrets.token_hex(32)

# Re-export so subprocess env can pick the same value the gateway accepts.
os.environ["LAZYCLAW_INTERNAL_TOKEN"] = _INTERNAL_TOKEN


def get_internal_token() -> str:
    """Return the current process's internal token."""
    return _INTERNAL_TOKEN


def require_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """FastAPI dependency that 401s any request without the matching token."""
    if not x_internal_token or not secrets.compare_digest(x_internal_token, _INTERNAL_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Internal-Token",
        )
