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
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Human-friendly feed titles per event kind (the body carries the detail).
_KIND_TITLES: dict[str, str] = {
    "done": "Task complete",
    "background_done": "Background task done",
    "background_failed": "Background task failed",
    "help_needed": "Agent needs help",
}


# Canonical implementations live in feed_store (the record-time choke point
# also needs them); these aliases keep this module's call sites unchanged.
from lazyclaw.notifications.feed_store import (  # noqa: E402
    strip_html as _strip_html,
    strip_markdown as _strip_markdown,
)


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
        verbose:             when True (default), `done` and `background_done`
                             pushes include a stats line ("2.2m | 144,522 tok |
                             11 calls") and a tools-used line. Heartbeat-fired
                             pushes (cron / reminder / watcher) pass False so
                             the user sees a clean message instead of debug
                             telemetry.
    """

    def __init__(
        self,
        bot: Any | None,
        admin_chat_id_fn: Callable[[], str | None],
        *,
        source_is_telegram: bool = False,
        verbose: bool = True,
        config: Any | None = None,
    ) -> None:
        self._bot = bot
        self._get_chat_id = admin_chat_id_fn
        self._source_is_telegram = source_is_telegram
        self._verbose = verbose
        # ``config`` enables per-user channel routing (telegram | app | both)
        # + recording to the in-app notification feed. When ``None`` (legacy
        # call sites), behaviour is unchanged: Telegram-only, no feed.
        self._config = config
        self._work_summary: Any | None = None

    # ── AgentCallback interface ──────────────────────────────────────

    async def on_event(self, event: Any) -> None:
        if self._source_is_telegram:
            return

        # Format FIRST so the feed mirrors exactly what would have gone to
        # Telegram (honours verbose / quiet / [SILENT] suppression in _format).
        text, parse_mode = self._format(event)
        if not text:
            return

        # Resolve the routing channel + record to the in-app feed for
        # ``app`` / ``both``. No-config notifiers stay Telegram-only.
        channel = "telegram"
        admin_uid = None
        if self._config is not None:
            try:
                from lazyclaw.notifications.channel import (
                    get_notification_channel,
                    resolve_admin_user_id,
                )

                admin_uid = await resolve_admin_user_id(self._config)
                if admin_uid:
                    channel = await get_notification_channel(
                        self._config, admin_uid,
                    )
            except Exception:
                logger.debug(
                    "TelegramNotifier channel resolve failed", exc_info=True,
                )

        # Durable record — ALWAYS (regardless of channel toggle). Task done /
        # failed / help are discrete events that belong in the Notification
        # Center even for telegram-only users (Spine, 2026-07-16). Suppressed
        # events already returned above (text is falsy); no-config notifiers
        # never resolve an admin_uid → stay Telegram-only.
        if admin_uid is not None:
            try:
                from lazyclaw.notifications.feed_store import (
                    record_notification,
                )

                kind = getattr(event, "kind", "info") or "info"
                severity = (
                    "important"
                    if kind in ("background_failed", "help_needed")
                    else "normal"
                )
                # admin_uid is only set inside the `self._config is not None`
                # block above, so config is guaranteed present here.
                await record_notification(
                    self._config, admin_uid, kind,  # type: ignore[arg-type]
                    _KIND_TITLES.get(kind, "Notification"),
                    _strip_html(text),
                    severity=severity,
                )
            except Exception:
                logger.warning(
                    "TelegramNotifier feed record failed", exc_info=True,
                )

        # Gate the Telegram send on the channel (app → skip).
        try:
            from lazyclaw.notifications.channel import should_send_telegram

            if not should_send_telegram(channel):
                return
        except Exception:
            pass  # default to sending on any resolver import failure

        chat_id = self._get_chat_id()
        if not chat_id or not self._bot:
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

            result_preview = ""
            if summary is not None:
                meta = {}
                for attr in ("duration_ms", "llm_calls", "total_tokens", "total_cost", "tools_used"):
                    val = getattr(summary, attr, None)
                    if val is not None:
                        meta[attr] = val
                stats_line = _format_stats_html(meta) if self._verbose else ""
                tools_line = _format_tools_html(meta) if self._verbose else ""
                # Prefer the full response over the 200-char CLI preview —
                # Telegram has its own ~4 KB envelope and the user expects
                # the complete reply, not a truncated stub.
                preview = (
                    getattr(summary, "result_full", "")
                    or getattr(summary, "result_preview", None)
                )
                if preview:
                    stripped = _strip_markdown(preview)
                    # Silent-mode suppression for cron pushes (verbose=False).
                    # Honoured for any heartbeat-fired callback so a cron job
                    # whose skill reports "nothing to tell the user" doesn't
                    # spam Telegram on every tick. Two trigger paths:
                    #   1. Explicit [SILENT] prefix from the skill result.
                    #   2. Brain dropped the prefix but the body is a known
                    #      no-news phrase (defensive — brain rewriting often).
                    if not self._verbose:
                        low = stripped.lower().lstrip()
                        if low.startswith("[silent]"):
                            return None, None
                        if (
                            "no unread conversations" in low
                            or "no new messages" in low
                            or "inbox is empty" in low
                            or "no unread messages" in low
                            or low.startswith("operation cancelled")
                            or low.startswith(
                                "i wasn't able to generate a response"
                            )
                        ):
                            return None, None
                    result_preview = html.escape(stripped[:3500])
                    if len(stripped) > 3500:
                        result_preview += "\n[truncated]"

                lines = ["[done] <b>Task complete</b>"]
                if stats_line:
                    lines.append(stats_line)
                if tools_line:
                    lines.append(tools_line)
                if result_preview:
                    lines.append("")
                    # Quiet mode (cron / reminder / watcher pushes): drop the
                    # <pre> wrapper so the body reads as a normal message,
                    # not a code block. Verbose mode keeps <pre> for the
                    # foreground "what did the agent do" surface.
                    if self._verbose:
                        lines.append(f"<pre>{result_preview}</pre>")
                    else:
                        lines.append(result_preview)
                return "\n".join(lines), "HTML"

            return "[done] <b>Task complete</b>", "HTML"

        if kind == "background_done":
            meta = event.metadata or {}
            # Brain fan-out: per-task push is suppressed at the source
            # in TaskRunner; the consolidator turn writes ONE merged
            # summary instead. Defensive guard here for any path that
            # bypasses TaskRunner's gate.
            if meta.get("source") == "brain":
                return None, None
            name = html.escape(meta.get("name", ""))
            result = _strip_markdown(meta.get("result", ""))
            # Quiet mode (cron / reminder / watcher pushes via
            # PrefixedTelegramNotifier with verbose=False): drop
            # results that explicitly opt out ([SILENT] prefix), known
            # no-news phrases from inbox sweeps, or cancelled bg tasks
            # — those are background-only noise the user shouldn't see.
            if not self._verbose:
                low = result.lower().lstrip()
                if low.startswith("[silent]"):
                    return None, None
                if (
                    "no unread conversations" in low
                    or "no new messages" in low
                    or "inbox is empty" in low
                    or "no unread messages" in low
                    or low.startswith("operation cancelled")
                ):
                    return None, None
            preview = html.escape(result[:500])
            if len(result) > 500:
                preview += "\n[truncated]"

            stats_line = _format_stats_html(meta) if self._verbose else ""
            tools_line = _format_tools_html(meta) if self._verbose else ""
            models = meta.get("models_used")
            model_line = ""
            if models and self._verbose:
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
                if self._verbose:
                    lines.append(f"<pre>{preview}</pre>")
                else:
                    lines.append(preview)
            return "\n".join(lines), "HTML"

        if kind == "background_failed":
            meta = event.metadata or {}
            # Brain fan-out — let the consolidator surface failures.
            if meta.get("source") == "brain":
                return None, None
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


class PrefixedTelegramNotifier(TelegramNotifier):
    """TelegramNotifier that prepends an icon + name header to ``done`` events.

    Used by background-fired paths (cron jobs, reminders, watcher
    expiries, MCP auto-replies) so the chat distinguishes scheduled
    pushes from foreground task completions.
    """

    def __init__(
        self,
        bot: Any | None,
        admin_chat_id_fn: Callable[[], str | None],
        *,
        prefix: str,
        icon: str = "⏰",
        source_is_telegram: bool = False,
        verbose: bool = False,
        config: Any | None = None,
    ) -> None:
        # Default verbose=False here: PrefixedTelegramNotifier is only
        # constructed by heartbeat-fired paths (cron / reminder / watcher
        # / MCP auto-reply), where the user wants a clean Telegram message,
        # not engineering telemetry. Foreground task completions still use
        # the base TelegramNotifier with verbose=True.
        super().__init__(
            bot, admin_chat_id_fn,
            source_is_telegram=source_is_telegram,
            verbose=verbose,
            config=config,
        )
        self._prefix = prefix
        self._icon = icon

    def _format(self, event: Any) -> tuple[str | None, str | None]:
        text, parse_mode = super()._format(event)
        if not text:
            return None, None
        if event.kind == "done" and self._prefix:
            # Drop bare-header pushes: when the cron / consolidator turn
            # produced no user-facing body, the base notifier returns
            # just "[done] Task complete" — rewriting line[0] would leave
            # a lone "⏰ survival_message_check" / "🧠 Consolidated"
            # bubble in chat, which is pure spam. A real reply has the
            # body appended after a blank line (≥3 lines).
            lines = text.split("\n")
            if len([ln for ln in lines if ln.strip()]) <= 1:
                return None, None
            lines[0] = f"{self._icon} <b>{html.escape(self._prefix)}</b>"
            text = "\n".join(lines)
        elif event.kind == "background_done":
            # Quiet-mode suppression already ran in the base notifier
            # (verbose=False is set in __init__), so `text` here means
            # the bg result is worth showing. If it ever falls through
            # with only the "[done] Background … done" header (no body
            # line), drop it — same reasoning as the `done` branch.
            lines = text.split("\n")
            if len([ln for ln in lines if ln.strip()]) <= 1:
                return None, None
        return text, parse_mode
