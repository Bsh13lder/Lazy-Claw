"""Claude Code CLI as an LLM provider.

Routes LLM calls through `claude -p` (covered by Claude Code subscription —
NOT the API; we shell out to the official binary the user already pays for).

Tool calling primary path: `--json-schema` constrains the model to return
{content, tool_calls[]}; we read the parsed `structured_output` field
directly. A legacy [TOOL_CALL] tag parser stays in place as a safety net
for older CLI versions or schema validation slips.

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

_TIMEOUT_S = 120  # 120s — cold-cache Sonnet turn can exceed 90s; retries handle stalls
_MAX_RETRIES = 2  # Retry once before giving up (total 2 attempts)
_WARM_POOL_SIZE = 3  # 3 pre-warmed processes — covers chat bursts without queueing
_WARM_EXPIRE_S = 60  # Kill warm process if unused after 60s

# Built-in Claude Code tools / MCP defaults that must be explicitly
# disallowed. `--tools ""` only disables the *built-in core* tools but
# does NOT block MCP servers or capabilities the user's subscription
# has globally (Canva, Gmail, Google Drive, Calendar, WebSearch, etc.).
# Without this list Sonnet will use its own WebSearch / WebFetch and
# put the answer in `content`, bypassing our agent loop entirely.
_DISALLOWED_BUILT_INS = [
    "Bash", "Read", "Edit", "Write", "Glob", "Grep",
    "WebSearch", "WebFetch", "Task", "Agent",
    "TodoWrite", "NotebookEdit", "BashOutput", "KillShell",
]
_TOOL_CALL_PATTERN = re.compile(
    r"\[TOOL_CALL\](.*?)\[/TOOL_CALL\]",
    re.DOTALL,
)

# JSON Schema passed via --json-schema when tools are available.
# Claude is forced to emit a single object matching this shape; the CLI
# then surfaces it under `structured_output` in the JSON envelope, so
# parsing becomes a dict lookup instead of regex over chatty prose.
_TOOL_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "content": {
            "type": "string",
            "description": (
                "Plain-text reply for the user. Empty string when only "
                "tool calls are needed."
            ),
        },
        "tool_calls": {
            "type": "array",
            "description": (
                "Tools to invoke. Empty array when no tools are needed. "
                "ONLY use tool names from the Tool Definitions block."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["name", "arguments"],
            },
        },
    },
    "required": ["content", "tool_calls"],
}

# Slimmer instructions — the schema enforces structure, so this block
# focuses on *which* tools are real, *when* to call vs. answer, and
# (critically) clarifies that EMITTING a tool_call is how you call it —
# LazyClaw's runtime executes them in a follow-up turn.
_TOOL_CALLING_INSTRUCTIONS = """
## How tool calling works here

You are running inside LazyClaw, a Python agent runtime. You have NO
direct internet access, NO web search, NO file system access, NO MCP
servers, NO code execution. The ONLY way to take any action is to EMIT
a tool call from the Tool Definitions block. LazyClaw's runtime then
executes it and sends the result back next turn as `[Tool Result: ...]`.

This is exactly like Anthropic's tool_use API: you propose, the runtime
returns the result.

## Output contract (enforced by JSON Schema)

Return a single JSON object with two keys:
- `content`: plain-text reply for the user (empty string when only emitting tool calls)
- `tool_calls`: array of `{name, arguments}` objects (empty array when no tools needed)

Behavioural rules:
- A tool listed below IS available. Emit it. Never refuse with "tool X is listed but not callable in this environment" — that's wrong, the runtime *will* call it.
- ONLY use tool names from the Tool Definitions block. Do NOT invent names.
- If the user asks for live data (counts, status, prices, search results, files, messages), `tool_calls` MUST be non-empty. Saying "Searching..." with an empty array is a hallucination.
- NEVER fabricate data and put it in `content`. Numbers, prices, stats, search results — all must come from a tool call. If no tool fits, say "I don't have a tool for that".
- For greetings or casual chat with no action needed, leave `tool_calls` empty and answer in `content`.

### Tool Definitions

"""

# Reminder block appended AFTER the tool list in the assembled prompt.
# Recall is highest on the last tokens before generation; this stays
# small to fit cheaply at the tail.
_TOOL_CALLING_REMINDER = """

---
REMINDER before responding:
1. You are NOT Claude Code. Built-in names like Read, Edit, Bash, Grep, Write, Glob, WebSearch, WebFetch, Agent do NOT exist here. ONLY emit tool names from the Tool Definitions above.
2. Every tool above IS available — LazyClaw's runtime executes it once you emit it. Never refuse with "not available in this environment".
3. If the user wants you to DO something (check, find, search, send, look up, scrape, read, write, list), `tool_calls` MUST be non-empty.
4. Output exactly one JSON object: `{"content": "...", "tool_calls": [{"name": "...", "arguments": {...}}]}`. No prose outside the JSON.
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


def check_claude_cli_auth() -> tuple[bool, str]:
    """Detect Claude CLI install + login state at boot.

    Returns (is_ready, message). Designed for a one-line server log:
    silent when fine, loud when broken. Never raises — broken claude
    just means MODE_CLAUDE is unavailable, other modes still work.

    Detection:
      1. Binary present? If not, MODE_CLAUDE is irrelevant — return ready=True
         and skip the warning entirely (user is not on a claude path).
      2. Credential file present and non-empty?
         Linux/Docker: ~/.claude/.credentials.json (claude writes it here)
         macOS host:   credential lives in Keychain, file may not exist —
                       skip the file check there.
    """
    binary = shutil.which("claude")
    if not binary:
        # No CLI installed — user clearly isn't trying to use MODE_CLAUDE.
        return True, ""

    # macOS host stores cred in Keychain, no file to check. We can't
    # probe Keychain from Python without Foundation bindings, so we
    # just trust it and skip the warning.
    import sys
    if sys.platform == "darwin" and not os.path.exists("/.dockerenv"):
        return True, ""

    cred_path = os.path.expanduser("~/.claude/.credentials.json")
    if not os.path.isfile(cred_path) or os.path.getsize(cred_path) == 0:
        return False, (
            "Claude CLI is installed but not logged in. "
            "Run `docker exec -it lazyclaw claude /login` "
            "(or `claude /login` on the host) to enable MODE_CLAUDE."
        )

    return True, ""


def _find_claude_binary() -> str:
    """Find the claude CLI binary, searching common install paths.

    The server process may not have the same PATH as the user's shell,
    so we check well-known locations explicitly.
    """
    # 1. Standard PATH lookup
    found = shutil.which("claude")
    if found:
        return found

    # 2. Common install locations (macOS / Linux)
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "bin", "claude"),
        os.path.join(home, "bin", "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            logger.info("Found claude CLI at %s (not in PATH)", path)
            return path

    # 3. Fallback — will fail at runtime with clear error
    return "claude"


class ClaudeCLIProvider(BaseLLMProvider):
    """LLM provider that routes through the `claude` CLI.

    All calls use the user's Claude Code subscription ($0 extra).
    Tool calling is done via prompt engineering with [TOOL_CALL] tags.
    """

    def __init__(self, claude_bin: str | None = None, model: str = "sonnet") -> None:
        self._claude_bin = claude_bin or _find_claude_binary()
        self._model = model
        self._active_sessions: dict[str, bool] = {}  # session_id → has_been_used
        # Warm pool: pre-spawned processes ready for immediate use
        # Each entry: (process, spawn_time, args_tuple) — args must match to reuse.
        # Pool is filled in parallel after each chat() so bursty chat traffic
        # gets near-zero startup overhead on follow-up turns.
        self._warm_procs: list[tuple[asyncio.subprocess.Process, float, tuple]] = []

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
        # OS argument length limits on large conversations. Note: do NOT
        # pass "-" as a positional arg. The Claude CLI does NOT treat "-"
        # as a stdin marker — it treats it as the literal prompt string,
        # which corrupted every call (verified empirically with --json-schema:
        # structured_output.echo came back as "-" instead of the user's text).
        # Omitting the positional arg lets the CLI read stdin automatically
        # when something is piped in.
        args = [
            self._claude_bin, "-p",
            "--output-format", "json",
            "--tools", "",  # Disable Claude Code's built-in core tools
            "--disallowedTools", *_DISALLOWED_BUILT_INS,  # Block MCPs + WebSearch
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
            # Order: intro → output contract → tool defs → REMINDER.
            # The reminder lands closest to generation, which is where
            # prompt adherence is highest in long contexts. The output
            # is *also* constrained by --json-schema below; the prompt
            # stays in for behavioural rules (which tools, when to call).
            #
            # Intro deliberately does NOT reference [System Context]
            # blocks — those only exist when system messages are passed,
            # and pointing the model at missing context made Sonnet
            # refuse with "I can't see my capabilities" hallucinations.
            tool_system = (
                "You are LazyClaw, an AI agent. Any [System Context] "
                "blocks in the conversation contain your project rules; "
                "your tools are listed in the Tool Definitions block "
                "below.\n\n"
                + _TOOL_CALLING_INSTRUCTIONS
                + _tools_text
                + _TOOL_CALLING_REMINDER
            )
            args.extend([
                "--system-prompt", tool_system,
                "--json-schema", json.dumps(_TOOL_OUTPUT_SCHEMA),
            ])
        else:
            args.extend([
                "--system-prompt",
                "You are LazyClaw, an AI agent. Any [System Context] "
                "blocks in the conversation contain your project rules. "
                "Respond concisely. You have no tools available; "
                "answer from your knowledge or say you don't know.",
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
                except ProcessLookupError:
                    pass
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
                combined = err or out or "(no output)"

                # Parse JSON error responses from claude CLI
                _not_logged_in = "Not logged in" in combined or "/login" in combined
                if _not_logged_in:
                    raise RuntimeError(
                        "Claude CLI is not logged in. Run 'claude' in terminal "
                        "and complete login, then restart LazyClaw."
                    )

                err_detail = combined[:500]
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "Claude CLI failed (exit %d, attempt %d/%d): %s",
                        proc.returncode, attempt + 1, _MAX_RETRIES,
                        err_detail[:200],
                    )
                    continue
                logger.error("Claude CLI failed (exit %d): %s", proc.returncode, err_detail)
                raise RuntimeError(f"Claude CLI error: {err_detail}")

            raw = stdout.decode("utf-8", errors="replace").strip()

            # Refill warm pool in the background. Spawn (POOL_SIZE - current)
            # in parallel — args-keyed, so identical follow-up calls hit a
            # ready proc with ~0ms startup overhead. Pool size = 3 covers
            # typical chat bursts (user fires 2-3 messages in a row).
            slots_to_fill = max(0, _WARM_POOL_SIZE - len(self._warm_procs))
            for _ in range(slots_to_fill):
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
                # Expired or dead — kill it (best-effort; ProcessLookupError
                # means it already died, which is exactly what we wanted).
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
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
        When we later call proc.communicate(input=...), it gets the prompt
        instantly. Args are stored so _grab_warm_proc only reuses matching
        processes. Concurrent callers are permitted — each checks the pool
        ceiling before spawning so we don't over-fill.
        """
        if len(self._warm_procs) >= _WARM_POOL_SIZE:
            return
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
            # Re-check after spawn (avoid race where multiple coroutines
            # all spawned before the first appended). If we're now over
            # the cap, kill this one immediately rather than leak it.
            if len(self._warm_procs) >= _WARM_POOL_SIZE:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                return
            self._warm_procs.append((proc, time.monotonic(), tuple(args)))
            logger.debug("Pre-warmed CLI process (PID %s, pool=%d/%d)",
                         proc.pid, len(self._warm_procs), _WARM_POOL_SIZE)
        except Exception as exc:
            logger.debug("Pre-warm failed: %s", exc)

    def _parse_response(self, raw: str) -> LLMResponse:
        """Parse claude -p --output-format json response."""
        # Try JSON parse first
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None

        # Check for error responses (is_error=true in JSON)
        if isinstance(data, dict) and data.get("is_error"):
            error_msg = data.get("result", "Unknown CLI error")
            raise RuntimeError(f"Claude CLI error: {error_msg}")

        if data is None:
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
        # CLI reports API-equivalent cost but it's covered by the
        # subscription — surfaced as $0 to the cost tracker. The actual
        # API-equivalent value lives in usage["cost_usd_subscription"]
        # for diagnostic visibility ("how much would this have cost?").
        api_equiv_cost = float(data.get("total_cost_usd") or 0.0)
        cost = 0.0

        # Strip Claude Code artifacts that sometimes leak into output
        result_text = re.sub(
            r"<system-reminder>.*?</system-reminder>", "", result_text, flags=re.DOTALL
        ).strip()

        # Parse usage into standard format. CLI exposes cache stats —
        # forward them so the dashboard can show prompt-cache savings.
        usage = {
            "prompt_tokens": usage_raw.get("input_tokens", 0),
            "completion_tokens": usage_raw.get("output_tokens", 0),
            "total_tokens": (
                usage_raw.get("input_tokens", 0)
                + usage_raw.get("output_tokens", 0)
            ),
            "cache_read_tokens": usage_raw.get("cache_read_input_tokens", 0),
            "cache_creation_tokens": usage_raw.get("cache_creation_input_tokens", 0),
            "cost_usd": cost,
            "cost_usd_subscription": api_equiv_cost,
            "provider": "claude_cli",
        }

        # Primary path: schema-validated structured_output. The CLI
        # populates this when --json-schema is passed AND the model
        # returned a schema-conformant object. Skip prose entirely.
        structured = data.get("structured_output")
        tool_calls: list[ToolCall] = []
        remaining: str = ""

        if isinstance(structured, dict) and "tool_calls" in structured:
            remaining = (structured.get("content") or "").strip()
            for raw_tc in structured.get("tool_calls") or []:
                if not isinstance(raw_tc, dict):
                    continue
                name = raw_tc.get("name")
                if not name:
                    continue
                args_obj = raw_tc.get("arguments") or {}
                if not isinstance(args_obj, dict):
                    # Schema requires object; defensively coerce.
                    logger.debug(
                        "Claude CLI structured_output tool '%s' has non-dict arguments (%s); coercing to {}",
                        name, type(args_obj).__name__,
                    )
                    args_obj = {}
                tool_calls.append(
                    ToolCall(
                        id=f"cli_{uuid.uuid4().hex[:8]}",
                        name=name,
                        arguments=args_obj,
                    )
                )
            logger.debug(
                "Claude CLI structured_output parsed: content_len=%d tool_calls=%d",
                len(remaining), len(tool_calls),
            )
        else:
            # Legacy/safety path: parse [TOOL_CALL] tags from prose.
            # Triggered when running an older CLI that doesn't emit
            # structured_output, or when the model slipped past schema
            # validation (rare but observed).
            remaining, tool_calls = _parse_tool_calls(result_text)
            if "[TOOL_CALL]" in result_text and not tool_calls:
                logger.warning(
                    "Claude CLI: [TOOL_CALL] found in text but parser returned 0 calls. "
                    "First 200 chars: %s",
                    result_text[:200],
                )

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
