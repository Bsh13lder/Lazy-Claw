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
    """Structural validation of an n8n workflow JSON.

    Checks:
      - top-level shape (name, nodes, connections)
      - every node has id/name/type
      - node names are unique (connections reference nodes by name)
      - every connection source/target references an existing node
      - type strings look like an n8n node type (namespace.nodeName)
    Returns list of issues (empty list = valid).
    """
    issues: list[str] = []
    if not isinstance(wf, dict):
        issues.append("Workflow must be a JSON object")
        return issues
    if "nodes" not in wf or not isinstance(wf.get("nodes"), list):
        issues.append("Missing or invalid 'nodes' array")
    if "connections" not in wf or not isinstance(wf.get("connections"), dict):
        issues.append("Missing or invalid 'connections' object")
    if not wf.get("name"):
        issues.append("Missing 'name' field")

    nodes = wf.get("nodes", []) if isinstance(wf.get("nodes"), list) else []
    node_names: set[str] = set()
    seen_names: set[str] = set()

    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            issues.append(f"Node {i} is not an object")
            continue
        name = node.get("name")
        node_type = node.get("type")
        if not node_type:
            issues.append(f"Node {i} missing 'type'")
        elif not isinstance(node_type, str) or "." not in node_type:
            issues.append(
                f"Node {i} ({name or '?'}) has invalid type '{node_type}' — "
                "expected format 'n8n-nodes-base.<nodeName>'"
            )
        if not name:
            issues.append(f"Node {i} missing 'name'")
        else:
            if name in seen_names:
                issues.append(f"Duplicate node name '{name}' — node names must be unique")
            seen_names.add(name)
            node_names.add(name)
        if not node.get("id"):
            issues.append(f"Node {i} ({name or '?'}) missing 'id'")

    connections = wf.get("connections", {})
    if isinstance(connections, dict):
        for source_name, outputs in connections.items():
            if source_name not in node_names:
                issues.append(
                    f"Connection source '{source_name}' does not match any node"
                )
                continue
            if not isinstance(outputs, dict):
                issues.append(f"Connection '{source_name}' has invalid shape")
                continue
            for output_type, branches in outputs.items():
                if not isinstance(branches, list):
                    continue
                for branch in branches:
                    if not isinstance(branch, list):
                        continue
                    for link in branch:
                        if not isinstance(link, dict):
                            continue
                        target = link.get("node")
                        if target and target not in node_names:
                            issues.append(
                                f"Connection {source_name} → '{target}' "
                                "points to a node that does not exist"
                            )
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
    from lazyclaw.llm.eco_router import EcoRouter, ROLE_BRAIN
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    # Route through EcoRouter with ROLE_BRAIN so workflow generation
    # uses the SAME model the user is already talking to. EcoRouter
    # resolves brain by reading the user's per-user settings (set via
    # `/mode`), so MiniMax users get MiniMax, CLAUDE-mode users get
    # Haiku-via-CLI, HYBRID users get Sonnet.
    #
    # Do NOT route to ROLE_WORKER — that resolves to gemma4 / Haiku
    # regardless of what the user picked, bypassing their explicit
    # model choice.
    paid_router = LLMRouter(config)
    router = EcoRouter(config, paid_router)

    # Learning loop: pull known-good past shapes for this kind of task
    # and prepend them as few-shot exemplars. This is what lets a small
    # model that doesn't memorize n8n's schema still emit correct JSON —
    # the product supplies the memory instead of the weights.
    exemplars = ""
    try:
        from lazyclaw.runtime.skill_lesson import (
            recall_skill_lessons, format_lessons_as_exemplars,
        )
        past = await recall_skill_lessons(
            config, user_id, topic="n8n", intent=description, k=3,
        )
        exemplars = format_lessons_as_exemplars(past)
    except Exception:
        logger.debug("n8n lesson recall failed", exc_info=True)

    user_msg = f"Create an n8n workflow: {description}"
    if name:
        user_msg += f"\nWorkflow name: {name}"
    if exemplars:
        user_msg = f"{exemplars}\n\n{user_msg}"

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

        try:
            response = await router.chat(messages, user_id=user_id, role=ROLE_BRAIN)
        except Exception as chat_exc:
            # Detect Anthropic credit exhaustion and surface a clear
            # billing error instead of falling through to the minimal
            # webhook scaffold — which would silently activate an empty
            # workflow and let the model claim success (see 20:14:38
            # log for the failure mode this guards against).
            msg = str(chat_exc).lower()
            if (
                "credit balance is too low" in msg
                or "insufficient credit" in msg
                or "billing" in msg
            ):
                raise ValueError(
                    "BILLING: Your Anthropic API credit balance is empty. "
                    "Top up at https://console.anthropic.com/settings/billing, "
                    "or switch to MiniMax / local via `/mode`. Workflow "
                    "generation can't proceed until the brain model is "
                    "reachable."
                ) from chat_exc
            raise
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
