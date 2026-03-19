"""Telegram channel adapter with rich agent status notifications.

Provides real-time specialist progress, structured completion summaries,
edit throttling to stay within Telegram rate limits, and image attachments.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time

import telegram.error
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

# Retry config for network-flaky Telegram sends
_SEND_MAX_RETRIES = 3
_SEND_RETRY_BASE_DELAY = 2.0  # seconds


async def _telegram_send_with_retry(coro_factory, max_retries=_SEND_MAX_RETRIES):
    """Retry a Telegram send on transient network errors.

    *coro_factory* is a zero-arg callable that returns a new awaitable each
    time (lambdas work: ``lambda: bot.send_message(...)``).
    """
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except (telegram.error.NetworkError, telegram.error.TimedOut) as exc:
            if attempt < max_retries - 1:
                delay = _SEND_RETRY_BASE_DELAY * (attempt + 1)
                logger.warning(
                    "Telegram send retry %d/%d: %s (waiting %.1fs)",
                    attempt + 1, max_retries, exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                raise

# Minimum seconds between Telegram message edits (rate limit protection)
_EDIT_THROTTLE_S = 2.0

# Approximate cost per LLM call (~500 output tokens) by model keyword
_MODEL_COST_MAP = {
    "mini": 0.0003, "haiku": 0.0004, "gpt-4o-mini": 0.0003,
    "gpt-4o": 0.005, "sonnet": 0.005, "gpt-5": 0.01,
    "opus": 0.02, "o3": 0.02, "o4": 0.02,
}


def _estimate_llm_cost(model: str) -> float:
    """Rough per-call cost estimate. NanoClaw-inspired cost tracking."""
    lower = (model or "").lower()
    for key, cost in _MODEL_COST_MAP.items():
        if key in lower:
            return cost
    return 0.003  # conservative default


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
        # NanoClaw-style stats counters
        self.llm_call_count = 0
        self.tool_count = 0
        self.estimated_cost = 0.0

    def get_status_report(self) -> str:
        """Build a detailed status report for /status queries."""
        elapsed = int(time.monotonic() - self._started)
        lines = [f"\u23f3 Working ({elapsed}s)"]

        if self.current_phase == "thinking":
            lines.append(
                f"\U0001f9e0 Thinking ({self.current_model}, step {self.current_iteration})"
            )
        elif self.current_phase == "tool":
            lines.append(f"\U0001f527 Running: {self.current_tool}")
        elif self.current_phase == "streaming":
            lines.append("\u270d\ufe0f Writing response...")
        elif self.current_phase == "team":
            lines.append("\U0001f916 Team mode active")
        elif self.current_phase == "merging":
            lines.append("\U0001f500 Merging results...")

        # Specialist grid
        if self._team_specialists:
            lines.append("")
            for name, state in self._team_specialists.items():
                lines.append(_format_specialist_line(name, state))

        if self.tool_log:
            recent = self.tool_log[-4:]
            lines.append("")
            for entry in recent:
                lines.append(f"  {entry}")

        return "\n".join(lines)

    def _build_stats_line(self) -> str:
        """Stats footer: LLM calls + tools (cost only in /usage + server)."""
        parts = [f"\U0001f4ca {self.llm_call_count} LLM"]
        if self.tool_count:
            parts.append(f"{self.tool_count} tools")
        return " | ".join(parts)

    def _build_status_text(self) -> str:
        """Build the inline status message for live editing."""
        elapsed = int(time.monotonic() - self._started)
        stats = self._build_stats_line()

        if not self._team_specialists:
            # Simple mode
            if self.current_phase == "thinking":
                base = (
                    f"\U0001f9e0 Thinking ({self.current_model}, "
                    f"step {self.current_iteration})... ({elapsed}s)"
                )
            elif self.current_phase == "tool":
                base = f"\U0001f527 Running: {self.current_tool} ({elapsed}s)"
            elif self.current_phase == "streaming":
                base = f"\u270d\ufe0f Writing response... ({elapsed}s)"
            elif self.current_phase == "merging":
                base = f"\U0001f500 Merging results... ({elapsed}s)"
            else:
                base = f"\u23f3 Working... ({elapsed}s)"
            return f"{base}\n\n{stats}"

        # Team mode — specialist grid
        lines = [f"\U0001f916 Team working ({elapsed}s)", ""]
        for name, state in self._team_specialists.items():
            lines.append(_format_specialist_line(name, state))
        lines.append(f"\n{stats}")
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

    async def _send_permanent(self, text: str) -> None:
        """Send a permanent (non-edited) message. Fire-and-forget safe."""
        try:
            await _telegram_send_with_retry(
                lambda: self._bot.send_message(
                    chat_id=self._chat_id, text=text,
                )
            )
        except Exception:
            pass

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
        display = event.metadata.get("display_name", event.detail)

        if kind == "llm_call":
            self.current_phase = "thinking"
            self.current_model = event.metadata.get("model", "?")
            self.current_iteration = event.metadata.get("iteration", 1)
            self.llm_call_count += 1
            self.estimated_cost += _estimate_llm_cost(self.current_model)
            await self._update_status()

        elif kind == "tool_call":
            self.current_phase = "tool"
            self.current_tool = display
            self.tool_log.append(f"\U0001f527 {display}")
            await self._update_status()

        elif kind == "tool_result":
            self.tool_log.append(f"\u2705 {display}")
            self.tool_count += 1
            # Send permanent message for non-trivial tools (fire-and-forget)
            _TRIVIAL_TOOLS = {"get_time", "calculate", "memory_recall"}
            if display not in _TRIVIAL_TOOLS:
                asyncio.create_task(self._send_permanent(
                    f"\U0001f527 {display} \u2713"
                ))

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

        elif kind == "team_merge":
            self.current_phase = "merging"
            await self._update_status(force=True)

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
            # Permanent message for specialist completion
            if success:
                tools_str = ", ".join(tools[-3:]) if tools else "direct"
                text = f"\u2705 {name} ({duration_ms / 1000:.1f}s) \u2014 {tools_str}"
            else:
                text = f"\u274c {name} \u2014 {str(error)[:60]}"
            asyncio.create_task(self._send_permanent(text))

        elif kind == "background_done":
            task_name = event.metadata.get("name", "")
            result = event.metadata.get("result", "")
            if len(result) > 3000:
                result = result[:3000] + "\n\n[truncated]"
            text = f"\u2705 Background task '{task_name}' done\n\n{result}"
            asyncio.create_task(self._send_permanent(text))

        elif kind == "background_failed":
            task_name = event.metadata.get("name", "")
            error = event.metadata.get("error", "unknown error")
            text = f"\u274c Background task '{task_name}' failed: {error[:200]}"
            asyncio.create_task(self._send_permanent(text))

        elif kind == "work_summary":
            # Delete status message first to avoid duplicate info
            await self._delete_status()
            summary = event.metadata.get("summary")
            if summary:
                text = format_summary_telegram(summary)
                try:
                    await _telegram_send_with_retry(
                        lambda: self._bot.send_message(
                            chat_id=self._chat_id, text=text,
                        )
                    )
                except Exception:
                    pass

        elif kind == "attachment":
            # Deliver binary attachments (screenshots, images) inline
            data = event.metadata.get("data", b"")
            media_type = event.metadata.get("media_type", "")
            caption = event.detail[:1024] if event.detail else None  # Telegram limit
            if data and media_type.startswith("image/"):
                try:
                    await _telegram_send_with_retry(
                        lambda: self._bot.send_photo(
                            chat_id=self._chat_id,
                            photo=io.BytesIO(data),
                            caption=caption,
                        )
                    )
                except Exception as exc:
                    logger.warning("Failed to send photo to chat %s: %s", self._chat_id, exc)

        elif kind == "token":
            self.current_phase = "streaming"

        elif kind == "done":
            self.busy = False
            await self._delete_status()


def _format_specialist_line(name: str, state: dict) -> str:
    """Format a single specialist status line for Telegram with emoji."""
    status = state.get("status", "queued")

    icon_map = {
        "queued": "\u23f3",        # ⏳
        "running": "\U0001f504",   # 🔄
        "done": "\u2705",          # ✅
        "error": "\u274c",         # ❌
    }
    icon = icon_map.get(status, "\u23f3")

    # Timing
    timing = ""
    if status == "running" and state.get("start_time"):
        elapsed = time.monotonic() - state["start_time"]
        timing = f" {elapsed:.0f}s"
        iteration = state.get("iteration")
        if iteration:
            timing += f", step {iteration}"
    elif state.get("duration_ms"):
        timing = f" {state['duration_ms'] / 1000:.1f}s"

    # Tools
    tools = state.get("tools_used", [])
    tools_str = f" \u2014 {', '.join(tools[-3:])}" if tools else ""

    # Error
    error = state.get("error")
    err_str = f" ({error})" if error and status == "error" else ""

    return f"{icon} {name} ({status}{timing}){tools_str}{err_str}"


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
        server_dashboard=None,
    ) -> None:
        self._token = token
        self._agent = agent
        self._config = config
        self._lane_queue = lane_queue
        self._server_dashboard = server_dashboard
        self._app = None
        # Track active callback per chat for status queries
        self._active_callbacks: dict[str, _TelegramCallback] = {}
        self._pending_messages: dict[str, list[str]] = {}
        # Admin chat_id — first chat to /start becomes admin
        # Set via TELEGRAM_ADMIN_CHAT env var, or auto-set on first /start
        self._admin_chat_id: str | None = os.environ.get("TELEGRAM_ADMIN_CHAT")
        self._allowed_chats: set[str] = set()
        if self._admin_chat_id:
            self._allowed_chats.add(self._admin_chat_id)

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

    def _is_allowed(self, chat_id: str) -> bool:
        """Check if a chat is allowed to interact with the bot."""
        if not self._allowed_chats:
            return True  # No admin set yet — first /start will claim it
        return chat_id in self._allowed_chats

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.message or not update.message.text:
            return

        chat_id = str(update.effective_chat.id)
        text = update.message.text.strip()
        if not text:
            return

        # Security: only allow admin/authorized chats
        if not self._is_allowed(chat_id):
            logger.warning("Unauthorized Telegram message from chat %s", chat_id)
            await update.message.reply_text(
                "\U0001f512 Not authorized. Ask the admin to add your chat ID."
            )
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

        # Wrap with server dashboard for terminal visibility
        effective_cb = callback
        if self._server_dashboard:
            self._server_dashboard.register_request(chat_id, text)
            from lazyclaw.runtime.callbacks import MultiCallback
            effective_cb = MultiCallback(
                callback, self._server_dashboard.make_request_cb(chat_id),
            )

        # Inject channel context so the agent knows it's on Telegram
        # and can send photos/messages to this chat
        channel_hint = (
            f"\n\n[Channel: Telegram | Chat ID: {chat_id} | "
            f"You can send images via see_browser — screenshots are auto-forwarded to this chat.]"
        )
        enriched_text = text + channel_hint

        try:
            if self._lane_queue:
                response = await self._lane_queue.enqueue(
                    user_id, enriched_text, callback=effective_cb,
                )
            else:
                response = await self._agent.process_message(
                    user_id, enriched_text, callback=effective_cb,
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
                await _telegram_send_with_retry(
                    lambda: update.message.reply_text(response)
                )
            else:
                for i in range(0, len(response), 4096):
                    chunk = response[i : i + 4096]
                    await _telegram_send_with_retry(
                        lambda c=chunk: update.message.reply_text(c)
                    )
        except Exception as e:
            logger.error(
                "Telegram handler error for chat %s: %s",
                chat_id, e, exc_info=True,
            )
            await callback._delete_status()
            try:
                await _telegram_send_with_retry(
                    lambda: update.message.reply_text(
                        "Sorry, something went wrong. Please try again."
                    )
                )
            except Exception:
                logger.error("Failed to send error reply to chat %s", chat_id)
        finally:
            callback.busy = False
            self._active_callbacks.pop(chat_id, None)
            if self._server_dashboard:
                self._server_dashboard.unregister_request(chat_id)

    async def _handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        chat_id = str(update.effective_chat.id)

        # First /start claims admin — secure the bot to this chat
        if not self._admin_chat_id:
            self._admin_chat_id = chat_id
            self._allowed_chats.add(chat_id)
            logger.info("Telegram admin claimed by chat %s", chat_id)
            await update.message.reply_text(
                "\U0001f512 Admin locked to this chat.\n\n"
                "Hey! I'm Claw, your AI assistant.\n"
                "Send me a message to chat.\n"
                "While I'm working, type /status or \"what's happening\" "
                "to see progress with specialist details."
            )
            return

        if chat_id not in self._allowed_chats:
            logger.warning("Unauthorized /start from chat %s", chat_id)
            await update.message.reply_text(
                "\U0001f512 Not authorized. This bot is locked to another chat."
            )
            return

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
