from __future__ import annotations

import json
import logging

import httpx

from lazyclaw.llm.providers.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    StreamChunk,
    ToolCall,
)

logger = logging.getLogger(__name__)


class MinimaxProvider(BaseLLMProvider):
    """MiniMax Token Plan — OpenAI-compatible API at api.minimax.io/v1.

    Message serialization mirrors the OpenAI provider so tool calling
    works correctly (role="tool" + tool_call_id).
    """

    def __init__(self, api_key: str, base_url: str = "https://api.minimax.io/v1") -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    # ── Message serialization (mirrors OpenAI provider) ──────────────

    @staticmethod
    def _serialize_message(m: LLMMessage) -> dict:
        if m.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": m.tool_call_id,
                "content": m.content,
            }
        if m.role == "assistant" and m.tool_calls:
            return {
                "role": "assistant",
                "content": m.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ],
            }
        return {"role": m.role, "content": m.content}

    @staticmethod
    def _fold_system_messages(
        messages: list[LLMMessage],
    ) -> tuple[list[LLMMessage], str]:
        """MiniMax M2 rejects role="system" (error 2013).

        Extract all system messages and fold them into a combined system
        prompt string. The caller either passes it as a top-level ``system``
        field OR prepends it to the first user message as a defensive
        fallback.
        """
        system_parts: list[str] = []
        remaining: list[LLMMessage] = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
                continue
            remaining.append(m)
        system_text = "\n\n".join(system_parts).strip()
        return remaining, system_text

    @staticmethod
    def _inline_system_into_first_user(
        messages: list[LLMMessage], system_text: str,
    ) -> list[LLMMessage]:
        """Prepend system_text to the first user message's content.

        Returns a new list (does not mutate input). If no user message
        exists, prepends a synthetic user turn carrying the system text.
        """
        if not system_text:
            return messages
        out: list[LLMMessage] = []
        injected = False
        for m in messages:
            if not injected and m.role == "user":
                prefixed_content = f"[System instructions]\n{system_text}\n\n[User]\n{m.content}"
                out.append(LLMMessage(
                    role="user",
                    content=prefixed_content,
                    tool_call_id=m.tool_call_id,
                    tool_calls=m.tool_calls,
                ))
                injected = True
            else:
                out.append(m)
        if not injected:
            out.insert(0, LLMMessage(role="user", content=system_text))
        return out

    # ── Internals ────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self, messages: list[LLMMessage], model: str, stream: bool = False, **kwargs,
    ) -> dict:
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)

        # MiniMax M2 rejects role="system" — inline it into the first user message.
        # This is the defensive path that works regardless of whether MiniMax
        # supports a top-level `system` field.
        non_system_msgs, system_text = self._fold_system_messages(messages)
        if system_text:
            non_system_msgs = self._inline_system_into_first_user(
                non_system_msgs, system_text,
            )

        payload: dict = {
            "model": model,
            "messages": [self._serialize_message(m) for m in non_system_msgs],
            "stream": stream,
        }

        for k in ("temperature", "top_p", "stop"):
            if k in kwargs:
                payload[k] = kwargs.pop(k)

        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        return payload

    @staticmethod
    def _parse_tool_calls(raw_tcs: list[dict]) -> list[ToolCall] | None:
        try:
            return [
                ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("function", {}).get("name", ""),
                    arguments=json.loads(
                        tc.get("function", {}).get("arguments", "{}"),
                    ),
                )
                for tc in raw_tcs
            ]
        except Exception as exc:
            logger.warning("Failed to parse MiniMax tool calls: %s", exc)
            return None

    @staticmethod
    def _parse_usage(data: dict) -> dict | None:
        u = data.get("usage")
        if not u:
            return None
        return {
            "prompt_tokens": u.get("prompt_tokens", 0),
            "completion_tokens": u.get("completion_tokens", 0),
            "total_tokens": u.get("total_tokens", 0),
        }

    # ── Chat (non-streaming) ────────────────────────────────────────

    async def chat(self, messages: list[LLMMessage], model: str, **kwargs) -> LLMResponse:
        payload = self._build_payload(messages, model, stream=False, **kwargs)
        logger.info("MiniMax chat model=%s msgs=%d tools=%d", model, len(messages), len(payload.get("tools", [])))

        response = await self._client.post(
            "/text/chatcompletion_v2",
            json=payload,
            headers=self._headers(),
        )
        response.raise_for_status()
        data = response.json()

        # Check MiniMax-specific error response
        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            logger.warning(
                "MiniMax API error: code=%s msg=%s",
                base_resp.get("status_code"), base_resp.get("status_msg"),
            )

        content = ""
        tool_calls: list[ToolCall] | None = None

        if data.get("choices"):
            choice = data["choices"][0]
            msg_data = choice.get("message", {})
            content = msg_data.get("content", "") or ""

            # If content is empty, check if reasoning ended up there
            # (reasoning_split may put all output in reasoning_details)
            if not content:
                reasoning = msg_data.get("reasoning_content", "")
                if reasoning:
                    logger.debug("MiniMax: content empty but reasoning_content present (%d chars)", len(reasoning))

            raw_tcs = msg_data.get("tool_calls")
            if raw_tcs:
                tool_calls = self._parse_tool_calls(raw_tcs)
        else:
            logger.warning("MiniMax: no choices in response. keys=%s base_resp=%s", list(data.keys()), data.get("base_resp"))

        # Log when response is empty (helps diagnose silent failures)
        if not content and not tool_calls:
            # Dump message keys to understand what MiniMax returned
            if data.get("choices"):
                msg_keys = list(data["choices"][0].get("message", {}).keys())
                finish = data["choices"][0].get("finish_reason")
                logger.warning(
                    "MiniMax empty response: finish_reason=%s msg_keys=%s base_resp=%s",
                    finish, msg_keys, base_resp,
                )

        return LLMResponse(
            content=content,
            model=model,
            usage=self._parse_usage(data),
            tool_calls=tool_calls,
        )

    # ── Streaming ────────────────────────────────────────────────────

    async def stream_chat(self, messages: list[LLMMessage], model: str, **kwargs):
        payload = self._build_payload(messages, model, stream=True, **kwargs)

        collected_tool_calls: dict[int, dict] = {}
        usage: dict | None = None

        async with self._client.stream(
            "POST", "/text/chatcompletion_v2",
            json=payload,
            headers=self._headers(),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue

                try:
                    data = json.loads(data_str)
                except Exception:
                    continue

                # Usage may arrive in a chunk without choices
                if data.get("usage"):
                    usage = self._parse_usage(data)

                choices = data.get("choices")
                if not choices:
                    continue
                choice = choices[0]
                if not choice:
                    continue

                delta = choice.get("delta") or {}
                finish = choice.get("finish_reason")

                # Text content
                content = delta.get("content") or ""
                if content:
                    yield StreamChunk(delta=content, model=model)

                # Tool call deltas (same format as OpenAI)
                tc_deltas = delta.get("tool_calls")
                if tc_deltas:
                    for tc_delta in tc_deltas:
                        idx = tc_delta.get("index", 0)
                        if idx not in collected_tool_calls:
                            collected_tool_calls[idx] = {
                                "id": tc_delta.get("id", ""),
                                "name": "",
                                "arguments": "",
                            }
                        entry = collected_tool_calls[idx]
                        if tc_delta.get("id"):
                            entry["id"] = tc_delta["id"]
                        func = tc_delta.get("function") or {}
                        if func.get("name"):
                            entry["name"] = func["name"]
                        if func.get("arguments"):
                            entry["arguments"] += func["arguments"]

                # Finish — emit final chunk with tool calls and usage
                if finish is not None:
                    parsed_tcs = None
                    if collected_tool_calls:
                        try:
                            parsed_tcs = [
                                ToolCall(
                                    id=tc["id"],
                                    name=tc["name"],
                                    arguments=json.loads(tc["arguments"]) if tc["arguments"] else {},
                                )
                                for tc in collected_tool_calls.values()
                            ]
                        except Exception as exc:
                            logger.warning("Failed to parse streamed MiniMax tool calls: %s", exc)

                    yield StreamChunk(
                        delta="",
                        tool_calls=parsed_tcs,
                        usage=usage,
                        model=model,
                        done=True,
                    )
                    return

        # Safety: stream ended without finish_reason
        parsed_tcs = None
        if collected_tool_calls:
            try:
                parsed_tcs = [
                    ToolCall(
                        id=tc["id"],
                        name=tc["name"],
                        arguments=json.loads(tc["arguments"]) if tc["arguments"] else {},
                    )
                    for tc in collected_tool_calls.values()
                ]
            except Exception as exc:
                logger.warning("Failed to parse streamed MiniMax tool calls: %s", exc)

        yield StreamChunk(
            delta="",
            tool_calls=parsed_tcs,
            usage=usage,
            model=model,
            done=True,
        )

    # ── Key verification ─────────────────────────────────────────────

    async def verify_key(self) -> bool:
        try:
            response = await self._client.post(
                "/text/chatcompletion_v2",
                json={
                    "model": "MiniMax-M2.7",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers=self._headers(),
            )
            return response.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
