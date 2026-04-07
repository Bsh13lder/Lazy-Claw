"""LLM-based n8n workflow JSON generation.

When no pre-built template matches the user's description,
this module asks the LLM to generate the full n8n workflow JSON.

Follows the same pattern as lazyclaw/skills/writer.py.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an n8n workflow generator. Given a user's description of what they want
automated, output a VALID n8n workflow JSON object that can be POSTed to
the n8n REST API at POST /api/v1/workflows.

Output ONLY the JSON object — no markdown fences, no explanation.

The JSON must have these top-level keys:
  "name": string — human-readable workflow name
  "nodes": array of node objects
  "connections": object mapping source node names to outputs
  "settings": {"executionOrder": "v1"}

Each node object needs:
  "parameters": dict of node-specific params
  "id": unique string (e.g. "node-1")
  "name": display name
  "type": n8n node type (e.g. "n8n-nodes-base.webhook")
  "typeVersion": number (use latest known)
  "position": [x, y] for canvas layout

Common n8n node types you can use:
  Triggers: scheduleTrigger, webhook, emailReadImap, rssFeedReadTrigger,
            googleDriveTrigger, telegramTrigger
  Actions: telegram, gmail, googleSheets, httpRequest, slack, discord,
           googleDrive, notion, airtable, code
  Logic: if, switch, merge, splitInBatches, wait, noOp

For credentials, use placeholder format:
  "credentials": {"telegramApi": {"id": "", "name": "Telegram"}}
The user will configure actual credentials in the n8n UI.

Keep workflows simple — prefer fewer nodes. Use the Code node for custom logic.
"""


def _parse_workflow_json(content: str) -> dict:
    """Extract workflow JSON from LLM output."""
    # Strip markdown fences if present
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if json_match:
        content = json_match.group(1).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.debug("Direct JSON parse failed for workflow output, trying brace extraction")

    brace_match = re.search(r"\{.*\}", content, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            logger.debug("Brace-extracted JSON parse also failed for workflow output")

    raise ValueError("Could not parse workflow JSON from LLM response")


def _validate_workflow(wf: dict) -> list[str]:
    """Basic validation of workflow structure. Returns list of issues."""
    issues: list[str] = []
    if not isinstance(wf, dict):
        issues.append("Workflow must be a JSON object")
        return issues
    if "nodes" not in wf or not isinstance(wf.get("nodes"), list):
        issues.append("Missing or invalid 'nodes' array")
    if "connections" not in wf:
        issues.append("Missing 'connections' object")
    if not wf.get("name"):
        issues.append("Missing 'name' field")
    for i, node in enumerate(wf.get("nodes", [])):
        if not node.get("type"):
            issues.append(f"Node {i} missing 'type'")
        if not node.get("name"):
            issues.append(f"Node {i} missing 'name'")
    return issues


async def generate_workflow_json(
    config,
    user_id: str,
    description: str,
    name: str | None = None,
) -> dict:
    """Use LLM to generate an n8n workflow from a natural language description.

    Returns a dict ready to POST to /api/v1/workflows.
    Raises ValueError if generation or validation fails after retries.
    """
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    router = LLMRouter(config)

    user_msg = f"Create an n8n workflow: {description}"
    if name:
        user_msg += f"\nWorkflow name: {name}"

    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_msg),
    ]

    max_attempts = 2
    last_error = ""

    for attempt in range(max_attempts):
        if attempt > 0 and last_error:
            messages.append(LLMMessage(
                role="user",
                content=f"The previous output had issues: {last_error}. Fix and output valid JSON only.",
            ))

        response = await router.chat(messages, user_id=user_id)
        content = response.content if hasattr(response, "content") else str(response)

        try:
            workflow = _parse_workflow_json(content)
        except ValueError as exc:
            last_error = str(exc)
            logger.warning("n8n workflow gen attempt %d: parse failed: %s", attempt + 1, exc)
            continue

        issues = _validate_workflow(workflow)
        if issues:
            last_error = "; ".join(issues)
            logger.warning("n8n workflow gen attempt %d: validation: %s", attempt + 1, last_error)
            continue

        # Ensure settings
        workflow.setdefault("settings", {"executionOrder": "v1"})
        if name and not workflow.get("name"):
            workflow["name"] = name

        return workflow

    raise ValueError(f"Failed to generate valid n8n workflow after {max_attempts} attempts: {last_error}")
