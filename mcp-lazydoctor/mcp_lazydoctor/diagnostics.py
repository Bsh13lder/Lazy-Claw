"""Core diagnostic engine — lint, test, type-check, and analyze."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from mcp_lazydoctor.config import DoctorConfig
from mcp_lazydoctor.runner import RunResult, run_tool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiagnosticIssue:
    """A single issue found by a diagnostic tool."""
    tool: str  # "ruff", "pytest", "mypy"
    severity: str  # "error", "warning", "info"
    file: str
    line: int
    code: str  # rule code like "E501", "F841"
    message: str
    fixable: bool = False

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "code": self.code,
            "message": self.message,
            "fixable": self.fixable,
        }


@dataclass(frozen=True)
class DiagnosticReport:
    """Full diagnostic report from all tools."""
    issues: tuple[DiagnosticIssue, ...] = ()
    test_summary: str = ""
    test_passed: bool = True
    tools_run: tuple[str, ...] = ()
    raw_outputs: dict = field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def fixable_count(self) -> int:
        return sum(1 for i in self.issues if i.fixable)

    @property
    def healthy(self) -> bool:
        return self.error_count == 0 and self.test_passed

    def summary(self) -> str:
        status = "HEALTHY" if self.healthy else "NEEDS ATTENTION"
        lines = [
            f"Status: {status}",
            f"Tools run: {', '.join(self.tools_run)}",
            f"Errors: {self.error_count}, Warnings: {self.warning_count}, Auto-fixable: {self.fixable_count}",
        ]
        if self.test_summary:
            lines.append(f"Tests: {self.test_summary}")
        return "\n".join(lines)

    def to_text(self, verbose: bool = False) -> str:
        """Format report as readable text."""
        lines = [self.summary(), ""]

        if self.issues:
            lines.append(f"--- Issues ({len(self.issues)}) ---")
            for issue in self.issues:
                fix_tag = " [FIXABLE]" if issue.fixable else ""
                lines.append(
                    f"[{issue.severity.upper()}] {issue.file}:{issue.line} "
                    f"{issue.code}: {issue.message}{fix_tag}"
                )

        if verbose:
            for tool_name, output in self.raw_outputs.items():
                lines.append(f"\n--- Raw: {tool_name} ---")
                lines.append(output[:3000])

        return "\n".join(lines)


def _ruff_path(config: DoctorConfig) -> str:
    return config.ruff_path or "ruff"


def _pytest_path(config: DoctorConfig) -> str:
    return config.pytest_path or "pytest"


def _mypy_path(config: DoctorConfig) -> str:
    return config.mypy_path or "mypy"


def _parse_ruff_json(output: str) -> list[DiagnosticIssue]:
    """Parse ruff JSON output into DiagnosticIssue list."""
    issues = []
    try:
        entries = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return issues

    for entry in entries:
        fix = entry.get("fix")
        issues.append(DiagnosticIssue(
            tool="ruff",
            severity="warning" if entry.get("code", "").startswith("W") else "error",
            file=entry.get("filename", "?"),
            line=entry.get("location", {}).get("row", 0),
            code=entry.get("code", "?"),
            message=entry.get("message", ""),
            fixable=fix is not None and fix.get("applicability", "") in ("safe", "suggested"),
        ))
    return issues


def _parse_pytest_output(result: RunResult) -> tuple[str, bool]:
    """Extract test summary and pass/fail from pytest output."""
    output = result.output
    # Look for the summary line like "5 passed, 2 failed in 1.23s"
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if "passed" in stripped or "failed" in stripped or "error" in stripped:
            passed = result.returncode == 0
            return stripped, passed
    if result.returncode == 0:
        return "All tests passed", True
    if result.returncode == 5:
        return "No tests found", True  # exit code 5 = no tests collected
    return f"Tests failed (exit code {result.returncode})", False


def _parse_mypy_output(output: str) -> list[DiagnosticIssue]:
    """Parse mypy text output into DiagnosticIssue list."""
    issues = []
    for line in output.splitlines():
        # Format: file.py:line: severity: message  [code]
        if ": error:" in line or ": warning:" in line or ": note:" in line:
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            filepath = parts[0].strip()
            try:
                lineno = int(parts[1].strip())
            except (ValueError, IndexError):
                lineno = 0
            rest = parts[2].strip() + ":" + parts[3] if len(parts) > 3 else parts[2].strip()

            severity = "info"
            if ": error:" in line:
                severity = "error"
            elif ": warning:" in line:
                severity = "warning"

            # Extract [code] from end
            code = "mypy"
            msg = rest
            if "[" in rest and rest.endswith("]"):
                bracket_idx = rest.rindex("[")
                code = rest[bracket_idx + 1:-1]
                msg = rest[:bracket_idx].strip()

            # Clean up "error: " prefix from message
            for prefix in ("error: ", "warning: ", "note: "):
                if msg.startswith(prefix):
                    msg = msg[len(prefix):]
                    break

            issues.append(DiagnosticIssue(
                tool="mypy",
                severity=severity,
                file=filepath,
                line=lineno,
                code=code,
                message=msg,
                fixable=False,
            ))
    return issues


async def run_lint(
    config: DoctorConfig,
    target: str | None = None,
) -> tuple[list[DiagnosticIssue], RunResult]:
    """Run ruff check and return issues."""
    target_path = target or str(config.project_root)
    result = await run_tool(
        [_ruff_path(config), "check", "--output-format=json", "--no-fix", target_path],
        cwd=config.project_root,
        timeout=config.lint_timeout,
    )
    issues = _parse_ruff_json(result.stdout)
    return issues, result


async def run_lint_fix(
    config: DoctorConfig,
    target: str | None = None,
) -> RunResult:
    """Run ruff check --fix to auto-fix safe issues."""
    if not config.auto_fix_enabled:
        return RunResult(
            command="ruff fix (disabled)",
            returncode=1,
            stdout="",
            stderr="Auto-fix is disabled in config (LAZYDOCTOR_AUTO_FIX=false)",
        )
    target_path = target or str(config.project_root)
    return await run_tool(
        [_ruff_path(config), "check", "--fix", "--unsafe-fixes=false", target_path],
        cwd=config.project_root,
        timeout=config.lint_timeout,
    )


async def run_format(
    config: DoctorConfig,
    target: str | None = None,
    check_only: bool = False,
) -> RunResult:
    """Run ruff format."""
    target_path = target or str(config.project_root)
    args = [_ruff_path(config), "format"]
    if check_only:
        args.append("--check")
    args.append(target_path)
    return await run_tool(
        args,
        cwd=config.project_root,
        timeout=config.lint_timeout,
    )


async def run_tests(
    config: DoctorConfig,
    target: str | None = None,
    verbose: bool = False,
) -> tuple[str, bool, RunResult]:
    """Run pytest and return (summary, passed, raw_result)."""
    args = [_pytest_path(config), "--tb=short", "-q"]
    if verbose:
        args.append("-v")
    if target:
        args.append(target)
    result = await run_tool(
        args,
        cwd=config.project_root,
        timeout=config.test_timeout,
    )
    summary, passed = _parse_pytest_output(result)
    return summary, passed, result


async def run_typecheck(
    config: DoctorConfig,
    target: str | None = None,
) -> tuple[list[DiagnosticIssue], RunResult]:
    """Run mypy and return issues."""
    args = [_mypy_path(config), "--no-color-output"]
    if target:
        args.append(target)
    else:
        args.append(str(config.project_root))
    result = await run_tool(
        args,
        cwd=config.project_root,
        timeout=config.lint_timeout,
    )
    issues = _parse_mypy_output(result.output)
    return issues, result


async def full_checkup(
    config: DoctorConfig,
    target: str | None = None,
    skip_tests: bool = False,
    skip_typecheck: bool = False,
) -> DiagnosticReport:
    """Run all diagnostics and produce a unified report."""
    import asyncio as _aio

    all_issues: list[DiagnosticIssue] = []
    raw_outputs: dict[str, str] = {}
    tools_run: list[str] = []
    test_summary = ""
    test_passed = True

    # Run lint + format check in parallel, optionally with tests and typecheck
    tasks = {
        "lint": run_lint(config, target),
        "format": run_format(config, target, check_only=True),
    }
    if not skip_tests:
        tasks["test"] = run_tests(config, target)
    if not skip_typecheck:
        tasks["typecheck"] = run_typecheck(config, target)

    results = await _aio.gather(*tasks.values(), return_exceptions=True)
    task_names = list(tasks.keys())

    for name, result in zip(task_names, results):
        tools_run.append(name)

        if isinstance(result, Exception):
            logger.error("Tool %s failed: %s", name, result)
            raw_outputs[name] = f"Error: {result}"
            continue

        if name == "lint":
            issues, raw = result
            all_issues.extend(issues)
            raw_outputs["ruff"] = raw.output

        elif name == "format":
            raw = result
            raw_outputs["ruff-format"] = raw.output
            if not raw.success:
                all_issues.append(DiagnosticIssue(
                    tool="ruff-format",
                    severity="warning",
                    file="(project)",
                    line=0,
                    code="FORMAT",
                    message="Code formatting issues detected. Run `ruff format` to fix.",
                    fixable=True,
                ))

        elif name == "test":
            summary, passed, raw = result
            test_summary = summary
            test_passed = passed
            raw_outputs["pytest"] = raw.output

        elif name == "typecheck":
            issues, raw = result
            all_issues.extend(issues)
            raw_outputs["mypy"] = raw.output

    return DiagnosticReport(
        issues=tuple(all_issues),
        test_summary=test_summary,
        test_passed=test_passed,
        tools_run=tuple(tools_run),
        raw_outputs=raw_outputs,
    )
