"""Telegram slash commands — direct admin calls, no LLM.

Every command calls existing functions from cli_admin, vault, mcp/manager, etc.
Responses use HTML formatting with emojis for a cozy Telegram experience.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from lazyclaw.config import Config

logger = logging.getLogger(__name__)


def parse_conversation_callback(data: str) -> tuple[str, bool] | None:
    """Map a Telegram callback_data to (conversation_id, approved) or None.

    Handles the convok:<conv_id> (✅ Send it) and convno:<conv_id> (✋ Cancel)
    buttons emitted by lazyclaw.comms.approvals.request_first_message_approval.

    Returns:
        (conv_id, True)  — user approved the first message
        (conv_id, False) — user cancelled the first message
        None             — data is not a conversation-approval callback
    """
    if data.startswith("convok:"):
        return data.split(":", 1)[1], True
    if data.startswith("convno:"):
        return data.split(":", 1)[1], False
    return None


# Commands shown in Telegram's "/" autocomplete menu
BOT_COMMANDS = [
    BotCommand("help", "\U0001f4cb All commands"),
    BotCommand("status", "\U0001f4ca Live status"),
    BotCommand("tasks", "\u26a1 Background tasks"),
    BotCommand("usage", "\U0001f4b0 Token costs"),
    BotCommand("mode", "\u2699\ufe0f AI routing mode"),
    BotCommand("act", "\U0001f3ad Operating mode: chat/ask/plan/auto"),
    BotCommand("model", "\U0001f9e0 Show/change models"),
    BotCommand("watch", "\U0001f514 Watchers (WhatsApp/Email)"),
    BotCommand("mcp", "\U0001f50c MCP servers"),
    BotCommand("key", "\U0001f511 API keys"),
    BotCommand("history", "\U0001f4ac Recent messages"),
    BotCommand("logs", "\U0001f4dd Activity log"),
    BotCommand("browser", "\U0001f310 Browser control"),
    BotCommand("usebrowser", "\U0001f5a5️ Host Brave bridge"),
    BotCommand("survival", "\U0001f4bc Job hunting"),
    BotCommand("profile", "\U0001f464 Freelance profile"),
    BotCommand("doctor", "\U0001fa7a Diagnostics"),
    BotCommand("screen", "\U0001f5a5 Desktop screenshot / VNC"),
    BotCommand("wipe", "\U0001f9f9 Clear history"),
    BotCommand("local", "\U0001f9e0 Local AI servers"),
    BotCommand("ram", "\U0001f4be RAM usage"),
    BotCommand("nuke", "\U0001f4a3 Data wipe"),
    BotCommand("qr", "\U0001f4f1 WhatsApp QR re-link"),
    BotCommand("recovery", "\U0001f510 Recovery phrase"),
    BotCommand("search", "\U0001f50d Search provider"),
    BotCommand("resetpass", "\U0001f510 Reset web UI password"),
    BotCommand("note", "\U0001f4dd Save a quick note"),
    BotCommand("idea", "\U0001f4a1 Save an idea"),
    BotCommand("remember", "\U0001f9e0 Save to memory"),
    BotCommand("permissions", "\U0001f510 Show permissions"),
    BotCommand("allow", "✅ /allow <category|skill>"),
    BotCommand("deny", "\U0001f6ab /deny <category|skill>"),
    BotCommand("confirm", "✅ /confirm <lesson_id>"),
    BotCommand("reject", "\U0001f6ab /reject <lesson_id> [reason]"),
    BotCommand("esc", "\U0001f4ac /esc <id> <reply> — answer escalation"),
    BotCommand("snooze", "⏰ /snooze <task> <duration>"),
    BotCommand("postpone", "📅 /postpone <task> <when>"),
    BotCommand("whenisdue", "📅 /whenisdue <task>"),
    BotCommand("progress", "📊 /progress <task>"),
]


class TelegramCommands:
    """All Telegram slash commands. Instant execution, no LLM call."""

    def __init__(
        self,
        adapter: Any,  # TelegramAdapter (avoid circular import)
        config: Config,
        agent: Any,
        task_runner: Any | None = None,
        team_lead: Any | None = None,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._agent = agent
        self._task_runner = task_runner
        self._team_lead = team_lead
        self._pinned_status: dict[str, int] = {}  # chat_id -> message_id
        self._pinned_task: asyncio.Task | None = None

    def register(self, app) -> None:
        """Register all command handlers + callback query handler."""
        cmds = {
            "start": self._handle_start, "help": self._handle_help,
            "key": self._handle_key, "model": self._handle_model,
            "mode": self._handle_eco, "eco": self._handle_eco,
            "doctor": self._handle_doctor,
            "logs": self._handle_logs, "usage": self._handle_usage,
            "tasks": self._handle_tasks, "cancel": self._handle_cancel,
            "history": self._handle_history, "wipe": self._handle_wipe,
            "nuke": self._handle_nuke, "mcp": self._handle_mcp,
            "watch": self._handle_watch,
            "whatsapp": self._handle_platform, "instagram": self._handle_platform,
            "email": self._handle_platform,
            "survival": self._handle_survival, "profile": self._handle_profile,
            "browser": self._handle_browser, "screen": self._handle_screen,
            "usebrowser": self._handle_usebrowser,
            "local": self._handle_local,
            "ram": self._handle_ram, "qr": self._handle_qr,
            "recovery": self._handle_recovery,
            "resetpass": self._handle_resetpass,
            "addadmin": self._handle_addadmin, "removeadmin": self._handle_removeadmin,
            "search": self._handle_search,
            "whoami": self._handle_whoami, "link": self._handle_link,
            "note": self._handle_note,
            "idea": self._handle_idea,
            "remember": self._handle_remember,
            "permissions": self._handle_permissions,
            "allow": self._handle_allow,
            "deny": self._handle_deny,
            "confirm": self._handle_lesson_confirm,
            "reject": self._handle_lesson_reject,
            "esc": self._handle_escalation_response,
            "snooze": self._handle_snooze,
            "postpone": self._handle_postpone,
            "whenisdue": self._handle_whenisdue,
            "progress": self._handle_progress,
            "act": self._handle_act,
        }
        for name, handler in cmds.items():
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(CallbackQueryHandler(self._handle_callback))

    # -- Helpers -----------------------------------------------------------

    def _is_allowed(self, chat_id: str) -> bool:
        return chat_id in self._adapter._allowed_chats or not self._adapter._admin_chat_id

    async def _resolve_user(self, chat_id: str) -> str:
        from lazyclaw.channels.telegram import resolve_user_id
        return await resolve_user_id(self._config, chat_id=chat_id)

    async def _auth(self, update: Update) -> str | None:
        chat_id = str(update.effective_chat.id)
        if not self._is_allowed(chat_id):
            return None
        return await self._resolve_user(chat_id)

    async def _reply(self, update: Update, text: str, html: bool = True) -> None:
        """Send reply with HTML formatting, splitting if over 4096 chars."""
        from lazyclaw.channels.telegram import _telegram_send_with_retry
        pm = "HTML" if html else None
        for i in range(0, len(text), 4000):
            chunk = text[i:i + 4000]
            await _telegram_send_with_retry(
                lambda c=chunk, p=pm: update.message.reply_text(c, parse_mode=p)
            )

    async def _send(self, bot, chat_id: str, text: str) -> None:
        """Send message with HTML (for /key which deletes original msg)."""
        from lazyclaw.channels.telegram import _telegram_send_with_retry
        await _telegram_send_with_retry(
            lambda: bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
        )

    async def _agent_dispatch(self, update: Update, chat_id: str, user_id: str, text: str) -> None:
        from lazyclaw.runtime.aio_helpers import fire_and_forget

        fire_and_forget(
            self._adapter._process_and_reply(update, chat_id, user_id, text),
            name=f"tg-cmd-{chat_id}-{id(text)}",
        )

    # -- /act (operating mode, ADR-0005) -----------------------------------

    async def _handle_act(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show/set the operating mode (Ask/Plan/Action/Execute).

        Distinct from /mode, which sets the ECO routing mode (which models).
        """
        user_id = await self._auth(update)
        if not user_id:
            return
        from lazyclaw.runtime.agent_mode import (
            MODE_LABELS,
            get_agent_mode,
            parse_mode_label,
        )

        arg = context.args[0].strip().lower() if context.args else ""
        if not arg:
            current = await get_agent_mode(self._config, user_id)
            await self._reply(
                update,
                f"⚙️ <b>Operating mode:</b> {MODE_LABELS[current]} "
                f"(<code>{current.value}</code>)\n\n"
                "<b>ask</b> — talk only, no tools\n"
                "<b>plan</b> — read-only research + plan, gate before executing\n"
                "<b>action</b> — act, confirm each write (default)\n"
                "<b>execute</b> — fully autonomous\n\n"
                "Change: <code>/act execute</code>",
            )
            return
        mode = parse_mode_label(arg)
        if mode is None:
            await self._reply(
                update,
                f"❌ Unknown mode <code>{arg}</code>. "
                "Use ask / plan / action / execute.",
            )
            return
        from lazyclaw.settings.general import update_general_settings

        await update_general_settings(
            self._config, user_id, {"agent_mode": mode.value},
        )
        await self._reply(
            update,
            f"✅ Operating mode set to <b>{MODE_LABELS[mode]}</b>.",
        )

    # -- /start ------------------------------------------------------------

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)

        if not self._adapter._admin_chat_id:
            self._adapter._admin_chat_id = chat_id
            self._adapter._allowed_chats.add(chat_id)
            logger.info("Telegram admin claimed by chat %s", chat_id)
            await self._reply(update,
                "\U0001f512 <b>Admin locked to this chat.</b>\n\n"
                "Hey! I'm <b>Claw</b> \U0001f43e\n\n"
                "Send me anything to chat, or /help for commands.\n\n"
                "\u26a1 <b>Quick setup:</b>\n"
                "<code>/key set ANTHROPIC_API_KEY sk-ant-xxx</code>\n"
                "<code>/mode claude</code>\n"
                "<code>/model brain claude-sonnet-4-6</code>"
            )
            if not self._pinned_task:
                self._pinned_task = asyncio.create_task(self._pinned_refresh_loop())
            return

        if chat_id not in self._adapter._allowed_chats:
            logger.warning("Unauthorized /start from chat %s", chat_id)
            await update.message.reply_text("\U0001f512 Not authorized. Bot is locked to another chat.")
            return

        await self._reply(update,
            "Hey! I'm <b>Claw</b> \U0001f43e\n"
            "Send me anything to chat, or /help for commands.\n\n"
            "\U0001f4ac <b>Just talk naturally:</b>\n"
            "\u2022 \"Check my WhatsApp\"\n"
            "\u2022 \"Remind me to call mom in 2 hours\"\n"
            "\u2022 \"Open wallapop and find me a MacBook\"\n"
            "\u2022 \"Search for flights to London\"\n"
            "\u2022 \"Watch my email for a reply from Amazon\""
        )

    # -- /help -------------------------------------------------------------

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        await self._reply(update,
            "\U0001f43e <b>LazyClaw</b>\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            "\U0001f4ac <b>Talk Naturally</b>\n"
            "\u2022 Browse: <i>\"Open twitter\"</i>\n"
            "\u2022 Messages: <i>\"Check my WhatsApp / Instagram / Email\"</i>\n"
            "\u2022 Reminders: <i>\"Remind me in 30 min to...\"</i>\n"
            "\u2022 Tasks: <i>\"Add task: buy groceries\"</i>\n"
            "\u2022 Research: <i>\"Search for flights to London\"</i>\n"
            "\u2022 Files: <i>\"What's on my desktop?\"</i>\n"
            "\u2022 Monitor: <i>\"Watch Amazon for price drop\"</i>\n"
            "\u2022 Memory: <i>\"Remember that my WiFi password is...\"</i>\n\n"
            "\u2699\ufe0f <b>Setup</b>\n"
            "/key \u2014 \U0001f511 API keys <i>(auto-deletes msg)</i>\n"
            "/model \u2014 \U0001f9e0 Show/change models\n"
            "/mode \u2014 \u2699\ufe0f AI routing mode\n"
            "/act \u2014 \U0001f3ad Operating mode (chat/ask/plan/auto)\n\n"
            "\U0001f4ca <b>Daily</b>\n"
            "/status \u2014 Live status\n"
            "/tasks \u2014 \u26a1 Background tasks\n"
            "/cancel \u2014 \U0001f6d1 Cancel task\n"
            "/usage \u2014 \U0001f4b0 Token costs\n"
            "/history \u2014 \U0001f4ac Recent messages\n"
            "/wipe \u2014 \U0001f9f9 Clear history\n\n"
            "\U0001f50c <b>Integrations</b>\n"
            "/watch \u2014 \U0001f514 Watchers (create/list/stop)\n"
            "/mcp \u2014 MCP servers\n"
            "/whatsapp \u2014 WhatsApp setup/status\n"
            "/instagram \u2014 Instagram setup/status\n"
            "/email \u2014 Email setup/status\n\n"
            "\U0001f4bc <b>Survival</b>\n"
            "/survival \u2014 Job hunting on/off\n"
            "/profile \u2014 \U0001f464 Freelance profile\n\n"
            "\U0001f6e1 <b>System</b>\n"
            "/local \u2014 \U0001f9e0 Local AI servers\n"
            "/ram \u2014 \U0001f4be RAM usage\n"
            "/screen \u2014 \U0001f5a5 Desktop screenshot / VNC\n"
            "/browser \u2014 \U0001f310 Browser control\n"
            "/doctor \u2014 \U0001fa7a Diagnostics\n"
            "/logs \u2014 \U0001f4dd Activity log\n"
            "/nuke \u2014 \U0001f4a3 Data wipe\n\n"
            "<i>Or just type naturally \u2014 I understand that too.</i>"
        )

    # -- /key --------------------------------------------------------------

    async def _handle_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        if not self._is_allowed(chat_id):
            return
        try:
            await update.message.delete()
        except Exception as exc:
            logger.debug("Failed to delete /key message (may already be gone): %s", exc)

        args = context.args or []
        user_id = await self._resolve_user(chat_id)
        bot = context.bot

        if not args:
            await self._send(bot, chat_id,
                "\U0001f511 <b>Key Management</b>\n\n"
                "<code>/key set NAME VALUE</code> \u2014 Store key\n"
                "<code>/key list</code> \u2014 Show stored keys\n"
                "<code>/key delete NAME</code> \u2014 Remove key"
            )
            return

        subcmd = args[0].lower()
        if subcmd == "set" and len(args) >= 3:
            key_name, key_value = args[1], " ".join(args[2:])
            from lazyclaw.crypto.vault import set_credential
            await set_credential(self._config, user_id, key_name, key_value)
            masked = key_value[:4] + "..." + key_value[-3:] if len(key_value) > 7 else "***"
            await self._send(bot, chat_id, f"\u2705 Saved <b>{key_name}</b> ({masked})\n\U0001f5d1 Your message was deleted.")
        elif subcmd == "list":
            from lazyclaw.crypto.vault import list_credentials
            keys = await list_credentials(self._config, user_id)
            if keys:
                items = "\n".join(f"  \u2022 <code>{k}</code>" for k in keys)
                await self._send(bot, chat_id, f"\U0001f511 <b>Stored Keys</b>\n\n{items}")
            else:
                await self._send(bot, chat_id, "\U0001f511 No keys stored yet.")
        elif subcmd == "delete" and len(args) >= 2:
            from lazyclaw.crypto.vault import delete_credential
            deleted = await delete_credential(self._config, user_id, args[1])
            icon = "\u2705" if deleted else "\u274c"
            msg = f"Deleted <b>{args[1]}</b>" if deleted else f"Key '{args[1]}' not found"
            await self._send(bot, chat_id, f"{icon} {msg}")
        else:
            await self._send(bot, chat_id, "\U0001f511 Usage: <code>/key set NAME VALUE</code>")

    # -- /model ------------------------------------------------------------

    async def _handle_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []
        from lazyclaw.llm.eco_settings import get_eco_settings, update_eco_settings
        from lazyclaw.llm.model_registry import get_mode_models

        if not args:
            _eco = await get_eco_settings(self._config, user_id)
            _models = get_mode_models(_eco.get("mode", "hybrid"))
            await self._reply(update,
                f"\U0001f9e0 <b>Models</b>\n\n"
                f"Brain: <code>{_eco.get('brain_model') or _models['brain']}</code>\n"
                f"Worker: <code>{_eco.get('worker_model') or _models['worker']}</code>\n"
                f"Fallback: <code>{_eco.get('fallback_model') or _models['fallback']}</code>\n\n"
                f"Change: <code>/model brain MODEL</code>"
            )
            return
        if len(args) >= 2:
            role, model = args[0].lower(), " ".join(args[1:])
            if role in ("brain", "worker", "fallback"):
                # Persist to the per-user users.settings JSON (same path as
                # the web UI Settings page and /mode brain/worker) so the
                # change applies across every channel, not just this one.
                try:
                    await update_eco_settings(
                        self._config, user_id, {f"{role}_model": model},
                    )
                except ValueError as exc:
                    await self._reply(update, f"\u274c {exc}")
                    return
                await self._reply(
                    update,
                    f"\u2705 {role.title()}: <code>{model}</code> "
                    f"(applies to Telegram + Web UI + CLI)",
                )
                return
        await self._reply(update, "\U0001f9e0 Usage: <code>/model brain MODEL</code> or <code>/model worker MODEL</code>")

    # -- /whoami -----------------------------------------------------------

    async def _handle_whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show which user_id this Telegram chat is bound to + active models.

        Useful to diagnose Telegram/Web-UI model drift — both paths must
        resolve to the same user_id to share eco settings.
        """
        chat_id = str(update.effective_chat.id)
        if not self._is_allowed(chat_id):
            return
        user_id = await self._resolve_user(chat_id)

        from lazyclaw.db.connection import db_session
        from lazyclaw.llm.eco_settings import get_eco_settings
        from lazyclaw.llm.model_registry import get_mode_models

        username = "?"
        bound = False
        try:
            async with db_session(self._config) as db:
                cur = await db.execute(
                    "SELECT username, "
                    "json_extract(settings, '$.telegram_chat_id') "
                    "FROM users WHERE id = ?",
                    (user_id,),
                )
                row = await cur.fetchone()
                if row:
                    username = row[0] or "?"
                    bound = bool(row[1]) and str(row[1]) == chat_id
        except Exception:
            logger.warning("whoami: user lookup failed", exc_info=True)

        eco = await get_eco_settings(self._config, user_id)
        mode = eco.get("mode", "hybrid")
        defaults = get_mode_models(mode)

        def pick(role: str) -> tuple[str, str]:
            per_mode = eco.get(f"{mode}_{role}_model")
            if per_mode:
                return per_mode, f"{mode}_{role}_model"
            generic = eco.get(f"{role}_model")
            if generic:
                return generic, f"{role}_model"
            return defaults.get(role, "?"), "mode default"

        brain, brain_src = pick("brain")
        worker, worker_src = pick("worker")
        fallback, fallback_src = pick("fallback")

        bind_hint = (
            "✅ bound"
            if bound
            else "⚠️ <b>not bound</b> — run <code>/link USERNAME</code> "
                 "to point this chat at your Web-UI user"
        )
        await self._reply(
            update,
            f"\U0001f464 <b>User</b>: <code>{user_id[:8]}</code> ({username})\n"
            f"\U0001f4ac <b>Chat</b>: <code>{chat_id}</code> — {bind_hint}\n\n"
            f"\U0001f9e0 <b>Mode</b>: {mode}\n"
            f"Brain:    <code>{brain}</code>  <i>[{brain_src}]</i>\n"
            f"Worker:   <code>{worker}</code>  <i>[{worker_src}]</i>\n"
            f"Fallback: <code>{fallback}</code>  <i>[{fallback_src}]</i>",
        )

    # -- /link -------------------------------------------------------------

    async def _handle_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Bind this Telegram chat_id to a specific Web-UI user by username.

        Writes users.settings.telegram_chat_id for that user, so
        resolve_user_id(chat_id) routes this chat to the same account the
        user logs into the Web UI with. Fixes multi-user divergence
        (admin placeholder vs real personal account).
        """
        chat_id = str(update.effective_chat.id)
        if not self._is_allowed(chat_id):
            return
        args = context.args or []
        if not args:
            await self._reply(
                update,
                "\U0001f517 <b>Link this chat to a Web-UI user</b>\n\n"
                "<code>/link USERNAME</code>\n\n"
                "Once bound, Telegram + Web UI + CLI all read the same "
                "eco settings (brain/worker/fallback models, mode, "
                "memory, skills).",
            )
            return

        username = args[0].strip()
        from lazyclaw.db.connection import db_session
        try:
            async with db_session(self._config) as db:
                cur = await db.execute(
                    "SELECT id, settings FROM users WHERE username = ?",
                    (username,),
                )
                row = await cur.fetchone()
                if not row:
                    await self._reply(update, f"❌ No user <code>{username}</code>")
                    return
                target_id, raw_settings = row[0], row[1]

                # Clear any previous binding to this chat on other users.
                await db.execute(
                    "UPDATE users SET settings = json_remove(settings, '$.telegram_chat_id') "
                    "WHERE json_extract(settings, '$.telegram_chat_id') = ?",
                    (chat_id,),
                )

                import json
                try:
                    current = json.loads(raw_settings) if raw_settings else {}
                except (json.JSONDecodeError, TypeError):
                    current = {}
                new_settings = dict(current)
                new_settings["telegram_chat_id"] = chat_id
                await db.execute(
                    "UPDATE users SET settings = ? WHERE id = ?",
                    (json.dumps(new_settings), target_id),
                )
                await db.commit()
        except Exception as exc:
            logger.exception("/link failed")
            await self._reply(update, f"❌ Link failed: {exc}")
            return

        await self._reply(
            update,
            f"✅ Chat <code>{chat_id}</code> → <b>{username}</b>\n\n"
            f"Telegram now shares eco settings + memory with the Web UI "
            f"login <b>{username}</b>. Run <code>/whoami</code> to confirm.",
        )

    # -- /mode (alias: /eco) ------------------------------------------------

    async def _handle_eco(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []
        from lazyclaw.llm.eco_settings import get_eco_settings, update_eco_settings
        from lazyclaw.llm.eco_router import normalize_mode, MODE_HYBRID, MODE_FULL, MODE_CLAUDE, _DISABLED_MODES, DISABLED_MODE_MESSAGE
        from lazyclaw.llm.model_registry import get_mode_models

        if not args:
            # Show current status
            s = await get_eco_settings(self._config, user_id)
            mode = s.get("mode", "hybrid")
            transport = s.get("claude_transport", "sdk")
            icons = {
                MODE_HYBRID: "\u2696\ufe0f", MODE_FULL: "\U0001f680",
                MODE_CLAUDE: "\u26a1",
            }
            # Mode label shows the active Claude transport so admin can
            # see at a glance which wire-protocol is firing.
            labels = {
                MODE_HYBRID: "HYBRID", MODE_FULL: "FULL",
                MODE_CLAUDE: f"CLAUDE ({transport.upper()})",
            }
            _models = get_mode_models(mode)
            brain = s.get("brain_model") or _models["brain"]
            worker = s.get("worker_model") or _models["worker"]
            fallback = s.get("fallback_model") or _models["fallback"]
            max_w = s.get("max_workers", 10)
            auto_fb = "\u2705" if s.get("auto_fallback") else "\u274c"
            budget = s.get("monthly_paid_budget", 0)

            text = (
                f"{icons.get(mode, '')} <b>Mode: {labels.get(mode, mode)}</b>\n"
                f"━━━━━━━━━━━━\n"
                f"\U0001f9e0 Brain: <b>{brain}</b>\n"
                f"\U0001f916 Worker: <b>{worker}</b>\n"
                f"\U0001f4ab Fallback: {fallback}\n"
                f"\U0001f465 Max workers: {max_w}\n"
                f"\u26a1 Auto-fallback: {auto_fb}\n"
            )
            if budget:
                text += f"\U0001f4b0 Budget: ${budget:.2f}/mo\n"
            text += (
                "\n<b>Commands:</b>\n"
                "<code>/mode hybrid</code> — Haiku brain + local worker\n"
                "<code>/mode full</code> — User-configured paid models\n"
                "<code>/mode claude</code> — Claude subscription (free)\n"
                "<code>/mode claude sdk</code> — Use native Agent SDK (default)\n"
                "<code>/mode claude cli</code> — Use legacy `claude -p`\n"
                "<code>/mode brain MODEL</code> — Set brain model\n"
                "<code>/mode worker MODEL</code> — Set worker model\n"
                "<code>/mode workers N</code> — Max workers (1-20)\n"
                "<code>/mode budget N</code> — Monthly $ cap"
            )
            await self._reply(update, text)
            return

        subcmd = args[0].lower()

        # Mode change: /eco hybrid|full|claude [sdk|cli]
        # Optional second arg picks the Claude transport when entering
        # CLAUDE mode. Both updates applied atomically so the next message
        # uses the new transport without restart.
        if subcmd in ("on", "hybrid", "off", "local", "full", "eco", "eco_on", "claude"):
            if subcmd in _DISABLED_MODES:
                await self._reply(update, f"\u26a0\ufe0f {DISABLED_MODE_MESSAGE}")
                return
            normalized = normalize_mode(subcmd)
            updates: dict = {"mode": normalized}
            transport_note = ""
            if normalized == MODE_CLAUDE and len(args) > 1:
                t = args[1].lower().strip()
                if t in ("sdk", "cli"):
                    updates["claude_transport"] = t
                    transport_note = f" (transport: {t.upper()})"
                else:
                    await self._reply(update,
                        f"\u26a0\ufe0f Unknown Claude transport "
                        f"<code>{t}</code>. Use <code>sdk</code> or "
                        f"<code>cli</code>."
                    )
                    return
            await update_eco_settings(self._config, user_id, updates)
            labels = {
                MODE_HYBRID: "HYBRID", MODE_FULL: "FULL",
                MODE_CLAUDE: "CLAUDE",
            }
            await self._reply(update,
                f"\u2705 Mode: <b>{labels.get(normalized, normalized)}</b>"
                f"{transport_note}"
            )
            return

        # Auto-fallback: /eco auto on|off
        if subcmd == "auto" and len(args) > 1:
            val = args[1].lower() in ("on", "true", "yes", "1")
            await update_eco_settings(self._config, user_id, {"auto_fallback": val})
            icon = "\u2705" if val else "\u274c"
            await self._reply(update, f"{icon} Auto-fallback: <b>{'ON' if val else 'OFF'}</b>")
            return

        # Workers: /eco workers N
        if subcmd == "workers" and len(args) > 1:
            try:
                n = int(args[1])
                await update_eco_settings(self._config, user_id, {"max_workers": n})
                await self._reply(update, f"\u2705 Max workers: <b>{n}</b>")
            except (ValueError, Exception) as e:
                await self._reply(update, f"\u274c {e}")
            return

        # Brain model: /eco brain MODEL
        if subcmd == "brain" and len(args) > 1:
            model = " ".join(args[1:])
            await update_eco_settings(self._config, user_id, {"brain_model": model})
            await self._reply(update, f"\u2705 Brain: <b>{model}</b>")
            return

        # Worker model: /eco worker MODEL
        if subcmd == "worker" and len(args) > 1:
            model = " ".join(args[1:])
            await update_eco_settings(self._config, user_id, {"worker_model": model})
            await self._reply(update, f"\u2705 Worker: <b>{model}</b>")
            return

        # Fallback model: /eco fallback MODEL
        if subcmd == "fallback" and len(args) > 1:
            model = " ".join(args[1:])
            await update_eco_settings(self._config, user_id, {"fallback_model": model})
            await self._reply(update, f"\u2705 Fallback: <b>{model}</b>")
            return

        # Budget: /eco budget N
        if subcmd == "budget" and len(args) > 1:
            try:
                val = float(args[1])
                await update_eco_settings(self._config, user_id, {"monthly_paid_budget": val})
                label = f"${val:.2f}/mo" if val > 0 else "unlimited"
                await self._reply(update, f"\u2705 Budget: <b>{label}</b>")
            except (ValueError, Exception) as e:
                await self._reply(update, f"\u274c {e}")
            return

        await self._reply(update, "\u274c Unknown. Use: <code>/mode hybrid|full|claude</code>")

    # -- /ram ---------------------------------------------------------------

    # -- /local ---------------------------------------------------------------

    async def _handle_local(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manage local Ollama worker for HYBRID mode.

        Ollama 0.19+ includes a native MLX backend for Apple Silicon —
        it handles model loading, memory management, and auto-swap automatically.
        We just need to start/stop the ollama serve process and pull the model.

        /local [on|off|status|restart]
        """
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []

        from lazyclaw.llm.model_registry import WORKER_MODEL
        from lazyclaw.llm.eco_settings import get_eco_settings, update_eco_settings
        from lazyclaw.llm.providers.ollama_provider import OllamaProvider

        eco_router = getattr(self._agent, "eco_router", None)
        if not eco_router:
            await self._reply(update, "\u274c No eco_router available")
            return

        settings = await get_eco_settings(self._config, user_id)
        worker_model = settings.get("worker_model") or WORKER_MODEL  # gemma4:e2b

        subcmd = args[0].lower() if args else "status"

        if subcmd == "status" or not args:
            ollama = OllamaProvider()
            is_running = await ollama.health_check()
            running_models = await ollama.list_running() if is_running else []

            o_icon = "\u2705" if is_running else "\u274c"
            from lazyclaw.llm.ram_monitor import get_ram_status
            ram = await get_ram_status()

            text = (
                "\U0001f9e0 <b>Local Ollama Worker</b>\n"
                "━━━━━━━━━━━━\n"
                f"{o_icon} Ollama: <b>{'running' if is_running else 'stopped'}</b>\n"
            )
            if running_models:
                for m in running_models:
                    text += f"\u2022 {m['name']} ({m['size_mb']}MB loaded)\n"
            else:
                text += f"\U0001f4e6 Model: {worker_model} (not loaded)\n"

            text += (
                f"\n\U0001f4be RAM: {ram.system_used_pct:.0f}% used"
            )
            if ram.ai_total_mb > 0:
                text += f" (AI: {ram.ai_total_mb}MB)"
            text += f"\n\U0001f7e2 Free: {ram.headroom_mb}MB"

            text += (
                "\n\n<b>Commands:</b>\n"
                "<code>/local on</code> — Start Ollama + pull model\n"
                "<code>/local off</code> — Stop Ollama\n"
                "<code>/local restart</code> — Restart Ollama\n"
                "<code>/local status</code> — Show this status"
            )
            await self._reply(update, text)
            return

        if subcmd in ("on", "start"):
            await self._reply(update, "\u23f3 Starting Ollama worker...")
            import shutil
            import asyncio as _aio

            if not shutil.which("ollama"):
                await self._reply(
                    update,
                    "\u274c Ollama not installed.\n"
                    "Install: <a href='https://ollama.com'>ollama.com</a>\n"
                    "Then run: <code>ollama pull gemma4:e2b</code>"
                )
                return

            # Start ollama serve in background (idempotent — ignores if already running)
            proc = await _aio.create_subprocess_exec(
                "ollama", "serve",
                stdout=_aio.subprocess.DEVNULL,
                stderr=_aio.subprocess.DEVNULL,
            )
            # Give it a moment to start
            await _aio.sleep(2)

            # Check it's up
            ollama = OllamaProvider()
            if not await ollama.health_check():
                await self._reply(update, "\u274c Ollama failed to start. Check system logs.")
                return

            # Pull model if not already present
            running = await ollama.list_running()
            model_loaded = any(m["name"].startswith(worker_model.split(":")[0]) for m in running)
            if not model_loaded:
                await self._reply(update, f"\u23f3 Pulling <b>{worker_model}</b> (first time may take a few minutes)...")
                pull_proc = await _aio.create_subprocess_exec(
                    "ollama", "pull", worker_model,
                    stdout=_aio.subprocess.PIPE,
                    stderr=_aio.subprocess.PIPE,
                )
                _, stderr = await pull_proc.communicate()
                if pull_proc.returncode != 0:
                    err = stderr.decode()[:200] if stderr else "unknown error"
                    await self._reply(update, f"\u274c Pull failed: {err}")
                    return

            eco_router.reset_local_check()
            await update_eco_settings(self._config, user_id, {"mode": "hybrid"})
            await self._reply(
                update,
                f"\u2705 Ollama running with <b>{worker_model}</b>\n"
                f"\u2696\ufe0f Auto-switched to <b>HYBRID</b> mode\n"
                f"(Sonnet 4.6 brain + local Gemma 4 worker)"
            )
            return

        if subcmd in ("off", "stop"):
            import asyncio as _aio
            stop = await _aio.create_subprocess_exec(
                "pkill", "-f", "ollama serve",
                stdout=_aio.subprocess.DEVNULL,
                stderr=_aio.subprocess.DEVNULL,
            )
            await stop.wait()
            eco_router.reset_local_check()
            await self._reply(update, "\u2705 Ollama stopped.")
            return

        if subcmd == "restart":
            await self._reply(update, "\u23f3 Restarting Ollama...")
            import asyncio as _aio
            import shutil
            if not shutil.which("ollama"):
                await self._reply(update, "\u274c Ollama not installed.")
                return
            stop = await _aio.create_subprocess_exec(
                "pkill", "-f", "ollama serve",
                stdout=_aio.subprocess.DEVNULL,
                stderr=_aio.subprocess.DEVNULL,
            )
            await stop.wait()
            await _aio.sleep(1)
            await _aio.create_subprocess_exec(
                "ollama", "serve",
                stdout=_aio.subprocess.DEVNULL,
                stderr=_aio.subprocess.DEVNULL,
            )
            await _aio.sleep(2)
            ollama = OllamaProvider()
            ok = await ollama.health_check()
            eco_router.reset_local_check()
            icon = "\u2705" if ok else "\u274c"
            await self._reply(
                update,
                f"{icon} Ollama {'running' if ok else 'failed to start'}\n"
                f"\U0001f4e6 Worker: {worker_model}"
            )
            return

        await self._reply(update, "\u274c Use: <code>/local on|off|restart|status</code>")

    # -- /ram ---------------------------------------------------------------

    async def _handle_ram(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        from lazyclaw.llm.ram_monitor import get_ram_status, format_ram_telegram
        status = await get_ram_status()
        await self._reply(update, format_ram_telegram(status))

    # -- /doctor -----------------------------------------------------------

    async def _handle_doctor(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        checks = []
        try:
            from lazyclaw.db.connection import db_session
            async with db_session(self._config) as db:
                count = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
            checks.append(f"\u2705 DB: OK ({count} users)")
        except Exception as e:
            checks.append(f"\u274c DB: FAIL ({e})")
        try:
            from lazyclaw.crypto.encryption import encrypt, decrypt
            from lazyclaw.crypto.key_manager import get_user_dek
            k = await get_user_dek(self._config, user_id)
            assert decrypt(encrypt("test", k), k) == "test"
            checks.append("\u2705 Encryption: OK")
        except Exception as e:
            checks.append(f"\u274c Encryption: FAIL ({e})")
        try:
            from lazyclaw.mcp.manager import _active_clients
            checks.append(f"\u2705 MCP: {len(_active_clients)} connected")
        except Exception as exc:
            logger.debug("MCP status check failed: %s", exc)
            checks.append("\u26a0\ufe0f MCP: unavailable")
        checks.append(f"\u2705 Telegram: OK")
        await self._reply(update, "\U0001fa7a <b>Diagnostics</b>\n━━━━━━━━━━━━\n" + "\n".join(checks))

    # -- /logs -------------------------------------------------------------

    async def _handle_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        limit = int(context.args[0]) if context.args else 10
        try:
            from lazyclaw.db.connection import db_session
            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT created_at, entry_type, content FROM agent_traces "
                    "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                )
                rows = await cursor.fetchall()
            if not rows:
                await self._reply(update, "\U0001f4dd No recent activity.")
                return
            lines = ["\U0001f4dd <b>Recent Activity</b>\n━━━━━━━━━━━━\n"]
            for ts, entry_type, content in reversed(rows):
                short_ts = ts[11:19] if len(ts) > 19 else ts
                preview = (content or "")[:70].replace("\n", " ").replace("<", "&lt;")
                lines.append(f"<code>{short_ts}</code> [{entry_type}] {preview}")
            await self._reply(update, "\n".join(lines))
        except Exception as e:
            await self._reply(update, f"\u274c Failed: {e}")

    # -- /usage ------------------------------------------------------------

    async def _handle_usage(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        try:
            from lazyclaw.db.connection import db_session
            async with db_session(self._config) as db:
                mc = (await (await db.execute("SELECT COUNT(*) FROM agent_messages WHERE user_id=?", (user_id,))).fetchone())[0]
                sc = (await (await db.execute("SELECT COUNT(*) FROM agent_traces WHERE user_id=? AND entry_type='session_start'", (user_id,))).fetchone())[0]
                mm = (await (await db.execute("SELECT COUNT(*) FROM personal_memory WHERE user_id=?", (user_id,))).fetchone())[0]
            from lazyclaw.llm.model_registry import get_mode_models
            from lazyclaw.llm.eco_settings import get_eco_settings
            _eco = await get_eco_settings(self._config, user_id)
            _models = get_mode_models(_eco.get("mode", "hybrid"))
            await self._reply(update,
                f"\U0001f4b0 <b>Usage Stats</b>\n━━━━━━━━━━━━\n\n"
                f"\U0001f4ac Messages: <b>{mc:,}</b>\n"
                f"\U0001f4c1 Sessions: <b>{sc:,}</b>\n"
                f"\U0001f9e0 Memories: <b>{mm:,}</b>\n\n"
                f"\U0001f916 Brain: <code>{_eco.get('brain_model') or _models['brain']}</code>\n"
                f"\u2699\ufe0f Worker: <code>{_eco.get('worker_model') or _models['worker']}</code>"
            )
        except Exception as e:
            await self._reply(update, f"\u274c Failed: {e}")

    # -- /tasks ------------------------------------------------------------

    async def _handle_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return

        sections: list[str] = []

        # Active agent tasks
        if self._team_lead:
            status = self._team_lead.format_status()
            if status:
                sections.append(f"\U0001f504 <b>Running</b>\n{status}")

        _cancel_buttons = []
        if self._task_runner:
            try:
                tasks = self._task_runner.list_all(user_id)
                if tasks:
                    icons = {"running": "\U0001f504", "done": "\u2705", "failed": "\u274c"}
                    task_lines = []
                    for t in tasks:
                        icon = icons.get(t.get("status", ""), "\u2753")
                        name = t.get("name", "unnamed")
                        task_lines.append(f"  {icon} {name}")
                        # Add cancel button for running tasks
                        if t.get("status") == "running":
                            _cancel_buttons.append(
                                [InlineKeyboardButton(
                                    f"\U0001f6d1 Cancel: {name[:30]}",
                                    callback_data=f"bgtask:cancel:{t['id']}",
                                )]
                            )
                    sections.append("\n".join(task_lines))
            except Exception as exc:
                logger.warning("Failed to list task runner tasks: %s", exc)

        # Watcher jobs (WhatsApp, Email, etc.)
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            from lazyclaw.heartbeat.orchestrator import list_jobs

            jobs = await list_jobs(self._config, user_id)
            watchers = [j for j in jobs if j.get("job_type") == "watcher"
                        and j.get("status") in ("active", "paused")]
            if watchers:
                watcher_lines = ["\U0001f514 <b>Watchers</b>"]
                for w in watchers:
                    name = w.get("name", "?")
                    status = w.get("status", "?")
                    icon = "\u2705" if status == "active" else "\u23f8"

                    # Parse context for details
                    raw_ctx = w.get("context", "{}")
                    try:
                        ctx = _json.loads(raw_ctx) if raw_ctx and not str(raw_ctx).startswith("enc:") else {}
                    except (ValueError, TypeError):
                        logger.debug("Failed to parse watcher context JSON", exc_info=True)
                        ctx = {}

                    service = ctx.get("service", "")
                    interval = int(ctx.get("check_interval", 120)) // 60
                    last_check = ctx.get("last_check", 0)
                    seen = len(ctx.get("last_seen_ids", []))

                    time_str = ""
                    if last_check and last_check > 0:
                        last_dt = _dt.fromtimestamp(last_check, tz=_tz.utc)
                        time_str = last_dt.strftime("%H:%M")

                    details = f"every {interval}m"
                    if time_str:
                        details += f" \u2022 last {time_str}"
                    if seen:
                        details += f" \u2022 {seen} seen"

                    watcher_lines.append(f"  {icon} {name}")
                    watcher_lines.append(f"      <i>{details}</i>")

                sections.append("\n".join(watcher_lines))

            # Cron jobs
            crons = [j for j in jobs if j.get("job_type") == "cron"
                     and j.get("status") in ("active", "paused")]
            if crons:
                cron_lines = ["\u23f0 <b>Scheduled</b>"]
                for c in crons:
                    name = c.get("name", "?")
                    cron_expr = c.get("cron_expression", "")
                    icon = "\u2705" if c.get("status") == "active" else "\u23f8"
                    cron_lines.append(f"  {icon} {name}")
                    if cron_expr:
                        cron_lines.append(f"      <i>{cron_expr}</i>")
                sections.append("\n".join(cron_lines))

        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("Tasks: failed to list jobs: %s", exc)

        if not sections:
            await self._reply(update, "\u26a1 No tasks or watchers active.")
            return

        header = "\u26a1 <b>Tasks &amp; Watchers</b>\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        text = header + "\n\n".join(sections)
        if _cancel_buttons:
            keyboard = InlineKeyboardMarkup(_cancel_buttons)
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await self._reply(update, text)

    # -- /cancel -----------------------------------------------------------

    async def _handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return

        cancelled = 0
        target = " ".join(context.args) if context.args else ""

        # Cancel foreground tasks (TeamLead)
        if self._team_lead:
            task_id = self._team_lead.find_cancel_target(target) if target else None
            if task_id:
                self._team_lead.cancel(task_id)
                cancelled += 1
            else:
                for t in self._team_lead.active_tasks:
                    self._team_lead.cancel(t.id)
                    cancelled += 1

        # Cancel background tasks (TaskRunner) — the actual asyncio tasks
        if self._task_runner:
            running = self._task_runner.list_running(user_id)
            for task_info in running:
                tid = task_info.get("id", "")
                if target and target.lower() not in (task_info.get("name", "")).lower():
                    continue
                ok = await self._task_runner.cancel(tid, user_id)
                if ok:
                    cancelled += 1

        if cancelled:
            await self._reply(update, f"\U0001f6d1 Cancelled {cancelled} task(s).")
        else:
            await self._reply(update, "\U0001f6d1 Nothing to cancel.")

    # -- /history ----------------------------------------------------------

    async def _handle_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        try:
            from lazyclaw.db.connection import db_session
            from lazyclaw.crypto.key_manager import get_user_dek
            from lazyclaw.crypto.encryption import decrypt
            key = await get_user_dek(self._config, user_id)
            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT role, content FROM agent_messages "
                    "WHERE user_id = ? AND role IN ('user', 'assistant') "
                    "ORDER BY created_at DESC LIMIT 10", (user_id,),
                )
                rows = await cursor.fetchall()
            if not rows:
                await self._reply(update, "\U0001f4ac No conversation history.")
                return
            lines = ["\U0001f4ac <b>Recent Messages</b>\n━━━━━━━━━━━━\n"]
            for role, content in reversed(rows):
                try:
                    text = decrypt(key, content) if content and content.startswith("enc:") else content
                except Exception:
                    logger.debug("Failed to decrypt message content for history display", exc_info=True)
                    text = "[encrypted]"
                icon = "\U0001f464" if role == "user" else "\U0001f43e"
                preview = (text or "")[:120].replace("\n", " ").replace("<", "&lt;")
                lines.append(f"{icon} {preview}")
            await self._reply(update, "\n".join(lines))
        except Exception as e:
            await self._reply(update, f"\u274c Failed: {e}")

    # -- /wipe -------------------------------------------------------------

    async def _handle_wipe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("\u2705 Yes, wipe", callback_data="wipe:confirm"),
            InlineKeyboardButton("\u274c Cancel", callback_data="wipe:cancel"),
        ]])
        await update.message.reply_text(
            "\U0001f9f9 Delete ALL conversation history?\n\n<i>This cannot be undone.</i>",
            reply_markup=keyboard, parse_mode="HTML",
        )

    # -- /nuke -------------------------------------------------------------

    async def _handle_nuke(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("\U0001f4ac Conversations", callback_data="nuke:messages")],
            [InlineKeyboardButton("\U0001f9e0 Memories", callback_data="nuke:memories")],
            [InlineKeyboardButton("\U0001f4dd Daily logs", callback_data="nuke:logs")],
            [InlineKeyboardButton("\U0001f511 Vault (API keys)", callback_data="nuke:vault")],
            [InlineKeyboardButton("\U0001f4a5 EVERYTHING", callback_data="nuke:all")],
            [InlineKeyboardButton("\u274c Cancel", callback_data="nuke:cancel")],
        ])
        await update.message.reply_text(
            "\U0001f4a3 <b>What do you want to delete?</b>",
            reply_markup=keyboard, parse_mode="HTML",
        )

    # -- /mcp --------------------------------------------------------------

    async def _handle_mcp(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []
        from lazyclaw.mcp.manager import (
            list_servers, set_favorite, connect_server, disconnect_server,
            _active_clients, BUNDLED_MCPS, _resolve_mcp_name,
            install_bundled_mcp, auto_register_bundled_mcps,
        )

        # -- /mcp (no args) — list registered + available-to-install ----------
        if not args:
            servers = await list_servers(self._config, user_id)
            registered_names = {s.get("name") for s in servers}

            lines: list[str] = []
            if servers:
                connected_count = sum(
                    1 for s in servers if s["id"] in _active_clients
                )
                lines.append(
                    f"\U0001f50c <b>MCP Servers</b>  "
                    f"({connected_count}/{len(servers)} online)"
                )
                lines.append("━━━━━━━━━━━━━━━━━━")
                for s in servers:
                    sname = s.get("name", "?")
                    sid = s["id"]
                    fav = "\u2b50 " if s.get("favorite") else ""
                    is_connected = sid in _active_clients
                    # Green = connected, Blue = installed/idle
                    status = "\U0001f7e2" if is_connected else "\U0001f535"
                    desc = BUNDLED_MCPS.get(sname, {}).get("description", "")
                    state_label = "online" if is_connected else "idle"
                    lines.append(f"{fav}{status} <b>{sname}</b>  <i>({state_label})</i>")
                    if desc:
                        lines.append(f"      <i>{desc[:60]}</i>")

            # Show uninstalled bundled MCPs
            uninstalled = [
                (n, info) for n, info in BUNDLED_MCPS.items()
                if n not in registered_names
            ]
            if uninstalled:
                lines.append("")
                lines.append("\U0001f4e6 <b>Available to install:</b>")
                for n, info in uninstalled:
                    desc = info.get("description", "")[:55]
                    lines.append(f"  \u2b07\ufe0f <code>{n}</code>")
                    if desc:
                        lines.append(f"      <i>{desc}</i>")

            if not servers and not uninstalled:
                await self._reply(update, "\U0001f50c No MCP servers available.")
                return

            lines.append("")
            lines.append(
                "\U0001f527 <b>Commands:</b>\n"
                "<code>/mcp install NAME</code>\n"
                "<code>/mcp install all</code>\n"
                "<code>/mcp connect NAME</code>\n"
                "<code>/mcp disconnect NAME</code>\n"
                "<code>/mcp fav NAME</code>  \u2022  <code>/mcp unfav NAME</code>"
            )
            await self._reply(update, "\n".join(lines))
            return

        subcmd = args[0].lower()
        name = " ".join(args[1:]) if len(args) > 1 else ""

        # -- /mcp install all | /mcp install NAME ----------------------------
        if subcmd == "install":
            # /mcp install all — batch install every bundled MCP
            if name.lower() == "all":
                await self._reply(update, "\u23f3 Installing all bundled MCPs...")
                results: list[str] = []
                for mcp_key in BUNDLED_MCPS:
                    try:
                        ok, msg = await install_bundled_mcp(mcp_key)
                        icon = "\u2705" if ok else "\u274c"
                        results.append(f"{icon} <b>{mcp_key}</b> — {msg}")
                    except Exception as e:
                        results.append(f"\u274c <b>{mcp_key}</b> — {e}")
                await auto_register_bundled_mcps(self._config, user_id)
                await self._reply(update, "\n".join(results))
                return

            if not name:
                await self._reply(
                    update,
                    "\U0001f50c Usage:\n"
                    "<code>/mcp install NAME</code>\n"
                    "<code>/mcp install all</code>",
                )
                return
            mcp_name = _resolve_mcp_name(name)
            if not mcp_name:
                await self._reply(update, f"\u274c Unknown MCP: {name}")
                return
            await self._reply(
                update, f"\u23f3 Installing <b>{mcp_name}</b>..."
            )
            try:
                success, msg = await install_bundled_mcp(mcp_name)
                if not success:
                    await self._reply(update, f"\u274c {msg}")
                    return
                # Register in DB + connect
                await auto_register_bundled_mcps(self._config, user_id)
                servers = await list_servers(self._config, user_id)
                server = next(
                    (s for s in servers if s["name"] == mcp_name), None
                )
                if server:
                    await connect_server(self._config, user_id, server["id"])
                    await self._reply(
                        update,
                        f"\u2705 Installed and connected: <b>{mcp_name}</b>",
                    )
                else:
                    await self._reply(
                        update, f"\u2705 {msg}\nUse <code>/mcp connect {mcp_name}</code>"
                    )
            except Exception as e:
                await self._reply(update, f"\u274c Install failed: {e}")
            return

        # -- /mcp connect all --------------------------------------------------
        if subcmd == "connect" and name.lower() == "all":
            servers = await list_servers(self._config, user_id)
            results: list[str] = []
            for s in servers:
                if s["id"] in _active_clients:
                    results.append(f"\U0001f7e2 <b>{s['name']}</b> — already online")
                    continue
                try:
                    await connect_server(self._config, user_id, s["id"])
                    results.append(f"\U0001f7e2 <b>{s['name']}</b> — connected")
                except Exception as e:
                    err_msg = str(e)[:50]
                    results.append(f"\u274c <b>{s['name']}</b> — {err_msg}")
            await self._reply(update, "\n".join(results) if results else "No servers to connect.")
            return

        if not name:
            await self._reply(
                update, f"\U0001f50c Usage: <code>/mcp {subcmd} NAME</code>"
            )
            return

        # -- Find server in DB -------------------------------------------------
        servers = await list_servers(self._config, user_id)
        server = next(
            (s for s in servers if s["name"].lower() == name.lower()), None
        )
        if not server:
            server = next(
                (s for s in servers if name.lower() in s["name"].lower()), None
            )

        # -- Auto-install on connect if not found ------------------------------
        if not server and subcmd == "connect":
            mcp_name = _resolve_mcp_name(name)
            if mcp_name:
                await self._reply(
                    update, f"\u23f3 Installing <b>{mcp_name}</b>..."
                )
                try:
                    success, msg = await install_bundled_mcp(mcp_name)
                    if not success:
                        await self._reply(update, f"\u274c {msg}")
                        return
                    await auto_register_bundled_mcps(self._config, user_id)
                    servers = await list_servers(self._config, user_id)
                    server = next(
                        (s for s in servers if s["name"] == mcp_name), None
                    )
                    if server:
                        await connect_server(
                            self._config, user_id, server["id"]
                        )
                        await self._reply(
                            update,
                            f"\u2705 Installed and connected: <b>{mcp_name}</b>",
                        )
                        return
                    else:
                        await self._reply(
                            update,
                            f"\u2705 Installed but could not register.\n"
                            f"Try <code>/mcp connect {mcp_name}</code>",
                        )
                        return
                except Exception as e:
                    logger.exception("MCP auto-install failed for %s", mcp_name)
                    await self._reply(update, f"\u274c Install failed: {e}")
                    return

        if not server:
            await self._reply(
                update,
                f"\u274c Server '{name}' not found. Use /mcp to list.",
            )
            return

        try:
            if subcmd == "fav":
                await set_favorite(self._config, user_id, server["name"], True)
                await self._reply(
                    update, f"\u2b50 <b>{server['name']}</b> added to favorites"
                )
            elif subcmd == "unfav":
                await set_favorite(
                    self._config, user_id, server["name"], False
                )
                await self._reply(
                    update,
                    f"\u2705 <b>{server['name']}</b> removed from favorites",
                )
            elif subcmd == "connect":
                await connect_server(self._config, user_id, server["id"])
                await self._reply(
                    update, f"\u2705 Connected: <b>{server['name']}</b>"
                )
            elif subcmd in ("disconnect", "disc"):
                await disconnect_server(user_id, server["id"])
                await self._reply(
                    update,
                    f"\U0001f50c Disconnected: <b>{server['name']}</b>",
                )
            else:
                await self._reply(
                    update,
                    "\U0001f50c Subcommands: install, connect, disconnect, fav, unfav",
                )
        except Exception as e:
            logger.exception("MCP command %s failed", subcmd)
            await self._reply(update, f"\u274c Failed: {e}")

    # -- /watch ------------------------------------------------------------

    async def _handle_watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []
        subcmd = args[0].lower() if args else ""

        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from lazyclaw.heartbeat.orchestrator import list_jobs, delete_job

        jobs = await list_jobs(self._config, user_id)
        watchers = [j for j in jobs if j.get("job_type") == "watcher"
                    and j.get("status") in ("active", "paused")]

        # /watch stop [name] — stop a watcher
        if subcmd == "stop":
            target = " ".join(args[1:]).lower() if len(args) > 1 else ""
            if not watchers:
                await self._reply(update, "\U0001f514 No active watchers to stop.")
                return
            # Find match
            match = None
            for w in watchers:
                wname = (w.get("name") or "").lower()
                if not target or target in wname:
                    match = w
                    break
            if not match:
                await self._reply(update, f"\u274c No watcher matching '{target}' found.")
                return
            await delete_job(self._config, user_id, match["id"])
            await self._reply(update, f"\U0001f6d1 Stopped: <b>{match.get('name', '?')}</b>")
            return

        # /watch mute GroupName — mute a group in the WhatsApp watcher
        if subcmd == "mute":
            group_name = " ".join(args[1:]).strip() if len(args) > 1 else ""
            if not group_name:
                await self._reply(update, "\U0001f507 Usage: <code>/watch mute Group Name</code>")
                return
            wa_watcher = next(
                (w for w in watchers if "whatsapp" in (w.get("name") or "").lower()),
                None,
            )
            if not wa_watcher:
                await self._reply(update, "\u274c No active WhatsApp watcher. Start one with <code>/watch whatsapp</code>")
                return
            raw_ctx = wa_watcher.get("context", "{}")
            try:
                ctx = _json.loads(raw_ctx) if raw_ctx and not str(raw_ctx).startswith("enc:") else {}
            except (ValueError, TypeError):
                logger.debug("Failed to parse WhatsApp watcher context JSON", exc_info=True)
                ctx = {}
            muted = [g for g in ctx.get("muted_groups", [])]
            if group_name.lower() not in [g.lower() for g in muted]:
                muted.append(group_name)
            ctx["muted_groups"] = muted
            from lazyclaw.heartbeat.orchestrator import update_job
            await update_job(self._config, user_id, wa_watcher["id"], context=_json.dumps(ctx))
            await self._reply(update, f"\U0001f507 Muted: <b>{group_name}</b>\nGroup messages won't trigger notifications.")
            return

        # /watch unmute GroupName — unmute a group in the WhatsApp watcher
        if subcmd == "unmute":
            group_name = " ".join(args[1:]).strip() if len(args) > 1 else ""
            if not group_name:
                # Show currently muted groups
                wa_watcher = next(
                    (w for w in watchers if "whatsapp" in (w.get("name") or "").lower()),
                    None,
                )
                if wa_watcher:
                    raw_ctx = wa_watcher.get("context", "{}")
                    try:
                        ctx = _json.loads(raw_ctx) if raw_ctx and not str(raw_ctx).startswith("enc:") else {}
                    except (ValueError, TypeError):
                        logger.debug("Failed to parse WhatsApp watcher context JSON", exc_info=True)
                        ctx = {}
                    muted = ctx.get("muted_groups", [])
                    if muted:
                        group_list = "\n".join(f"  \U0001f507 {g}" for g in muted)
                        await self._reply(
                            update,
                            f"\U0001f507 <b>Muted groups:</b>\n{group_list}\n\n"
                            f"Use <code>/watch unmute Group Name</code> to unmute.",
                        )
                    else:
                        await self._reply(update, "\U0001f50a No muted groups.")
                else:
                    await self._reply(update, "\u274c No active WhatsApp watcher.")
                return
            wa_watcher = next(
                (w for w in watchers if "whatsapp" in (w.get("name") or "").lower()),
                None,
            )
            if not wa_watcher:
                await self._reply(update, "\u274c No active WhatsApp watcher.")
                return
            raw_ctx = wa_watcher.get("context", "{}")
            try:
                ctx = _json.loads(raw_ctx) if raw_ctx and not str(raw_ctx).startswith("enc:") else {}
            except (ValueError, TypeError):
                logger.debug("Failed to parse WhatsApp watcher context JSON for unmute", exc_info=True)
                ctx = {}
            muted = ctx.get("muted_groups", [])
            ctx["muted_groups"] = [g for g in muted if g.lower() != group_name.lower()]
            from lazyclaw.heartbeat.orchestrator import update_job
            await update_job(self._config, user_id, wa_watcher["id"], context=_json.dumps(ctx))
            await self._reply(update, f"\U0001f50a Unmuted: <b>{group_name}</b>")
            return

        # /watch whatsapp [minutes] — create a new watcher
        if subcmd in ("whatsapp", "wa", "email", "instagram", "ig"):
            service = {"wa": "whatsapp", "ig": "instagram"}.get(subcmd, subcmd)
            interval_min = 2
            if len(args) > 1:
                try:
                    interval_min = max(1, int(args[1]))
                except ValueError:
                    logger.warning("Invalid watcher interval %r, using default %d min", args[1], interval_min)

            # Check if already watching this service
            already = any(service in (w.get("name") or "").lower() for w in watchers)
            if already:
                await self._reply(
                    update,
                    f"\u26a0\ufe0f Already watching {service}. "
                    f"Use <code>/watch stop</code> first."
                )
                return

            # Create watcher via skill
            from lazyclaw.skills.builtin.watch_mcp import WatchMCPSkill
            skill = WatchMCPSkill(config=self._config)
            result = await skill.execute(user_id, {
                "service": service,
                "check_interval_minutes": interval_min,
                "duration_hours": -1,  # Infinite
            })
            await self._reply(update, f"\U0001f514 {result}")
            return

        # /watch (no args) — list active watchers
        if not watchers:
            await self._reply(
                update,
                "\U0001f514 <b>Watchers</b>\n"
                "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
                "No active watchers.\n\n"
                "\U0001f527 <b>Commands:</b>\n"
                "<code>/watch whatsapp</code> \u2014 watch WhatsApp (2 min)\n"
                "<code>/watch whatsapp 1</code> \u2014 watch every 1 min\n"
                "<code>/watch email</code> \u2014 watch Email\n"
                "<code>/watch mute GroupName</code> \u2014 silence a group\n"
                "<code>/watch unmute</code> \u2014 list/unmute groups\n"
                "<code>/watch stop</code> \u2014 stop a watcher"
            )
            return

        lines = [
            "\U0001f514 <b>Watchers</b>",
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        ]
        for w in watchers:
            name = w.get("name", "?")
            status = w.get("status", "?")
            icon = "\u2705" if status == "active" else "\u23f8"

            raw_ctx = w.get("context", "{}")
            try:
                ctx = _json.loads(raw_ctx) if raw_ctx and not str(raw_ctx).startswith("enc:") else {}
            except (ValueError, TypeError):
                logger.debug("Failed to parse watcher context JSON for status", exc_info=True)
                ctx = {}

            service = ctx.get("service", "")
            interval = int(ctx.get("check_interval", 120)) // 60
            last_check = ctx.get("last_check", 0)
            seen = len(ctx.get("last_seen_ids", []))

            time_str = ""
            if last_check and last_check > 0:
                last_dt = _dt.fromtimestamp(last_check, tz=_tz.utc)
                time_str = last_dt.strftime("%H:%M")

            muted_count = len(ctx.get("muted_groups", []))
            details = f"every {interval}m"
            if time_str:
                details += f" \u2022 last {time_str}"
            if seen:
                details += f" \u2022 {seen} seen"
            if muted_count:
                details += f" \u2022 \U0001f507{muted_count} muted"

            lines.append(f"\n{icon} <b>{name}</b>")
            lines.append(f"    {details}")

        lines.append(
            "\n\n\U0001f527 <code>/watch stop</code> \u2014 stop a watcher\n"
            "\U0001f507 <code>/watch mute GroupName</code> \u2014 silence a group"
        )
        await self._reply(update, "\n".join(lines))

    # -- /whatsapp, /instagram, /email -------------------------------------

    async def _handle_platform(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        chat_id = str(update.effective_chat.id)
        cmd = update.message.text.split()[0].lstrip("/").lower()
        subcmd = (context.args[0].lower() if context.args else "status")
        icons = {"whatsapp": "\U0001f4f1", "instagram": "\U0001f4f7", "email": "\U0001f4e7"}
        await self._reply(update, f"{icons.get(cmd, '')} Checking {cmd}...")
        await self._agent_dispatch(update, chat_id, user_id, f"{cmd} {subcmd}")

    # -- /survival ---------------------------------------------------------

    async def _handle_survival(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []
        if not args:
            await self._reply(update, "\U0001f4bc Usage: <code>/survival on|off|status</code>")
            return
        chat_id = str(update.effective_chat.id)
        subcmd = args[0].lower()
        await self._reply(update, f"\U0001f4bc Survival {subcmd}...")
        await self._agent_dispatch(update, chat_id, user_id, f"survival mode {subcmd}")

    # -- /profile ----------------------------------------------------------

    async def _handle_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []
        try:
            from lazyclaw.survival.profile import get_profile, update_profile
        except ImportError:
            await self._reply(update, "\u274c Survival module not available.")
            return
        if not args:
            try:
                p = await get_profile(self._config, user_id)
                skills = ", ".join(p.skills) if p.skills else "<i>not set</i>"
                platforms = ", ".join(p.platforms) if p.platforms else "<i>not set</i>"
                await self._reply(update,
                    f"\U0001f464 <b>Freelance Profile</b>\n━━━━━━━━━━━━\n\n"
                    f"\U0001f4bc Title: {p.title or '<i>not set</i>'}\n"
                    f"\U0001f528 Skills: {skills}\n"
                    f"\U0001f4b5 Rate: ${p.min_hourly_rate}/hr\n"
                    f"\U0001f310 Platforms: {platforms}"
                )
            except Exception:
                logger.debug("Failed to load user profile for display", exc_info=True)
                await self._reply(update, "\U0001f464 No profile set. Try: <code>/profile skills python,react</code>")
            return
        field, value = args[0].lower(), " ".join(args[1:]) if len(args) > 1 else ""
        if not value:
            await self._reply(update, f"\U0001f464 Usage: <code>/profile {field} VALUE</code>")
            return
        try:
            updates = {}
            if field == "skills":
                updates["skills"] = [s.strip() for s in value.split(",")]
            elif field == "rate":
                updates["min_hourly_rate"] = float(value)
            elif field == "platforms":
                updates["platforms"] = [p.strip() for p in value.split(",")]
            elif field == "title":
                updates["title"] = value
            else:
                await self._reply(update, "\U0001f464 Fields: skills, rate, platforms, title")
                return
            await update_profile(self._config, user_id, updates)
            await self._reply(update, f"\u2705 Updated <b>{field}</b>: {value}")
        except Exception as e:
            await self._reply(update, f"\u274c Failed: {e}")

    # -- /browser ----------------------------------------------------------

    async def _handle_browser(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        subcmd = (context.args[0].lower() if context.args else "status")
        if subcmd == "status":
            try:
                from lazyclaw.browser.browser_settings import browser_idle_seconds
                idle = browser_idle_seconds()
                if idle < 3600:
                    await self._reply(update, f"\U0001f310 Browser: <b>active</b> (idle {idle:.0f}s)")
                else:
                    await self._reply(update, "\U0001f310 Browser: <b>idle</b>")
            except Exception:
                logger.debug("Browser status check failed, assuming not running", exc_info=True)
                await self._reply(update, "\U0001f310 Browser: <b>not running</b>")
        elif subcmd == "close":
            try:
                from lazyclaw.browser.cdp_backend import close_browser
                await close_browser()
                await self._reply(update, "\u2705 Browser closed.")
            except Exception as e:
                await self._reply(update, f"\u274c Failed: {e}")
        elif subcmd == "screenshot":
            chat_id = str(update.effective_chat.id)
            await self._agent_dispatch(update, chat_id, user_id, "take a screenshot and send it")
        else:
            await self._reply(update, "\U0001f310 Usage: <code>/browser status|close|screenshot</code>")

    # -- /usebrowser -------------------------------------------------------

    async def _handle_usebrowser(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Bridge the agent to the user's REAL host Brave via CDP."""
        user_id = await self._auth(update)
        if not user_id:
            return
        subcmd = (context.args[0].lower() if context.args else "start")

        from lazyclaw.browser import host_bridge
        from lazyclaw.browser.browser_settings import (
            get_browser_settings, update_browser_settings,
        )

        port = getattr(self._config, "cdp_port", 9222)

        if subcmd == "status":
            settings = await get_browser_settings(self._config, user_id)
            ws = await host_bridge.probe_host_cdp(port)
            mode = settings.get("use_host_browser", "off")
            last = settings.get("last_host_cdp_source") or "never"
            runtime = "docker" if host_bridge.is_docker_runtime() else "native"
            await self._reply(
                update,
                f"\U0001f5a5️ <b>Host browser bridge</b>\n"
                f"Mode: <code>{mode}</code>\n"
                f"Runtime: <code>{runtime}</code>\n"
                f"Reachable now: <code>{'yes' if ws else 'no'}</code>\n"
                f"Last source: <code>{last}</code>",
            )
            return

        if subcmd == "stop":
            await update_browser_settings(
                self._config, user_id, {"use_host_browser": "off"},
            )
            await self._reply(
                update,
                "✅ Host browser bridge stopped. Next browser action uses the container Brave.",
            )
            return

        # start (default)
        settings = await get_browser_settings(self._config, user_id)
        token = settings.get("host_cdp_token") or host_bridge.generate_host_token()
        if token != settings.get("host_cdp_token"):
            await update_browser_settings(
                self._config, user_id, {"host_cdp_token": token},
            )
        await update_browser_settings(
            self._config, user_id, {"use_host_browser": "auto"},
        )

        ws = await host_bridge.probe_host_cdp(port)
        if ws:
            await self._reply(
                update,
                "✅ <b>Using your real Brave</b>\n"
                "All your cookies, saved logins, and extensions are in play. "
                "Send <code>/usebrowser stop</code> to revert.",
            )
            return

        cmd = host_bridge.build_launch_command(token)
        warning = host_bridge.security_warning()
        # Telegram needs HTML-escaping for <code>; we send as plain text with
        # a code block to keep the one-liner pasteable.
        await self._reply(
            update,
            "\U0001f5a5️ <b>Host Brave not reachable yet</b>\n\n"
            "Run this on your Mac (paste into Terminal):\n"
            f"<pre>{cmd}</pre>\n"
            f"⚠️ {warning}\n\n"
            "Once Brave is running with those flags, send <code>/usebrowser</code> again.",
        )

    # -- /screen -----------------------------------------------------------

    async def _handle_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        subcmd = (context.args[0].lower() if context.args else "shot")

        if subcmd in ("shot", "screenshot"):
            # Full desktop screenshot → send as photo
            from lazyclaw.browser.remote_takeover import take_desktop_screenshot
            await self._reply(update, "\U0001f4f8 Capturing screen...")
            data = await take_desktop_screenshot()
            if data:
                import io
                from lazyclaw.channels.telegram import _telegram_send_with_retry
                await _telegram_send_with_retry(
                    lambda: update.message.reply_photo(
                        photo=io.BytesIO(data),
                        caption="\U0001f5a5 Desktop screenshot",
                    )
                )
            else:
                await self._reply(update, "\u274c Screenshot failed. Check permissions (macOS: Settings \u2192 Privacy \u2192 Screen Recording).")

        elif subcmd == "vnc":
            # Start VNC session → send noVNC link
            import sys
            from lazyclaw.browser.remote_takeover import (
                is_remote_capable, get_active_session,
                start_macos_remote_session, start_remote_session,
                _IS_MACOS,
            )

            # Check if session already active
            existing = get_active_session(user_id)
            if existing:
                await self._reply(update,
                    f"\U0001f5a5 <b>VNC session active</b>\n\n"
                    f"Tap to connect:\n{existing.url}"
                )
                return

            if not is_remote_capable():
                if _IS_MACOS:
                    await self._reply(update,
                        "\u274c <b>VNC not ready</b>\n\n"
                        "Install: <code>pip install websockify</code>\n"
                        "noVNC: <code>git clone https://github.com/novnc/noVNC data/novnc</code>\n"
                        "Enable: System Settings \u2192 General \u2192 Sharing \u2192 Screen Sharing"
                    )
                else:
                    await self._reply(update,
                        "\u274c <b>VNC not ready</b>\n\n"
                        "Install: <code>apt install x11vnc xvfb</code>\n"
                        "<code>pip install websockify</code>\n"
                        "Set: <code>LAZYCLAW_SERVER_MODE=true</code>"
                    )
                return

            try:
                if _IS_MACOS:
                    session = await start_macos_remote_session(user_id)
                else:
                    # Linux server mode — needs browser info
                    from lazyclaw.browser.cdp_backend import get_cdp_port, get_profile_dir, get_browser_binary
                    session = await start_remote_session(
                        user_id,
                        cdp_port=get_cdp_port(),
                        profile_dir=get_profile_dir(user_id),
                        browser_bin=get_browser_binary(),
                    )
                await self._reply(update,
                    f"\U0001f5a5 <b>VNC session started</b>\n\n"
                    f"Tap to take control:\n{session.url}\n\n"
                    f"<i>Auto-closes in 5 minutes if unused.</i>"
                )
            except RuntimeError as e:
                await self._reply(update, f"\u274c {e}")
            except Exception as e:
                await self._reply(update, f"\u274c VNC failed: {e}")

        elif subcmd == "stop":
            from lazyclaw.browser.remote_takeover import stop_remote_session
            await stop_remote_session(user_id)
            await self._reply(update, "\u2705 VNC session stopped.")

        else:
            await self._reply(update,
                "\U0001f5a5 <b>Screen</b>\n\n"
                "<code>/screen</code> \u2014 Desktop screenshot\n"
                "<code>/screen vnc</code> \u2014 Start VNC remote control\n"
                "<code>/screen stop</code> \u2014 Stop VNC session"
            )

    # -- /qr — Force WhatsApp QR re-link -----------------------------------

    async def _handle_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return

        await self._reply(update, "\U0001f4f1 Forcing WhatsApp re-link...")

        from lazyclaw.mcp.manager import (
            _active_clients, list_servers, connect_server, disconnect_server,
        )

        # Find WhatsApp MCP server
        servers = await list_servers(self._config, user_id)
        wa_server = None
        for s in servers:
            if "whatsapp" in s.get("name", "").lower():
                wa_server = s
                break

        if not wa_server:
            await self._reply(update, "\u274c WhatsApp MCP not installed. Use <code>/mcp install mcp-whatsapp</code>")
            return

        server_id = wa_server["id"]

        # Disconnect existing connection
        if server_id in _active_clients:
            try:
                await disconnect_server(user_id, server_id)
            except Exception:
                logger.warning("Failed to disconnect WhatsApp server %s before reconnect", server_id, exc_info=True)

        # Delete auth state so Baileys generates a fresh QR
        import shutil
        data_dir = self._config.data_dir / "whatsapp_sessions" / "baileys_auth"
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)
            data_dir.mkdir(parents=True, exist_ok=True)

        # Reconnect — this spawns a new process which will emit a QR event
        # The QR gets auto-sent to Telegram via the WhatsApp MCP's built-in sender
        try:
            await connect_server(self._config, user_id, server_id, force=True)
        except Exception as exc:
            logger.warning("QR reconnect error: %s", exc)

        # Wait a moment for QR to be generated and sent via Telegram
        await asyncio.sleep(4)

        # Try calling whatsapp_setup to check status
        client = _active_clients.get(server_id)
        if client and client.is_connected:
            try:
                result = await client.call_tool("whatsapp_setup", {})
                import json
                data = json.loads(result)
                status = data.get("data", {}).get("status", "unknown")
                if status == "connected":
                    await self._reply(
                        update,
                        "\u2705 WhatsApp already connected — no QR needed!\n"
                        "If you want to force a fresh QR, first unlink this device in WhatsApp → Linked Devices.",
                    )
                    return
                if status == "qr_needed":
                    await self._reply(
                        update,
                        "\U0001f4f2 QR code sent above as a photo — scan it now!\n\n"
                        "<b>Steps:</b>\n"
                        "1. Open WhatsApp on phone\n"
                        "2. Settings → Linked Devices → Link a Device\n"
                        "3. Scan the QR image\n\n"
                        "\u23f3 You have ~60 seconds.",
                    )
                    return
            except Exception:
                logger.warning("Failed to check WhatsApp status after QR reconnect", exc_info=True)

        await self._reply(
            update,
            "\U0001f504 WhatsApp reconnecting... QR should appear as a photo above.\n"
            "If no QR appeared, the session may still be valid — try <code>/watch whatsapp</code>.",
        )

    # -- /recovery ---------------------------------------------------------

    async def _handle_recovery(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Generate or regenerate the account recovery phrase.

        Usage:
          /recovery            — explain the feature
          /recovery generate   — generate a new phrase (sends privately, auto-deletes in 60s)
        """
        user_id = await self._auth(update)
        if not user_id:
            return

        if not context.args or context.args[0] != "generate":
            await self._reply(update,
                "\U0001f510 <b>Recovery Phrase</b>\n\n"
                "A recovery phrase lets you regain access to your account if you forget your password.\n\n"
                "To generate or regenerate your phrase, run:\n"
                "<code>/recovery generate</code>\n\n"
                "\u26a0\ufe0f You will be prompted to confirm your password via DM.\n"
                "\u26a0\ufe0f The phrase is shown <b>once</b> and auto-deletes after 60 seconds.\n"
                "\u26a0\ufe0f Generating a new phrase invalidates the previous one."
            )
            return

        # Prompt for password (can't be passed in command for security)
        await self._reply(update,
            "\U0001f510 <b>Recovery phrase generation</b>\n\n"
            "Reply with your current password to generate a recovery phrase.\n\n"
            "<b>Send it as a reply to this message</b> — your password message will be "
            "deleted immediately after use.\n\n"
            "\u26a0\ufe0f If you use Telegram in a group, move to a private chat with the bot first."
        )
        # Store pending recovery state on the adapter so the next message is handled
        self._adapter._pending_recovery.add(user_id)

    # -- /resetpass ----------------------------------------------------------

    async def _handle_resetpass(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reset web UI password from Telegram (admin-only, no old password needed).

        Usage: /resetpass <new_password>
        """
        user_id = await self._auth(update)
        if not user_id:
            return

        chat_id = str(update.effective_chat.id)
        if chat_id != self._adapter._admin_chat_id:
            await self._reply(update, "\U0001f451 Only the primary admin can reset passwords.")
            return

        if not context.args:
            await self._reply(update,
                "\U0001f510 <b>Reset Web UI Password</b>\n\n"
                "Usage: <code>/resetpass &lt;new_password&gt;</code>\n\n"
                "This resets your web dashboard login password.\n"
                "No old password needed — Telegram admin access is your proof of identity."
            )
            return

        new_password = " ".join(context.args)
        if len(new_password) < 4:
            await self._reply(update, "\u26a0\ufe0f Password too short (min 4 characters).")
            return

        try:
            from lazyclaw.gateway.auth import hash_password
            from lazyclaw.db.connection import db_session

            pw_hash = hash_password(new_password)
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (pw_hash, user_id),
                )
                await db.commit()

            # Delete the command message (contains password in plaintext)
            try:
                await update.message.delete()
            except Exception:
                logger.debug("Could not delete plaintext password message from chat", exc_info=True)

            await self._reply(update,
                "\u2705 <b>Password reset!</b>\n\n"
                "You can now log in at the web dashboard with your new password.\n"
                "Username: <code>blck</code>"
            )
        except Exception as exc:
            logger.error("Password reset failed: %s", exc)
            await self._reply(update, f"\u274c Password reset failed: {exc}")

    # -- /addadmin, /removeadmin -------------------------------------------

    async def _handle_addadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        if chat_id != self._adapter._admin_chat_id:
            await self._reply(update, "\U0001f451 Only the primary admin can add admins.")
            return
        if not context.args:
            await self._reply(update, "\U0001f451 Usage: <code>/addadmin CHAT_ID</code>")
            return
        new_chat = context.args[0]
        self._adapter._allowed_chats.add(new_chat)
        await self._reply(update, f"\u2705 Added admin: <code>{new_chat}</code>")

    async def _handle_removeadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        if chat_id != self._adapter._admin_chat_id:
            await self._reply(update, "\U0001f451 Only the primary admin can remove admins.")
            return
        if not context.args:
            await self._reply(update, "\U0001f451 Usage: <code>/removeadmin CHAT_ID</code>")
            return
        target = context.args[0]
        if target == self._adapter._admin_chat_id:
            await self._reply(update, "\u274c Can't remove the primary admin.")
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("\u2705 Yes, remove", callback_data=f"rmadmin:{target}"),
            InlineKeyboardButton("\u274c Cancel", callback_data="rmadmin:cancel"),
        ]])
        await update.message.reply_text(
            f"\U0001f451 Remove admin <code>{target}</code>?",
            reply_markup=keyboard, parse_mode="HTML",
        )

    # -- /search -----------------------------------------------------------

    async def _handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Switch search provider or show usage.

        Usage:
          /search             — show current provider + usage
          /search auto        — Brave → scraper → DDG (default)
          /search brave       — Brave Search API only
          /search scraper     — mcp-scraper Google scrape only
          /search duckduckgo  — DuckDuckGo only
        """
        user_id = await self._auth(update)
        if not user_id:
            return

        from lazyclaw.skills.builtin.web_search import (
            get_active_provider,
            get_search_usage,
            set_active_provider,
        )

        if context.args:
            arg = context.args[0].lower()
            if arg in ("auto", "brave", "scraper", "duckduckgo"):
                try:
                    from lazyclaw.settings.general import update_general_settings

                    await update_general_settings(
                        self._config, user_id, {"search_provider": arg}
                    )
                except Exception as exc:
                    await self._reply(update, f"\u274c Failed to save: {exc}")
                    return
                # Mirror non-auto picks to the global default so other
                # users without a preference inherit a sensible choice.
                if arg != "auto":
                    set_active_provider(arg)
                await self._reply(update, f"\U0001f50d Search provider set to: {arg}")
                return
            await self._reply(
                update,
                "\u2753 Usage: <code>/search auto|brave|scraper|duckduckgo</code>",
            )
            return

        provider = get_active_provider()
        usage = get_search_usage()
        await self._reply(update,
            f"\U0001f50d <b>Search Provider</b>\n\n"
            f"Active: <b>{provider}</b>\n"
            f"Usage this month: {usage.status()}\n\n"
            f"Switch: <code>/search brave</code> or <code>/search auto</code>"
        )

    # -- /note, /idea, /remember -------------------------------------------

    async def _save_quick_note(
        self, update: Update, kind_tag: str, content: str,
    ) -> None:
        """Shared backend for the three note-capture commands. Talks
        directly to LazyBrain — no agent LLM, no tokens."""
        from lazyclaw.channels.note_capture import (
            kind_label,
            save_quick_note,
        )

        user_id = await self._auth(update)
        if not user_id:
            return
        content = (content or "").strip()
        if not content:
            await self._reply(
                update,
                f"✍️ Type the {kind_label(kind_tag).lower()} after the command.\n\n"
                f"<code>/note buy organic eggs at the market</code>",
            )
            return
        try:
            note = await save_quick_note(
                self._config, user_id, content, kind_tag,
                source="telegram",
            )
        except Exception as exc:
            logger.warning("quick note save failed", exc_info=True)
            await self._reply(
                update, f"⚠️ Could not save: {exc}",
            )
            return
        label = kind_label(kind_tag)
        snippet = content if len(content) <= 80 else content[:77] + "…"
        await self._reply(
            update,
            f"\U0001f4dd <b>{label} saved</b>\n<code>{snippet}</code>\n\n"
            f"<i>Open the Notes page on the Web UI to edit.</i>",
        )

    async def _handle_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.message.text or "")
        body = text.partition(" ")[2]  # everything after "/note "
        await self._save_quick_note(update, "kind/note", body)

    async def _handle_idea(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.message.text or "")
        body = text.partition(" ")[2]
        await self._save_quick_note(update, "kind/idea", body)

    async def _handle_remember(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.message.text or "")
        body = text.partition(" ")[2]
        await self._save_quick_note(update, "kind/memory", body)

    # -- Permissions -------------------------------------------------------

    async def _handle_permissions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show current category defaults + per-skill overrides."""
        user_id = await self._auth(update)
        if not user_id:
            return

        from lazyclaw.permissions.settings import get_permission_settings

        settings = await get_permission_settings(self._config, user_id)
        cat = settings.get("category_defaults", {})
        overrides = settings.get("skill_overrides", {})
        icon = {"allow": "✅", "ask": "❓", "deny": "\U0001f6ab"}

        lines = ["\U0001f510 <b>Permissions</b>", "", "<b>Categories:</b>"]
        for name in sorted(cat):
            level = cat[name]
            lines.append(f"  {icon.get(level, '?')} <code>{name}</code> — {level}")

        if overrides:
            lines.append("")
            lines.append("<b>Skill overrides:</b>")
            for name in sorted(overrides):
                level = overrides[name]
                lines.append(f"  {icon.get(level, '?')} <code>{name}</code> — {level}")

        lines.append("")
        lines.append(
            "<i>Change with</i> <code>/allow &lt;name&gt;</code> "
            "<i>or</i> <code>/deny &lt;name&gt;</code>"
        )
        await self._reply(update, "\n".join(lines))

    async def _handle_allow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/allow <category|skill> — set permission to allow."""
        await self._apply_perm(update, context, "allow")

    async def _handle_deny(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/deny <category|skill> — set permission to deny."""
        await self._apply_perm(update, context, "deny")

    async def _apply_perm(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        level: str,
    ) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return

        if not context.args:
            await self._reply(
                update,
                f"❓ <b>Usage:</b> <code>/{level} &lt;category|skill&gt;</code>\n\n"
                "<b>Examples:</b>\n"
                f"  <code>/{level} tasks</code>\n"
                f"  <code>/{level} list_tasks</code>\n\n"
                "<i>Use</i> <code>/permissions</code> <i>to see current settings.</i>",
            )
            return

        target = context.args[0].strip()
        from lazyclaw.permissions.settings import apply_permission_change

        try:
            result = await apply_permission_change(
                self._config, user_id, target, level,
            )
        except ValueError as exc:
            await self._reply(update, f"❌ {exc}")
            return

        icon = {"allow": "✅", "ask": "❓", "deny": "\U0001f6ab"}[level]
        label = "Category" if result["kind"] == "category" else "Skill"
        await self._reply(
            update,
            f"{icon} <b>{label} '<code>{result['target']}</code>' set to {level}</b>",
        )

    # -- /esc — deliver the user's response to a pending escalation -------

    async def _handle_escalation_response(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/esc <escalation_id> <reply text>

        Wakes a pending escalation registered by ``escalate_to_human``
        with the user's decision. The skill execute() returns this text
        back to the calling agent so it can act on it (typically reply
        to an Upwork client).
        """
        user_id = await self._auth(update)
        if not user_id:
            return

        if not context.args or len(context.args) < 2:
            await self._reply(
                update,
                "❓ <b>Usage:</b> <code>/esc &lt;escalation_id&gt; &lt;your reply&gt;</code>\n\n"
                "<i>The escalation_id is the short code shown in the "
                "escalation push (e.g. 'a3f1d92e44'). The reply can be "
                "either a numeric option (1-4) or free text.</i>",
            )
            return

        escalation_id = context.args[0].strip()
        response = " ".join(context.args[1:]).strip()
        if not response:
            await self._reply(update, "⚠️ Reply text is empty.")
            return

        from lazyclaw.skills.builtin.escalate_to_human import deliver_response
        ok = deliver_response(escalation_id, response)
        if not ok:
            await self._reply(
                update,
                f"⚠️ No pending escalation with id <code>{escalation_id}</code>.\n\n"
                "<i>It may have already been resolved or timed out. "
                "Check the chat where the original push appeared.</i>",
            )
            return
        await self._reply(
            update,
            f"✅ <b>Escalation <code>{escalation_id}</code> delivered.</b>\n"
            f"<i>The agent will use your reply to respond to the client.</i>",
        )

    # -- /confirm and /reject — close the lesson verification loop ---------

    async def _handle_lesson_confirm(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/confirm <lesson_id> — promote pending lesson to verified."""
        await self._apply_lesson_transition(update, context, target="verified")

    async def _handle_lesson_reject(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/reject <lesson_id> [reason] — mark lesson known-bad."""
        await self._apply_lesson_transition(update, context, target="known-bad")

    async def _apply_lesson_transition(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        target: str,
    ) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return

        if not context.args:
            verb = "confirm" if target == "verified" else "reject"
            await self._reply(
                update,
                f"❓ <b>Usage:</b> <code>/{verb} &lt;lesson_id&gt; [reason]</code>\n\n"
                "<i>The lesson_id is the LazyBrain note ID of a kind/shape "
                "card — you'll see it appended to skill-lesson recall lines.</i>",
            )
            return

        lesson_id = context.args[0].strip()
        reason: str | None = None
        if len(context.args) > 1:
            reason = " ".join(context.args[1:]).strip() or None

        from lazyclaw.runtime.skill_lesson import transition_outcome

        try:
            result = await transition_outcome(
                self._config, user_id,
                lesson_id=lesson_id,
                target=target,
                reason=reason,
            )
        except Exception as exc:
            logger.warning(
                "lesson transition raised: %s", exc, exc_info=True,
            )
            await self._reply(update, f"❌ Transition failed: {exc}")
            return

        if not result.get("ok"):
            await self._reply(
                update,
                f"❌ Could not transition lesson <code>{lesson_id}</code>: "
                f"{result.get('reason') or 'unknown'}",
            )
            return

        icon = "✅" if target == "verified" else "\U0001f6ab"
        title = result.get("title") or "(untitled)"
        line = (
            f"{icon} <b>{result.get('from')} → {result.get('to')}</b>\n"
            f"<i>{title}</i>"
        )
        if reason:
            line += f"\n\n<code>reason:</code> {reason}"
        await self._reply(update, line)

    # -- Task NL reschedule shortcuts -------------------------------------

    async def _handle_snooze(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/snooze <task_name> <duration> — push reminder by 'snooze 2h' style.

        Duration is anything ``RescheduleTaskSkill`` accepts: '2h', '30m',
        '1d', or even a bare time like '10am'. The skill itself does the
        heavy lifting; this handler just splits args.
        """
        user_id = await self._auth(update)
        if not user_id:
            return
        if len(context.args) < 2:
            await self._reply(
                update,
                "❓ <b>Usage:</b> <code>/snooze &lt;task name&gt; &lt;duration&gt;</code>\n"
                "<i>Examples:</i> <code>/snooze upwork 2h</code>, "
                "<code>/snooze dentist tomorrow 9am</code>",
            )
            return
        # Last token is the duration, everything before it the task name.
        # Heuristic: take everything after the FIRST clearly-temporal
        # token as the phrase. Fallback: split on the last space.
        phrase = context.args[-1]
        task_name = " ".join(context.args[:-1])
        # Extend phrase to "snooze <X>" so the skill's regex catches it.
        await self._dispatch_reschedule(update, user_id, task_name, f"snooze {phrase}")

    async def _handle_postpone(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/postpone <task_name> <when> — reschedule with NL date phrase."""
        user_id = await self._auth(update)
        if not user_id:
            return
        if len(context.args) < 2:
            await self._reply(
                update,
                "❓ <b>Usage:</b> <code>/postpone &lt;task name&gt; &lt;when&gt;</code>\n"
                "<i>Examples:</i> <code>/postpone dentist Friday 10am</code>, "
                "<code>/postpone upwork next Monday 9</code>",
            )
            return
        # Same name/phrase split as snooze — the user's phrase format
        # ("Friday 10am") is multi-token, so we anchor on a heuristic:
        # take the last 1-3 tokens as the phrase based on whether they
        # look like time/date words. Simpler: if the whole arg list has
        # a known time word, split there; else last 2 tokens.
        phrase = " ".join(context.args[-2:])
        task_name = " ".join(context.args[:-2]) or context.args[0]
        await self._dispatch_reschedule(update, user_id, task_name, phrase)

    async def _handle_progress(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/progress <task_name> — raw timeline of progress entries.

        Read-only. No LLM. Returns up to 20 most-recent entries with
        timestamps, kind, and free-text. Falls through to brain only
        if no task name was given (better UX than a usage stub).
        """
        user_id = await self._auth(update)
        if not user_id:
            return
        if not context.args:
            await self._reply(
                update,
                "❓ <b>Usage:</b> <code>/progress &lt;task name&gt;</code>",
            )
            return
        task_name = " ".join(context.args).strip()
        from lazyclaw.skills.builtin.task_manager import _fuzzy_match_task
        from lazyclaw.tasks.store import list_tasks, read_progress_log
        from lazyclaw.tasks.timezone import get_user_tz

        tasks = await list_tasks(self._config, user_id)
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            await self._reply(update, f"No task matching '{task_name}'.")
            return
        log = await read_progress_log(
            self._config, user_id, match["id"], limit=20,
        )
        if not log:
            await self._reply(
                update,
                f"<b>{match['title']}</b> — no progress entries yet.",
            )
            return

        from datetime import datetime as _dt, timezone as _tz
        tz = await get_user_tz(self._config, user_id)
        icon_map = {
            "started": "🚀", "working": "🟡", "progress": "📊",
            "stuck": "🔴", "blocked": "🚧",
            "paused": "⏸️", "resumed": "▶️", "done": "✅",
            "pulse_fired": "🔔",
        }
        lines = [f"<b>{match['title']}</b> — progress timeline:"]
        for entry in log:
            try:
                dt = _dt.fromisoformat(entry["ts"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_tz.utc)
                local = dt.astimezone(tz)
                ts_short = local.strftime("%a %H:%M")
            except Exception:
                ts_short = (entry.get("ts") or "")[:16]
            kind = entry.get("kind") or "progress"
            icon = icon_map.get(kind, "📝")
            text = entry.get("text") or ""
            line = f"{icon} {ts_short} · {kind}"
            if text:
                # Cap free text at 80 chars per line so the timeline
                # stays scannable.
                line += f": {text[:80]}"
            lines.append(line)
        await self._reply(update, "\n".join(lines))

    async def _handle_whenisdue(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """/whenisdue <task_name> — read-only lookup via ask_about_task."""
        user_id = await self._auth(update)
        if not user_id:
            return
        if not context.args:
            await self._reply(
                update,
                "❓ <b>Usage:</b> <code>/whenisdue &lt;task name&gt;</code>",
            )
            return
        task_name = " ".join(context.args).strip()
        from lazyclaw.skills.builtin.task_reschedule import AskAboutTaskSkill
        skill = AskAboutTaskSkill(config=self._config)
        try:
            result = await skill.execute(user_id, {"task_name": task_name})
        except Exception as exc:
            logger.warning("ask_about_task failed: %s", exc, exc_info=True)
            await self._reply(update, f"❌ Lookup failed: {exc}")
            return
        await self._reply(update, result)

    async def _dispatch_reschedule(
        self,
        update: Update,
        user_id: str,
        task_name: str,
        phrase: str,
    ) -> None:
        """Shared dispatcher — both /snooze and /postpone funnel here."""
        from lazyclaw.skills.builtin.task_reschedule import RescheduleTaskSkill
        skill = RescheduleTaskSkill(config=self._config)
        try:
            result = await skill.execute(
                user_id, {"task_name": task_name, "phrase": phrase},
            )
        except Exception as exc:
            logger.warning("reschedule skill failed: %s", exc, exc_info=True)
            await self._reply(update, f"❌ Reschedule failed: {exc}")
            return
        await self._reply(update, result)

    # -- Callback query handler (inline keyboards) -------------------------

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if ":" not in data:
            return
        action, value = data.split(":", 1)
        chat_id = str(query.message.chat_id)
        # Top-level authorization gate: destructive branches (wipe:confirm,
        # nuke:all/messages/memories/logs/vault, rmadmin) act on the resolved
        # user's encrypted data. Reject unauthorized chats before ANY branch.
        # This is a superset guard; per-branch checks below stay in place.
        if not self._is_allowed(chat_id):
            logger.warning("Unauthorized Telegram callback from chat %s", chat_id)
            return
        user_id = await self._resolve_user(chat_id)

        # -- convok / convno — autonomous conversation first-message approval ----
        # callback_data shape:
        #   convok:<conv_id>  → user approved the drafted opener (✅ Send it)
        #   convno:<conv_id>  → user cancelled (✋ Cancel)
        conv_result = parse_conversation_callback(data)
        if conv_result is not None:
            conv_id, approved = conv_result
            try:
                from lazyclaw.comms import conversation_runner, conversation_store
                from lazyclaw.gateway.routes.inbox import _get_registry
                from types import SimpleNamespace

                conv = await conversation_store.get_conversation(
                    self._config, user_id, conv_id
                )
                if conv is None:
                    await query.edit_message_text("❌ Conversation not found.")
                    return
                deps = SimpleNamespace(
                    registry=_get_registry(),
                    eco_router=None,
                    permission_checker=None,
                )
                await conversation_runner.on_approval(
                    self._config, deps, conv, approved
                )
                if approved:
                    await query.edit_message_text("Sent ✅")
                else:
                    await query.edit_message_text("Cancelled")
            except Exception as exc:
                logger.exception("conversation approval callback failed for conv %s", conv_id)
                await query.edit_message_text(f"❌ Error: {exc}")
            return

        if action == "accept":
            # Contract-intake watcher's ✅ Accept / ⏭ Skip buttons.
            # callback_data shape:
            #   accept:<slug>            → run the {slug}_accept template
            #   accept:skip:<slug>       → user skipped this item
            if not self._is_allowed(chat_id):
                logger.warning(
                    "Unauthorized accept callback from chat %s", chat_id,
                )
                return
            if value.startswith("skip:"):
                slug = value.split(":", 1)[1]
                await query.edit_message_text(
                    f"⏭ Skipped — {slug}_accept not fired. Watcher continues."
                )
                return
            slug = value
            template_name = f"{slug}_accept"
            try:
                from lazyclaw.browser import templates as tpl_store
                tpl = await tpl_store.get_template_by_name(
                    self._config, user_id, template_name,
                )
                if tpl is None:
                    await query.edit_message_text(
                        f"❌ Template '{template_name}' not found. "
                        "Run `list_browser_templates` to see what's saved."
                    )
                    return
                # Mark the template active so the next agent turn uses
                # it; surface a confirmation immediately so the user
                # sees the 3-second-acceptance promise was met. The
                # active_templates registry is sync + in-memory (per
                # template_handlers.py:19).
                from lazyclaw.browser import active_templates
                active_templates.set_active(user_id, tpl["id"])
                await query.edit_message_text(
                    f"✅ Accept fired — `{template_name}` queued. "
                    "The next browser turn will run the saved accept flow."
                )
            except Exception as exc:
                logger.exception("accept callback failed for %s", slug)
                await query.edit_message_text(
                    f"❌ Accept failed: {exc}. Run the template manually."
                )
            return

        if action == "wipe":
            if value == "confirm":
                try:
                    from lazyclaw.db.connection import db_session
                    async with db_session(self._config) as db:
                        await db.execute("DELETE FROM agent_messages WHERE user_id = ?", (user_id,))
                        await db.commit()
                    await query.edit_message_text("\u2705 History cleared.")
                except Exception as e:
                    await query.edit_message_text(f"\u274c Failed: {e}")
            else:
                await query.edit_message_text("\u274c Cancelled.")

        elif action == "nuke":
            if value == "cancel":
                await query.edit_message_text("\u274c Cancelled.")
                return
            try:
                from lazyclaw.db.connection import db_session
                deleted = []
                async with db_session(self._config) as db:
                    if value in ("messages", "all"):
                        await db.execute("DELETE FROM agent_messages WHERE user_id = ?", (user_id,))
                        deleted.append("\U0001f4ac conversations")
                    if value in ("memories", "all"):
                        await db.execute("DELETE FROM personal_memory WHERE user_id = ?", (user_id,))
                        deleted.append("\U0001f9e0 memories")
                    if value in ("logs", "all"):
                        await db.execute("DELETE FROM daily_logs WHERE user_id = ?", (user_id,))
                        deleted.append("\U0001f4dd daily logs")
                    if value in ("vault", "all"):
                        await db.execute("DELETE FROM credential_vault WHERE user_id = ?", (user_id,))
                        deleted.append("\U0001f511 vault")
                    await db.commit()
                await query.edit_message_text(f"\U0001f4a5 Deleted: {', '.join(deleted)}")
            except Exception as e:
                await query.edit_message_text(f"\u274c Failed: {e}")

        elif action == "rmadmin":
            if value == "cancel":
                await query.edit_message_text("\u274c Cancelled.")
            else:
                self._adapter._allowed_chats.discard(value)
                await query.edit_message_text(f"\u2705 Removed admin: {value}")

        elif action == "bgtask":
            # Background task cancel from /tasks inline buttons
            if value.startswith("cancel:"):
                task_id = value.split(":", 1)[1]
                if self._task_runner:
                    ok = await self._task_runner.cancel(task_id, user_id)
                    if ok:
                        await query.edit_message_text("\U0001f6d1 Task cancelled.")
                    else:
                        await query.edit_message_text("\u274c Task not found or already finished.")
                else:
                    await query.edit_message_text("\u274c No task runner available.")

        elif action == "task":
            # Task manager inline buttons: done, snooze, tomorrow
            try:
                sub_action, task_id = value.split(":", 1)
            except ValueError:
                return
            try:
                from lazyclaw.tasks.store import (
                    complete_task, get_task_owner, update_task,
                )
                from datetime import timedelta

                # The chat may be bound to a different user than the task
                # owner (multi-user setups where resolve_user_id returns the
                # first-registered user). Look up the real owner from the
                # task itself, which we trust because the callback_data was
                # signed by our own heartbeat daemon.
                task_owner_id = await get_task_owner(self._config, task_id)
                if not task_owner_id:
                    logger.info(
                        "Task callback: task %s not found in DB (chat=%s)",
                        task_id, chat_id,
                    )
                    await query.edit_message_text(
                        "\u274c Task not found (already deleted?)"
                    )
                    return
                if not self._is_allowed(chat_id):
                    logger.warning(
                        "Unauthorized task callback from chat %s for task %s",
                        chat_id, task_id,
                    )
                    return
                user_id = task_owner_id

                if sub_action == "done":
                    # Get task name before completing
                    from lazyclaw.tasks.store import get_task
                    task_info = await get_task(self._config, user_id, task_id)
                    task_title = task_info.get("title", "Task") if task_info else "Task"

                    ok = await complete_task(self._config, user_id, task_id)
                    if ok:
                        _now = datetime.now(timezone.utc)
                        try:
                            import time as _time
                            _off = -_time.timezone if _time.daylight == 0 else -_time.altzone
                            _local = _now.astimezone(timezone(timedelta(seconds=_off)))
                            _time_str = _local.strftime("%H:%M")
                        except Exception:
                            _time_str = _now.strftime("%H:%M UTC")
                        await query.edit_message_text(
                            f"\u2705 Done: {task_title}\n"
                            f"Completed at {_time_str}"
                        )
                    else:
                        await query.edit_message_text("\u274c Failed to complete.")
                elif sub_action == "snooze":
                    snooze_dt = datetime.now(timezone.utc) + timedelta(hours=1)
                    await update_task(
                        self._config, user_id, task_id,
                        reminder_at=snooze_dt.isoformat(), nag_count=0,
                    )
                    try:
                        import time as _time
                        _off = -_time.timezone if _time.daylight == 0 else -_time.altzone
                        _local = snooze_dt.astimezone(timezone(timedelta(seconds=_off)))
                        _snooze_str = _local.strftime("%H:%M")
                    except Exception:
                        _snooze_str = snooze_dt.strftime("%H:%M UTC")
                    await query.edit_message_text(
                        f"\u23f0 Snoozed — next reminder at {_snooze_str}"
                    )
                elif sub_action == "tomorrow":
                    # Tomorrow 9am in LOCAL time → convert to UTC
                    try:
                        import time as _time
                        _off = -_time.timezone if _time.daylight == 0 else -_time.altzone
                        _local_tz = timezone(timedelta(seconds=_off))
                        _local_tomorrow = (
                            datetime.now(_local_tz).replace(
                                hour=9, minute=0, second=0, microsecond=0,
                            ) + timedelta(days=1)
                        )
                        tomorrow_9am = _local_tomorrow.astimezone(
                            timezone.utc
                        ).isoformat()
                    except Exception:
                        tomorrow_9am = (
                            datetime.now(timezone.utc).replace(
                                hour=7, minute=0, second=0, microsecond=0,
                            ) + timedelta(days=1)
                        ).isoformat()
                    await update_task(
                        self._config, user_id, task_id,
                        reminder_at=tomorrow_9am,
                        due_date=tomorrow_9am[:10],
                        nag_count=0,
                    )
                    await query.edit_message_text("\U0001f4c5 Moved to tomorrow 9:00 AM")
            except Exception as exc:
                logger.warning("Task callback failed: %s", exc, exc_info=True)
                await query.edit_message_text(f"\u274c Error: {exc}")

        elif action == "wmute":
            # \ud83d\udd07 Mute chat \u2014 fired from a WhatsApp watcher push.
            # callback_data shape: wmute:<short_job_id>:<chat_slug>
            # We use the in-memory _last_watcher_context (set by the
            # daemon's _store_watcher_context call) to recover the real
            # chat name, since the 24-char slug is lossy.
            if not self._is_allowed(chat_id):
                logger.warning(
                    "Unauthorized wmute callback from chat %s", chat_id,
                )
                return
            try:
                _short_job, slug = value.split(":", 1)
            except ValueError:
                await query.edit_message_text("\u274c Bad mute callback.")
                return
            try:
                from lazyclaw.heartbeat.daemon import get_last_watcher_context
                wctx = get_last_watcher_context(user_id) or {}
                chat_names = wctx.get("chat_names") or []
                # Prefer the first stored chat name \u2014 it matches the chat
                # whose buttons the user just tapped (notifications are
                # 1:1 with surfaced chats in single-message layout).
                chat_name = chat_names[0] if chat_names else slug.replace("_", " ")
            except Exception:
                chat_name = slug.replace("_", " ")
            try:
                from lazyclaw.mcp.manager import _active_clients
                mcp_client = None
                for sid, c in _active_clients.items():
                    cn = getattr(c, "name", "") or ""
                    if "whatsapp" in cn.lower():
                        mcp_client = c
                        break
                if mcp_client is None:
                    await query.edit_message_text(
                        "\u274c WhatsApp not connected \u2014 can't mute."
                    )
                    return
                import json as _json
                raw = await mcp_client.call_tool(
                    "whatsapp_mute", {"chat": chat_name, "action": "mute"},
                )
                data = _json.loads(raw) if raw.strip().startswith("{") else {}
                final_name = data.get("chat") or chat_name
                await query.edit_message_text(
                    f"\U0001f507 Muted on WhatsApp: <b>{final_name}</b>",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("wmute callback failed: %s", exc, exc_info=True)
                await query.edit_message_text(f"\u274c Mute failed: {exc}")

        elif action == "wsnooze":
            # \u23f0 Snooze watcher \u2014 pauses checks for N minutes without
            # deleting the job. callback_data: wsnooze:<short_job>:<mins>
            if not self._is_allowed(chat_id):
                logger.warning(
                    "Unauthorized wsnooze callback from chat %s", chat_id,
                )
                return
            try:
                short_job, mins_str = value.split(":", 1)
                mins = int(mins_str)
            except (ValueError, TypeError):
                await query.edit_message_text("\u274c Bad snooze callback.")
                return
            if mins <= 0 or mins > 24 * 60:
                await query.edit_message_text("\u274c Snooze out of range.")
                return
            try:
                import json as _json
                from datetime import timedelta as _td
                from lazyclaw.crypto.encryption import (
                    decrypt as _dec,
                    encrypt as _enc,
                    is_encrypted as _is_enc,
                )
                from lazyclaw.crypto.keys import get_user_dek
                from lazyclaw.db.connection import db_session

                key = await get_user_dek(self._config, user_id)
                async with db_session(self._config) as db:
                    cursor = await db.execute(
                        "SELECT id, name, context FROM agent_jobs "
                        "WHERE user_id = ? AND job_type = 'watcher' "
                        "AND status = 'active'",
                        (user_id,),
                    )
                    rows = await cursor.fetchall()

                target_id: str | None = None
                target_name = ""
                target_ctx: dict = {}
                for jid, enc_name, enc_ctx in rows:
                    if not jid.startswith(short_job):
                        continue
                    try:
                        raw = (
                            _dec(enc_ctx, key) if enc_ctx and _is_enc(enc_ctx)
                            else enc_ctx or "{}"
                        )
                        target_ctx = _json.loads(raw)
                        target_name = (
                            _dec(enc_name, key) if enc_name and _is_enc(enc_name)
                            else enc_name or "watcher"
                        )
                        target_id = jid
                        break
                    except Exception:
                        logger.debug("ctx decrypt failed during snooze", exc_info=True)
                        continue

                if not target_id:
                    await query.edit_message_text(
                        "\u274c Watcher not found (already removed?)."
                    )
                    return

                until = (
                    datetime.now(timezone.utc) + _td(minutes=mins)
                ).isoformat()
                target_ctx["snoozed_until"] = until
                # Re-encrypt and persist.
                async with db_session(self._config) as db:
                    new_blob = _enc(_json.dumps(target_ctx), key)
                    await db.execute(
                        "UPDATE agent_jobs SET context = ? WHERE id = ?",
                        (new_blob, target_id),
                    )
                    await db.commit()

                hrs, mns = divmod(mins, 60)
                if hrs and mns:
                    span = f"{hrs}h {mns}m"
                elif hrs:
                    span = f"{hrs}h"
                else:
                    span = f"{mns}m"
                await query.edit_message_text(
                    f"\u23f0 Snoozed <b>{target_name}</b> for {span}",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("wsnooze callback failed: %s", exc, exc_info=True)
                await query.edit_message_text(f"\u274c Snooze failed: {exc}")

    # -- Pinned status (auto-refresh) --------------------------------------

    async def _pinned_refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            for chat_id, msg_id in list(self._pinned_status.items()):
                try:
                    await self._update_pinned(chat_id, msg_id)
                except Exception:
                    logger.warning("Failed to refresh pinned status for chat %s", chat_id, exc_info=True)

    async def _update_pinned(self, chat_id: str, msg_id: int) -> None:
        from lazyclaw.llm.model_registry import get_mode_models
        # Show ECO-resolved models (not config defaults)
        _mode = "hybrid"
        try:
            from lazyclaw.llm.eco_settings import get_eco_settings
            _eco = await get_eco_settings(self._config, self._admin_user_id or "")
            _mode = _eco.get("mode", "hybrid")
        except Exception:
            logger.warning("Failed to read ECO settings for pinned status; using default mode", exc_info=True)
        _models = get_mode_models(_mode)
        lines = [
            "\U0001f43e <b>LazyClaw Status</b>",
            "━━━━━━━━━━━━",
            f"\U0001f9e0 Brain: <code>{_models['brain']}</code>",
            f"\u2699\ufe0f Worker: <code>{_models['worker']}</code>",
        ]
        try:
            from lazyclaw.mcp.manager import _active_clients
            lines.append(f"\U0001f50c MCP: {len(_active_clients)} connected")
        except Exception:
            logger.warning("Failed to read MCP active clients for pinned status", exc_info=True)
        if self._team_lead:
            n = len(self._team_lead.active_tasks)
            lines.append(f"\u26a1 Tasks: {n} running")
        else:
            lines.append("\u26a1 Tasks: 0")
        try:
            await self._adapter._app.bot.edit_message_text(
                chat_id=int(chat_id), message_id=msg_id,
                text="\n".join(lines), parse_mode="HTML",
            )
        except Exception:
            logger.warning("Failed to edit pinned status message for chat %s", chat_id, exc_info=True)
