"""Browser `ocr` action — local Tesseract text extraction.

Use when the accessibility tree / DOM is weak (canvas-heavy pages, PDFs
rendered in-browser, banking or gov portals that use bitmap text). This
is much cheaper than the `ask_vision` escalation — Tesseract is local and
free, ~200ms on a viewport-sized region.

Falls back to a structured DEPENDENCY_MISSING error that directs the agent
to ``ask_vision`` when the binary isn't installed.
"""

from __future__ import annotations

import io
import logging

from lazyclaw.browser.action_errors import (
    RETRY_ESCALATE_TO_VISION,
    ActionError,
    ActionErrorCode,
)

from .backends import get_backend

logger = logging.getLogger(__name__)

# Soft cap on OCR region dimensions — tesseract handles larger images fine
# but latency grows roughly linearly, and at full-page scale we want the
# agent to call `ask_vision` instead.
_MAX_REGION_PIXELS = 4_000_000  # e.g. 2000x2000


async def action_ocr(
    user_id: str, params: dict, tab_context, config, snapshot_mgr,
) -> str:
    """Run local Tesseract OCR on the current viewport (optionally cropped)."""
    backend = await get_backend(user_id, tab_context)

    # 1. Capture a raw PNG of the viewport. Re-use backend.screenshot() — it
    #    also refreshes the thumbnail cache, which is a free side benefit.
    try:
        png_bytes = await backend.screenshot(full_page=False)
    except Exception as exc:
        logger.debug("screenshot failed in OCR action", exc_info=True)
        return str(ActionError(
            code=ActionErrorCode.FRAME_DETACHED,
            message=f"Could not capture viewport for OCR: {exc}",
            hint="Ensure a tab is open, then retry.",
            retry_strategy="wait",
        ))

    # 2. Lazy import Pillow. We know it's installed (required elsewhere) but
    #    catch the import error anyway so OCR degrades instead of crashing.
    try:
        from PIL import Image
    except ImportError:
        return str(ActionError(
            code=ActionErrorCode.DEPENDENCY_MISSING,
            message="Pillow is required for OCR but is not installed.",
            hint="Run `pip install Pillow`, or retry with action=ask_vision.",
            retry_strategy=RETRY_ESCALATE_TO_VISION,
        ))

    try:
        img = Image.open(io.BytesIO(png_bytes))
    except Exception as exc:
        return str(ActionError(
            code=ActionErrorCode.FRAME_DETACHED,
            message=f"Viewport PNG could not be decoded: {exc}",
            hint="The page may have navigated mid-capture. Retry.",
            retry_strategy="wait",
        ))

    # 3. Optional crop. Accepts either a dict {x, y, width, height} or a
    #    parallel x/y/width/height at the top level for convenience.
    region = _extract_region(params, img.width, img.height)
    if region:
        if region["width"] * region["height"] > _MAX_REGION_PIXELS:
            return str(ActionError(
                code=ActionErrorCode.POLICY_DENIED,
                message=(
                    f"OCR region is too large "
                    f"({region['width']}×{region['height']}px). Crop to a "
                    f"smaller area or use ask_vision for full-page reads."
                ),
                hint="Pass region={x, y, width, height} covering only the text you need.",
                retry_strategy=RETRY_ESCALATE_TO_VISION,
            ))
        img = img.crop((
            region["x"],
            region["y"],
            region["x"] + region["width"],
            region["y"] + region["height"],
        ))

    # 4. Run Tesseract. The Python wrapper is a hard dep; the binary is opt-in.
    try:
        import pytesseract
    except ImportError:
        return str(ActionError(
            code=ActionErrorCode.DEPENDENCY_MISSING,
            message="pytesseract is not installed.",
            hint="Run `pip install pytesseract`, or retry with action=ask_vision.",
            retry_strategy=RETRY_ESCALATE_TO_VISION,
        ))

    try:
        text = pytesseract.image_to_string(img)
    except pytesseract.TesseractNotFoundError:
        return str(ActionError(
            code=ActionErrorCode.DEPENDENCY_MISSING,
            message="Tesseract binary not installed on this system.",
            hint=(
                "Install with `brew install tesseract` (macOS) or "
                "`apt install tesseract-ocr` (Linux), or retry with "
                "action=ask_vision."
            ),
            retry_strategy=RETRY_ESCALATE_TO_VISION,
        ))
    except Exception as exc:
        logger.debug("Tesseract failed", exc_info=True)
        return str(ActionError(
            code=ActionErrorCode.FRAME_DETACHED,
            message=f"OCR failed: {exc}",
            hint="Try again or fall back to action=ask_vision.",
            retry_strategy=RETRY_ESCALATE_TO_VISION,
        ))

    cleaned = text.strip()
    if not cleaned:
        return (
            "(OCR returned no text — the region may be image-free, or the "
            "characters too small/stylised. Consider action=ask_vision.)"
        )
    return cleaned


def _extract_region(params: dict, max_w: int, max_h: int) -> dict | None:
    """Normalise region params and clamp to the viewport."""
    raw = params.get("region")
    if not raw:
        # Also accept a flat x/y/width/height at the top level.
        if any(k in params for k in ("x", "y", "width", "height")):
            raw = {
                "x": params.get("x", 0),
                "y": params.get("y", 0),
                "width": params.get("width", max_w),
                "height": params.get("height", max_h),
            }
        else:
            return None

    try:
        x = max(0, int(raw.get("x", 0)))
        y = max(0, int(raw.get("y", 0)))
        width = int(raw.get("width", max_w - x))
        height = int(raw.get("height", max_h - y))
    except (TypeError, ValueError):
        return None

    width = max(1, min(width, max_w - x))
    height = max(1, min(height, max_h - y))
    return {"x": x, "y": y, "width": width, "height": height}


# Helper retained for tests / future callers that want to pass in raw PNG bytes.
def ocr_png_bytes(png_bytes: bytes, region: dict | None = None) -> str:
    """Run Tesseract on raw PNG bytes. Raises on missing deps."""
    from PIL import Image
    import pytesseract

    img = Image.open(io.BytesIO(png_bytes))
    if region:
        img = img.crop((
            region["x"],
            region["y"],
            region["x"] + region["width"],
            region["y"] + region["height"],
        ))
    return pytesseract.image_to_string(img).strip()
