"""Lightweight Telegram push for notification-worthy agent events.

Wired alongside platform callbacks via MultiCallback so that ANY
completed task, failure, or human-in-the-loop request reaches the
admin's Telegram — regardless of which platform originated the work.

Only handles: done, background_done, background_failed, help_needed.
Skips all noisy intermediate events (tool_call, token, etc.).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


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
            msg = "\u2705 Task complete"
            summary = self._work_summary
            if summary is not None:
                elapsed = getattr(summary, "elapsed_s", None)
                llm_calls = getattr(summary, "llm_calls", None)
                if elapsed is not None and llm_calls is not None:
                    msg += f" \u2014 {elapsed:.0f}s, {llm_calls} LLM calls"
            self._work_summary = None
            return msg

        if kind == "background_done":
            name = event.metadata.get("name", "")
            result = event.metadata.get("result", "")
            preview = result[:500]
            if len(result) > 500:
                preview += "\n\n[truncated]"
            return f"\u2705 Background '{name}' done\n\n{preview}"

        if kind == "background_failed":
            name = event.metadata.get("name", "")
            error = event.metadata.get("error", "")[:200]
            return f"\u274c Background '{name}' failed: {error}"

        if kind == "help_needed":
            return f"\U0001f198 Agent stuck: {event.detail}\n\nReply in CLI/TUI to help."

        return None
