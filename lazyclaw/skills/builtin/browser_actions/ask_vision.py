"""Browser ask_vision action — return what's visible on the page as text.

For browser-driving agents, "what does the page say?" answers ~90% of
visual questions: error messages, form labels, prices, modal copy, button
labels, login prompts. We solve that with **Apple Vision OCR** on macOS
(native, ~200ms, zero RAM, no hallucinations) and fall through to
Tesseract on Linux/Windows.

Design choice — May 2026 (commit replacing local VLM fallback):
  * Local <3GB VLMs (Moondream, SmolVLM2, etc.) hallucinate buttons that
    aren't there. We tested. Confidently wrong is worse than no answer.
  * Local 3-7B VLMs (Gemma4:e2b, Qwen2.5-VL-3B) take 15-37s per inference
    on M2 16GB and starve the rest of the agent stack of RAM.
  * The brain (Sonnet/Haiku/MiniMax) is already smart. Hand it the OCR
    text + bounding boxes and let it reason. No second LLM in the loop.

When OCR isn't enough (image-only CAPTCHAs, true visual judgement like
"does this button look disabled"), we'll add a cheap cloud vision
fallback (Sonnet vision) on demand — TODO. Not blocking shipping today.

UI/Telegram still receive the raw screenshot via the ToolResult attachment.
"""

from __future__ import annotations

import logging

from lazyclaw.runtime.tool_result import Attachment, ToolResult

from .backends import get_backend

logger = logging.getLogger(__name__)


_INTRO = (
    "Visible text on the current page (Apple Vision OCR, top-down reading "
    "order, low-confidence regions filtered):\n"
)
_INTRO_TESSERACT = "Visible text on the current page (Tesseract OCR):\n"
_NO_TEXT = (
    "(no text recognized on the visible viewport — page may be image-only, "
    "or the content hasn't rendered yet; try `screenshot` then retry, or "
    "ask the user)"
)


async def action_ask_vision(
    user_id: str, params: dict, tab_context,
) -> ToolResult:
    """Return the OCR'd text of the current viewport for the brain to reason over.

    Optional `params["question"]` is logged but not used to filter — the brain
    sees all the text and can answer the question itself.
    """
    question = (params.get("question") or "").strip()
    backend = await get_backend(user_id, tab_context)

    try:
        png_bytes = await backend.screenshot()
    except Exception as exc:
        logger.warning("ask_vision screenshot failed: %s", exc)
        return ToolResult(text=f"Could not capture screenshot: {exc}")

    attachment = Attachment(
        data=png_bytes,
        media_type="image/png",
        filename="screenshot.png",
    )

    text, intro = _ocr_text(png_bytes)

    if not text:
        body = _NO_TEXT
    else:
        body = intro + text

    if question:
        # Just echo the question so the brain remembers what it was asking
        # while reading the OCR. We deliberately don't filter; LLM is smart.
        body = f"Question: {question}\n\n{body}"

    return ToolResult(text=body, attachments=(attachment,))


def _ocr_text(png_bytes: bytes) -> tuple[str, str]:
    """Run OCR with the best available backend. Returns (text, intro_label)."""
    # Tier 1: Apple Vision (macOS only, native, ~200ms, no install of weights).
    try:
        from lazyclaw.browser import apple_vision

        if apple_vision.is_available():
            return apple_vision.read_plaintext(png_bytes), _INTRO
    except Exception as exc:
        logger.debug("Apple Vision OCR failed: %s", exc)

    # Tier 2: Tesseract — cross-platform fallback, slower (~1-3s) but
    # works on Linux/Windows containers.
    try:
        from .ocr import ocr_png_bytes

        return ocr_png_bytes(png_bytes), _INTRO_TESSERACT
    except Exception as exc:
        logger.warning("Tesseract OCR failed: %s", exc)
        return "", _INTRO
