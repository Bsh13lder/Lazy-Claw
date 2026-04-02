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
import os
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

_TIMEOUT_S = 45  # Reduced from 120 — retry on timeout instead of blocking
_MAX_RETRIES = 2  # Retry once before giving up (total 2 attempts)
_WARM_POOL_SIZE = 1  # Pre-warmed processes ready for instant use
_WARM_EXPIRE_S = 60  # Kill warm process if unused after 60s
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
- NEVER report numbers, stats, or data from memory or previous messages. If the user asks for live data (follower counts, message counts, status), you MUST call a tool. If no tool exists for it, say "I don't have a tool to check that" — NEVER guess or repeat old numbers.

### Tool Definitions

"""


def _derive_session_id(user_id: str, context_id: str) -> str:
    """Derive a deterministic UUID for a session context."""
    key = f"lazyclaw:{user_id}:{context_id}"
    h = hashlib.sha256(key.encode()).hexdigest()
    return str(uuid.UUID(h[:32]))


# MCP UUID prefix pattern: mcp_<uuid>_<tool_name>
_MCP_UUID_RE = re.compile(r"^mcp_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_")


def _shorten_tool_name(name: str) -> str:
    """Strip UUID prefix from MCP tool names for cleaner prompts.

    mcp_c2d0f293-ccf7-4987-a4dd-7edadc97261f_instagram_read_profile
    → instagram_read_profile

    Non-MCP tools pass through unchanged.
    """
    return _MCP_UUID_RE.sub("", name)


def _serialize_tools(tools: list[dict]) -> tuple[str, dict[str, str]]:
    """Serialize OpenAI-format tool dicts into a compact text block.

    Returns (serialized_text, short_to_full_name_map).
    MCP tool names are shortened (UUID prefix stripped) to reduce
    prompt bloat and help the LLM pick the right tool.
    """
    lines: list[str] = []
    name_map: dict[str, str] = {}  # short_name → full_name

    for tool in tools:
        func = tool.get("function", {})
        full_name = func.get("name", "unknown")
        short_name = _shorten_tool_name(full_name)
        desc = func.get("description", "")
        params = func.get("parameters", {})
        props = params.get("properties", {})
        required = params.get("required", [])

        if short_name != full_name:
            name_map[short_name] = full_name

        param_lines: list[str] = []
        for pname, pdef in props.items():
            ptype = pdef.get("type", "string")
            pdesc = pdef.get("description", "")
            req = " (required)" if pname in required else ""
            param_lines.append(f"    - {pname}: {ptype}{req} — {pdesc}")

        lines.append(f"**{short_name}** — {desc}")
        if param_lines:
            lines.append("  Parameters:")
            lines.extend(param_lines)
        lines.append("")

    return "\n".join(lines), name_map


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
                            logger.debug("Skipping malformed JSON object in Claude CLI output at offset %d", start)
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
        # Warm pool: pre-spawned processes ready for immediate use
        # Each entry: (process, spawn_time, args_tuple) — args must match to reuse
        self._warm_procs: list[tuple[asyncio.subprocess.Process, float, tuple]] = []
        self._warming: bool = False

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

        # Build CLI args — prompt piped via stdin (not CLI arg) to avoid
        # OS argument length limits on large conversations.
        args = [
            self._claude_bin, "-p", "-",  # "-" reads prompt from stdin
            "--output-format", "json",
            "--tools", "",  # Disable Claude Code's built-in tools
            "--model", self._model,
        ]

        # Always override Claude Code's system prompt to prevent its
        # built-in tools (Read, Edit, Bash) from leaking into responses.
        # SOUL.md and capabilities are already in the prompt text as
        # [System Context] blocks via _serialize_messages().
        # Name map for reversing short MCP names back to full UUID names.
        # Stored on instance so _parse_response can access it.
        self._tool_name_map: dict[str, str] = {}

        if tools:
            _tools_text, self._tool_name_map = _serialize_tools(tools)
            tool_system = (
                "You are LazyClaw, an AI agent. The user's instructions "
                "and your capabilities are in the [System Context] blocks "
                "in the conversation. Follow those rules.\n\n"
                "CRITICAL: You are NOT Claude Code. Do NOT call tools named "
                "Read, Edit, Bash, Grep, Write, Glob, WebSearch, WebFetch, "
                "Agent, or any Claude Code tool. They do NOT exist. "
                "ONLY call tools from the list below.\n\n"
                + _TOOL_CALLING_INSTRUCTIONS
                + _tools_text
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

        logger.info("Claude CLI call: tools=%d, model=%s, prompt_len=%d chars",
                    len(tools), self._model, len(prompt_text))
        if tools:
            logger.info("Claude CLI tool names: %s", [t.get("function", {}).get("name") for t in tools])
        # Dump first 500 chars of prompt for debugging
        logger.debug("Claude CLI prompt preview: %s", prompt_text[:500])

        last_error = None
        for attempt in range(_MAX_RETRIES):
            # Try to grab a pre-warmed process first
            proc = self._grab_warm_proc(args)

            try:
                if proc is None:
                    # Strip ANTHROPIC_API_KEY so Claude CLI uses the
                    # subscription instead of a potentially empty API key
                    # loaded from .env by the server process.
                    _env = {k: v for k, v in os.environ.items()
                            if k != "ANTHROPIC_API_KEY"}
                    proc = await asyncio.create_subprocess_exec(
                        *args,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=_env,
                    )

                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=prompt_text.encode("utf-8")),
                    timeout=_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    logger.warning("Failed to kill timed-out Claude CLI process", exc_info=True)
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "Claude CLI timed out after %ds (attempt %d/%d), retrying...",
                        _TIMEOUT_S, attempt + 1, _MAX_RETRIES,
                    )
                    continue
                logger.error("Claude CLI timed out after %ds (all retries)", _TIMEOUT_S)
                raise RuntimeError(f"Claude CLI timed out after {_TIMEOUT_S}s")
            except FileNotFoundError:
                raise RuntimeError(
                    "claude CLI not found. Install Claude Code: "
                    "https://docs.anthropic.com/en/docs/claude-code"
                )

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                out = stdout.decode("utf-8", errors="replace").strip()
                # Claude CLI often writes errors to stdout as JSON
                err_detail = err or out[:500] or "(no output)"
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "Claude CLI failed (exit %d, attempt %d/%d): stderr=%s stdout=%s",
                        proc.returncode, attempt + 1, _MAX_RETRIES,
                        err[:200] or "(empty)", out[:200] or "(empty)",
                    )
                    continue
                logger.error("Claude CLI failed (exit %d): %s", proc.returncode, err_detail)
                raise RuntimeError(f"Claude CLI error: {err_detail}")

            raw = stdout.decode("utf-8", errors="replace").strip()

            # Pre-warm next process in background (for next iteration)
            asyncio.create_task(self._pre_warm(args))

            return self._parse_response(raw)

        raise RuntimeError("Claude CLI failed after all retries")

    def _grab_warm_proc(
        self, args: list[str],
    ) -> asyncio.subprocess.Process | None:
        """Grab a pre-warmed process if one matches args and is alive.

        CRITICAL: warm processes are spawned with specific CLI args
        (--system-prompt, --tools, --model). Only stdin content changes.
        Using a warm process spawned with different args would send the
        prompt to a process with the WRONG system prompt — causing the
        model to ignore tools or behave incorrectly.
        """
        import time
        now = time.monotonic()
        args_key = tuple(args)
        remaining: list[tuple[asyncio.subprocess.Process, float, tuple]] = []
        result: asyncio.subprocess.Process | None = None
        for proc, spawned_at, warm_args in self._warm_procs:
            age = now - spawned_at
            if age > _WARM_EXPIRE_S or proc.returncode is not None:
                # Expired or dead — kill it
                try:
                    proc.kill()
                except Exception:
                    logger.warning("Failed to kill expired warm Claude CLI process", exc_info=True)
                continue
            if result is None and warm_args == args_key:
                # Args match — use this one
                logger.debug("Using pre-warmed CLI process (age: %.1fs)", age)
                result = proc
            else:
                # Keep for later or different args — don't kill
                remaining.append((proc, spawned_at, warm_args))
        self._warm_procs = remaining
        return result

    async def _pre_warm(self, args: list[str]) -> None:
        """Spawn a process in the background so it's ready for the next call.

        The process starts, loads claude, and blocks on stdin.read().
        When we later call proc.communicate(input=...), it gets the prompt instantly.
        Args are stored so _grab_warm_proc only reuses matching processes.
        """
        if self._warming or len(self._warm_procs) >= _WARM_POOL_SIZE:
            return
        self._warming = True
        try:
            import time
            _env = {k: v for k, v in os.environ.items()
                    if k != "ANTHROPIC_API_KEY"}
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_env,
            )
            self._warm_procs.append((proc, time.monotonic(), tuple(args)))
            logger.debug("Pre-warmed CLI process (PID %s)", proc.pid)
        except Exception as exc:
            logger.debug("Pre-warm failed: %s", exc)
        finally:
            self._warming = False

    def _parse_response(self, raw: str) -> LLMResponse:
        """Parse claude -p --output-format json response."""
        # Try JSON parse first
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: treat as plain text
            logger.warning("Claude CLI returned non-JSON, treating as text")
            remaining, tool_calls = _parse_tool_calls(raw)
            # Reverse-map short MCP names
            _nmap = getattr(self, "_tool_name_map", {})
            if tool_calls and _nmap:
                tool_calls = [
                    ToolCall(id=tc.id, name=_nmap.get(tc.name, tc.name), arguments=tc.arguments)
                    for tc in tool_calls
                ]
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

        # Reverse-map short MCP names back to full UUID names
        _nmap = getattr(self, "_tool_name_map", {})
        if tool_calls and _nmap:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=_nmap.get(tc.name, tc.name),
                    arguments=tc.arguments,
                )
                for tc in tool_calls
            ]

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
