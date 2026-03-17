"""MCP server — exposes lazydoctor tools to any MCP client."""
from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.types import Tool, TextContent

from mcp_lazydoctor.config import DoctorConfig
from mcp_lazydoctor.diagnostics import (
    full_checkup,
    run_lint,
    run_tests,
    run_typecheck,
    run_format,
)
from mcp_lazydoctor.fixer import auto_fix
from mcp_lazydoctor.git_ops import git_status, diff_stat, recent_commits

logger = logging.getLogger(__name__)


def _text(content: str) -> list[TextContent]:
    return [TextContent(type="text", text=content)]


def create_server(config: DoctorConfig) -> Server:
    server = Server("mcp-lazydoctor")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="doctor_checkup",
                description=(
                    "Full health checkup: lint + format check + tests + type check. "
                    "Returns a unified diagnostic report with all issues found."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "File or directory to check (default: entire project)",
                        },
                        "skip_tests": {
                            "type": "boolean",
                            "description": "Skip running pytest (faster)",
                            "default": False,
                        },
                        "skip_typecheck": {
                            "type": "boolean",
                            "description": "Skip running mypy (faster)",
                            "default": False,
                        },
                        "verbose": {
                            "type": "boolean",
                            "description": "Include raw tool output in report",
                            "default": False,
                        },
                    },
                },
            ),
            Tool(
                name="doctor_lint",
                description="Run ruff linter on the project. Returns structured issues with file, line, code, and message.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "File or directory to lint (default: entire project)",
                        },
                    },
                },
            ),
            Tool(
                name="doctor_test",
                description="Run pytest and return test results summary.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "Test file or directory (default: auto-discover)",
                        },
                        "verbose": {
                            "type": "boolean",
                            "description": "Show individual test results",
                            "default": False,
                        },
                    },
                },
            ),
            Tool(
                name="doctor_typecheck",
                description="Run mypy type checker and return type errors.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "File or directory to type-check (default: entire project)",
                        },
                    },
                },
            ),
            Tool(
                name="doctor_fix",
                description=(
                    "Auto-fix safe lint issues and format code. "
                    "Runs ruff --fix (safe fixes only) + ruff format. "
                    "Reports before/after issue counts. "
                    "Optionally creates a fix branch and commits."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "File or directory to fix (default: entire project)",
                        },
                    },
                },
            ),
            Tool(
                name="doctor_format",
                description="Format code with ruff format. Use check_only=true to just check without modifying.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "File or directory to format (default: entire project)",
                        },
                        "check_only": {
                            "type": "boolean",
                            "description": "Only check, don't modify files",
                            "default": False,
                        },
                    },
                },
            ),
            Tool(
                name="doctor_git_status",
                description="Show git status, recent commits, and uncommitted changes.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="doctor_heal",
                description=(
                    "Full self-healing loop: diagnose -> fix -> verify. "
                    "Runs checkup, applies auto-fixes, re-runs checkup to verify improvement. "
                    "This is the main autonomous healing tool."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "File or directory to heal (default: entire project)",
                        },
                        "max_rounds": {
                            "type": "integer",
                            "description": "Maximum fix-verify rounds (default: 3)",
                            "default": 3,
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        target = arguments.get("target")

        # --- doctor_checkup ---
        if name == "doctor_checkup":
            report = await full_checkup(
                config,
                target=target,
                skip_tests=arguments.get("skip_tests", False),
                skip_typecheck=arguments.get("skip_typecheck", False),
            )
            verbose = arguments.get("verbose", False)
            return _text(report.to_text(verbose=verbose))

        # --- doctor_lint ---
        elif name == "doctor_lint":
            issues, raw = await run_lint(config, target)
            if not issues:
                return _text("No lint issues found.")
            lines = [f"Found {len(issues)} issues:\n"]
            for issue in issues:
                fix_tag = " [FIXABLE]" if issue.fixable else ""
                lines.append(
                    f"  {issue.file}:{issue.line} {issue.code}: {issue.message}{fix_tag}"
                )
            return _text("\n".join(lines))

        # --- doctor_test ---
        elif name == "doctor_test":
            verbose = arguments.get("verbose", False)
            summary, passed, raw = await run_tests(config, target, verbose=verbose)
            status = "PASSED" if passed else "FAILED"
            text = f"Tests: {status}\n{summary}"
            if verbose or not passed:
                text += f"\n\n{raw.output[:4000]}"
            return _text(text)

        # --- doctor_typecheck ---
        elif name == "doctor_typecheck":
            issues, raw = await run_typecheck(config, target)
            if not issues:
                return _text("No type errors found.")
            lines = [f"Found {len(issues)} type issues:\n"]
            for issue in issues:
                lines.append(f"  {issue.file}:{issue.line} [{issue.code}]: {issue.message}")
            return _text("\n".join(lines))

        # --- doctor_fix ---
        elif name == "doctor_fix":
            result = await auto_fix(config, target)
            return _text(result.to_text())

        # --- doctor_format ---
        elif name == "doctor_format":
            check_only = arguments.get("check_only", False)
            result = await run_format(config, target, check_only=check_only)
            if result.success:
                msg = "Code is properly formatted." if check_only else "Code formatted successfully."
            else:
                msg = result.output[:3000]
            return _text(msg)

        # --- doctor_git_status ---
        elif name == "doctor_git_status":
            status = await git_status(config)
            diff = await diff_stat(config)
            commits = await recent_commits(config)
            text = (
                f"Git Status:\n{status.stdout or 'Clean'}\n\n"
                f"Changes:\n{diff}\n\n"
                f"Recent Commits:\n{commits}"
            )
            return _text(text)

        # --- doctor_heal ---
        elif name == "doctor_heal":
            max_rounds = min(arguments.get("max_rounds", 3), 5)
            report_lines: list[str] = []

            for round_num in range(1, max_rounds + 1):
                report_lines.append(f"=== Round {round_num}/{max_rounds} ===")

                # Diagnose
                report = await full_checkup(
                    config, target=target,
                    skip_tests=(round_num > 1),  # only test on first round
                    skip_typecheck=True,  # mypy is slow, skip in loop
                )
                report_lines.append(f"Diagnosis: {report.summary()}")

                if report.healthy and report.fixable_count == 0:
                    report_lines.append("Project is healthy. No fixes needed.")
                    break

                if report.fixable_count == 0:
                    report_lines.append(
                        f"Found {report.error_count} errors but none are auto-fixable. "
                        "Manual intervention needed."
                    )
                    # Include the issues for context
                    for issue in report.issues:
                        if issue.severity == "error":
                            report_lines.append(
                                f"  {issue.file}:{issue.line} {issue.code}: {issue.message}"
                            )
                    break

                # Fix
                fix_result = await auto_fix(config, target)
                report_lines.append(f"Fix: {fix_result.fixes_applied} issues fixed")

                if not fix_result.improved:
                    report_lines.append("No improvement after fix. Stopping.")
                    break

                report_lines.append(
                    f"Issues: {fix_result.issues_before} -> {fix_result.issues_after}"
                )

                if fix_result.issues_after == 0:
                    report_lines.append("All fixable issues resolved!")
                    break

            # Final verification
            final_report = await full_checkup(config, target=target, skip_typecheck=True)
            report_lines.append(f"\n=== Final Status ===\n{final_report.to_text()}")

            return _text("\n".join(report_lines))

        return _text(f"Unknown tool: {name}")

    return server
