"""Lightweight Telegram push for notification-worthy agent events.

Wired alongside platform callbacks via MultiCallback so that ANY
completed task, failure, or human-in-the-loop request reaches the
admin's Telegram — regardless of which platform originated the work.

Only handles: done, background_done, background_failed, help_needed.
Skips all noisy intermediate events (tool_call, token, etc.).
"""

from __future__ import annotations

import html
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


def _format_stats_html(meta: dict) -> str:
    """Build an HTML stats line from event metadata."""
    parts: list[str] = []
    duration_ms = meta.get("duration_ms")
    if duration_ms is not None:
        secs = duration_ms / 1000
        parts.append(f"{secs:.0f}s" if secs < 60 else f"{secs / 60:.1f}m")
    tokens = meta.get("total_tokens")
    if tokens:
        parts.append(f"{tokens:,} tok")
    llm_calls = meta.get("llm_calls")
    if llm_calls:
        parts.append(f"{llm_calls} calls")
    cost = meta.get("total_cost")
    if cost and cost > 0:
        parts.append(f"${cost:.4f}")
    return " | ".join(parts)


def _format_tools_html(meta: dict) -> str:
    """Format tools used as a compact HTML line."""
    tools = meta.get("tools_used")
    if not tools:
        return ""
    tool_list = ", ".join(html.escape(t) for t in tools[:8])
    extra = f" +{len(tools) - 8} more" if len(tools) > 8 else ""
    return f"Tools: <i>{tool_list}{extra}</i>"


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

        text, parse_mode = self._format(event)
        if not text:
            return

        try:
            from lazyclaw.channels.telegram import _telegram_send_with_retry

            await _telegram_send_with_retry(
                lambda: self._bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    parse_mode=parse_mode,
                )
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

                    ctx = html.escape(context[:300])
                    msg = f"<b>Agent needs help</b>\n\n{ctx}"
                    if needs_browser:
                        msg += "\n\n<i>Browser handoff requested — reply in CLI/TUI.</i>"
                    else:
                        msg += "\n\n<i>Waiting for help in CLI/TUI.</i>"
                    await _telegram_send_with_retry(
                        lambda: self._bot.send_message(
                            chat_id=int(chat_id), text=msg, parse_mode="HTML",
                        )
                    )
                except Exception as exc:
                    logger.debug("Failed to notify Telegram about CLI help request: %s", exc)
        return "skip"

    # ── Formatting ───────────────────────────────────────────────────

    def _format(self, event: Any) -> tuple[str | None, str | None]:
        """Return (text, parse_mode) or (None, None) if event should be skipped."""
        kind = event.kind

        if kind == "work_summary":
            self._work_summary = event.metadata.get("summary")
            return None, None

        if kind == "done":
            summary = self._work_summary
            self._work_summary = None

            stats_parts: list[str] = []
            result_preview = ""
            if summary is not None:
                meta = {}
                for attr in ("duration_ms", "llm_calls", "total_tokens", "total_cost", "tools_used"):
                    val = getattr(summary, attr, None)
                    if val is not None:
                        meta[attr] = val
                stats_line = _format_stats_html(meta)
                tools_line = _format_tools_html(meta)
                preview = getattr(summary, "result_preview", None)
                if preview:
                    result_preview = html.escape(_strip_markdown(preview)[:500])

                lines = ["[done] <b>Task complete</b>"]
                if stats_line:
                    lines.append(stats_line)
                if tools_line:
                    lines.append(tools_line)
                if result_preview:
                    lines.append("")
                    lines.append(f"<pre>{result_preview}</pre>")
                return "\n".join(lines), "HTML"

            return "[done] <b>Task complete</b>", "HTML"

        if kind == "background_done":
            meta = event.metadata or {}
            name = html.escape(meta.get("name", ""))
            result = _strip_markdown(meta.get("result", ""))
            preview = html.escape(result[:500])
            if len(result) > 500:
                preview += "\n[truncated]"

            stats_line = _format_stats_html(meta)
            tools_line = _format_tools_html(meta)
            models = meta.get("models_used")
            model_line = ""
            if models:
                model_line = "Model: " + ", ".join(html.escape(m) for m in models)

            lines = [f"[done] <b>Background '{name}' done</b>"]
            if stats_line:
                lines.append(stats_line)
            if model_line:
                lines.append(model_line)
            if tools_line:
                lines.append(tools_line)
            if preview:
                lines.append("")
                lines.append(f"<pre>{preview}</pre>")
            return "\n".join(lines), "HTML"

        if kind == "background_failed":
            meta = event.metadata or {}
            name = html.escape(meta.get("name", ""))
            error = html.escape(_strip_markdown(meta.get("error", ""))[:300])
            return (
                f"[error] <b>Background '{name}' failed</b>\n\n"
                f"<pre>{error}</pre>"
            ), "HTML"

        if kind == "help_needed":
            detail = html.escape(_strip_markdown(event.detail or "")[:300])
            return f"[help] <b>Agent stuck</b>\n\n{detail}\n\n<i>Reply in CLI/TUI to help.</i>", "HTML"

        return None, None
