"""n8n workflow automation management skills.

BaseSkill subclasses for managing n8n workflows via REST API:
  - n8n_status: health check + API key validation
  - n8n_list_workflows: list all workflows with status
  - n8n_create_workflow: create workflow from natural language
  - n8n_manage_workflow: activate / deactivate / delete
  - n8n_run_workflow: execute a workflow manually
  - n8n_list_executions: execution history + error inspection
  - n8n_get_workflow / n8n_update_workflow: inspect + edit workflows
  - n8n_list_credentials / n8n_create_credential / n8n_delete_credential
  - n8n_get_execution: raw per-node I/O + error stacks (include_data=true)
  - n8n_google_sheets_setup: Sheets-specific OAuth2 credential helper
  - n8n_google_oauth_setup: generic multi-scope Google OAuth credential
  - n8n_google_services_setup: batch per-service Google credential shells
  - n8n_test_workflow: dry-run a workflow before activating
  - n8n_search_templates: query the community template library
  - n8n_install_template: import a community template by id
  - n8n_list_webhooks: discover public webhook URLs across workflows
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_N8N_DEFAULT_BASE = "http://lazyclaw-n8n:5678"

# Types that n8n counts as triggers capable of AUTOMATIC activation.
# n8n refuses active=true on workflows whose only trigger is manual
# (Manual Trigger / Start) — those are run by clicking "Execute
# Workflow" in the UI. Excluding them here prevents the create-skill
# from attempting an activation that's guaranteed to 400.
_ACTIVATABLE_TRIGGER_TYPES: frozenset[str] = frozenset({
    "n8n-nodes-base.webhook",
    "n8n-nodes-base.formTrigger",
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.cron",
    "n8n-nodes-base.emailReadImap",
    "n8n-nodes-base.rssFeedRead",
    "n8n-nodes-base.executeWorkflowTrigger",
})

# Types that count as manual-only trigger nodes — present in the
# workflow but don't enable activation.
_MANUAL_ONLY_TRIGGER_TYPES: frozenset[str] = frozenset({
    "n8n-nodes-base.manualTrigger",
    "n8n-nodes-base.start",
})

# Back-compat alias used elsewhere in the module.
_TRIGGER_NODE_TYPES = _ACTIVATABLE_TRIGGER_TYPES | _MANUAL_ONLY_TRIGGER_TYPES


class N8nHTTPError(RuntimeError):
    """n8n API returned a non-2xx status.

    Carries the HTTP status, the request method+path, and a best-effort
    decoded body so the tool layer can surface a readable error to the
    brain (status + n8n's own message field when present).
    """

    def __init__(self, status: int, method: str, path: str, body_text: str, message: str | None = None):
        self.status = status
        self.method = method
        self.path = path
        self.body_text = (body_text or "")[:800]
        self.message = message or ""
        super().__init__(
            f"n8n {method} {path} -> {status}"
            + (f": {self.message}" if self.message else f": {self.body_text[:200]}")
        )


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

    Returns parsed JSON response dict. 204 (No Content) returns {}.
    Raises:
      RuntimeError on config/auth problems (missing/invalid key).
      N8nHTTPError on any 4xx/5xx from n8n, with the body attached.
      httpx.ConnectError / httpx.TimeoutException when n8n is unreachable.
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
        status = resp.status_code

        if status == 401:
            raise RuntimeError(
                "n8n API key is invalid. Update with: vault_set key=n8n_api_key value=NEW_KEY"
            )

        if status >= 400:
            body_text = ""
            message = ""
            try:
                body_text = resp.text
            except Exception:
                body_text = ""
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    message = (
                        payload.get("message")
                        or payload.get("error")
                        or payload.get("hint")
                        or ""
                    )
                    if not message and "errors" in payload:
                        errors = payload.get("errors")
                        if isinstance(errors, list) and errors:
                            message = str(errors[0])[:400]
            except Exception:
                pass
            logger.warning(
                "n8n %s %s -> %d: %s",
                method, path, status, (message or body_text)[:400],
            )
            raise N8nHTTPError(status, method, path, body_text, message)

        if status == 204 or not resp.content:
            return {}

        try:
            data = resp.json()
        except Exception:
            snippet = (resp.text or "")[:200]
            logger.warning(
                "n8n %s %s -> %d returned non-JSON body: %s",
                method, path, status, snippet,
            )
            raise N8nHTTPError(
                status, method, path, resp.text or "",
                message=f"expected JSON, got: {snippet}",
            )
        if data is None:
            return {}
        return data


def _summarize_node_io(run: dict) -> str:
    """Build a compact summary of a node run's input/output payloads.

    Used by n8n_get_execution when include_data=true. Truncates each payload to
    keep the output size bounded.
    """
    try:
        data_block = run.get("data", {})
        main = data_block.get("main") if isinstance(data_block, dict) else None
        if not main or not isinstance(main, list):
            return ""
        lines: list[str] = []
        for branch_idx, branch in enumerate(main):
            if not isinstance(branch, list) or not branch:
                continue
            preview = json.dumps(branch[:3], default=str, ensure_ascii=False)[:600]
            lines.append(f"    Output[{branch_idx}]: {preview}")
        return "\n".join(lines)
    except Exception:
        return ""


def _normalize_scope(scope: str | list[str] | None) -> str:
    """Collapse a scope list into a single space-separated string.

    Accepts:
      - a list of scope URLs
      - a string with scopes separated by any mix of commas, newlines, tabs,
        or multiple spaces
    Returns a single-space-joined string (Google OAuth2 spec format).

    Deduplicates while preserving first-occurrence order. Drops empties.
    Fixes the common bug where pasting "scope1, scope2, scope3" produces
    scopes with trailing commas that Google rejects as invalid.
    """
    if not scope:
        return ""
    if isinstance(scope, list):
        parts_iter = scope
    else:
        raw = scope.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        raw = raw.replace(",", " ").replace(";", " ")
        parts_iter = raw.split(" ")
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts_iter:
        token = (part or "").strip().strip(",;")
        if not token or token in seen:
            continue
        seen.add(token)
        cleaned.append(token)
    return " ".join(cleaned)


# Curated defaults used by the generic Google OAuth setup skill. Each line is
# one scope URL; the function joins with spaces. Keep this list conservative —
# restricted scopes (gmail.modify, drive) require Google app verification or
# test-user whitelisting.
_DEFAULT_GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/cse",
)


async def _find_webhook_trigger(config, user_id: str, wf_id: str) -> tuple[dict | None, dict | None, bool]:
    """Look up a workflow and return (workflow, first_webhook_node, is_active).

    Returns (None, None, False) on error; workflow always present on success.
    """
    wf = await _n8n_request(config, user_id, "GET", f"/api/v1/workflows/{wf_id}")
    webhook_node = None
    for node in wf.get("nodes", []) or []:
        if isinstance(node, dict) and node.get("type") == "n8n-nodes-base.webhook":
            webhook_node = node
            break
    return wf, webhook_node, bool(wf.get("active", False))


def _webhook_base_urls() -> tuple[str, str]:
    """Return (production_base, test_base) webhook URL prefixes.

    The brain runs inside Docker so lazyclaw-n8n:5678 is reachable internally.
    The public URL is only for printing to the user for external callers.
    """
    internal = (os.getenv("N8N_BASE_URL", _N8N_DEFAULT_BASE)).rstrip("/")
    return internal, internal


async def _trigger_via_webhook(
    config,
    user_id: str,
    wf_id: str,
    data: dict,
    prefer_test_url: bool,
    timeout: float = 60.0,
) -> str:
    """Trigger a workflow via its Webhook node. Returns a human-readable result.

    n8n's public REST API does not expose manual workflow execution (/run
    returns 405). The only programmatic path is a Webhook trigger node.
    """
    import httpx

    wf, webhook_node, is_active = await _find_webhook_trigger(config, user_id, wf_id)
    wf_name = wf.get("name", "?") if wf else "?"

    if not webhook_node:
        return (
            f"Workflow '{wf_name}' (id: {wf_id}) cannot be triggered from the API.\n"
            "n8n's public REST API does not support manual workflow execution — "
            "only workflows with a Webhook trigger node can be fired programmatically.\n\n"
            "Options:\n"
            "  1. Add a Webhook node to the workflow (n8n_update_workflow).\n"
            "  2. Activate it with n8n_manage_workflow(action=activate) and let its "
            "native trigger (Schedule/Email/RSS/etc.) fire.\n"
            "  3. Open n8n UI and click 'Execute Workflow' manually for a one-off run."
        )

    params = webhook_node.get("parameters") or {}
    path = (params.get("path") or "").strip("/")
    method = (params.get("httpMethod") or "POST").upper()
    if not path:
        return (
            f"Workflow '{wf_name}' has a Webhook node but no path configured. "
            "Set a path in the Webhook node before triggering."
        )

    internal, _ = _webhook_base_urls()
    use_test = prefer_test_url or not is_active
    prefix = "/webhook-test" if use_test else "/webhook"
    url = f"{internal}{prefix}/{path}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            if method in ("GET", "DELETE"):
                resp = await client.request(method, url, params=data or None)
            else:
                resp = await client.request(method, url, json=data or {})
        except Exception as exc:
            return f"Error: webhook call to '{wf_name}' failed ({exc}). URL: {method} {url}"

    status = resp.status_code
    body = resp.text[:1500]

    if 200 <= status < 300:
        return (
            f"Triggered '{wf_name}' via webhook.\n"
            f"URL: {method} {url}\n"
            f"HTTP status: {status}\n"
            f"Response: {body or '(empty)'}"
        )

    if use_test and status == 404:
        hint = (
            " The /webhook-test/* URL only works while someone is clicking "
            "'Listen for test event' in the n8n UI. Activate the workflow "
            "first (n8n_manage_workflow action=activate) — then it uses the "
            "production /webhook/* URL automatically."
        )
    elif status == 404:
        hint = (
            " Workflow is active but n8n returned 404 — the Webhook node's "
            "path parameter is probably wrong. Read it back with "
            "n8n_get_workflow and do not guess another path."
        )
    elif status == 500:
        hint = (
            " n8n executed the workflow but something inside it threw — "
            "call n8n_list_executions then n8n_get_execution(include_data=true) "
            "to see which node failed. Do not re-trigger."
        )
    else:
        hint = " Read the body above verbatim before retrying."

    return (
        f"Error: webhook trigger for '{wf_name}' returned HTTP {status}.\n"
        f"URL: {method} {url}\n"
        f"Response: {body or '(empty)'}.{hint}"
    )


# ---------------------------------------------------------------------------
# Workflow node schema validation
# ---------------------------------------------------------------------------
#
# When the model or a template produces a workflow node with missing
# required parameters, n8n accepts the CREATE/UPDATE but rejects the
# ACTIVATE with an opaque "Cannot publish workflow: N node have
# configuration issues". We validate proactively so the agent sees a
# specific error BEFORE n8n sees the bad payload, and the error names
# the node + field so a one-shot fix is possible.
#
# The `SCHEMA_VIOLATION:` prefix is a hard-stop marker mirroring
# STOP_OAUTH_CREDENTIAL — stuck-detector + SOUL.md rules instruct the
# model not to loop on these.


def _validate_workflow_nodes(nodes: list[dict]) -> list[str]:
    """Return a list of human-readable violations for invalid nodes.

    Empty list == valid. Rules covered:
      * n8n-nodes-base.googleSheets v4+ requires `resource` + `operation`.
        `documentId` and `sheetName` must be resource-locator dicts
        (`__rl: True`, `value`, `mode`).
      * n8n-nodes-base.webhook requires `parameters.path`.
      * n8n-nodes-base.httpRequest requires `parameters.url`.
      * n8n-nodes-base.code requires `parameters.jsCode` or `pythonCode`.

    This is intentionally conservative — we block the PUT only on
    things n8n will definitely reject on activate.
    """
    violations: list[str] = []
    if not isinstance(nodes, list):
        return violations

    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type") or ""
        name = node.get("name") or node.get("id") or "?"
        params = node.get("parameters") or {}
        if not isinstance(params, dict):
            violations.append(
                f"Node '{name}' ({ntype}): parameters is not an object."
            )
            continue

        if ntype == "n8n-nodes-base.googleSheets":
            if not params.get("resource"):
                violations.append(
                    f"Node '{name}' (googleSheets) is missing required "
                    "`resource`. Set one of: 'sheet' (append/read/update), "
                    "'spreadsheet' (create/delete)."
                )
            if not params.get("operation"):
                violations.append(
                    f"Node '{name}' (googleSheets) is missing required "
                    "`operation`. For resource='sheet' use 'append' | "
                    "'appendOrUpdate' | 'read' | 'update'; for "
                    "resource='spreadsheet' use 'create' | 'delete'."
                )
            doc = params.get("documentId")
            if isinstance(doc, dict) and doc.get("__rl") is not True:
                violations.append(
                    f"Node '{name}' (googleSheets) has documentId without "
                    "`__rl: true`. Use: "
                    "{\"__rl\": true, \"value\": \"<id>\", \"mode\": \"id\"}."
                )
            sn = params.get("sheetName")
            if isinstance(sn, dict) and sn.get("__rl") is not True:
                violations.append(
                    f"Node '{name}' (googleSheets) has sheetName without "
                    "`__rl: true`. Use: "
                    "{\"__rl\": true, \"value\": \"gid=0\", \"mode\": \"list\"}."
                )

        elif ntype == "n8n-nodes-base.webhook":
            if not (params.get("path") or "").strip():
                violations.append(
                    f"Node '{name}' (webhook) is missing `path`. "
                    "Set a URL path like 'send-email'."
                )

        elif ntype == "n8n-nodes-base.httpRequest":
            if not params.get("url"):
                violations.append(
                    f"Node '{name}' (httpRequest) is missing required `url`."
                )

        elif ntype == "n8n-nodes-base.code":
            if not (params.get("jsCode") or params.get("pythonCode")):
                violations.append(
                    f"Node '{name}' (code) is missing `jsCode` or `pythonCode`."
                )

    return violations


def _schema_violation_error(violations: list[str]) -> str:
    """Format violations as a hard-stop error the brain should relay once."""
    header = (
        "Error: SCHEMA_VIOLATION: n8n rejected the workflow because the "
        "nodes below would fail activation. Fix exactly these fields and "
        "call update ONCE — do NOT rebuild the workflow, do NOT swap "
        "resource types, do NOT loop.\n"
    )
    lines = "\n".join(f"  - {v}" for v in violations)
    return header + lines


_OAUTH_ERROR_MARKERS = (
    "credentials have not been set up",
    "credentials are not set",
    "oauth2 credential is not",
    "please connect your account",
    "authorization required",
    "node does not have any credentials",
    "credential oauth",
    "oauth token",
    "refresh token",
    "access token is invalid",
)


def _is_oauth_credential_error(exc: Exception) -> bool:
    if not isinstance(exc, N8nHTTPError):
        return False
    combined = (exc.message + " " + exc.body_text).lower()
    return any(m in combined for m in _OAUTH_ERROR_MARKERS)


def _oauth_error_message(exc: N8nHTTPError) -> str:
    """Produce a hard-stop message for the brain when an n8n call fails
    because a Google (or other) OAuth credential has not been finished
    in the n8n UI. The `STOP:` prefix tells the model not to retry or
    pivot — just relay this verbatim to the user.
    """
    base_hint = (
        "A credential in this workflow has not been authorized yet. "
        "The user must complete OAuth consent in the n8n UI: "
        "http://localhost:5678/home/credentials — open the Google/OAuth "
        "credential shown in the workflow, click 'Connect my account', "
        "sign in, and re-run the workflow.\n\n"
        "Do NOT retry this call. Do NOT call run_command, browser, or any "
        "other tool to work around it. Tell the user the exact URL above "
        "and stop."
    )
    return (
        f"Error: STOP_OAUTH_CREDENTIAL: n8n {exc.method} {exc.path} -> "
        f"{exc.status}: {exc.message or exc.body_text[:200]}. {base_hint}"
    )


def _connection_error_msg(exc: Exception) -> str:
    """Readable error string for n8n failures, always prefixed `Error:`.

    The `Error:` prefix is a stable marker the brain (and stuck_detector)
    keys off to avoid retry loops. Every non-success path must go through
    here so no tool ever returns a success-shaped string on failure.
    """
    # OAuth credential errors get a dedicated hard-stop message so the
    # brain hands the consent URL to the user instead of looping tools.
    if _is_oauth_credential_error(exc):
        return _oauth_error_message(exc)

    if isinstance(exc, N8nHTTPError):
        hint = ""
        if exc.status == 404:
            hint = (
                " Hint: the workflow/resource ID probably does not exist. "
                "Do not retry with a different guessed ID — list workflows "
                "with n8n_list_workflows and confirm the correct one."
            )
        elif exc.status == 400:
            hint = (
                " Hint: n8n rejected the payload. Read the message above "
                "verbatim, fix exactly that field, and retry once — "
                "do NOT rebuild the workflow from scratch."
            )
        elif exc.status == 409:
            hint = " Hint: a workflow with that name may already exist."
        elif 500 <= exc.status < 600:
            hint = (
                " Hint: this is an n8n server error, not your payload. "
                "Tell the user — do not retry in a loop."
            )
        detail = exc.message or exc.body_text[:200] or f"status {exc.status}"
        return (
            f"Error: n8n {exc.method} {exc.path} -> {exc.status}: {detail}."
            f"{hint}"
        )

    exc_str = str(exc)
    exc_type = type(exc).__name__
    if "ConnectError" in exc_type or "Connection refused" in exc_str:
        return (
            "Error: cannot reach n8n at http://lazyclaw-n8n:5678. "
            "Check the docker sidecar is running: docker compose up -d n8n."
        )
    if "Timeout" in exc_type:
        return (
            f"Error: n8n request timed out ({exc}). "
            "The server is slow or unreachable — do not retry in a loop."
        )
    if isinstance(exc, RuntimeError):
        return f"Error: {exc}"
    return f"Error: n8n call failed ({exc_type}): {exc}"


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
# 2.5. n8n_list_templates — menu of built-in parameterized templates
# ---------------------------------------------------------------------------

class N8nListTemplatesSkill(BaseSkill):
    """Return the menu of built-in n8n templates shipped with LazyClaw.

    These are parameterized workflow builders (webhook_to_telegram,
    keyword_research_to_sheet, etc.) that produce n8n JSON known to pass
    n8n's POST validation. Brain should consult this menu BEFORE calling
    n8n_create_workflow, so it can pick a known-good template by name
    instead of hoping the keyword matcher guesses right.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_list_templates"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "List LazyClaw's built-in n8n workflow templates. Zero network "
            "calls. Call this BEFORE n8n_create_workflow to pick a known-"
            "good starting point. If no template fits, fall back to "
            "n8n_search_templates (community library) or describe the "
            "workflow and n8n_create_workflow will LLM-generate it."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            from lazyclaw.skills.builtin.n8n_templates import TEMPLATES
        except Exception as exc:
            return f"Error: could not load template registry: {exc}"

        if not TEMPLATES:
            return "No built-in templates registered."

        lines = ["== LazyClaw built-in n8n templates ==", ""]
        for tmpl in TEMPLATES:
            lines.append(f"- {tmpl['name']}")
            desc = (tmpl.get("description") or "").strip()
            if desc:
                lines.append(f"    {desc}")
        lines.append("")
        lines.append(
            "Use one by calling n8n_create_workflow with a description that "
            "mentions the template's key keywords (e.g. 'keyword research "
            "to google sheet', 'webhook to telegram'). LazyClaw will "
            "match the template and build known-good n8n JSON."
        )
        return "\n".join(lines)


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
            from lazyclaw.skills.builtin.n8n_templates import match_template, TEMPLATES
            template = match_template(description)

            workflow_json: dict
            if template:
                build_params = {**extra_params}
                if wf_name:
                    build_params["name"] = wf_name
                workflow_json = template["build"](build_params)
                source = f"template: {template['name']}"
                logger.info(
                    "n8n_create_workflow: matched template '%s' for description %r",
                    template["name"], description[:80],
                )
            else:
                logger.info(
                    "n8n_create_workflow: no template match for %r (of %d built-ins), "
                    "falling back to LLM generation",
                    description[:80], len(TEMPLATES),
                )
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

            # n8n 1.x POST /workflows accepts ONLY these four top-level
            # fields — same allowlist as the PUT path. Strips anything the
            # LLM hallucinated (e.g. "active", "tags", "triggerCount").
            create_body = {
                "name": workflow_json.get("name") or wf_name or "Untitled",
                "nodes": workflow_json.get("nodes") or [],
                "connections": workflow_json.get("connections") or {},
                "settings": workflow_json.get("settings") or {"executionOrder": "v1"},
            }

            # Create via API
            result = await _n8n_request(
                self._config, user_id, "POST", "/api/v1/workflows",
                body=create_body, timeout=30.0,
            )

            wf_id = result.get("id", "?")
            created_name = result.get("name", workflow_json.get("name", "Untitled"))

            # Activation gating: validate node schemas AND presence of
            # a trigger node before attempting activate. Otherwise we
            # return a structured "created but not activated" message so
            # the model has one clear next step instead of 25 panicky
            # update/activate retries.
            nodes_for_check = create_body.get("nodes") or []
            violations = _validate_workflow_nodes(nodes_for_check)

            def _node_type(n: dict) -> str:
                return (n.get("type") or "") if isinstance(n, dict) else ""

            has_activatable_trigger = any(
                _node_type(n) in _ACTIVATABLE_TRIGGER_TYPES
                for n in nodes_for_check
            )
            has_any_trigger = has_activatable_trigger or any(
                _node_type(n) in _MANUAL_ONLY_TRIGGER_TYPES
                for n in nodes_for_check
            )

            if violations:
                return (
                    f"Workflow '{created_name}' created (ID: {wf_id}) "
                    f"from {source} but NOT activated — schema issues:\n"
                    + "\n".join(f"  - {v}" for v in violations)
                    + f"\nFix via n8n_update_workflow(workflow_id='{wf_id}', ...) "
                    "ONCE, then activate with "
                    f"n8n_manage_workflow(workflow_id='{wf_id}', action='activate'). "
                    f"Open http://localhost:5678/workflow/{wf_id} to view."
                )
            if not has_any_trigger:
                return (
                    f"Workflow '{created_name}' created (ID: {wf_id}) "
                    f"from {source} but NOT activated — no trigger node. "
                    "Add a webhook/schedule trigger via "
                    f"n8n_update_workflow(workflow_id='{wf_id}', ...), "
                    "then call n8n_manage_workflow(action='activate'). "
                    f"Open http://localhost:5678/workflow/{wf_id} to view."
                )
            if not has_activatable_trigger:
                # Manual Trigger only — n8n refuses active=true. Return a
                # friendly "created, run manually" message so the model
                # does NOT call n8n_manage_workflow(action=activate) and
                # bounce off a 400.
                return (
                    f"Workflow '{created_name}' created (ID: {wf_id}) "
                    f"from {source}. Uses a Manual Trigger — n8n does "
                    "NOT allow active=true on manual-only workflows. "
                    "Run it by opening "
                    f"http://localhost:5678/workflow/{wf_id} and clicking "
                    "'Execute Workflow'. For programmatic firing, swap "
                    "Manual Trigger for a Webhook via "
                    f"n8n_update_workflow(workflow_id='{wf_id}', ...) "
                    "and re-activate."
                )

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
                # Pre-check: manual-only workflows can't be activated.
                try:
                    wf = await _n8n_request(
                        self._config, user_id, "GET",
                        f"/api/v1/workflows/{wf_id}",
                    )
                    wf_nodes = wf.get("nodes") or []
                    has_activatable = any(
                        (n.get("type") or "") in _ACTIVATABLE_TRIGGER_TYPES
                        for n in wf_nodes if isinstance(n, dict)
                    )
                    has_manual_only = any(
                        (n.get("type") or "") in _MANUAL_ONLY_TRIGGER_TYPES
                        for n in wf_nodes if isinstance(n, dict)
                    )
                    if not has_activatable and has_manual_only:
                        return (
                            f"Error: Workflow {wf_id} only has a Manual "
                            "Trigger — n8n does NOT allow activation of "
                            "manual-only workflows. To fire programmatically, "
                            "replace Manual Trigger with a Webhook node. "
                            f"Open http://localhost:5678/workflow/{wf_id} "
                            "and click 'Execute Workflow' to run it once."
                        )
                except Exception:
                    # Pre-check is best-effort; fall through to activate.
                    pass
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
        return (
            "Trigger an n8n workflow by POSTing to its Webhook node. Requires "
            "the workflow to have a Webhook trigger and to be active. For "
            "workflows with other trigger types (Schedule, Email, RSS) you "
            "activate them and let the trigger fire naturally — n8n's public "
            "API does not support manual execution directly."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow ID to trigger",
                },
                "data": {
                    "type": "object",
                    "description": "JSON payload to POST to the webhook.",
                },
            },
            "required": ["workflow_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        wf_id = params.get("workflow_id", "").strip()
        if not wf_id:
            return "Error: workflow_id is required."
        input_data = params.get("data") or {}
        try:
            return await _trigger_via_webhook(
                self._config, user_id, wf_id, input_data,
                prefer_test_url=False, timeout=60.0,
            )
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
            wf_id = (params.get("workflow_id") or "").strip()
            changes = params.get("workflow_json")
            if not wf_id:
                return "Error: workflow_id is required."
            if not isinstance(changes, dict):
                return "Error: workflow_json must be a JSON object."

            # Fetch current workflow first
            current = await _n8n_request(
                self._config, user_id, "GET", f"/api/v1/workflows/{wf_id}",
            )

            # Merge changes into current (shallow merge; nodes/connections replace entirely)
            full = {**current, **changes}
            # n8n 1.x public API: PUT /workflows/{id} accepts ONLY these four
            # top-level fields. Everything else triggers
            # "request/body must NOT have additional properties".
            # Allowlist is the robust approach — the blocklist we had before
            # always missed whatever version-specific field n8n added next.
            merged: dict = {
                "name": full.get("name") or "Untitled",
                "nodes": full.get("nodes") or [],
                "connections": full.get("connections") or {},
                "settings": full.get("settings") or {"executionOrder": "v1"},
            }

            # Validate shape locally before hitting the API so a malformed
            # workflow_json gets a specific error instead of a bare 400.
            try:
                from lazyclaw.skills.builtin.n8n_workflow_builder import _validate_workflow
                issues = _validate_workflow(merged)
            except Exception:
                issues = []
            if issues:
                return (
                    f"Error: workflow JSON invalid (not sent to n8n): "
                    f"{'; '.join(issues[:5])}. Fix the structure and retry once."
                )

            # Node-level schema pre-flight — catches missing `operation` /
            # `resource` on googleSheets, missing webhook `path`, etc.
            # Blocks the PUT so the model sees a specific fix hint instead
            # of an opaque 400 from n8n's activation check.
            node_violations = _validate_workflow_nodes(merged.get("nodes") or [])
            if node_violations:
                return _schema_violation_error(node_violations)

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
            "Get full details of an n8n execution: per-node input/output data, "
            "full error stack traces, timing. Use include_data=true for raw logs "
            "when debugging a failing workflow."
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
                "include_data": {
                    "type": "boolean",
                    "description": (
                        "If true, includes raw per-node input/output payloads "
                        "and full error stacks. Larger response — use when "
                        "debugging a failure."
                    ),
                },
            },
            "required": ["execution_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            ex_id = params["execution_id"]
            include_data = bool(params.get("include_data", False))
            path = f"/api/v1/executions/{ex_id}"
            if include_data:
                path += "?includeData=true"
            data = await _n8n_request(self._config, user_id, "GET", path)

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

            result_data = data.get("data", {}).get("resultData", {})
            run_data = result_data.get("runData", {})
            if run_data:
                lines.append("Node Results:")
                for node_name, node_runs in run_data.items():
                    for idx, run in enumerate(node_runs):
                        node_status = run.get("executionStatus", "?")
                        run_time = run.get("executionTime")
                        time_str = f" ({run_time}ms)" if run_time is not None else ""
                        lines.append(f"  {node_name}[{idx}]: {node_status}{time_str}")

                        error = run.get("error")
                        if error:
                            err_msg = ""
                            err_stack = ""
                            if isinstance(error, dict):
                                err_msg = error.get("message", "")
                                err_stack = error.get("stack", "")
                            else:
                                err_msg = str(error)
                            if err_msg:
                                lines.append(f"    Error: {err_msg}")
                            if include_data and err_stack:
                                stack_preview = err_stack[:800]
                                lines.append(f"    Stack:\n{stack_preview}")

                        if include_data:
                            node_io = _summarize_node_io(run)
                            if node_io:
                                lines.append(node_io)

            last_error = result_data.get("error")
            if last_error:
                err_msg = (
                    last_error.get("message", str(last_error))
                    if isinstance(last_error, dict) else str(last_error)
                )
                lines.append("")
                lines.append(f"Execution Error: {err_msg}")
                if include_data and isinstance(last_error, dict):
                    stack = last_error.get("stack", "")
                    if stack:
                        lines.append(f"Stack:\n{stack[:1200]}")

            return "\n".join(lines)
        except Exception as exc:
            return _connection_error_msg(exc)


# ---------------------------------------------------------------------------
# 11. n8n_create_credential
# ---------------------------------------------------------------------------

class N8nCreateCredentialSkill(BaseSkill):
    """Create a new credential in n8n.

    For non-OAuth credentials (HTTP Header Auth, API Key, Basic Auth, plain
    user/pass) the credential is fully usable immediately. For OAuth2
    credentials, this only creates the shell — the user still has to click
    "Connect my account" in the n8n UI to complete the consent flow.
    Prefer `n8n_google_sheets_setup` for Google Sheets specifically.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_create_credential"

    @property
    def description(self) -> str:
        return (
            "Create a new credential in n8n (API keys, HTTP auth, basic auth, "
            "etc.). Use when the user says 'add a credential for X', "
            "'save the Slack token in n8n', 'create n8n API auth'. "
            "For Google Sheets OAuth use n8n_google_sheets_setup instead."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Display name shown in n8n (e.g. 'My Slack token').",
                },
                "credential_type": {
                    "type": "string",
                    "description": (
                        "n8n credential type ID. Common values: "
                        "'httpHeaderAuth', 'httpBasicAuth', 'httpQueryAuth', "
                        "'slackApi', 'openAiApi', 'notionApi'. "
                        "For OAuth2 use the helper skill instead."
                    ),
                },
                "data": {
                    "type": "object",
                    "description": (
                        "Credential data as a JSON object. Shape depends on "
                        "credential_type. E.g. for httpHeaderAuth: "
                        "{\"name\": \"Authorization\", \"value\": \"Bearer xyz\"}."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["name", "credential_type", "data"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        name = params.get("name", "").strip()
        cred_type = params.get("credential_type", "").strip()
        data = params.get("data")
        if not name or not cred_type:
            return "Error: name and credential_type are required."
        if not isinstance(data, dict):
            return "Error: data must be a JSON object."

        try:
            result = await _n8n_request(
                self._config, user_id, "POST", "/api/v1/credentials",
                body={"name": name, "type": cred_type, "data": data},
            )
        except Exception as exc:
            return _connection_error_msg(exc)

        cred_id = result.get("id") or "(unknown)"
        return (
            f"Created n8n credential '{name}' (type: {cred_type}, id: {cred_id}). "
            "For OAuth2 types, open the n8n Credentials page and click "
            "'Connect my account' to complete the consent flow."
        )


# ---------------------------------------------------------------------------
# 12. n8n_delete_credential
# ---------------------------------------------------------------------------

class N8nDeleteCredentialSkill(BaseSkill):
    """Delete a credential from n8n by ID or exact name."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_delete_credential"

    @property
    def description(self) -> str:
        return (
            "Delete a credential from n8n. Use when the user says 'remove the "
            "Slack credential', 'delete old n8n API key'. To rotate a "
            "credential, delete then call n8n_create_credential with the new value."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "credential": {
                    "type": "string",
                    "description": "Credential ID (exact) or display name (exact match).",
                },
            },
            "required": ["credential"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        needle = params.get("credential", "").strip()
        if not needle:
            return "Error: credential is required."

        # Resolve display-name to id via list if needed.
        target_id = needle
        try:
            listing = await _n8n_request(
                self._config, user_id, "GET", "/api/v1/credentials",
            )
        except Exception as exc:
            return _connection_error_msg(exc)

        rows = listing.get("data", listing) if isinstance(listing, dict) else []
        match = next(
            (
                c for c in rows
                if isinstance(c, dict) and (
                    c.get("id") == needle or c.get("name") == needle
                )
            ),
            None,
        )
        if match is not None:
            target_id = match["id"]

        try:
            await _n8n_request(
                self._config, user_id, "DELETE",
                f"/api/v1/credentials/{target_id}",
            )
        except Exception as exc:
            return _connection_error_msg(exc)
        return f"Deleted n8n credential '{needle}'."


# ---------------------------------------------------------------------------
# 13. n8n_google_sheets_setup
# ---------------------------------------------------------------------------

class N8nGoogleSheetsSetupSkill(BaseSkill):
    """Create a Google Sheets OAuth2 credential shell in n8n.

    OAuth2 requires a browser-based consent step that n8n must run itself
    (the public API cannot accept a pre-authorized refresh token). This
    skill automates everything except the final click:

    1. Pulls clientId + clientSecret from LazyClaw vault
       (keys: google_oauth_client_id, google_oauth_client_secret).
       Falls back to skill parameters if the user passes them inline.
    2. POSTs a googleSheetsOAuth2Api credential to n8n with the right scope.
    3. Returns a URL for the user to open — one click through Google consent
       and the credential is connected.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_google_sheets_setup"

    @property
    def description(self) -> str:
        return (
            "Set up Google Sheets OAuth2 for n8n. Creates the credential "
            "shell using clientId/clientSecret from vault (or parameters), "
            "then returns a URL the user opens once to grant consent. "
            "Use when the user says 'auth Google Sheets in n8n', "
            "'connect Google Sheets'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Optional display name (default: 'Google Sheets').",
                },
                "client_id": {
                    "type": "string",
                    "description": (
                        "Optional. OAuth2 clientId. If omitted, loaded from "
                        "vault key google_oauth_client_id."
                    ),
                },
                "client_secret": {
                    "type": "string",
                    "description": (
                        "Optional. OAuth2 clientSecret. If omitted, loaded "
                        "from vault key google_oauth_client_secret."
                    ),
                },
                "scope": {
                    "type": "string",
                    "description": (
                        "OAuth scope(s). Accepts a single URL or multiple "
                        "scopes separated by spaces, commas, or newlines — "
                        "automatically normalized to Google's required "
                        "space-separated format. Default: "
                        "https://www.googleapis.com/auth/spreadsheets."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        display_name = (params.get("name") or "Google Sheets").strip()
        client_id = (params.get("client_id") or "").strip()
        client_secret = (params.get("client_secret") or "").strip()
        scope = _normalize_scope(
            params.get("scope")
            or "https://www.googleapis.com/auth/spreadsheets"
        )

        # Fall back to vault for anything missing.
        if not client_id or not client_secret:
            try:
                from lazyclaw.crypto.vault import get_credential
                if not client_id:
                    client_id = (await get_credential(
                        self._config, user_id, "google_oauth_client_id",
                    )) or ""
                if not client_secret:
                    client_secret = (await get_credential(
                        self._config, user_id, "google_oauth_client_secret",
                    )) or ""
            except Exception:
                logger.debug("Failed to load Google OAuth creds from vault", exc_info=True)

        if not client_id or not client_secret:
            return (
                "Missing Google OAuth credentials. Save them first:\n"
                "  vault_set key=google_oauth_client_id value=<your-client-id>\n"
                "  vault_set key=google_oauth_client_secret value=<your-client-secret>\n"
                "Or pass them to this skill as client_id / client_secret."
            )

        body = {
            "name": display_name,
            "type": "googleSheetsOAuth2Api",
            "data": {
                "clientId": client_id,
                "clientSecret": client_secret,
                "scope": scope,
            },
        }
        try:
            result = await _n8n_request(
                self._config, user_id, "POST", "/api/v1/credentials",
                body=body,
            )
        except Exception as exc:
            return _connection_error_msg(exc)

        cred_id = result.get("id") or ""
        # n8n's UI opens the credentials list at /home/credentials. From
        # there the user clicks the new credential and hits "Connect my
        # account". We can also deep-link to the edit view on some n8n
        # builds: /home/credentials/{id} — safe to include as hint.
        base = os.getenv("N8N_PUBLIC_URL", "http://localhost:5678").rstrip("/")
        consent_url = f"{base}/home/credentials"
        if cred_id:
            consent_url = f"{base}/home/credentials/{cred_id}"

        return (
            f"Created Google Sheets OAuth2 credential '{display_name}' (id: "
            f"{cred_id or 'unknown'}).\n"
            f"Open this URL once to finish consent:\n  {consent_url}\n"
            "Click 'Connect my account' and sign in with the Google account "
            "that owns the Sheets you want to automate. After consent, the "
            "credential is ready — any Google Sheets node can select it."
        )


# ---------------------------------------------------------------------------
# 14. n8n_test_workflow — dry-run a workflow before activating
# ---------------------------------------------------------------------------

class N8nTestWorkflowSkill(BaseSkill):
    """Execute an inactive workflow once with sample data for testing.

    Distinct from n8n_run_workflow: this is framed as a preview/test step,
    returning both the execution id and a compact summary of node outputs so
    the brain can verify correctness before calling n8n_manage_workflow to
    activate. The underlying endpoint is the same — n8n runs inactive
    workflows on demand via the REST API.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_test_workflow"

    @property
    def description(self) -> str:
        return (
            "Dry-run an n8n workflow with sample data before activating. Works "
            "for workflows with a Webhook trigger: fires the webhook and "
            "returns the HTTP response body. For workflows using Schedule / "
            "Email / RSS triggers, activate first then let the trigger fire — "
            "n8n's public API can't dry-run non-webhook workflows."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow ID to test",
                },
                "data": {
                    "type": "object",
                    "description": (
                        "Sample JSON payload to POST to the webhook. Matches "
                        "what the first node would receive in production."
                    ),
                },
            },
            "required": ["workflow_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        wf_id = params.get("workflow_id", "").strip()
        if not wf_id:
            return "Error: workflow_id is required."
        input_data = params.get("data") or {}
        try:
            result = await _trigger_via_webhook(
                self._config, user_id, wf_id, input_data,
                prefer_test_url=False, timeout=60.0,
            )
        except Exception as exc:
            return _connection_error_msg(exc)

        return (
            f"{result}\n\n"
            "Next steps:\n"
            f"  - If the response looks right and the workflow isn't active yet: "
            f"n8n_manage_workflow(workflow_id=\"{wf_id}\", action=\"activate\")\n"
            "  - For per-node debug data (on failure): check n8n_list_executions "
            "and n8n_get_execution(include_data=true)."
        )


# ---------------------------------------------------------------------------
# 15. n8n_search_templates — query the community template library
# ---------------------------------------------------------------------------

class N8nSearchTemplatesSkill(BaseSkill):
    """Search n8n.io's public template library.

    Hits https://api.n8n.io/api/templates/search — no auth needed. Returns
    top matches the brain can then install via n8n_install_template.
    """

    _TEMPLATES_API = "https://api.n8n.io/api/templates/search"

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_search_templates"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Search n8n's public template library (1500+ community workflows). "
            "Returns top matching templates with their IDs. Use when built-in "
            "templates don't cover the user's request — e.g. 'find an n8n "
            "template for Notion → Slack', 'search templates for OpenAI'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms (service names, use case keywords).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10, max 25).",
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        import httpx

        query = params.get("query", "").strip()
        if not query:
            return "Error: query is required."
        limit = max(1, min(int(params.get("limit") or 10), 25))

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    self._TEMPLATES_API,
                    params={"search": query, "rows": limit},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return f"Template search failed: {exc}"

        workflows = data.get("workflows") or []
        if not workflows:
            return f"No community templates matched '{query}'."

        lines = [f"Top {len(workflows)} community templates for '{query}':", ""]
        for wf in workflows[:limit]:
            tpl_id = wf.get("id", "?")
            name = wf.get("name", "(untitled)")
            desc = (wf.get("description") or "").strip().replace("\n", " ")
            if len(desc) > 140:
                desc = desc[:137] + "..."
            nodes = wf.get("nodes") or []
            node_types = sorted({
                (n.get("name") or "").replace("n8n-nodes-base.", "")
                for n in nodes if isinstance(n, dict)
            })
            node_summary = ", ".join(node_types[:6])
            lines.append(f"- [{tpl_id}] {name}")
            if desc:
                lines.append(f"  {desc}")
            if node_summary:
                lines.append(f"  Nodes: {node_summary}")
        lines.append("")
        lines.append(
            "Install one with: n8n_install_template(template_id=<id>, activate=false)"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 16. n8n_install_template — import a community template by id
# ---------------------------------------------------------------------------

class N8nInstallTemplateSkill(BaseSkill):
    """Fetch a template from n8n.io and POST it to the user's n8n as a workflow.

    Strips read-only fields n8n rejects on POST (id, active, credentials refs
    that don't exist locally). Leaves the workflow inactive by default — the
    user/brain activates it after checking credentials.
    """

    _TEMPLATE_API = "https://api.n8n.io/api/templates/workflows/{id}"

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_install_template"

    @property
    def description(self) -> str:
        return (
            "Install an n8n community template by its template_id (from "
            "n8n_search_templates). Imports the workflow inactive so the user "
            "can wire up credentials. Use after n8n_search_templates."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "template_id": {
                    "type": "integer",
                    "description": "Template ID from n8n_search_templates results.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional display name override.",
                },
                "activate": {
                    "type": "boolean",
                    "description": (
                        "Activate immediately after import. Default false — "
                        "safer, since credentials usually need wiring first."
                    ),
                },
            },
            "required": ["template_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        import httpx

        tpl_id = params.get("template_id")
        if tpl_id is None:
            return "Error: template_id is required."
        override_name = (params.get("name") or "").strip()
        activate = bool(params.get("activate", False))

        url = self._TEMPLATE_API.format(id=tpl_id)
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                template = resp.json()
        except Exception as exc:
            return f"Failed to fetch template {tpl_id}: {exc}"

        workflow = (template.get("workflow") or {}).copy()
        if not workflow:
            return f"Template {tpl_id} has no workflow payload."

        if override_name:
            workflow["name"] = override_name
        # Same allowlist as n8n_create_workflow — n8n 1.x POST /workflows
        # only accepts these four top-level fields.
        post_body = {
            "name": workflow.get("name") or "Imported template",
            "nodes": workflow.get("nodes") or [],
            "connections": workflow.get("connections") or {},
            "settings": workflow.get("settings") or {"executionOrder": "v1"},
        }

        try:
            created = await _n8n_request(
                self._config, user_id, "POST", "/api/v1/workflows", body=post_body,
            )
        except Exception as exc:
            return _connection_error_msg(exc)

        wf_id = created.get("id", "?")
        wf_name = created.get("name", workflow.get("name", "(unnamed)"))
        lines = [
            f"Installed template {tpl_id} as workflow '{wf_name}' (id: {wf_id}).",
            "Status: inactive.",
            "Next: wire up any missing credentials in the n8n UI, then:",
            f"  n8n_test_workflow(workflow_id=\"{wf_id}\")  # dry-run",
            f"  n8n_manage_workflow(workflow_id=\"{wf_id}\", action=\"activate\")",
        ]
        if activate:
            try:
                await _n8n_request(
                    self._config, user_id, "POST",
                    f"/api/v1/workflows/{wf_id}/activate",
                )
                lines[1] = "Status: active."
            except Exception as exc:
                lines.append(f"Activation failed: {exc}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 17. n8n_list_webhooks — discover public webhook URLs
# ---------------------------------------------------------------------------

class N8nListWebhooksSkill(BaseSkill):
    """List every Webhook node across all workflows with its public URL.

    Useful when the user (or the brain) needs to hand out a webhook URL to an
    external service — Stripe, Calendly, GitHub — without digging through the
    n8n UI.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_list_webhooks"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "List all webhook URLs exposed by n8n workflows, with method and "
            "path. Use when an external service (Stripe, GitHub, Calendly) "
            "needs a webhook URL to POST to, or when the user asks 'what "
            "webhooks do I have?'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            listing = await _n8n_request(
                self._config, user_id, "GET", "/api/v1/workflows",
            )
        except Exception as exc:
            return _connection_error_msg(exc)

        rows = listing.get("data", listing) if isinstance(listing, dict) else []
        if not rows:
            return "No workflows found."

        public_base = os.getenv("N8N_PUBLIC_URL", "http://localhost:5678").rstrip("/")
        webhook_base = os.getenv("N8N_WEBHOOK_BASE_URL", public_base).rstrip("/")

        entries: list[str] = []
        for wf in rows:
            if not isinstance(wf, dict):
                continue
            wf_id = wf.get("id", "?")
            wf_name = wf.get("name", "(unnamed)")
            active = wf.get("active", False)
            for node in wf.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                node_type = node.get("type", "")
                if node_type != "n8n-nodes-base.webhook":
                    continue
                node_params = node.get("parameters") or {}
                path = (node_params.get("path") or "").strip("/")
                method = (node_params.get("httpMethod") or "POST").upper()
                production_url = f"{webhook_base}/webhook/{path}" if path else f"{webhook_base}/webhook/<unset>"
                test_url = f"{webhook_base}/webhook-test/{path}" if path else f"{webhook_base}/webhook-test/<unset>"
                flag = "active" if active else "INACTIVE"
                entries.append(
                    f"- {wf_name} (id: {wf_id}) [{flag}] "
                    f"node '{node.get('name', '?')}'\n"
                    f"  {method} {production_url}\n"
                    f"  test:    {test_url}"
                )

        if not entries:
            return "No webhook nodes found in any workflow."
        return (
            f"Webhook endpoints ({len(entries)} total):\n"
            "(inactive workflows only receive on the /webhook-test/* URL until activated)\n\n"
            + "\n".join(entries)
        )


# ---------------------------------------------------------------------------
# 18. n8n_google_oauth_setup — generic multi-scope Google OAuth credential
# ---------------------------------------------------------------------------

class N8nGoogleOAuthSetupSkill(BaseSkill):
    """Create a multi-scope googleOAuth2Api credential for any Google service.

    Difference from n8n_google_sheets_setup: this creates the generic
    googleOAuth2Api type — usable from HTTP Request nodes calling any Google
    API (Gmail, Drive, Sheets, Calendar, YouTube, Analytics, Search Console,
    Custom Search) with one OAuth token. Sensible multi-scope default:
    userinfo + Sheets + Drive + Gmail + Calendar + YouTube-read +
    Analytics-read + Search-Console-read + Custom-Search.

    Scope input is auto-normalized — paste comma- or newline-separated lists
    and it still works. That fixes the "invalid_scope" error from trailing
    commas that Google otherwise rejects.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_google_oauth_setup"

    @property
    def description(self) -> str:
        return (
            "Set up a generic multi-scope Google OAuth2 credential in n8n "
            "(type: googleOAuth2Api). Default scopes cover Gmail, Drive, "
            "Sheets, Calendar, YouTube, Analytics, Search Console, Custom "
            "Search, and userinfo. Use when the user asks to 'connect "
            "Google', 'auth all Google services', or needs cross-service "
            "flows. Scope input is whitespace/comma/newline tolerant — paste "
            "any format. For Sheets-only use n8n_google_sheets_setup."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Credential display name. Default: 'Google (all scopes)'.",
                },
                "client_id": {
                    "type": "string",
                    "description": (
                        "OAuth2 clientId. If omitted, loaded from vault key "
                        "google_oauth_client_id."
                    ),
                },
                "client_secret": {
                    "type": "string",
                    "description": (
                        "OAuth2 clientSecret. If omitted, loaded from vault "
                        "key google_oauth_client_secret."
                    ),
                },
                "scopes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of OAuth scope URLs. If omitted, uses the "
                        "curated multi-service default (Gmail, Drive, Sheets, "
                        "Calendar, YouTube, Analytics, Search Console, "
                        "Custom Search, userinfo)."
                    ),
                },
                "scope": {
                    "type": "string",
                    "description": (
                        "Alternative to 'scopes': raw scope string. Accepts "
                        "commas, newlines, tabs, or spaces between entries — "
                        "normalized before sending. Prefer 'scopes' array."
                    ),
                },
                "extra_scopes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Append these scopes to the default list. Ignored "
                        "when 'scopes' or 'scope' is explicitly set."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        display_name = (params.get("name") or "Google (all scopes)").strip()
        client_id = (params.get("client_id") or "").strip()
        client_secret = (params.get("client_secret") or "").strip()

        raw_scopes = params.get("scopes")
        raw_scope = params.get("scope")
        extra = params.get("extra_scopes") or []

        if raw_scopes:
            scope = _normalize_scope(list(raw_scopes))
        elif raw_scope:
            scope = _normalize_scope(raw_scope)
        else:
            combined = list(_DEFAULT_GOOGLE_SCOPES) + [str(s) for s in extra if s]
            scope = _normalize_scope(combined)

        if not scope:
            return "Error: at least one scope is required."

        if not client_id or not client_secret:
            try:
                from lazyclaw.crypto.vault import get_credential
                if not client_id:
                    client_id = (await get_credential(
                        self._config, user_id, "google_oauth_client_id",
                    )) or ""
                if not client_secret:
                    client_secret = (await get_credential(
                        self._config, user_id, "google_oauth_client_secret",
                    )) or ""
            except Exception:
                logger.debug("Failed to load Google OAuth creds from vault", exc_info=True)

        if not client_id or not client_secret:
            return (
                "Missing Google OAuth credentials. Save them first:\n"
                "  vault_set key=google_oauth_client_id value=<your-client-id>\n"
                "  vault_set key=google_oauth_client_secret value=<your-client-secret>\n"
                "Or pass them to this skill as client_id / client_secret."
            )

        body = {
            "name": display_name,
            "type": "googleOAuth2Api",
            "data": {
                "clientId": client_id,
                "clientSecret": client_secret,
                "scope": scope,
                "authUrl": "https://accounts.google.com/o/oauth2/v2/auth",
                "accessTokenUrl": "https://oauth2.googleapis.com/token",
                "authQueryParameters": (
                    "access_type=offline"
                    "&prompt=select_account%20consent"
                    "&include_granted_scopes=true"
                ),
                "authentication": "header",
            },
        }
        try:
            result = await _n8n_request(
                self._config, user_id, "POST", "/api/v1/credentials", body=body,
            )
        except Exception as exc:
            return _connection_error_msg(exc)

        cred_id = result.get("id") or ""
        base = os.getenv("N8N_PUBLIC_URL", "http://localhost:5678").rstrip("/")
        consent_url = f"{base}/home/credentials/{cred_id}" if cred_id else f"{base}/home/credentials"

        scope_list = scope.split(" ")
        scope_preview = "\n".join(f"  - {s}" for s in scope_list)

        return (
            f"Created Google OAuth2 credential '{display_name}' "
            f"(id: {cred_id or 'unknown'}, type: googleOAuth2Api).\n"
            f"Scopes ({len(scope_list)}):\n{scope_preview}\n\n"
            f"Finish consent here:\n  {consent_url}\n"
            "Click 'Connect my account' and sign in with the Google account "
            "that owns the services you want to control.\n\n"
            "Before clicking, make sure in Google Cloud Console:\n"
            "  1. Each requested API is ENABLED for your project.\n"
            "  2. OAuth consent screen has each scope added.\n"
            "  3. Your Google account is listed under 'Test users' (while the "
            "app is in Testing mode — required for restricted scopes like "
            "gmail.modify and drive).\n\n"
            "If this errors with 'invalid_scope' before the chooser appears, "
            "your OAuth client's project doesn't have all the APIs enabled. "
            "Use n8n_google_services_setup instead — it creates per-service "
            "credentials that piggyback on n8n's built-in OAuth and don't "
            "need any Google Cloud Console setup."
        )


# ---------------------------------------------------------------------------
# 19. n8n_google_services_setup — per-service Google OAuth credential shells
# ---------------------------------------------------------------------------

# Mapping: service keyword -> (n8n credential type, default display name,
# extra scopes to inject for generic googleOAuth2Api types only).
# All type names verified against n8n 2.14.x dist/credentials/ on disk.
_GOOGLE_SERVICE_CREDENTIAL_TYPES: dict[str, tuple[str, str, str]] = {
    "gmail":         ("gmailOAuth2Api",             "Gmail",                ""),
    "drive":         ("googleDriveOAuth2Api",       "Google Drive",         ""),
    "sheets":        ("googleSheetsOAuth2Api",      "Google Sheets",        ""),
    "calendar":      ("googleCalendarOAuth2Api",    "Google Calendar",      ""),
    "youtube":       ("youTubeOAuth2Api",           "YouTube",              ""),
    "analytics":     ("googleAnalyticsOAuth2Api",   "Google Analytics",     ""),
    "docs":          ("googleDocsOAuth2Api",        "Google Docs",          ""),
    "slides":        ("googleSlidesOAuth2Api",      "Google Slides",        ""),
    "contacts":      ("googleContactsOAuth2Api",    "Google Contacts",      ""),
    "tasks":         ("googleTasksOAuth2Api",       "Google Tasks",         ""),
    "translate":     ("googleTranslateOAuth2Api",   "Google Translate",     ""),
    "chat":          ("googleChatOAuth2Api",        "Google Chat",          ""),
    "bigquery":      ("googleBigQueryOAuth2Api",    "Google BigQuery",      ""),
    "ads":           ("googleAdsOAuth2Api",         "Google Ads",           ""),
    # Search Console has no dedicated n8n credential — fall back to the
    # generic googleOAuth2Api with webmasters scopes. Used with HTTP Request
    # nodes against the Search Console API.
    "searchconsole": (
        "googleOAuth2Api",
        "Google Search Console",
        "https://www.googleapis.com/auth/webmasters.readonly",
    ),
}


class N8nGoogleServicesSetupSkill(BaseSkill):
    """Batch-create per-service Google credential shells.

    This bypasses the generic googleOAuth2Api flow (which fails with
    'invalid_scope' when the user's own OAuth client doesn't have all APIs
    enabled). Instead it creates ONE credential per service — each uses
    n8n's pre-verified OAuth app under the hood, so the user can click
    'Sign in with Google' in the n8n UI to finish auth without touching
    Google Cloud Console at all.

    Trade-off: one credential per service to click through, but each one
    takes ~15 seconds and works immediately.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "n8n"

    @property
    def name(self) -> str:
        return "n8n_google_services_setup"

    @property
    def description(self) -> str:
        return (
            "Create per-service Google OAuth2 credential shells in n8n for "
            "a list of services (gmail, drive, sheets, calendar, youtube, "
            "analytics, searchconsole, docs, contacts, tasks, translate). "
            "Each uses n8n's built-in OAuth app — no Google Cloud Console "
            "setup needed, no invalid_scope errors. Use this when the "
            "user asks to 'connect all Google services' or when the generic "
            "n8n_google_oauth_setup fails with invalid_scope. Returns a "
            "consent URL per credential; the user opens each and clicks "
            "'Sign in with Google' once."
        )

    @property
    def parameters_schema(self) -> dict:
        supported = ", ".join(sorted(_GOOGLE_SERVICE_CREDENTIAL_TYPES.keys()))
        return {
            "type": "object",
            "properties": {
                "services": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        f"Services to set up. Supported: {supported}. "
                        "Omit for the default set (gmail, drive, sheets, "
                        "calendar, youtube, analytics, searchconsole)."
                    ),
                },
                "prefix": {
                    "type": "string",
                    "description": (
                        "Optional display-name prefix. E.g. 'Hirossa' → "
                        "'Hirossa Gmail', 'Hirossa Google Drive'. Useful "
                        "for users with multiple Google accounts."
                    ),
                },
                "client_id": {
                    "type": "string",
                    "description": (
                        "Optional. Your own OAuth clientId. If omitted, "
                        "the credential is created as a shell and n8n's "
                        "built-in Sign-in-with-Google button is used."
                    ),
                },
                "client_secret": {
                    "type": "string",
                    "description": (
                        "Optional. Your own OAuth clientSecret. Paired with "
                        "client_id."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        requested = params.get("services")
        if not requested:
            # Search Console has no dedicated n8n credential (confirmed in
            # n8n 2.14.x dist/credentials). Excluded from defaults. For
            # Search Console data, use an HTTP Request node + your own
            # googleOAuth2Api credential, or scrape via the browser skill.
            requested = [
                "gmail", "drive", "sheets", "calendar",
                "youtube", "analytics", "docs",
            ]
        prefix = (params.get("prefix") or "").strip()
        client_id = (params.get("client_id") or "").strip()
        client_secret = (params.get("client_secret") or "").strip()

        unknown = [s for s in requested if s not in _GOOGLE_SERVICE_CREDENTIAL_TYPES]
        if unknown:
            return (
                f"Unknown services: {unknown}. Supported: "
                f"{sorted(_GOOGLE_SERVICE_CREDENTIAL_TYPES.keys())}"
            )

        base = os.getenv("N8N_PUBLIC_URL", "http://localhost:5678").rstrip("/")

        results: list[str] = []
        errors: list[str] = []
        for key in requested:
            cred_type, default_label, extra_scope = _GOOGLE_SERVICE_CREDENTIAL_TYPES[key]
            display_name = f"{prefix} {default_label}".strip() if prefix else default_label

            data: dict = {}
            if client_id and client_secret:
                data["clientId"] = client_id
                data["clientSecret"] = client_secret

            # Generic googleOAuth2Api types (e.g. the Search Console fallback)
            # need scope + endpoint config; dedicated per-service types have
            # those baked in.
            if cred_type == "googleOAuth2Api" and extra_scope:
                data["scope"] = _normalize_scope(extra_scope)
                data["authUrl"] = "https://accounts.google.com/o/oauth2/v2/auth"
                data["accessTokenUrl"] = "https://oauth2.googleapis.com/token"
                data["authQueryParameters"] = (
                    "access_type=offline&prompt=select_account%20consent"
                )
                data["authentication"] = "header"

            body = {"name": display_name, "type": cred_type, "data": data}
            try:
                result = await _n8n_request(
                    self._config, user_id, "POST", "/api/v1/credentials", body=body,
                )
            except Exception as exc:
                errors.append(f"  - {display_name}: {exc}")
                continue

            cred_id = result.get("id") or ""
            consent = f"{base}/home/credentials/{cred_id}" if cred_id else f"{base}/home/credentials"
            results.append(
                f"  - {display_name} (id: {cred_id or '?'}): {consent}"
            )

        lines = [
            f"Created {len(results)} per-service Google credential(s) in n8n.",
            "",
            "Open each URL → click 'Sign in with Google' (or 'Connect my "
            "account') → pick your Google account → done.",
            "",
            "Credentials:",
            *results,
        ]
        if errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(errors)
        if not client_id:
            lines.append("")
            lines.append(
                "Note: these credentials were created without your own "
                "client_id, so they use n8n's built-in OAuth app. That "
                "avoids the 'invalid_scope' error from custom OAuth "
                "clients with missing APIs."
            )
        return "\n".join(lines)



