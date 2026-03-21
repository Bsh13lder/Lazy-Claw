"""Background task runner — parallel agent execution.

Each background task gets a fresh Agent instance and runs independently,
allowing the user to keep chatting while tasks execute.

Notifications push to Telegram and server dashboard via callbacks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session
from lazyclaw.runtime.callbacks import AgentEvent

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.llm.router import LLMRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Concurrency limits
MAX_GLOBAL_TASKS = 5
MAX_PER_USER_TASKS = 2
DEFAULT_TIMEOUT = 300  # 5 minutes


class TaskRunner:
    """Runs agent tasks in background, parallel to foreground chat.

    Usage:
        runner = TaskRunner(config, router, registry, eco_router)
        task_id = await runner.submit(user_id, "check bitcoin price", name="btc")
        # Returns immediately — task runs in background
        # User notified via callback when done
    """

    def __init__(
        self,
        config: Config,
        router: LLMRouter,
        registry: SkillRegistry,
        eco_router: EcoRouter,
        permission_checker=None,
    ) -> None:
        self._config = config
        self._router = router
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker

        # In-memory tracking (cleaned up on completion)
        self._running: dict[str, asyncio.Task] = {}
        self._task_users: dict[str, str] = {}
        self._task_names: dict[str, str] = {}
        self._task_starts: dict[str, float] = {}

    async def submit(
        self,
        user_id: str,
        instruction: str,
        name: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        callback: AgentCallback | None = None,
        on_complete=None,
    ) -> str:
        """Submit a task for background execution. Returns task_id immediately.

        Raises RuntimeError if concurrency limits exceeded.
        """
        # Validate limits
        if len(self._running) >= MAX_GLOBAL_TASKS:
            raise RuntimeError(
                f"Maximum {MAX_GLOBAL_TASKS} background tasks running globally. "
                f"Wait for one to finish or cancel with /tasks."
            )
        user_count = sum(1 for u in self._task_users.values() if u == user_id)
        if user_count >= MAX_PER_USER_TASKS:
            raise RuntimeError(
                f"Maximum {MAX_PER_USER_TASKS} background tasks per user. "
                f"Wait for one to finish or cancel with /tasks."
            )

        task_id = str(uuid4())
        task_name = name or f"task_{task_id[:8]}"

        # Store in DB (encrypted)
        key = derive_server_key(self._config.server_secret, user_id)
        encrypted_instruction = encrypt(instruction, key)

        async with db_session(self._config) as db:
            await db.execute(
                "INSERT INTO background_tasks "
                "(id, user_id, name, instruction, status, timeout) "
                "VALUES (?, ?, ?, ?, 'running', ?)",
                (task_id, user_id, task_name, encrypted_instruction, timeout),
            )
            await db.commit()

        # Spawn background execution
        bg_task = asyncio.create_task(
            self._execute(task_id, user_id, instruction, timeout, callback, on_complete),
            name=f"bg-{task_name}",
        )
        self._running[task_id] = bg_task
        self._task_users[task_id] = user_id
        self._task_names[task_id] = task_name
        self._task_starts[task_id] = time.monotonic()

        logger.info(
            "Background task %s (%s) started for user %s",
            task_id[:8], task_name, user_id,
        )
        return task_id

    async def _execute(
        self,
        task_id: str,
        user_id: str,
        instruction: str,
        timeout: int,
        callback: AgentCallback | None,
        on_complete=None,
    ) -> None:
        """Run agent in background with its own context."""
        from lazyclaw.runtime.agent import Agent

        key = derive_server_key(self._config.server_secret, user_id)
        task_name = self._task_names.get(task_id, task_id[:8])
        _status = "done"

        try:
            # Create FRESH Agent instance (isolated state, no race conditions)
            agent = Agent(
                config=self._config,
                router=self._router,
                registry=self._registry,
                eco_router=self._eco_router,
                permission_checker=self._permission_checker,
            )
            agent.is_background = True  # Browser uses headless in background

            async with asyncio.timeout(timeout):
                result = await agent.process_message(
                    user_id, instruction, callback=callback,
                )

            # Store result (encrypted)
            encrypted_result = encrypt(result, key)
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'done', result = ?, "
                    "completed_at = datetime('now') WHERE id = ?",
                    (encrypted_result, task_id),
                )
                await db.commit()

            logger.info("Background task %s (%s) completed", task_id[:8], task_name)

            # Notify user
            if callback:
                await callback.on_event(AgentEvent(
                    "background_done",
                    f"Background task '{task_name}' completed",
                    {"task_id": task_id, "name": task_name, "result": result},
                ))

        except asyncio.TimeoutError:
            _status = "failed"
            logger.warning(
                "Background task %s (%s) timed out after %ds",
                task_id[:8], task_name, timeout,
            )
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'failed', "
                    "error = ?, completed_at = datetime('now') WHERE id = ?",
                    (f"Timed out after {timeout} seconds", task_id),
                )
                await db.commit()

            if callback:
                await callback.on_event(AgentEvent(
                    "background_failed",
                    f"Background task '{task_name}' timed out",
                    {"task_id": task_id, "name": task_name,
                     "error": f"Timed out after {timeout}s"},
                ))

        except asyncio.CancelledError:
            _status = "cancelled"
            logger.info("Background task %s (%s) cancelled", task_id[:8], task_name)
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'cancelled', "
                    "completed_at = datetime('now') WHERE id = ?",
                    (task_id,),
                )
                await db.commit()

        except Exception as exc:
            _status = "failed"
            logger.error(
                "Background task %s (%s) failed: %s",
                task_id[:8], task_name, exc,
            )
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'failed', "
                    "error = ?, completed_at = datetime('now') WHERE id = ?",
                    (str(exc)[:500], task_id),
                )
                await db.commit()

            if callback:
                await callback.on_event(AgentEvent(
                    "background_failed",
                    f"Background task '{task_name}' failed",
                    {"task_id": task_id, "name": task_name, "error": str(exc)[:200]},
                ))

        finally:
            # ALWAYS clean up (prevents memory leaks)
            self._running.pop(task_id, None)
            self._task_users.pop(task_id, None)
            self._task_names.pop(task_id, None)
            self._task_starts.pop(task_id, None)

            # Notify originator (e.g., team lead state cleanup)
            if on_complete:
                try:
                    await on_complete(task_id, _status)
                except Exception as exc:
                    logger.warning("on_complete callback failed for task %s: %s", task_id[:8], exc)

    def list_running(self, user_id: str | None = None) -> list[dict]:
        """List running background tasks."""
        now = time.monotonic()
        result = []
        for tid, task in self._running.items():
            uid = self._task_users.get(tid, "")
            if user_id and uid != user_id:
                continue
            elapsed = now - self._task_starts.get(tid, now)
            result.append({
                "id": tid,
                "name": self._task_names.get(tid, tid[:8]),
                "user_id": uid,
                "status": "running",
                "elapsed": f"{elapsed:.0f}s",
                "elapsed_seconds": elapsed,
            })
        return result

    async def list_all(self, user_id: str, limit: int = 20) -> list[dict]:
        """List all tasks from DB (running + completed + failed)."""
        key = derive_server_key(self._config.server_secret, user_id)

        async with db_session(self._config) as db:
            rows = await db.execute(
                "SELECT id, name, status, error, created_at, completed_at "
                "FROM background_tasks WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
            results = await rows.fetchall()

        tasks = []
        for row in results:
            tasks.append({
                "id": row[0],
                "name": row[1],
                "status": row[2],
                "error": row[3],
                "created_at": row[4],
                "completed_at": row[5],
            })
        return tasks

    async def cancel(self, task_id: str, user_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled."""
        uid = self._task_users.get(task_id)
        if uid != user_id:
            return False

        task = self._running.get(task_id)
        if task and not task.done():
            task.cancel()
            logger.info("Cancelled background task %s", task_id[:8])
            return True
        return False

    async def cancel_all(self) -> int:
        """Cancel all running tasks. Call on shutdown."""
        count = 0
        for tid, task in list(self._running.items()):
            if not task.done():
                task.cancel()
                count += 1

        # Wait for all to finish
        if self._running:
            await asyncio.gather(
                *self._running.values(), return_exceptions=True,
            )

        logger.info("Cancelled %d background tasks on shutdown", count)
        return count

    @property
    def running_count(self) -> int:
        """Number of currently running tasks."""
        return len(self._running)
