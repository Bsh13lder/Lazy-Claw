from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class CreateSkillSkill(BaseSkill):
    """Agent skill to create new instruction skills for the user."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "create_skill"

    @property
    def description(self) -> str:
        return (
            "Create a new custom skill from a natural language instruction. "
            "The skill becomes a reusable tool the agent can invoke. "
            "Example: create a 'daily_standup' skill that asks standup questions."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name (lowercase, underscores, e.g. 'daily_standup')",
                },
                "description": {
                    "type": "string",
                    "description": "Short description of what the skill does",
                },
                "instruction": {
                    "type": "string",
                    "description": "The natural language instruction the agent follows when this skill is invoked",
                },
            },
            "required": ["name", "description", "instruction"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Skills not configured"
        from lazyclaw.skills.manager import create_instruction_skill
        skill_id = await create_instruction_skill(
            self._config, user_id,
            params["name"], params["description"], params["instruction"],
        )
        return f"Skill '{params['name']}' created. It will be available in your next message."


class ListSkillsSkill(BaseSkill):
    """Agent skill to list user's custom skills."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "list_skills"

    @property
    def description(self) -> str:
        return "List all custom skills the user has created."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Skills not configured"
        from lazyclaw.skills.manager import list_user_skills
        skills = await list_user_skills(self._config, user_id)
        if not skills:
            return "No custom skills created yet."
        lines = [f"- **{s['name']}** ({s['type']}): {s['description']}" for s in skills]
        return "Your custom skills:\n" + "\n".join(lines)


class DeleteSkillSkill(BaseSkill):
    """Agent skill to delete a custom skill."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "delete_skill"

    @property
    def description(self) -> str:
        return "Delete a custom skill by name."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the skill to delete",
                },
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Skills not configured"
        from lazyclaw.skills.manager import delete_user_skill
        deleted = await delete_user_skill(self._config, user_id, params["name"])
        if deleted:
            return f"Skill '{params['name']}' deleted."
        return f"No skill found with name '{params['name']}'."
