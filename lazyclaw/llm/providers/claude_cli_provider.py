"""Claude Code CLI as an LLM provider.

Routes LLM calls through `claude -p` (covered by Claude Code subscription).
Tool calling is prompt-engineered: tool schemas are injected into the system
prompt, Claude responds with [TOOL_CALL] tags, and we parse them back into
ToolCall objects.

Session persistence via --session-id / --resume enables multi-turn context
within a single agentic loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import uuid
from typing import Any

from lazyclaw.llm.providers.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    StreamChunk,
    ToolCall,
)

logger = logging.getLogger(__name__)

_TIMEOUT_S = 120
_TOOL_CALL_PATTERN = re.compile(
    r"\[TOOL_CALL\](.*?)\[/TOOL_CALL\]",
    re.DOTALL,
)

# System prompt fragment that teaches Claude how to call tools
_TOOL_CALLING_INSTRUCTIONS = """
## Available Tools

To use a tool, output ONE [TOOL_CALL] tag per tool:

[TOOL_CALL]{"name": "tool_name", "arguments": {"param": "value"}}[/TOOL_CALL]

CRITICAL RULES:
- Each tool call MUST be in its OWN [TOOL_CALL]...[/TOOL_CALL] tags
- NEVER put multiple JSON objects inside one [TOOL_CALL] tag
- Do NOT output any text before the first [TOOL_CALL] when calling tools
- If you don't need any tools, respond with plain text only (no tags)
- ONLY use tools listed below. Do NOT invent tool names.

### Tool Definitions

"""


def _derive_session_id(user_id: str, context_id: str) -> str:
    """Derive a deterministic UUID for a session context."""
    key = f"lazyclaw:{user_id}:{context_id}"
    h = hashlib.sha256(key.encode()).hexdigest()
    return str(uuid.UUID(h[:32]))


def _serialize_tools(tools: list[dict]) -> str:
    """Serialize OpenAI-format tool dicts into a compact text block."""
    lines: list[str] = []
    for tool in tools:
        func = tool.get("function", {})
        name = func.get("name", "unknown")
        desc = func.get("description", "")
        params = func.get("parameters", {})
        props = params.get("properties", {})
        required = params.get("required", [])

        param_lines: list[str] = []
        for pname, pdef in props.items():
            ptype = pdef.get("type", "string")
            pdesc = pdef.get("description", "")
            req = " (required)" if pname in required else ""
            param_lines.append(f"    - {pname}: {ptype}{req} — {pdesc}")

        lines.append(f"**{name}** — {desc}")
        if param_lines:
            lines.append("  Parameters:")
            lines.extend(param_lines)
        lines.append("")

    return "\n".join(lines)


def _serialize_messages(messages: list[LLMMessage]) -> str:
    """Serialize conversation messages into a text prompt.

    System messages become context blocks. Tool results become labeled
    sections. Assistant tool_calls become [TOOL_CALL] blocks.
    """
    parts: list[str] = []

    for msg in messages:
        if msg.role == "system":
            parts.append(f"[System Context]\n{msg.content}\n")
        elif msg.role == "user":
            parts.append(f"[User]\n{msg.content}\n")
        elif msg.role == "assistant":
            if msg.tool_calls:
                tc_text = "\n".join(
                    f'[TOOL_CALL]{{"name": "{tc.name}", '
                    f'"arguments": {json.dumps(tc.arguments)}}}[/TOOL_CALL]'
                    for tc in msg.tool_calls
                )
                if msg.content:
                    parts.append(f"[Assistant]\n{msg.content}\n{tc_text}\n")
                else:
                    parts.append(f"[Assistant]\n{tc_text}\n")
            else:
                parts.append(f"[Assistant]\n{msg.content}\n")
        elif msg.role == "tool":
            tool_id = msg.tool_call_id or "unknown"
            parts.append(f"[Tool Result: {tool_id}]\n{msg.content}\n")

    return "\n".join(parts)


def _extract_json_objects(raw: str) -> list[dict]:
    """Extract all top-level JSON objects from a string.

    Handles: single object, concatenated objects, objects separated
    by whitespace/newlines, and objects wrapped in markdown fences.
    """
    # Strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = cleaned.rstrip("`").strip()

    objects: list[dict] = []
    i = 0
    while i < len(cleaned):
        if cleaned[i] == "{":
            # Find matching closing brace via counting
            depth = 0
            start = i
            in_string = False
            escape_next = False
            for j in range(i, len(cleaned)):
                c = cleaned[j]
                if escape_next:
                    escape_next = False
                    continue
                if c == "\\":
                    escape_next = True
                    continue
                if c == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(cleaned[start : j + 1])
                            objects.append(obj)
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            else:
                break  # Unmatched brace, stop
        else:
            i += 1

    return objects


def _parse_tool_calls(text: str) -> tuple[str, list[ToolCall]]:
    """Extract [TOOL_CALL] blocks from response text.

    Handles edge cases:
    - Multiple JSON objects in one [TOOL_CALL] block
    - Nested braces in arguments
    - Markdown code fences around JSON

    Returns (remaining_text, list_of_tool_calls).
    """
    matches = _TOOL_CALL_PATTERN.findall(text)
    if not matches:
        return text.strip(), []

    tool_calls: list[ToolCall] = []
    for raw_content in matches:
        objects = _extract_json_objects(raw_content.strip())
        for data in objects:
            name = data.get("name")
            if not name:
                continue
            tc = ToolCall(
                id=f"cli_{uuid.uuid4().hex[:8]}",
                name=name,
                arguments=data.get("arguments", {}),
            )
            tool_calls.append(tc)

    if not tool_calls and matches:
        logger.warning(
            "Found %d [TOOL_CALL] blocks but parsed 0 tool calls", len(matches)
        )

    # Remove tool call tags from text
    remaining = _TOOL_CALL_PATTERN.sub("", text).strip()
    return remaining, tool_calls


class ClaudeCLIProvider(BaseLLMProvider):
    """LLM provider that routes through the `claude` CLI.

    All calls use the user's Claude Code subscription ($0 extra).
    Tool calling is done via prompt engineering with [TOOL_CALL] tags.
    """

    def __init__(self, claude_bin: str | None = None, model: str = "sonnet") -> None:
        self._claude_bin = claude_bin or shutil.which("claude") or "claude"
        self._model = model
        self._active_sessions: dict[str, bool] = {}  # session_id → has_been_used

    async def verify_key(self) -> bool:
        """Check if claude CLI is available."""
        return await self.health_check()

    async def health_check(self) -> bool:
        """Verify claude CLI is installed and accessible."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._claude_bin, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return False

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str = "",
        **kwargs: Any,
    ) -> LLMResponse:
        """Send messages through claude -p and return structured response.

        Args:
            messages: Conversation messages.
            model: Ignored (uses self._model for the CLI).
            **kwargs: tools (list[dict]), session_id (str), etc.
        """
        tools: list[dict] = kwargs.pop("tools", None) or []
        session_id: str | None = kwargs.pop("session_id", None)

        # Build the prompt from messages
        prompt_text = _serialize_messages(messages)

        # Build CLI args — use --system-prompt (override) when tools
        # are present to prevent Claude Code's own system prompt from
        # leaking tool names (Read, Edit, Bash) that confuse routing.
        args = [
            self._claude_bin, "-p", prompt_text,
            "--output-format", "json",
            "--tools", "",  # Disable Claude Code's built-in tools
            "--model", self._model,
        ]

        # Always override Claude Code's system prompt to prevent its
        # built-in tools (Read, Edit, Bash) from leaking into responses.
        # SOUL.md and capabilities are already in the prompt text as
        # [System Context] blocks via _serialize_messages().
        if tools:
            tool_system = (
                "You are LazyClaw, an AI agent. The user's instructions "
                "and your capabilities are in the [System Context] blocks "
                "in the conversation. Follow those rules.\n\n"
                "CRITICAL: You are NOT Claude Code. Do NOT call tools named "
                "Read, Edit, Bash, Grep, Write, Glob, WebSearch, WebFetch, "
                "Agent, or any Claude Code tool. They do NOT exist. "
                "ONLY call tools from the list below.\n\n"
                + _TOOL_CALLING_INSTRUCTIONS
                + _serialize_tools(tools)
            )
            args.extend(["--system-prompt", tool_system])
        else:
            args.extend([
                "--system-prompt",
                "You are LazyClaw, an AI agent. The user's instructions "
                "and your capabilities are in the [System Context] blocks "
                "in the conversation. Follow those rules. "
                "Respond concisely. Do NOT use any tools.",
            ])

        # Session management
        if session_id:
            if self._active_sessions.get(session_id):
                args.extend(["--resume", session_id])
            else:
                args.extend(["--session-id", session_id])
                self._active_sessions[session_id] = True

        # Suppress session persistence for stateless calls
        if not session_id:
            args.append("--no-session-persistence")

        logger.debug("Claude CLI call: tools=%d, model=%s", len(tools), self._model)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error("Claude CLI timed out after %ds", _TIMEOUT_S)
            raise RuntimeError(f"Claude CLI timed out after {_TIMEOUT_S}s")
        except FileNotFoundError:
            raise RuntimeError(
                "claude CLI not found. Install Claude Code: "
                "https://docs.anthropic.com/en/docs/claude-code"
            )

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            logger.error("Claude CLI failed (exit %d): %s", proc.returncode, err)
            raise RuntimeError(f"Claude CLI error: {err}")

        raw = stdout.decode("utf-8", errors="replace").strip()
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> LLMResponse:
        """Parse claude -p --output-format json response."""
        # Try JSON parse first
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: treat as plain text
            logger.warning("Claude CLI returned non-JSON, treating as text")
            remaining, tool_calls = _parse_tool_calls(raw)
            return LLMResponse(
                content=remaining,
                model=f"claude-cli ({self._model})",
                tool_calls=tool_calls or None,
            )

        result_text = data.get("result", "")
        usage_raw = data.get("usage", {})
        # CLI reports API cost but it's covered by subscription — always $0
        cost = 0.0

        # Strip Claude Code artifacts that sometimes leak into output
        result_text = re.sub(
            r"<system-reminder>.*?</system-reminder>", "", result_text, flags=re.DOTALL
        ).strip()

        # Parse usage into standard format
        usage = {
            "prompt_tokens": usage_raw.get("input_tokens", 0),
            "completion_tokens": usage_raw.get("output_tokens", 0),
            "total_tokens": (
                usage_raw.get("input_tokens", 0)
                + usage_raw.get("output_tokens", 0)
            ),
            "cost_usd": cost,
            "provider": "claude_cli",
        }

        # Check for tool calls in the result text
        remaining, tool_calls = _parse_tool_calls(result_text)

        if tool_calls:
            logger.info(
                "Claude CLI parsed %d tool calls: %s",
                len(tool_calls),
                [tc.name for tc in tool_calls],
            )
        elif "[TOOL_CALL]" in result_text:
            # Tag present but parser didn't match — log for debugging
            logger.warning(
                "Claude CLI: [TOOL_CALL] found in text but parser returned 0 calls. "
                "First 200 chars: %s",
                result_text[:200],
            )

        return LLMResponse(
            content=remaining,
            model=f"claude-cli ({self._model})",
            usage=usage,
            tool_calls=tool_calls or None,
        )

    async def stream_chat(
        self, messages: list[LLMMessage], model: str = "", **kwargs: Any
    ):
        """Stream not natively supported — falls back to chat + single chunk."""
        response = await self.chat(messages, model, **kwargs)
        yield StreamChunk(
            delta=response.content,
            tool_calls=response.tool_calls,
            usage=response.usage,
            model=response.model,
            done=True,
        )
