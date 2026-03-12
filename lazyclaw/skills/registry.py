from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def list_tools(self) -> list[dict]:
        """Return all skills in OpenAI function-calling format."""
        return [skill.to_openai_tool() for skill in self._skills.values()]

    def register_defaults(self) -> None:
        """Register all built-in skills."""
        from lazyclaw.skills.builtin.web_search import WebSearchSkill
        from lazyclaw.skills.builtin.get_time import GetTimeSkill
        from lazyclaw.skills.builtin.calculate import CalculateSkill

        self.register(WebSearchSkill())
        self.register(GetTimeSkill())
        self.register(CalculateSkill())
