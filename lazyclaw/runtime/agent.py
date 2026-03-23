from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.llm.router import LLMRouter
from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.llm.providers.base import LLMMessage, ToolCall
from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session

from lazyclaw.runtime.callbacks import AgentEvent, CancellationToken, NullCallback
from lazyclaw.runtime.events import (
    FAST_DISPATCH, INSTANT_COMMAND,
    HELP_NEEDED, HELP_RESPONSE,
)
from lazyclaw.runtime.team_lead import TeamLead
from lazyclaw.runtime.stuck_detector import detect_stuck
from lazyclaw.runtime.tool_executor import APPROVAL_PREFIX, ToolExecutor
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Chat-only patterns — messages that NEVER need tools.
# Everything else gets tools and the LLM decides what to use.
# Action confirmations — short messages that continue a tool-using conversation.
# "do it", "go", "run it", "yes do it", "go ahead", "start", "proceed", "continue"
_ACTION_CONFIRM_PATTERN = re.compile(
    r"^(do it|do that|go|go ahead|run it|start|proceed|continue|execute|"
    r"yes do it|yeah do it|yep do it|ok do it|sure do it|"
    r"finish it|try it|try again|retry|send it|ship it|"
    r"first one|second one|option 1|option 2|the first|the second)[\s!?.,]*$",
    re.IGNORECASE,
)

_CHAT_ONLY_PATTERN = re.compile(
    r"^(hi|hey|hello|yo|sup|thanks|thank you|thx|ok|okay|sure|"
    r"yes|no|yep|nope|good morning|good night|good evening|"
    r"bye|goodbye|see you|how are you|what's up|whats up|"
    r"nice|cool|great|awesome|lol|haha|hehe|wow|omg|"
    r"good|bad|fine|alright|sounds good|got it|understood|"
    r"perfect|exactly|right|correct|wrong|nah|meh)[\s!?.,]*$",
    re.IGNORECASE,
)


# ── Meta-Tool Pattern ─────────────────────────────────────────────────
# Instead of regex-guessing which tools the LLM needs (brittle, 5K tokens),
# send only 3-4 base tools. LLM discovers others via search_tools on demand.
# Schemas for discovered tools are injected dynamically.

# Base tools always sent (tiny — ~400 tokens total)
_BASE_TOOL_NAMES = frozenset({
    "search_tools", "recall_memories", "save_memory", "delegate",
    "connect_mcp_server", "disconnect_mcp_server", "browser",
})

# Max chars for a tool result before truncation (prevents MCP JSON blowup)
_MAX_TOOL_RESULT_CHARS = 4000


def _extract_tool_names_from_search_result(result: str) -> list[str]:
    """Extract tool names from search_tools result text (bold **name**: pattern)."""
    return re.findall(r"\*\*(\w+)\*\*:", result)


def _compact_history(
    history: list[LLMMessage], keep_recent: int = 4,
) -> list[LLMMessage]:
    """Keep last N user/assistant exchanges in full, compact older ones.

    Old messages become 1-line summaries. Tool messages are dropped.
    Returns new list (immutable pattern).
    """
    if not history:
        return history

    # Split into user/assistant pairs and tool messages
    # Keep system messages (summaries) as-is
    recent: list[LLMMessage] = []
    old: list[LLMMessage] = []

    # Count user/assistant messages from the end
    user_assistant_count = 0
    for msg in reversed(history):
        if msg.role in ("user", "assistant") and not msg.tool_calls:
            user_assistant_count += 1

    # Walk forward, putting old messages into compact and recent into full
    seen_recent = 0
    cutoff = max(0, user_assistant_count - keep_recent)
    ua_idx = 0

    for msg in history:
        # System messages (summaries) — always keep
        if msg.role == "system":
            recent.append(msg)
            continue

        # Tool messages from old conversations — drop entirely
        if msg.role == "tool":
            if ua_idx < cutoff:
                continue  # drop old tool results
            recent.append(msg)
            continue

        # Assistant with tool_calls from old conversations — compact
        if msg.role == "assistant" and msg.tool_calls:
            if ua_idx < cutoff:
                tools_used = ", ".join(tc.name for tc in msg.tool_calls)
                summary = f"[Used {tools_used}]"
                if msg.content:
                    summary = msg.content[:100] + f" [{tools_used}]"
                old.append(LLMMessage(role="assistant", content=summary))
                continue
            recent.append(msg)
            continue

        # User/assistant messages
        if msg.role in ("user", "assistant"):
            ua_idx += 1
            if ua_idx <= cutoff:
                # Compact old messages
                content = (msg.content or "")[:100]
                if len(msg.content or "") > 100:
                    content += "..."
                old.append(LLMMessage(role=msg.role, content=content))
            else:
                recent.append(msg)
            continue

        recent.append(msg)

    # If there are old messages, combine into a single summary
    if old:
        summary_lines = []
        for msg in old[-6:]:  # Keep last 6 compacted messages max
            prefix = "User" if msg.role == "user" else "Assistant"
            summary_lines.append(f"{prefix}: {msg.content}")
        compact_msg = LLMMessage(
            role="system",
            content="--- Earlier conversation (compacted) ---\n" + "\n".join(summary_lines),
        )
        return [compact_msg] + recent

    return recent


def _prune_old_tool_results(
    messages: list[LLMMessage], keep_last_n: int = 2,
) -> list[LLMMessage]:
    """Replace old tool results with 1-line summaries. Keep last N in full.

    Returns new list (immutable pattern — never mutate input).
    Keeps tool_call_id intact for OpenAI validation.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.role == "tool"]

    if len(tool_indices) <= keep_last_n:
        return messages

    to_compress = set(tool_indices[:-keep_last_n])

    result: list[LLMMessage] = []
    for i, msg in enumerate(messages):
        if i in to_compress:
            content = msg.content or ""
            summary = content[:150].replace("\n", " ").strip()
            if len(content) > 150:
                summary += "..."
            result.append(LLMMessage(
                role=msg.role,
                content=f"[Previous result truncated] {summary}",
                tool_call_id=msg.tool_call_id,
            ))
        else:
            result.append(msg)

    return result


def _cap_tool_result(result: str) -> str:
    """Truncate oversized tool results to save tokens.

    MCP tools can return huge JSON arrays (50+ contacts, full page snapshots).
    Cap at ~4K chars — enough for the LLM to understand, not enough to blow up context.
    """
    if not result or len(result) <= _MAX_TOOL_RESULT_CHARS:
        return result
    # Keep first part + tail hint
    truncated = result[:_MAX_TOOL_RESULT_CHARS]
    remaining = len(result) - _MAX_TOOL_RESULT_CHARS
    return f"{truncated}\n... [truncated {remaining} chars]"


def _strip_tool_messages(history: list[LLMMessage]) -> list[LLMMessage]:
    """Remove tool-related messages from history for tool-free conversations.

    Converts assistant messages with tool_calls to plain text,
    and drops tool role messages entirely. This prevents GPT-5 from
    hallucinating tool calls when it sees old tool patterns in history.
    """
    result = []
    for msg in history:
        if msg.role == "tool":
            continue  # Drop tool result messages
        if msg.role == "assistant" and msg.tool_calls:
            # Convert tool-calling assistant message to plain text
            parts = []
            if msg.content:
                parts.append(msg.content)
            for tc in msg.tool_calls:
                parts.append(f"[Used {tc.name}]")
            result.append(LLMMessage(
                role="assistant",
                content=" ".join(parts) if parts else "[Used tools]",
            ))
        else:
            result.append(msg)
    return result


def _wants_any_tools(message: str) -> bool:
    """Decide if tools should be available for this message.

    Inverted approach: assume tools UNLESS the message is clearly just chat.
    The LLM is smart enough to not call tools on "what's my name?" even
    if tools are available. We only block tools for pure greetings/acks.
    """
    lower = message.lower().strip()
    # Action confirmations — these continue a prior tool-using conversation.
    # MUST check before the short-message filter, because "do it" is <8 chars.
    if _ACTION_CONFIRM_PATTERN.match(lower):
        return True
    # Very short messages (under 5 chars) — greetings like "hi", "ok", "ty"
    if len(lower) < 5:
        return False
    # Known chat-only patterns — no tools needed
    if _CHAT_ONLY_PATTERN.match(lower):
        return False
    # Everything else: give the LLM tools and let it decide
    return True


# ── Fast Dispatch ─────────────────────────────────────────────────────
# Tools that trigger automatic delegation to background specialists.

HEAVY_TOOLS: frozenset[str] = frozenset({
    "web_search", "run_command",
    "read_file", "write_file",
})


def _handle_instant_command(
    message: str, team_lead: TeamLead | None, task_runner=None,
    user_id: str | None = None,
) -> str | None:
    """Handle status and cancel commands without LLM. Returns response or None."""
    if team_lead is None:
        return None
    stripped = message.strip()
    if TeamLead.is_status_query(stripped):
        return team_lead.format_status()
    is_cancel, target = TeamLead.is_cancel_command(stripped)
    if is_cancel:
        task_id = team_lead.find_cancel_target(target)
        if task_id:
            if task_runner and user_id:
                asyncio.ensure_future(task_runner.cancel(task_id, user_id))
            team_lead.cancel(task_id)
            return f"Cancelled task matching \"{target}\""
        return f"No running task matching \"{target}\""
    return None


async def _extract_and_store_lesson(
    eco_router, config, user_id: str, message: str, recent: list,
) -> None:
    """Fire-and-forget: extract a lesson from user correction and store it.

    Never raises — all errors caught and logged. Uses gpt-5-mini for cost.
    """
    if eco_router is None:
        return
    try:
        from lazyclaw.runtime.lesson_extractor import extract_lesson
        from lazyclaw.runtime.lesson_store import store_lesson

        lesson = await extract_lesson(
            eco_router, user_id, message, recent,
        )
        if lesson:
            await store_lesson(config, user_id, lesson)
    except Exception as e:
        logger.debug("Lesson extraction background task failed: %s", e)


class Agent:
    def __init__(
        self,
        config: Config,
        router: LLMRouter,
        registry: SkillRegistry | None = None,
        eco_router: EcoRouter | None = None,
        permission_checker=None,
        task_runner=None,
        team_lead: TeamLead | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.eco_router = eco_router or EcoRouter(config, router)
        self.registry = registry
        self.executor = (
            ToolExecutor(
                registry,
                permission_checker=permission_checker,
                timeout=config.tool_timeout,
            )
            if registry
            else None
        )
        self._task_runner = task_runner
        self._team_lead = team_lead

    async def process_message(
        self,
        user_id: str,
        message: str,
        chat_session_id: str | None = None,
        callback=None,
        channel_context: str | None = None,
    ) -> str:
        cb = callback or NullCallback()
        cancel_token = CancellationToken()
        if hasattr(cb, 'cancel_token'):
            cb.cancel_token = cancel_token

        # Instant commands — no LLM call needed
        instant = _handle_instant_command(message, self._team_lead, self._task_runner, user_id)
        if instant is not None:
            await cb.on_event(AgentEvent(INSTANT_COMMAND, instant, {}))
            await cb.on_event(AgentEvent("done", "Response ready", {}))
            return instant

        key = derive_server_key(self.config.server_secret, user_id)
        _start_time = time.monotonic()
        _all_tools_used: list[str] = []

        # ── Compound task splitting ──────────────────────────────────
        # If message has multiple tasks AND we have a TaskRunner,
        # split and dispatch each to the right lane. TeamLead stays free.
        if (
            self._team_lead
            and self._task_runner
            and not getattr(self, "is_background", False)
            and _wants_any_tools(message)
        ):
            from lazyclaw.runtime.task_splitter import split_tasks, _looks_compound

            if _looks_compound(message):
                sub_tasks = await split_tasks(
                    self.eco_router, user_id, message,
                    worker_model=self.config.worker_model,
                )
                if len(sub_tasks) > 1:
                    # Dispatch each sub-task
                    dispatched: list[str] = []
                    for st in sub_tasks:
                        task_id = await self._task_runner.submit(
                            user_id=user_id,
                            instruction=st.instruction,
                            name=st.name,
                            callback=callback,
                        )
                        dispatched.append(f"{st.name} ({st.lane})")

                    summary = ", ".join(dispatched)
                    status_msg = (
                        f"On it — split into {len(sub_tasks)} tasks:\n"
                        + "\n".join(f"  • {d}" for d in dispatched)
                    )

                    await cb.on_event(AgentEvent(
                        FAST_DISPATCH, status_msg,
                        {"tasks": len(sub_tasks), "names": dispatched},
                    ))
                    await cb.on_event(AgentEvent("stream_done", "", {}))
                    await cb.on_event(AgentEvent("done", "Dispatched", {}))
                    return status_msg

        # Register foreground task with TeamLead (skip for background agents)
        _fg_task_id: str | None = None
        if self._team_lead and not getattr(self, "is_background", False):
            _fg_task_id = str(uuid4())
            self._team_lead.register(_fg_task_id, "chat", message[:80], "foreground")
        _session_tokens = 0

        # Initialize trace recorder
        from lazyclaw.replay.recorder import TraceRecorder
        recorder = TraceRecorder(self.config, user_id)
        await recorder.record_user_message(message)

        import asyncio as _aio

        from lazyclaw.memory.compressor import compress_history
        from lazyclaw.skills.manager import load_user_skills
        from lazyclaw.runtime.context_builder import build_context
        from lazyclaw.runtime.personality import load_personality

        # Decide upfront: does this message need tools?
        needs_tools_early = self.registry is not None and _wants_any_tools(message)

        async def _load_history():
            async with db_session(self.config) as db:
                if chat_session_id:
                    rows = await db.execute(
                        "SELECT id, role, content, tool_name, metadata FROM agent_messages "
                        "WHERE user_id = ? AND chat_session_id = ? ORDER BY created_at ASC",
                        (user_id, chat_session_id),
                    )
                else:
                    rows = await db.execute(
                        "SELECT id, role, content, tool_name, metadata FROM agent_messages "
                        "WHERE user_id = ? ORDER BY created_at ASC",
                        (user_id,),
                    )
                return await rows.fetchall()

        if needs_tools_early:
            # Full parallel init — load history, skills, and rich context
            history_rows, _, system_prompt = await _aio.gather(
                _load_history(),
                load_user_skills(self.config, user_id, self.registry),
                build_context(self.config, user_id, registry=self.registry),
            )
        else:
            # Fast chat path — minimal system prompt, skip skills + MCP + memories
            system_prompt = load_personality()  # Cached, ~0ms
            history_rows = await _load_history()

        history = await compress_history(
            self.config, self.eco_router, user_id, chat_session_id,
            raw_messages=history_rows,
        )

        # Register delegate skill — lets the agent dispatch to specialists
        # inline (NanoClaw pattern: no separate team lead LLM call)
        _delegate_registered = False
        if self.registry is not None:
            from lazyclaw.skills.builtin.delegate import DelegateSkill

            delegate_skill = DelegateSkill(
                config=self.config,
                registry=self.registry,
                eco_router=self.eco_router,
                permission_checker=self.executor._checker if self.executor else None,
                callback=cb,
            )
            self.registry.register(delegate_skill)
            _delegate_registered = True

        # Meta-tool pattern: send only base tools (search_tools, memory, delegate).
        # LLM discovers other tools on demand via search_tools → schemas injected dynamically.
        needs_tools = self.registry is not None and _wants_any_tools(message)
        tools: list = []
        if needs_tools:
            # Only send base tool schemas (~400 tokens instead of 5000+)
            tools = [
                schema for name in _BASE_TOOL_NAMES
                if (schema := self.registry.get_tool_schema(name)) is not None
            ]
            # Include tools from favorite connected MCPs directly
            from lazyclaw.mcp.manager import _favorite_server_ids, _active_clients
            for sid in _favorite_server_ids:
                if sid in _active_clients:
                    for tool_info in self.registry.list_mcp_tools():
                        func = tool_info.get("function", {})
                        tname = func.get("name", "")
                        if sid.replace("-", "") in tname.replace("-", ""):
                            schema = self.registry.get_tool_schema(tname)
                            if schema is not None:
                                tools.append(schema)
            logger.info("Meta-tool mode: %d base tools for: %s", len(tools), message[:50])
        else:
            logger.info("No tools — fast chat path for: %s", message[:50])

        # Build context — keep recent messages, compact old ones
        if needs_tools:
            chat_history = _compact_history(history, keep_recent=4)
        else:
            chat_history = _strip_tool_messages(history)
            if len(chat_history) > 6:
                chat_history = chat_history[-6:]

        # Message order optimized for prompt caching:
        # 1. System (STATIC — cached), 2. Channel (semi-static),
        # 3. History (dynamic), 4. User message (dynamic)
        system_messages = [LLMMessage(role="system", content=system_prompt)]
        if channel_context:
            system_messages.append(LLMMessage(role="system", content=channel_context))
        messages: list[LLMMessage] = (
            system_messages
            + chat_history
            + [LLMMessage(role="user", content=message)]
        )
        logger.info("Context: %d messages (%d history + system + user), tools=%d",
                     len(messages), len(chat_history), len(tools))

        # ── Learn from corrections (fire-and-forget) ─────────────
        # If the user is correcting the previous response, extract a
        # compact lesson and save it to memory for future sessions.
        if len(history) >= 2:
            from lazyclaw.runtime.lesson_extractor import is_correction

            prev_assistant = next(
                (m for m in reversed(history) if m.role == "assistant"),
                None,
            )
            if prev_assistant and is_correction(message):
                _aio.create_task(
                    _extract_and_store_lesson(
                        self.eco_router, self.config, user_id,
                        message, history[-4:],
                    )
                )

        # Agentic loop — brain decides when to stop, safety cap prevents runaway
        max_iterations = self.config.max_tool_iterations
        _nudge_at = int(max_iterations * 0.8)  # Nudge LLM at 80% of cap
        _nudged = False
        all_new_messages: list[LLMMessage] = [LLMMessage(role="user", content=message)]
        _tool_call_history: list[str] = []  # Track tool names for loop detection
        _tool_results: list[str] = []  # Track results for error detection
        _escalated = False  # True after auto-escalation to brain_model
        _tool_call_cache: dict[str, str] = {}  # (name, args_hash) → result

        response = None
        iteration = 0
        try:
            for iteration in range(max_iterations):
                if cancel_token.is_cancelled:
                    if self._team_lead and _fg_task_id:
                        self._team_lead.cancel(_fg_task_id)
                    if _delegate_registered and self.registry:
                        self.registry.unregister("delegate")
                    await cb.on_event(AgentEvent("done", "Cancelled", {}))
                    return "Operation cancelled."

                # Prune old tool results to save tokens
                # Early iterations: keep 2 full results for context
                # Later iterations: keep only 1 (agent already has the gist)
                if iteration > 0:
                    keep_n = 1 if iteration >= 3 else 2
                    messages = _prune_old_tool_results(messages, keep_last_n=keep_n)

                kwargs: dict = {}
                if tools:
                    kwargs["tools"] = tools

                # Worker (Haiku) by default. Brain (Sonnet) only after auto-escalation.
                if not _escalated:
                    iter_model = self.config.worker_model

                model_name = iter_model
                # Show actual routing model if available
                if self.eco_router and self.eco_router.last_routing:
                    model_name = self.eco_router.last_routing.display_name
                logger.info("Iteration %d: calling %s", iteration + 1, model_name)
                await cb.on_event(AgentEvent(
                    "llm_call",
                    f"Thinking ({model_name})...",
                    {"iteration": iteration + 1, "model": model_name},
                ))
                await recorder.record_llm_call(
                    model=None, message_count=len(messages), has_tools=bool(tools),
                )

                # Use streaming when callback is present (CLI) for real-time output
                from lazyclaw.llm.providers.base import LLMResponse as _LLMResp

                streamed_content = ""
                response = None

                try:
                    async for chunk in self.eco_router.stream_chat(
                        messages, user_id=user_id, model=iter_model, **kwargs
                    ):
                        if chunk.delta:
                            streamed_content += chunk.delta
                            await cb.on_event(AgentEvent(
                                "token", chunk.delta, {"model": chunk.model},
                            ))

                        if chunk.done:
                            response = _LLMResp(
                                content=streamed_content,
                                model=chunk.model,
                                usage=chunk.usage,
                                tool_calls=chunk.tool_calls,
                            )
                except Exception as exc:
                    logger.error("Streaming failed: %s", exc, exc_info=True)
                    await cb.on_event(AgentEvent("stream_done", "", {}))
                    response = _LLMResp(
                        content=f"Sorry, an error occurred: {exc}",
                        model="unknown",
                        tool_calls=[],
                    )

                if response is None:
                    response = _LLMResp(content=streamed_content or "No response received.", model="unknown")

                logger.info(
                    "LLM response: model=%s, content_len=%d, tool_calls=%d",
                    response.model,
                    len(response.content or ""),
                    len(response.tool_calls or []),
                )

                await recorder.record_llm_response(
                    content=response.content or "",
                    model=response.model,
                    has_tool_calls=bool(response.tool_calls),
                )

                # Report token usage
                usage = response.usage or {}
                total_tokens = usage.get("total_tokens", 0)
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                eco_mode = usage.get("eco_mode")  # "eco", "hybrid_free", or None (paid)
                _session_tokens += total_tokens
                await cb.on_event(AgentEvent(
                    "tokens",
                    f"{total_tokens} tokens ({prompt_tokens} in, {completion_tokens} out)",
                    {"total": total_tokens, "prompt": prompt_tokens,
                     "completion": completion_tokens, "model": response.model,
                     "eco_mode": eco_mode},
                ))

                # If no tools were provided but LLM returned tool_calls
                # (hallucination from history patterns), ignore them
                if not tools and response.tool_calls:
                    logger.warning(
                        "LLM returned %d tool_calls but no tools were provided — ignoring",
                        len(response.tool_calls),
                    )
                    response = _LLMResp(
                        content=response.content or "Hello! How can I help you?",
                        model=response.model,
                        usage=response.usage,
                        tool_calls=[],
                    )

                if not response.tool_calls:
                    # Final text response (already streamed to user)
                    await cb.on_event(AgentEvent("stream_done", "", {}))
                    all_new_messages.append(
                        LLMMessage(role="assistant", content=response.content)
                    )
                    break

                # ── Fast dispatch ─────────────────────────────────────
                # On the FIRST LLM call, if heavy tools detected and
                # auto_delegate is on, push to TaskRunner and return
                # immediately so the team lead stays free for new messages.
                if (
                    iteration == 0
                    and self._task_runner is not None
                    and any(tc.name in HEAVY_TOOLS for tc in response.tool_calls)
                ):
                    from lazyclaw.runtime.agent_settings import get_agent_settings

                    _agent_settings = await get_agent_settings(self.config, user_id)
                    _active_count = self._team_lead.active_count if self._team_lead else 0
                    if (
                        _agent_settings.get("auto_delegate", True)
                        and _active_count
                            < _agent_settings.get("max_concurrent_specialists", 3)
                    ):
                        _specialist_name = response.tool_calls[0].name

                        _task_id = await self._task_runner.submit(
                            user_id=user_id,
                            instruction=message,
                            name=f"auto_{_specialist_name}",
                            timeout=_agent_settings.get("specialist_timeout_s", 120),
                            callback=callback,
                        )

                        # Foreground task was re-routed to background
                        if self._team_lead and _fg_task_id:
                            self._team_lead.cancel(_fg_task_id)
                            _fg_task_id = None

                        await cb.on_event(AgentEvent(
                            FAST_DISPATCH,
                            f"Dispatched to background ({_specialist_name})",
                            {"task_id": _task_id, "specialist": _specialist_name},
                        ))
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                        await cb.on_event(AgentEvent("done", "Dispatched", {}))

                        if _delegate_registered:
                            self.registry.unregister("delegate")
                        return "On it — working in background."

                # Assistant message with tool calls (may have partial text content)
                assistant_msg = LLMMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
                messages.append(assistant_msg)
                all_new_messages.append(assistant_msg)

                # Execute each tool call
                for tc in response.tool_calls:
                    _display = self.registry.get_display_name(tc.name) if self.registry else tc.name
                    _all_tools_used.append(_display)
                    await cb.on_event(AgentEvent(
                        "tool_call", _display,
                        {"tool": tc.name, "display_name": _display, "args": tc.arguments},
                    ))
                    await recorder.record_tool_call(tc.name, tc.arguments)
                    # Inject background flag so browser uses headless
                    if getattr(self, "is_background", False) and tc.name == "browser":
                        tc = ToolCall(
                            id=tc.id, name=tc.name,
                            arguments={**tc.arguments, "_background": True},
                        )

                    # Duplicate call cache — skip re-executing identical tool calls
                    _cache_key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                    if _cache_key in _tool_call_cache:
                        result = _tool_call_cache[_cache_key]
                        logger.info("Tool %s cache hit (skipped re-execution)", tc.name)
                    else:
                        result = await self.executor.execute(tc, user_id, callback=cb)

                    # Cap oversized results before injecting into context
                    if isinstance(result, str):
                        result = _cap_tool_result(result)
                    _tool_call_cache[_cache_key] = result if isinstance(result, str) else str(result)

                    await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))
                    await cb.on_event(AgentEvent(
                        "tool_result", _display,
                        {"tool": tc.name, "display_name": _display},
                    ))

                    # Track step with TeamLead
                    if self._team_lead and _fg_task_id:
                        self._team_lead.update_step(_fg_task_id, _display)

                    # Handle approval-required responses
                    if isinstance(result, str) and result.startswith(APPROVAL_PREFIX):
                        try:
                            parts = result[len(APPROVAL_PREFIX):]
                            colon_idx = parts.index(":")
                            skill_name = parts[:colon_idx]
                            args_json = parts[colon_idx + 1:]
                            parsed_args = json.loads(args_json) if args_json != "{}" else {}
                        except (ValueError, json.JSONDecodeError) as parse_err:
                            logger.error("Malformed approval response: %s (%s)", result, parse_err)
                            result = f"Tool error: malformed approval response"
                            tool_msg = LLMMessage(
                                role="tool",
                                content=result,
                                tool_call_id=tc.id,
                            )
                            messages.append(tool_msg)
                            all_new_messages.append(tool_msg)
                            continue

                        # Try inline approval via callback (CLI y/n prompt)
                        # Pass display name so the UI shows friendly names
                        if hasattr(cb, '_pending_display_name'):
                            cb._pending_display_name = _display
                        approved = await cb.on_approval_request(skill_name, parsed_args)

                        if approved:
                            await cb.on_event(AgentEvent(
                                "approval", f"{_display} approved",
                                {"skill": skill_name, "display_name": _display, "approved": True},
                            ))
                            result = await self.executor.execute_allowed(tc, user_id, callback=cb)
                            await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))
                            await cb.on_event(AgentEvent(
                                "tool_result", _display,
                                {"tool": tc.name, "display_name": _display},
                            ))
                        else:
                            await cb.on_event(AgentEvent(
                                "approval", f"{_display} denied",
                                {"skill": skill_name, "display_name": _display, "approved": False},
                            ))
                            result = (
                                f"The user denied the action '{skill_name}'. "
                                f"Do not retry this action. Explain what you wanted to do "
                                f"and ask if the user wants to try a different approach."
                            )

                    tool_msg = LLMMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id,
                    )
                    messages.append(tool_msg)
                    all_new_messages.append(tool_msg)
                    _tool_call_history.append(tc.name)
                    _tool_results.append(
                        result if isinstance(result, str) else str(result)
                    )

                    # Dynamic schema injection: after search_tools returns,
                    # inject discovered tool schemas so LLM can call them next iteration
                    if tc.name == "search_tools" and self.registry:
                        discovered = _extract_tool_names_from_search_result(
                            result if isinstance(result, str) else str(result)
                        )
                        for dname in discovered:
                            schema = self.registry.get_tool_schema(dname)
                            if schema and schema not in tools:
                                tools.append(schema)
                        if discovered:
                            logger.info(
                                "Injected %d tool schemas: %s",
                                len(discovered), ", ".join(discovered),
                            )

                # ── Running-long nudge ──
                # At 80% of safety cap, tell the LLM to wrap up or ask user
                if iteration >= _nudge_at and not _nudged:
                    _nudged = True
                    _nudge_msg = LLMMessage(
                        role="system",
                        content=(
                            "You've been working for a while. You have a few steps left. "
                            "Either: (1) finish what you're doing and summarize results, "
                            "or (2) if you need more work, explain what's left and ask "
                            "the user if they want you to continue."
                        ),
                    )
                    messages.append(_nudge_msg)
                    logger.info(
                        "Iteration %d/%d: injected running-long nudge",
                        iteration + 1, max_iterations,
                    )

                # ── Stuck detection (replaces old inline loop detection) ──
                # Only run if tools were actually called this iteration
                if response and response.tool_calls:
                    _last_result = _tool_results[-1] if _tool_results else None
                    _stuck_signal = detect_stuck(
                        _tool_call_history, _tool_results, _last_result,
                    )
                    if _stuck_signal is not None:
                        logger.warning(
                            "Stuck detected: %s (%s)", _stuck_signal.reason, _stuck_signal.context,
                        )

                        # Auto-escalate: retry with brain_model before asking user
                        if not _escalated:
                            _escalated = True
                            logger.info("Auto-escalating to brain model: %s", self.config.brain_model)
                            await cb.on_event(AgentEvent(
                                "llm_call",
                                f"Escalating to {self.config.brain_model}...",
                                {"escalation": True, "model": self.config.brain_model},
                            ))
                            # Clear stuck state — give brain a fresh shot
                            _tool_call_history.clear()
                            _tool_results.clear()
                            iter_model = self.config.brain_model
                            continue

                        await cb.on_event(AgentEvent(
                            HELP_NEEDED, _stuck_signal.context,
                            {"reason": _stuck_signal.reason, "tool": _stuck_signal.tool_name,
                             "needs_browser": _stuck_signal.needs_browser},
                        ))

                        # Ask user for help — waits indefinitely
                        _help_response = await cb.on_help_request(
                            _stuck_signal.context, _stuck_signal.needs_browser,
                        )

                        if _help_response == "skip":
                            all_new_messages.append(LLMMessage(
                                role="assistant",
                                content=f"I got stuck: {_stuck_signal.context}. Let me try a different approach.",
                            ))
                            break

                        # Browser handoff: user said "ready" → ensure visible → wait for "done"
                        if _stuck_signal.needs_browser and _help_response in (
                            "ready", "show me", "show", "ok", "yes",
                        ):
                            if not getattr(self, "is_background", False):
                                from lazyclaw.skills.builtin.browser_skill import (
                                    _get_visible_cdp_backend, _raise_browser_window,
                                )
                                await _get_visible_cdp_backend(user_id)
                                await _raise_browser_window()
                                # Give macOS time to bring window to foreground
                                await asyncio.sleep(1.0)
                                await cb.on_event(AgentEvent(
                                    HELP_RESPONSE,
                                    "Browser is visible. Take over and say 'done' when finished.",
                                    {},
                                ))
                                _done_resp = await cb.on_help_request(
                                    "Browser is open on your screen. Say 'done' when you're finished.",
                                    False,
                                )
                                if _done_resp == "skip":
                                    all_new_messages.append(LLMMessage(
                                        role="assistant",
                                        content=f"I got stuck: {_stuck_signal.context}. User chose to skip.",
                                    ))
                                    break

                        # Notify observability that help was received
                        await cb.on_event(AgentEvent(
                            HELP_RESPONSE,
                            f"User help received: {_help_response}",
                            {"response": _help_response},
                        ))

                        # Take snapshot after user intervention
                        try:
                            from lazyclaw.skills.builtin.browser_skill import _get_cdp_backend
                            _snap_backend = await _get_cdp_backend(user_id)
                            _snap_url = await _snap_backend.current_url()
                            _snap_title = await _snap_backend.title()
                            _snapshot = f"After user help: now on {_snap_title} ({_snap_url})"
                        except Exception:
                            _snapshot = f"User intervention complete. Response: {_help_response}"

                        # Inject snapshot as assistant message (NOT tool —
                        # tool results were already appended, so a second tool
                        # msg with the same id would orphan and crash OpenAI).
                        _help_msg = LLMMessage(
                            role="assistant", content=_snapshot,
                        )
                        messages.append(_help_msg)
                        all_new_messages.append(_help_msg)
                        _tool_call_history.clear()
                        _tool_results.clear()
                        continue

            else:
                # Safety cap reached — shouldn't happen often with nudge
                if self._team_lead and _fg_task_id:
                    self._team_lead.fail(_fg_task_id, "Safety cap reached")
                    _fg_task_id = None
                logger.warning(
                    "Safety cap reached (%d iterations). Last tool: %s",
                    max_iterations,
                    _tool_call_history[-1] if _tool_call_history else "none",
                )
                all_new_messages.append(
                    LLMMessage(
                        role="assistant",
                        content=(response.content if response and response.content else "")
                        or (
                            "I hit the safety limit. Here's what I've done so far. "
                            "Say 'continue' if you want me to keep going."
                        ),
                    )
                )
        except asyncio.CancelledError:
            if self._team_lead and _fg_task_id:
                self._team_lead.cancel(_fg_task_id)
            await cb.on_event(AgentEvent("done", "Cancelled", {}))
            if _delegate_registered and self.registry:
                self.registry.unregister("delegate")
            return "Operation cancelled."

        # ── Post-loop: persist + cleanup (guarded by finally) ─────────
        content = ""
        try:
            # Resolve chat session
            if not chat_session_id:
                async with db_session(self.config) as db:
                    row = await db.execute(
                        "SELECT id FROM agent_chat_sessions "
                        "WHERE user_id = ? AND archived_at IS NULL "
                        "ORDER BY created_at DESC LIMIT 1",
                        (user_id,),
                    )
                    existing = await row.fetchone()
                    if existing:
                        chat_session_id = existing[0]
                    else:
                        chat_session_id = str(uuid4())
                        await db.execute(
                            "INSERT INTO agent_chat_sessions (id, user_id) VALUES (?, ?)",
                            (chat_session_id, user_id),
                        )
                        await db.commit()

            # Store ALL messages (user, assistant, tool calls, tool results) encrypted
            rows = []
            for msg in all_new_messages:
                msg_id = str(uuid4())
                encrypted_content = encrypt(msg.content, key)
                tool_name = None
                metadata = None

                if msg.tool_calls:
                    metadata = json.dumps(
                        [
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                            for tc in msg.tool_calls
                        ]
                    )
                if msg.tool_call_id:
                    tool_name = msg.tool_call_id

                rows.append((msg_id, user_id, chat_session_id, msg.role, encrypted_content, tool_name, metadata))

            async with db_session(self.config) as db:
                await db.executemany(
                    "INSERT INTO agent_messages (id, user_id, chat_session_id, role, content, tool_name, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                await db.commit()

            # Record and return the final assistant message content
            final = all_new_messages[-1]
            content = final.content or ""
            if not content.strip():
                content = "I wasn't able to generate a response. Please try again."
            await recorder.record_final_response(content)

            # Fire work summary for direct mode (with model attribution)
            from lazyclaw.runtime.summary import build_work_summary
            from lazyclaw.llm.pricing import calculate_cost

            _models_used = []
            _total_cost = 0.0
            _routing = self.eco_router.last_routing if self.eco_router else None
            if _routing:
                _models_used.append((_routing.display_name, _routing.icon, _routing.is_local))
                _total_cost = calculate_cost(
                    _routing.model,
                    _session_tokens // 2,  # approximate split
                    _session_tokens - _session_tokens // 2,
                )

            _direct_summary = build_work_summary(
                start_time=_start_time,
                llm_calls=iteration + 1,
                tools_used=_all_tools_used,
                specialists=[],
                total_tokens=_session_tokens,
                user_message=message,
                response=content,
                models_used=_models_used,
                total_cost=_total_cost,
            )
            await cb.on_event(AgentEvent(
                "work_summary", "Task complete",
                {"summary": _direct_summary},
            ))

        finally:
            # ALWAYS mark foreground task done/failed in TeamLead
            if self._team_lead and _fg_task_id:
                if content:
                    self._team_lead.complete(_fg_task_id, content[:100])
                else:
                    self._team_lead.fail(_fg_task_id, "Post-loop error")

            await cb.on_event(AgentEvent("done", "Response ready", {}))

            # Clean up delegate skill to avoid stale callback references
            if _delegate_registered and self.registry:
                self.registry.unregister("delegate")

        return content
