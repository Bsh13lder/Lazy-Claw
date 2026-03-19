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
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session

from lazyclaw.runtime.callbacks import AgentEvent, CancellationToken, NullCallback
from lazyclaw.runtime.tool_executor import APPROVAL_PREFIX, ToolExecutor
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Chat-only patterns — messages that NEVER need tools.
# Everything else gets tools and the LLM decides what to use.
_CHAT_ONLY_PATTERN = re.compile(
    r"^(hi|hey|hello|yo|sup|thanks|thank you|thx|ok|okay|sure|"
    r"yes|no|yep|nope|good morning|good night|good evening|"
    r"bye|goodbye|see you|how are you|what's up|whats up|"
    r"nice|cool|great|awesome|lol|haha|hehe|wow|omg|"
    r"good|bad|fine|alright|sounds good|got it|understood|"
    r"perfect|exactly|right|correct|wrong|nah|meh)[\s!?.,]*$",
    re.IGNORECASE,
)


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
    # Very short messages (under 8 chars) — greetings like "hi", "ok"
    if len(lower) < 8:
        return False
    # Known chat-only patterns — no tools needed
    if _CHAT_ONLY_PATTERN.match(lower):
        return False
    # Everything else: give the LLM tools and let it decide
    return True


class Agent:
    def __init__(
        self,
        config: Config,
        router: LLMRouter,
        registry: SkillRegistry | None = None,
        eco_router: EcoRouter | None = None,
        permission_checker=None,
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

    async def process_message(
        self,
        user_id: str,
        message: str,
        chat_session_id: str | None = None,
        callback=None,
    ) -> str:
        cb = callback or NullCallback()
        cancel_token = CancellationToken()
        if hasattr(cb, 'cancel_token'):
            cb.cancel_token = cancel_token
        key = derive_server_key(self.config.server_secret, user_id)
        _start_time = time.monotonic()
        _all_tools_used: list[str] = []
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

        # Get tools — all or nothing. Chat-only messages get no tools (fast path).
        # Everything else gets ALL tools (core + MCP) and the LLM decides.
        needs_tools = self.registry is not None and _wants_any_tools(message)
        tools: list = []
        if needs_tools:
            tools = self.registry.list_core_tools() + self.registry.list_mcp_tools()
            logger.info("Tools enabled (%d) for: %s", len(tools), message[:50])
        else:
            logger.info("No tools — fast chat path for: %s", message[:50])

        # Build context — for simple chat, use minimal history (fast path)
        if needs_tools:
            # Full history for tool-capable requests
            chat_history = history
        else:
            # Minimal history for simple chat — last 6 user/assistant messages only
            # This drops the huge summary + old context, making GPT-5 respond in ~5s
            chat_history = _strip_tool_messages(history)
            if len(chat_history) > 6:
                chat_history = chat_history[-6:]

        messages: list[LLMMessage] = (
            [LLMMessage(role="system", content=system_prompt)]
            + chat_history
            + [LLMMessage(role="user", content=message)]
        )
        logger.info("Context: %d messages (%d history + system + user), tools=%d",
                     len(messages), len(chat_history), len(tools))

        # Agentic loop
        max_iterations = self.config.max_tool_iterations
        all_new_messages: list[LLMMessage] = [LLMMessage(role="user", content=message)]

        response = None
        iteration = 0
        try:
            for iteration in range(max_iterations):
                if cancel_token.is_cancelled:
                    await cb.on_event(AgentEvent("done", "Cancelled", {}))
                    return "Operation cancelled."

                kwargs: dict = {}
                if tools:
                    kwargs["tools"] = tools

                model_name = self.config.default_model
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
                        messages, user_id=user_id, **kwargs
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
                    result = await self.executor.execute(tc, user_id, callback=cb)
                    await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))
                    await cb.on_event(AgentEvent(
                        "tool_result", _display,
                        {"tool": tc.name, "display_name": _display},
                    ))

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
            else:
                # Max iterations reached
                all_new_messages.append(
                    LLMMessage(
                        role="assistant",
                        content=(response.content if response and response.content else "")
                        or "I've reached the maximum number of tool calls. Here's what I found so far.",
                    )
                )
        except asyncio.CancelledError:
            await cb.on_event(AgentEvent("done", "Cancelled", {}))
            if _delegate_registered and self.registry:
                self.registry.unregister("delegate")
            return "Operation cancelled."

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
        # Batch insert for performance (~10-20ms savings over individual inserts)
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

        # Fire work summary for direct mode
        from lazyclaw.runtime.summary import build_work_summary
        _direct_summary = build_work_summary(
            start_time=_start_time,
            llm_calls=iteration + 1,
            tools_used=_all_tools_used,
            specialists=[],
            total_tokens=_session_tokens,
            user_message=message,
            response=content,
        )
        await cb.on_event(AgentEvent(
            "work_summary", "Task complete",
            {"summary": _direct_summary},
        ))

        await cb.on_event(AgentEvent("done", "Response ready", {}))

        # Clean up delegate skill to avoid stale callback references
        if _delegate_registered and self.registry:
            self.registry.unregister("delegate")

        return content
