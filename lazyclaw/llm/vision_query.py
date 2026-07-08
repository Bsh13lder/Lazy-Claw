"""Claude vision via the Agent SDK — on-demand image-Q&A through subscription.

A one-shot helper that takes raw PNG bytes plus a natural-language question and
returns Sonnet's text answer. Used as the Tier-2 escalation inside
`ask_vision`: when Apple Vision OCR returns nothing or the question is a true
visual judgement ("does this button look disabled?", "is there a non-text
captcha?"), we hand the screenshot to Sonnet directly.

Routing rules (per session design 2026-05-16):
  * MODE_CLAUDE only — fires through the user's Claude Max subscription
    (zero incremental cost until the 2026-06-15 budget split). Any other mode
    silently returns "" so the caller falls back to OCR-only output.
  * No MCP, no tools, no system prompt beyond a minimal vision-judge nudge.
  * Streaming-input form of `claude_agent_sdk.query()` — that's the only
    surface that lets us pass `content` blocks (image + text) instead of the
    plain-string prompt the chat path uses.

Failure mode is "return empty string". The caller always has the OCR fallback,
so swallowing exceptions here keeps the brain flow stable.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, AsyncIterable

logger = logging.getLogger(__name__)


_VISION_SYSTEM_PROMPT = (
    "You are a visual judge for a browser-driving agent. The user will show "
    "you a screenshot and ask a question. Answer in 1-3 short sentences, "
    "concretely, based only on what is visible. If you can't tell, say so."
)


async def ask_claude_vision(
    user_id: str,
    png_bytes: bytes,
    question: str,
    *,
    timeout_s: float = 30.0,
) -> str:
    """Send a screenshot + question to Sonnet via the Agent SDK. Returns the
    text answer, or "" on any failure / wrong-mode condition.

    The empty-string contract is load-bearing: `ask_vision` uses it to decide
    whether to surface this tier's output to the brain or fall back to OCR.
    """
    question = (question or "").strip()
    if not png_bytes:
        return ""

    try:
        from claude_agent_sdk import (
            query as sdk_query,
            ClaudeAgentOptions,
            AssistantMessage,
            TextBlock,
        )
    except ImportError as exc:
        logger.debug("vision_query: claude-agent-sdk missing (%s)", exc)
        return ""

    # MODE_CLAUDE gate — anything else means the user explicitly opted out of
    # the subscription path, so we don't quietly spend paid-API tokens here.
    try:
        from lazyclaw.config import load_config
        from lazyclaw.llm.eco_router import _load_eco_settings, MODE_CLAUDE
    except Exception as exc:
        logger.debug("vision_query: cannot load eco settings (%s)", exc)
        return ""

    try:
        config = load_config()
        settings = await _load_eco_settings(config, user_id)
    except Exception as exc:
        logger.debug("vision_query: eco settings unavailable (%s)", exc)
        return ""

    if settings.mode != MODE_CLAUDE:
        logger.debug("vision_query: mode=%s != claude, skipping", settings.mode)
        return ""

    transport = getattr(settings, "claude_transport", "sdk") or "sdk"
    if transport != "sdk":
        logger.debug("vision_query: transport=%s != sdk, skipping", transport)
        return ""

    model = settings.claude_brain_model or "claude-sonnet-4-6"

    b64_png = base64.b64encode(png_bytes).decode("ascii")

    # Stream-json user message with mixed image + text content.
    # Format mirrors Anthropic's native API content blocks — the underlying
    # claude CLI accepts this verbatim via `--input-format stream-json`.
    user_msg: dict[str, Any] = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64_png,
                    },
                },
                {
                    "type": "text",
                    "text": question or "Describe what's visible on this screen.",
                },
            ],
        },
    }

    async def _one_shot() -> AsyncIterable[dict[str, Any]]:
        yield user_msg

    # Same env-clearing trick the chat-path provider uses: the SDK *merges*
    # `options.env` over `os.environ` rather than replacing, so we have to
    # explicitly blank out any API_KEY/AUTH_TOKEN/BASE_URL that would preempt
    # the OAuth subscription path. See claude_sdk_provider._build_options for
    # the full rationale.
    # Force OAuth subscription auth AND inject the persisted setup-token — the
    # SAME injection claude_sdk_provider._build_options does. Without it this
    # path falls back to the (often-expired) ~/.claude/.credentials.json and
    # 401s, which surfaces as the SDK "error result: success" quirk and a BLIND
    # browser specialist: every ask_vision call fails, the model can't read the
    # page, and it re-fires `browser` in a loop (2026-07-06 incident).
    from lazyclaw.llm.providers._claude_token import read_claude_oauth_token

    _vision_env = {
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "ANTHROPIC_BASE_URL": "",
    }
    _vision_token = read_claude_oauth_token()
    if _vision_token:
        _vision_env["CLAUDE_CODE_OAUTH_TOKEN"] = _vision_token

    options = ClaudeAgentOptions(
        model=model,
        permission_mode="bypassPermissions",
        strict_mcp_config=True,
        env=_vision_env,
        system_prompt=_VISION_SYSTEM_PROMPT,
    )

    import asyncio

    text_parts: list[str] = []
    try:
        async def _consume() -> None:
            async for msg in sdk_query(prompt=_one_shot(), options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)

        await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning("vision_query: timed out after %.1fs", timeout_s)
        return ""
    except Exception as exc:
        logger.warning("vision_query: SDK call failed: %s", exc)
        return ""

    return "\n".join(p for p in text_parts if p).strip()
