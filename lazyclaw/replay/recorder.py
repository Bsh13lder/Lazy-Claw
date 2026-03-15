"""Trace recorder — captures agent actions into the agent_traces table.

Fire-and-forget recording: errors in recording never block the agent.
All content encrypted at rest using the user's server-derived key.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, encrypt
from lazyclaw.db.connection import db_session
from lazyclaw.replay.models import (
    CRITIC_REVIEW,
    FINAL_RESPONSE,
    LLM_CALL,
    LLM_RESPONSE,
    TEAM_DELEGATION,
    TEAM_RESULT,
    TOOL_CALL,
    TOOL_RESULT,
    USER_MESSAGE,
)

logger = logging.getLogger(__name__)


class TraceRecorder:
    """Records agent actions into a trace session.

    Usage:
        recorder = TraceRecorder(config, user_id)
        session_id = recorder.session_id
        await recorder.record_user_message(message)
        await recorder.record_llm_call(messages_summary)
        await recorder.record_tool_call(name, args)
        ...
    """

    def __init__(self, config: Config, user_id: str) -> None:
        self._config = config
        self._user_id = user_id
        self._key = derive_server_key(config.server_secret, user_id)
        self._session_id = str(uuid4())
        self._sequence = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    async def _record(
        self, entry_type: str, content: str, metadata: dict | None = None
    ) -> None:
        """Store a trace entry. Fire-and-forget — never raises."""
        try:
            self._sequence += 1
            entry_id = str(uuid4())
            encrypted_content = encrypt(content, self._key)
            metadata_json = json.dumps(metadata) if metadata else None

            async with db_session(self._config) as db:
                await db.execute(
                    "INSERT INTO agent_traces "
                    "(id, user_id, trace_session_id, sequence, entry_type, "
                    "content, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (entry_id, self._user_id, self._session_id,
                     self._sequence, entry_type, encrypted_content,
                     metadata_json),
                )
                await db.commit()
        except Exception as exc:
            logger.warning("Trace recording failed (non-fatal): %s", exc)

    async def record_user_message(self, message: str) -> None:
        """Record the user's input message."""
        await self._record(USER_MESSAGE, message)

    async def record_llm_call(
        self, model: str | None = None, message_count: int = 0, has_tools: bool = False
    ) -> None:
        """Record an LLM API call."""
        content = f"LLM call: model={model or 'default'}, messages={message_count}"
        await self._record(
            LLM_CALL, content,
            metadata={"model": model, "message_count": message_count, "has_tools": has_tools},
        )

    async def record_llm_response(
        self, content: str, model: str | None = None, has_tool_calls: bool = False
    ) -> None:
        """Record the LLM's response."""
        summary = content[:500] if content else "(empty)"
        await self._record(
            LLM_RESPONSE, summary,
            metadata={"model": model, "has_tool_calls": has_tool_calls, "length": len(content or "")},
        )

    async def record_tool_call(self, tool_name: str, arguments: dict | None = None) -> None:
        """Record a tool invocation."""
        args_summary = json.dumps(arguments)[:300] if arguments else "{}"
        await self._record(
            TOOL_CALL, f"Tool call: {tool_name}({args_summary})",
            metadata={"tool_name": tool_name, "arguments": arguments},
        )

    async def record_tool_result(self, tool_name: str, result: str) -> None:
        """Record a tool's result."""
        summary = result[:500] if result else "(empty)"
        await self._record(
            TOOL_RESULT, f"Tool result ({tool_name}): {summary}",
            metadata={"tool_name": tool_name, "result_length": len(result or "")},
        )

    async def record_team_delegation(
        self, specialist_name: str, instruction: str
    ) -> None:
        """Record the team lead delegating to a specialist."""
        await self._record(
            TEAM_DELEGATION,
            f"Delegated to {specialist_name}: {instruction[:300]}",
            metadata={"specialist": specialist_name},
        )

    async def record_team_result(
        self, specialist_name: str, result: str, success: bool = True
    ) -> None:
        """Record a specialist's result."""
        summary = result[:500] if result else "(empty)"
        await self._record(
            TEAM_RESULT,
            f"Result from {specialist_name}: {summary}",
            metadata={"specialist": specialist_name, "success": success},
        )

    async def record_critic_review(self, review: str) -> None:
        """Record the critic's review."""
        await self._record(CRITIC_REVIEW, review[:500])

    async def record_final_response(self, response: str) -> None:
        """Record the final response sent to the user."""
        await self._record(FINAL_RESPONSE, response[:500])
