"""MCP server for project self-healing + LazyClaw maintenance audit."""
from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.types import Tool, TextContent

from mcp_lazydoctor.apply import apply_finding, revert_finding
from mcp_lazydoctor.audit.runner import run_audit
from mcp_lazydoctor.config import LazyDoctorConfig
from mcp_lazydoctor.diagnostics import diagnose_project, run_lint, run_tests
from mcp_lazydoctor.findings import FindingStore
from mcp_lazydoctor.fixer import auto_fix_lint
from mcp_lazydoctor.runner import run_heal_cycle

logger = logging.getLogger(__name__)


def create_server(config: LazyDoctorConfig) -> Server:
    server = Server("mcp-lazydoctor")

    # FindingStore is opened lazily on first audit-class tool call so the
    # existing healing tools don't pay the sqlite open cost.
    _store: dict[str, FindingStore] = {}

    def _get_store() -> FindingStore:
        if "store" not in _store:
            _store["store"] = FindingStore(config.findings_db)
        return _store["store"]

    @server.list_tools()
    async def list_tools():
        return [
            # ── Existing healing tools (unchanged) ───────────────────
            Tool(
                name="doctor_diagnose",
                description="Run all diagnostics (lint + tests) on the project.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="doctor_lint",
                description="Run linter on the project and report issues.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="doctor_test",
                description="Run tests on the project and report results.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="doctor_fix",
                description="Auto-fix lint issues in the project.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "dry_run": {
                            "type": "boolean",
                            "description": "Preview fixes without applying",
                        },
                    },
                },
            ),
            Tool(
                name="doctor_heal",
                description="Run full diagnose-fix-verify cycle.",
                inputSchema={"type": "object", "properties": {}},
            ),
            # ── New audit tools ──────────────────────────────────────
            Tool(
                name="doctor_audit",
                description=(
                    "Run the LazyClaw maintenance audit. Scans bundled MCPs "
                    "for outdated pip and npx deps and persists findings. "
                    "Returns the AuditReport (audit_id, findings list with "
                    "id/title/severity/proposed_action). Use scope='deps' "
                    "(default) for the MVP coverage; severity_min filters "
                    "out chatty patch bumps (default 'medium')."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["deps", "all"],
                            "description": "Audit scope. 'all' aliases 'deps' in MVP.",
                        },
                        "severity_min": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": "Minimum severity to surface. Default medium.",
                        },
                    },
                },
            ),
            Tool(
                name="doctor_list_findings",
                description=(
                    "List findings from the local FindingStore. Filter by "
                    "status (pending|approved|rejected|applied|failed|"
                    "needs_human) and limit. No args = recent 50."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                },
            ),
            Tool(
                name="doctor_apply_finding",
                description=(
                    "Apply ONE finding by id on a sandboxed audit branch "
                    "(lazydoctor/audit-YYYY-MM-DD). Edits the target file, "
                    "runs a per-MCP smoke install + pytest, commits on "
                    "green, hard-resets on red. Honours config.dry_run by "
                    "default — pass dry_run=False explicitly to go live "
                    "even when the server is in dry-run mode."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "finding_id": {"type": "string"},
                        "dry_run": {"type": "boolean"},
                    },
                    "required": ["finding_id"],
                },
            ),
            Tool(
                name="doctor_revert_finding",
                description=(
                    "Revert a previously-applied finding via `git revert` "
                    "of its recorded commit. Safe — never rewrites history."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"finding_id": {"type": "string"}},
                    "required": ["finding_id"],
                },
            ),
            Tool(
                name="doctor_mark_finding",
                description=(
                    "Mark a finding rejected or needs_human (the audit "
                    "bridge calls this on user reject so the row stops "
                    "showing up as pending)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "finding_id": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["rejected", "needs_human", "pending"],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["finding_id", "status"],
                },
            ),
            Tool(
                name="doctor_check_mcp_versions",
                description=(
                    "Read-only convenience wrapper around the deps audit "
                    "without persisting findings. Useful from CLI to peek "
                    "at drift before scheduling a real audit."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "severity_min": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            # ── Existing healing tools ────────────────────────────────
            if name == "doctor_diagnose":
                results = await diagnose_project(config.project_root)
                output = []
                for r in results:
                    output.append({
                        "tool": r.tool, "success": r.success,
                        "errors": r.errors[:20], "warnings": r.warnings[:10],
                    })
                return [TextContent(type="text", text=json.dumps(output, indent=2))]

            elif name == "doctor_lint":
                result = await run_lint(config.project_root)
                return [TextContent(type="text", text=result.output[:4000])]

            elif name == "doctor_test":
                result = await run_tests(config.project_root)
                return [TextContent(type="text", text=result.output[:4000])]

            elif name == "doctor_fix":
                dry_run = arguments.get("dry_run", config.dry_run)
                result = await auto_fix_lint(config.project_root, dry_run)
                return [TextContent(type="text", text=result[:4000])]

            elif name == "doctor_heal":
                result = await run_heal_cycle(config)
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            # ── Audit tools ──────────────────────────────────────────
            elif name == "doctor_audit":
                report = await run_audit(
                    config,
                    scope=arguments.get("scope", "deps"),
                    severity_min=arguments.get("severity_min", "medium"),
                    store=_get_store(),
                )
                return [TextContent(type="text", text=json.dumps(report.to_dict(), indent=2))]

            elif name == "doctor_list_findings":
                rows = _get_store().list(
                    status=arguments.get("status"),
                    limit=int(arguments.get("limit", 50)),
                )
                return [TextContent(type="text", text=json.dumps(rows, indent=2))]

            elif name == "doctor_apply_finding":
                finding_id = arguments["finding_id"]
                dry = arguments.get("dry_run")
                result = await apply_finding(
                    config, finding_id,
                    store=_get_store(),
                    dry_run=dry,
                )
                return [TextContent(type="text", text=json.dumps(result.to_dict(), indent=2))]

            elif name == "doctor_revert_finding":
                result = await revert_finding(
                    config, arguments["finding_id"], store=_get_store(),
                )
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "doctor_mark_finding":
                ok = _get_store().update_status(
                    arguments["finding_id"], arguments["status"],
                    reason=arguments.get("reason"),
                )
                return [TextContent(type="text", text=json.dumps(
                    {"success": ok, "finding_id": arguments["finding_id"],
                     "status": arguments["status"]}, indent=2,
                ))]

            elif name == "doctor_check_mcp_versions":
                # Run the audit but DON'T persist — pass store=None.
                report = await run_audit(
                    config,
                    scope="deps",
                    severity_min=arguments.get("severity_min", "medium"),
                    store=None,
                )
                return [TextContent(type="text", text=json.dumps(report.to_dict(), indent=2))]

        except Exception as exc:
            logger.error("lazydoctor tool %s failed: %s", name, exc, exc_info=True)
            return [TextContent(type="text", text=f"Error: {exc}")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
