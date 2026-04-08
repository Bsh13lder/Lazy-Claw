"""Vision fallback — when browser actions fail repeatedly, analyze a screenshot.

Uses a vision-capable LLM to describe what's on the page and suggest next
actions. This handles CAPTCHAs, unexpected popups, broken layouts, and
situations where the accessibility tree / DOM snapshot doesn't capture
the problem.

Zero-cost path: only triggered after 3+ consecutive failures on the same
page. Screenshot is taken once and analyzed once.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Minimum seconds between vision analyses (prevents token burn)
_COOLDOWN_SECONDS = 30.0
_last_analysis_time: float = 0.0

# Consecutive failure threshold before triggering vision
FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class VisionAnalysis:
    """Result of vision model analyzing a browser screenshot."""

    description: str       # What's visible on the page
    suggestion: str        # Recommended next action
    is_captcha: bool       # Whether a CAPTCHA was detected
    is_login_wall: bool    # Whether a login form is blocking
    is_error_page: bool    # Whether an error page is showing
    confidence: float      # 0.0 - 1.0


async def analyze_screenshot(
    screenshot_bytes: bytes,
    context: str = "",
    model: str = "",
) -> VisionAnalysis:
    """Send a screenshot to a vision-capable LLM for analysis.

    Uses the eco router to pick the best available vision model.
    Falls back to a basic heuristic analysis if no vision model available.
    """
    global _last_analysis_time

    # Cooldown check
    now = time.monotonic()
    if now - _last_analysis_time < _COOLDOWN_SECONDS:
        return VisionAnalysis(
            description="(cooldown — analysis skipped)",
            suggestion="Wait a moment before retrying.",
            is_captcha=False, is_login_wall=False, is_error_page=False,
            confidence=0.0,
        )
    _last_analysis_time = now

    # Try vision model via LLM router
    try:
        return await _analyze_with_llm(screenshot_bytes, context, model)
    except Exception as exc:
        logger.warning("Vision LLM analysis failed, using heuristic: %s", exc)
        return _heuristic_fallback()


async def _analyze_with_llm(
    screenshot_bytes: bytes,
    context: str,
    model: str,
) -> VisionAnalysis:
    """Analyze screenshot using a vision-capable LLM."""
    from lazyclaw.llm.router import route_message

    b64_image = base64.b64encode(screenshot_bytes).decode("ascii")

    prompt = (
        "You are analyzing a browser screenshot for an AI agent that's stuck. "
        "Describe what you see concisely, then suggest what action to take.\n\n"
        "Focus on:\n"
        "1. Is there a CAPTCHA? (reCAPTCHA, hCaptcha, Turnstile, 'I am human')\n"
        "2. Is there a login wall or auth prompt?\n"
        "3. Is there an error message?\n"
        "4. What interactive elements are visible?\n"
        "5. What should the agent do next?\n"
    )
    if context:
        prompt += f"\nContext: {context}\n"

    prompt += (
        "\nRespond in this exact format:\n"
        "DESCRIPTION: <what you see>\n"
        "CAPTCHA: yes/no\n"
        "LOGIN_WALL: yes/no\n"
        "ERROR_PAGE: yes/no\n"
        "SUGGESTION: <what to do next>\n"
    )

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64_image,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]

    # Use Haiku for cost-efficiency (vision analysis is one-shot)
    response = await route_message(
        messages=messages,
        system="You are a browser screenshot analyzer. Be concise.",
        model=model or "haiku",
        max_tokens=300,
    )

    text = response.get("content", "") if isinstance(response, dict) else str(response)
    return _parse_vision_response(text)


def _parse_vision_response(text: str) -> VisionAnalysis:
    """Parse the structured response from the vision model."""
    lines = text.strip().split("\n")
    result = {
        "description": "",
        "suggestion": "",
        "captcha": False,
        "login_wall": False,
        "error_page": False,
    }

    for line in lines:
        line_lower = line.strip().lower()
        if line_lower.startswith("description:"):
            result["description"] = line.split(":", 1)[1].strip()
        elif line_lower.startswith("suggestion:"):
            result["suggestion"] = line.split(":", 1)[1].strip()
        elif line_lower.startswith("captcha:"):
            result["captcha"] = "yes" in line_lower
        elif line_lower.startswith("login_wall:"):
            result["login_wall"] = "yes" in line_lower
        elif line_lower.startswith("error_page:"):
            result["error_page"] = "yes" in line_lower

    # Fallback: if structured parsing failed, use the raw text
    if not result["description"] and text:
        result["description"] = text[:200]
        result["suggestion"] = "Review the screenshot description and decide next action."

    return VisionAnalysis(
        description=result["description"],
        suggestion=result["suggestion"],
        is_captcha=result["captcha"],
        is_login_wall=result["login_wall"],
        is_error_page=result["error_page"],
        confidence=0.8 if result["description"] else 0.3,
    )


def _heuristic_fallback() -> VisionAnalysis:
    """Basic fallback when no vision model is available."""
    return VisionAnalysis(
        description="Unable to analyze screenshot (no vision model available).",
        suggestion=(
            "Try: 1) Take a new snapshot to check page state, "
            "2) If stuck on CAPTCHA, use browser(action='show') to open visible browser, "
            "3) Ask the user for help."
        ),
        is_captcha=False,
        is_login_wall=False,
        is_error_page=False,
        confidence=0.1,
    )


async def check_and_analyze(
    backend,
    failure_count: int,
    context: str = "",
) -> VisionAnalysis | None:
    """Check if vision fallback should trigger, and if so, analyze.

    Returns VisionAnalysis if triggered, None if not yet needed.
    Called by action handlers after a verification failure.
    """
    if failure_count < FAILURE_THRESHOLD:
        return None

    logger.info(
        "Vision fallback triggered after %d consecutive failures", failure_count,
    )

    try:
        screenshot = await backend.screenshot()
        return await analyze_screenshot(screenshot, context=context)
    except Exception as exc:
        logger.warning("Vision fallback screenshot failed: %s", exc)
        return _heuristic_fallback()
