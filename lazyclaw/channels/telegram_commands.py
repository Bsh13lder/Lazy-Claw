"""Telegram slash commands — direct admin calls, no LLM.

Every command calls existing functions from cli_admin, vault, mcp/manager, etc.
Responses use HTML formatting with emojis for a cozy Telegram experience.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

# Commands shown in Telegram's "/" autocomplete menu
BOT_COMMANDS = [
    BotCommand("help", "\U0001f4cb All commands"),
    BotCommand("status", "\U0001f4ca Live status"),
    BotCommand("tasks", "\u26a1 Background tasks"),
    BotCommand("usage", "\U0001f4b0 Token costs"),
    BotCommand("eco", "\U0001f331 AI mode"),
    BotCommand("model", "\U0001f9e0 Show/change models"),
    BotCommand("mcp", "\U0001f50c MCP servers"),
    BotCommand("key", "\U0001f511 API keys"),
    BotCommand("history", "\U0001f4ac Recent messages"),
    BotCommand("logs", "\U0001f4dd Activity log"),
    BotCommand("browser", "\U0001f310 Browser control"),
    BotCommand("survival", "\U0001f4bc Job hunting"),
    BotCommand("profile", "\U0001f464 Freelance profile"),
    BotCommand("doctor", "\U0001fa7a Diagnostics"),
    BotCommand("screen", "\U0001f5a5 Desktop screenshot / VNC"),
    BotCommand("wipe", "\U0001f9f9 Clear history"),
    BotCommand("nuke", "\U0001f4a3 Data wipe"),
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
            "eco": self._handle_eco, "doctor": self._handle_doctor,
            "logs": self._handle_logs, "usage": self._handle_usage,
            "tasks": self._handle_tasks, "cancel": self._handle_cancel,
            "history": self._handle_history, "wipe": self._handle_wipe,
            "nuke": self._handle_nuke, "mcp": self._handle_mcp,
            "whatsapp": self._handle_platform, "instagram": self._handle_platform,
            "email": self._handle_platform,
            "survival": self._handle_survival, "profile": self._handle_profile,
            "browser": self._handle_browser, "screen": self._handle_screen,
            "addadmin": self._handle_addadmin, "removeadmin": self._handle_removeadmin,
        }
        for name, handler in cmds.items():
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(CallbackQueryHandler(self._handle_callback))

    # -- Helpers -----------------------------------------------------------

    def _is_allowed(self, chat_id: str) -> bool:
        return chat_id in self._adapter._allowed_chats or not self._adapter._admin_chat_id

    async def _resolve_user(self, chat_id: str) -> str:
        from lazyclaw.channels.telegram import resolve_user_id
        return await resolve_user_id(self._config)

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
        asyncio.create_task(
            self._adapter._process_and_reply(update, chat_id, user_id, text),
            name=f"tg-cmd-{chat_id}-{id(text)}",
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
                "<code>/eco hybrid</code>\n"
                "<code>/model brain claude-sonnet-4-20250514</code>"
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
            "Send me anything to chat, or /help for commands."
        )

    # -- /help -------------------------------------------------------------

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        await self._reply(update,
            "\U0001f43e <b>LazyClaw Commands</b>\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            "\u2699\ufe0f <b>Setup</b>\n"
            "/key \u2014 \U0001f511 API keys <i>(auto-deletes msg)</i>\n"
            "/model \u2014 \U0001f9e0 Show/change models\n"
            "/eco \u2014 \U0001f331 AI mode\n\n"
            "\U0001f4ca <b>Daily</b>\n"
            "/status \u2014 Live status\n"
            "/tasks \u2014 \u26a1 Background tasks\n"
            "/cancel \u2014 \U0001f6d1 Cancel task\n"
            "/usage \u2014 \U0001f4b0 Token costs\n"
            "/history \u2014 \U0001f4ac Recent messages\n"
            "/wipe \u2014 \U0001f9f9 Clear history\n\n"
            "\U0001f50c <b>Integrations</b>\n"
            "/mcp \u2014 MCP servers\n"
            "/whatsapp \u2014 WhatsApp setup/status\n"
            "/instagram \u2014 Instagram setup/status\n"
            "/email \u2014 Email setup/status\n\n"
            "\U0001f4bc <b>Survival</b>\n"
            "/survival \u2014 Job hunting on/off\n"
            "/profile \u2014 \U0001f464 Freelance profile\n\n"
            "\U0001f6e1 <b>Admin</b>\n"
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
        except Exception:
            pass

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
        if not await self._auth(update):
            return
        args = context.args or []
        if not args:
            await self._reply(update,
                f"\U0001f9e0 <b>Models</b>\n\n"
                f"Brain: <code>{self._config.brain_model}</code>\n"
                f"Worker: <code>{self._config.worker_model}</code>\n\n"
                f"Change: <code>/model brain MODEL</code>"
            )
            return
        if len(args) >= 2:
            role, model = args[0].lower(), args[1]
            if role in ("brain", "worker"):
                setattr(self._config, f"{role}_model", model)
                await self._reply(update, f"\u2705 {role.title()}: <code>{model}</code>")
                return
        await self._reply(update, "\U0001f9e0 Usage: <code>/model brain MODEL</code> or <code>/model worker MODEL</code>")

    # -- /eco --------------------------------------------------------------

    async def _handle_eco(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []
        try:
            from lazyclaw.llm.eco_settings import get_eco_settings, update_eco_settings
        except ImportError:
            from lazyclaw.gateway.routes.eco import get_eco_settings, update_eco_settings

        if not args:
            s = await get_eco_settings(self._config, user_id)
            mode = s.get("mode", "full")
            icons = {"local": "\U0001f4bb", "eco": "\U0001f331", "hybrid": "\u2696\ufe0f", "full": "\U0001f680"}
            budget = s.get("monthly_paid_budget", 0)
            text = f"{icons.get(mode, '')} <b>ECO: {mode.upper()}</b>"
            if budget:
                text += f"\n\U0001f4b0 Budget: ${budget:.2f}/mo"
            text += "\n\nChange: <code>/eco local|eco|hybrid|full</code>"
            await self._reply(update, text)
        else:
            mode = args[0].lower()
            if mode not in ("local", "eco", "hybrid", "full"):
                await self._reply(update, "\u274c Valid: local, eco, hybrid, full")
                return
            await update_eco_settings(self._config, user_id, {"mode": mode})
            await self._reply(update, f"\u2705 ECO: <b>{mode.upper()}</b>")

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
            from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
            k = derive_server_key(user_id)
            assert decrypt(k, encrypt(k, "test")) == "test"
            checks.append("\u2705 Encryption: OK")
        except Exception as e:
            checks.append(f"\u274c Encryption: FAIL ({e})")
        try:
            from lazyclaw.mcp.manager import _active_clients
            checks.append(f"\u2705 MCP: {len(_active_clients)} connected")
        except Exception:
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
            await self._reply(update,
                f"\U0001f4b0 <b>Usage Stats</b>\n━━━━━━━━━━━━\n\n"
                f"\U0001f4ac Messages: <b>{mc:,}</b>\n"
                f"\U0001f4c1 Sessions: <b>{sc:,}</b>\n"
                f"\U0001f9e0 Memories: <b>{mm:,}</b>\n\n"
                f"\U0001f916 Brain: <code>{self._config.brain_model}</code>\n"
                f"\u2699\ufe0f Worker: <code>{self._config.worker_model}</code>"
            )
        except Exception as e:
            await self._reply(update, f"\u274c Failed: {e}")

    # -- /tasks ------------------------------------------------------------

    async def _handle_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        if self._team_lead:
            status = self._team_lead.format_status()
            if status:
                await self._reply(update, f"\u26a1 <b>Tasks</b>\n━━━━━━━━━━━━\n\n{status}", html=False)
                return
        if self._task_runner:
            try:
                user_id = await self._resolve_user(str(update.effective_chat.id))
                tasks = self._task_runner.list_all(user_id)
                if not tasks:
                    await self._reply(update, "\u26a1 No tasks running.")
                    return
                lines = ["\u26a1 <b>Tasks</b>\n━━━━━━━━━━━━\n"]
                icons = {"running": "\U0001f504", "done": "\u2705", "failed": "\u274c"}
                for t in tasks:
                    icon = icons.get(t.get("status", ""), "\u2753")
                    lines.append(f"  {icon} {t.get('name', 'unnamed')}")
                await self._reply(update, "\n".join(lines))
            except Exception as e:
                await self._reply(update, f"\u274c Failed: {e}")
        else:
            await self._reply(update, "\u26a1 No tasks running.")

    # -- /cancel -----------------------------------------------------------

    async def _handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        target = " ".join(context.args) if context.args else ""
        if self._team_lead:
            task_id = self._team_lead.find_cancel_target(target) if target else None
            if not task_id:
                active = self._team_lead.active_tasks
                if active:
                    for t in active:
                        self._team_lead.cancel(t.id)
                    await self._reply(update, f"\U0001f6d1 Cancelled {len(active)} task(s).")
                else:
                    await self._reply(update, "\U0001f6d1 Nothing to cancel.")
            else:
                self._team_lead.cancel(task_id)
                await self._reply(update, f"\U0001f6d1 Cancelled: <code>{task_id[:8]}</code>")
        else:
            await self._reply(update, "\U0001f6d1 No task tracker available.")

    # -- /history ----------------------------------------------------------

    async def _handle_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._auth(update)
        if not user_id:
            return
        try:
            from lazyclaw.db.connection import db_session
            from lazyclaw.crypto.encryption import derive_server_key, decrypt
            key = derive_server_key(user_id)
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
            _active_clients, BUNDLED_MCPS,
        )
        if not args:
            servers = await list_servers(self._config, user_id)
            if not servers:
                await self._reply(update, "\U0001f50c No MCP servers registered.")
                return
            lines = ["\U0001f50c <b>MCP Servers</b>\n━━━━━━━━━━━━\n"]
            for s in servers:
                name = s.get("name", "?")
                fav = "\u2b50 " if s.get("favorite") else "  "
                connected = " \u2705" if s["id"] in _active_clients else ""
                desc = BUNDLED_MCPS.get(name, {}).get("description", "")
                lines.append(f"{fav}<b>{name}</b>{connected}")
                if desc:
                    lines.append(f"    <i>{desc[:55]}</i>")
            await self._reply(update, "\n".join(lines))
            return
        subcmd = args[0].lower()
        name = " ".join(args[1:]) if len(args) > 1 else ""
        if not name:
            await self._reply(update, f"\U0001f50c Usage: <code>/mcp {subcmd} NAME</code>")
            return
        servers = await list_servers(self._config, user_id)
        server = next((s for s in servers if s["name"].lower() == name.lower()), None)
        if not server:
            server = next((s for s in servers if name.lower() in s["name"].lower()), None)
        if not server:
            await self._reply(update, f"\u274c Server '{name}' not found. Use /mcp to list.")
            return
        try:
            if subcmd == "fav":
                await set_favorite(self._config, user_id, server["name"], True)
                await self._reply(update, f"\u2b50 <b>{server['name']}</b> added to favorites")
            elif subcmd == "unfav":
                await set_favorite(self._config, user_id, server["name"], False)
                await self._reply(update, f"\u2705 <b>{server['name']}</b> removed from favorites")
            elif subcmd == "connect":
                await connect_server(self._config, user_id, server["id"])
                await self._reply(update, f"\u2705 Connected: <b>{server['name']}</b>")
            elif subcmd in ("disconnect", "disc"):
                await disconnect_server(user_id, server["id"])
                await self._reply(update, f"\U0001f50c Disconnected: <b>{server['name']}</b>")
            else:
                await self._reply(update, "\U0001f50c Subcommands: fav, unfav, connect, disconnect")
        except Exception as e:
            await self._reply(update, f"\u274c Failed: {e}")

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

    # -- Callback query handler (inline keyboards) -------------------------

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if ":" not in data:
            return
        action, value = data.split(":", 1)
        chat_id = str(query.message.chat_id)
        user_id = await self._resolve_user(chat_id)

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

    # -- Pinned status (auto-refresh) --------------------------------------

    async def _pinned_refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            for chat_id, msg_id in list(self._pinned_status.items()):
                try:
                    await self._update_pinned(chat_id, msg_id)
                except Exception:
                    pass

    async def _update_pinned(self, chat_id: str, msg_id: int) -> None:
        lines = [
            "\U0001f43e <b>LazyClaw Status</b>",
            "━━━━━━━━━━━━",
            f"\U0001f9e0 Brain: <code>{self._config.brain_model}</code>",
            f"\u2699\ufe0f Worker: <code>{self._config.worker_model}</code>",
        ]
        try:
            from lazyclaw.mcp.manager import _active_clients
            lines.append(f"\U0001f50c MCP: {len(_active_clients)} connected")
        except Exception:
            pass
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
            pass
