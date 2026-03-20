"""Telegram channel adapter with clean, minimal notification UX.

Single status message edited in-place, deleted on completion.
Response sent with tiny inline footer. No spam.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time

import telegram.error
from telegram import Update
from telegram.constants import ChatAction
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
_EDIT_THROTTLE_S = 3.0

# Delay before showing status message (skip for fast responses)
_STATUS_DELAY_S = 2.0

# Typing indicator interval
_TYPING_INTERVAL_S = 4.0


class _TelegramCallback:
    """Tracks agent status, sends typing indicator and one edited status message.

    Lifecycle:
    1. Typing indicator starts immediately
    2. Status message appears after 2s delay (if still working)
    3. Status message edited in-place (throttled at 3s)
    4. On completion: delete status, send response with inline footer
    """

    def __init__(self, bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._status_msg = None
        self._started = time.monotonic()
        self._last_edit_time: float = 0.0
        # Typing keepalive task
        self._typing_task: asyncio.Task | None = None
        # Delayed status message task
        self._status_delay_task: asyncio.Task | None = None
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
        # Stats counters
        self.llm_call_count = 0
        self.tool_count = 0
        self.total_tokens = 0
        # Work summary (stored for footer, not sent separately)
        self._work_summary = None

    # ── Typing indicator ──────────────────────────────────────────────

    async def _start_typing(self) -> None:
        """Start background typing indicator (every 4s)."""
        if self._typing_task and not self._typing_task.done():
            return

        async def _keepalive():
            try:
                while True:
                    try:
                        await self._bot.send_chat_action(
                            chat_id=self._chat_id,
                            action=ChatAction.TYPING,
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(_TYPING_INTERVAL_S)
            except asyncio.CancelledError:
                pass

        self._typing_task = asyncio.create_task(_keepalive())

    async def _stop_typing(self) -> None:
        """Cancel the typing keepalive task."""
        if self._typing_task and not self._typing_task.done():
            self._typing_task.cancel()
            try:
                await self._typing_task
            except (asyncio.CancelledError, Exception):
                pass
        self._typing_task = None

    # ── Delayed status message ────────────────────────────────────────

    def _schedule_status_message(self) -> None:
        """Show status message after 2s delay (skipped for fast responses)."""
        if self._status_delay_task and not self._status_delay_task.done():
            return

        async def _delayed_send():
            try:
                await asyncio.sleep(_STATUS_DELAY_S)
                if self.busy:
                    await self._update_status(force=True)
            except asyncio.CancelledError:
                pass

        self._status_delay_task = asyncio.create_task(_delayed_send())

    def _cancel_status_delay(self) -> None:
        """Cancel the delayed status message if it hasn't fired yet."""
        if self._status_delay_task and not self._status_delay_task.done():
            self._status_delay_task.cancel()
        self._status_delay_task = None

    # ── Status report ─────────────────────────────────────────────────

    def get_status_report(self) -> str:
        """Build a detailed status report for /status queries."""
        elapsed = int(time.monotonic() - self._started)

        if self.current_phase == "thinking":
            header = f"\U0001f504 Working ({elapsed}s)"
            detail = f"\n\n\U0001f9e0 {self.current_model}, step {self.current_iteration}"
        elif self.current_phase == "tool":
            header = f"\U0001f504 Working ({elapsed}s)"
            detail = f"\n\n\U0001f527 Running: {self.current_tool}"
        elif self.current_phase == "streaming":
            header = f"\U0001f504 Working ({elapsed}s)"
            detail = "\n\n\u270d\ufe0f Writing response..."
        elif self.current_phase == "merging":
            header = f"\U0001f504 Working ({elapsed}s)"
            detail = "\n\n\U0001f500 Merging results..."
        elif self.current_phase == "team":
            header = f"\U0001f916 Team ({elapsed}s)"
            detail = ""
        else:
            header = f"\U0001f504 Working ({elapsed}s)"
            detail = ""

        lines = [header]
        if detail:
            lines.append(detail)

        # Specialist grid
        if self._team_specialists:
            lines.append("")
            for name, state in self._team_specialists.items():
                lines.append(_format_specialist_line(name, state))

        # Recent tools
        if self.tool_log:
            recent = self.tool_log[-4:]
            lines.append("")
            lines.append("Recent: " + ", ".join(
                entry.split(" ", 1)[-1] if " " in entry else entry
                for entry in recent
            ))

        lines.append(f"\n\U0001f4ca {self.llm_call_count} LLM \u2502 {self.total_tokens:,} tokens")
        return "\n".join(lines)

    # ── Status message (edited in-place) ──────────────────────────────

    def _build_status_text(self) -> str:
        """Build the inline status message for live editing."""
        elapsed = int(time.monotonic() - self._started)

        if not self._team_specialists:
            # Simple mode
            lines = [f"\U0001f504 Working ({elapsed}s)"]

            if self.current_phase == "thinking":
                lines.append(
                    f"\n\U0001f9e0 {self.current_model}, step {self.current_iteration}"
                )
            elif self.current_phase == "tool":
                lines.append(f"\U0001f527 {self.current_tool}")
            elif self.current_phase == "streaming":
                lines.append("\u270d\ufe0f Writing response...")
            elif self.current_phase == "merging":
                lines.append("\U0001f500 Merging results...")

            lines.append(f"\n\U0001f4ca {self.llm_call_count} LLM \u2502 {self.total_tokens:,} tokens")
            return "\n".join(lines)

        # Team mode — specialist grid
        lines = [f"\U0001f916 Team ({elapsed}s)", ""]
        for name, state in self._team_specialists.items():
            lines.append(_format_specialist_line(name, state))
        lines.append(f"\n\U0001f4ca {self.llm_call_count} LLM \u2502 {self.total_tokens:,} tokens")
        return "\n".join(lines)

    async def _update_status(self, force: bool = False) -> None:
        """Edit the status message in-place with throttling."""
        now = time.monotonic()
        if not force and (now - self._last_edit_time) < _EDIT_THROTTLE_S:
            return

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
        """Delete the status message."""
        self._cancel_status_delay()
        if self._status_msg:
            try:
                await self._status_msg.delete()
            except Exception:
                pass
            self._status_msg = None

    # ── Footer builder ────────────────────────────────────────────────

    def _build_footer(self) -> str:
        """Build inline footer for response message."""
        elapsed_s = time.monotonic() - self._started
        parts = [f"\u2705 {elapsed_s:.1f}s"]
        if self.llm_call_count:
            parts.append(f"{self.llm_call_count} LLM")
        if self.total_tokens:
            parts.append(f"{self.total_tokens:,} tokens")
        return " \u2502 ".join(parts)

    def _build_error_footer(self) -> str:
        """Build footer for error messages."""
        elapsed_s = time.monotonic() - self._started
        parts = [f"{elapsed_s:.1f}s"]
        if self.llm_call_count:
            parts.append(f"{self.llm_call_count} LLM")
        return " \u2502 ".join(parts)

    # ── Event handlers ────────────────────────────────────────────────

    async def on_approval_request(
        self, skill_name: str, arguments: dict
    ) -> bool:
        return True  # Auto-approve in Telegram

    async def on_event(self, event: AgentEvent) -> None:
        kind = event.kind
        display = event.metadata.get("display_name", event.detail)

        if kind == "llm_call":
            self.current_phase = "thinking"
            self.current_model = event.metadata.get("model", "?")
            self.current_iteration = event.metadata.get("iteration", 1)
            self.llm_call_count += 1
            await self._update_status()

        elif kind == "tokens":
            self.total_tokens += event.metadata.get("total", 0)

        elif kind == "tool_call":
            self.current_phase = "tool"
            self.current_tool = display
            self.tool_log.append(f"\U0001f527 {display}")
            await self._update_status()

        elif kind == "tool_result":
            self.tool_log.append(f"\u2705 {display}")
            self.tool_count += 1
            await self._update_status()

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

        elif kind == "background_done":
            task_name = event.metadata.get("name", "")
            result = event.metadata.get("result", "")
            if len(result) > 3000:
                result = result[:3000] + "\n\n[truncated]"
            text = f"\u2705 Background task '{task_name}' done\n\n{result}"
            try:
                await _telegram_send_with_retry(
                    lambda: self._bot.send_message(
                        chat_id=self._chat_id, text=text,
                    )
                )
            except Exception:
                pass

        elif kind == "background_failed":
            task_name = event.metadata.get("name", "")
            error = event.metadata.get("error", "unknown error")
            text = f"\u274c Background task '{task_name}' failed: {error[:200]}"
            try:
                await _telegram_send_with_retry(
                    lambda: self._bot.send_message(
                        chat_id=self._chat_id, text=text,
                    )
                )
            except Exception:
                pass

        elif kind == "work_summary":
            # Store summary for footer — don't send separately
            self._work_summary = event.metadata.get("summary")

        elif kind == "attachment":
            data = event.metadata.get("data", b"")
            media_type = event.metadata.get("media_type", "")
            caption = event.detail[:1024] if event.detail else None
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
        """Handle /status command."""
        chat_id = str(update.effective_chat.id)
        cb = self._active_callbacks.get(chat_id)
        if cb and cb.busy:
            await update.message.reply_text(cb.get_status_report())
        else:
            # Idle — show uptime + processed count if dashboard available
            idle_lines = ["\U0001f4a4 Idle \u2014 waiting for your message."]
            if self._server_dashboard:
                up = self._server_dashboard.uptime_s
                hours, rem = divmod(up, 3600)
                minutes = rem // 60
                up_str = f"{hours}h{minutes}m" if hours else f"{minutes}m"
                done = self._server_dashboard.total_processed
                idle_lines.append(f"\nUp {up_str} \u2502 {done} done today")
            await update.message.reply_text("\n".join(idle_lines))

    def _is_allowed(self, chat_id: str) -> bool:
        if not self._allowed_chats:
            return True
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
        """Run agent and reply with result. Clean lifecycle:
        typing → status (delayed) → response with footer."""
        callback = _TelegramCallback(self._app.bot, int(chat_id))
        self._active_callbacks[chat_id] = callback

        # Phase 1: Acknowledge — typing indicator immediately
        await callback._start_typing()
        # Delayed status message (only shows if task takes >2s)
        callback._schedule_status_message()

        # Wrap with server dashboard for terminal visibility
        effective_cb = callback
        if self._server_dashboard:
            self._server_dashboard.register_request(chat_id, text)
            from lazyclaw.runtime.callbacks import MultiCallback
            effective_cb = MultiCallback(
                callback, self._server_dashboard.make_request_cb(chat_id),
            )

        # Inject channel context
        channel_hint = (
            f"\n\n[Channel: Telegram | Chat ID: {chat_id} | "
            f"You can send images via browser(action=\"screenshot\") "
            f"\u2014 screenshots are auto-forwarded to this chat.]"
        )
        enriched_text = text + channel_hint

        try:
            logger.debug("Telegram: awaiting agent response for chat %s", chat_id)
            if self._lane_queue:
                response = await self._lane_queue.enqueue(
                    user_id, enriched_text, callback=effective_cb,
                )
            else:
                response = await self._agent.process_message(
                    user_id, enriched_text, callback=effective_cb,
                )
            logger.debug("Telegram: got response for chat %s (len=%d)", chat_id, len(response or ""))
            if not response or not response.strip():
                response = "Sorry, I couldn't process that. Please try again."
            logger.info(
                "Telegram response to chat %s: %s", chat_id, response[:100],
            )

            # Phase 3: Done — stop typing, delete status, send response with footer
            await callback._stop_typing()
            await callback._delete_status()

            footer = callback._build_footer()
            full_response = f"{response}\n\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n{footer}"

            # Split long messages (4096 char limit), footer on last chunk
            if len(full_response) <= 4096:
                await _telegram_send_with_retry(
                    lambda: update.message.reply_text(full_response)
                )
            else:
                # Send response chunks, footer on last one
                resp_chunks = []
                for i in range(0, len(response), 4000):
                    resp_chunks.append(response[i : i + 4000])
                for i, chunk in enumerate(resp_chunks):
                    if i == len(resp_chunks) - 1:
                        # Last chunk gets footer
                        chunk_with_footer = f"{chunk}\n\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n{footer}"
                        await _telegram_send_with_retry(
                            lambda c=chunk_with_footer: update.message.reply_text(c)
                        )
                    else:
                        await _telegram_send_with_retry(
                            lambda c=chunk: update.message.reply_text(c)
                        )
            logger.debug("Telegram: reply sent to chat %s", chat_id)

        except Exception as e:
            logger.error(
                "Telegram handler error for chat %s: %s",
                chat_id, e, exc_info=True,
            )
            await callback._stop_typing()
            await callback._delete_status()
            try:
                error_footer = callback._build_error_footer()
                error_msg = (
                    f"\u274c Something went wrong.\n\n"
                    f"{str(e)[:200]}\n\n"
                    f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n{error_footer}"
                )
                await _telegram_send_with_retry(
                    lambda: update.message.reply_text(error_msg)
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

        # First /start claims admin
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
