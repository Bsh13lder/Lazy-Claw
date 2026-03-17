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

    def list_core_tools(self) -> list[dict]:
        """Return only built-in/user skills (no MCP tools) in OpenAI format."""
        return [
            skill.to_openai_tool()
            for skill in self._skills.values()
            if skill.category != "mcp"
        ]

    def list_mcp_tools(self) -> list[dict]:
        """Return only MCP-bridged skills in OpenAI format."""
        return [
            skill.to_openai_tool()
            for skill in self._skills.values()
            if skill.category == "mcp"
        ]

    def get_display_name(self, internal_name: str) -> str:
        """Resolve internal tool name to human-friendly display name."""
        skill = self._skills.get(internal_name)
        if skill is not None:
            return skill.display_name
        return internal_name

    def list_by_category(self) -> dict[str, list[str]]:
        """Return skill names grouped by category."""
        categories: dict[str, list[str]] = {}
        for skill in self._skills.values():
            cat = skill.category
            categories.setdefault(cat, []).append(skill.name)
        return categories

    def register_defaults(self, config=None) -> None:
        """Register all built-in skills."""
        from lazyclaw.skills.builtin.web_search import WebSearchSkill
        from lazyclaw.skills.builtin.get_time import GetTimeSkill
        from lazyclaw.skills.builtin.calculate import CalculateSkill
        from lazyclaw.skills.builtin.memory_save import MemorySaveSkill
        from lazyclaw.skills.builtin.memory_recall import MemoryRecallSkill

        self.register(WebSearchSkill())
        self.register(GetTimeSkill())
        self.register(CalculateSkill())
        self.register(MemorySaveSkill(config=config))
        self.register(MemoryRecallSkill(config=config))

        from lazyclaw.skills.builtin.vault import VaultSetSkill, VaultListSkill, VaultDeleteSkill

        self.register(VaultSetSkill(config=config))
        self.register(VaultListSkill(config=config))
        self.register(VaultDeleteSkill(config=config))

        from lazyclaw.skills.builtin.skill_crud import CreateSkillSkill, ListSkillsSkill, DeleteSkillSkill

        self.register(CreateSkillSkill(config=config))
        self.register(ListSkillsSkill(config=config))
        self.register(DeleteSkillSkill(config=config))

        from lazyclaw.skills.builtin.browser import BrowseWebSkill, ReadPageSkill, SaveSiteLoginSkill

        self.register(BrowseWebSkill(config=config))
        self.register(ReadPageSkill(config=config))
        self.register(SaveSiteLoginSkill(config=config))

        from lazyclaw.skills.builtin.computer import (
            RunCommandSkill, ReadFileSkill, WriteFileSkill,
            ListDirectorySkill, TakeScreenshotSkill,
        )

        self.register(RunCommandSkill(config=config))
        self.register(ReadFileSkill(config=config))
        self.register(WriteFileSkill(config=config))
        self.register(ListDirectorySkill(config=config))
        self.register(TakeScreenshotSkill(config=config))

        # Job & reminder skills
        from lazyclaw.skills.builtin.jobs import (
            ScheduleJobSkill, SetReminderSkill, ListJobsSkill, ManageJobSkill,
        )

        self.register(ScheduleJobSkill(config=config))
        self.register(SetReminderSkill(config=config))
        self.register(ListJobsSkill(config=config))
        self.register(ManageJobSkill(config=config))

        # Real browser skills (CDP — control user's actual Chrome)
        from lazyclaw.skills.builtin.real_browser import (
            SeeBrowserSkill, ListTabsSkill, ReadTabSkill,
            SwitchTabSkill, BrowserActionSkill,
        )

        self.register(SeeBrowserSkill(config=config))
        self.register(ListTabsSkill(config=config))
        self.register(ReadTabSkill(config=config))
        self.register(SwitchTabSkill(config=config))
        self.register(BrowserActionSkill(config=config))
