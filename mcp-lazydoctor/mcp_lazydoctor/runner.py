"""Safe subprocess runner for doctor tools (ruff, pytest, mypy, git)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Only these executables are allowed
_ALLOWED_COMMANDS = frozenset({
    "ruff", "pytest", "python", "mypy", "git", "pip",
})


@dataclass(frozen=True)
class RunResult:
    """Result of a subprocess execution."""
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined stdout + stderr, trimmed."""
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(self.stderr.strip())
        return "\n".join(parts)


async def run_tool(
    args: list[str],
    cwd: Path,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run a subprocess safely with timeout and command allowlist."""
    if not args:
        return RunResult(command="", returncode=1, stdout="", stderr="No command provided")

    executable = Path(args[0]).name
    if executable not in _ALLOWED_COMMANDS:
        return RunResult(
            command=" ".join(args),
            returncode=1,
            stdout="",
            stderr=f"Blocked: '{executable}' is not in the allowed command list: {sorted(_ALLOWED_COMMANDS)}",
        )

    cmd_str = " ".join(args)
    logger.info("Running: %s (cwd=%s, timeout=%ds)", cmd_str, cwd, timeout)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
        return RunResult(
            command=cmd_str,
            returncode=proc.returncode or 0,
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
        )
    except asyncio.TimeoutError:
        logger.warning("Command timed out after %ds: %s", timeout, cmd_str)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return RunResult(
            command=cmd_str,
            returncode=1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            timed_out=True,
        )
    except FileNotFoundError:
        return RunResult(
            command=cmd_str,
            returncode=1,
            stdout="",
            stderr=f"Command not found: {args[0]}. Is it installed?",
        )
    except Exception as exc:
        logger.error("Unexpected error running %s: %s", cmd_str, exc)
        return RunResult(
            command=cmd_str,
            returncode=1,
            stdout="",
            stderr=f"Unexpected error: {exc}",
        )
