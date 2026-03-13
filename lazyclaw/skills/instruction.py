from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class InstructionSkill(BaseSkill):
    """A user-created skill backed by a natural language instruction.

    When invoked, the agent receives the instruction as a tool result
    and follows it to complete the task.
    """

    def __init__(
        self,
        skill_name: str,
        skill_description: str,
        instruction: str,
        params_schema: dict | None = None,
    ) -> None:
        self._name = skill_name
        self._description = skill_description
        self._instruction = instruction
        self._params_schema = params_schema or {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Optional input or context for this skill",
                },
            },
            "required": [],
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict:
        return self._params_schema

    async def execute(self, user_id: str, params: dict) -> str:
        user_input = params.get("input", "")
        result = f"Follow this instruction:\n\n{self._instruction}"
        if user_input:
            result += f"\n\nUser provided context: {user_input}"
        return result
