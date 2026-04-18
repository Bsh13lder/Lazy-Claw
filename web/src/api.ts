// ── Types ──────────────────────────────────────────────────────────────────

export interface User {
  id: string;
  username: string;
  display_name: string | null;
  role: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  skill_type: string;
  enabled: boolean;
  category?: string;
  instruction?: string;
  code?: string;
}

export interface Job {
  id: string;
  name: string;
  instruction: string;
  cron_expression: string | null;
  status: string;
  last_run: string | null;
  next_run: string | null;
  job_type?: string;
  context?: string;
}

export interface McpServer {
  id: string;
  name: string;
  transport: string;
  command?: string;
  url?: string;
  status: string;
  tool_count: number;
}

export interface Memory {
  id: string;
  key: string;
  value: string;
  created_at: string;
}

export interface DailyLog {
  date: string;
  summary: string;
  created_at?: string;
}

export interface EcoSettings {
  mode: string;
  show_badges: boolean;
  monthly_paid_budget: number;
  locked_provider?: string;
  allowed_providers?: string[];
  // Per-mode model overrides
  hybrid_brain_model?: string | null;
  hybrid_worker_model?: string | null;
  hybrid_fallback_model?: string | null;
  full_brain_model?: string | null;
  full_worker_model?: string | null;
  full_fallback_model?: string | null;
  claude_brain_model?: string | null;
  claude_worker_model?: string | null;
  claude_fallback_model?: string | null;
  free_providers?: string[];
  preferred_free_model?: string | null;
  [key: string]: unknown; // allow dynamic per-mode keys
}

export interface ModelInfo {
  id: string;
  display_name: string;
  provider: string;
  is_local: boolean;
  role: string;
  tool_calling: boolean;
  optimized: boolean;
}

export interface ModelsData {
  models: ModelInfo[];
  mode_defaults: Record<string, Record<string, string>>;
}

export interface EcoUsage {
  free_count: number;
  paid_count: number;
  total: number;
  free_percentage: number;
}

export interface EcoCosts {
  models: Record<string, unknown>;
  total_cost: number;
  total_calls: number;
  local_pct: number;
}

export interface EcoProvider {
  name: string;
  configured: boolean;
  [key: string]: unknown;
}

export interface RateLimits {
  [provider: string]: {
    requests_per_minute: number;
    requests_per_day: number;
    tokens_per_minute: number;
  };
}

export interface TeamSettings {
  mode: string;
  critic_mode: boolean;
  max_parallel: number;
  specialist_timeout: number;
}

export interface Specialist {
  name: string;
  display_name: string;
  system_prompt: string;
  allowed_skills: string[];
  preferred_model: string | null;
  is_builtin: boolean;
}

export interface PermissionSettings {
  category_defaults: Record<string, string>;
  skill_overrides: Record<string, string>;
  auto_approve_timeout: number;
}

export interface ChatSession {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name: string | null;
  tool_calls: { name: string; args: Record<string, unknown>; result?: string }[] | null;
  created_at: string;
}

export interface McpTool {
  name: string;
  description: string;
}

// ── Request helper ─────────────────────────────────────────────────────────

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body.detail || body.message || body.error || message;
    } catch {
      // not JSON
    }
    throw new ApiError(message, res.status);
  }

  return res.json();
}

// ── Auth ───────────────────────────────────────────────────────────────────

interface AuthBody { username: string; password: string }
interface RegisterBody extends AuthBody { invite_token?: string }

export const register = (body: RegisterBody) =>
  request<User>("/api/auth/register", { method: "POST", body: JSON.stringify(body) });

export const login = (body: AuthBody) =>
  request<User>("/api/auth/login", { method: "POST", body: JSON.stringify(body) });

export const logout = () =>
  request<{ status: string }>("/api/auth/logout", { method: "POST" });

export const getMe = () =>
  request<User>("/api/auth/me");

// ── Chat ───────────────────────────────────────────────────────────────────

export const sendMessage = (message: string) =>
  request<{ response: string }>("/api/agent/chat", { method: "POST", body: JSON.stringify({ message }) });

// ── Chat Sessions ─────────────────────────────────────────────────────────

export const listChatSessions = () =>
  request<{ sessions: ChatSession[] }>("/api/chat/sessions").then((r) => r.sessions);

export const createChatSession = (title = "New Chat") =>
  request<{ id: string; title: string }>("/api/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });

export const updateChatSession = (id: string, updates: { title?: string; archived?: boolean }) =>
  request<{ status: string }>(`/api/chat/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });

export const deleteChatSession = (id: string) =>
  request<{ status: string }>(`/api/chat/sessions/${id}`, { method: "DELETE" });

export const getSessionMessages = (sessionId: string, opts?: { limit?: number; before?: string }) => {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.before) params.set("before", opts.before);
  const qs = params.toString();
  return request<{ messages: ChatMessage[] }>(`/api/chat/sessions/${sessionId}/messages${qs ? `?${qs}` : ""}`)
    .then((r) => r.messages);
};

// ── Browser canvas ─────────────────────────────────────────────────────────

export interface BrowserStateInfo {
  state: { url?: string; title?: string; ts?: number } | null;
  events: Array<{
    kind: string;
    ts: number;
    action?: string;
    target?: string;
    url?: string;
    title?: string;
    detail?: string;
    extra?: Record<string, unknown>;
  }>;
  has_thumbnail: boolean;
}

export const getBrowserState = () =>
  request<BrowserStateInfo>("/api/browser/state");

/** Returns latest browser thumbnail blob, or null if none captured yet. */
export async function getBrowserFrame(): Promise<Blob | null> {
  const res = await fetch("/api/browser/frame", { credentials: "include" });
  if (res.status === 204 || !res.ok) return null;
  return res.blob();
}

/** Force the browser backend to capture a fresh thumbnail right now. */
export const refreshBrowserFrame = () =>
  request<{ status: string; url?: string; error?: string }>(
    "/api/browser/frame/refresh",
    { method: "POST" },
  );

export interface BrowserLiveMode {
  active: boolean;
  remaining_seconds: number;
  expires_at?: number;
}

export const getBrowserLiveMode = () =>
  request<BrowserLiveMode>("/api/browser/live-mode");

export const startBrowserLiveMode = (seconds?: number) =>
  request<BrowserLiveMode>("/api/browser/live-mode/start", {
    method: "POST",
    body: JSON.stringify({ seconds }),
  });

export const stopBrowserLiveMode = () =>
  request<{ active: boolean }>("/api/browser/live-mode/stop", { method: "POST" });

export interface BrowserRemoteSession {
  active: boolean;
  url: string | null;
  capable: boolean;
}

export const getBrowserRemoteStatus = () =>
  request<BrowserRemoteSession>("/api/browser/remote-session");

export const startBrowserRemoteSession = () =>
  request<{ url: string }>("/api/browser/remote-session/start", { method: "POST" });

export const stopBrowserRemoteSession = () =>
  request<{ status: string }>("/api/browser/remote-session/stop", { method: "POST" });

export interface PendingCheckpoint {
  name: string;
  detail?: string | null;
  created_at: number;
}

export const getPendingCheckpoint = () =>
  request<{ pending: PendingCheckpoint | null }>("/api/browser/checkpoint");

export const approveCheckpoint = (name: string, reason?: string) =>
  request<{ status: string }>("/api/browser/checkpoint/approve", {
    method: "POST",
    body: JSON.stringify({ name, reason }),
  });

export const rejectCheckpoint = (name: string, reason?: string) =>
  request<{ status: string }>("/api/browser/checkpoint/reject", {
    method: "POST",
    body: JSON.stringify({ name, reason }),
  });

// ── Browser Templates ─────────────────────────────────────────────────────

export interface BrowserTemplateDraft {
  name: string;
  icon: string;
  setup_urls: string[];
  checkpoints: string[];
  playbook: string;
}

export interface BrowserTemplate extends BrowserTemplateDraft {
  id: string;
  system_prompt?: string | null;
  watch_url?: string | null;
  watch_extractor?: string | null;
  watch_condition?: string | null;
  page_reader_mode?: string;
  created_at?: string;
  updated_at?: string;
}

/** Capture the user's in-flight browser flow as a new template. */
export const saveTemplateFromCurrentSession = (name: string) =>
  request<{
    template: BrowserTemplate;
    captured: { event_count: number; url_count: number; checkpoint_count: number };
  }>("/api/browser/templates/from-current-session", {
    method: "POST",
    body: JSON.stringify({ name }),
  });

/** Generate a NON-persisted template draft from a one-line description. */
export const createTemplateFromPrompt = (prompt: string) =>
  request<{ draft: BrowserTemplateDraft }>("/api/browser/templates/from-prompt", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  }).then((r) => r.draft);

// ── Health ─────────────────────────────────────────────────────────────────

export const healthCheck = () =>
  request<{ status: string; version: string; started_at?: number }>("/api/health");

// ── Skills ─────────────────────────────────────────────────────────────────

export const listSkills = () =>
  request<{ skills: Skill[] }>("/api/skills").then((r) => r.skills);

export const createSkill = (body: {
  skill_type: string;
  name: string;
  description: string;
  instruction?: string;
  code?: string;
  parameters_schema?: Record<string, unknown>;
}) =>
  request<{ id: string }>("/api/skills", { method: "POST", body: JSON.stringify(body) });

export const updateSkill = (id: string, body: {
  name?: string;
  description?: string;
  instruction?: string;
  code?: string;
  enabled?: boolean;
}) =>
  request<{ status: string }>(`/api/skills/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const deleteSkill = (id: string) =>
  request<{ status: string }>(`/api/skills/${id}`, { method: "DELETE" });

export const generateSkill = (body: { description: string; name?: string }) =>
  request<Record<string, unknown>>("/api/skills/generate", { method: "POST", body: JSON.stringify(body) });

// ── Jobs ───────────────────────────────────────────────────────────────────

export const listJobs = () =>
  request<{ jobs: Job[] }>("/api/jobs").then((r) => r.jobs);

export const createJob = (body: {
  name: string;
  instruction: string;
  job_type?: string;
  cron_expression?: string;
  context?: string;
}) =>
  request<{ id: string }>("/api/jobs", { method: "POST", body: JSON.stringify(body) });

export const updateJob = (id: string, body: {
  name?: string;
  instruction?: string;
  cron_expression?: string;
  context?: string;
}) =>
  request<{ status: string }>(`/api/jobs/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const pauseJob = (id: string) =>
  request<{ status: string }>(`/api/jobs/${id}/pause`, { method: "POST" });

export const resumeJob = (id: string) =>
  request<{ status: string }>(`/api/jobs/${id}/resume`, { method: "POST" });

export const deleteJob = (id: string) =>
  request<{ status: string }>(`/api/jobs/${id}`, { method: "DELETE" });

export interface JobDraft {
  name: string;
  instruction: string;
  job_type: "cron" | "one_off";
  cron_expression: string | null;
  context: string | null;
}

export const createJobFromPrompt = (prompt: string) =>
  request<{ draft: JobDraft }>("/api/jobs/from-prompt", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  }).then((r) => r.draft);

// ── MCP ────────────────────────────────────────────────────────────────────

export const listMcpServers = () =>
  request<{ servers: McpServer[] }>("/api/mcp/servers").then((r) => r.servers);

export const addMcpServer = (body: {
  name: string;
  transport: "stdio" | "sse" | "streamable_http";
  config: Record<string, unknown>;
}) =>
  request<{ id: string; status: string }>("/api/mcp/servers", { method: "POST", body: JSON.stringify(body) });

export const getMcpServer = (id: string) =>
  request<McpServer>(`/api/mcp/servers/${id}`);

export const removeMcpServer = (id: string) =>
  request<{ status: string }>(`/api/mcp/servers/${id}`, { method: "DELETE" });

export const connectMcp = (id: string) =>
  request<{ status: string }>(`/api/mcp/servers/${id}/connect`, { method: "POST" });

export const reconnectMcp = (id: string) =>
  request<void>(`/api/mcp/servers/${id}/reconnect`, { method: "POST" });

export const disconnectMcp = (id: string) =>
  request<void>(`/api/mcp/servers/${id}/disconnect`, { method: "POST" });

export const getMcpServerTools = (id: string) =>
  request<{ tools: McpTool[] }>(`/api/mcp/servers/${id}/tools`).then((r) => r.tools);

// ── Memory ─────────────────────────────────────────────────────────────────

export const listMemories = () =>
  request<{ memories: Memory[] }>("/api/memory/personal").then((r) => r.memories);

export const deleteMemory = (id: string) =>
  request<{ status: string }>(`/api/memory/personal/${id}`, { method: "DELETE" });

export const listDailyLogs = () =>
  request<{ logs: DailyLog[] }>("/api/memory/daily-logs").then((r) => r.logs);

export const getDailyLog = (date: string) =>
  request<DailyLog>(`/api/memory/daily-logs/${date}`);

export const generateDailyLog = (date: string) =>
  request<{ date: string; summary: string }>(`/api/memory/daily-logs/${date}/generate`, { method: "POST" });

export const deleteDailyLog = (date: string) =>
  request<{ status: string }>(`/api/memory/daily-logs/${date}`, { method: "DELETE" });

// ── Vault ──────────────────────────────────────────────────────────────────

export const listVaultKeys = () =>
  request<{ keys: string[] }>("/api/vault").then((r) => r.keys);

export const setVaultKey = (key: string, value: string) =>
  request<{ status: string }>(`/api/vault/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });

export const deleteVaultKey = (key: string) =>
  request<{ status: string }>(`/api/vault/${encodeURIComponent(key)}`, { method: "DELETE" });

// ── ECO ────────────────────────────────────────────────────────────────────

export const getEcoSettings = () =>
  request<{ success: boolean; data: EcoSettings }>("/api/eco/settings").then((r) => r.data);

export const updateEcoSettings = (updates: Partial<EcoSettings>) =>
  request<{ success: boolean; data: EcoSettings }>("/api/eco/settings", {
    method: "PATCH",
    body: JSON.stringify(updates),
  }).then((r) => r.data);

export const getEcoUsage = () =>
  request<{ success: boolean; data: EcoUsage }>("/api/eco/usage").then((r) => r.data);

export const getEcoProviders = () =>
  request<{ success: boolean; data: { all_providers: EcoProvider[] } }>("/api/eco/providers")
    .then((r) => r.data.all_providers);

export const getEcoRateLimits = () =>
  request<{ success: boolean; data: RateLimits }>("/api/eco/rate-limits").then((r) => r.data);

export const getEcoModels = () =>
  request<{ success: boolean; data: ModelsData }>("/api/eco/models").then((r) => r.data);

export const getEcoCosts = () =>
  request<{ success: boolean; data: EcoCosts }>("/api/eco/costs").then((r) => r.data);

// ── Teams ──────────────────────────────────────────────────────────────────

export const getTeamSettings = () =>
  request<{ success: boolean; data: TeamSettings }>("/api/teams/settings").then((r) => r.data);

export const updateTeamSettings = (updates: Partial<TeamSettings>) =>
  request<{ success: boolean; data: TeamSettings }>("/api/teams/settings", {
    method: "PATCH",
    body: JSON.stringify(updates),
  }).then((r) => r.data);

export const listSpecialists = () =>
  request<{ success: boolean; data: Specialist[] }>("/api/teams/specialists").then((r) => r.data);

export const createSpecialist = (body: {
  name: string;
  display_name: string;
  system_prompt: string;
  allowed_skills: string[];
  preferred_model?: string;
}) =>
  request<{ success: boolean }>("/api/teams/specialists", { method: "POST", body: JSON.stringify(body) });

export const deleteSpecialist = (name: string) =>
  request<{ success: boolean }>(`/api/teams/specialists/${encodeURIComponent(name)}`, { method: "DELETE" });

// ── Permissions ────────────────────────────────────────────────────────────

export const getPermissionSettings = () =>
  request<{ success: boolean; data: PermissionSettings }>("/api/permissions/settings").then((r) => r.data);

export const updatePermissionSettings = (updates: Partial<PermissionSettings>) =>
  request<{ success: boolean; data: PermissionSettings }>("/api/permissions/settings", {
    method: "PATCH",
    body: JSON.stringify(updates),
  }).then((r) => r.data);

export const listSkillPermissions = () =>
  request<{ success: boolean; data: Record<string, unknown>[] }>("/api/permissions/skills").then((r) => r.data);

export const setSkillPermission = (skillName: string, level: string) =>
  request<void>(`/api/permissions/skills/${encodeURIComponent(skillName)}`, {
    method: "PATCH",
    body: JSON.stringify({ level }),
  });

export const removeSkillPermission = (skillName: string) =>
  request<void>(`/api/permissions/skills/${encodeURIComponent(skillName)}`, { method: "DELETE" });

export const listPendingApprovals = () =>
  request<{ success: boolean; data: Record<string, unknown>[] }>("/api/permissions/approvals").then((r) => r.data);

export const approveRequest = (id: string) =>
  request<void>(`/api/permissions/approvals/${id}/approve`, { method: "POST" });

export const denyRequest = (id: string) =>
  request<void>(`/api/permissions/approvals/${id}/deny`, { method: "POST" });

// ── Activity ──────────────────────────────────────────────────────────────

export interface AgentTask {
  task_id: string;
  name: string;
  description: string;
  instruction?: string;    // full untruncated user instruction
  lane: string;
  status: string;
  elapsed_s?: number;
  current_step?: string;
  current_tool?: string;
  step_count?: number;
  phase?: string;          // TAOR phase: think|act|observe|reflect
  recent_tools?: string[];
  duration_s?: number | null;
  result_preview?: string;
  result?: string;         // full untruncated result
  error?: string | null;
}

export interface AgentStatus {
  active: AgentTask[];
  background: AgentTask[];
  recent: AgentTask[];
}

export const getAgentStatus = () =>
  request<AgentStatus>("/api/agents/status");

// ── Activity Feed + Metrics ──────────────────────────────────────────────

export interface ActivityEvent {
  id: string;
  type: "task" | "tool_execution" | "specialist" | "approval" | "error";
  title: string;
  detail: string;
  status: string;
  timestamp: string;
  duration_ms?: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface AgentMetrics {
  avg_duration_s: number;
  success_rate: number;
  total_completed: number;
  total_failed: number;
  tasks_last_hour: number;
  tool_calls_today: number;
}

export const getActivityFeed = (limit = 30) =>
  request<{ success: boolean; data: ActivityEvent[] }>(`/api/agents/activity/feed?limit=${limit}`).then((r) => r.data);

export const getAgentMetrics = () =>
  request<{ success: boolean; data: AgentMetrics }>("/api/agents/metrics").then((r) => r.data);

export const cancelTask = (taskId: string) =>
  request<{ success: boolean }>("/api/agents/cancel", {
    method: "POST",
    body: JSON.stringify({ task_id: taskId }),
  });

export const cancelAllTasks = () =>
  request<{ success: boolean }>("/api/agents/cancel-all", { method: "POST" });

// ── Replay ─────────────────────────────────────────────────────────────────

export interface TraceEntry {
  id: string;
  trace_session_id: string;
  sequence: number;
  entry_type: string;
  content: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface TraceSummary {
  trace_session_id: string;
  entry_count: number;
  created_at: string;
  [key: string]: unknown;
}

export const listTraces = (limit = 20) =>
  request<{ success: boolean; data: TraceSummary[] }>(`/api/replay/traces?limit=${limit}`).then((r) => r.data);

export const getTrace = (traceSessionId: string) =>
  request<{ success: boolean; data: TraceEntry[] }>(`/api/replay/traces/${traceSessionId}`).then((r) => r.data);

export const deleteTrace = (traceSessionId: string) =>
  request<{ success: boolean }>(`/api/replay/traces/${traceSessionId}`, { method: "DELETE" });

export const shareTrace = (traceSessionId: string, expiresHours = 72) =>
  request<{ success: boolean; data: { share_token: string; url: string } }>("/api/replay/share", {
    method: "POST",
    body: JSON.stringify({ trace_session_id: traceSessionId, expires_hours: expiresHours }),
  }).then((r) => r.data);

export interface ShareInfo {
  id: string;
  trace_session_id: string;
  share_token: string;
  expires_at: string;
  created_at: string;
}

export const listShares = (traceSessionId?: string) => {
  const qs = traceSessionId ? `?trace_session_id=${traceSessionId}` : "";
  return request<{ success: boolean; data: ShareInfo[] }>(`/api/replay/shares${qs}`).then((r) => r.data);
};

export const deleteShare = (shareId: string) =>
  request<{ success: boolean }>(`/api/replay/shares/${shareId}`, { method: "DELETE" });

// ── Teams (additional) ────────────────────────────────────────────────────

export const updateSpecialist = (name: string, body: {
  display_name?: string;
  system_prompt?: string;
  allowed_skills?: string[];
  preferred_model?: string;
}) =>
  request<{ success: boolean }>(`/api/teams/specialists/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export interface TeamSession {
  session_id: string;
  specialist: string;
  task: string;
  status: string;
  created_at: string;
}

export const listTeamSessions = () =>
  request<{ success: boolean; data: TeamSession[] }>("/api/teams/sessions").then((r) => r.data);

export const getTeamSession = (sessionId: string) =>
  request<{ success: boolean; data: Record<string, unknown>[] }>(`/api/teams/sessions/${sessionId}`).then((r) => r.data);

// ── Audit Log ─────────────────────────────────────────────────────────────

export interface AuditEntry {
  id: string;
  action: string;
  skill_name: string | null;
  result_summary: string | null;
  source: string;
  created_at: string;
}

export const getAuditLog = (opts?: { action?: string; since?: string; limit?: number }) => {
  const params = new URLSearchParams();
  if (opts?.action) params.set("action", opts.action);
  if (opts?.since) params.set("since", opts.since);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return request<{ success: boolean; data: AuditEntry[]; count: number }>(
    `/api/permissions/audit${qs ? `?${qs}` : ""}`
  ).then((r) => ({ entries: r.data, count: r.count }));
};

// ── Watchers ──────────────────────────────────────────────────────────────

export interface Watcher {
  id: string;
  name: string;
  status: string;
  job_type: string;
  instruction?: string | null;
  url?: string | null;
  page_type?: string | null;
  check_interval?: number | null;
  expires_at?: string | null;
  last_check?: string | null;
  last_value?: string | null;
  custom_js?: string | null;
  what_to_watch?: string | null;
  one_shot?: boolean;
  template_id?: string | null;
  template_name?: string | null;
  template_icon?: string | null;
  template_watch_condition?: string | null;
  created_at?: string;
  last_run?: string | null;
  next_run?: string | null;
  next_check_ts?: number | null;
  check_count: number;
  trigger_count: number;
  error_count: number;
  last_error?: string | null;
  last_trigger_ts?: number | null;
  last_trigger_message?: string | null;
}

export interface WatcherCheck {
  ts: number;
  changed: boolean;
  triggered: boolean;
  value_preview?: string | null;
  error?: string | null;
  notification?: string | null;
}

export interface WatcherSummary {
  total: number;
  active: number;
  paused: number;
  last_trigger_ts?: number | null;
  last_trigger_name?: string | null;
  last_trigger_message?: string | null;
}

export const listWatchers = () =>
  request<{ watchers: Watcher[] }>("/api/watchers").then((r) => r.watchers);

export const getWatcherSummary = () =>
  request<WatcherSummary>("/api/watchers/summary");

export const getWatcher = (id: string) =>
  request<Watcher>(`/api/watchers/${id}`);

export const getWatcherHistory = (id: string) =>
  request<{ watcher_id: string; checks: WatcherCheck[] }>(`/api/watchers/${id}/history`)
    .then((r) => r.checks);

export const pauseWatcher = (id: string) =>
  request<{ status: string }>(`/api/watchers/${id}/pause`, { method: "POST" });

export const resumeWatcher = (id: string) =>
  request<{ status: string }>(`/api/watchers/${id}/resume`, { method: "POST" });

export const updateWatcher = (id: string, body: {
  check_interval?: number;
  custom_js?: string | null;
  what_to_watch?: string | null;
  notify_template?: string | null;
}) =>
  request<Watcher>(`/api/watchers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteWatcher = (id: string) =>
  request<{ status: string }>(`/api/watchers/${id}`, { method: "DELETE" });

export const testWatcher = (id: string) =>
  request<{ url?: string; page_type?: string; extracted_value?: string; timestamp?: string }>(
    `/api/watchers/${id}/test`,
    { method: "POST" },
  );

// ── Browser / Site Memory ─────────────────────────────────────────────────

export interface SiteMemory {
  id: string;
  domain: string;
  memory_type: string;
  title: string | null;
  content: string | null;
  success_count: number;
  fail_count: number;
  last_used: string | null;
  created_at: string;
}

export const listSiteMemories = () =>
  request<{ memories: SiteMemory[] }>("/api/browser/site-memory").then((r) => r.memories);

export const deleteSiteMemory = (id: string) =>
  request<{ status: string }>(`/api/browser/site-memory/${id}`, { method: "DELETE" });

export const clearDomainMemory = (domain: string) =>
  request<{ deleted: number }>(`/api/browser/site-memory/domain/${encodeURIComponent(domain)}`, { method: "DELETE" });

// ── Compression ───────────────────────────────────────────────────────────

export const getCompressionStats = (chatSessionId?: string) => {
  const qs = chatSessionId ? `?chat_session_id=${chatSessionId}` : "";
  return request<{ success: boolean; data: Record<string, unknown> }>(`/api/compression/stats${qs}`).then((r) => r.data);
};

// ── Connector ─────────────────────────────────────────────────────────────

export const getConnectorStatus = () =>
  request<{ connected: boolean; device_info: Record<string, unknown> | null }>("/api/connector/status");

// ── LazyBrain (Python-native PKM) ─────────────────────────────────────────

export interface LazyBrainNote {
  id: string;
  title: string | null;
  content: string;
  tags: string[];
  importance: number;
  pinned: boolean;
  trace_session_id: string | null;
  title_key: string | null;
  created_at: string;
  updated_at: string;
}

export interface LazyBrainTag {
  tag: string;
  count: number;
}

export interface LazyBrainGraphNode {
  id: string;
  label: string;
  pinned: boolean;
  importance: number;
  is_root?: boolean;
}

export interface LazyBrainGraphEdge {
  source: string;
  target: string;
  label: string;
}

export interface LazyBrainGraph {
  nodes: LazyBrainGraphNode[];
  edges: LazyBrainGraphEdge[];
}

export const listLazyBrainNotes = (opts?: {
  tag?: string;
  pinned?: boolean;
  limit?: number;
  offset?: number;
}) => {
  const params = new URLSearchParams();
  if (opts?.tag) params.set("tag", opts.tag);
  if (opts?.pinned) params.set("pinned", "true");
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.offset) params.set("offset", String(opts.offset));
  const qs = params.toString();
  return request<{ notes: LazyBrainNote[] }>(
    `/api/lazybrain/notes${qs ? `?${qs}` : ""}`,
  ).then((r) => r.notes);
};

export const createLazyBrainNote = (body: {
  content: string;
  title?: string;
  tags?: string[];
  importance?: number;
  pinned?: boolean;
}) =>
  request<LazyBrainNote>("/api/lazybrain/notes", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getLazyBrainNote = (id: string) =>
  request<LazyBrainNote>(`/api/lazybrain/notes/${id}`);

export const updateLazyBrainNote = (
  id: string,
  body: Partial<{
    content: string;
    title: string;
    tags: string[];
    importance: number;
    pinned: boolean;
  }>,
) =>
  request<LazyBrainNote>(`/api/lazybrain/notes/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteLazyBrainNote = (id: string) =>
  request<{ status: string; id: string }>(`/api/lazybrain/notes/${id}`, {
    method: "DELETE",
  });

export const getLazyBrainBacklinks = (id: string) =>
  request<{ note_id: string; backlinks: LazyBrainNote[] }>(
    `/api/lazybrain/notes/${id}/backlinks`,
  );

export const searchLazyBrain = (q: string, tag?: string, limit = 20) => {
  const params = new URLSearchParams({ q, limit: String(limit) });
  if (tag) params.set("tag", tag);
  return request<{ query: string; results: LazyBrainNote[] }>(
    `/api/lazybrain/search?${params}`,
  );
};

export const getLazyBrainGraph = (opts?: {
  root_id?: string;
  depth?: number;
  limit?: number;
}) => {
  const params = new URLSearchParams();
  if (opts?.root_id) params.set("root_id", opts.root_id);
  if (opts?.depth) params.set("depth", String(opts.depth));
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return request<LazyBrainGraph>(
    `/api/lazybrain/graph${qs ? `?${qs}` : ""}`,
  );
};

export const getLazyBrainJournal = (isoDate: string) =>
  request<{ date: string; note: LazyBrainNote | null }>(
    `/api/lazybrain/journal/${isoDate}`,
  );

export const appendLazyBrainJournal = (isoDate: string, content: string) =>
  request<LazyBrainNote>(`/api/lazybrain/journal/${isoDate}`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

export const listLazyBrainJournal = (limit = 14) =>
  request<{ notes: LazyBrainNote[] }>(
    `/api/lazybrain/journal?limit=${limit}`,
  ).then((r) => r.notes);

export const listLazyBrainTags = () =>
  request<{ tags: LazyBrainTag[] }>("/api/lazybrain/tags").then((r) => r.tags);
