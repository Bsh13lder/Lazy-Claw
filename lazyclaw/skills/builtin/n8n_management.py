"""n8n workflow automation management skills.

Six BaseSkill subclasses for managing n8n workflows via REST API:
  - n8n_status: health check + API key validation
  - n8n_list_workflows: list all workflows with status
  - n8n_create_workflow: create workflow from natural language
  - n8n_manage_workflow: activate / deactivate / delete
  - n8n_run_workflow: execute a workflow manually
  - n8n_list_executions: execution history + error inspection
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_N8N_DEFAULT_BASE = "http://lazyclaw-n8n:5678"


# ---------------------------------------------------------------------------
# Shared HTTP helper
# ---------------------------------------------------------------------------

async def _n8n_request(
    config: Any,
    user_id: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float = 15.0,
) -> dict:
    """Make an authenticated n8n API request.

    API key lookup order:
      1. Encrypted vault (key: 'n8n_api_key')
      2. Environment variable N8N_API_KEY
    Base URL: vault 'n8n_base_url' -> env N8N_BASE_URL -> default.

    Returns parsed JSON response.
    Raises RuntimeError on auth/connection errors.
    """
    import httpx

    # Resolve API key
    api_key = ""
    if config:
        try:
            from lazyclaw.crypto.vault import get_credential
            api_key = await get_credential(config, user_id, "n8n_api_key") or ""
        except Exception:
            logger.debug("Failed to load n8n API key from vault, falling back to env", exc_info=True)
    if not api_key:
        api_key = os.getenv("N8N_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "n8n API key not configured. Set it with: "
            "vault_set key=n8n_api_key value=YOUR_KEY  "
            "(Get the key from n8n Settings > API)"
        )

    # Resolve base URL
    base_url = os.getenv("N8N_BASE_URL", _N8N_DEFAULT_BASE)
    if config:
        try:
            from lazyclaw.crypto.vault import get_credential
            stored_url = await get_credential(config, user_id, "n8n_base_url")
            if stored_url:
                base_url = stored_url
        except Exception:
            logger.debug("Failed to load n8n base URL from vault, using env/default", exc_info=True)

    url = f"{base_url.rstrip('/')}{path}"
    headers = {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, url, headers=headers, json=body)
        if resp.status_code == 401:
            raise RuntimeError("n8n API key is invalid. Update with: vault_set key=n8n_api_key value=NEW_KEY")
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()


def _connection_error_msg(exc: Exception) -> str:
    """Friendly message for n8n connection failures."""
    exc_str = str(exc)
    exc_type = type(exc).__name__
    if "ConnectError" in exc_type or "Connection refused" in exc_str:
        return (
            "Cannot reach n8n. Make sure it's running: "
            "docker compose up -d n8n"
        )
    if "RuntimeError" in exc_type:
        return str(exc)
    return f"n8n error: {exc}"


# ---------------------------------------------------------------------------
# 1. n8n_status
# ---------------------------------------------------------------------------

class N8nStatusSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_status"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "Check if n8n is running and the API key is valid."

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            data = await _n8n_request(self._config, user_id, "GET", "/api/v1/workflows?limit=1")
            count = data.get("count", len(data.get("data", [])))
            return f"n8n is running. API key valid. {count} workflow(s) found."
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 2. n8n_list_workflows
# ---------------------------------------------------------------------------

class N8nListWorkflowsSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_list_workflows"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "List all n8n workflows with their status (active/inactive)."

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            data = await _n8n_request(self._config, user_id, "GET", "/api/v1/workflows?limit=100")
            workflows = data.get("data", [])

            if not workflows:
                return "No workflows found in n8n. Create one with n8n_create_workflow."

            lines = ["== n8n Workflows ==", ""]
            lines.append(f"{'ID':<8} {'Active':<8} {'Name'}")
            lines.append("-" * 50)

            for wf in workflows:
                wf_id = str(wf.get("id", "?"))
                active = "YES" if wf.get("active") else "no"
                wf_name = wf.get("name", "Untitled")
                lines.append(f"{wf_id:<8} {active:<8} {wf_name}")

            lines.append("")
            lines.append(f"Total: {len(workflows)} workflow(s)")
            return "\n".join(lines)
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 3. n8n_create_workflow
# ---------------------------------------------------------------------------

class N8nCreateWorkflowSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_create_workflow"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Create an n8n workflow from a natural language description. "
            "Tries pre-built templates first, falls back to LLM generation. "
            "Example: 'Watch my email and notify me on Telegram when I get a new message'"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What the workflow should do, in plain language",
                },
                "name": {
                    "type": "string",
                    "description": "Optional name for the workflow",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Optional parameters for templates (e.g., chat_id, cron, "
                        "feed_url, folder_id, sheet_id)"
                    ),
                },
                "activate": {
                    "type": "boolean",
                    "description": "Activate the workflow immediately (default: false)",
                },
            },
            "required": ["description"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            description = params["description"]
            wf_name = params.get("name", "")
            extra_params = params.get("params", {})
            activate = params.get("activate", False)

            # 1. Try template matching
            from lazyclaw.skills.builtin.n8n_templates import match_template
            template = match_template(description)

            workflow_json: dict
            if template:
                build_params = {**extra_params}
                if wf_name:
                    build_params["name"] = wf_name
                workflow_json = template["build"](build_params)
                source = f"template: {template['name']}"
            else:
                # 2. Fall back to LLM generation
                try:
                    from lazyclaw.skills.builtin.n8n_workflow_builder import generate_workflow_json
                    workflow_json = await generate_workflow_json(
                        self._config, user_id, description, wf_name or None,
                    )
                    source = "LLM-generated"
                except Exception as gen_exc:
                    logger.warning("LLM workflow generation failed: %s", gen_exc)
                    # 3. Last resort: create a minimal webhook workflow
                    workflow_json = {
                        "name": wf_name or "New Workflow",
                        "nodes": [
                            {
                                "parameters": {"httpMethod": "POST", "path": "trigger"},
                                "id": "webhook-1",
                                "name": "Webhook",
                                "type": "n8n-nodes-base.webhook",
                                "typeVersion": 2,
                                "position": [250, 300],
                                "webhookId": "",
                            },
                        ],
                        "connections": {},
                        "settings": {"executionOrder": "v1"},
                    }
                    source = "minimal scaffold (LLM generation failed)"

            # Ensure name is set
            if wf_name and "name" not in workflow_json:
                workflow_json["name"] = wf_name

            # Create via API
            result = await _n8n_request(
                self._config, user_id, "POST", "/api/v1/workflows",
                body=workflow_json, timeout=30.0,
            )

            wf_id = result.get("id", "?")
            created_name = result.get("name", workflow_json.get("name", "Untitled"))

            # Activate if requested
            if activate and wf_id != "?":
                try:
                    await _n8n_request(
                        self._config, user_id, "POST",
                        f"/api/v1/workflows/{wf_id}/activate",
                    )
                except Exception as act_exc:
                    return (
                        f"Workflow '{created_name}' created (ID: {wf_id}) "
                        f"from {source}, but activation failed: {act_exc}. "
                        f"Some nodes may need credentials configured in n8n first."
                    )

            status = "active" if activate else "inactive"
            return (
                f"Workflow '{created_name}' created (ID: {wf_id}, {status}). "
                f"Source: {source}. "
                f"Open http://localhost:5678/workflow/{wf_id} to view/edit."
            )
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 4. n8n_manage_workflow
# ---------------------------------------------------------------------------

class N8nManageWorkflowSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_manage_workflow"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return "Activate, deactivate, or delete an n8n workflow by ID."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow ID (use n8n_list_workflows to find it)",
                },
                "action": {
                    "type": "string",
                    "enum": ["activate", "deactivate", "delete"],
                    "description": "Action to perform on the workflow",
                },
            },
            "required": ["workflow_id", "action"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            wf_id = params["workflow_id"]
            action = params["action"]

            if action == "activate":
                await _n8n_request(self._config, user_id, "POST", f"/api/v1/workflows/{wf_id}/activate")
                return f"Workflow {wf_id} activated."
            elif action == "deactivate":
                await _n8n_request(self._config, user_id, "POST", f"/api/v1/workflows/{wf_id}/deactivate")
                return f"Workflow {wf_id} deactivated."
            elif action == "delete":
                await _n8n_request(self._config, user_id, "DELETE", f"/api/v1/workflows/{wf_id}")
                return f"Workflow {wf_id} deleted."
            else:
                return f"Unknown action '{action}'. Use: activate, deactivate, or delete."
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 5. n8n_run_workflow
# ---------------------------------------------------------------------------

class N8nRunWorkflowSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_run_workflow"

    @property
    def description(self) -> str:
        return "Execute an n8n workflow manually by ID and return the result."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow ID to execute",
                },
                "data": {
                    "type": "object",
                    "description": "Optional input data to pass to the workflow",
                },
            },
            "required": ["workflow_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            wf_id = params["workflow_id"]
            input_data = params.get("data", {})

            result = await _n8n_request(
                self._config, user_id, "POST",
                f"/api/v1/workflows/{wf_id}/run",
                body={"data": input_data} if input_data else None,
                timeout=60.0,
            )

            execution_id = result.get("id", "?")
            status = result.get("status", result.get("finished", "unknown"))
            return f"Workflow {wf_id} executed. Execution ID: {execution_id}, status: {status}."
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 6. n8n_list_executions
# ---------------------------------------------------------------------------

class N8nListExecutionsSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_list_executions"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "List recent n8n workflow executions with status and errors. "
            "Optionally filter by workflow ID."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Optional: filter by workflow ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 10)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            limit = params.get("limit", 10)
            path = f"/api/v1/executions?limit={limit}"
            wf_id = params.get("workflow_id")
            if wf_id:
                path += f"&workflowId={wf_id}"

            data = await _n8n_request(self._config, user_id, "GET", path)
            executions = data.get("data", [])

            if not executions:
                return "No executions found."

            lines = ["== Recent Executions ==", ""]
            lines.append(f"{'ID':<12} {'Workflow':<10} {'Status':<12} {'Finished'}")
            lines.append("-" * 55)

            for ex in executions:
                ex_id = str(ex.get("id", "?"))
                ex_wf = str(ex.get("workflowId", "?"))
                status = ex.get("status", "?")
                finished = ex.get("stoppedAt", ex.get("startedAt", "?"))
                if isinstance(finished, str) and "T" in finished:
                    finished = finished.split("T")[0] + " " + finished.split("T")[1][:5]
                lines.append(f"{ex_id:<12} {ex_wf:<10} {status:<12} {finished}")

            lines.append("")
            lines.append(f"Showing {len(executions)} execution(s)")
            return "\n".join(lines)
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 7. n8n_get_workflow
# ---------------------------------------------------------------------------

class N8nGetWorkflowSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_get_workflow"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "Get full details of an n8n workflow by ID (nodes, connections, credentials)."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow ID (use n8n_list_workflows to find it)",
                },
            },
            "required": ["workflow_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            wf_id = params["workflow_id"]
            data = await _n8n_request(self._config, user_id, "GET", f"/api/v1/workflows/{wf_id}")

            name = data.get("name", "Untitled")
            active = "active" if data.get("active") else "inactive"
            nodes = data.get("nodes", [])
            node_summary = ", ".join(n.get("name", n.get("type", "?")) for n in nodes)

            lines = [
                f"== Workflow: {name} (ID: {wf_id}, {active}) ==",
                "",
                f"Nodes ({len(nodes)}): {node_summary}",
                "",
                "Full JSON:",
                json.dumps(data, indent=2),
            ]
            return "\n".join(lines)
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 8. n8n_update_workflow
# ---------------------------------------------------------------------------

class N8nUpdateWorkflowSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_update_workflow"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Update an n8n workflow by ID. Fetches the current workflow, "
            "merges your changes, and PUTs the result. "
            "Pass the full or partial workflow JSON to update."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow ID to update",
                },
                "workflow_json": {
                    "type": "object",
                    "description": (
                        "The workflow object with changes. Can be a full workflow "
                        "or partial (e.g., just {\"name\": \"New Name\"} or "
                        "{\"nodes\": [...], \"connections\": {...}})"
                    ),
                },
            },
            "required": ["workflow_id", "workflow_json"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            wf_id = params["workflow_id"]
            changes = params["workflow_json"]

            # Fetch current workflow first
            current = await _n8n_request(
                self._config, user_id, "GET", f"/api/v1/workflows/{wf_id}",
            )

            # Merge changes into current (shallow merge; nodes/connections replace entirely)
            merged = {**current, **changes}
            # Remove all read-only fields that n8n rejects on PUT
            for key in (
                "id", "createdAt", "updatedAt", "versionId", "active",
                "isArchived", "triggerCount", "meta", "tags",
                "activeVersion", "shared", "usedCredentials",
            ):
                merged.pop(key, None)

            result = await _n8n_request(
                self._config, user_id, "PUT",
                f"/api/v1/workflows/{wf_id}",
                body=merged, timeout=30.0,
            )

            updated_name = result.get("name", "Untitled")
            return (
                f"Workflow '{updated_name}' (ID: {wf_id}) updated successfully. "
                f"Open http://localhost:5678/workflow/{wf_id} to view."
            )
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 9. n8n_list_credentials
# ---------------------------------------------------------------------------

class N8nListCredentialsSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_list_credentials"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "List all configured n8n credentials (name, type, ID — not secret values)."

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            data = await _n8n_request(self._config, user_id, "GET", "/api/v1/credentials")
            creds = data.get("data", [])

            if not creds:
                return "No credentials configured in n8n. Add them in n8n Settings > Credentials."

            lines = ["== n8n Credentials ==", ""]
            lines.append(f"{'ID':<8} {'Type':<30} {'Name'}")
            lines.append("-" * 60)

            for cred in creds:
                cred_id = str(cred.get("id", "?"))
                cred_type = cred.get("type", "?")
                cred_name = cred.get("name", "Untitled")
                lines.append(f"{cred_id:<8} {cred_type:<30} {cred_name}")

            lines.append("")
            lines.append(f"Total: {len(creds)} credential(s)")
            return "\n".join(lines)
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 10. n8n_get_execution
# ---------------------------------------------------------------------------

class N8nGetExecutionSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_get_execution"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Get full details of an n8n execution by ID, including "
            "node outputs, error messages, and timing."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "execution_id": {
                    "type": "string",
                    "description": "The execution ID (use n8n_list_executions to find it)",
                },
            },
            "required": ["execution_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            ex_id = params["execution_id"]
            data = await _n8n_request(self._config, user_id, "GET", f"/api/v1/executions/{ex_id}")

            status = data.get("status", "?")
            wf_name = data.get("workflowData", {}).get("name", "?")
            wf_id = data.get("workflowId", "?")
            started = data.get("startedAt", "?")
            stopped = data.get("stoppedAt", "?")

            lines = [
                f"== Execution {ex_id} ==",
                f"Workflow: {wf_name} (ID: {wf_id})",
                f"Status: {status}",
                f"Started: {started}",
                f"Finished: {stopped}",
                "",
            ]

            # Extract per-node results
            result_data = data.get("data", {}).get("resultData", {})
            run_data = result_data.get("runData", {})
            if run_data:
                lines.append("Node Results:")
                for node_name, node_runs in run_data.items():
                    for run in node_runs:
                        node_status = run.get("executionStatus", "?")
                        error = run.get("error")
                        error_msg = ""
                        if isinstance(error, dict):
                            error_msg = error.get("message", "")
                        elif error:
                            error_msg = str(error)
                        lines.append(f"  {node_name}: {node_status}")
                        if error_msg:
                            lines.append(f"    Error: {error_msg}")

            # Show last error if present at top level
            last_error = result_data.get("error")
            if last_error:
                err_msg = last_error.get("message", str(last_error)) if isinstance(last_error, dict) else str(last_error)
                lines.append("")
                lines.append(f"Execution Error: {err_msg}")

            return "\n".join(lines)
        except Exception as exc:
            return _connection_error_msg(exc)
