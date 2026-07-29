"""Serve the sideloadable Android APK + its version metadata.

NOTE: the ``/api/mobile/version`` and ``/api/mobile/apk`` endpoints are
intentionally unauthenticated for first-run/self-update convenience on a
trusted LAN. The APK is a generic client build and contains no user data.
If the gateway is ever exposed to the public internet, gate these behind a
signed/HMAC download URL or a shared secret.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mobile", tags=["mobile"])

# Default served location; overridable for tests.
_APK_DIR = Path(__file__).resolve().parents[3] / "mobile" / "dist"


def set_apk_dir(path: Path) -> None:
    global _APK_DIR
    _APK_DIR = Path(path)


def _apk_path() -> Path:
    return _APK_DIR / "app-release.apk"


def _version_path() -> Path:
    return _APK_DIR / "version.json"


def _keyboard_apk_path() -> Path:
    return _APK_DIR / "keyboard.apk"


def _keyboard_version_path() -> Path:
    return _APK_DIR / "keyboard-version.json"


# A version manifest is the one response that must NEVER come from a cache: a
# stale copy makes a freshly published build invisible and the client concludes
# "up to date" forever. These endpoints previously returned a bare JSONResponse
# with no freshness info at all, which HTTP lets intermediaries and browsers
# cache heuristically — reported as "there is no new APK" while the server was
# serving the new build correctly on every path.
_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@router.get("/version")
async def mobile_version() -> JSONResponse:
    vp = _version_path()
    if not vp.exists() or not _apk_path().exists():
        logger.warning(
            "[route:mobile] GET version -> 404 no mobile build published (dir=%s)",
            _APK_DIR,
        )
        raise HTTPException(status_code=404, detail="No mobile build published")
    return JSONResponse(json.loads(vp.read_text()), headers=_NO_STORE)


@router.get("/apk")
async def mobile_apk() -> FileResponse:
    ap = _apk_path()
    if not ap.exists():
        logger.warning(
            "[route:mobile] GET apk -> 404 no APK published (dir=%s)", _APK_DIR,
        )
        raise HTTPException(status_code=404, detail="No APK published")
    return FileResponse(
        ap,
        media_type="application/vnd.android.package-archive",
        filename="lazyclaw.apk",
    )


@router.get("/keyboard-version")
async def keyboard_version() -> JSONResponse:
    vp = _keyboard_version_path()
    if not vp.exists() or not _keyboard_apk_path().exists():
        logger.warning(
            "[route:mobile] GET keyboard-version -> 404 no keyboard build published (dir=%s)",
            _APK_DIR,
        )
        raise HTTPException(status_code=404, detail="No keyboard build published")
    return JSONResponse(json.loads(vp.read_text()), headers=_NO_STORE)


@router.get("/keyboard-apk")
async def keyboard_apk() -> FileResponse:
    ap = _keyboard_apk_path()
    if not ap.exists():
        logger.warning(
            "[route:mobile] GET keyboard-apk -> 404 no keyboard APK published (dir=%s)",
            _APK_DIR,
        )
        raise HTTPException(status_code=404, detail="No keyboard APK published")
    return FileResponse(
        ap,
        media_type="application/vnd.android.package-archive",
        filename="ai-keyboard.apk",
    )
