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

# Commands shown in Telegram's "/" autocomplete menu
BOT_COMMANDS = [
    BotCommand("help", "\U0001f4cb All commands"),
    BotCommand("status", "\U0001f4ca Live status"),
    BotCommand("tasks", "\u26a1 Background tasks"),
    BotCommand("usage", "\U0001f4b0 Token costs"),
    BotCommand("mode", "\u2699\ufe0f AI routing mode"),
    BotCommand("model", "\U0001f9e0 Show/change models"),
    BotCommand("watch", "\U0001f514 Watchers (WhatsApp/Email)"),
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
    BotCommand("local", "\U0001f9e0 Local AI servers"),
    BotCommand("ram", "\U0001f4be RAM usage"),
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
            "local": self._handle_local,
            "ram": self._handle_ram,
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
        from lazyclaw.runtime.aio_helpers import fire_and_forget

        fire_and_forget(
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
                "<code>/mode claude</code>\n"
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
            "/mode \u2014 \u2699\ufe0f AI routing mode\n\n"
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
            from lazyclaw.llm.model_registry import get_mode_models
            from lazyclaw.llm.eco_settings import get_eco_settings
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
            role, model = args[0].lower(), args[1]
            if role in ("brain", "worker"):
                setattr(self._config, f"{role}_model", model)
                await self._reply(update, f"\u2705 {role.title()}: <code>{model}</code>")
                return
        await self._reply(update, "\U0001f9e0 Usage: <code>/model brain MODEL</code> or <code>/model worker MODEL</code>")

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
            icons = {
                MODE_HYBRID: "\u2696\ufe0f", MODE_FULL: "\U0001f680",
                MODE_CLAUDE: "\u26a1",
            }
            labels = {
                MODE_HYBRID: "HYBRID", MODE_FULL: "FULL",
                MODE_CLAUDE: "CLAUDE CLI",
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
                "<code>/mode claude</code> — All via Claude CLI (free)\n"
                "<code>/mode brain MODEL</code> — Set brain model\n"
                "<code>/mode worker MODEL</code> — Set worker model\n"
                "<code>/mode workers N</code> — Max workers (1-20)\n"
                "<code>/mode budget N</code> — Monthly $ cap"
            )
            await self._reply(update, text)
            return

        subcmd = args[0].lower()

        # Mode change: /eco hybrid|full (reject old eco/local modes)
        if subcmd in ("on", "hybrid", "off", "local", "full", "eco", "eco_on", "claude"):
            if subcmd in _DISABLED_MODES:
                await self._reply(update, f"\u26a0\ufe0f {DISABLED_MODE_MESSAGE}")
                return
            normalized = normalize_mode(subcmd)
            await update_eco_settings(self._config, user_id, {"mode": normalized})
            labels = {
                MODE_HYBRID: "HYBRID", MODE_FULL: "FULL",
                MODE_CLAUDE: "CLAUDE CLI",
            }
            await self._reply(update, f"\u2705 Mode: <b>{labels.get(normalized, normalized)}</b>")
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
        user_id = await self._auth(update)
        if not user_id:
            return
        args = context.args or []
        from lazyclaw.llm.mlx_manager import MLXManager
        from lazyclaw.llm.model_registry import BRAIN_MODEL, WORKER_MODEL
        from lazyclaw.llm.eco_settings import get_eco_settings

        # Get or create MLX manager (singleton on eco_router)
        eco_router = getattr(self._agent, "eco_router", None)
        if not eco_router:
            await self._reply(update, "\u274c No eco_router available")
            return

        # Lazy-init MLX manager on eco_router
        if not hasattr(eco_router, "_mlx_manager"):
            eco_router._mlx_manager = MLXManager()
        manager = eco_router._mlx_manager

        if not args:
            # Show status
            status = await manager.check_health()
            brain = status["brain"]
            worker = status["worker"]

            b_icon = "\u2705" if brain["healthy"] else "\u274c"
            w_icon = "\u2705" if worker["healthy"] else "\u274c"
            b_model = brain["model"] or "not running"
            w_model = worker["model"] or "not running"
            b_port = brain["port"] or 8080
            w_port = worker["port"] or 8081

            # RAM info
            from lazyclaw.llm.ram_monitor import get_ram_status
            ram = await get_ram_status()

            text = (
                "\U0001f9e0 <b>Local AI Servers</b>\n"
                "━━━━━━━━━━━━\n"
                f"{b_icon} Brain: <b>{b_model}</b> (:{b_port})\n"
                f"{w_icon} Worker: <b>{w_model}</b> (:{w_port})\n"
                f"\n\U0001f4be RAM: {ram.system_used_pct:.0f}% used"
            )
            if ram.ai_total_mb > 0:
                text += f" (AI: {ram.ai_total_mb}MB)"
            text += f"\n\U0001f7e2 Free: {ram.headroom_mb}MB"

            text += (
                "\n\n<b>Commands:</b>\n"
                "<code>/local on</code> — Start brain + worker\n"
                "<code>/local off</code> — Stop all servers\n"
                "<code>/local brain</code> — Start brain only\n"
                "<code>/local worker</code> — Worker only (hybrid mode)\n"
                "<code>/local stop brain</code> — Stop brain, keep worker\n"
                "<code>/local stop worker</code> — Stop worker, keep brain\n"
                "<code>/local restart</code> — Restart all"
            )
            await self._reply(update, text)
            return

        subcmd = args[0].lower()

        # Get user's configured models
        settings = await get_eco_settings(self._config, user_id)
        brain_model = settings.get("brain_model") or BRAIN_MODEL
        worker_model = settings.get("worker_model") or WORKER_MODEL

        if subcmd in ("on", "start"):
            # ECO mode: brain is Haiku (API), only start worker locally.
            # Starting both on 16GB M2 causes OOM (Qwen 9B + Nanbeige 3B).
            await self._reply(update, "\u23f3 Starting local worker server...")

            w_ok = await manager.start_worker(worker_model)

            # Reset eco_router's local provider cache so it discovers the new server
            eco_router.reset_local_check()

            lines = []
            lines.append(f"{'✅' if w_ok else '❌'} Worker: {worker_model.split('/')[-1]}")
            lines.append(f"ℹ️ Brain: {brain_model.split('/')[-1]} (API — no local server needed)")

            if w_ok:
                from lazyclaw.llm.eco_settings import update_eco_settings
                await update_eco_settings(self._config, user_id, {"mode": "hybrid"})
                lines.append("\n\U0001f389 Worker running! Auto-switched to <b>HYBRID</b> (Haiku brain + local worker)")
            else:
                lines.append(
                    "\n\u274c Both failed. Is mlx-lm installed?\n"
                    "<code>pip install mlx-lm</code>"
                )

            await self._reply(update, "\n".join(lines))
            return

        if subcmd in ("off", "stop") and len(args) == 1:
            await manager.stop_all()
            eco_router.reset_local_check()
            await self._reply(update, "\u2705 Local AI servers stopped.")
            return

        # /local stop brain | /local stop worker
        if subcmd == "stop" and len(args) > 1:
            target = args[1].lower()
            if target == "brain" and hasattr(manager, '_brain') and manager._brain:
                await manager._stop_server(manager._brain)
                manager._brain = None
                eco_router.reset_local_check()
                await self._reply(update, "\u2705 Brain stopped. Worker still running.")
                return
            if target == "worker" and hasattr(manager, '_worker') and manager._worker:
                await manager._stop_server(manager._worker)
                manager._worker = None
                eco_router.reset_local_check()
                await self._reply(update, "\u2705 Worker stopped. Brain still running.")
                return
            await self._reply(update, "\u274c Use: <code>/local stop brain</code> or <code>/local stop worker</code>")
            return

        if subcmd == "brain":
            await self._reply(update, f"\u23f3 Starting brain: {brain_model.split('/')[-1]}...")
            ok = await manager.start_brain(brain_model)
            eco_router.reset_local_check()
            icon = "\u2705" if ok else "\u274c"
            await self._reply(update, f"{icon} Brain: {'running' if ok else 'failed'}")
            return

        if subcmd == "worker":
            # Worker-only = hybrid mode (Sonnet brain + local nanbeige workers)
            await self._reply(update, f"\u23f3 Starting worker: {worker_model.split('/')[-1]}...")
            ok = await manager.start_worker(worker_model)
            eco_router.reset_local_check()
            if ok:
                # Auto-suggest hybrid mode
                from lazyclaw.llm.eco_settings import update_eco_settings
                await update_eco_settings(self._config, user_id, {"mode": "hybrid"})
                await self._reply(
                    update,
                    f"\u2705 Worker running: {worker_model.split('/')[-1]}\n"
                    f"\u2696\ufe0f Auto-switched to <b>HYBRID</b> mode\n"
                    f"(Sonnet brain + local worker)"
                )
            else:
                await self._reply(update, "\u274c Worker failed to start")
            return

        if subcmd == "restart":
            await self._reply(update, "\u23f3 Restarting local worker...")
            await manager.stop_all()
            w_ok = await manager.start_worker(worker_model)
            eco_router.reset_local_check()
            w = "\u2705" if w_ok else "\u274c"
            await self._reply(update, f"{w} Worker: {worker_model.split('/')[-1]}\nℹ️ Brain: API (no local server)\n\n\u2705 Restarted!")
            return

        await self._reply(update, "\u274c Use: <code>/local on|off|restart|brain|worker</code>")

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
            except Exception:
                pass

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
                    fav = "\u2b50" if s.get("favorite") else "  "
                    is_connected = sid in _active_clients
                    status = "\u2705" if is_connected else "\u26aa"
                    desc = BUNDLED_MCPS.get(sname, {}).get("description", "")
                    lines.append(f"{fav} {status} <b>{sname}</b>")
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
                "<code>/mcp connect NAME</code>\n"
                "<code>/mcp disconnect NAME</code>\n"
                "<code>/mcp fav NAME</code>  \u2022  <code>/mcp unfav NAME</code>"
            )
            await self._reply(update, "\n".join(lines))
            return

        subcmd = args[0].lower()
        name = " ".join(args[1:]) if len(args) > 1 else ""

        # -- /mcp install NAME ------------------------------------------------
        if subcmd == "install":
            if not name:
                await self._reply(
                    update, "\U0001f50c Usage: <code>/mcp install NAME</code>"
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

        # /watch whatsapp [minutes] — create a new watcher
        if subcmd in ("whatsapp", "wa", "email", "instagram", "ig"):
            service = {"wa": "whatsapp", "ig": "instagram"}.get(subcmd, subcmd)
            interval_min = 2
            if len(args) > 1:
                try:
                    interval_min = max(1, int(args[1]))
                except ValueError:
                    pass

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

            lines.append(f"\n{icon} <b>{name}</b>")
            lines.append(f"    {details}")

        lines.append(
            "\n\n\U0001f527 <code>/watch stop</code> \u2014 stop a watcher"
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
                from lazyclaw.tasks.store import complete_task, update_task
                from datetime import timedelta

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
        from lazyclaw.llm.model_registry import get_mode_models
        # Show ECO-resolved models (not config defaults)
        _mode = "hybrid"
        try:
            from lazyclaw.llm.eco_settings import get_eco_settings
            _eco = await get_eco_settings(self._config, self._admin_user_id or "")
            _mode = _eco.get("mode", "hybrid")
        except Exception:
            pass
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
