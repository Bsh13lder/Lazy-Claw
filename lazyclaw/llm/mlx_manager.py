"""MLX Model Manager — lifecycle management for mlx_lm.server.

DEPRECATED: Ollama 0.19+ includes a native MLX backend and manages model
processes automatically. Use `ollama serve` + OllamaProvider instead.

This module is kept for backward compatibility only. It will be removed
in a future release after the Ollama migration is fully validated.

Legacy architecture (replaced by Ollama):
  - External server: User starts mlx_lm.server manually
  - Managed server:  MLXManager starts/stops the server process
  - On-demand mode:  Server starts on first request, auto-stops after idle
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_BRAIN_PORT = 8080
_DEFAULT_WORKER_PORT = 8081
_HEALTH_TIMEOUT = 5
_STARTUP_TIMEOUT = 60  # seconds to wait for server to be ready
_IDLE_TIMEOUT = 120  # seconds — auto-stop after 2 min idle (saves ~10 GB RAM)


@dataclass(frozen=True)
class MLXServerConfig:
    """Immutable configuration for an MLX server instance."""

    model_path: str  # HuggingFace model ID or local path
    port: int = _DEFAULT_BRAIN_PORT
    host: str = _DEFAULT_HOST
    quantize: str | None = None  # e.g. "4bit", "8bit" (None = use model's native)
    max_tokens: int = 4096
    trust_remote_code: bool = True


@dataclass
class MLXServerState:
    """Mutable state for a running MLX server."""

    config: MLXServerConfig
    process: subprocess.Popen | None = None
    healthy: bool = False
    model_loaded: str | None = None


class MLXManager:
    """Manages MLX server instances for brain and worker models.

    Supports two concurrent servers (brain on :8080, worker on :8081)
    so both models stay loaded — zero swap latency between brain/worker.

    Usage:
        manager = MLXManager()
        await manager.start_brain("mlx-community/Qwen3.5-9B-MLX-4bit")
        await manager.start_worker("mlx-community/Nanbeige4.1-3B-8bit")
        # ... use providers at localhost:8080 and localhost:8081
        await manager.stop_all()
    """

    def __init__(
        self,
        on_demand: bool = True,
        on_idle_stop: asyncio.coroutines = None,
    ) -> None:
        self._brain: MLXServerState | None = None
        self._worker: MLXServerState | None = None
        self._lock = asyncio.Lock()
        # On-demand mode: start server when needed, stop after idle
        self._on_demand = on_demand
        self._idle_handle: asyncio.TimerHandle | None = None
        # Remember model configs for on-demand restart
        self._worker_model: str | None = None
        self._brain_model: str | None = None
        # Callback when idle-stop fires (e.g. ECO router resets local_checked)
        self._on_idle_stop = on_idle_stop

    # ── Properties ────────────────────────────────────────────────────

    @property
    def brain_url(self) -> str:
        port = self._brain.config.port if self._brain else _DEFAULT_BRAIN_PORT
        return f"http://{_DEFAULT_HOST}:{port}"

    @property
    def worker_url(self) -> str:
        port = self._worker.config.port if self._worker else _DEFAULT_WORKER_PORT
        return f"http://{_DEFAULT_HOST}:{port}"

    @property
    def brain_healthy(self) -> bool:
        return self._brain is not None and self._brain.healthy

    @property
    def worker_healthy(self) -> bool:
        return self._worker is not None and self._worker.healthy

    # ── Start/Stop ────────────────────────────────────────────────────

    async def start_brain(
        self, model_path: str, port: int = _DEFAULT_BRAIN_PORT
    ) -> bool:
        """Start (or restart) the brain model server."""
        self._brain_model = model_path
        async with self._lock:
            if self._brain and self._brain.process:
                await self._stop_server(self._brain)

            config = MLXServerConfig(model_path=model_path, port=port)
            self._brain = await self._start_server(config)
            if self._brain.healthy and self._on_demand:
                self._reset_idle_timer()
            return self._brain.healthy

    async def start_worker(
        self, model_path: str, port: int = _DEFAULT_WORKER_PORT
    ) -> bool:
        """Start (or restart) the worker model server."""
        self._worker_model = model_path
        async with self._lock:
            if self._worker and self._worker.process:
                await self._stop_server(self._worker)

            config = MLXServerConfig(model_path=model_path, port=port)
            self._worker = await self._start_server(config)
            if self._worker.healthy and self._on_demand:
                self._reset_idle_timer()
            return self._worker.healthy

    async def stop_all(self) -> None:
        """Stop all managed servers."""
        self._cancel_idle_timer()
        async with self._lock:
            if self._brain:
                await self._stop_server(self._brain)
                self._brain = None
            if self._worker:
                await self._stop_server(self._worker)
                self._worker = None

    # ── On-demand lifecycle ────────────────────────────────────────────

    def touch(self) -> None:
        """Reset the idle timer — call this on every inference request."""
        if self._on_demand:
            self._reset_idle_timer()

    async def ensure_running(self) -> bool:
        """Start the server if not running (on-demand mode).

        Returns True if at least one server is healthy.
        Cold start takes ~6s on M2 for Nanbeige 3B.
        """
        # Already running?
        if self._worker and self._worker.healthy:
            self.touch()
            return True
        if self._brain and self._brain.healthy:
            self.touch()
            return True

        # Start the worker (most common single-model setup)
        if self._worker_model:
            logger.info("On-demand: starting MLX worker (%s)...", self._worker_model)
            return await self.start_worker(self._worker_model)

        if self._brain_model:
            logger.info("On-demand: starting MLX brain (%s)...", self._brain_model)
            return await self.start_brain(self._brain_model)

        return False

    def _reset_idle_timer(self) -> None:
        """Cancel existing timer and start a new one."""
        self._cancel_idle_timer()
        try:
            loop = asyncio.get_running_loop()
            self._idle_handle = loop.call_later(
                _IDLE_TIMEOUT, lambda: asyncio.ensure_future(self._idle_shutdown()),
            )
        except RuntimeError:
            pass  # No running loop (e.g. during tests)

    def _cancel_idle_timer(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    async def _idle_shutdown(self) -> None:
        """Auto-stop servers after idle timeout to free GPU RAM."""
        # Only stop managed servers (with a process we started)
        has_managed = (
            (self._worker and self._worker.process)
            or (self._brain and self._brain.process)
        )
        if not has_managed:
            return

        logger.info(
            "MLX idle for %ds — stopping to free GPU RAM", _IDLE_TIMEOUT,
        )
        async with self._lock:
            if self._worker and self._worker.process:
                await self._stop_server(self._worker)
                self._worker = None
            if self._brain and self._brain.process:
                await self._stop_server(self._brain)
                self._brain = None

        # Notify ECO router to reset local_checked so next request
        # triggers on-demand start again
        if self._on_idle_stop is not None:
            try:
                self._on_idle_stop()
            except Exception:
                pass

    # ── Health ────────────────────────────────────────────────────────

    async def check_health(self) -> dict:
        """Check health of all managed servers. Returns status dict."""
        brain_ok = await self._check_server_health(
            self._brain
        ) if self._brain else False
        worker_ok = await self._check_server_health(
            self._worker
        ) if self._worker else False

        if self._brain:
            self._brain.healthy = brain_ok
        if self._worker:
            self._worker.healthy = worker_ok

        return {
            "brain": {
                "healthy": brain_ok,
                "model": self._brain.model_loaded if self._brain else None,
                "port": self._brain.config.port if self._brain else None,
            },
            "worker": {
                "healthy": worker_ok,
                "model": self._worker.model_loaded if self._worker else None,
                "port": self._worker.config.port if self._worker else None,
            },
        }

    async def ensure_healthy(self) -> bool:
        """Verify both servers are healthy. Returns True if at least brain works."""
        status = await self.check_health()
        return status["brain"]["healthy"]

    # ── External server detection ─────────────────────────────────────

    async def detect_external(
        self,
        brain_port: int = _DEFAULT_BRAIN_PORT,
        worker_port: int = _DEFAULT_WORKER_PORT,
    ) -> dict:
        """Detect externally-managed MLX servers (user started them).

        Returns: {"brain": model_name | None, "worker": model_name | None}
        """
        result = {"brain": None, "worker": None}

        for role, port in [("brain", brain_port), ("worker", worker_port)]:
            model = await self._probe_server(port)
            if model:
                result[role] = model
                config = MLXServerConfig(model_path=model, port=port)
                state = MLXServerState(
                    config=config, process=None, healthy=True, model_loaded=model
                )
                if role == "brain":
                    self._brain = state
                else:
                    self._worker = state
                logger.info(
                    "Detected external MLX %s server: %s on :%d",
                    role, model, port,
                )

        return result

    # ── Server lifecycle (internal) ───────────────────────────────────

    async def _start_server(self, config: MLXServerConfig) -> MLXServerState:
        """Start an mlx_lm.server process and wait for it to be ready."""
        import sys

        # Try direct binary first, then venv binary, then python -m
        mlx_lm_path = shutil.which("mlx_lm.server")
        if not mlx_lm_path:
            # Check venv bin directory
            venv_bin = Path(sys.executable).parent
            venv_path = venv_bin / "mlx_lm.server"
            if venv_path.exists():
                mlx_lm_path = str(venv_path)

        if mlx_lm_path:
            cmd = [
                mlx_lm_path,
                "--model", config.model_path,
                "--port", str(config.port),
                "--host", config.host,
            ]
        else:
            # Fallback: run as Python module
            cmd = [
                sys.executable, "-m", "mlx_lm", "server",
                "--model", config.model_path,
                "--port", str(config.port),
                "--host", config.host,
            ]

        if config.trust_remote_code:
            cmd.append("--trust-remote-code")

        logger.info("Starting MLX server: %s", " ".join(cmd))

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Don't inherit parent signals — server lives independently
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
        except FileNotFoundError:
            logger.error("mlx_lm.server binary not found")
            return MLXServerState(config=config, healthy=False)
        except Exception as exc:
            logger.error("Failed to start MLX server: %s", exc)
            return MLXServerState(config=config, healthy=False)

        state = MLXServerState(config=config, process=process)

        # Wait for server to become healthy
        healthy = await self._wait_for_ready(config.port)
        state.healthy = healthy

        if healthy:
            state.model_loaded = await self._probe_server(config.port)
            logger.info(
                "MLX server ready on :%d (model: %s)",
                config.port, state.model_loaded,
            )
        else:
            logger.warning(
                "MLX server on :%d failed to start within %ds",
                config.port, _STARTUP_TIMEOUT,
            )
            # Kill the process if it didn't start properly
            await self._stop_server(state)

        return state

    async def _stop_server(self, state: MLXServerState) -> None:
        """Gracefully stop an MLX server process."""
        if not state.process:
            return

        pid = state.process.pid
        logger.info("Stopping MLX server (PID %d, port %d)", pid, state.config.port)

        try:
            state.process.terminate()
            # Wait up to 5s for graceful shutdown
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, state.process.wait
                    ),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                logger.warning("MLX server PID %d didn't stop, killing", pid)
                state.process.kill()
                state.process.wait()
        except ProcessLookupError:
            pass  # Already dead

        state.process = None
        state.healthy = False

    async def _wait_for_ready(self, port: int) -> bool:
        """Poll until the server responds to /v1/models or timeout."""
        url = f"http://{_DEFAULT_HOST}:{port}/v1/models"
        deadline = asyncio.get_event_loop().time() + _STARTUP_TIMEOUT

        while asyncio.get_event_loop().time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return True
            except Exception:
                pass
            await asyncio.sleep(1)

        return False

    async def _check_server_health(self, state: MLXServerState) -> bool:
        """Quick health check for a single server."""
        if state.process and state.process.poll() is not None:
            # Process died
            return False

        try:
            url = f"http://{_DEFAULT_HOST}:{state.config.port}/v1/models"
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False

    async def _probe_server(self, port: int) -> str | None:
        """Get the model name from a running server."""
        try:
            url = f"http://{_DEFAULT_HOST}:{port}/v1/models"
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                models = data.get("data", [])
                return models[0].get("id") if models else None
        except Exception:
            return None

    # ── Status for display ────────────────────────────────────────────

    def get_status_text(self) -> str:
        """Return human-readable status for Telegram/TUI display."""
        lines = []

        if self._brain:
            icon = "\u2705" if self._brain.healthy else "\u274c"
            model = self._brain.model_loaded or "loading..."
            lines.append(f"{icon} Brain: {model} (:{self._brain.config.port})")
        else:
            lines.append("\u26aa Brain: not started")

        if self._worker:
            icon = "\u2705" if self._worker.healthy else "\u274c"
            model = self._worker.model_loaded or "loading..."
            lines.append(f"{icon} Worker: {model} (:{self._worker.config.port})")
        else:
            lines.append("\u26aa Worker: not started")

        return "\n".join(lines)
