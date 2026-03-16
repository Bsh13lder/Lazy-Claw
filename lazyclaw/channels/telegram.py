from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)


class TelegramAdapter(ChannelAdapter):
    def __init__(self, token: str, agent: Agent, config: Config, lane_queue=None) -> None:
        self._token = token
        self._agent = agent
        self._config = config
        self._lane_queue = lane_queue
        self._app = None

    async def start(self) -> None:
        self._app = ApplicationBuilder().token(self._token).build()
        self._app.add_handler(CommandHandler("start", self._handle_start))
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

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message or not update.message.text:
            return

        chat_id = str(update.effective_chat.id)
        text = update.message.text.strip()
        if not text:
            return

        # MVP: all Telegram messages routed to "default" user
        user_id = "default"
        logger.info("Telegram message from chat %s: %s", chat_id, text[:100])

        try:
            if self._lane_queue:
                response = await self._lane_queue.enqueue(user_id, text)
            else:
                response = await self._agent.process_message(user_id, text)

            logger.info("Telegram response to chat %s: %s", chat_id, response[:100])

            # Split long messages for Telegram's 4096 char limit
            if len(response) <= 4096:
                await update.message.reply_text(response)
            else:
                for i in range(0, len(response), 4096):
                    await update.message.reply_text(response[i:i + 4096])
        except Exception as e:
            logger.error("Telegram handler error for chat %s: %s", chat_id, e, exc_info=True)
            try:
                await update.message.reply_text("Sorry, something went wrong. Please try again.")
            except Exception:
                logger.error("Failed to send error reply to chat %s", chat_id)

    async def _handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await update.message.reply_text(
            "Hey! I'm Claw, your AI assistant. Send me a message!"
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
