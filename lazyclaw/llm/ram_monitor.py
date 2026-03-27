"""RAM monitor — tracks memory usage for local AI models and LazyClaw.

Provides real-time RAM stats for TUI dashboard and /ram Telegram command.
Works on macOS (Apple Silicon) via sysctl + ps. Falls back to psutil if available.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RAMStatus:
    """Immutable snapshot of system and model RAM usage."""

    system_total_mb: int
    system_available_mb: int
    system_used_pct: float
    lazyclaw_rss_mb: int
    mlx_brain_mb: int       # 0 if not running
    mlx_worker_mb: int      # 0 if not running
    ollama_mb: int           # 0 if not running
    models_estimated_mb: int  # from model_registry
    ai_total_mb: int         # mlx_brain + mlx_worker + ollama

    @property
    def ai_pct_of_system(self) -> float:
        """Percentage of system RAM used by AI models."""
        if self.system_total_mb == 0:
            return 0.0
        return round(self.ai_total_mb / self.system_total_mb * 100, 1)

    @property
    def headroom_mb(self) -> int:
        """Available RAM minus OS overhead estimate."""
        return max(0, self.system_available_mb - 500)  # 500MB OS safety margin


async def get_ram_status() -> RAMStatus:
    """Get current RAM status. Never raises — returns zeroes on error."""
    try:
        total = await _get_total_ram()
        available = await _get_available_ram()
        used_pct = round((total - available) / total * 100, 1) if total > 0 else 0.0
        lazyclaw_rss = _get_process_rss(os.getpid())
        mlx_brain = await _get_process_rss_by_port(8080)
        mlx_worker = await _get_process_rss_by_port(8081)
        ollama = await _get_process_rss_by_name("ollama")
        ai_total = mlx_brain + mlx_worker + ollama

        # Estimated from registry
        from lazyclaw.llm.model_registry import estimate_eco_ram_mb
        estimated = estimate_eco_ram_mb()

        return RAMStatus(
            system_total_mb=total,
            system_available_mb=available,
            system_used_pct=used_pct,
            lazyclaw_rss_mb=lazyclaw_rss,
            mlx_brain_mb=mlx_brain,
            mlx_worker_mb=mlx_worker,
            ollama_mb=ollama,
            models_estimated_mb=estimated,
            ai_total_mb=ai_total,
        )
    except Exception as exc:
        logger.debug("RAM monitor error: %s", exc)
        return RAMStatus(
            system_total_mb=0, system_available_mb=0, system_used_pct=0.0,
            lazyclaw_rss_mb=0, mlx_brain_mb=0, mlx_worker_mb=0,
            ollama_mb=0, models_estimated_mb=0, ai_total_mb=0,
        )


# ── Formatters ────────────────────────────────────────────────────────

def format_ram_telegram(status: RAMStatus) -> str:
    """Format RAM status as HTML for Telegram."""
    bar = _progress_bar(status.system_used_pct)

    lines = [
        "<b>RAM Usage</b>",
        f"{bar} {status.system_used_pct}%",
        "",
        f"\U0001f4bb System: {_fmt_mb(status.system_total_mb - status.system_available_mb)} / {_fmt_mb(status.system_total_mb)}",
        f"\U0001f9e0 LazyClaw: {_fmt_mb(status.lazyclaw_rss_mb)}",
    ]

    if status.mlx_brain_mb > 0:
        lines.append(f"\U0001f9e0 MLX Brain: {_fmt_mb(status.mlx_brain_mb)}")
    if status.mlx_worker_mb > 0:
        lines.append(f"\U0001f916 MLX Worker: {_fmt_mb(status.mlx_worker_mb)}")
    if status.ollama_mb > 0:
        lines.append(f"\U0001f999 Ollama: {_fmt_mb(status.ollama_mb)}")

    if status.ai_total_mb > 0:
        lines.append(
            f"\n\U0001f916 AI Total: {_fmt_mb(status.ai_total_mb)} "
            f"({status.ai_pct_of_system}% of RAM)"
        )

    lines.append(f"\n\U0001f7e2 Headroom: {_fmt_mb(status.headroom_mb)}")

    return "\n".join(lines)


def format_ram_tui(status: RAMStatus) -> str:
    """Format RAM status as plain text for TUI sidebar."""
    parts = [
        f"RAM {status.system_used_pct}%",
        f"{_fmt_mb(status.system_total_mb - status.system_available_mb)}/{_fmt_mb(status.system_total_mb)}",
    ]
    if status.ai_total_mb > 0:
        parts.append(f"AI:{_fmt_mb(status.ai_total_mb)}")
    return " | ".join(parts)


def format_ram_compact(status: RAMStatus) -> str:
    """One-line RAM for status bar."""
    return (
        f"RAM {status.system_used_pct}% "
        f"(AI:{_fmt_mb(status.ai_total_mb)} "
        f"Free:{_fmt_mb(status.headroom_mb)})"
    )


# ── macOS system info ─────────────────────────────────────────────────

async def _get_total_ram() -> int:
    """Get total system RAM in MB via sysctl."""
    try:
        result = await asyncio.create_subprocess_exec(
            "sysctl", "-n", "hw.memsize",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        return int(stdout.strip()) // (1024 * 1024)
    except Exception:
        return 0


async def _get_available_ram() -> int:
    """Get available RAM in MB via memory_pressure (macOS).

    Uses the 'System-wide memory free percentage' line which is the
    most accurate metric — includes reclaimable caches, inactive pages,
    and purgeable memory that macOS will release when apps need it.
    """
    total = await _get_total_ram()

    # memory_pressure gives the real "free %" that macOS reports
    try:
        result = await asyncio.create_subprocess_exec(
            "memory_pressure",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        text = stdout.decode()

        for line in text.splitlines():
            if "System-wide memory free percentage:" in line:
                # "System-wide memory free percentage: 50%"
                pct_str = line.split(":")[1].strip().rstrip("%")
                free_pct = int(pct_str)
                return total * free_pct // 100
    except Exception:
        pass

    # Fallback: vm_stat (free + inactive + purgeable + speculative)
    try:
        result = await asyncio.create_subprocess_exec(
            "vm_stat",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        text = stdout.decode()

        page_size = 16384
        reclaimable = 0

        for line in text.splitlines():
            if "page size of" in line:
                parts = line.split("page size of")
                if len(parts) > 1:
                    page_size = int(parts[1].strip().split()[0])
            elif any(k in line for k in ("Pages free:", "Pages inactive:", "Pages purgeable:", "Pages speculative:")):
                reclaimable += int(line.split(":")[1].strip().rstrip("."))

        return (reclaimable * page_size) // (1024 * 1024)
    except Exception:
        return 0


def _get_process_rss(pid: int) -> int:
    """Get RSS of a specific process in MB. Returns 0 on error."""
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # ps reports RSS in KB
            return int(result.stdout.strip()) // 1024
    except Exception:
        pass
    return 0


async def _get_process_rss_by_port(port: int) -> int:
    """Find process listening on a port and return its RSS in MB."""
    try:
        result = await asyncio.create_subprocess_exec(
            "lsof", "-ti", f":{port}",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        pids = stdout.decode().strip().split("\n")
        if pids and pids[0]:
            return _get_process_rss(int(pids[0]))
    except Exception:
        pass
    return 0


async def _get_process_rss_by_name(name: str) -> int:
    """Find process by name and return total RSS in MB."""
    try:
        result = await asyncio.create_subprocess_exec(
            "pgrep", "-f", name,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        pids = stdout.decode().strip().split("\n")
        total = 0
        for pid_str in pids:
            if pid_str.strip():
                total += _get_process_rss(int(pid_str.strip()))
        return total
    except Exception:
        pass
    return 0


# ── Helpers ───────────────────────────────────────────────────────────

def _fmt_mb(mb: int) -> str:
    """Format MB as human-readable (e.g., '5.6 GB' or '512 MB')."""
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb} MB"


def _progress_bar(pct: float, width: int = 10) -> str:
    """Simple text progress bar."""
    filled = int(pct / 100 * width)
    empty = width - filled
    bar = "\u2588" * filled + "\u2591" * empty
    return f"[{bar}]"
