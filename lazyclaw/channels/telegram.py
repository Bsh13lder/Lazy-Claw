"""Telegram channel adapter with clean, minimal notification UX.

Single status message edited in-place, deleted on completion.
Response sent with tiny inline footer. No spam.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import time

import telegram.error
from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from lazyclaw.channels.base import ChannelAdapter, OutboundMessage
from lazyclaw.config import Config
from lazyclaw.runtime.agent import Agent
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.session_resolver import get_primary_session_id

logger = logging.getLogger(__name__)

# Global reference for webhook/external access
_telegram_adapter_instance: "TelegramAdapter | None" = None


def get_telegram_adapter() -> "TelegramAdapter | None":
    """Return the active TelegramAdapter (set during start)."""
    return _telegram_adapter_instance


# Retry config for network-flaky Telegram sends
_SEND_MAX_RETRIES = 3
_SEND_RETRY_BASE_DELAY = 2.0  # seconds


def _friendly_model_name(model: str | None) -> str:
    """Condense a raw model id into a short human-facing label."""
    if not model:
        return ""
    m = model.lower()
    if "sonnet-4-6" in m or "sonnet-4.6" in m:
        return "Sonnet 4.6"
    if "haiku-4-5" in m or "haiku-4.5" in m:
        return "Haiku 4.5"
    if "opus" in m:
        return "Opus"
    if "sonnet" in m:
        return "Sonnet"
    if "haiku" in m:
        return "Haiku"
    if "gemma" in m or "e2b" in m:
        return "Gemma E2B"
    if m == "claude-cli":
        return "Claude CLI"
    if m in ("unknown", "error"):
        return ""
    # Last-ditch: strip known vendor/date noise
    return model.replace("claude-", "").split("-2025")[0][:24]


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


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting for plain-text Telegram messages."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1 (\2)', text)
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    return text


def _has_html_links(text: str) -> bool:
    """Check if text contains HTML anchor tags."""
    return '<a href="' in text


def _prepare_html(text: str) -> str:
    """Escape HTML entities in text but preserve <a> tags for Telegram HTML mode."""
    # Extract <a> tags and replace with placeholders
    _link_re = re.compile(r'<a\s+href="[^"]*">[^<]*</a>')
    placeholders: list[str] = []
    def _save_link(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"\x00LINK{len(placeholders) - 1}\x00"
    text = _link_re.sub(_save_link, text)
    # Escape HTML entities in the remaining text
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Restore <a> tags
    for i, link in enumerate(placeholders):
        text = text.replace(f"\x00LINK{i}\x00", link)
    return text


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
        # Final model + ECO fallback (set on the "done" event, used in footer)
        self.final_model: str = ""
        self.fallback_reason: str | None = None
        # Work summary (stored for footer, not sent separately)
        self._work_summary = None
        # Fast dispatch flag — background task still running after process_message returns
        self.dispatched = False
        # Help request coordination (human-in-the-loop)
        self._help_future: asyncio.Future | None = None

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
                    except Exception as exc:
                        logger.debug("Typing indicator send failed: %s", exc)
                    await asyncio.sleep(_TYPING_INTERVAL_S)
            except asyncio.CancelledError:
                pass  # intentional: typing keepalive cancelled on stop

        self._typing_task = asyncio.create_task(_keepalive())

    async def _stop_typing(self) -> None:
        """Cancel the typing keepalive task."""
        if self._typing_task and not self._typing_task.done():
            self._typing_task.cancel()
            try:
                await self._typing_task
            except (asyncio.CancelledError, Exception):
                pass  # intentional: cancelled task cleanup, exception is expected
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
                pass  # intentional: delay cancelled if response arrived quickly

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
        """Edit the status message in-place with throttling.

        The initial send is muted (disable_notification=True) so tool-switch
        progress never pings the user's phone — only the final reply does.
        Subsequent edits inherit the silent creation (Telegram only notifies
        on new messages, not edits).
        """
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
                    disable_notification=True,
                )
            self._last_edit_time = now
        except Exception as exc:
            logger.debug("Status message edit failed (ok if text unchanged): %s", exc)

    async def _delete_status(self) -> None:
        """Delete the status message."""
        self._cancel_status_delay()
        if self._status_msg:
            try:
                await self._status_msg.delete()
            except Exception as exc:
                logger.debug("Status message delete failed: %s", exc)
            self._status_msg = None

    # ── Footer builder ────────────────────────────────────────────────

    def _build_footer(self) -> str:
        """Build inline footer for response message.

        Shows the final model used + an explicit fallback chip if the ECO
        router had to swap away from the user's configured brain (e.g. 529
        overload → Claude CLI). Surfacing this prevents the silent "why am
        I suddenly getting Haiku?" confusion.
        """
        elapsed_s = time.monotonic() - self._started
        parts = [f"\u2705 {elapsed_s:.1f}s"]
        if self.llm_call_count:
            parts.append(f"{self.llm_call_count} LLM")
        if self.total_tokens:
            parts.append(f"{self.total_tokens:,} tokens")
        model_label = _friendly_model_name(self.final_model)
        if self.fallback_reason:
            reason_label = {
                "overloaded": "Sonnet overloaded",
                "auth": "auth error",
                "cli_failed": "CLI failed",
                "local_failed": "local model failed",
                "worker_failed": "worker failed",
            }.get(self.fallback_reason, self.fallback_reason)
            chip = f"\u26a0\ufe0f fallback \u2192 {model_label or '?'} ({reason_label})"
            parts.append(chip)
        elif model_label:
            parts.append(model_label)
        return " \u2502 ".join(parts)

    def _build_error_footer(self) -> str:
        """Build footer for error messages."""
        elapsed_s = time.monotonic() - self._started
        parts = [f"{elapsed_s:.1f}s"]
        if self.llm_call_count:
            parts.append(f"{self.llm_call_count} LLM")
        return " \u2502 ".join(parts)

    # ── Event handlers ────────────────────────────────────────────────

    _DANGEROUS_SKILL_PREFIXES = frozenset({
        "computer", "vault", "delete", "connector",
    })

    async def on_approval_request(
        self, skill_name: str, arguments: dict
    ) -> bool:
        """Auto-approve safe skills in Telegram. Deny dangerous categories."""
        lower_name = skill_name.lower()
        for prefix in self._DANGEROUS_SKILL_PREFIXES:
            if lower_name.startswith(prefix):
                logger.warning("Telegram auto-denied dangerous skill: %s", skill_name)
                return False
        return True

    async def on_help_request(
        self, context: str, needs_browser: bool,
    ) -> str:
        """Send help request to Telegram and wait indefinitely for user reply."""
        msg = f"\U0001f198 I'm stuck: {context}\n\nReply 'ready' to take over"
        if needs_browser:
            msg += " (I'll open the browser for you)"
        msg += ", or 'skip' to let me try something else."

        try:
            await _telegram_send_with_retry(
                lambda: self._bot.send_message(
                    chat_id=self._chat_id, text=msg,
                )
            )
        except Exception as exc:
            logger.warning("Failed to send help request: %s", exc)
            return "skip"

        # Wait indefinitely for user response via Telegram message
        loop = asyncio.get_running_loop()
        self._help_future = loop.create_future()
        try:
            result = await self._help_future
        except asyncio.CancelledError:
            return "skip"
        finally:
            self._help_future = None
        return result

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
            _task_preview = event.metadata.get("instruction", "")
            _label = event.detail
            if _task_preview:
                _label = f"{event.detail}\n\U0001f4cb {_task_preview}"
            self.specialists_active.append(_label)
            await self._update_status(force=True)

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

        elif kind == "fast_dispatch":
            self.dispatched = True
            self.current_phase = "dispatched"

        elif kind == "background_done":
            import html as _html
            from lazyclaw.notifications.telegram_notifier import (
                _format_stats_html,
                _format_tools_html,
            )

            meta = event.metadata or {}
            task_name = _html.escape(meta.get("name", ""))
            result = _strip_markdown(meta.get("result", ""))
            preview = _html.escape(result[:2000])
            if len(result) > 2000:
                preview += "\n[truncated]"

            stats_line = _format_stats_html(meta)
            tools_line = _format_tools_html(meta)
            models = meta.get("models_used")
            model_line = ""
            if models:
                model_line = "Model: " + ", ".join(_html.escape(m) for m in models)

            lines = [f"[done] <b>Background '{task_name}' done</b>"]
            if stats_line:
                lines.append(stats_line)
            if model_line:
                lines.append(model_line)
            if tools_line:
                lines.append(tools_line)
            if preview:
                lines.append("")
                lines.append(f"<pre>{preview}</pre>")

            text = "\n".join(lines)
            try:
                await _telegram_send_with_retry(
                    lambda: self._bot.send_message(
                        chat_id=self._chat_id, text=text,
                        parse_mode="HTML",
                    )
                )
            except Exception as exc:
                logger.warning("Failed to send background_done notification: %s", exc)
            # Signal dispatch complete so adapter can clean up
            self.dispatched = False
            self.busy = False

        elif kind == "background_failed":
            import html as _html

            meta = event.metadata or {}
            task_name = _html.escape(meta.get("name", ""))
            error = _html.escape(meta.get("error", "unknown error")[:300])
            text = (
                f"[error] <b>Background '{task_name}' failed</b>\n\n"
                f"<pre>{error}</pre>"
            )
            try:
                await _telegram_send_with_retry(
                    lambda: self._bot.send_message(
                        chat_id=self._chat_id, text=text,
                        parse_mode="HTML",
                    )
                )
            except Exception as exc:
                logger.warning("Failed to send background_failed notification: %s", exc)
            # Signal dispatch complete so adapter can clean up
            self.dispatched = False
            self.busy = False

        elif kind == "help_response":
            # Forward noVNC remote takeover URL to the user
            novnc_url = event.metadata.get("novnc_url")
            if novnc_url:
                context = event.metadata.get("stuck_context", "Browser control needed")
                is_http = novnc_url.startswith("http://")
                text = f"\U0001f510 Need help: {context}\n\nTap to take control:\n{novnc_url}"
                if is_http:
                    text += "\n\n\u26a0\ufe0f Connection is not encrypted (HTTP). Use on trusted network only."
                try:
                    await _telegram_send_with_retry(
                        lambda: self._bot.send_message(
                            chat_id=self._chat_id, text=text,
                        )
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to send noVNC URL to chat %s: %s",
                        self._chat_id, exc,
                    )

        elif kind == "browser_plan":
            # Show the browsing plan as a brief message — silent, so the
            # user isn't pinged every time the agent replans.
            goal = event.metadata.get("goal", event.detail)
            steps = event.metadata.get("steps", [])
            steps_text = "\n".join(
                f"{i + 1}. {s}" for i, s in enumerate(steps[:5])
            )
            text = f"\U0001f3af Plan: {goal}"
            if steps_text:
                text += f"\n{steps_text}"
            try:
                await _telegram_send_with_retry(
                    lambda: self._bot.send_message(
                        chat_id=self._chat_id, text=text,
                        disable_notification=True,
                    )
                )
            except Exception as exc:
                logger.debug("Failed to send browser_plan: %s", exc)

        elif kind == "browser_action":
            action = event.metadata.get("action", "")
            target = event.metadata.get("target", "")
            step_n = event.metadata.get("step_number", 0)
            total = event.metadata.get("total_steps", 0)
            step_info = f" (step {step_n}/{total})" if total else ""
            label = f"Browser: {action}"
            if target:
                label += f" {target[:60]}"
            label += step_info
            self.current_phase = "browser"
            self.current_tool = label
            self.tool_log.append(f"\U0001f310 {label}")
            await self._update_status()

        elif kind == "browser_verify":
            succeeded = event.metadata.get("succeeded", True)
            evidence = event.metadata.get("evidence", event.detail)
            if succeeded:
                self.tool_log.append(f"\u2705 {evidence[:80]}")
            else:
                self.tool_log.append(f"\u274c {evidence[:80]}")
            await self._update_status()

        elif kind == "browser_progress":
            met = event.metadata.get("requirements_met", 0)
            total = event.metadata.get("total_requirements", 0)
            sources = event.metadata.get("sources_checked", 0)
            gaps = event.metadata.get("gaps", [])
            status = event.metadata.get("status", "gathering")
            if total:
                label = f"\U0001f4ca Research: {met}/{total} found, {sources} source(s)"
            else:
                label = f"\U0001f4ca Research: {sources} source(s) checked"
            if gaps and status == "gathering":
                label += f" — still need: {', '.join(gaps[:2])}"
            self.tool_log.append(label)
            await self._update_status()

        elif kind == "work_summary":
            # Store summary for footer — don't send separately
            self._work_summary = event.metadata.get("summary")

        elif kind == "attachment":
            # Interim screenshots (browser snapshots, etc.) are sent silently
            # so a run that takes 5 screenshots doesn't ping the phone 5 times.
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
                            disable_notification=True,
                        )
                    )
                except Exception as exc:
                    logger.warning("Failed to send photo to chat %s: %s", self._chat_id, exc)

        elif kind == "token":
            self.current_phase = "streaming"

        elif kind == "plan_pending":
            # Business agent gate — show plan + inline Approve/Reject buttons.
            plan_text = event.metadata.get("plan", event.detail) or ""
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "\u2705 Approve", callback_data="plan:approve",
                    ),
                    InlineKeyboardButton(
                        "\u274c Reject", callback_data="plan:reject",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "\u26a1 Approve & trust 5min",
                        callback_data="plan:approve_trust",
                    ),
                ],
            ])
            try:
                await _telegram_send_with_retry(
                    lambda: self._bot.send_message(
                        chat_id=self._chat_id,
                        text=f"\U0001f4cb *Plan — review before I start*\n\n{plan_text}",
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                )
            except Exception as exc:
                logger.warning("Failed to send plan prompt: %s", exc)

        elif kind == "plan_question":
            # Agent wants a clarifying answer before drafting a plan.
            # Use ForceReply so the user's next message is routed to
            # plan_checkpoint.answer_clarification() by _handle_message.
            question = event.metadata.get("question", event.detail) or ""
            try:
                await _telegram_send_with_retry(
                    lambda: self._bot.send_message(
                        chat_id=self._chat_id,
                        text=f"\u2753 {question}",
                        reply_markup=ForceReply(
                            selective=False,
                            input_field_placeholder="Your answer…",
                        ),
                    )
                )
            except Exception as exc:
                logger.warning("Failed to send plan question: %s", exc)

        elif kind == "plan_approved":
            try:
                note = "\u26a1 Auto-approving next plans for 5min" if (
                    event.metadata.get("auto_approve_session")
                ) else "\u25b6\ufe0f Running the plan..."
                await _telegram_send_with_retry(
                    lambda: self._bot.send_message(
                        chat_id=self._chat_id, text=note,
                    )
                )
            except Exception:
                pass

        elif kind == "done":
            self.busy = False
            self.final_model = event.metadata.get("model_used") or self.final_model
            self.fallback_reason = event.metadata.get("fallback_reason")


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


async def resolve_user_id(config, chat_id: str | None = None) -> str:
    """Resolve the LazyClaw user_id for a Telegram chat.

    Priority:
      1. Explicit binding — user whose settings.telegram_chat_id matches chat_id
         (set via /link USERNAME, persisted once per chat).
      2. Legacy fallback — first registered user by created_at.

    Rationale: with multiple users in the DB (admin placeholder + real
    personal account), the old "first user" heuristic routed Telegram to a
    different user_id than the Web UI session, so the two channels read
    different eco settings and appeared to use different models.
    """
    from lazyclaw.db.connection import db_session
    try:
        async with db_session(config) as db:
            if chat_id:
                cursor = await db.execute(
                    "SELECT id FROM users "
                    "WHERE json_extract(settings, '$.telegram_chat_id') = ? "
                    "LIMIT 1",
                    (str(chat_id),),
                )
                row = await cursor.fetchone()
                if row:
                    return row[0]
            cursor = await db.execute(
                "SELECT id FROM users ORDER BY created_at LIMIT 1"
            )
            row = await cursor.fetchone()
            if row:
                return row[0]
    except Exception:
        logger.warning("Failed to resolve primary user_id from DB", exc_info=True)
    return "default"


class TelegramAdapter(ChannelAdapter):
    def __init__(
        self, token: str, agent: Agent, config: Config, lane_queue=None,
        server_dashboard=None, task_runner=None, team_lead=None,
    ) -> None:
        self._token = token
        self._agent = agent
        self._config = config
        self._lane_queue = lane_queue
        self._server_dashboard = server_dashboard
        self._task_runner = task_runner
        self._team_lead = team_lead
        self._app = None
        # Track active callback per chat for status queries
        self._active_callbacks: dict[str, _TelegramCallback] = {}
        self._pending_messages: dict[str, list[str]] = {}
        # user_ids awaiting a password message for recovery phrase generation
        self._pending_recovery: set[str] = set()
        # Admin chat_id — first chat to /start becomes admin
        # Set via TELEGRAM_ADMIN_CHAT env var, or auto-set on first /start
        self._admin_chat_id: str | None = os.environ.get("TELEGRAM_ADMIN_CHAT")
        self._allowed_chats: set[str] = set()
        if self._admin_chat_id:
            self._allowed_chats.add(self._admin_chat_id)

    async def start(self) -> None:
        self._app = ApplicationBuilder().token(self._token).build()

        # Register all admin slash commands (instant, no LLM)
        from lazyclaw.channels.telegram_commands import TelegramCommands
        self._commands = TelegramCommands(
            adapter=self,
            config=self._config,
            agent=self._agent,
            task_runner=self._task_runner,
            team_lead=self._team_lead,
        )
        self._commands.register(self._app)

        # /status still handled here (needs access to active callbacks)
        self._app.add_handler(
            CommandHandler("status", self._handle_status_cmd),
        )
        # Plan-mode inline button callbacks — business agent approval gate.
        self._app.add_handler(
            CallbackQueryHandler(
                self._handle_plan_callback, pattern=r"^plan:",
            )
        )
        # Text messages → agent (must be AFTER command handlers)
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram adapter started")

        global _telegram_adapter_instance
        _telegram_adapter_instance = self

        # Register "/" autocomplete menu with Telegram (must be after start)
        try:
            from lazyclaw.channels.telegram_commands import BOT_COMMANDS
            await self._app.bot.set_my_commands(BOT_COMMANDS)
            logger.info("Telegram command menu registered (%d commands)", len(BOT_COMMANDS))
        except Exception as exc:
            logger.debug("Could not set bot commands menu: %s", exc)

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        logger.info("Telegram adapter stopped")

    async def _handle_plan_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle inline button taps on a plan-approval message."""
        query = update.callback_query
        if query is None:
            return
        await query.answer()  # Stop the Telegram spinner immediately.

        data = (query.data or "").split(":", 1)
        action = data[1] if len(data) == 2 else ""

        # Resolve the LazyClaw user_id (chat-bound via /link, else legacy).
        try:
            cb_chat_id = str(query.message.chat_id) if query.message else None
            lc_user_id = await resolve_user_id(self._config, chat_id=cb_chat_id)
        except Exception as exc:
            logger.warning("Plan callback user resolution failed: %s", exc)
            await query.edit_message_text("\u26a0\ufe0f Could not identify you.")
            return

        from lazyclaw.runtime import plan_checkpoint

        if action == "approve":
            released = plan_checkpoint.approve(
                lc_user_id, reason="approved via Telegram",
            )
            await query.edit_message_text(
                query.message.text_markdown +
                ("\n\n\u2705 _Approved_" if released else "\n\n(no plan pending)"),
                parse_mode="Markdown",
            )
        elif action == "approve_trust":
            released = plan_checkpoint.approve(
                lc_user_id,
                reason="approved + session trust via Telegram",
                auto_approve_session=True,
            )
            await query.edit_message_text(
                query.message.text_markdown +
                ("\n\n\u26a1 _Approved + trusting for 5min_"
                 if released else "\n\n(no plan pending)"),
                parse_mode="Markdown",
            )
        elif action == "reject":
            released = plan_checkpoint.reject(
                lc_user_id, reason="rejected via Telegram",
            )
            await query.edit_message_text(
                query.message.text_markdown +
                ("\n\n\u274c _Rejected_" if released else "\n\n(no plan pending)"),
                parse_mode="Markdown",
            )

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

        # If user replied to a specific message, include the quoted text as context
        # so the agent knows what the user is responding to
        if update.message.reply_to_message and update.message.reply_to_message.text:
            quoted = update.message.reply_to_message.text.strip()
            if quoted:
                text = f"[Replying to: {quoted[:500]}]\n\n{text}"

        # Security: only allow admin/authorized chats
        if not self._is_allowed(chat_id):
            logger.warning("Unauthorized Telegram message from chat %s", chat_id)
            await update.message.reply_text(
                "\U0001f512 Not authorized. Ask the admin to add your chat ID."
            )
            return

        # Check if any active callback is waiting for a help response
        for _cb in self._active_callbacks.values():
            if (
                hasattr(_cb, "_help_future")
                and _cb._help_future is not None
                and not _cb._help_future.done()
                and _cb._chat_id == int(chat_id)
            ):
                _cb._help_future.set_result(text.strip().lower())
                return  # Consumed by help flow

        # Status query while agent is working
        active_cbs = [cb for cb in self._active_callbacks.values()
                       if cb._chat_id == int(chat_id) and cb.busy]
        if active_cbs and _is_status_query(text):
            await update.message.reply_text(active_cbs[0].get_status_report())
            return

        # Resolve actual user_id from database — prefer explicit chat->user
        # binding set via /link so Telegram matches the Web UI session user.
        user_id = await resolve_user_id(self._config, chat_id=chat_id)
        logger.info("Telegram message from chat %s (user %s): %s", chat_id, user_id[:8], text[:100])

        # ── Recovery phrase: intercept password message ──
        if user_id in self._pending_recovery:
            self._pending_recovery.discard(user_id)
            # Delete the password message immediately for security
            try:
                await update.message.delete()
            except Exception:
                logger.warning("Failed to delete recovery password message for user %s", user_id)
            await self._handle_recovery_password(update, user_id, text)
            return

        # ── Plan-mode clarifying question: intercept answer ──
        # If the agent asked a QUESTION: via plan_checkpoint and is blocked
        # waiting for an answer, route this message as the answer instead
        # of starting a new turn.
        try:
            from lazyclaw.runtime import plan_checkpoint
            if plan_checkpoint.get_pending_question(user_id) is not None:
                released = plan_checkpoint.answer_clarification(user_id, text)
                if released:
                    try:
                        await update.message.reply_text(
                            "\u2713 Got it, planning now…"
                        )
                    except Exception:
                        pass
                    return
        except Exception:
            logger.debug("plan-question intercept failed", exc_info=True)

        # ── Instant mute: reply "mute" to a watcher notification ──
        # Zero LLM calls — handle directly for instant response
        raw_text = update.message.text or ""
        if await self._handle_instant_mute(update, user_id, raw_text):
            return

        # ── Instant done: short "done / finished / hecho" reply ──
        # Lets the user complete a nagging task by typing instead of tapping
        # the inline button. Zero LLM calls.
        if await self._handle_instant_done(update, user_id, raw_text):
            return

        # Launch concurrently — LaneQueue serializes per user, fast dispatch
        # returns in <2s so the queue drains quickly for heavy tasks
        import asyncio as _aio
        _aio.create_task(
            self._process_and_reply(update, chat_id, user_id, text),
            name=f"tg-{chat_id}-{id(text)}",
        )

    async def _handle_recovery_password(
        self, update: Update, user_id: str, password: str,
    ) -> None:
        """Generate a recovery phrase using the provided password.

        The phrase is sent as a temporary message that self-destructs after 60 s.
        """
        from lazyclaw.gateway.auth import generate_recovery_for_user

        chat_id = str(update.effective_chat.id)
        try:
            phrase = await generate_recovery_for_user(self._config, user_id, password)
        except PermissionError:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text="\u274c Incorrect password. Recovery phrase not generated.",
                parse_mode="HTML",
            )
            return
        except Exception as exc:
            logger.warning("Recovery generation failed for user %s: %s", user_id, exc)
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=f"\u274c Failed to generate recovery phrase: {exc}",
                parse_mode="HTML",
            )
            return

        words = phrase.split()
        numbered = "\n".join(f"{i+1}. <code>{w}</code>" for i, w in enumerate(words))
        msg = await self._app.bot.send_message(
            chat_id=chat_id,
            text=(
                "\U0001f510 <b>Your Recovery Phrase</b>\n\n"
                f"{numbered}\n\n"
                "\u26a0\ufe0f <b>Write these 12 words down and store them safely offline.</b>\n"
                "\u26a0\ufe0f This message will be deleted in 60 seconds.\n"
                "\u26a0\ufe0f This phrase will NOT be shown again."
            ),
            parse_mode="HTML",
        )
        # Schedule deletion of the phrase message after 60 seconds
        import asyncio as _aio
        async def _delete_later():
            await _aio.sleep(60)
            try:
                await msg.delete()
            except Exception:
                logger.warning("Failed to auto-delete recovery phrase message for user %s", user_id)
        _aio.create_task(_delete_later(), name=f"recovery-delete-{user_id}")

    async def _handle_instant_mute(
        self, update: Update, user_id: str, raw_text: str,
    ) -> bool:
        """Handle instant mute/unmute commands replying to watcher notifications.

        Returns True if handled (caller should stop), False otherwise.
        Recognized triggers: "mute", "silence", "shut up", "unmute"
        Works on reply to notification OR as standalone within 10 min of last notification.
        """
        import time as _time

        clean = raw_text.strip().lower()
        # Match: "mute", "mute this", "mute it", "silence", "shut up", "unmute"
        _MUTE_TRIGGERS = {"mute", "mute this", "mute it", "silence", "silence this", "shut up", "silence it"}
        _UNMUTE_TRIGGERS = {"unmute", "unmute this", "unmute it"}

        is_mute = clean in _MUTE_TRIGGERS
        is_unmute = clean in _UNMUTE_TRIGGERS
        if not is_mute and not is_unmute:
            return False

        # Get the last watcher notification context
        try:
            from lazyclaw.heartbeat.daemon import get_last_watcher_context
            wctx = get_last_watcher_context(user_id)
        except Exception:
            return False

        if not wctx or wctx.get("service") != "whatsapp":
            return False
        if (_time.time() - wctx.get("timestamp", 0)) > 600:
            return False  # Notification older than 10 min

        chat_names = wctx.get("chat_names", [])
        if not chat_names:
            # Try to parse from the notification text as fallback
            notif = wctx.get("notification", "")
            # Pattern: "▸ 👥 GroupName" or "💬  SenderName"
            import re
            # Groups: "👥 GroupName" or "▸ GroupName"
            matches = re.findall(r"[\U0001f465\u25B8]\s*(.+?)(?:\s*\(\d+\)|\s+\d{2}:\d{2}|\s*$)", notif)
            if matches:
                chat_names = [m.strip() for m in matches if m.strip()]
            # Single message: "💬  SenderName  ·"
            if not chat_names:
                m = re.search(r"\U0001f4ac\s+(.+?)\s+[\u00b7]", notif)
                if m:
                    chat_names = [m.group(1).strip()]

        if not chat_names:
            await update.message.reply_text(
                "Can't determine which chat to mute. "
                "Try: mute <group name>"
            )
            return True

        # If multiple chats in the notification, mute all of them
        # (user said "mute" to the whole notification)
        action = "unmute" if is_unmute else "mute"
        results = []

        # Find the WhatsApp MCP client
        from lazyclaw.mcp.manager import _active_clients
        mcp_client = None
        for sid, c in _active_clients.items():
            client_name = getattr(c, "name", "") or ""
            if "whatsapp" in client_name.lower():
                mcp_client = c
                break

        if mcp_client is None:
            await update.message.reply_text(
                "WhatsApp not connected. Can't mute right now."
            )
            return True

        for chat_name in chat_names:
            try:
                import json
                raw = await mcp_client.call_tool(
                    "whatsapp_mute",
                    {"chat": chat_name, "action": action},
                )
                data = json.loads(raw) if raw.strip().startswith("{") else {"result": raw}
                muted_name = data.get("chat", chat_name)
                results.append(muted_name)
            except Exception as exc:
                logger.warning("Instant mute failed for '%s': %s", chat_name, exc)
                results.append(f"{chat_name} (failed)")

        emoji = "\U0001f507" if is_mute else "\U0001f50a"
        verb = "Muted" if is_mute else "Unmuted"
        if len(results) == 1:
            await update.message.reply_text(f"{emoji} {verb}: {results[0]}")
        else:
            names = "\n".join(f"  \u2022 {r}" for r in results)
            await update.message.reply_text(f"{emoji} {verb}:\n{names}")

        return True

    # Short positive replies that should complete an open task reminder.
    # Kept deliberately tight (single-token / short phrase) so normal
    # conversation isn't hijacked. English + Spanish (user in Madrid).
    _DONE_TRIGGERS = frozenset({
        "done", "done!", "done.", "did it", "i did it", "i done it",
        "finished", "i finished it", "i'm done", "im done",
        "task done", "task is done", "task is finish", "task finished",
        "complete", "completed", "✅", "done ✅",
        "hecho", "ya", "terminado", "termine", "terminé", "listo", "ya esta", "ya está",
    })

    async def _handle_instant_done(
        self, update: Update, user_id: str, raw_text: str,
    ) -> bool:
        """Handle short 'done / finished / hecho' replies against an open
        task reminder.

        Returns True when the message was consumed (caller should stop so
        the agent is never invoked for what is really a UI click).

        Strategy:
        - Only trigger on short, unambiguous positive replies.
        - Find tasks currently being nagged (any user on this chat).
        - If exactly one nagging task: complete it silently (no LLM).
        - If multiple: list them with numbers, prompting a specific reply.
        - If none: fall through so the agent handles it normally.
        """
        clean = raw_text.strip().lower()
        # Strip trailing punctuation that doesn't change intent.
        compact = clean.rstrip(".!? ").strip()
        if compact not in self._DONE_TRIGGERS and clean not in self._DONE_TRIGGERS:
            return False
        # Extra safety: never hijack longer messages.
        if len(raw_text) > 40:
            return False

        try:
            from lazyclaw.tasks.store import complete_task, get_nagging_tasks
        except Exception:
            logger.debug("tasks.store unavailable for instant-done", exc_info=True)
            return False

        try:
            # Look across all users — the reminder daemon pushes every user's
            # nag to the single admin chat, so the task owner may not match
            # the chat's resolved user_id.
            nagging = await get_nagging_tasks(self._config, user_id=None, limit=5)
        except Exception:
            logger.warning("get_nagging_tasks failed", exc_info=True)
            return False

        if not nagging:
            return False  # Let the agent take it — maybe it's a real answer.

        if len(nagging) > 1:
            lines = ["Which one is done?"]
            for i, t in enumerate(nagging[:5], 1):
                lines.append(f"  {i}. {t.get('title', 'task')}")
            lines.append("\nReply with the number, or tap the \u2705 button on the reminder.")
            await update.message.reply_text("\n".join(lines))
            return True

        task = nagging[0]
        owner_id = task.get("user_id") or user_id
        task_id = task["id"]
        title = task.get("title", "Task")
        try:
            ok = await complete_task(self._config, owner_id, task_id)
        except Exception as exc:
            logger.warning("instant_done complete_task failed: %s", exc, exc_info=True)
            return False
        if not ok:
            await update.message.reply_text(
                f"\u274c Couldn't complete '{title}' — open it in /tasks and try again."
            )
            return True

        await update.message.reply_text(f"\u2705 Done: {title}")
        return True

    async def _process_and_reply(
        self, update: Update, chat_id: str, user_id: str, text: str,
    ) -> None:
        """Run agent and reply with result. Clean lifecycle:
        typing → status (delayed) → response with footer."""
        from uuid import uuid4
        request_id = f"{chat_id}-{uuid4().hex[:8]}"

        callback = _TelegramCallback(self._app.bot, int(chat_id))
        self._active_callbacks[request_id] = callback

        # Phase 1: Acknowledge — typing indicator immediately
        await callback._start_typing()
        # Delayed status message (only shows if task takes >2s)
        callback._schedule_status_message()

        # Wrap with server dashboard for terminal visibility
        effective_cb = callback
        if self._server_dashboard:
            self._server_dashboard.register_request(request_id, text)
            from lazyclaw.runtime.callbacks import MultiCallback
            effective_cb = MultiCallback(
                callback, self._server_dashboard.make_request_cb(request_id),
            )

        # Inject channel context as separate system message (not in user text)
        channel_context = (
            f"[Channel: Telegram | Chat ID: {chat_id} | "
            f"You can send images via browser(action=\"screenshot\") "
            f"\u2014 screenshots are auto-forwarded to this chat.]"
        )

        # Inject last watcher notification context ONLY when user is replying
        # to a notification (via Telegram reply) or explicitly mentions the channel.
        # Without this guard, the LLM sees notification context on every message
        # within 10 min and starts calling channel tools unprompted.
        _is_reply_to_notification = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.is_bot
        )
        _msg_lower = text.lower()
        _mentions_channel = any(
            kw in _msg_lower
            for kws in (["whatsapp", "wa "], ["instagram", "insta", "ig "], ["email", "gmail", "mail"])
            for kw in kws
        )
        try:
            from lazyclaw.heartbeat.daemon import get_last_watcher_context
            import time as _time
            _wctx = get_last_watcher_context(user_id)
            if (
                _wctx
                and (_time.time() - _wctx.get("timestamp", 0)) < 600  # within 10 min
                and (_is_reply_to_notification or _mentions_channel)
            ):
                _svc = _wctx["service"]
                _notif = _wctx.get("notification", "")
                channel_context += (
                    f"\n\n[RECENT {_svc.upper()} NOTIFICATION — user is replying to this]\n"
                    f"{_notif}\n\n"
                    f"IMPORTANT: If user says 'reply', 'tell him', 'say yes', etc. — "
                    f"use the MCP {_svc}_send tool to send the message to the contact shown above. "
                    f"Do NOT ask who to reply to — the contact is in the notification above.\n"
                    f"If user says 'mute X' with a specific group name — use whatsapp_mute tool.\n"
                    f"Do NOT call other channel tools unless the user explicitly asks."
                )
        except Exception:
            logger.warning("Failed to inject watcher channel context for user %s", user_id, exc_info=True)

        # Resolve the user's primary session so Telegram shares history with
        # Web UI / CLI / TUI / REPL. Cached after first resolve.
        try:
            primary_session_id = await get_primary_session_id(self._config, user_id)
        except Exception:
            logger.warning(
                "Failed to resolve primary session for user %s — using NULL bucket",
                user_id, exc_info=True,
            )
            primary_session_id = None

        try:
            logger.debug("Telegram: awaiting agent response for chat %s", chat_id)
            if self._lane_queue:
                response = await self._lane_queue.enqueue(
                    user_id, text, callback=effective_cb,
                    channel_context=channel_context,
                    chat_session_id=primary_session_id,
                )
            else:
                response = await self._agent.process_message(
                    user_id, text, callback=effective_cb,
                    channel_context=channel_context,
                    chat_session_id=primary_session_id,
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

            response = _strip_markdown(response)
            footer = callback._build_footer()

            # Use HTML parse_mode when response contains embedded links
            use_html = _has_html_links(response)
            if use_html:
                response = _prepare_html(response)
            parse_mode = "HTML" if use_html else None

            full_response = f"{response}\n\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n{footer}"

            # Split long messages (4096 char limit), footer on last chunk
            if len(full_response) <= 4096:
                await _telegram_send_with_retry(
                    lambda: update.message.reply_text(
                        full_response, parse_mode=parse_mode,
                    )
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
                            lambda c=chunk_with_footer: update.message.reply_text(
                                c, parse_mode=parse_mode,
                            )
                        )
                    else:
                        await _telegram_send_with_retry(
                            lambda c=chunk: update.message.reply_text(
                                c, parse_mode=parse_mode,
                            )
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
            if not callback.dispatched:
                # Normal path — request fully complete, clean up dashboard
                self._active_callbacks.pop(request_id, None)
                if self._server_dashboard:
                    self._server_dashboard.unregister_request(request_id)
            # else: fast dispatch — background task still running,
            # dashboard card stays visible until background_done/failed

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
