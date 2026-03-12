from __future__ import annotations

import json
import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.llm.router import LLMRouter
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session

from lazyclaw.runtime.tool_executor import ToolExecutor
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class Agent:
    def __init__(
        self,
        config: Config,
        router: LLMRouter,
        registry: SkillRegistry | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.registry = registry
        self.executor = ToolExecutor(registry) if registry else None

    async def process_message(
        self,
        user_id: str,
        message: str,
        chat_session_id: str | None = None,
    ) -> str:
        key = derive_server_key(self.config.server_secret, user_id)

        # Load conversation history
        async with db_session(self.config) as db:
            rows = await db.execute(
                "SELECT role, content FROM agent_messages "
                "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
                (user_id,),
            )
            history_rows = await rows.fetchall()

        history: list[LLMMessage] = []
        for role, content in reversed(history_rows):
            decrypted = decrypt(content, key) if content.startswith("enc:") else content
            history.append(LLMMessage(role=role, content=decrypted))

        # Build prompt with memories
        from lazyclaw.runtime.context_builder import build_context
        system_prompt = await build_context(self.config, user_id)

        # Get tools
        tools = self.registry.list_tools() if self.registry else []

        # Build initial messages
        messages: list[LLMMessage] = (
            [LLMMessage(role="system", content=system_prompt)]
            + history
            + [LLMMessage(role="user", content=message)]
        )

        # Agentic loop
        max_iterations = 10
        all_new_messages: list[LLMMessage] = [LLMMessage(role="user", content=message)]

        response = None
        for _ in range(max_iterations):
            kwargs: dict = {}
            if tools:
                kwargs["tools"] = tools

            response = await self.router.chat(messages, user_id=user_id, **kwargs)

            if not response.tool_calls:
                # Final text response
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
                result = await self.executor.execute(tc, user_id)
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

        # Return the final assistant message content
        final = all_new_messages[-1]
        return final.content
