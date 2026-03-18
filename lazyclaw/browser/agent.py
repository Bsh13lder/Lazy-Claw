"""Browser Agent Manager — AI-powered autonomous browser automation.

Extracted from LazyTasker. Uses browser-use library to drive Chromium
via LLM. Supports human-in-the-loop, persistent sessions, site memory,
takeover mode, and instruction injection.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any, Awaitable, Callable

from lazyclaw.browser.manager import BrowserSessionPool
from lazyclaw.browser.site_memory import format_memories_for_context, recall, remember
from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, decrypt_field, encrypt_field
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
MAX_GLOBAL_TASKS = 5
MAX_PER_USER_TASKS = 2
HELP_TIMEOUT_SECONDS = 600  # 10 minutes
DEFAULT_MAX_STEPS = 20
HUMANIZE_DELAY = 0.5  # seconds between steps


def _make_browser_llm_class():
    """Lazy-create ChatOpenAI subclass that allows browser-use to set extra attrs."""
    try:
        from langchain_openai import ChatOpenAI

        class _BrowserChatOpenAI(ChatOpenAI):
            """ChatOpenAI with extra='allow' for browser-use compatibility."""
            model_config = {"extra": "allow"}
            provider: str = "openai"

        return _BrowserChatOpenAI
    except ImportError:
        return None


_BrowserChatOpenAI = _make_browser_llm_class()


class BrowserAgentManager:
    """Manages browser automation tasks with human-in-the-loop support."""

    def __init__(
        self,
        config: Config,
        session_pool: BrowserSessionPool,
        on_needs_help: Callable[..., Awaitable[None]] | None = None,
        on_task_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._session_pool = session_pool
        self._on_needs_help = on_needs_help
        self._on_task_complete = on_task_complete

        # In-memory tracking
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._running_task_users: dict[str, str] = {}
        self._task_agents: dict[str, Any] = {}
        self._latest_screenshots: dict[str, bytes] = {}

        # Help synchronization
        self._help_events: dict[str, asyncio.Event] = {}
        self._help_responses: dict[str, str] = {}

        # Takeover
        self._takeover_active: dict[str, bool] = {}
        self._takeover_events: dict[str, asyncio.Event] = {}

        # Instruction injection
        self._pending_instructions: dict[str, list[str]] = {}

    # ── Task CRUD ────────────────────────────────────────────────────────

    async def create_task(
        self,
        user_id: str,
        instruction: str,
        max_steps: int = 0,
    ) -> str:
        """Create a browser task. Checks concurrency limits.

        Returns task ID.
        """
        if len(self._running_tasks) >= MAX_GLOBAL_TASKS:
            raise RuntimeError("Too many concurrent browser tasks globally")

        user_task_count = sum(
            1 for uid in self._running_task_users.values() if uid == user_id
        )
        if user_task_count >= MAX_PER_USER_TASKS:
            raise RuntimeError("Too many concurrent browser tasks for this user")

        task_id = str(uuid.uuid4())
        key = derive_server_key(self._config.server_secret, user_id)
        enc_instruction = encrypt_field(instruction, key)

        async with db_session(self._config) as db:
            await db.execute(
                "INSERT INTO browser_tasks (id, user_id, instruction, status, max_steps) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (task_id, user_id, enc_instruction, max_steps or DEFAULT_MAX_STEPS),
            )
            await db.commit()

        logger.info("Created browser task %s for user %s", task_id, user_id)
        return task_id

    async def start_task(self, task_id: str) -> None:
        """Start a pending browser task as a background asyncio.Task."""
        async with db_session(self._config) as db:
            row = await db.execute_fetchall(
                "SELECT user_id, instruction, status, max_steps FROM browser_tasks WHERE id = ?",
                (task_id,),
            )
            if not row:
                raise ValueError(f"Task {task_id} not found")
            task_data = row[0]

        if task_data["status"] != "pending":
            raise ValueError(f"Task {task_id} is not pending (status={task_data['status']})")

        user_id = task_data["user_id"]
        key = derive_server_key(self._config.server_secret, user_id)
        instruction = decrypt_field(task_data["instruction"], key)
        max_steps = task_data["max_steps"] or DEFAULT_MAX_STEPS

        async with db_session(self._config) as db:
            await db.execute(
                "UPDATE browser_tasks SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()

        bg_task = asyncio.create_task(
            self._run_agent(task_id, instruction, user_id, max_steps)
        )
        self._running_tasks[task_id] = bg_task
        self._running_task_users[task_id] = user_id

    async def _run_agent(
        self,
        task_id: str,
        instruction: str,
        user_id: str,
        max_steps: int,
        step_offset: int = 0,
    ) -> None:
        """Core agent execution — creates browser-use Agent and runs it."""
        try:
            from browser_use import Agent, Controller
        except ImportError as exc:
            logger.error("browser-use not installed: %s", exc)
            await self._fail_task(task_id, f"Missing dependency: {exc}")
            return

        if _BrowserChatOpenAI is None:
            await self._fail_task(task_id, "Missing dependency: langchain-openai")
            return

        key = derive_server_key(self._config.server_secret, user_id)
        agent_ref: list[Any] = [None]

        # ── Controller with ask_human ────────────────────────────────
        controller = Controller()

        @controller.action(
            "Ask the human user for help when stuck, need credentials, or encounter CAPTCHA"
        )
        async def ask_human(question: str) -> str:
            # Save screenshot
            screenshot_bytes = self._latest_screenshots.get(task_id)

            # Update task status
            enc_question = encrypt_field(question, key)
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE browser_tasks SET status = 'needs_help', "
                    "help_question = ?, updated_at = datetime('now') WHERE id = ?",
                    (enc_question, task_id),
                )
                await db.commit()

            # Notify via callback
            if self._on_needs_help:
                await self._on_needs_help(task_id, user_id, question, screenshot_bytes)

            # Wait for response
            event = asyncio.Event()
            self._help_events[task_id] = event
            try:
                await asyncio.wait_for(event.wait(), timeout=HELP_TIMEOUT_SECONDS)
                response = self._help_responses.pop(task_id, "No response provided")
            except asyncio.TimeoutError:
                response = "User did not respond within 10 minutes. Try to continue on your own."

            # Resume task
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE browser_tasks SET status = 'running', "
                    "help_question = NULL, updated_at = datetime('now') WHERE id = ?",
                    (task_id,),
                )
                await db.commit()

            return response

        # ── Step callback ────────────────────────────────────────────
        async def on_step(state: Any, model_output: Any, step_number: int) -> None:
            actual_step = step_number + step_offset

            # Extract action and thinking
            action_text = ""
            thinking_text = ""
            try:
                if model_output and hasattr(model_output, "current_state"):
                    cs = model_output.current_state
                    action_text = getattr(cs, "next_goal", "") or ""
                    thinking_text = getattr(cs, "evaluation_previous_goal", "") or ""
            except Exception:
                pass

            # Extract URL
            current_url = ""
            try:
                current_url = getattr(state, "url", "") or ""
            except Exception:
                pass

            # Cache screenshot
            try:
                if state and hasattr(state, "screenshot"):
                    raw = state.screenshot
                    if isinstance(raw, str):
                        raw = base64.b64decode(raw)
                    self._latest_screenshots[task_id] = raw
            except Exception:
                pass

            # Save log entry
            log_id = str(uuid.uuid4())
            async with db_session(self._config) as db:
                await db.execute(
                    "INSERT INTO browser_task_logs (id, task_id, step_number, action, thinking, url) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        log_id,
                        task_id,
                        actual_step,
                        encrypt_field(action_text, key),
                        encrypt_field(thinking_text, key),
                        current_url,
                    ),
                )
                await db.execute(
                    "UPDATE browser_tasks SET steps_completed = ?, updated_at = datetime('now') WHERE id = ?",
                    (actual_step, task_id),
                )
                await db.commit()

            # Human-like pacing
            await asyncio.sleep(HUMANIZE_DELAY)

            # Inject pending instructions
            pending = self._pending_instructions.pop(task_id, [])
            if pending and agent_ref[0]:
                try:
                    from langchain_core.messages import HumanMessage

                    agent = agent_ref[0]
                    for instr in pending:
                        msg = HumanMessage(content=f"[User instruction]: {instr}")
                        agent.message_manager.history.add_message(msg)
                        logger.info("Injected instruction into task %s", task_id)
                except Exception as exc:
                    logger.warning("Failed to inject instruction: %s", exc)

            # Takeover handling
            if self._takeover_active.get(task_id):
                event = asyncio.Event()
                self._takeover_events[task_id] = event
                try:
                    await asyncio.wait_for(event.wait(), timeout=HELP_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    self._takeover_active[task_id] = False

                async with db_session(self._config) as db:
                    await db.execute(
                        "UPDATE browser_tasks SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                        (task_id,),
                    )
                    await db.commit()

            # Learn from successful steps
            if action_text and current_url:
                try:
                    await remember(
                        self._config,
                        user_id,
                        current_url,
                        "navigation",
                        action_text[:100],
                        {"action": action_text, "url": current_url},
                    )
                except Exception:
                    pass

        # ── Build enriched instruction ───────────────────────────────
        enriched = instruction
        try:
            site_memories = await recall(self._config, user_id, instruction)
            memory_context = format_memories_for_context(site_memories)
            if memory_context:
                enriched = f"{instruction}\n\n{memory_context}"
        except Exception:
            pass

        # ── Create LLM ──────────────────────────────────────────────
        browser_model = self._config.default_model
        api_key = self._config.openai_api_key or ""

        # Try to resolve from credential vault
        if not api_key:
            from lazyclaw.crypto.vault import get_credential

            api_key = await get_credential(self._config, user_id, "openai_api_key") or ""

        if not api_key:
            await self._fail_task(task_id, "No OpenAI API key configured for browser agent")
            return

        try:
            llm = _BrowserChatOpenAI(model=browser_model, api_key=api_key)
        except Exception as exc:
            await self._fail_task(task_id, f"Failed to create LLM: {exc}")
            return

        # ── Get browser session ──────────────────────────────────────
        session = await self._session_pool.get_session(user_id)
        browser, _ = await session.get_browser()

        # ── Create and run agent ─────────────────────────────────────
        system_rules = (
            "You are a browser automation agent. Be direct and efficient.\n"
            "- If you need credentials or encounter CAPTCHA, use ask_human.\n"
            "- If stuck after 3 attempts, use ask_human for guidance.\n"
            "- Navigate carefully, wait for pages to load.\n"
            "- Report your final result clearly."
        )

        try:
            agent = Agent(
                task=enriched,
                llm=llm,
                browser=browser,
                controller=controller,
                max_failures=3,
                max_actions_per_step=3,
                use_vision=True,
                register_new_step_callback=on_step,
                extend_system_message=system_rules,
            )
            agent_ref[0] = agent
            self._task_agents[task_id] = agent

            history = await agent.run(max_steps=max_steps)
            result = history.final_result() or "Task completed successfully."

            # Save result
            enc_result = encrypt_field(result, key)
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE browser_tasks SET status = 'completed', result = ?, "
                    "completed_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                    (enc_result, task_id),
                )
                await db.commit()

            logger.info("Browser task %s completed", task_id)

            if self._on_task_complete:
                await self._on_task_complete(task_id, user_id, result)

        except asyncio.CancelledError:
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE browser_tasks SET status = 'cancelled', updated_at = datetime('now') WHERE id = ?",
                    (task_id,),
                )
                await db.commit()
            logger.info("Browser task %s cancelled", task_id)

        except Exception as exc:
            logger.error("Browser task %s failed: %s", task_id, exc)
            await self._fail_task(task_id, str(exc))

        finally:
            self._running_tasks.pop(task_id, None)
            self._running_task_users.pop(task_id, None)
            self._task_agents.pop(task_id, None)
            self._latest_screenshots.pop(task_id, None)
            self._help_events.pop(task_id, None)
            self._help_responses.pop(task_id, None)
            self._takeover_active.pop(task_id, None)
            self._takeover_events.pop(task_id, None)
            self._pending_instructions.pop(task_id, None)

    async def _fail_task(self, task_id: str, error: str) -> None:
        """Mark task as failed with error message."""
        async with db_session(self._config) as db:
            await db.execute(
                "UPDATE browser_tasks SET status = 'failed', error = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (error, task_id),
            )
            await db.commit()

    # ── Task lifecycle ───────────────────────────────────────────────

    async def cancel_task(self, task_id: str, user_id: str) -> None:
        """Cancel a running browser task."""
        bg_task = self._running_tasks.get(task_id)
        if not bg_task:
            raise ValueError(f"Task {task_id} is not running")

        if self._running_task_users.get(task_id) != user_id:
            raise ValueError("Task belongs to a different user")

        # Unblock help wait if active
        event = self._help_events.get(task_id)
        if event:
            self._help_responses[task_id] = "CANCELLED"
            event.set()

        bg_task.cancel()

    async def provide_help(self, task_id: str, user_id: str, response: str) -> None:
        """Provide a response to a task that needs help."""
        event = self._help_events.get(task_id)
        if event:
            self._help_responses[task_id] = response
            event.set()
        elif task_id in self._running_tasks:
            # Task is running but not waiting for help — inject as instruction
            await self.inject_instruction(task_id, user_id, response)
        else:
            raise ValueError(f"Task {task_id} is not running or waiting for help")

    async def inject_instruction(self, task_id: str, user_id: str, instruction: str) -> None:
        """Queue an instruction for injection at the next step."""
        if task_id not in self._running_tasks:
            raise ValueError(f"Task {task_id} is not running")
        if self._running_task_users.get(task_id) != user_id:
            raise ValueError("Task belongs to a different user")

        self._pending_instructions.setdefault(task_id, []).append(instruction)

    async def continue_task(
        self, task_id: str, user_id: str, instruction: str
    ) -> None:
        """Continue a completed/failed task with new instruction."""
        async with db_session(self._config) as db:
            rows = await db.execute_fetchall(
                "SELECT status, result, steps_completed FROM browser_tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            )
            if not rows:
                raise ValueError(f"Task {task_id} not found")

        task = rows[0]
        if task["status"] not in ("completed", "failed", "cancelled"):
            raise ValueError(f"Cannot continue task with status '{task['status']}'")

        key = derive_server_key(self._config.server_secret, user_id)
        prev_result = decrypt_field(task["result"], key) or "No previous result"
        step_offset = task["steps_completed"] or 0

        context = (
            f"PREVIOUS RESULT:\n{prev_result}\n\n"
            f"NEW INSTRUCTION:\n{instruction}"
        )

        async with db_session(self._config) as db:
            await db.execute(
                "UPDATE browser_tasks SET status = 'running', instruction = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (encrypt_field(instruction, key), task_id),
            )
            await db.commit()

        bg_task = asyncio.create_task(
            self._run_agent(task_id, context, user_id, DEFAULT_MAX_STEPS, step_offset)
        )
        self._running_tasks[task_id] = bg_task
        self._running_task_users[task_id] = user_id

    # ── Takeover mode ────────────────────────────────────────────────

    async def request_takeover(self, task_id: str, user_id: str) -> None:
        """Request manual control of a running task."""
        if task_id not in self._running_tasks:
            raise ValueError(f"Task {task_id} is not running")
        if self._running_task_users.get(task_id) != user_id:
            raise ValueError("Task belongs to a different user")

        self._takeover_active[task_id] = True

        # Release help wait if active
        event = self._help_events.get(task_id)
        if event:
            self._help_responses[task_id] = "User took over manual control"
            event.set()

        async with db_session(self._config) as db:
            await db.execute(
                "UPDATE browser_tasks SET status = 'takeover', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()

    async def release_takeover(self, task_id: str, user_id: str) -> None:
        """Release manual control back to the agent."""
        if self._running_task_users.get(task_id) != user_id:
            raise ValueError("Task belongs to a different user")

        self._takeover_active[task_id] = False
        event = self._takeover_events.pop(task_id, None)
        if event:
            event.set()

    async def execute_user_action(
        self, task_id: str, user_id: str, action: dict
    ) -> dict:
        """Execute a user action during takeover mode.

        action: {"type": "click"|"type"|"scroll"|"key", "x": int, "y": int,
                 "text": str, "key": str, "delta_x": int, "delta_y": int}
        """
        if not self._takeover_active.get(task_id):
            raise ValueError("Takeover not active for this task")
        if self._running_task_users.get(task_id) != user_id:
            raise ValueError("Task belongs to a different user")

        agent = self._task_agents.get(task_id)
        if not agent:
            raise ValueError("No agent session available")

        try:
            page = await agent.browser_session.get_current_page()
            action_type = action.get("type", "")

            if action_type == "click":
                await page.mouse.click(action["x"], action["y"])
            elif action_type == "type":
                await page.keyboard.insert_text(action["text"])
            elif action_type == "scroll":
                await page.mouse.move(
                    action.get("x", 0), action.get("y", 0)
                )
                await page.mouse.wheel(
                    action.get("delta_x", 0), action.get("delta_y", -300)
                )
            elif action_type == "key":
                await page.keyboard.press(action["key"])
            else:
                return {"error": f"Unknown action type: {action_type}"}

            return {"status": "ok"}
        except Exception as exc:
            logger.error("User action failed: %s", exc)
            return {"error": str(exc)}

    # ── Queries ──────────────────────────────────────────────────────

    async def get_task(self, task_id: str, user_id: str) -> dict | None:
        """Get task details (decrypted)."""
        async with db_session(self._config) as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM browser_tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            )
            if not rows:
                return None

        key = derive_server_key(self._config.server_secret, user_id)
        row = dict(rows[0])
        row["instruction"] = decrypt_field(row.get("instruction"), key)
        row["result"] = decrypt_field(row.get("result"), key)
        row["help_question"] = decrypt_field(row.get("help_question"), key)
        return row

    async def list_tasks(self, user_id: str, limit: int = 50) -> list[dict]:
        """List user's browser tasks (decrypted)."""
        async with db_session(self._config) as db:
            rows = await db.execute_fetchall(
                "SELECT id, status, steps_completed, created_at, updated_at, completed_at "
                "FROM browser_tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
        return [dict(row) for row in rows]

    async def get_task_logs(
        self, task_id: str, user_id: str, after_id: str | None = None
    ) -> list[dict]:
        """Get step-by-step logs for a task (decrypted)."""
        # Verify ownership
        async with db_session(self._config) as db:
            owner = await db.execute_fetchall(
                "SELECT user_id FROM browser_tasks WHERE id = ?", (task_id,)
            )
            if not owner or owner[0]["user_id"] != user_id:
                return []

        key = derive_server_key(self._config.server_secret, user_id)

        async with db_session(self._config) as db:
            if after_id:
                rows = await db.execute_fetchall(
                    "SELECT * FROM browser_task_logs WHERE task_id = ? AND id > ? "
                    "ORDER BY step_number",
                    (task_id, after_id),
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT * FROM browser_task_logs WHERE task_id = ? ORDER BY step_number",
                    (task_id,),
                )

        result = []
        for row in rows:
            entry = dict(row)
            entry["action"] = decrypt_field(entry.get("action"), key)
            entry["thinking"] = decrypt_field(entry.get("thinking"), key)
            result.append(entry)
        return result

    def get_live_screenshot(self, task_id: str) -> bytes | None:
        """Get the latest cached screenshot for a running task."""
        return self._latest_screenshots.get(task_id)
