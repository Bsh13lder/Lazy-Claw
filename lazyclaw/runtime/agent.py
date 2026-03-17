from __future__ import annotations

import asyncio
import json
import logging
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

# Keywords that suggest MCP tools are needed
_MCP_KEYWORDS = (
    "claude code", "health check", "healthcheck", "api hunter", "apihunter",
    "free api", "free provider", "vault whisper", "vaultwhisper", "privacy",
    "pii", "scrub", "task ai", "taskai", "categorize", "prioritize",
    "deduplicate", "freeride", "mcp", "provider status", "leaderboard",
)

# Keywords that suggest any tools are needed (search, browse, compute, file ops, etc.)
_TOOL_KEYWORDS = (
    "search", "find", "look up", "browse", "open", "navigate", "click",
    "run", "execute", "command", "terminal", "shell",
    "read file", "write file", "create file", "delete file", "list dir",
    "screenshot", "remember", "save", "recall", "memory", "memorize",
    "calculate", "compute", "math",
    "vault", "credential", "api key", "password", "secret",
    "skill", "create skill", "delete skill",
    "time", "date", "what time", "what day",
    "web", "website", "url", "http", "page",
    "schedule", "cron", "remind",
)


def _wants_mcp_tools(message: str) -> bool:
    """Check if the user's message suggests MCP tools are needed."""
    lower = message.lower()
    return any(kw in lower for kw in _MCP_KEYWORDS)


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
    """Check if the user's message suggests ANY tool usage.

    Simple conversations (hello, greetings, questions, chat) → no tools.
    This prevents GPT-5 from running commands when the user just says "hi".
    """
    lower = message.lower().strip()
    # Very short messages are almost never tool-worthy
    if len(lower) < 10:
        return False
    return any(kw in lower for kw in _TOOL_KEYWORDS)


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

        # Initialize trace recorder
        from lazyclaw.replay.recorder import TraceRecorder
        recorder = TraceRecorder(self.config, user_id)
        await recorder.record_user_message(message)

        import asyncio as _aio

        # Parallel initialization — load independent data concurrently
        from lazyclaw.memory.compressor import compress_history
        from lazyclaw.skills.manager import load_user_skills
        from lazyclaw.runtime.context_builder import build_context

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

        history_rows, _, system_prompt = await _aio.gather(
            _load_history(),
            load_user_skills(self.config, user_id, self.registry),
            build_context(self.config, user_id),
        )

        history = await compress_history(
            self.config, self.eco_router, user_id, chat_session_id,
            raw_messages=history_rows,
        )

        # Check team mode — delegate to specialists if complex
        from lazyclaw.teams.settings import get_team_settings
        from lazyclaw.teams.specialist import load_specialists
        from lazyclaw.teams.lead import TeamLead

        team_settings = await get_team_settings(self.config, user_id)
        if team_settings.get("mode", "never") != "never":
            await cb.on_event(AgentEvent("team_delegate", "Evaluating team delegation...", {}))
            specialists = await load_specialists(self.config, user_id)
            team_lead = TeamLead(self.config, self.eco_router)
            team_result = await team_lead.process(
                user_id=user_id,
                message=message,
                settings=team_settings,
                specialists=specialists,
                registry=self.registry,
                permission_checker=self.executor._checker if self.executor else None,
                callback=cb,
                cancel_token=cancel_token,
            )
            if team_result is not None:
                await recorder.record_team_delegation("team_lead", message)
                await recorder.record_final_response(team_result)
                # Store user message + team response encrypted
                async with db_session(self.config) as db:
                    if not chat_session_id:
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

                    for role, content in [("user", message), ("assistant", team_result)]:
                        msg_id = str(uuid4())
                        await db.execute(
                            "INSERT INTO agent_messages "
                            "(id, user_id, chat_session_id, role, content) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (msg_id, user_id, chat_session_id, role, encrypt(content, key)),
                        )
                    await db.commit()

                return team_result

        # Get tools — only when the message suggests tool usage is needed
        # Simple chat (hello, questions, conversation) → no tools → fast direct response
        needs_tools = self.registry is not None and _wants_any_tools(message)
        tools: list = []
        if needs_tools:
            tools = self.registry.list_core_tools()
            mcp_tools = self.registry.list_mcp_tools()
            if mcp_tools and _wants_mcp_tools(message):
                tools = tools + mcp_tools
            logger.info("Tools enabled for message: %s", message[:50])
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
                    await cb.on_event(AgentEvent(
                        "tool_call", f"Using {tc.name}...",
                        {"tool": tc.name, "args": tc.arguments},
                    ))
                    await recorder.record_tool_call(tc.name, tc.arguments)
                    result = await self.executor.execute(tc, user_id)
                    await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))
                    await cb.on_event(AgentEvent(
                        "tool_result", f"{tc.name} done",
                        {"tool": tc.name},
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
                        approved = await cb.on_approval_request(skill_name, parsed_args)

                        if approved:
                            await cb.on_event(AgentEvent(
                                "approval", f"'{skill_name}' approved",
                                {"skill": skill_name, "approved": True},
                            ))
                            result = await self.executor.execute_allowed(tc, user_id)
                            await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))
                            await cb.on_event(AgentEvent(
                                "tool_result", f"{tc.name} done",
                                {"tool": tc.name},
                            ))
                        else:
                            await cb.on_event(AgentEvent(
                                "approval", f"'{skill_name}' denied",
                                {"skill": skill_name, "approved": False},
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
        async with db_session(self.config) as db:
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

                await db.execute(
                    "INSERT INTO agent_messages (id, user_id, chat_session_id, role, content, tool_name, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (msg_id, user_id, chat_session_id, msg.role, encrypted_content, tool_name, metadata),
                )
            await db.commit()

        # Record and return the final assistant message content
        final = all_new_messages[-1]
        content = final.content or ""
        if not content.strip():
            content = "I wasn't able to generate a response. Please try again."
        await recorder.record_final_response(content)
        await cb.on_event(AgentEvent("done", "Response ready", {}))
        return content
