"""Background async daemon for proactive heartbeat checks and cron jobs."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import decrypt, is_encrypted
from lazyclaw.db.connection import db_session
from lazyclaw.heartbeat.cron import calculate_next_run, is_due

logger = logging.getLogger(__name__)

# Hosts that MUST be polled through the user's live (signed-in) Brave on
# the primary CDP port. A fresh headless instance with a copied profile
# fails Cloudflare's TLS/JS fingerprint check on these sites and silently
# loads the login or challenge page — the watcher then hashes
# {"unread":0,"rooms":[]} forever and no notification ever fires.
# Subdomain match is automatic.
#
# Two-layer set:
#   - ``_LIVE_BROWSER_WATCHER_HOSTS_BUILTIN`` — ships with lazyclaw, can't
#     be removed by a user (protects upwork/linkedin from accidental
#     opt-out via NL skill).
#   - Per-user extras — stored in ``users.settings.browser.live_hosts``,
#     managed by the ``add_live_browser_host`` / ``remove_live_browser_host``
#     / ``list_live_browser_hosts`` NL skills. The contract-intake
#     auto-setup phase appends the platform host here when it provisions
#     a new gig watcher.
_LIVE_BROWSER_WATCHER_HOSTS_BUILTIN: frozenset[str] = frozenset({
    "upwork.com",
    "linkedin.com",
})


def _host_matches(host: str, hosts: frozenset[str] | set[str] | list[str]) -> bool:
    """True if ``host`` equals or is a subdomain of any entry in ``hosts``.

    Case-insensitive; ``hosts`` entries are expected to be already
    normalized (lowercase, no leading ``www.``). Empty / falsy host
    returns False.
    """
    h = (host or "").lower()
    if not h:
        return False
    for needle in hosts:
        if h == needle or h.endswith("." + needle):
            return True
    return False


def _needs_live_browser(
    host: str,
    user_extras: frozenset[str] | set[str] | list[str] = frozenset(),
) -> bool:
    """True when a host requires polling through the user's signed-in browser.

    Checks the builtin set first (always wins), then the per-user
    extras. Both layers use the same subdomain-match semantics.
    """
    if _host_matches(host, _LIVE_BROWSER_WATCHER_HOSTS_BUILTIN):
        return True
    return _host_matches(host, user_extras)


# Last watcher notification per user — agent reads this for reply context
# Format: {user_id: {"service": "whatsapp", "items": [...], "notification": "...", "timestamp": float}}
_last_watcher_context: dict[str, dict] = {}


def get_last_watcher_context(user_id: str) -> dict | None:
    """Get last watcher notification context for a user. Used by agent for reply context."""
    return _last_watcher_context.get(user_id)


def _store_watcher_context(
    user_id: str,
    service: str,
    items: list,
    notification: str,
    chat_names: list[str] | None = None,
) -> None:
    """Store last watcher notification so agent can reference it.

    chat_names: list of chat/group names mentioned in the notification,
    used for instant mute commands without LLM parsing.
    """
    import time
    _last_watcher_context[user_id] = {
        "service": service,
        "items": items[:5],  # Cap stored items
        "notification": notification,
        "timestamp": time.time(),
        "chat_names": chat_names or [],
    }


def _scan_proc_cmdlines(needle: str) -> list[tuple[int, str]]:
    """Scan /proc/[pid]/cmdline for entries containing ``needle``.

    Returns a list of (pid, full_cmdline_with_spaces). Used by the
    idle-browser reaper instead of shelling out to ``ps aux`` — that
    binary isn't installed in the slim Debian Docker image we ship,
    and the reaper was logging a WARNING every 60s as a result.

    /proc/<pid>/cmdline holds the args separated by NUL bytes; we
    flatten to spaces so callers can do simple substring matches.
    Quietly skips PIDs we can't read (other-uid, race, kernel threads).
    """
    matches: list[tuple[int, str]] = []
    try:
        proc_root = "/proc"
        if not os.path.isdir(proc_root):
            return matches  # macOS host without /proc — no-op
        for entry in os.listdir(proc_root):
            if not entry.isdigit():
                continue
            cmdline_path = f"{proc_root}/{entry}/cmdline"
            try:
                with open(cmdline_path, "rb") as fh:
                    raw = fh.read()
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
            if needle in cmdline:
                try:
                    matches.append((int(entry), cmdline))
                except ValueError:
                    continue
    except Exception:
        # /proc may be unavailable or restricted — fall back to empty
        # list; callers must already handle the no-match case.
        pass
    return matches


class HeartbeatDaemon:
    """Periodically checks for due cron jobs and enqueues them."""

    def __init__(
        self,
        config: Config,
        lane_queue,
        telegram_push=None,
        notifier_factory=None,
        team_lead=None,
    ) -> None:
        self._config = config
        self._lane_queue = lane_queue
        self._telegram_push = telegram_push  # async fn(text) → send to Telegram admin
        # Called per background-fired enqueue to build a callback that pushes
        # the agent's reply to Telegram with a "[icon] [name]" header so
        # cron/reminder/watcher pushes are distinguishable from foreground task
        # completions. Signature: (prefix: str, icon: str = "⏰") -> AgentCallback
        self._notifier_factory = notifier_factory
        # TeamLead reference so the idle-browser reaper can see live
        # subagent / specialist work. Without it the reaper only checks
        # the ``background_tasks`` table and kills Chrome out from under
        # an explore subagent that's mid-scrape.
        self._team_lead = team_lead
        self._task: asyncio.Task | None = None
        # In-memory record of "we already seeded today's journal for this user".
        # Resets on restart (idempotent re-seed via tag lookup is cheap).
        self._last_journal_seed_iso: dict[str, str] = {}
        # Last day we ran the LazyBrain topic-rollup sweep per user. Cooldown
        # check inside the job is the real gate; this just bounds tick cost.
        self._last_topic_rollup_iso: dict[str, str] = {}
        # Tick counter so the dirty-embedding reindex pass + plan ingest run
        # at sensible cadences (every tick / once an hour) without blocking
        # the cron + watcher passes that need to fire promptly.
        self._tick_count: int = 0
        # Last day we fired the end-of-day progress summary per user.
        # Same pattern as _last_journal_seed_iso — bounds tick cost.
        self._last_eod_summary_iso: dict[str, str] = {}

    async def start(self) -> None:
        """Launch the heartbeat loop as a background task."""
        if self._task is not None:
            logger.warning("HeartbeatDaemon already running")
            return
        # Fix C: scrub instruction-drift on managed survival crons before
        # the first tick — every drift wastes ~45s per cron fire because
        # instant_dispatch can no longer recognise the intent.
        try:
            await self._restore_survival_cron_drift()
        except Exception:
            logger.exception(
                "survival cron drift restore failed; continuing startup"
            )
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "HeartbeatDaemon started (interval=%ds)",
            self._config.heartbeat_interval,
        )

    async def _restore_survival_cron_drift(self) -> None:
        """Restore canonical instruction text on managed survival crons.

        Loads every user with a job whose name is in
        ``SURVIVAL_CANONICAL_INSTRUCTIONS``, compares the persisted
        instruction to the canonical text, and updates if they differ.
        Logs one line per restore so the operator sees what happened.
        No-ops cleanly when nothing has drifted.
        """
        from lazyclaw.heartbeat.orchestrator import list_jobs, update_job
        from lazyclaw.skills.builtin.survival.mode_skill import (
            SURVIVAL_CANONICAL_INSTRUCTIONS,
        )
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT DISTINCT user_id FROM agent_jobs WHERE name IN ("
                + ",".join("?" * len(SURVIVAL_CANONICAL_INSTRUCTIONS))
                + ")",
                tuple(SURVIVAL_CANONICAL_INSTRUCTIONS.keys()),
            )
            user_ids = [r[0] for r in await cursor.fetchall()]
        restored = 0
        checked = 0
        for user_id in user_ids:
            try:
                jobs = await list_jobs(self._config, user_id)
            except Exception:
                logger.debug(
                    "drift restore: list_jobs failed for user %s",
                    user_id, exc_info=True,
                )
                continue
            for job in jobs:
                name = job.get("name")
                canonical = SURVIVAL_CANONICAL_INSTRUCTIONS.get(name)
                if canonical is None:
                    continue
                checked += 1
                current = job.get("instruction") or ""
                if current == canonical:
                    continue
                try:
                    await update_job(
                        self._config, user_id, job["id"],
                        instruction=canonical,
                    )
                    restored += 1
                    logger.warning(
                        "Survival cron drift detected — restored %s "
                        "(user=%s): %r -> %r",
                        name, user_id[:8], current[:60], canonical,
                    )
                except Exception:
                    logger.exception(
                        "drift restore failed for %s (user=%s)",
                        name, user_id[:8],
                    )
        if checked:
            logger.info(
                "Survival cron drift scan complete: checked=%d restored=%d",
                checked, restored,
            )

    async def stop(self) -> None:
        """Cancel the heartbeat loop and wait for clean shutdown."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await asyncio.shield(self._task)
        except (asyncio.CancelledError, Exception):
            # Intentional: swallow cancellation/shutdown errors when stopping the daemon
            pass
        self._task = None
        logger.info("HeartbeatDaemon stopped")

    async def _loop(self) -> None:
        """Infinite loop: tick then sleep."""
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("HeartbeatDaemon tick failed")
            await asyncio.sleep(self._config.heartbeat_interval)

    async def _tick(self) -> None:
        """Single heartbeat: find users with active jobs and check them."""
        self._tick_count += 1

        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT DISTINCT user_id FROM agent_jobs WHERE status = 'active'"
            )
            user_ids = [r[0] for r in await cursor.fetchall()]

        for user_id in user_ids:
            try:
                await self._check_due_jobs(user_id)
            except Exception:
                logger.exception(
                    "Failed checking due jobs for user %s", user_id
                )
            try:
                await self._check_watchers(user_id)
            except Exception:
                logger.exception(
                    "Failed checking watchers for user %s", user_id
                )
            try:
                await self._check_mcp_watchers(user_id)
            except Exception:
                logger.exception(
                    "Failed checking MCP watchers for user %s", user_id
                )

        # Check task reminders that need nagging (Due App-style)
        await self._check_task_nagging()

        # Seed today's LazyBrain journal for each registered user (once/day).
        await self._seed_today_journals()

        # Run LazyBrain topic-rollup sweep once per day per user.
        await self._sweep_topic_rollups()

        # Second-Brain Substrate (Phase 2): drain dirty embeddings.
        # Removed 2026-05-21: hourly mirror of ~/.claude/plans/*.md into
        # LazyBrain (cross-project session leak — Claude Code plans from
        # ANY project were being ingested as user notes here).
        await self._reindex_dirty_embeddings()

        # Retry tasks whose initial LazyBrain mirror failed (Ollama down,
        # encryption hiccup, etc.). Cheap NOOP when there's nothing to retry.
        try:
            await self._retry_lazybrain_mirrors()
        except Exception:
            logger.debug("LazyBrain mirror retry sweep failed", exc_info=True)

        # Stale-task soft nudge — fire once per 4h-silent in_progress task
        # so forgotten work doesn't drift forever. Cleared on user reply.
        try:
            await self._sweep_stale_progress()
        except Exception:
            logger.debug("stale progress sweep failed", exc_info=True)

        # End-of-day progress summary — once per user per day at 20:00
        # local. Read-only, no LLM call. Opt-in via settings.general.eod_summary.
        try:
            await self._sweep_eod_summary()
        except Exception:
            logger.debug("EOD summary sweep failed", exc_info=True)

        # Keep persistent browser alive if enabled for any user
        await self._ensure_persistent_browser()

        # Tab health: reap idle tabs (default 10 min), enforce max_open_tabs
        # cap, and refresh white-screen tabs. Runs every 5 ticks (~5 min
        # with the default 60s heartbeat interval) — frequent enough to
        # claw back RAM, infrequent enough not to thrash the user's Brave.
        # Skipped on the first tick to avoid sweeping before users are even
        # registered.
        if self._tick_count > 1 and self._tick_count % 5 == 0:
            try:
                await self._check_tab_health()
            except Exception:
                logger.debug("tab health sweep failed", exc_info=True)

    async def _check_tab_health(self) -> None:
        """Per-user tab reap + cap + white-screen refresh.

        Uses the live (primary) CDP backend — the user's signed-in Brave —
        because that's where stray tabs accumulate. Background headless
        instances tear down their own tabs at job end, so they don't need
        sweeping.

        Skips users who have ``persistent`` browser mode = "off" (no
        live browser to sweep). Skips when no Brave is reachable on the
        primary CDP port (nothing to do, no error).
        """
        from lazyclaw.browser.browser_settings import get_browser_settings
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.browser.tab_reaper import run_tab_health_cycle

        primary_port = getattr(self._config, "cdp_port", 9222)
        ws_url = await find_chrome_cdp(primary_port)
        if not ws_url:
            return  # no live Brave reachable, nothing to sweep

        async with db_session(self._config) as db:
            cursor = await db.execute("SELECT DISTINCT id FROM users")
            user_ids = [r[0] for r in await cursor.fetchall()]

        for user_id in user_ids:
            try:
                settings = await get_browser_settings(self._config, user_id)
            except Exception:
                continue
            if settings.get("persistent") == "off":
                continue

            idle_seconds = float(
                settings.get("idle_tab_close_seconds", 600)
            )
            max_tabs = int(settings.get("max_open_tabs", 8))
            refresh_blanks = bool(
                settings.get("auto_refresh_white_screens", True)
            )

            # Anchored hosts = URLs every active watcher targets, so
            # the reaper doesn't close the tab a watcher needs to poll.
            anchored_urls = await self._gather_watcher_urls(user_id)

            backend = self._get_primary_cdp(user_id)
            try:
                summary = await run_tab_health_cycle(
                    backend,
                    idle_seconds=idle_seconds,
                    max_tabs=max_tabs,
                    anchored_urls=anchored_urls,
                    refresh_blanks=refresh_blanks,
                )
                if (
                    summary["idle_closed"]
                    or summary["cap_closed"]
                    or summary["blanks_refreshed"]
                ):
                    logger.info(
                        "tab health user=%s scanned=%d idle_closed=%d "
                        "cap_closed=%d blanks_refreshed=%d anchored=%s",
                        user_id[:8],
                        summary["tabs_scanned"],
                        summary["idle_closed"],
                        summary["cap_closed"],
                        summary["blanks_refreshed"],
                        summary["anchored_hosts"],
                    )
            except Exception:
                logger.debug(
                    "tab health cycle failed for user %s", user_id,
                    exc_info=True,
                )
            finally:
                try:
                    await backend.close()
                except Exception:
                    logger.debug(
                        "tab health backend close failed", exc_info=True,
                    )

    @staticmethod
    def _build_watcher_keyboard(
        job_id: str,
        service: str,
        notified_items: list[dict],
    ) -> list[list[dict]]:
        """Build the inline keyboard attached to every MCP watcher push.

        Layout:
            [ 🔇 Mute chat ]               (whatsapp only — kills source noise)
            [ ⏰ 1h ] [ ⏰ 4h ] [ ⏰ 8h ]   (universal snooze controls)

        callback_data format (≤64B Telegram cap):
            wmute:<short_job>:<short_chat>   — mute the surfaced chat
            wsnooze:<short_job>:<minutes>    — pause this watcher

        ``short_job`` is the first 12 chars of the job id; combined with
        the chat slug it stays inside Telegram's hard limit while staying
        unique enough across active watchers per user.
        """
        short_job = job_id[:12]
        rows: list[list[dict]] = []

        # WhatsApp gets a one-tap mute that calls whatsapp_mute on the
        # MCP side — the chat goes silent on the user's phone too, not
        # just in lazyclaw, so noise is killed at the source.
        if service == "whatsapp" and notified_items:
            first = notified_items[0]
            chat_label = (
                first.get("groupName")
                or first.get("chatName")
                or first.get("from", "")
            ) or ""
            # Slug to ASCII-ish, max 24 chars so callback_data fits.
            slug = "".join(
                c if c.isalnum() else "_" for c in chat_label
            )[:24] or "chat"
            rows.append([
                {"text": "\U0001f507 Mute chat", "callback_data": f"wmute:{short_job}:{slug}"},
            ])

        rows.append([
            {"text": "⏰ 1h", "callback_data": f"wsnooze:{short_job}:60"},
            {"text": "⏰ 4h", "callback_data": f"wsnooze:{short_job}:240"},
            {"text": "⏰ 8h", "callback_data": f"wsnooze:{short_job}:480"},
        ])
        return rows

    async def _gather_watcher_urls(self, user_id: str) -> list[str]:
        """Return URLs of every active browser watcher (job_type='watcher')
        for this user. Used by ``_check_tab_health`` to mark tabs the
        watcher framework still needs open."""
        import json as _json
        out: list[str] = []
        try:
            key = await get_user_dek(self._config, user_id)
        except Exception:
            return out
        try:
            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT context FROM agent_jobs "
                    "WHERE user_id = ? AND status = 'active' "
                    "AND job_type = 'watcher'",
                    (user_id,),
                )
                rows = await cursor.fetchall()
        except Exception:
            return out
        for (enc_ctx,) in rows:
            try:
                raw = (
                    decrypt(enc_ctx, key)
                    if enc_ctx and is_encrypted(enc_ctx)
                    else enc_ctx or "{}"
                )
                ctx = _json.loads(raw) if raw else {}
                url = ctx.get("url")
                if url and isinstance(url, str):
                    out.append(url)
            except Exception:
                continue
        return out

    async def _check_due_jobs(self, user_id: str) -> None:
        """Load active jobs for a user and enqueue any that are due."""
        from lazyclaw.heartbeat import orchestrator

        key = await get_user_dek(self._config, user_id)

        # Check cron jobs (recurring). next_run is read so is_due can honour
        # the schedule on the FIRST tick after creation; without it, fresh
        # jobs (last_run IS NULL) used to fire on the very next heartbeat
        # regardless of the cron expression.
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, name, instruction, cron_expression, last_run, next_run "
                "FROM agent_jobs "
                "WHERE user_id = ? AND status = 'active' AND cron_expression IS NOT NULL",
                (user_id,),
            )
            cron_jobs = await cursor.fetchall()

        for row in cron_jobs:
            job_id, enc_name, enc_instruction, cron_expression, last_run, next_run = row
            try:
                if not is_due(cron_expression, last_run, next_run):
                    continue

                job_name = (
                    decrypt(enc_name, key)
                    if enc_name and is_encrypted(enc_name)
                    else enc_name
                )
                instruction = (
                    decrypt(enc_instruction, key)
                    if enc_instruction and is_encrypted(enc_instruction)
                    else enc_instruction
                )

                # Pulse jobs short-circuit before the brain. Instruction
                # shape is "[PULSE:<task_id>:<template_id>]" — we render
                # the template via Telegram directly with zero LLM cost.
                if instruction and instruction.startswith("[PULSE:"):
                    handled = await self._fire_task_pulse(
                        user_id, job_id, instruction, cron_expression,
                    )
                    if handled:
                        continue
                    # Fall through if pulse couldn't be fired (template
                    # missing, task done) — orchestrator.mark_run pauses
                    # below.

                logger.info("Job '%s' (%s) is due, enqueueing", job_name, job_id)
                if not self._lane_queue._running:
                    logger.debug("LaneQueue not ready yet — skipping job '%s' this tick", job_name)
                    continue
                cb = (
                    self._notifier_factory(job_name, "⏰")
                    if self._notifier_factory else None
                )
                cb_kwargs = {"callback": cb} if cb is not None else {}
                run_failed = False
                run_error: str | None = None
                try:
                    # Route through ``{user_id}:heartbeat`` so the cron's
                    # brain turn runs in parallel with the user's
                    # foreground chat lane (LaneQueue keys per string;
                    # different keys = independent FIFO processors).
                    # Without this, a cron tick blocks the user's next
                    # message for 30–120s under MODE_CLAUDE.
                    result_text = await self._lane_queue.enqueue(
                        f"{user_id}:heartbeat",
                        f"[JOB:{job_name}] {instruction}",
                        **cb_kwargs,
                    )
                except Exception as exc:
                    run_failed = True
                    run_error = f"{type(exc).__name__}: {exc}"
                    logger.exception("Lane enqueue raised for cron job %s", job_id)
                else:
                    if isinstance(result_text, str) and result_text.startswith(
                        "Error processing message:"
                    ):
                        run_failed = True
                        run_error = result_text[:500]

                next_run = calculate_next_run(cron_expression)
                await orchestrator.mark_run(self._config, job_id, next_run)
                try:
                    if run_failed:
                        await orchestrator.mark_run_outcome(
                            self._config, user_id, job_id,
                            "failed", error=run_error,
                        )
                    else:
                        await orchestrator.mark_run_outcome(
                            self._config, user_id, job_id, "success",
                        )
                except Exception:
                    logger.debug(
                        "mark_run_outcome failed for cron job %s",
                        job_id, exc_info=True,
                    )
            except Exception:
                logger.exception("Error processing job %s for user %s", job_id, user_id)

        # Check one-time reminders
        await self._check_due_reminders(user_id, key)

    async def _check_due_reminders(self, user_id: str, key: bytes) -> None:
        """Fire one-time reminders that are due, then auto-delete them."""
        from datetime import datetime, timezone
        from lazyclaw.heartbeat.orchestrator import delete_job

        now = datetime.now(timezone.utc).isoformat()

        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, name, instruction, next_run "
                "FROM agent_jobs "
                "WHERE user_id = ? AND status = 'active' "
                "AND job_type = 'reminder' AND next_run IS NOT NULL "
                "AND next_run <= ?",
                (user_id, now),
            )
            reminders = await cursor.fetchall()

        for row in reminders:
            job_id, enc_name, enc_instruction, next_run = row
            try:
                message = (
                    decrypt(enc_instruction, key)
                    if enc_instruction and is_encrypted(enc_instruction)
                    else enc_instruction
                )

                # Skip task-linked reminders — handled by _check_task_nagging
                # with inline buttons (Done/Snooze/Tomorrow)
                if message and "[TASK_REMINDER:" in message:
                    continue

                job_name = (
                    decrypt(enc_name, key)
                    if enc_name and is_encrypted(enc_name)
                    else enc_name
                )

                logger.info("Reminder '%s' (%s) is due, firing", job_name, job_id)

                # Enqueue as agent message (reaches Telegram via callback)
                cb = (
                    self._notifier_factory(job_name or "Reminder", "🔔")
                    if self._notifier_factory else None
                )
                cb_kwargs = {"callback": cb} if cb is not None else {}
                # Heartbeat lane — see note at _check_due_jobs above.
                await self._lane_queue.enqueue(
                    f"{user_id}:heartbeat",
                    f"[REMINDER] {message}",
                    **cb_kwargs,
                )

                # Auto-delete — one-shot reminder, done
                await delete_job(self._config, user_id, job_id)
                logger.info("Reminder '%s' auto-deleted after firing", job_name)
            except Exception:
                logger.exception(
                    "Error processing reminder %s for user %s", job_id, user_id,
                )

    def _get_primary_cdp(self, user_id: str):
        """Return a CDPBackend pointing at the user's live Brave on the primary port.

        Does NOT copy the profile or launch a separate browser — this
        backend connects to the user's already-running, signed-in
        instance. Used for Cloudflare-protected watchers (see
        ``_LIVE_BROWSER_WATCHER_HOSTS``) where a fresh headless on a
        copied profile gets bounced at the challenge page.

        Caller MUST NOT close this backend in cleanup — closing would
        tear down the connection but the user's Brave would keep
        running. Just drop the reference.
        """
        from lazyclaw.browser.cdp_backend import CDPBackend
        from lazyclaw.browser.profile_resolver import resolve_profile_dir

        primary_port = getattr(self._config, "cdp_port", 9222)
        profile_dir = resolve_profile_dir(self._config, user_id)
        return CDPBackend(port=primary_port, profile_dir=str(profile_dir))

    async def _get_background_cdp(self, user_id: str):
        """Get a CDP backend for background jobs without touching the user's live Brave.

        Strategy:
        - If no browser on port 9222: launch headless on 9222 (normal path)
        - If headless browser on port 9222: reuse it directly
        - If VISIBLE browser on port 9222: copy cookies to temp dir,
          launch a separate headless instance on port 9223

        Returns (CDPBackend, temp_dir_path_or_None). Caller must clean up
        temp_dir if returned.
        """
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.browser.cdp_backend import CDPBackend
        from lazyclaw.browser.profile_resolver import resolve_profile_dir

        primary_port = getattr(self._config, "cdp_port", 9222)
        profile_dir = resolve_profile_dir(self._config, user_id)

        # Check if something is running on the primary port
        ws_url = await find_chrome_cdp(primary_port)

        if not ws_url:
            # Nothing running — use primary port, auto-launch will handle it
            return CDPBackend(port=primary_port, profile_dir=str(profile_dir)), None

        # Something IS running — check if it's headless. /proc scan
        # works in both the slim container (no ``ps`` binary) and on
        # the host.
        is_headless = False
        try:
            matches = _scan_proc_cmdlines(f"remote-debugging-port={primary_port}")
            is_headless = any("headless" in cmdline for _, cmdline in matches)
        except Exception:
            # Can't tell — assume it's visible to be safe
            logger.debug("Failed to check if browser is headless, assuming visible", exc_info=True)

        if is_headless:
            # Headless on primary port — safe to reuse directly
            return CDPBackend(port=primary_port, profile_dir=str(profile_dir)), None

        # Visible browser on primary port — copy cookies to temp dir,
        # launch separate headless on background port (9223)
        bg_port = primary_port + 1  # 9223
        temp_dir = None

        try:
            temp_dir = tempfile.mkdtemp(prefix="lazyclaw_bg_")
            # Skip runtime lock/socket files that can't be copied
            _SKIP_NAMES = {"SingletonSocket", "SingletonLock", "SingletonCookie", "RunningChromeVersion"}

            def _ignore_runtime(directory: str, files: list[str]) -> set[str]:
                return {f for f in files if f in _SKIP_NAMES}

            if profile_dir.exists():
                shutil.copytree(
                    str(profile_dir), f"{temp_dir}/profile",
                    dirs_exist_ok=True, ignore=_ignore_runtime,
                )
                logger.info(
                    "Copied cookies to temp profile for background CDP (port %d)",
                    bg_port,
                )
            backend = CDPBackend(port=bg_port, profile_dir=f"{temp_dir}/profile")
            return backend, temp_dir
        except Exception as exc:
            logger.warning("Failed to create background CDP: %s, falling back to primary", exc)
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return CDPBackend(port=primary_port, profile_dir=str(profile_dir)), None

    async def _cleanup_background_cdp(self, backend, temp_dir: str | None) -> None:
        """Clean up background CDP resources."""
        try:
            await backend.close()
        except Exception:
            logger.warning("Failed to close background CDP backend", exc_info=True)
        if temp_dir:
            # Kill the background headless if we launched one
            bg_port = getattr(self._config, "cdp_port", 9222) + 1
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pkill", "-f", f"--remote-debugging-port={bg_port}",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except Exception:
                logger.warning("Failed to pkill background headless browser on port %d", bg_port, exc_info=True)
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _check_watchers(self, user_id: str) -> None:
        """Check all active watchers for a user. Zero LLM calls."""
        import json
        from urllib.parse import urlparse

        from lazyclaw.browser.browser_settings import touch_browser_activity
        from lazyclaw.browser.watcher import (
            check_watcher,
            is_check_due,
            is_watcher_expired,
        )
        from lazyclaw.heartbeat.orchestrator import delete_job, update_job

        key = await get_user_dek(self._config, user_id)

        # Fetch active watchers
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, name, instruction, context "
                "FROM agent_jobs "
                "WHERE user_id = ? AND status = 'active' AND job_type = 'watcher'",
                (user_id,),
            )
            watchers = await cursor.fetchall()

        if not watchers:
            return

        # Per-user extras for live-browser host routing (e.g., a gig
        # platform the user added via add_live_browser_host). Loaded once
        # per heartbeat tick to keep _needs_live_browser sync + cheap.
        from lazyclaw.browser.browser_settings import get_live_hosts
        try:
            user_live_hosts = frozenset(await get_live_hosts(self._config, user_id))
        except Exception:
            logger.debug(
                "get_live_hosts failed for user %s — defaulting to builtin-only",
                user_id, exc_info=True,
            )
            user_live_hosts = frozenset()

        # Lazily allocate backends so we don't spin up a separate
        # headless instance for users whose only watchers are
        # Cloudflare-protected (and vice versa).
        bg_backend = None
        bg_temp_dir = None
        live_backend = None

        try:
            for row in watchers:
                job_id, enc_name, enc_instruction, enc_context = row
                try:
                    job_name = (
                        decrypt(enc_name, key)
                        if enc_name and is_encrypted(enc_name)
                        else enc_name or "unnamed"
                    )

                    # Decrypt and parse context
                    raw_ctx = (
                        decrypt(enc_context, key)
                        if enc_context and is_encrypted(enc_context)
                        else enc_context or "{}"
                    )
                    ctx = json.loads(raw_ctx)

                    # Skip MCP watchers — handled by _check_mcp_watchers()
                    if ctx.get("type") == "mcp_watcher":
                        continue

                    # Check expiration
                    if is_watcher_expired(ctx):
                        logger.info("Watcher '%s' expired, removing", job_name)
                        await delete_job(self._config, user_id, job_id)
                        try:
                            from lazyclaw.watchers import history as _hist
                            _hist.forget_watcher(user_id, job_id)
                        except Exception:
                            logger.debug("history forget failed", exc_info=True)
                        cb = (
                            self._notifier_factory(
                                f"Watcher expired: {job_name}", "👁️",
                            )
                            if self._notifier_factory else None
                        )
                        cb_kwargs = {"callback": cb} if cb is not None else {}
                        # Heartbeat lane — see note at _check_due_jobs above.
                        await self._lane_queue.enqueue(
                            f"{user_id}:heartbeat",
                            f"[WATCHER] '{job_name}' has expired and stopped.",
                            **cb_kwargs,
                        )
                        continue

                    # Check interval
                    if not is_check_due(ctx):
                        continue

                    # Route per-watcher: Cloudflare-protected hosts must
                    # poll through the user's live signed-in Brave or
                    # they're silently blocked at the JS challenge.
                    # Builtin set ∪ user's per-account extras.
                    watch_host = (urlparse(ctx.get("url", "")).hostname or "").lower()
                    use_live = _needs_live_browser(watch_host, user_live_hosts)
                    if use_live:
                        if live_backend is None:
                            live_backend = self._get_primary_cdp(user_id)
                        active_backend = live_backend
                    else:
                        if bg_backend is None:
                            bg_backend, bg_temp_dir = await self._get_background_cdp(user_id)
                        active_backend = bg_backend

                    # Run the check — zero LLM calls
                    touch_browser_activity()
                    check_error: str | None = None
                    try:
                        changed, notification, new_ctx = await check_watcher(
                            active_backend, ctx, passive=use_live,
                        )
                    except Exception as exc:
                        check_error = f"{type(exc).__name__}: {exc}"
                        # Record the failure before re-raising to keep the
                        # existing top-level handler behavior.
                        try:
                            from lazyclaw.watchers import history as _hist
                            _hist.record_check(
                                user_id, job_id,
                                changed=False,
                                triggered=False,
                                error=check_error,
                            )
                        except Exception:
                            logger.debug("history record (error) failed", exc_info=True)
                        raise

                    # Save updated context (new last_value, last_check)
                    await update_job(
                        self._config, user_id, job_id,
                        context=json.dumps(new_ctx),
                    )

                    # Record this check in the in-memory ring for the Watchers UI
                    try:
                        from lazyclaw.watchers import history as _hist
                        _hist.record_check(
                            user_id, job_id,
                            changed=changed,
                            triggered=bool(changed and notification),
                            value_preview=(
                                (new_ctx.get("last_value") or "")[:500] or None
                            ),
                            notification=notification if (changed and notification) else None,
                        )
                    except Exception:
                        logger.debug("history record failed", exc_info=True)

                    if changed and notification:
                        logger.info("Watcher '%s' detected change", job_name)

                        # Light up the BrowserCanvas (zero LLM tokens)
                        try:
                            from lazyclaw.browser import event_bus
                            event_bus.publish(event_bus.BrowserEvent(
                                user_id=user_id,
                                kind="alert",
                                target=job_name,
                                detail=notification[:200],
                                extra={
                                    "template_name": ctx.get("template_name"),
                                    "template_id": ctx.get("template_id"),
                                    "watch_url": ctx.get("url"),
                                },
                            ))
                        except Exception:
                            logger.debug("Canvas alert publish failed", exc_info=True)

                        # Resolve the on_change_instruction up-front so we
                        # can make the push/brain decision atomically.
                        # Watchers double-fired in the past — once with the
                        # raw 🔔 push, once with the brain's "done" reply —
                        # which read as duplicate spam in Telegram. Now the
                        # contract is exactly ONE Telegram message per
                        # detected change:
                        #   * accept_slug present → 🔔 + Accept/Skip buttons
                        #     (no brain turn — buttons drive the next step)
                        #   * brain turn will fire → SKIP raw push, the
                        #     brain's reply IS the message
                        #   * otherwise → 🔔 raw push (no brain turn)
                        accept_slug = ctx.get("accept_template_slug")
                        on_change_instr = (
                            ctx.get("on_change_instruction")
                            if "on_change_instruction" in ctx
                            else new_ctx.get("on_change_instruction")
                            if "on_change_instruction" in new_ctx
                            else (
                                f"Watcher '{job_name}' detected new content "
                                f"at {ctx.get('url', '')}. Read the page, "
                                f"summarize what changed, and route per "
                                f"normal rules (auto-reply where safe, "
                                f"escalate sensitive items, draft for "
                                f"active deals). Stay terse."
                            )
                        )
                        will_fire_brain = bool(
                            on_change_instr
                            and on_change_instr.strip()
                            and self._lane_queue
                            and self._lane_queue._running
                            and not accept_slug
                        )

                        if accept_slug:
                            try:
                                from lazyclaw.notifications.push import push_telegram
                                ok = await push_telegram(
                                    self._config,
                                    f"🔔 {notification}",
                                    parse_mode=None,
                                    inline_keyboard=[[
                                        {"text": "✅ Accept",
                                         "callback_data": f"accept:{accept_slug}"},
                                        {"text": "⏭ Skip",
                                         "callback_data": f"accept:skip:{accept_slug}"},
                                    ]],
                                )
                                if not ok:
                                    logger.warning(
                                        "Telegram keyboard push for %s returned False",
                                        accept_slug,
                                    )
                            except Exception as exc:
                                logger.warning(
                                    "Telegram keyboard push failed (%s): %s",
                                    accept_slug, exc,
                                )
                        elif not will_fire_brain and self._telegram_push:
                            # No brain turn will run — surface the raw alert
                            # ourselves with snooze buttons so the user can
                            # quiet the watcher without losing it.
                            try:
                                from lazyclaw.notifications.push import push_telegram
                                logger.info("Pushing watcher notification to Telegram (no brain)")
                                await push_telegram(
                                    self._config,
                                    f"🔔 {notification}",
                                    parse_mode=None,
                                    inline_keyboard=self._build_watcher_keyboard(
                                        job_id, "browser", [],
                                    ),
                                )
                            except Exception as exc:
                                logger.warning("Telegram push failed: %s", exc)

                        if will_fire_brain:
                            try:
                                instr = on_change_instr.strip()
                                if instr:
                                    # Heartbeat lane — see note at
                                    # _check_due_jobs above.
                                    asyncio.create_task(
                                        self._lane_queue.enqueue(
                                            f"{user_id}:heartbeat",
                                            f"[WATCHER:{job_name}] {instr}",
                                        ),
                                        name=f"watcher-brain-{job_id[:8]}",
                                    )
                                    logger.info(
                                        "Watcher '%s' enqueued brain turn on change",
                                        job_name,
                                    )
                            except Exception:
                                logger.warning(
                                    "Watcher '%s' brain enqueue failed",
                                    job_name, exc_info=True,
                                )

                        # One-shot watcher — auto-delete after first trigger
                        if new_ctx.get("one_shot"):
                            await delete_job(self._config, user_id, job_id)
                            logger.info("One-shot watcher '%s' auto-deleted", job_name)

                except Exception:
                    logger.exception(
                        "Error checking watcher %s for user %s", job_id, user_id,
                    )
        finally:
            # Only the background backend owns lifecycle (it spawned a
            # separate headless + temp profile). The live backend just
            # holds a CDP socket to the user's Brave; closing it is a
            # no-op for the browser but we still drop the socket cleanly.
            if bg_backend is not None:
                await self._cleanup_background_cdp(bg_backend, bg_temp_dir)
            if live_backend is not None:
                try:
                    await live_backend.close()
                except Exception:
                    logger.debug(
                        "Failed to close live CDP backend handle", exc_info=True,
                    )

    async def _check_mcp_watchers(self, user_id: str) -> None:
        """Check all MCP-based watchers (WhatsApp, Email, etc.). Zero LLM calls."""
        import json

        from lazyclaw.heartbeat.mcp_watcher import (
            check_mcp_watcher,
            is_mcp_check_due,
            is_mcp_watcher,
            is_mcp_watcher_expired,
        )
        from lazyclaw.heartbeat.orchestrator import delete_job, update_job

        key = await get_user_dek(self._config, user_id)

        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, name, instruction, context "
                "FROM agent_jobs "
                "WHERE user_id = ? AND status = 'active' AND job_type = 'watcher'",
                (user_id,),
            )
            watchers = await cursor.fetchall()

        if not watchers:
            return

        # Get active MCP clients
        from lazyclaw.mcp.manager import _active_clients

        for row in watchers:
            job_id, enc_name, enc_instruction, enc_context = row
            try:
                job_name = (
                    decrypt(enc_name, key)
                    if enc_name and is_encrypted(enc_name)
                    else enc_name or "unnamed"
                )
                raw_ctx = (
                    decrypt(enc_context, key)
                    if enc_context and is_encrypted(enc_context)
                    else enc_context or "{}"
                )
                ctx = json.loads(raw_ctx)

                # Only handle MCP watchers here (browser watchers handled by _check_watchers)
                if not is_mcp_watcher(ctx):
                    continue

                if is_mcp_watcher_expired(ctx):
                    logger.info("MCP watcher '%s' expired, removing", job_name)
                    await delete_job(self._config, user_id, job_id)
                    try:
                        from lazyclaw.watchers import history as _hist
                        _hist.forget_watcher(user_id, job_id)
                    except Exception:
                        logger.debug("history forget failed", exc_info=True)
                    if self._telegram_push:
                        await self._telegram_push(f"MCP watcher '{job_name}' expired and stopped.")
                    continue

                if not is_mcp_check_due(ctx):
                    continue

                # Run the MCP check
                logger.info("MCP watcher '%s' checking (%s)...", job_name, ctx.get("service", "?"))
                mcp_check_error: str | None = None
                try:
                    changed, notification, new_ctx = await check_mcp_watcher(
                        ctx, _active_clients,
                        config=self._config, user_id=user_id,
                    )
                except Exception as exc:
                    mcp_check_error = f"{type(exc).__name__}: {exc}"
                    try:
                        from lazyclaw.watchers import history as _hist
                        _hist.record_check(
                            user_id, job_id,
                            changed=False, triggered=False, error=mcp_check_error,
                        )
                    except Exception:
                        logger.debug("history record (error) failed", exc_info=True)
                    raise

                # Save updated context
                await update_job(
                    self._config, user_id, job_id,
                    context=json.dumps(new_ctx),
                )

                # ── First-poll baseline push (one-time, friendly) ──
                # mcp_watcher.check_mcp_watcher returns changed=False with a
                # _baseline_count key on the very first poll. Surface it
                # ONCE so the user sees the watcher is alive without being
                # flooded by their existing backlog.
                if not changed and "_baseline_count" in new_ctx:
                    baseline = int(new_ctx.get("_baseline_count", 0))
                    svc = ctx.get("service", "watcher")
                    if self._telegram_push:
                        try:
                            await self._telegram_push(
                                f"\U0001f441️ Watching <b>{svc}</b> · baseline "
                                f"{baseline} message{'s' if baseline != 1 else ''} "
                                f"recorded. Only <i>new</i> messages will notify."
                            )
                        except Exception:
                            logger.debug("baseline push failed", exc_info=True)
                    # Strip the marker so it doesn't persist on disk.
                    new_ctx.pop("_baseline_count", None)
                    await update_job(
                        self._config, user_id, job_id,
                        context=json.dumps(new_ctx),
                    )

                # Record this MCP check in the watcher history ring.
                try:
                    from lazyclaw.watchers import history as _hist
                    _hist.record_check(
                        user_id, job_id,
                        changed=changed,
                        triggered=bool(changed and notification),
                        value_preview=(notification[:500] if changed and notification else None),
                        notification=notification if (changed and notification) else None,
                    )
                except Exception:
                    logger.debug("history record failed", exc_info=True)

                if changed and notification:
                    logger.info("MCP watcher '%s' detected change", job_name)

                    # Push to Telegram with inline buttons for one-tap control.
                    # The "🔇 Mute chat" button mutes the specific chat on
                    # WhatsApp's side (via whatsapp_mute MCP tool) — kills
                    # the noise at the source, not just in this notifier.
                    # The "⏰ 1h / 4h" buttons set ctx.snoozed_until so the
                    # watcher itself stays alive but stops polling for a
                    # window — perfect for "I'm in a meeting, hush".
                    _notified = new_ctx.get("_notified_items", [])
                    _service = ctx.get("service", "")
                    keyboard = self._build_watcher_keyboard(job_id, _service, _notified)
                    if self._config and (self._telegram_push or True):
                        try:
                            from lazyclaw.notifications.push import push_telegram
                            await push_telegram(
                                self._config,
                                f"\U0001f514 {notification}",
                                parse_mode=None,
                                inline_keyboard=keyboard,
                            )
                        except Exception as exc:
                            logger.warning("Telegram watcher push failed: %s", exc)

                    # Store last notification so agent has context for user replies
                    # Extract chat names from notified items for instant mute
                    _chat_names = list({
                        item.get("chatName") or item.get("groupName") or item.get("from", "")
                        for item in _notified
                        if item.get("chatName") or item.get("groupName") or item.get("from")
                    })
                    _store_watcher_context(
                        user_id, ctx.get("service", ""), _notified[:5],
                        notification, chat_names=_chat_names,
                    )

                    # Auto-reply: enqueue to agent if instruction provided
                    auto_reply = ctx.get("auto_reply")
                    if auto_reply and self._lane_queue:
                        _svc = ctx.get("service", "")
                        cb = (
                            self._notifier_factory(
                                f"{_svc or 'MCP'} auto-reply", "📨",
                            )
                            if self._notifier_factory else None
                        )
                        cb_kwargs = {"callback": cb} if cb is not None else {}
                        # Heartbeat lane — see note at _check_due_jobs above.
                        await self._lane_queue.enqueue(
                            f"{user_id}:heartbeat",
                            f"[MCP_WATCHER] New {_svc} messages. {auto_reply}\n\n{notification}",
                            **cb_kwargs,
                        )

                    if new_ctx.get("one_shot"):
                        await delete_job(self._config, user_id, job_id)
                        logger.info("One-shot MCP watcher '%s' auto-deleted", job_name)

            except Exception:
                logger.exception(
                    "Error checking MCP watcher %s for user %s", job_id, user_id,
                )

    async def _check_task_nagging(self) -> None:
        """Due App-style nagging + advance pre-reminders for important tasks.

        Pass 1: fire any pre_reminders entries whose timestamp is now <= now.
                Heads-up notifications, no escalation, no inline keyboard.
        Pass 2: existing reminder_at + nag escalation (15m / 30m / 60m / 60m,
                capped at 5). Sends Telegram push with inline [Done] [Snooze]
                [Tomorrow] buttons.

        Atomic claim: every nag fire goes through a conditional UPDATE
        that bumps ``nag_count`` and stamps ``nag_fired_at`` in the same
        statement. A heartbeat crash between the SELECT and the UPDATE
        can no longer cause a double-fire — the WHERE clause anchors on
        the exact ``nag_count`` we read, so a concurrent tick that
        already fired the same nag finds rowcount=0 and skips. Stale
        claims older than 5 minutes are auto-released so a hard crash
        doesn't permanently block a task from nagging again.
        """
        from datetime import timedelta

        if not self._telegram_push:
            return

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        # Stale-claim window: a nag whose ``nag_fired_at`` is older than
        # this is treated as abandoned (process crashed before delivering)
        # and re-claimable. 5min is comfortably longer than any expected
        # Telegram push round-trip.
        stale_threshold = (now - timedelta(minutes=5)).isoformat()

        # ── Pass 1: advance pre-reminders ──────────────────────────────────
        try:
            await self._fire_due_pre_reminders(now, now_iso)
        except Exception:
            logger.exception("pre-reminder pass failed")

        # Find all users who have tasks with due reminders
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT DISTINCT user_id FROM tasks "
                "WHERE status IN ('todo', 'in_progress') "
                "AND reminder_at IS NOT NULL AND reminder_at <= ?",
                (now_iso,),
            )
            user_ids = [r[0] for r in await cursor.fetchall()]

        for user_id in user_ids:
            key = await get_user_dek(self._config, user_id)

            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT id, title, reminder_at, nag_count, nag_fired_at "
                    "FROM tasks "
                    "WHERE user_id = ? AND status IN ('todo', 'in_progress') "
                    "AND reminder_at IS NOT NULL AND reminder_at <= ? "
                    "AND nag_count < 5",
                    (user_id, now_iso),
                )
                rows = await cursor.fetchall()

            for task_id, enc_title, reminder_at, nag_count, nag_fired_at in rows:
                # Calculate if this nag is due based on escalation
                # Intervals: 0=immediate, 1=+15min, 2=+30min, 3+=+1hr
                intervals = [0, 15, 30, 60, 60]
                interval_min = intervals[min(nag_count, len(intervals) - 1)]

                if nag_count > 0:
                    try:
                        remind_dt = datetime.fromisoformat(reminder_at)
                        if remind_dt.tzinfo is None:
                            remind_dt = remind_dt.replace(tzinfo=timezone.utc)
                        nag_due = remind_dt + timedelta(minutes=interval_min)
                        if now < nag_due:
                            continue  # Not time for this nag yet
                    except (ValueError, TypeError):
                        logger.debug("Failed to parse reminder datetime for nag check, skipping", exc_info=True)
                        continue

                # ── Atomic claim ──────────────────────────────────────────
                # Bump nag_count + stamp nag_fired_at + push reminder_at
                # forward (so the next escalation interval is computed
                # from "now") in a single UPDATE. The WHERE clause
                # anchors on the exact ``nag_count`` we read; a concurrent
                # tick that already fired the same nag finds rowcount=0
                # and we skip the push. Stale claims (>5min) get
                # re-claimed automatically so a crash can't permanently
                # block a task.
                async with db_session(self._config) as db:
                    claim_result = await db.execute(
                        "UPDATE tasks SET nag_count = ?, reminder_at = ?, "
                        "nag_fired_at = ? "
                        "WHERE id = ? AND user_id = ? "
                        "AND status IN ('todo', 'in_progress') "
                        "AND nag_count = ? "
                        "AND (nag_fired_at IS NULL OR nag_fired_at <= ?)",
                        (
                            nag_count + 1, now_iso, now_iso,
                            task_id, user_id,
                            nag_count, stale_threshold,
                        ),
                    )
                    await db.commit()

                if claim_result.rowcount == 0:
                    # Either another tick is already firing this nag, the
                    # task was completed/cancelled in the gap, or our read
                    # was stale. No double-send either way.
                    logger.debug(
                        "Skipping task %s nag #%d — claim lost",
                        task_id, nag_count + 1,
                    )
                    continue

                # Decrypt title and get task details
                try:
                    from lazyclaw.crypto.encryption import is_encrypted
                    title = (
                        decrypt(enc_title, key) if is_encrypted(enc_title)
                        else enc_title
                    )
                except Exception:
                    logger.warning("Failed to decrypt task title for reminder, using placeholder", exc_info=True)
                    title = "Task reminder"

                # Get full task for category/priority
                _category = ""
                _priority = ""
                try:
                    async with db_session(self._config) as db:
                        cursor = await db.execute(
                            "SELECT category, priority FROM tasks WHERE id = ?",
                            (task_id,),
                        )
                        _row = await cursor.fetchone()
                        if _row:
                            _enc_cat, _priority = _row
                            if _enc_cat:
                                _category = (
                                    decrypt(_enc_cat, key)
                                    if is_encrypted(_enc_cat) else _enc_cat
                                )
                except Exception:
                    logger.warning("Failed to load task category/priority for reminder notification", exc_info=True)

                # Format wall-clock time using the user's tz, not the server's.
                # Falls back to the server local clock if settings lookup fails.
                _local_time = ""
                try:
                    from lazyclaw.tasks.timezone import get_user_tz
                    user_tz = await get_user_tz(self._config, user_id)
                    _local_now = now.astimezone(user_tz)
                    _local_time = _local_now.strftime("%H:%M")
                except Exception:
                    logger.warning("Failed to format local time for reminder notification", exc_info=True)

                # Build notification text
                _pri_icon = {"urgent": "\U0001f534", "high": "\U0001f7e0", "medium": "", "low": "\U0001f7e2"}.get(_priority, "")
                _cat_tag = f" [{_category}]" if _category else ""
                _time_tag = f" \u23f0 {_local_time}" if _local_time else ""
                nag_label = f"\n\U0001f50a Reminder #{nag_count + 1}" if nag_count > 0 else ""

                msg_text = f"\U0001f514 {_pri_icon}{title}{_cat_tag}{_time_tag}{nag_label}"

                # Build inline keyboard
                try:
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "\u2705 Done", callback_data=f"task:done:{task_id}"
                        ),
                        InlineKeyboardButton(
                            "\u23f0 1h", callback_data=f"task:snooze:{task_id}"
                        ),
                        InlineKeyboardButton(
                            "\U0001f4c5 Tomorrow", callback_data=f"task:tomorrow:{task_id}"
                        ),
                        InlineKeyboardButton(
                            "\u270f\ufe0f Edit", callback_data=f"task:edit:{task_id}"
                        ),
                    ]])

                    await self._telegram_push(msg_text, reply_markup=keyboard)
                except TypeError:
                    logger.debug("Telegram keyboard not supported, sending plain text", exc_info=True)
                    await self._telegram_push(msg_text)
                except ImportError:
                    logger.debug("Telegram library not available for keyboard, sending plain text")
                    await self._telegram_push(msg_text)

                logger.debug(
                    "Task nag #%d for %s: %s", nag_count + 1, task_id, title,
                )

    async def _fire_due_pre_reminders(self, now, now_iso) -> None:
        """Fire any pending advance reminders whose timestamp has arrived.

        Each fired timestamp is removed from the task's ``pre_reminders``
        JSON array so it never fires twice. No inline keyboard — these are
        heads-up notifications; the at-time reminder (with Done/Snooze/
        Tomorrow buttons) fires separately when ``reminder_at`` lands.
        """
        import html as _html
        import json
        from datetime import timedelta

        from lazyclaw.crypto.encryption import is_encrypted
        from lazyclaw.tasks.store import update_task

        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, user_id, title, pre_reminders, reminder_at, priority "
                "FROM tasks "
                "WHERE status IN ('todo', 'in_progress') "
                "AND pre_reminders IS NOT NULL AND pre_reminders != '[]'",
            )
            rows = await cursor.fetchall()

        for row in rows:
            task_id, user_id, enc_title, raw_pre, reminder_at, priority = row
            try:
                pending = json.loads(raw_pre or "[]")
            except (json.JSONDecodeError, TypeError):
                logger.debug("bad pre_reminders JSON on task %s, skipping", task_id)
                continue
            due = [t for t in pending if t and t <= now_iso]
            if not due:
                continue

            try:
                key = await get_user_dek(self._config, user_id)
                title = (
                    decrypt(enc_title, key)
                    if enc_title and is_encrypted(enc_title)
                    else enc_title or "Task reminder"
                )
            except Exception:
                logger.warning(
                    "Failed to decrypt title for pre-reminder, using placeholder",
                    exc_info=True,
                )
                title = "Task reminder"

            for fired_iso in due:
                lead = self._format_lead_time(fired_iso, reminder_at)
                pri_icon = {"urgent": "\U0001f534", "high": "\U0001f7e0"}.get(
                    priority or "", "",
                )
                msg = (
                    f"⏰ {pri_icon} <b>{_html.escape(title)}</b>\n"
                    f"<i>{_html.escape(lead)}</i>"
                )
                try:
                    await self._telegram_push(msg)
                except Exception as exc:
                    logger.warning("Pre-reminder push failed: %s", exc)

            remaining = [t for t in pending if t and t > now_iso]
            try:
                await update_task(
                    self._config, user_id, task_id,
                    pre_reminders=remaining,
                )
            except Exception:
                logger.warning(
                    "Failed to persist pruned pre_reminders for task %s",
                    task_id, exc_info=True,
                )

    @staticmethod
    def _format_lead_time(fired_iso: str, reminder_at: str | None) -> str:
        """Return a short label like 'in 2h', 'in 30m' for the lead-time."""
        from datetime import timedelta as _td

        if not reminder_at:
            return "soon"
        try:
            base = datetime.fromisoformat(reminder_at)
            fired = datetime.fromisoformat(fired_iso)
        except (ValueError, TypeError):
            return "soon"
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        if fired.tzinfo is None:
            fired = fired.replace(tzinfo=timezone.utc)
        delta = base - fired
        if delta <= _td(0):
            return "now"
        total_min = int(delta.total_seconds() // 60)
        if total_min >= 60 * 24:
            days = total_min // (60 * 24)
            return f"in {days}d"
        if total_min >= 60:
            hours = total_min // 60
            mins = total_min % 60
            return f"in {hours}h" if mins == 0 else f"in {hours}h{mins}m"
        return f"in {total_min}m"

    async def _fire_task_pulse(
        self,
        user_id: str,
        job_id: str,
        instruction: str,
        cron_expression: str,
    ) -> bool:
        """Send a check-in pulse for a task, render the template's
        questions + buttons, and bump run_count.

        Returns True when the pulse was fired (or intentionally skipped
        because the task is done) so the caller can ``continue`` and
        not enqueue the instruction to the brain. Returns False on
        infrastructure failure so the caller falls through.

        Format of ``instruction``: ``[PULSE:<task_id>:<template_id>]``.
        """
        from lazyclaw.heartbeat import orchestrator as _orchestrator
        from lazyclaw.tasks import progress_templates as _pt
        from lazyclaw.tasks.store import (
            append_progress_entry, get_task,
        )

        # Parse instruction — bail on any deformation.
        try:
            payload = instruction[len("[PULSE:"):].rstrip("]")
            task_id, template_id = payload.split(":", 1)
        except Exception:
            logger.debug("malformed PULSE instruction: %r", instruction)
            return False
        if not task_id or not template_id:
            return False

        try:
            task = await get_task(self._config, user_id, task_id)
        except Exception:
            logger.debug("pulse: get_task failed", exc_info=True)
            return False
        if not task:
            # Task deleted under us — auto-clean the job.
            try:
                await _orchestrator.delete_job(self._config, user_id, job_id)
            except Exception:
                logger.debug("pulse: cleanup of orphan job failed", exc_info=True)
            return True

        # If the task already completed, pause the pulse job and skip.
        if task.get("status") in {"done", "cancelled", "failed"}:
            try:
                await _orchestrator.pause_job(self._config, user_id, job_id)
            except Exception:
                logger.debug("pulse: pause-on-complete failed", exc_info=True)
            return True

        try:
            template = await _pt.get_template(
                self._config, user_id, template_id,
            )
        except Exception:
            logger.debug("pulse: get_template failed", exc_info=True)
            return False
        if not template:
            return False

        # Render the pulse message — just the questions + inline buttons.
        # No LLM. No "should I be helpful here?" — the user opted in.
        title = task.get("title") or "task"
        question_lines = [
            f"• {q.get('label')}" for q in (template.get("questions") or [])
            if q.get("label")
        ]
        msg_lines = [f"🟡 Still on \"{title}\"?"]
        if question_lines:
            msg_lines.extend(question_lines)
        msg_text = "\n".join(msg_lines)

        # Send Telegram message with inline keyboard from template buttons.
        if self._telegram_push:
            try:
                from telegram import (
                    InlineKeyboardButton, InlineKeyboardMarkup,
                )
                buttons = template.get("buttons") or []
                if buttons:
                    keyboard_row = [
                        InlineKeyboardButton(
                            (b.get("label") or "")[:30],
                            callback_data=f"{b['action']}:{task_id}",
                        )
                        for b in buttons if b.get("action")
                    ]
                    keyboard = InlineKeyboardMarkup([keyboard_row]) if keyboard_row else None
                    if keyboard is not None:
                        await self._telegram_push(msg_text, reply_markup=keyboard)
                    else:
                        await self._telegram_push(msg_text)
                else:
                    await self._telegram_push(msg_text)
            except (TypeError, ImportError):
                await self._telegram_push(msg_text)
            except Exception:
                logger.debug("pulse: telegram push failed", exc_info=True)
                return False

        # Record state — progress log entry, run_count bump,
        # last_pulse_fired_at on the task row.
        try:
            await append_progress_entry(
                self._config, user_id, task_id,
                kind="pulse_fired", text=None, source="pulse",
            )
        except Exception:
            logger.debug("pulse: append_progress_entry failed", exc_info=True)
        try:
            await _pt.bump_run_count(
                self._config, user_id, template_id, success=False,
            )
        except Exception:
            logger.debug("pulse: bump_run_count failed", exc_info=True)
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE tasks SET last_pulse_fired_at = ? "
                    "WHERE id = ? AND user_id = ?",
                    (now_iso, task_id, user_id),
                )
                await db.commit()
        except Exception:
            logger.debug("pulse: last_pulse_fired_at stamp failed", exc_info=True)

        # Advance next_run + mark success on the orchestrator side so
        # the daemon doesn't re-fire on the next tick.
        try:
            from lazyclaw.heartbeat.cron import calculate_next_run
            next_run = calculate_next_run(cron_expression)
            await _orchestrator.mark_run(self._config, job_id, next_run)
            await _orchestrator.mark_run_outcome(
                self._config, user_id, job_id, "success",
            )
        except Exception:
            logger.debug("pulse: mark_run failed", exc_info=True)

        return True

    async def _sweep_stale_progress(self) -> None:
        """One-time soft nudge for in_progress tasks gone silent > 4h.

        Detection: task is in_progress AND ((latest progress entry OR
        last_attempted_at OR created_at) older than 4h) AND no pulse
        fired within the last 30min AND ``nudge_sent_at`` is null
        (one-time per silence window).

        Cleared by user response (any ``progress:*`` callback or NL
        progress entry) or task completion. Both clearing paths set
        ``nudge_sent_at = NULL`` via ``clear_nudge_sent`` in store.py.
        """
        from datetime import timedelta as _td

        if not self._telegram_push:
            return

        now = datetime.now(timezone.utc)
        stale_threshold = (now - _td(hours=4)).isoformat()
        recent_pulse = (now - _td(minutes=30)).isoformat()

        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, user_id, title, last_attempted_at, created_at, "
                "last_pulse_fired_at "
                "FROM tasks "
                "WHERE status = 'in_progress' "
                "AND nudge_sent_at IS NULL "
                "AND (last_pulse_fired_at IS NULL OR last_pulse_fired_at < ?) "
                "AND ((last_attempted_at IS NULL AND created_at < ?) "
                "OR last_attempted_at < ?) "
                "LIMIT 10",
                (recent_pulse, stale_threshold, stale_threshold),
            )
            rows = await cursor.fetchall()

        if not rows:
            return

        from lazyclaw.crypto.encryption import is_encrypted as _is_encrypted
        from lazyclaw.tasks.store import _decrypt_progress_log

        for task_id, user_id, enc_title, last_attempted, created_at, last_pulse in rows:
            try:
                key = await get_user_dek(self._config, user_id)
                # The latest progress_log entry beats last_attempted_at —
                # users may not be triggering attempts but talking about
                # the task constantly.
                async with db_session(self._config) as db:
                    cursor = await db.execute(
                        "SELECT progress_log FROM tasks WHERE id = ?",
                        (task_id,),
                    )
                    plog_row = await cursor.fetchone()
                last_entry_ts = None
                if plog_row and plog_row[0]:
                    log = _decrypt_progress_log(plog_row[0], key)
                    if log:
                        last_entry_ts = log[-1].get("ts")
                # Recompute staleness against the freshest signal.
                latest_signal = max(
                    [s for s in (last_entry_ts, last_attempted, last_pulse) if s],
                    default=created_at,
                )
                if latest_signal and latest_signal > stale_threshold:
                    continue

                title = (
                    decrypt(enc_title, key) if _is_encrypted(enc_title)
                    else enc_title
                ) if enc_title else "task"

                msg = (
                    f"🤔 Last update on \"{title}\" was over 4h ago — "
                    "still going?"
                )
                try:
                    from telegram import (
                        InlineKeyboardButton, InlineKeyboardMarkup,
                    )
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "✅ Done", callback_data=f"progress:done:{task_id}",
                        ),
                        InlineKeyboardButton(
                            "🟡 Working", callback_data=f"progress:working:{task_id}",
                        ),
                        InlineKeyboardButton(
                            "⏸️ Pause", callback_data=f"progress:paused:{task_id}",
                        ),
                    ]])
                    await self._telegram_push(msg, reply_markup=keyboard)
                except (TypeError, ImportError):
                    await self._telegram_push(msg)

                # Stamp nudge_sent_at so we don't loop on the next tick.
                async with db_session(self._config) as db:
                    await db.execute(
                        "UPDATE tasks SET nudge_sent_at = ? "
                        "WHERE id = ? AND user_id = ?",
                        (now.isoformat(), task_id, user_id),
                    )
                    await db.commit()
            except Exception:
                logger.debug(
                    "stale-nudge fire failed for task %s", task_id, exc_info=True,
                )

    async def _sweep_eod_summary(self) -> None:
        """End-of-day progress summary at 20:00 user-tz.

        Read-only: counts in_progress tasks per user, summarizes their
        day's progress entries, pushes one Telegram message. Gated by
        ``users.settings.general.eod_summary`` (default True) and a
        per-user once-per-day in-memory marker so a multi-tick window
        only fires once.
        """
        from lazyclaw.settings.general import get_general_settings
        from lazyclaw.tasks.store import _decrypt_progress_log
        from lazyclaw.tasks.timezone import get_user_tz

        if not self._telegram_push:
            return

        try:
            async with db_session(self._config) as db:
                cursor = await db.execute("SELECT id FROM users")
                users = [r[0] for r in await cursor.fetchall()]
        except Exception:
            return

        for user_id in users:
            try:
                tz = await get_user_tz(self._config, user_id)
                local_now = datetime.now(tz)
                today = local_now.date().isoformat()

                # 20:00–22:00 firing window — fire once per day even if
                # the daemon was off at exactly 20:00.
                if not (20 <= local_now.hour < 22):
                    continue
                if self._last_eod_summary_iso.get(user_id) == today:
                    continue

                settings = await get_general_settings(self._config, user_id)
                if not settings.get("eod_summary", True):
                    self._last_eod_summary_iso[user_id] = today
                    continue

                key = await get_user_dek(self._config, user_id)

                async with db_session(self._config) as db:
                    cursor = await db.execute(
                        "SELECT id, title, progress_log, created_at "
                        "FROM tasks "
                        "WHERE user_id = ? AND status = 'in_progress' "
                        "ORDER BY created_at DESC LIMIT 20",
                        (user_id,),
                    )
                    rows = await cursor.fetchall()

                if not rows:
                    self._last_eod_summary_iso[user_id] = today
                    continue

                from lazyclaw.crypto.encryption import (
                    is_encrypted as _is_encrypted,
                )

                lines = [f"📋 Today's progress ({today}):"]
                for row in rows:
                    task_id, enc_title, plog, created_at = row
                    try:
                        title = (
                            decrypt(enc_title, key)
                            if _is_encrypted(enc_title) else enc_title
                        ) if enc_title else "(untitled)"
                    except Exception:
                        title = "(undecryptable)"
                    log = _decrypt_progress_log(plog, key)
                    today_entries = [
                        e for e in log
                        if (e.get("ts") or "").startswith(today)
                    ]
                    if today_entries:
                        last = today_entries[-1]
                        lines.append(
                            f"• {title} — {len(today_entries)} entries; "
                            f"last: {last.get('kind')} "
                            f"({(last.get('text') or '')[:60]})"
                        )
                    else:
                        lines.append(f"• {title} — 0 entries today")

                try:
                    await self._telegram_push("\n".join(lines))
                except Exception:
                    logger.debug("EOD summary push failed", exc_info=True)
                self._last_eod_summary_iso[user_id] = today
            except Exception:
                logger.debug(
                    "EOD summary failed for user %s", user_id, exc_info=True,
                )

    async def _retry_lazybrain_mirrors(self) -> None:
        """Retry tasks whose initial LazyBrain mirror save silently failed.

        The create_task path is fire-and-forget on mirror failure so a
        flaky LazyBrain or Ollama hiccup doesn't break task creation.
        Without this sweep those tasks would forever lack a brain note —
        the user thinks "the system lost my task" because they can't
        find it in the PKM.

        Strategy: every tick, scan tasks created in the last 24h with
        ``lazybrain_note_id IS NULL`` and re-run the mirror via the
        store's status mirror (which already heals missing notes). 24h
        is the cutoff so an old, intentionally orphan task (deleted note)
        doesn't get auto-recreated forever.

        Bounded at 20 retries per tick to keep the sweep cheap.
        """
        from datetime import timedelta as _td

        from lazyclaw.tasks.store import _mirror_status_to_lazybrain, get_task

        cutoff = (datetime.now(timezone.utc) - _td(hours=24)).isoformat()

        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, user_id, status FROM tasks "
                "WHERE lazybrain_note_id IS NULL "
                "AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 20",
                (cutoff,),
            )
            rows = await cursor.fetchall()

        if not rows:
            return

        healed = 0
        for task_id, user_id, status in rows:
            try:
                task = await get_task(self._config, user_id, task_id)
                if not task:
                    continue
                # _mirror_status_to_lazybrain auto-heals when the note id
                # is missing (creates a fresh mirror with the current
                # status baked in). On success it stamps the note id back
                # onto the task row, so the next sweep won't pick this up.
                await _mirror_status_to_lazybrain(
                    self._config, user_id, task, status or "todo",
                )
                refreshed = await get_task(self._config, user_id, task_id)
                if refreshed and refreshed.get("lazybrain_note_id"):
                    healed += 1
            except Exception:
                logger.debug(
                    "mirror-retry: task %s remained orphan", task_id, exc_info=True,
                )

        if healed:
            logger.info(
                "LazyBrain mirror retry healed %d/%d orphan tasks", healed, len(rows),
            )

    async def _seed_today_journals(self) -> None:
        """Ensure each registered user has a journal note for today.

        Idempotent: keyed off ``self._last_journal_seed_iso[user_id]`` so we
        only call into LazyBrain once per user per day. The marker resets on
        restart, but ``ensure_today_journal`` itself looks up by tag before
        inserting, so a re-seed just re-finds the existing note — no dupes.
        """
        from lazyclaw.lazybrain import journal as _journal
        from lazyclaw.lazybrain import timezone_util as _tzu

        try:
            async with db_session(self._config) as db:
                cursor = await db.execute("SELECT id FROM users")
                users = [r[0] for r in await cursor.fetchall()]
        except Exception:
            logger.warning("Could not list users for journal seed", exc_info=True)
            return

        for user_id in users:
            today = _tzu.today_iso(user_id)
            if self._last_journal_seed_iso.get(user_id) == today:
                continue
            try:
                await _journal.ensure_today_journal(self._config, user_id)
                self._last_journal_seed_iso[user_id] = today
                logger.debug(
                    "seeded today's journal for user %s (%s)", user_id, today,
                )
            except Exception:
                logger.warning(
                    "Could not seed today's journal for user %s",
                    user_id, exc_info=True,
                )

    async def _sweep_topic_rollups(self) -> None:
        """Run the LazyBrain topic-rollup sweep at most once per user per day.

        The sweep itself enforces a longer per-topic cooldown — this is just
        an outer guard so the brain LLM isn't queried multiple times per
        heartbeat tick.
        """
        from lazyclaw.heartbeat import topic_rollup_job
        from lazyclaw.lazybrain import timezone_util as _tzu

        try:
            async with db_session(self._config) as db:
                cursor = await db.execute("SELECT id FROM users")
                users = [r[0] for r in await cursor.fetchall()]
        except Exception:
            logger.debug("topic rollup sweep: list users failed", exc_info=True)
            return

        for user_id in users:
            today = _tzu.today_iso(user_id)
            if self._last_topic_rollup_iso.get(user_id) == today:
                continue
            try:
                summary = await topic_rollup_job.run_topic_rollup_sweep(
                    self._config, user_id,
                )
                self._last_topic_rollup_iso[user_id] = today
                if summary.get("processed"):
                    logger.info(
                        "topic rollup sweep: user=%s processed=%d skipped=%d",
                        user_id, len(summary["processed"]),
                        len(summary.get("skipped", [])),
                    )
            except Exception:
                logger.warning(
                    "topic rollup sweep failed for user %s",
                    user_id, exc_info=True,
                )

    async def _reindex_dirty_embeddings(self) -> None:
        """Drain notes whose ``embedding_dirty=1`` flag is still set.

        Most ticks find zero dirty notes — the SELECT against the indexed
        column costs microseconds. The flag is set when ``save_note`` /
        ``update_note`` writes content but the synchronous embedding upsert
        fails (Ollama down). Without this pass, those notes would be
        invisible to ``semantic_search`` until the user manually
        triggered ``lazybrain_reindex_embeddings``.
        """
        try:
            from lazyclaw.lazybrain import embeddings as _lb_emb
        except Exception:
            return
        try:
            async with db_session(self._config) as db:
                cursor = await db.execute("SELECT id FROM users")
                users = [r[0] for r in await cursor.fetchall()]
        except Exception:
            logger.debug("reindex pass: list users failed", exc_info=True)
            return
        for user_id in users:
            try:
                summary = await _lb_emb.reindex_dirty_batch(
                    self._config, user_id, limit=50,
                )
                if summary.get("indexed"):
                    logger.info(
                        "embedding reindex: user=%s indexed=%d skipped=%d",
                        user_id, summary["indexed"], summary["skipped"],
                    )
            except Exception:
                logger.debug(
                    "embedding reindex failed for user %s",
                    user_id, exc_info=True,
                )

    async def _ensure_persistent_browser(self) -> None:
        """Manage browser lifecycle based on persistence mode.

        - "on"   → restart if crashed
        - "auto" → restart if crashed AND recently active, kill if idle
        - "off"  → do nothing (on-demand only)
        """
        try:
            import asyncio

            from lazyclaw.browser.browser_settings import (
                browser_idle_seconds,
                get_browser_settings,
            )
            from lazyclaw.browser.cdp import find_chrome_cdp

            async with db_session(self._config) as db:
                cursor = await db.execute("SELECT id FROM users LIMIT 10")
                users = [r[0] for r in await cursor.fetchall()]

            port = getattr(self._config, "cdp_port", 9222)
            browser_alive = bool(await find_chrome_cdp(port))

            for user_id in users:
                settings = await get_browser_settings(self._config, user_id)
                mode = settings.get("persistent", "auto")

                if mode == "off":
                    continue

                if mode == "on":
                    # Always keep alive — restart if dead
                    if not browser_alive:
                        await self._launch_browser(user_id, port)
                    return

                if mode == "auto":
                    idle = browser_idle_seconds()
                    timeout = settings.get("idle_timeout", 3600)  # 1 hour default

                    # Check if there are active watchers — keep alive
                    has_watchers = False
                    async with db_session(self._config) as db:
                        cursor = await db.execute(
                            "SELECT COUNT(*) FROM agent_jobs "
                            "WHERE user_id = ? AND job_type = 'watcher' "
                            "AND status = 'active'",
                            (user_id,),
                        )
                        row = await cursor.fetchone()
                        has_watchers = row and row[0] > 0

                    # Check if there are running background tasks — keep alive
                    has_bg_tasks = False
                    async with db_session(self._config) as db:
                        cursor = await db.execute(
                            "SELECT COUNT(*) FROM background_tasks "
                            "WHERE user_id = ? AND status = 'running'",
                            (user_id,),
                        )
                        row = await cursor.fetchone()
                        has_bg_tasks = row and row[0] > 0

                    # Subagents (lane='subagent') and specialists
                    # (lane='specialist') don't write to background_tasks
                    # — they're tracked in TeamLead's in-memory active set.
                    # Without this check the reaper kills Chrome mid-scrape.
                    has_team_work = False
                    if self._team_lead is not None:
                        try:
                            for t in self._team_lead.active_tasks:
                                if (
                                    t.lane in ("subagent", "specialist")
                                    and self._team_lead._task_users.get(
                                        t.task_id, ""
                                    ) == user_id
                                ):
                                    has_team_work = True
                                    break
                        except Exception:
                            logger.debug(
                                "team_lead probe failed in browser reaper",
                                exc_info=True,
                            )

                    if (
                        browser_alive
                        and idle != float("inf")
                        and idle > timeout
                        and not has_watchers
                        and not has_bg_tasks
                        and not has_team_work
                    ):
                        # Idle too long and no watchers — kill it
                        logger.info(
                            "Auto-closing idle browser (%.0fs idle, %ds timeout)",
                            idle, timeout,
                        )
                        try:
                            matches = _scan_proc_cmdlines(f"remote-debugging-port={port}")
                            for pid, _cmdline in matches:
                                try:
                                    os.kill(pid, signal.SIGTERM)
                                except ProcessLookupError:
                                    # Intentional: process may have exited before SIGTERM
                                    pass
                                except PermissionError:
                                    # Host Brave bridge is running as a
                                    # different uid (typical when the daemon
                                    # is in a Docker container and the browser
                                    # is on the host). Stop trying to kill it —
                                    # log once at info and let the user manage
                                    # it.
                                    logger.info(
                                        "Idle-browser PID %s on port %d is "
                                        "owned by another uid (likely host "
                                        "Brave bridge) — leaving it alone",
                                        pid, port,
                                    )
                        except Exception as exc:
                            logger.warning(
                                "Idle-browser reap on port %d failed: %s: %s",
                                port, type(exc).__name__, exc,
                            )
                    elif not browser_alive and (
                        idle < timeout or has_watchers or has_bg_tasks or has_team_work
                    ):
                        # Browser died but still needed — restart
                        await self._launch_browser(user_id, port)
                    return

        except Exception:
            logger.debug("Persistent browser check failed", exc_info=True)

    async def _launch_browser(self, user_id: str, port: int) -> None:
        """Launch headless browser for a user."""
        from lazyclaw.browser.cdp_backend import CDPBackend
        from lazyclaw.browser.profile_resolver import resolve_profile_dir

        logger.info("Launching persistent browser for user %s", user_id)
        profile_dir = str(resolve_profile_dir(self._config, user_id))
        backend = CDPBackend(port=port, profile_dir=profile_dir)
        ws_url = await backend._auto_launch_chrome()
        if ws_url:
            logger.info("Persistent browser running (CDP port %d)", port)
        else:
            logger.warning("Failed to launch persistent browser")

    async def _load_heartbeat_md(self) -> str:
        """Load the HEARTBEAT.md personality file content."""
        heartbeat_path = (
            Path(__file__).resolve().parent.parent.parent / "personality" / "HEARTBEAT.md"
        )
        if not heartbeat_path.exists():
            return ""

        return heartbeat_path.read_text(encoding="utf-8")
