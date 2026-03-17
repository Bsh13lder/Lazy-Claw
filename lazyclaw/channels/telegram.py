from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from lazyclaw.channels.base import ChannelAdapter, OutboundMessage
from lazyclaw.config import Config
from lazyclaw.runtime.agent import Agent
from lazyclaw.runtime.callbacks import AgentEvent

logger = logging.getLogger(__name__)


class _TelegramCallback:
    """Tracks agent status and sends live updates to Telegram chat."""

    def __init__(self, bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._status_msg = None
        self._started = time.monotonic()
        # Live state
        self.busy = True
        self.current_phase = "preparing"
        self.current_tool = ""
        self.current_model = ""
        self.current_iteration = 0
        self.tool_log: list[str] = []
        self.specialists_active: list[str] = []

    def get_status_report(self) -> str:
        elapsed = int(time.monotonic() - self._started)
        lines = [f"Working for {elapsed}s"]
        if self.current_phase == "thinking":
            lines.append(f"Thinking ({self.current_model}, step {self.current_iteration})")
        elif self.current_phase == "tool":
            lines.append(f"Running: {self.current_tool}")
        elif self.current_phase == "streaming":
            lines.append("Writing response...")
        elif self.current_phase == "team":
            lines.append("Team lead delegating work")
        if self.specialists_active:
            lines.append(f"Specialists: {', '.join(self.specialists_active[-3:])}")
        if self.tool_log:
            recent = self.tool_log[-3:]
            lines.append("Recent:")
            for entry in recent:
                lines.append(f"  {entry}")
        return "\n".join(lines)

    async def _update_status(self, text: str) -> None:
        """Edit the status message in-place, or send a new one."""
        try:
            if self._status_msg:
                await self._status_msg.edit_text(f"⏳ {text}")
            else:
                self._status_msg = await self._bot.send_message(
                    chat_id=self._chat_id, text=f"⏳ {text}"
                )
        except Exception:
            pass  # Telegram edit can fail if text unchanged

    async def _delete_status(self) -> None:
        if self._status_msg:
            try:
                await self._status_msg.delete()
            except Exception:
                pass
            self._status_msg = None

    async def on_approval_request(self, skill_name: str, arguments: dict) -> bool:
        # Auto-approve in Telegram (no interactive prompt available)
        return True

    async def on_event(self, event: AgentEvent) -> None:
        kind = event.kind
        if kind == "llm_call":
            self.current_phase = "thinking"
            self.current_model = event.metadata.get("model", "?")
            self.current_iteration = event.metadata.get("iteration", 1)
            await self._update_status(
                f"Thinking ({self.current_model}, step {self.current_iteration})..."
            )
        elif kind == "tool_call":
            self.current_phase = "tool"
            self.current_tool = event.detail
            self.tool_log.append(event.detail)
            await self._update_status(f"Running: {event.detail}")
        elif kind == "tool_result":
            self.tool_log.append(f"Done: {event.detail}")
        elif kind == "team_delegate":
            self.current_phase = "team"
            self.specialists_active.append(event.detail)
            await self._update_status(f"Team: {event.detail}")
        elif kind == "token":
            self.current_phase = "streaming"
        elif kind == "done":
            self.busy = False
            await self._delete_status()


# Status query keywords
_STATUS_KEYWORDS = {
    "what's happening", "whats happening", "what are you doing",
    "status", "what's going on", "whats going on", "?",
    "what is happening", "are you working",
}


def _is_status_query(text: str) -> bool:
    lower = text.lower().strip()
    return lower in _STATUS_KEYWORDS or lower == "/status"


class TelegramAdapter(ChannelAdapter):
    def __init__(self, token: str, agent: Agent, config: Config, lane_queue=None) -> None:
        self._token = token
        self._agent = agent
        self._config = config
        self._lane_queue = lane_queue
        self._app = None
        # Track active callback per chat for status queries
        self._active_callbacks: dict[str, _TelegramCallback] = {}
        self._pending_messages: dict[str, list[str]] = {}

    async def start(self) -> None:
        self._app = ApplicationBuilder().token(self._token).build()
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("status", self._handle_status_cmd))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram adapter started")

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        logger.info("Telegram adapter stopped")

    async def _handle_status_cmd(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /status command — show what agent is doing."""
        chat_id = str(update.effective_chat.id)
        cb = self._active_callbacks.get(chat_id)
        if cb and cb.busy:
            await update.message.reply_text(cb.get_status_report())
        else:
            await update.message.reply_text("Idle — waiting for your message.")

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message or not update.message.text:
            return

        chat_id = str(update.effective_chat.id)
        text = update.message.text.strip()
        if not text:
            return

        # Status query while agent is working
        active_cb = self._active_callbacks.get(chat_id)
        if active_cb and active_cb.busy:
            if _is_status_query(text):
                await update.message.reply_text(active_cb.get_status_report())
                return
            # Queue the message
            self._pending_messages.setdefault(chat_id, []).append(text)
            await update.message.reply_text("📥 Queued — will process after current task.")
            return

        # MVP: all Telegram messages routed to "default" user
        user_id = "default"
        logger.info("Telegram message from chat %s: %s", chat_id, text[:100])

        await self._process_and_reply(update, chat_id, user_id, text)

        # Process queued messages
        queued = self._pending_messages.pop(chat_id, [])
        for queued_text in queued:
            await self._process_and_reply(update, chat_id, user_id, queued_text)

    async def _process_and_reply(
        self, update: Update, chat_id: str, user_id: str, text: str,
    ) -> None:
        """Run agent and reply with result. Tracks status for live queries."""
        callback = _TelegramCallback(self._app.bot, int(chat_id))
        self._active_callbacks[chat_id] = callback

        try:
            if self._lane_queue:
                response = await self._lane_queue.enqueue(
                    user_id, text, callback=callback,
                )
            else:
                response = await self._agent.process_message(
                    user_id, text, callback=callback,
                )
            if not response or not response.strip():
                response = "Sorry, I couldn't process that. Please try again."
            logger.info("Telegram response to chat %s: %s", chat_id, response[:100])

            # Clean up status message
            await callback._delete_status()

            # Split long messages for Telegram's 4096 char limit
            if len(response) <= 4096:
                await update.message.reply_text(response)
            else:
                for i in range(0, len(response), 4096):
                    await update.message.reply_text(response[i:i + 4096])
        except Exception as e:
            logger.error("Telegram handler error for chat %s: %s", chat_id, e, exc_info=True)
            await callback._delete_status()
            try:
                await update.message.reply_text("Sorry, something went wrong. Please try again.")
            except Exception:
                logger.error("Failed to send error reply to chat %s", chat_id)
        finally:
            callback.busy = False
            self._active_callbacks.pop(chat_id, None)

    async def _handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await update.message.reply_text(
            "Hey! I'm Claw, your AI assistant.\n\n"
            "Send me a message to chat.\n"
            "While I'm working, type /status or \"what's happening\" to see progress."
        )

    async def send_message(
        self, external_user_id: str, message: OutboundMessage
    ) -> None:
        if self._app is None:
            raise RuntimeError("Telegram adapter not started")
        await self._app.bot.send_message(
            chat_id=int(external_user_id), text=message.text
        )

    @staticmethod
    async def verify_token(token: str) -> dict | None:
        """Call Telegram getMe API to verify token. Returns bot info or None."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data["result"]
        return None
