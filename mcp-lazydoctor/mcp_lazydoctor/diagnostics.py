"""Project diagnostics: lint, type check, import analysis."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiagnosticResult:
    tool: str
    success: bool
    output: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def run_command(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"
    except asyncio.TimeoutError:
        return 1, "", f"Command timed out: {' '.join(cmd)}"


async def run_lint(project_root: str) -> DiagnosticResult:
    """Run ruff or flake8 on the project."""
    code, stdout, stderr = await run_command(
        ["python", "-m", "ruff", "check", "--output-format=text", "."],
        cwd=project_root,
    )
    output = stdout or stderr
    errors = [line for line in output.splitlines() if ": E" in line or ": F" in line]
    warnings = [line for line in output.splitlines() if ": W" in line]
    return DiagnosticResult(
        tool="ruff", success=(code == 0),
        output=output, errors=errors, warnings=warnings,
    )


async def run_tests(project_root: str) -> DiagnosticResult:
    """Run pytest on the project."""
    code, stdout, stderr = await run_command(
        ["python", "-m", "pytest", "--tb=short", "-q"],
        cwd=project_root,
    )
    output = stdout + stderr
    errors = [line for line in output.splitlines() if "FAILED" in line or "ERROR" in line]
    return DiagnosticResult(
        tool="pytest", success=(code == 0),
        output=output, errors=errors,
    )


async def diagnose_project(project_root: str) -> list[DiagnosticResult]:
    """Run all diagnostics on the project."""
    results = await asyncio.gather(
        run_lint(project_root),
        run_tests(project_root),
        return_exceptions=True,
    )
    return [
        r if isinstance(r, DiagnosticResult)
        else DiagnosticResult(tool="unknown", success=False, output=str(r))
        for r in results
    ]
