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

    def list_names_by_prefix(self, prefix: str) -> list[str]:
        """Return skill names starting with `prefix`, sorted."""
        return sorted(n for n in self._skills if n.startswith(prefix))

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
        self.register(WebSearchSkill(registry=self))
        self.register(KeywordResearchSkill())
        self.register(GetTimeSkill())
        self.register(CalculateSkill())
        self.register(MemorySaveSkill(config=config))
        self.register(MemoryRecallSkill(config=config))
        self.register(SendEmailSkill(config=config, registry=self))

        # MiniMax generative media (image / speech) — utility category (ALLOW).
        # Raw HTTP to MiniMax's native generative endpoints; uses the same
        # MINIMAX_API_KEY as the LLM router. No-op until that key is set.
        from lazyclaw.skills.builtin.generate_image import GenerateImageSkill
        from lazyclaw.skills.builtin.text_to_speech import TextToSpeechSkill

        self.register(GenerateImageSkill(config=config))
        self.register(TextToSpeechSkill(config=config))

        from lazyclaw.skills.builtin.vault import (
            VaultSetSkill, VaultGetSkill, VaultListSkill, VaultDeleteSkill,
            SaveSiteLoginSkill,
        )

        self.register(VaultSetSkill(config=config))
        self.register(VaultGetSkill(config=config))
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

        # Unified contact store — name → verified handle (phone / email / Instagram).
        # Brain MUST call find_contact before any channel send when the user
        # names a person. Closes the silent-drop bug (whatsapp + others).
        from lazyclaw.skills.builtin.contacts import (
            FindContactSkill, ListContactsSkill, SaveContactSkill,
            SyncMacOSContactsSkill, UpdateContactSkill,
        )

        self.register(FindContactSkill(config=config))
        self.register(SaveContactSkill(config=config))
        self.register(UpdateContactSkill(config=config))
        self.register(ListContactsSkill(config=config))
        self.register(SyncMacOSContactsSkill(config=config))

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
            ScheduleJobSkill, SetReminderSkill, ListJobsSkill,
            EditJobSkill, ManageJobSkill,
        )

        self.register(ScheduleJobSkill(config=config))
        self.register(SetReminderSkill(config=config))
        self.register(ListJobsSkill(config=config))
        self.register(EditJobSkill(config=config))
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

        # NL reschedule + read-only "what's on this task?" — accepts the
        # phrases the user actually types so the brain doesn't need to
        # round-trip "snooze 2h" → discrete reminder_at field.
        from lazyclaw.skills.builtin.task_reschedule import (
            AskAboutTaskSkill, RescheduleTaskSkill,
        )
        self.register(RescheduleTaskSkill(config=config))
        self.register(AskAboutTaskSkill(config=config))

        # Awake mode — keep the macOS host running lid-closed via caffeinate +
        # pmset. Works through the host awake bridge (root LaunchDaemon). NL-
        # discoverable: "stay awake", "sleep for 2h", "wake me at 7am", etc.
        from lazyclaw.skills.builtin.awake_mode import AwakeModeSkill

        self.register(AwakeModeSkill(config=config))

        # Project budget manager — set budgets, log expenses (mirrored to
        # LazyBrain notes wikilinking the project), recurring auto-charged
        # spend. A "project" is the task category, so these roll up the same
        # categories tasks already use.
        from lazyclaw.skills.builtin.budget_manager import (
            AddExpenseSkill, AddProjectBudgetSkill, AddRecurringExpenseSkill,
            CreateProjectSkill, ExpenseReportSkill, ListExpensesSkill,
            SetDefaultExpenseProjectSkill, SetProjectBudgetSkill,
        )
        self.register(CreateProjectSkill(config=config))
        self.register(SetProjectBudgetSkill(config=config))
        self.register(AddProjectBudgetSkill(config=config))
        self.register(AddExpenseSkill(config=config))
        self.register(ListExpensesSkill(config=config))
        self.register(ExpenseReportSkill(config=config))
        self.register(AddRecurringExpenseSkill(config=config))
        self.register(SetDefaultExpenseProjectSkill(config=config))

        # Sheets — private encrypted spreadsheets (Univer web editor +
        # agent-editable). Same local-encrypted-store profile as budgets/tasks.
        from lazyclaw.skills.builtin.sheets import (
            ConvertSheetLinksSkill, CreateSheetSkill, ListSheetsSkill,
            ReadSheetSkill, RecalcSheetSkill, SendSheetSkill, SetCellsSkill,
            SetFormulaSkill,
        )
        self.register(CreateSheetSkill(config=config))
        self.register(ListSheetsSkill(config=config))
        self.register(ReadSheetSkill(config=config))
        self.register(SetCellsSkill(config=config))
        self.register(SetFormulaSkill(config=config))
        self.register(RecalcSheetSkill(config=config))
        self.register(SendSheetSkill(config=config))
        self.register(ConvertSheetLinksSkill(config=config))

        # Docs — private encrypted word-processor documents (Univer Docs
        # editor + agent edits). Same encrypted-blob profile as sheets.
        from lazyclaw.skills.builtin.docs import (
            AppendToDocSkill, CreateDocSkill, ListDocsSkill, ReadDocSkill,
            SendDocSkill, SetDocContentSkill,
        )
        self.register(CreateDocSkill(config=config))
        self.register(ListDocsSkill(config=config))
        self.register(ReadDocSkill(config=config))
        self.register(AppendToDocSkill(config=config))
        self.register(SetDocContentSkill(config=config))
        self.register(SendDocSkill(config=config))

        # PDF toolkit — fill/sign/merge/split/extract/generate over an
        # encrypted PDF store. Permissive libs only (pypdf/reportlab/
        # pdfplumber/pikepdf), never PyMuPDF/borb (AGPL).
        from lazyclaw.skills.builtin.pdf import (
            AddTextToPdfSkill, FillPdfFormSkill, GeneratePdfSkill,
            ListPdfsSkill, MergePdfsSkill, ReadPdfSkill, SendPdfSkill,
            SplitPdfSkill,
        )
        self.register(ListPdfsSkill(config=config))
        self.register(ReadPdfSkill(config=config))
        self.register(MergePdfsSkill(config=config))
        self.register(SplitPdfSkill(config=config))
        self.register(FillPdfFormSkill(config=config))
        self.register(AddTextToPdfSkill(config=config))
        self.register(GeneratePdfSkill(config=config))
        self.register(SendPdfSkill(config=config))

        # Progress tracking — pulse check-ins via templates (Tier 2 of
        # the progress-tracking system). Templates store
        # questions + buttons + cadence; the heartbeat fires them on
        # schedule via [PULSE:<task_id>:<template_id>] agent_jobs rows.
        from lazyclaw.skills.builtin.progress_templates import (
            ApplyProgressTemplateSkill, ListProgressTemplatesSkill,
            PauseProgressPulseSkill, ResumeProgressPulseSkill,
            SaveProgressTemplateSkill,
        )
        self.register(SaveProgressTemplateSkill(config=config))
        self.register(ListProgressTemplatesSkill(config=config))
        self.register(ApplyProgressTemplateSkill(config=config))
        self.register(PauseProgressPulseSkill(config=config))
        self.register(ResumeProgressPulseSkill(config=config))

        # LazyBrain skills — Python-native Logseq-style PKM shared with the agent
        from lazyclaw.skills.builtin.lazybrain import (
            AskNotesSkill, EmbeddingStatusSkill, MorningBriefingSkill,
            RebuildFtsSkill, ReindexEmbeddingsSkill,
            SemanticSearchSkill, SuggestLinksSkill, SuggestMetadataSkill,
            TopicRollupSkill,
            SaveNoteSkill, UpdateNoteSkill, DeleteNoteSkill,
            GetNoteSkill, SearchNotesSkill, RecallTypedMemorySkill,
            FindLinkedSkill, GraphNeighborsSkill,
            AppendJournalSkill, ListJournalSkill,
            GetJournalSkill, DeleteJournalSkill,
            DeleteJournalLineSkill, RewriteJournalSkill,
            ListTagsSkill, ListTitlesSkill,
            RenamePageSkill, MergeNotesSkill,
            PinNoteSkill, UnpinNoteSkill, ListPinnedSkill,
            EnableWeeklyRollupSkill, EnableMonthlyRollupSkill, MarkRolledUpSkill,
            ListRollupsSkill, MorningReviewSkill,
        )

        self.register(SaveNoteSkill(config=config))
        self.register(UpdateNoteSkill(config=config))
        self.register(DeleteNoteSkill(config=config))
        self.register(GetNoteSkill(config=config))
        self.register(SearchNotesSkill(config=config))
        self.register(RecallTypedMemorySkill(config=config))
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
        self.register(EnableMonthlyRollupSkill(config=config))
        self.register(MarkRolledUpSkill(config=config))
        self.register(ListRollupsSkill(config=config))

        # LazyBrain AI-native skills (Phase 2)
        self.register(SuggestLinksSkill(config=config))
        self.register(SuggestMetadataSkill(config=config))
        self.register(SemanticSearchSkill(config=config))
        self.register(AskNotesSkill(config=config))
        self.register(TopicRollupSkill(config=config))
        self.register(MorningBriefingSkill(config=config))
        self.register(ReindexEmbeddingsSkill(config=config))
        self.register(EmbeddingStatusSkill(config=config))
        self.register(RebuildFtsSkill(config=config))
        self.register(MorningReviewSkill(config=config))

        # Bounty hunter skills — wraps the vendored claude-bug-bounty fork.
        # Always registered; the recon skill itself reports a clear error
        # if `pip install -e claude-bug-bounty/` hasn't been run yet.
        from lazyclaw.skills.builtin.bounty import (
            BountyDisableProgramSkill,
            BountyHuntSkill,
            BountyListFindingsSkill,
            BountyListProgramsSkill,
            BountyLoginSkill,
            BountyProbeSkill,
            BountyReconSkill,
            BountyRegisterProgramSkill,
            BountyValidateFindingSkill,
        )
        self.register(BountyRegisterProgramSkill(config=config))
        self.register(BountyDisableProgramSkill(config=config))
        self.register(BountyListProgramsSkill(config=config))
        self.register(BountyListFindingsSkill(config=config))
        self.register(BountyReconSkill(config=config))
        self.register(BountyValidateFindingSkill(config=config))
        self.register(BountyLoginSkill(config=config))
        self.register(BountyProbeSkill(config=config))
        self.register(BountyHuntSkill(config=config))

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

        # Host-browser CDP bridge — agent drives user's real Brave/Chrome
        from lazyclaw.skills.builtin.host_browser_skill import UseHostBrowserSkill
        self.register(UseHostBrowserSkill(config=config))

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

        from lazyclaw.skills.builtin.search_provider import (
            ClearBraveApiKeySkill,
            SetBraveApiKeySkill,
            SetSearchProviderSkill,
            ShowSearchProviderSkill,
        )

        self.register(SetSearchProviderSkill(config=config))
        self.register(ShowSearchProviderSkill(config=config))
        self.register(SetBraveApiKeySkill(config=config))
        self.register(ClearBraveApiKeySkill(config=config))

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
        self.register(N8nTestWorkflowSkill(config=config))
        self.register(N8nSearchTemplatesSkill(config=config))
        self.register(N8nInstallTemplateSkill(config=config))
        self.register(N8nListWebhooksSkill(config=config))
        # n8n Google OAuth setup skills — DEPRECATED. workspace-mcp now
        # handles Google Sheets/Drive/Gmail/Calendar via its own tools
        # (start_google_auth, list_spreadsheets, …). These n8n-specific
        # OAuth shells caused a loop where the agent spawned credentials
        # instead of actually doing Google work. See ADR-0003.
        # Re-enable here if n8n Google nodes are ever needed again.
        # self.register(N8nGoogleSheetsSetupSkill(config=config))
        # self.register(N8nGoogleOAuthSetupSkill(config=config))
        # self.register(N8nGoogleServicesSetupSkill(config=config))

        # n8n on-demand one-shot tasks — DEPRECATED in favor of the direct
        # Google Workspace API path (see ADR-0003). The two skills below
        # are unregistered, NOT deleted — files remain in n8n_oneshot.py
        # so re-enabling is a one-line change if we ever need to fall
        # back. Use `google_run_task` + `google_project_planning_kickoff`
        # instead. n8n itself stays for multi-step / visual workflows.
        # from lazyclaw.skills.builtin.n8n_oneshot import (
        #     N8nRunTaskSkill, ProjectPlanningKickoffSkill,
        # )
        # self.register(N8nRunTaskSkill(config=config))
        # self.register(ProjectPlanningKickoffSkill(config=config))

        # Direct Google Workspace API path (no n8n). See ADR-0003.
        # Prefer this over n8n_run_task for atomic Google ops, and
        # google_project_planning_kickoff over the legacy n8n composite
        # for "start a project from scratch" flows.
        from lazyclaw.skills.builtin.google_direct import (
            GoogleDirectTaskSkill, GoogleProjectPlanningKickoffSkill,
        )
        self.register(GoogleDirectTaskSkill(config=config))
        self.register(GoogleProjectPlanningKickoffSkill(config=config))

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
            SetCriticModelSkill, ListSpecialistsSkill, ManageSpecialistSkill,
        )

        self.register(ShowTeamSettingsSkill(config=config))
        self.register(SetTeamModeSkill(config=config))
        self.register(SetCriticModeSkill(config=config))
        self.register(SetCriticModelSkill(config=config))
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
            ApplyRedditDmSkill,
            DraftFreelanceProposalSkill,
            InvoiceClientSkill,
            ReviewDeliverableSkill,
            SearchJobsSkill,
            SetFreelancePitchSkill,
            SetSkillsProfileSkill,
            SetUpworkBotBehaviorSkill,
            StartGigSkill,
            SubmitDeliverableSkill,
            SurvivalModeSkill,
            SurvivalStatusSkill,
            SyncUpworkProfileSkill,
            ExecuteContractIntakeSetupSkill,
            NewContractIntakeSkill,
            UpworkContractPollSkill,
            UpworkInboxCheckSkill,
            UpworkLastConversationSkill,
            WatchRedditForHireSkill,
        )

        self.register(SearchJobsSkill(config=config, registry=self))
        self.register(ApplyJobSkill(config=config, registry=self))
        self.register(ApplyRedditDmSkill(config=config, registry=self))
        self.register(SurvivalModeSkill(config=config, registry=self))
        self.register(SyncUpworkProfileSkill(config=config, registry=self))
        self.register(SetSkillsProfileSkill(config=config))
        self.register(SetFreelancePitchSkill(config=config))
        self.register(SetUpworkBotBehaviorSkill(config=config))
        self.register(SurvivalStatusSkill(config=config))
        self.register(ReviewDeliverableSkill(config=config, registry=self))
        self.register(StartGigSkill(config=config, registry=self))
        self.register(SubmitDeliverableSkill(config=config, registry=self))
        self.register(InvoiceClientSkill(config=config, registry=self))
        self.register(DraftFreelanceProposalSkill(config=config, registry=self))
        self.register(UpworkInboxCheckSkill(config=config, registry=self))
        self.register(UpworkContractPollSkill(config=config, registry=self))
        self.register(UpworkLastConversationSkill(config=config, registry=self))
        self.register(NewContractIntakeSkill(config=config, registry=self))
        self.register(ExecuteContractIntakeSetupSkill(config=config, registry=self))

        # Wire the contract-intake auto-setup executor's default
        # dispatch callback to the just-built registry + config. The
        # module registers itself with goal_executor at import time;
        # this call gives it the runtime references it needs to
        # resolve config + skills when fired by GoalExecutor._dispatch.
        try:
            from lazyclaw.runtime import contract_intake_executor
            contract_intake_executor.register_runtime(config, self)
        except Exception as exc:  # pragma: no cover — import-time only
            import logging
            logging.getLogger(__name__).warning(
                "contract_intake_executor.register_runtime failed: %s",
                exc, exc_info=True,
            )
        self.register(WatchRedditForHireSkill(config=config))

        # Generic escalation channel (used by Upwork inbox bot + any
        # other skill that hits an off-scope / sensitive client message
        # and needs the human user to decide). See Telegram /esc command
        # wiring in lazyclaw/channels/telegram_commands.py.
        from lazyclaw.skills.builtin.escalate_to_human import EscalateToHumanSkill
        self.register(EscalateToHumanSkill(config=config))

        # TodoWrite — real-time task plan tracking (mandatory for 3+ step tasks)
        from lazyclaw.skills.builtin.todo_write import TodoWriteSkill
        self.register(TodoWriteSkill(config=config))

        # Background status — brain-facing query for live progress of
        # running background tasks. Read-only state snapshot from TeamLead
        # (no LLM call). The `attach_team_lead()` hook is wired by Agent
        # at construction time (see runtime/agent.py).
        from lazyclaw.skills.builtin.background_status import BackgroundStatusSkill
        self.register(BackgroundStatusSkill(config=config))

        # Lazydoctor — weekly maintenance audit (deps + Phase 2 broken-tool /
        # stale-config). Setup wizard configures cadence; bridge surfaces
        # findings as Telegram approval cards; run-now triggers an
        # on-demand audit. The actual audit + apply lives in the
        # mcp-lazydoctor MCP (separate process).
        from lazyclaw.skills.builtin.lazydoctor_setup import LazydoctorSetupSkill
        from lazyclaw.skills.builtin.lazydoctor_run_now import LazydoctorRunNowSkill
        from lazyclaw.skills.builtin.lazydoctor_audit_bridge import (
            LazydoctorReviewFindingSkill,
            LazydoctorSummarizePendingSkill,
        )
        self.register(LazydoctorSetupSkill(config=config))
        self.register(LazydoctorRunNowSkill(config=config))
        self.register(LazydoctorReviewFindingSkill(config=config))
        self.register(LazydoctorSummarizePendingSkill(config=config))

        # Goal Executor (Phase B/C — autonomous high-level objectives).
        from lazyclaw.skills.builtin.goal import (
            AbortGoalSkill,
            AnswerGoalQuestionsSkill,
            ContinueCodeGoalSkill,
            GoalProgressReportSkill,
            GoalStatusSkill,
            ListGoalsSkill,
            StartGoalSkill,
        )
        self.register(StartGoalSkill(config=config))
        self.register(AnswerGoalQuestionsSkill(config=config))
        self.register(GoalStatusSkill(config=config))
        self.register(ListGoalsSkill(config=config))
        self.register(AbortGoalSkill(config=config))
        self.register(GoalProgressReportSkill(config=config))
        self.register(ContinueCodeGoalSkill(config=config))

        # Multi-account browser identity skills (Phase A — Goal Executor).
        from lazyclaw.skills.builtin.browser_account import (
            AddLiveBrowserHostSkill,
            ListBrowserAccountsSkill,
            ListLiveBrowserHostsSkill,
            RegisterBrowserAccountSkill,
            RemoveLiveBrowserHostSkill,
            SwitchBrowserAccountSkill,
            TuneBrowserCadenceSkill,
        )
        self.register(RegisterBrowserAccountSkill(config=config))
        self.register(ListBrowserAccountsSkill(config=config))
        self.register(SwitchBrowserAccountSkill(config=config))
        self.register(TuneBrowserCadenceSkill(config=config))
        self.register(AddLiveBrowserHostSkill(config=config))
        self.register(RemoveLiveBrowserHostSkill(config=config))
        self.register(ListLiveBrowserHostsSkill(config=config))

    def get_skill(self, name: str) -> "BaseSkill | None":
        """Get a registered skill instance by name."""
        return self._skills.get(name)
