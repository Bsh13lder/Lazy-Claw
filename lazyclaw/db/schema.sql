CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    encryption_salt TEXT NOT NULL,
    display_name TEXT,
    personality_file TEXT DEFAULT 'personality/SOUL.md',
    settings TEXT DEFAULT '{}',
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT DEFAULT (datetime('now')),
    password_encrypted_dek TEXT,
    recovery_encrypted_dek TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    chat_session_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Performance indexes for hot-path queries
CREATE INDEX IF NOT EXISTS idx_agent_messages_session
ON agent_messages(user_id, chat_session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_messages_user
ON agent_messages(user_id, created_at);

CREATE TABLE IF NOT EXISTS agent_chat_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT,
    message_count INTEGER DEFAULT 0,
    archived_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS credential_vault (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, key)
);

CREATE TABLE IF NOT EXISTS channel_bindings (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    channel TEXT NOT NULL,
    external_id TEXT NOT NULL,
    config TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(channel, external_id)
);

CREATE TABLE IF NOT EXISTS channel_configs (
    channel TEXT PRIMARY KEY,
    config TEXT NOT NULL,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    skill_type TEXT NOT NULL DEFAULT 'instruction',
    category TEXT NOT NULL DEFAULT 'custom',
    name TEXT NOT NULL,
    description TEXT,
    instruction TEXT,
    code TEXT,
    parameters_schema TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skills_user
ON skills(user_id);

CREATE TABLE IF NOT EXISTS personal_memory (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    memory_type TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    importance INTEGER DEFAULT 5,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_personal_memory_user
ON personal_memory(user_id, importance DESC);

CREATE TABLE IF NOT EXISTS daily_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    date TEXT NOT NULL,
    summary TEXT,
    key_events TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, date)
);

CREATE TABLE IF NOT EXISTS site_memory (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    domain TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    title TEXT,
    content TEXT,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    last_used TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS browser_tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    instruction TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    help_question TEXT,
    error TEXT,
    steps_completed INTEGER DEFAULT 0,
    max_steps INTEGER DEFAULT 20,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS browser_task_logs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES browser_tasks(id),
    step_number INTEGER NOT NULL,
    action TEXT,
    thinking TEXT,
    url TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_models (
    model_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    is_default INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_model_assignments (
    user_id TEXT NOT NULL REFERENCES users(id),
    feature TEXT NOT NULL,
    model_id TEXT NOT NULL REFERENCES ai_models(model_id),
    PRIMARY KEY (user_id, feature)
);

CREATE TABLE IF NOT EXISTS connector_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    token TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT (datetime('now')),
    last_used TEXT
);

CREATE TABLE IF NOT EXISTS mcp_connections (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    transport TEXT NOT NULL,
    config TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    favorite INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mcp_tool_cache (
    server_name TEXT NOT NULL,
    tools_json TEXT NOT NULL,
    cached_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (server_name)
);

CREATE TABLE IF NOT EXISTS agent_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    job_type TEXT NOT NULL DEFAULT 'cron',
    instruction TEXT NOT NULL,
    cron_expression TEXT,
    context TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_run TEXT,
    next_run TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_queue (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    source TEXT NOT NULL DEFAULT 'heartbeat',
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now')),
    processed_at TEXT
);

-- Task manager (second brain)

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'todo',
    owner TEXT NOT NULL DEFAULT 'user',
    due_date TEXT,
    reminder_at TEXT,
    reminder_job_id TEXT,
    recurring TEXT,
    tags TEXT,
    nag_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_status
ON tasks(user_id, status, due_date);

CREATE INDEX IF NOT EXISTS idx_tasks_user_reminder
ON tasks(user_id, reminder_at);

-- Permissions system

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    skill_name TEXT NOT NULL,
    arguments TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    chat_session_id TEXT,
    source TEXT NOT NULL DEFAULT 'agent',
    decided_by TEXT,
    decided_at TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_approval_user_status
ON approval_requests(user_id, status);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    skill_name TEXT,
    arguments_hash TEXT,
    result_summary TEXT,
    approval_id TEXT,
    source TEXT NOT NULL DEFAULT 'agent',
    ip_address TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_user_created
ON audit_log(user_id, created_at);

-- Multi-Agent Teams

CREATE TABLE IF NOT EXISTS specialists (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    allowed_skills TEXT NOT NULL,
    preferred_model TEXT,
    is_builtin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_team_messages (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    team_session_id TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    message_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_team_messages_session
ON agent_team_messages(team_session_id);

-- Context Compression

CREATE TABLE IF NOT EXISTS message_summaries (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    chat_session_id TEXT,
    from_message_id TEXT NOT NULL,
    to_message_id TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_summaries_user_session
ON message_summaries(user_id, chat_session_id);

-- Session Replay

CREATE TABLE IF NOT EXISTS agent_traces (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    trace_session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_traces_session
ON agent_traces(trace_session_id, sequence);

CREATE TABLE IF NOT EXISTS trace_shares (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    trace_session_id TEXT NOT NULL,
    share_token TEXT NOT NULL UNIQUE,
    expires_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Background Tasks (parallel execution)

CREATE TABLE IF NOT EXISTS background_tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT,
    instruction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    result TEXT,
    error TEXT,
    timeout INTEGER DEFAULT 300,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_bg_tasks_user_status
ON background_tasks(user_id, status);

CREATE TABLE IF NOT EXISTS survival_gigs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    external_job_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    budget TEXT,
    budget_value REAL DEFAULT 0,
    client_name TEXT,
    url TEXT,
    status TEXT NOT NULL DEFAULT 'found',
    proposal_text TEXT,
    workspace_path TEXT,
    deliverable_summary TEXT,
    invoice_id TEXT,
    amount_earned REAL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gigs_user_status
ON survival_gigs(user_id, status);

-- Performance indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_job_queue_user_status
ON job_queue(user_id, status);

CREATE INDEX IF NOT EXISTS idx_site_memory_user_domain
ON site_memory(user_id, domain);

CREATE INDEX IF NOT EXISTS idx_channel_bindings_user
ON channel_bindings(user_id, channel);

CREATE INDEX IF NOT EXISTS idx_daily_logs_user_date
ON daily_logs(user_id, date DESC);
