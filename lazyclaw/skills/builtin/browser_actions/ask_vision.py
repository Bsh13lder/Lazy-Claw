"""Browser ask_vision action — return what's visible on the page as text.

Two-tier pipeline (2026-05-16):

  Tier 1 — Apple Vision OCR (macOS, native, ~200ms, no RAM, no hallucinations).
           Tesseract on Linux/Windows. Returns visible text in reading order.
           Answers ~90% of browser questions (errors, prices, modal copy,
           form labels, "is there a CAPTCHA?").

  Tier 2 — Sonnet vision via the Claude Agent SDK. Fires on user's Claude Max
           subscription (zero incremental cost). Used for genuine visual
           judgement that text-only OCR can't answer ("does this button look
           disabled?", "is this layout broken?", non-text CAPTCHAs).

Trigger policy:
  * mode="auto" (default) — OCR first; escalate to Sonnet when OCR is empty
    OR when the question contains visual-judgement keywords.
  * mode="ocr"  — OCR only, never escalates. Use when you want determinism
    and the question is clearly textual.
  * mode="llm"  — skip OCR, go straight to Sonnet. Use for pure visual
    judgement where OCR is noise.

The Sonnet tier is GATED to MODE_CLAUDE/SDK in vision_query.py — any other eco
mode silently returns "" and we fall back to OCR-only output. This keeps the
"subscription-only, no surprise paid-API spend" guarantee from the May 2026
design memo.

Why we don't use a local <3GB VLM as Tier 2 (memo 2026-05-04, still valid):
  * Moondream 1.9B hallucinated buttons that don't exist. Confidently wrong.
  * Gemma4:e2b accurate but 37s cold + 15s/call on M2 16GB → unusable.
  * Sonnet via subscription costs nothing and reads UI correctly.

UI/Telegram still receive the raw screenshot via the ToolResult attachment
regardless of which tier ran.
"""

from __future__ import annotations

import logging
import re

from lazyclaw.runtime.tool_result import Attachment, ToolResult

from .backends import get_backend

logger = logging.getLogger(__name__)


_INTRO_OCR = (
    "Visible text on the current page (Apple Vision OCR, top-down reading "
    "order, low-confidence regions filtered):\n"
)
_INTRO_TESSERACT = "Visible text on the current page (Tesseract OCR):\n"
_INTRO_VISION_LLM = "Sonnet vision answer (image inspected directly):\n"
_NO_TEXT = (
    "(no text recognized on the visible viewport — page may be image-only, "
    "or the content hasn't rendered yet; try `screenshot` then retry, or "
    "ask the user)"
)

# Heuristic for auto-escalation: questions that ask about appearance, state,
# or non-textual features need pixel-level reasoning that OCR can't provide.
# Conservative on purpose — keyword false-positives just cost one extra SDK
# call, false-negatives keep OCR-only output which is the safe default.
_VISUAL_KEYWORD_RE = re.compile(
    r"\b("
    r"look(?:s|ing)?\s+like|"
    r"appear(?:s|ance)?|"
    r"color|colou?red|"
    r"highlight(?:ed)?|"
    r"disabled|grey(?:ed)?|grayed|"
    r"layout|broken|misaligned|overlap|"
    r"icon|logo|image|picture|avatar|"
    r"captcha|"
    r"font|bold|italic|underlin|"
    r"button.*(?:state|look|color)|"
    r"screenshot\s+show"
    r")\b",
    re.IGNORECASE,
)

_MIN_OCR_USEFUL_CHARS = 10  # below this, treat OCR as effectively empty


async def action_ask_vision(
    user_id: str, params: dict, tab_context,
) -> ToolResult:
    """Return what's on the current viewport for the brain to reason over.

    Params:
      question: optional NL question. Influences auto-escalation and is passed
                to the Sonnet tier verbatim. Ignored by OCR (OCR always returns
                all visible text).
      mode:     "auto" | "ocr" | "llm". Default "auto".
    """
    question = (params.get("question") or "").strip()
    mode = (params.get("mode") or "auto").strip().lower()
    if mode not in ("auto", "ocr", "llm"):
        mode = "auto"

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

    ocr_text: str = ""
    ocr_intro: str = _INTRO_OCR
    vision_llm_text: str = ""

    # Tier 1 — OCR (skipped when mode="llm").
    if mode != "llm":
        ocr_text, ocr_intro = _ocr_text(png_bytes)

    # Tier 2 — Sonnet vision (fires per mode policy).
    should_call_llm = mode == "llm" or (
        mode == "auto"
        and (
            len(ocr_text.strip()) < _MIN_OCR_USEFUL_CHARS
            or _is_visual_question(question)
        )
    )
    if should_call_llm:
        vision_llm_text = await _try_claude_vision(user_id, png_bytes, question)

    body = _compose_body(question, ocr_text, ocr_intro, vision_llm_text)
    return ToolResult(text=body, attachments=(attachment,))


def _is_visual_question(question: str) -> bool:
    if not question:
        return False
    return bool(_VISUAL_KEYWORD_RE.search(question))


async def _try_claude_vision(user_id: str, png_bytes: bytes, question: str) -> str:
    """Best-effort Sonnet vision call. Returns "" on any failure or
    wrong-mode condition — caller falls back to OCR output."""
    try:
        from lazyclaw.llm.vision_query import ask_claude_vision

        return await ask_claude_vision(user_id, png_bytes, question)
    except Exception as exc:
        logger.warning("ask_vision: Sonnet tier failed: %s", exc)
        return ""


def _compose_body(
    question: str, ocr_text: str, ocr_intro: str, vision_llm_text: str,
) -> str:
    """Assemble the text body for the brain. Order: question echo (if any) →
    Sonnet answer (if any) → OCR text (if any) → fallback no-text notice."""
    parts: list[str] = []
    if question:
        parts.append(f"Question: {question}")

    if vision_llm_text:
        parts.append(_INTRO_VISION_LLM + vision_llm_text)

    if ocr_text.strip():
        parts.append(ocr_intro + ocr_text)

    if not parts or (not vision_llm_text and not ocr_text.strip()):
        parts.append(_NO_TEXT)

    return "\n\n".join(parts)


def _ocr_text(png_bytes: bytes) -> tuple[str, str]:
    """Run OCR with the best available backend. Returns (text, intro_label)."""
    try:
        from lazyclaw.browser import apple_vision

        if apple_vision.is_available():
            return apple_vision.read_plaintext(png_bytes), _INTRO_OCR
    except Exception as exc:
        logger.debug("Apple Vision OCR failed: %s", exc)

    try:
        from .ocr import ocr_png_bytes

        return ocr_png_bytes(png_bytes), _INTRO_TESSERACT
    except Exception as exc:
        logger.warning("Tesseract OCR failed: %s", exc)
        return "", _INTRO_OCR
