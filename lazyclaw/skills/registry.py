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
        from lazyclaw.skills.builtin.keyword_research import KeywordResearchSkill
        from lazyclaw.skills.builtin.get_time import GetTimeSkill
        from lazyclaw.skills.builtin.calculate import CalculateSkill
        from lazyclaw.skills.builtin.memory_save import MemorySaveSkill
        from lazyclaw.skills.builtin.memory_recall import MemoryRecallSkill
        from lazyclaw.skills.builtin.tool_discovery import SearchToolsSkill
        from lazyclaw.skills.builtin.send_email import SendEmailSkill

        self.register(SearchToolsSkill(registry=self))
        self.register(WebSearchSkill())
        self.register(KeywordResearchSkill())
        self.register(GetTimeSkill())
        self.register(CalculateSkill())
        self.register(MemorySaveSkill(config=config))
        self.register(MemoryRecallSkill(config=config))
        self.register(SendEmailSkill(config=config, registry=self))

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

        # Task manager skills (second brain)
        from lazyclaw.skills.builtin.task_manager import (
            AddTaskSkill, ListTasksSkill, CompleteTaskSkill,
            FailTaskSkill,
            UpdateTaskSkill, DeleteTaskSkill, DailyBriefingSkill,
            WorkTodosSkill, StopBackgroundSkill,
        )

        self.register(AddTaskSkill(config=config))
        self.register(ListTasksSkill(config=config))
        self.register(CompleteTaskSkill(config=config))
        self.register(FailTaskSkill(config=config))
        self.register(UpdateTaskSkill(config=config))
        self.register(DeleteTaskSkill(config=config))
        self.register(DailyBriefingSkill(config=config))
        self.register(WorkTodosSkill(config=config))
        self.register(StopBackgroundSkill(config=config))

        # LazyBrain skills — Python-native Logseq-style PKM shared with the agent
        from lazyclaw.skills.builtin.lazybrain import (
            AskNotesSkill, MorningBriefingSkill, ReindexEmbeddingsSkill,
            SemanticSearchSkill, SuggestLinksSkill, SuggestMetadataSkill,
            TopicRollupSkill,
            SaveNoteSkill, UpdateNoteSkill, DeleteNoteSkill,
            GetNoteSkill, SearchNotesSkill,
            FindLinkedSkill, GraphNeighborsSkill,
            AppendJournalSkill, ListJournalSkill,
            GetJournalSkill, DeleteJournalSkill,
            DeleteJournalLineSkill, RewriteJournalSkill,
            ListTagsSkill, ListTitlesSkill,
            RenamePageSkill, MergeNotesSkill,
            PinNoteSkill, UnpinNoteSkill, ListPinnedSkill,
            EnableWeeklyRollupSkill,
        )

        self.register(SaveNoteSkill(config=config))
        self.register(UpdateNoteSkill(config=config))
        self.register(DeleteNoteSkill(config=config))
        self.register(GetNoteSkill(config=config))
        self.register(SearchNotesSkill(config=config))
        self.register(FindLinkedSkill(config=config))
        self.register(GraphNeighborsSkill(config=config))
        self.register(AppendJournalSkill(config=config))
        self.register(ListJournalSkill(config=config))
        self.register(GetJournalSkill(config=config))
        self.register(DeleteJournalSkill(config=config))
        self.register(DeleteJournalLineSkill(config=config))
        self.register(RewriteJournalSkill(config=config))
        self.register(ListTagsSkill(config=config))
        self.register(ListTitlesSkill(config=config))
        self.register(RenamePageSkill(config=config))
        self.register(MergeNotesSkill(config=config))
        self.register(PinNoteSkill(config=config))
        self.register(UnpinNoteSkill(config=config))
        self.register(ListPinnedSkill(config=config))
        self.register(EnableWeeklyRollupSkill(config=config))

        # LazyBrain AI-native skills (Phase 2)
        self.register(SuggestLinksSkill(config=config))
        self.register(SuggestMetadataSkill(config=config))
        self.register(SemanticSearchSkill(config=config))
        self.register(AskNotesSkill(config=config))
        self.register(TopicRollupSkill(config=config))
        self.register(MorningBriefingSkill(config=config))
        self.register(ReindexEmbeddingsSkill(config=config))

        # Note: real_browser.py skills removed — merged into BrowserSkill above

        # Browser management skills
        from lazyclaw.skills.builtin.browser_management import (
            BrowserSetPersistentSkill, BrowserApproveConnectSkill,
        )

        self.register(BrowserSetPersistentSkill(config=config))
        self.register(BrowserApproveConnectSkill(config=config))

        # Remote browser takeover (noVNC link, works in Telegram + web)
        from lazyclaw.skills.builtin.browser_share import ShareBrowserControlSkill
        self.register(ShareBrowserControlSkill(config=config))

        # Checkpoint approval — agent pauses for user OK before risky actions
        from lazyclaw.skills.builtin.checkpoint_skill import RequestUserApprovalSkill
        self.register(RequestUserApprovalSkill(config=config))

        # Browser templates (saved-agent recipes for govt appointments etc.)
        from lazyclaw.skills.builtin.browser_templates_skill import (
            DeleteBrowserTemplateSkill, ListBrowserTemplatesSkill,
            RunBrowserTemplateSkill, SaveBrowserTemplateSkill,
            WatchAppointmentSlotsSkill,
        )
        self.register(SaveBrowserTemplateSkill(config=config))
        self.register(ListBrowserTemplatesSkill(config=config))
        self.register(DeleteBrowserTemplateSkill(config=config))
        self.register(RunBrowserTemplateSkill(config=config))
        self.register(WatchAppointmentSlotsSkill(config=config))

        # Watcher skills (zero-token site monitoring)
        from lazyclaw.skills.builtin.watcher_skills import (
            WatchSiteSkill, StopWatcherSkill, ListWatchersSkill,
            PauseWatcherSkill, ResumeWatcherSkill,
            EditWatcherSkill, TestWatcherSkill,
        )

        self.register(WatchSiteSkill(config=config))
        self.register(StopWatcherSkill(config=config))
        self.register(ListWatchersSkill(config=config))
        self.register(PauseWatcherSkill(config=config))
        self.register(ResumeWatcherSkill(config=config))
        self.register(EditWatcherSkill(config=config))
        self.register(TestWatcherSkill(config=config))

        # MCP watcher skill (WhatsApp, Email monitoring via MCP)
        from lazyclaw.skills.builtin.watch_mcp import WatchMCPSkill
        self.register(WatchMCPSkill(config=config))

        # Pipeline / CRM skills (generic contacts + deals)
        from lazyclaw.skills.builtin.pipeline import (
            PipelineAddContactSkill, PipelineListContactsSkill,
            PipelineUpdateContactSkill, PipelineDeleteContactSkill,
            PipelineAddDealSkill, PipelineListDealsSkill,
            PipelineUpdateDealSkill, PipelineDeleteDealSkill,
        )

        self.register(PipelineAddContactSkill(config=config))
        self.register(PipelineListContactsSkill(config=config))
        self.register(PipelineUpdateContactSkill(config=config))
        self.register(PipelineDeleteContactSkill(config=config))
        self.register(PipelineAddDealSkill(config=config))
        self.register(PipelineListDealsSkill(config=config))
        self.register(PipelineUpdateDealSkill(config=config))
        self.register(PipelineDeleteDealSkill(config=config))

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

        # n8n workflow automation skills
        from lazyclaw.skills.builtin.n8n_management import (
            N8nStatusSkill, N8nListWorkflowsSkill, N8nCreateWorkflowSkill,
            N8nManageWorkflowSkill, N8nRunWorkflowSkill, N8nListExecutionsSkill,
            N8nGetWorkflowSkill, N8nUpdateWorkflowSkill,
            N8nListCredentialsSkill, N8nGetExecutionSkill,
            N8nCreateCredentialSkill, N8nDeleteCredentialSkill,
            N8nGoogleSheetsSetupSkill,
            N8nTestWorkflowSkill, N8nSearchTemplatesSkill,
            N8nInstallTemplateSkill, N8nListWebhooksSkill,
            N8nGoogleOAuthSetupSkill, N8nGoogleServicesSetupSkill,
            N8nListTemplatesSkill,
        )

        self.register(N8nStatusSkill(config=config))
        self.register(N8nListWorkflowsSkill(config=config))
        self.register(N8nListTemplatesSkill(config=config))
        self.register(N8nCreateWorkflowSkill(config=config))
        self.register(N8nManageWorkflowSkill(config=config))
        self.register(N8nRunWorkflowSkill(config=config))
        self.register(N8nListExecutionsSkill(config=config))
        self.register(N8nGetWorkflowSkill(config=config))
        self.register(N8nUpdateWorkflowSkill(config=config))
        self.register(N8nListCredentialsSkill(config=config))
        self.register(N8nGetExecutionSkill(config=config))
        self.register(N8nCreateCredentialSkill(config=config))
        self.register(N8nDeleteCredentialSkill(config=config))
        self.register(N8nGoogleSheetsSetupSkill(config=config))
        self.register(N8nTestWorkflowSkill(config=config))
        self.register(N8nSearchTemplatesSkill(config=config))
        self.register(N8nInstallTemplateSkill(config=config))
        self.register(N8nListWebhooksSkill(config=config))
        self.register(N8nGoogleOAuthSetupSkill(config=config))
        self.register(N8nGoogleServicesSetupSkill(config=config))

        # n8n on-demand one-shot tasks (ephemeral create-run-delete).
        from lazyclaw.skills.builtin.n8n_oneshot import (
            N8nRunTaskSkill, ProjectPlanningKickoffSkill,
        )
        self.register(N8nRunTaskSkill(config=config))
        self.register(ProjectPlanningKickoffSkill(config=config))

        # Project asset registry (backed by LazyBrain project notes).
        from lazyclaw.skills.builtin.project_assets import (
            RegisterProjectAssetSkill, LookupProjectAssetSkill,
            ListProjectAssetsSkill,
        )
        self.register(RegisterProjectAssetSkill(config=config))
        self.register(LookupProjectAssetSkill(config=config))
        self.register(ListProjectAssetsSkill(config=config))

        # Cross-topic skill-outcome lessons — agent-visible recall path
        # for the learning loop (writes happen automatically; this is
        # the explicit "what worked before?" handle for small models).
        from lazyclaw.skills.builtin.topic_lessons import RecallTopicLessonsSkill
        self.register(RecallTopicLessonsSkill(config=config))

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
            InstallMCPServerSkill,
        )

        self.register(ListMCPServersSkill(config=config))
        self.register(AddMCPServerSkill(config=config))
        self.register(RemoveMCPServerSkill(config=config))
        self.register(ConnectMCPServerSkill(config=config))
        self.register(DisconnectMCPServerSkill(config=config))
        self.register(ConnectRemoteMCPSkill(config=config, registry=self))
        self.register(FavoriteMCPServerSkill(config=config))
        self.register(UnfavoriteMCPServerSkill(config=config))
        self.register(InstallMCPServerSkill(config=config))

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
            ListMemoriesSkill, DeleteMemorySkill, DeleteMemoriesByQuerySkill,
            ListDailyLogsSkill, ViewDailyLogSkill, DeleteDailyLogSkill,
        )

        self.register(ListMemoriesSkill(config=config))
        self.register(DeleteMemorySkill(config=config))
        self.register(DeleteMemoriesByQuerySkill(config=config))
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
            DraftFreelanceProposalSkill,
            InvoiceClientSkill,
            ReviewDeliverableSkill,
            SearchJobsSkill,
            SetSkillsProfileSkill,
            StartGigSkill,
            SubmitDeliverableSkill,
            SurvivalModeSkill,
            SurvivalStatusSkill,
            WatchRedditForHireSkill,
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
        self.register(DraftFreelanceProposalSkill(config=config, registry=self))
        self.register(WatchRedditForHireSkill(config=config))

        # TodoWrite — real-time task plan tracking (mandatory for 3+ step tasks)
        from lazyclaw.skills.builtin.todo_write import TodoWriteSkill
        self.register(TodoWriteSkill(config=config))

    def get_skill(self, name: str) -> "BaseSkill | None":
        """Get a registered skill instance by name."""
        return self._skills.get(name)
