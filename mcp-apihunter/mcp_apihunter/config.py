"""Runtime configuration, read from the environment the bundled-MCP launcher
injects into this subprocess.

The launcher (`lazyclaw/mcp/manager.py`, `inject_user_context=True`) provides
`LAZYCLAW_USER_ID`, `LAZYCLAW_BROWSER_PROFILE_DIR`, `LAZYCLAW_CDP_PORT` and (in
Docker) `LAZYCLAW_CDP_HOST`. `SERVER_SECRET` is inherited from the parent's
environment (loaded from `.env`). Everything apihunter needs to run is derived
from those — no separate config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ApiHunterConfig:
    server_secret: str
    user_id: str
    manifest_root: Path
    cdp_host: str
    cdp_port: int

    @property
    def has_manifest_key(self) -> bool:
        return bool(self.server_secret and self.user_id)


def load_config() -> ApiHunterConfig:
    profile_dir = os.environ.get("LAZYCLAW_BROWSER_PROFILE_DIR", "")
    # Fall back to a private dir under the package data area when the profile
    # dir isn't injected (e.g. a standalone run), so the store always has a home.
    root = Path(profile_dir) if profile_dir else Path.home() / ".apihunter"
    return ApiHunterConfig(
        server_secret=os.environ.get("SERVER_SECRET", ""),
        user_id=os.environ.get("LAZYCLAW_USER_ID", ""),
        manifest_root=root,
        cdp_host=os.environ.get("LAZYCLAW_CDP_HOST", "127.0.0.1"),
        cdp_port=int(os.environ.get("LAZYCLAW_CDP_PORT", "9222")),
    )
