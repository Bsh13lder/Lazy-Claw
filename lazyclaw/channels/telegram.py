"""Telegram channel adapter with rich agent status notifications.

Provides real-time specialist progress, structured completion summaries,
and edit throttling to stay within Telegram rate limits.
"""

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
from lazyclaw.runtime.summary import format_summary_telegram

logger = logging.getLogger(__name__)

# Minimum seconds between Telegram message edits (rate limit protection)
_EDIT_THROTTLE_S = 2.0


class _TelegramCallback:
    """Tracks agent status and sends rich live updates to Telegram chat.

    Features over the previous version:
    - Specialist progress grid (queued/running/done with tools)
    - Edit throttling (2s minimum between edits)
    - Structured completion summary with tools and timing
    - specialist_thinking event support
    """

    def __init__(self, bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._status_msg = None
        self._started = time.monotonic()
        self._last_edit_time: float = 0.0
        # Live state
        self.busy = True
        self.current_phase = "preparing"
        self.current_tool = ""
        self.current_model = ""
        self.current_iteration = 0
        self.tool_log: list[str] = []
        self.specialists_active: list[str] = []
        # Team tracking
        self._team_specialists: dict[str, dict] = {}

    def get_status_report(self) -> str:
        """Build a detailed status report for /status queries."""
        elapsed = int(time.monotonic() - self._started)
        lines = [f"\u23f3 Working ({elapsed}s)"]

        if self.current_phase == "thinking":
            lines.append(
                f"\u25cf Thinking ({self.current_model}, step {self.current_iteration})"
            )
        elif self.current_phase == "tool":
            lines.append(f"\u25c6 Running: {self.current_tool}")
        elif self.current_phase == "streaming":
            lines.append("\u25cf Writing response...")
        elif self.current_phase == "team":
            lines.append("\u25cf Team mode active")

        # Specialist grid
        if self._team_specialists:
            lines.append("")
            for name, state in self._team_specialists.items():
                lines.append(_format_specialist_line(name, state))

        if self.tool_log:
            recent = self.tool_log[-4:]
            lines.append("")
            lines.append("Recent:")
            for entry in recent:
                lines.append(f"  {entry}")

        return "\n".join(lines)

    def _build_status_text(self) -> str:
        """Build the inline status message for live editing."""
        elapsed = int(time.monotonic() - self._started)

        if not self._team_specialists:
            # Simple mode — single line
            if self.current_phase == "thinking":
                return (
                    f"\u23f3 Thinking ({self.current_model}, "
                    f"step {self.current_iteration})... ({elapsed}s)"
                )
            if self.current_phase == "tool":
                return f"\u23f3 Running: {self.current_tool} ({elapsed}s)"
            if self.current_phase == "streaming":
                return f"\u23f3 Writing response... ({elapsed}s)"
            return f"\u23f3 Working... ({elapsed}s)"

        # Team mode — specialist grid
        lines = [f"\u23f3 Team working ({elapsed}s)", ""]
        for name, state in self._team_specialists.items():
            lines.append(_format_specialist_line(name, state))
        return "\n".join(lines)

    async def _update_status(self, force: bool = False) -> None:
        """Edit the status message in-place with throttling.

        Args:
            force: Skip throttle check (for important events like done).
        """
        now = time.monotonic()
        if not force and (now - self._last_edit_time) < _EDIT_THROTTLE_S:
            return  # Throttled — skip this update

        text = self._build_status_text()
        try:
            if self._status_msg:
                await self._status_msg.edit_text(text)
            else:
                self._status_msg = await self._bot.send_message(
                    chat_id=self._chat_id, text=text,
                )
            self._last_edit_time = now
        except Exception:
            pass  # Telegram edit can fail if text unchanged

    async def _delete_status(self) -> None:
        if self._status_msg:
            try:
                await self._status_msg.delete()
            except Exception:
                pass
            self._status_msg = None

    async def on_approval_request(
        self, skill_name: str, arguments: dict
    ) -> bool:
        # Auto-approve in Telegram (no interactive prompt available)
        return True

    async def on_event(self, event: AgentEvent) -> None:
        kind = event.kind

        if kind == "llm_call":
            self.current_phase = "thinking"
            self.current_model = event.metadata.get("model", "?")
            self.current_iteration = event.metadata.get("iteration", 1)
            await self._update_status()

        elif kind == "tool_call":
            self.current_phase = "tool"
            self.current_tool = event.detail
            self.tool_log.append(event.detail)
            await self._update_status()

        elif kind == "tool_result":
            self.tool_log.append(f"\u2713 {event.detail}")

        elif kind == "team_delegate":
            self.current_phase = "team"
            self.specialists_active.append(event.detail)
            await self._update_status()

        elif kind == "team_start":
            self.current_phase = "team"
            specialists = event.metadata.get("specialists", [])
            self._team_specialists = {
                name: {
                    "status": "queued", "start_time": None,
                    "duration_ms": 0, "tools_used": [], "error": None,
                }
                for name in specialists
            }
            await self._update_status(force=True)

        elif kind == "specialist_start":
            name = event.metadata.get("specialist", "?")
            if name in self._team_specialists:
                self._team_specialists[name]["status"] = "running"
                self._team_specialists[name]["start_time"] = time.monotonic()
            await self._update_status()

        elif kind == "specialist_thinking":
            # Update specialist status with iteration info
            name = event.metadata.get("specialist", "?")
            iteration = event.metadata.get("iteration", 1)
            if name in self._team_specialists:
                self._team_specialists[name]["iteration"] = iteration
            await self._update_status()

        elif kind == "specialist_tool":
            name = event.metadata.get("specialist", "?")
            tool = event.metadata.get("tool", "?")
            if name in self._team_specialists:
                self._team_specialists[name].setdefault("tools_used", []).append(tool)
            self.tool_log.append(f"{name} \u2192 {tool}")
            await self._update_status()

        elif kind == "specialist_done":
            name = event.metadata.get("specialist", "?")
            duration_ms = event.metadata.get("duration_ms", 0)
            success = event.metadata.get("success", True)
            tools = event.metadata.get("tools_used", [])
            error = event.metadata.get("error")
            if name in self._team_specialists:
                self._team_specialists[name]["status"] = (
                    "done" if success else "error"
                )
                self._team_specialists[name]["duration_ms"] = duration_ms
                self._team_specialists[name]["tools_used"] = tools
                self._team_specialists[name]["error"] = error
            await self._update_status(force=True)

        elif kind == "team_merge":
            self.current_phase = "merging"
            await self._update_status(force=True)

        elif kind == "work_summary":
            # Send structured completion summary before the response
            summary = event.metadata.get("summary")
            if summary:
                text = format_summary_telegram(summary)
                try:
                    await self._bot.send_message(
                        chat_id=self._chat_id, text=text,
                    )
                except Exception:
                    pass

        elif kind == "token":
            self.current_phase = "streaming"

        elif kind == "done":
            self.busy = False
            await self._delete_status()


def _format_specialist_line(name: str, state: dict) -> str:
    """Format a single specialist status line for Telegram."""
    status = state.get("status", "queued")

    icon_map = {
        "queued": "\u25cb", "running": "\u25cf",
        "done": "\u2713", "error": "\u2717",
    }
    icon = icon_map.get(status, "\u25cb")

    # Timing
    timing = ""
    if status == "running" and state.get("start_time"):
        elapsed = time.monotonic() - state["start_time"]
        timing = f" {elapsed:.0f}s"
        iteration = state.get("iteration")
        if iteration:
            timing += f" step {iteration}"
    elif state.get("duration_ms"):
        timing = f" {state['duration_ms'] / 1000:.1f}s"

    # Tools
    tools = state.get("tools_used", [])
    tools_str = f" \u2014 {', '.join(tools[-3:])}" if tools else ""

    # Error
    error = state.get("error")
    err_str = f" ({error})" if error and status == "error" else ""

    return f"  {icon} {name} ({status}{timing}){tools_str}{err_str}"


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
    def __init__(
        self, token: str, agent: Agent, config: Config, lane_queue=None,
    ) -> None:
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
        self._app.add_handler(
            CommandHandler("status", self._handle_status_cmd),
        )
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
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /status command — show what agent is doing."""
        chat_id = str(update.effective_chat.id)
        cb = self._active_callbacks.get(chat_id)
        if cb and cb.busy:
            await update.message.reply_text(cb.get_status_report())
        else:
            await update.message.reply_text("Idle \u2014 waiting for your message.")

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
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
            await update.message.reply_text(
                "\U0001f4e5 Queued \u2014 will process after current task."
            )
            return

        # MVP: all Telegram messages routed to "default" user
        user_id = "default"
        logger.info("Telegram message from chat %s: %s", chat_id, text[:100])

        await self._process_and_reply(update, chat_id, user_id, text)

        # Process queued messages
        queued = self._pending_messages.pop(chat_id, [])
        for queued_text in queued:
            await self._process_and_reply(
                update, chat_id, user_id, queued_text,
            )

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
            logger.info(
                "Telegram response to chat %s: %s", chat_id, response[:100],
            )

            # Clean up status message
            await callback._delete_status()

            # Split long messages for Telegram's 4096 char limit
            if len(response) <= 4096:
                await update.message.reply_text(response)
            else:
                for i in range(0, len(response), 4096):
                    await update.message.reply_text(response[i : i + 4096])
        except Exception as e:
            logger.error(
                "Telegram handler error for chat %s: %s",
                chat_id, e, exc_info=True,
            )
            await callback._delete_status()
            try:
                await update.message.reply_text(
                    "Sorry, something went wrong. Please try again."
                )
            except Exception:
                logger.error("Failed to send error reply to chat %s", chat_id)
        finally:
            callback.busy = False
            self._active_callbacks.pop(chat_id, None)

    async def _handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await update.message.reply_text(
            "Hey! I'm Claw, your AI assistant.\n\n"
            "Send me a message to chat.\n"
            "While I'm working, type /status or \"what's happening\" "
            "to see progress with specialist details."
        )

    async def send_message(
        self, external_user_id: str, message: OutboundMessage,
    ) -> None:
        if self._app is None:
            raise RuntimeError("Telegram adapter not started")
        await self._app.bot.send_message(
            chat_id=int(external_user_id), text=message.text,
        )

    @staticmethod
    async def verify_token(token: str) -> dict | None:
        """Call Telegram getMe API to verify token. Returns bot info or None."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{token}/getMe"
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data["result"]
        return None
