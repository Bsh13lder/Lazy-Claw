CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    encryption_salt TEXT NOT NULL,
    display_name TEXT,
    personality_file TEXT DEFAULT 'personality/SOUL.md',
    settings TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
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

CREATE TABLE IF NOT EXISTS personal_memory (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    memory_type TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    importance INTEGER DEFAULT 5,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    date TEXT NOT NULL,
    summary TEXT,
    key_events TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, date)
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
