"""Lightweight Telegram push for notification-worthy agent events.

Wired alongside platform callbacks via MultiCallback so that ANY
completed task, failure, or human-in-the-loop request reaches the
admin's Telegram — regardless of which platform originated the work.

Only handles: done, background_done, background_failed, help_needed.
Skips all noisy intermediate events (tool_call, token, etc.).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting for plain-text Telegram messages."""
    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # Italic: *text* or _text_
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Strikethrough: ~~text~~
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # Inline code: `text`
    text = re.sub(r'`(.+?)`', r'\1', text)
    # Headers: ### text → text
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Links: [text](url) → text (url)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1 (\2)', text)
    # Bullet points: - text → • text
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    return text


class TelegramNotifier:
    """Push notification-worthy events to Telegram admin chat.

    Constructor args:
        bot:                 telegram.Bot instance (may be None if Telegram is not connected)
        admin_chat_id_fn:    callable returning current admin chat ID (str | None)
        source_is_telegram:  set True when the originating platform IS Telegram
                             to avoid duplicate notifications
    """

    def __init__(
        self,
        bot: Any | None,
        admin_chat_id_fn: Callable[[], str | None],
        *,
        source_is_telegram: bool = False,
    ) -> None:
        self._bot = bot
        self._get_chat_id = admin_chat_id_fn
        self._source_is_telegram = source_is_telegram
        self._work_summary: Any | None = None

    # ── AgentCallback interface ──────────────────────────────────────

    async def on_event(self, event: Any) -> None:
        if self._source_is_telegram:
            return

        chat_id = self._get_chat_id()
        if not chat_id or not self._bot:
            return

        text = self._format(event)
        if not text:
            return

        try:
            from lazyclaw.channels.telegram import _telegram_send_with_retry

            await _telegram_send_with_retry(
                lambda: self._bot.send_message(chat_id=int(chat_id), text=text)
            )
        except Exception as exc:
            logger.debug("TelegramNotifier send failed: %s", exc)

    async def on_approval_request(
        self, skill_name: str, arguments: dict,
    ) -> bool:
        return False

    async def on_help_request(
        self, context: str, needs_browser: bool,
    ) -> str:
        # Notify Telegram that CLI/TUI user needs help (info only)
        if not self._source_is_telegram:
            chat_id = self._get_chat_id()
            if chat_id and self._bot:
                try:
                    from lazyclaw.channels.telegram import _telegram_send_with_retry

                    msg = f"\U0001f198 Agent stuck: {context}"
                    if needs_browser:
                        msg += "\n\nBrowser handoff requested — reply in CLI/TUI."
                    else:
                        msg += "\n\nWaiting for help in CLI/TUI."
                    await _telegram_send_with_retry(
                        lambda: self._bot.send_message(
                            chat_id=int(chat_id), text=msg,
                        )
                    )
                except Exception:
                    pass
        return "skip"

    # ── Formatting ───────────────────────────────────────────────────

    def _format(self, event: Any) -> str | None:
        kind = event.kind

        if kind == "work_summary":
            self._work_summary = event.metadata.get("summary")
            return None

        if kind == "done":
            summary = self._work_summary
            self._work_summary = None

            stats_parts: list[str] = []
            result_preview = ""
            if summary is not None:
                dur = getattr(summary, "duration_ms", None)
                if dur is not None:
                    secs = dur / 1000
                    stats_parts.append(f"{secs:.0f}s" if secs < 60 else f"{secs / 60:.1f}m")
                calls = getattr(summary, "llm_calls", None)
                if calls:
                    stats_parts.append(f"{calls} LLM calls")
                tokens = getattr(summary, "total_tokens", None)
                if tokens:
                    stats_parts.append(f"{tokens:,} tokens")
                cost = getattr(summary, "total_cost", None)
                if cost and cost > 0:
                    stats_parts.append(f"${cost:.4f}")
                tools = getattr(summary, "tools_used", None)
                if tools:
                    stats_parts.append(f"tools: {', '.join(tools)}")
                preview = getattr(summary, "result_preview", None)
                if preview:
                    result_preview = f"\n\n{_strip_markdown(preview)}"

            stats_line = f"\n{' | '.join(stats_parts)}" if stats_parts else ""
            return f"\u2705 Task complete{stats_line}{result_preview}"

        if kind == "background_done":
            name = event.metadata.get("name", "")
            result = _strip_markdown(event.metadata.get("result", ""))
            preview = result[:500]
            if len(result) > 500:
                preview += "\n\n[truncated]"

            # Stats line from work_summary (if available)
            stats_parts: list[str] = []
            duration_ms = event.metadata.get("duration_ms")
            if duration_ms is not None:
                secs = duration_ms / 1000
                stats_parts.append(f"{secs:.0f}s" if secs < 60 else f"{secs / 60:.1f}m")
            tokens = event.metadata.get("total_tokens")
            if tokens:
                stats_parts.append(f"{tokens:,} tokens")
            llm_calls = event.metadata.get("llm_calls")
            if llm_calls:
                stats_parts.append(f"{llm_calls} LLM calls")
            cost = event.metadata.get("total_cost")
            if cost and cost > 0:
                stats_parts.append(f"${cost:.4f}")
            models = event.metadata.get("models_used")
            if models:
                stats_parts.append(", ".join(models))
            tools = event.metadata.get("tools_used")
            if tools:
                stats_parts.append(f"tools: {', '.join(tools)}")

            stats_line = f"\n{' | '.join(stats_parts)}" if stats_parts else ""
            return f"\u2705 Background '{name}' done{stats_line}\n\n{preview}"

        if kind == "background_failed":
            name = event.metadata.get("name", "")
            error = _strip_markdown(event.metadata.get("error", ""))[:200]
            return f"\u274c Background '{name}' failed: {error}"

        if kind == "help_needed":
            detail = _strip_markdown(event.detail or "")
            return f"\U0001f198 Agent stuck: {detail}\n\nReply in CLI/TUI to help."

        return None
