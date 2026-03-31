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
    # macOS breakdown (Activity Monitor style)
    app_memory_mb: int = 0    # active - purgeable
    wired_mb: int = 0         # kernel + GPU (Apple Silicon unified memory)
    compressed_mb: int = 0    # compressor pages
    cached_mb: int = 0        # inactive + purgeable (reclaimable)

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
        breakdown = await _get_ram_breakdown(total)
        available = breakdown["available_mb"]
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
            app_memory_mb=breakdown["app_mb"],
            wired_mb=breakdown["wired_mb"],
            compressed_mb=breakdown["compressed_mb"],
            cached_mb=breakdown["cached_mb"],
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
        f"\U0001f4ca <b>RAM</b>  {bar} {status.system_used_pct}%  "
        f"({_fmt_mb(status.system_total_mb)})",
        "",
    ]

    # macOS breakdown (Activity Monitor style)
    if status.wired_mb > 0:
        lines.append(f"\u2699\ufe0f macOS + GPU: {_fmt_mb(status.wired_mb)}")
    if status.app_memory_mb > 0:
        lines.append(f"\U0001f4f1 Apps: {_fmt_mb(status.app_memory_mb)}")
    if status.compressed_mb > 0:
        lines.append(f"\U0001f4e6 Compressed: {_fmt_mb(status.compressed_mb)}")
    if status.cached_mb > 0:
        lines.append(f"\U0001f4c2 Cached: {_fmt_mb(status.cached_mb)}")

    # LazyClaw + AI processes
    lines.append("")
    if status.lazyclaw_rss_mb > 0:
        lines.append(f"\U0001f43e LazyClaw: {_fmt_mb(status.lazyclaw_rss_mb)}")
    if status.mlx_brain_mb > 0:
        brain_name = _get_model_name_for_port(8080) or "Brain"
        lines.append(f"\U0001f9e0 MLX {brain_name}: {_fmt_mb(status.mlx_brain_mb)}")
    if status.mlx_worker_mb > 0:
        worker_name = _get_model_name_for_port(8081) or "Worker"
        lines.append(f"\U0001f916 MLX {worker_name}: {_fmt_mb(status.mlx_worker_mb)}")
    if status.ollama_mb > 0:
        lines.append(f"\U0001f999 Ollama: {_fmt_mb(status.ollama_mb)}")
    if status.ai_total_mb > 0:
        lines.append(
            f"     <b>AI Total</b>: {_fmt_mb(status.ai_total_mb)} "
            f"({status.ai_pct_of_system}%)"
        )

    headroom_icon = "\U0001f7e2" if status.headroom_mb > 2000 else (
        "\U0001f7e1" if status.headroom_mb > 500 else "\U0001f534"
    )
    lines.append(f"\n{headroom_icon} Headroom: {_fmt_mb(status.headroom_mb)}")

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


# ── Helpers ───────────────────────────────────────────────────────────


def _get_model_name_for_port(port: int) -> str | None:
    """Get the short model name running on an MLX port (8080/8081).

    Parses the --model arg from the process command line.
    Returns e.g. 'Nanbeige 3B' or 'Qwen3.5 4B', or None.
    """
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        pid = result.stdout.strip().split("\n")[0]
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", pid],
            capture_output=True, text=True, timeout=5,
        )
        cmd = result.stdout.strip()
        # Extract --model value from command line
        if "--model" in cmd:
            parts = cmd.split("--model")
            if len(parts) > 1:
                model_path = parts[1].strip().split()[0]
                # "mlx-community/Nanbeige4.1-3B-8bit" → "Nanbeige 3B"
                name = model_path.split("/")[-1]
                # Clean up: remove quantization suffix, add space before size
                for suffix in ("-MLX-4bit", "-MLX-8bit", "-8bit", "-4bit"):
                    name = name.replace(suffix, "")
                # "Nanbeige4.1-3B" → "Nanbeige 3B"
                import re
                name = re.sub(r"[\d.]+[-_](\d+B)", r" \1", name)
                name = name.replace("-", " ").strip()
                return name
    except Exception:
        pass
    return None


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


async def _get_ram_breakdown(total: int) -> dict[str, int]:
    """Get RAM breakdown via vm_stat (macOS), Activity Monitor style.

    Returns dict with: available_mb, app_mb, wired_mb, compressed_mb, cached_mb.

    On Apple Silicon, "wired" includes GPU unified memory — this is normal
    and not a leak.  The breakdown makes this visible to the user.
    """
    zeroes = {
        "available_mb": 0, "app_mb": 0,
        "wired_mb": 0, "compressed_mb": 0, "cached_mb": 0,
    }
    if total == 0:
        return zeroes

    # Primary: vm_stat
    try:
        result = await asyncio.create_subprocess_exec(
            "vm_stat",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        text = stdout.decode()

        page_size = 16384
        pages: dict[str, int] = {}

        for line in text.splitlines():
            if "page size of" in line:
                parts = line.split("page size of")
                if len(parts) > 1:
                    page_size = int(parts[1].strip().split()[0])
            for key in (
                "Pages free:",
                "Pages active:",
                "Pages inactive:",
                "Pages speculative:",
                "Pages wired down:",
                "Pages purgeable:",
                "Pages occupied by compressor:",
            ):
                if key in line:
                    val = line.split(":")[1].strip().rstrip(".")
                    pages[key] = int(val)

        def _to_mb(page_count: int) -> int:
            return (page_count * page_size) // (1024 * 1024)

        free = pages.get("Pages free:", 0)
        active = pages.get("Pages active:", 0)
        inactive = pages.get("Pages inactive:", 0)
        speculative = pages.get("Pages speculative:", 0)
        wired = pages.get("Pages wired down:", 0)
        purgeable = pages.get("Pages purgeable:", 0)
        compressed = pages.get("Pages occupied by compressor:", 0)

        # Activity Monitor categories
        app_pages = max(0, active - purgeable)
        cached_pages = inactive + purgeable
        available_pages = free + inactive + purgeable + speculative

        return {
            "available_mb": _to_mb(available_pages),
            "app_mb": _to_mb(app_pages),
            "wired_mb": _to_mb(wired),
            "compressed_mb": _to_mb(compressed),
            "cached_mb": _to_mb(cached_pages),
        }
    except Exception:
        pass

    # Fallback: memory_pressure percentage (no breakdown)
    try:
        result = await asyncio.create_subprocess_exec(
            "memory_pressure",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        text = stdout.decode()

        for line in text.splitlines():
            if "System-wide memory free percentage:" in line:
                pct_str = line.split(":")[1].strip().rstrip("%")
                free_pct = int(pct_str)
                return {**zeroes, "available_mb": total * free_pct // 100}
    except Exception:
        pass

    return zeroes


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
