from __future__ import annotations

import json
import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.llm.router import LLMRouter
from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session

from lazyclaw.runtime.tool_executor import APPROVAL_PREFIX, ToolExecutor
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


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
            ToolExecutor(registry, permission_checker=permission_checker)
            if registry
            else None
        )

    async def process_message(
        self,
        user_id: str,
        message: str,
        chat_session_id: str | None = None,
    ) -> str:
        key = derive_server_key(self.config.server_secret, user_id)

        # Initialize trace recorder
        from lazyclaw.replay.recorder import TraceRecorder
        recorder = TraceRecorder(self.config, user_id)
        await recorder.record_user_message(message)

        # Load conversation history with compression
        async with db_session(self.config) as db:
            rows = await db.execute(
                "SELECT id, role, content, tool_name, metadata FROM agent_messages "
                "WHERE user_id = ? ORDER BY created_at ASC",
                (user_id,),
            )
            history_rows = await rows.fetchall()

        from lazyclaw.memory.compressor import compress_history
        history = await compress_history(
            self.config, self.eco_router, user_id, chat_session_id,
            raw_messages=history_rows,
        )

        # Load user's custom instruction skills into registry
        from lazyclaw.skills.manager import load_user_skills
        await load_user_skills(self.config, user_id, self.registry)

        # Build prompt with memories
        from lazyclaw.runtime.context_builder import build_context
        system_prompt = await build_context(self.config, user_id)

        # Check team mode — delegate to specialists if complex
        from lazyclaw.teams.settings import get_team_settings
        from lazyclaw.teams.specialist import load_specialists
        from lazyclaw.teams.lead import TeamLead

        team_settings = await get_team_settings(self.config, user_id)
        if team_settings.get("mode", "auto") != "never":
            specialists = await load_specialists(self.config, user_id)
            team_lead = TeamLead(self.config, self.eco_router)
            team_result = await team_lead.process(
                user_id=user_id,
                message=message,
                settings=team_settings,
                specialists=specialists,
                registry=self.registry,
                permission_checker=self.executor._checker if self.executor else None,
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

            await recorder.record_llm_call(
                model=None, message_count=len(messages), has_tools=bool(tools),
            )
            response = await self.eco_router.chat(messages, user_id=user_id, **kwargs)
            await recorder.record_llm_response(
                content=response.content or "",
                model=response.model,
                has_tool_calls=bool(response.tool_calls),
            )

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
                await recorder.record_tool_call(tc.name, tc.arguments)
                result = await self.executor.execute(tc, user_id)
                await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))

                # Handle approval-required responses
                if isinstance(result, str) and result.startswith(APPROVAL_PREFIX):
                    parts = result[len(APPROVAL_PREFIX):]
                    colon_idx = parts.index(":")
                    skill_name = parts[:colon_idx]
                    args_json = parts[colon_idx + 1:]

                    # Create approval request in DB
                    from lazyclaw.permissions.approvals import create_approval
                    from lazyclaw.permissions.settings import get_permission_settings

                    settings = await get_permission_settings(self.config, user_id)
                    timeout = settings.get("auto_approve_timeout", 300)

                    approval = await create_approval(
                        self.config,
                        user_id,
                        skill_name,
                        json.loads(args_json) if args_json != "{}" else None,
                        source="agent",
                        timeout_seconds=timeout,
                    )

                    # Tell the LLM the action needs approval
                    result = (
                        f"Action '{skill_name}' requires user approval. "
                        f"Approval ID: {approval.id}. "
                        f"Tell the user what you want to do and ask them to approve or deny. "
                        f"Do NOT proceed until approved."
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
        await recorder.record_final_response(final.content)
        return final.content
