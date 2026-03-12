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
    def __init__(self, token: str, agent: Agent, config: Config) -> None:
        self._token = token
        self._agent = agent
        self._config = config
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
        text = update.message.text
        chat_id = str(update.effective_chat.id)
        # MVP: all Telegram messages routed to "default" user
        user_id = "default"
        logger.debug("Telegram message from chat %s: %s", chat_id, text[:80])
        response = await self._agent.process_message(user_id, text)
        await update.message.reply_text(response)

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
