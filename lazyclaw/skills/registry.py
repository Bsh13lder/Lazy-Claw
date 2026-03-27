from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}
        # Cached tool lists — invalidated on register/unregister
        self._core_cache: list[dict] | None = None
        self._mcp_cache: list[dict] | None = None
        self._all_cache: list[dict] | None = None

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill
        self._invalidate_cache()

    def unregister(self, name: str) -> None:
        """Remove a skill by name. No-op if not found."""
        if name in self._skills:
            del self._skills[name]
            self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        self._core_cache = None
        self._mcp_cache = None
        self._all_cache = None

    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def get_mcp_by_base_name(self, base_name: str) -> BaseSkill | None:
        """Find first MCP skill matching a base tool name."""
        suffix = f"_{base_name}"
        for name, skill in self._skills.items():
            if name.startswith("mcp_") and name.endswith(suffix):
                return skill
        return None

    def list_tools(self) -> list[dict]:
        """Return all skills in OpenAI function-calling format (cached)."""
        if self._all_cache is None:
            self._all_cache = [
                skill.to_openai_tool() for skill in self._skills.values()
            ]
        return self._all_cache

    def list_core_tools(self) -> list[dict]:
        """Return only built-in/user skills (no MCP) in OpenAI format (cached)."""
        if self._core_cache is None:
            self._core_cache = [
                skill.to_openai_tool()
                for skill in self._skills.values()
                if skill.category != "mcp"
            ]
        return self._core_cache

    def list_mcp_tools(self) -> list[dict]:
        """Return only MCP-bridged skills in OpenAI format (cached)."""
        if self._mcp_cache is None:
            self._mcp_cache = [
                skill.to_openai_tool()
                for skill in self._skills.values()
                if skill.category == "mcp"
            ]
        return self._mcp_cache

    def get_tool_schema(self, name: str) -> dict | None:
        """Get the OpenAI-format tool schema for a single tool by name."""
        skill = self._skills.get(name)
        if skill is not None:
            return skill.to_openai_tool()
        return None

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
        from lazyclaw.skills.builtin.tool_discovery import SearchToolsSkill

        self.register(SearchToolsSkill(registry=self))
        self.register(WebSearchSkill())
        self.register(GetTimeSkill())
        self.register(CalculateSkill())
        self.register(MemorySaveSkill(config=config))
        self.register(MemoryRecallSkill(config=config))

        from lazyclaw.skills.builtin.vault import (
            VaultSetSkill, VaultListSkill, VaultDeleteSkill, SaveSiteLoginSkill,
        )

        self.register(VaultSetSkill(config=config))
        self.register(VaultListSkill(config=config))
        self.register(VaultDeleteSkill(config=config))
        self.register(SaveSiteLoginSkill(config=config))

        from lazyclaw.skills.builtin.skill_crud import CreateSkillSkill, ListSkillsSkill, DeleteSkillSkill

        self.register(CreateSkillSkill(config=config))
        self.register(ListSkillsSkill(config=config))
        self.register(DeleteSkillSkill(config=config))

        from lazyclaw.skills.builtin.browser_skill import BrowserSkill

        self.register(BrowserSkill(config=config))

        from lazyclaw.skills.builtin.payment_skill import PaymentSkill

        self.register(PaymentSkill(config=config))

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

        # Note: real_browser.py skills removed — merged into BrowserSkill above

        # Browser management skills
        from lazyclaw.skills.builtin.browser_management import (
            BrowserSetPersistentSkill, BrowserApproveConnectSkill,
        )

        self.register(BrowserSetPersistentSkill(config=config))
        self.register(BrowserApproveConnectSkill(config=config))

        # Watcher skills (zero-token site monitoring)
        from lazyclaw.skills.builtin.watcher_skills import (
            WatchSiteSkill, StopWatcherSkill, ListWatchersSkill,
        )

        self.register(WatchSiteSkill(config=config))
        self.register(StopWatcherSkill(config=config))
        self.register(ListWatchersSkill(config=config))

        # MCP watcher skill (WhatsApp, Email monitoring via MCP)
        from lazyclaw.skills.builtin.watch_mcp import WatchMCPSkill
        self.register(WatchMCPSkill(config=config))

        # AI management skills (ECO mode, providers, Ollama)
        from lazyclaw.skills.builtin.eco_management import (
            EcoSetModeSkill, EcoShowStatusSkill, EcoSetProviderSkill,
            EcoSetModelSkill, EcoListModelsSkill,
        )

        self.register(EcoSetModeSkill(config=config))
        self.register(EcoShowStatusSkill(config=config))
        self.register(EcoSetProviderSkill(config=config))
        self.register(EcoSetModelSkill(config=config))
        self.register(EcoListModelsSkill(config=config))

        from lazyclaw.skills.builtin.provider_management import (
            ProviderListSkill, ProviderAddSkill, ProviderScanSkill,
        )

        self.register(ProviderListSkill(config=config))
        self.register(ProviderAddSkill(config=config))
        self.register(ProviderScanSkill(config=config))

        from lazyclaw.skills.builtin.ollama_management import (
            OllamaListSkill, OllamaInstallSkill, OllamaDeleteSkill, OllamaShowSkill,
        )

        self.register(OllamaListSkill(config=config))
        self.register(OllamaInstallSkill(config=config))
        self.register(OllamaDeleteSkill(config=config))
        self.register(OllamaShowSkill(config=config))

        # System status skills
        from lazyclaw.skills.builtin.system_status import (
            ShowStatusSkill, RunDoctorSkill, ShowUsageSkill,
            ShowLogsSkill, SetModelSkill,
        )

        self.register(ShowStatusSkill(config=config))
        self.register(RunDoctorSkill(config=config))
        self.register(ShowUsageSkill(config=config))
        self.register(ShowLogsSkill(config=config))
        self.register(SetModelSkill(config=config))

        # Permission management skills
        from lazyclaw.skills.builtin.permission_management import (
            ShowPermissionsSkill, SetPermissionSkill, ListPendingApprovalsSkill,
            DecideApprovalSkill, QueryAuditLogSkill,
        )

        self.register(ShowPermissionsSkill(config=config))
        self.register(SetPermissionSkill(config=config))
        self.register(ListPendingApprovalsSkill(config=config))
        self.register(DecideApprovalSkill(config=config))
        self.register(QueryAuditLogSkill(config=config))

        # MCP management skills
        from lazyclaw.skills.builtin.mcp_management import (
            ListMCPServersSkill, AddMCPServerSkill, RemoveMCPServerSkill,
            ConnectMCPServerSkill, DisconnectMCPServerSkill,
            ConnectRemoteMCPSkill,
            FavoriteMCPServerSkill, UnfavoriteMCPServerSkill,
        )

        self.register(ListMCPServersSkill(config=config))
        self.register(AddMCPServerSkill(config=config))
        self.register(RemoveMCPServerSkill(config=config))
        self.register(ConnectMCPServerSkill(config=config))
        self.register(DisconnectMCPServerSkill(config=config))
        self.register(ConnectRemoteMCPSkill(config=config, registry=self))
        self.register(FavoriteMCPServerSkill(config=config))
        self.register(UnfavoriteMCPServerSkill(config=config))

        # Team management skills
        from lazyclaw.skills.builtin.team_management import (
            ShowTeamSettingsSkill, SetTeamModeSkill, SetCriticModeSkill,
            ListSpecialistsSkill, ManageSpecialistSkill,
        )

        self.register(ShowTeamSettingsSkill(config=config))
        self.register(SetTeamModeSkill(config=config))
        self.register(SetCriticModeSkill(config=config))
        self.register(ListSpecialistsSkill(config=config))
        self.register(ManageSpecialistSkill(config=config))

        # Memory management skills (extends existing save_memory + recall_memories)
        from lazyclaw.skills.builtin.memory_management import (
            ListMemoriesSkill, DeleteMemorySkill, ListDailyLogsSkill,
            ViewDailyLogSkill, DeleteDailyLogSkill,
        )

        self.register(ListMemoriesSkill(config=config))
        self.register(DeleteMemorySkill(config=config))
        self.register(ListDailyLogsSkill(config=config))
        self.register(ViewDailyLogSkill(config=config))
        self.register(DeleteDailyLogSkill(config=config))

        # Replay management skills
        from lazyclaw.skills.builtin.replay_management import (
            ListTracesSkill, ViewTraceSkill, DeleteTraceSkill,
            ShareTraceSkill, ManageSharesSkill,
        )

        self.register(ListTracesSkill(config=config))
        self.register(ViewTraceSkill(config=config))
        self.register(DeleteTraceSkill(config=config))
        self.register(ShareTraceSkill(config=config))
        self.register(ManageSharesSkill(config=config))

        # Session management skills
        from lazyclaw.skills.builtin.session_management import (
            ClearHistorySkill, ShowCompressionSkill,
        )

        self.register(ClearHistorySkill(config=config))
        self.register(ShowCompressionSkill(config=config))

        # Site memory management skills
        from lazyclaw.skills.builtin.site_memory_management import (
            ListSiteMemoriesSkill, DeleteSiteMemorySkill,
        )

        self.register(ListSiteMemoriesSkill(config=config))
        self.register(DeleteSiteMemorySkill(config=config))

        # Agent limit management skills
        from lazyclaw.skills.builtin.agent_limits import (
            SetMaxAgentsSkill, SetRamLimitSkill,
            ToggleAutoDelegateSkill, ShowAgentLimitsSkill,
        )

        self.register(SetMaxAgentsSkill(config=config))
        self.register(SetRamLimitSkill(config=config))
        self.register(ToggleAutoDelegateSkill(config=config))
        self.register(ShowAgentLimitsSkill(config=config))

        # Survival skills (job hunting + gig execution pipeline)
        from lazyclaw.skills.builtin.survival import (
            ApplyJobSkill,
            InvoiceClientSkill,
            ReviewDeliverableSkill,
            SearchJobsSkill,
            SetSkillsProfileSkill,
            StartGigSkill,
            SubmitDeliverableSkill,
            SurvivalModeSkill,
            SurvivalStatusSkill,
        )

        self.register(SearchJobsSkill(config=config, registry=self))
        self.register(ApplyJobSkill(config=config, registry=self))
        self.register(SurvivalModeSkill(config=config))
        self.register(SetSkillsProfileSkill(config=config))
        self.register(SurvivalStatusSkill(config=config))
        self.register(ReviewDeliverableSkill(config=config, registry=self))
        self.register(StartGigSkill(config=config, registry=self))
        self.register(SubmitDeliverableSkill(config=config, registry=self))
        self.register(InvoiceClientSkill(config=config, registry=self))
