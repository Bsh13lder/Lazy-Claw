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
}

export interface EcoUsage {
  free_count: number;
  paid_count: number;
  total: number;
  free_percentage: number;
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
}

export interface Specialist {
  name: string;
  description: string;
  system_prompt?: string;
  builtin: boolean;
}

export interface PermissionSettings {
  category_defaults: Record<string, string>;
  skill_overrides: Record<string, string>;
  auto_approve_timeout: number;
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

// ── Health ─────────────────────────────────────────────────────────────────

export const healthCheck = () =>
  request<{ status: string; version: string }>("/api/health");

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

export const createSpecialist = (body: { name: string; description: string; system_prompt?: string }) =>
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
  lane: string;
  status: string;
  elapsed_s?: number;
  current_step?: string;
  step_count?: number;
  duration_s?: number | null;
  result_preview?: string;
  error?: string | null;
}

export interface AgentStatus {
  active: AgentTask[];
  background: AgentTask[];
  recent: AgentTask[];
}

export const getAgentStatus = () =>
  request<AgentStatus>("/api/agents/status");

// ── Replay ─────────────────────────────────────────────────────────────────

export const listTraces = () =>
  request<{ success: boolean; data: Record<string, unknown>[] }>("/api/replay/traces").then((r) => r.data);
