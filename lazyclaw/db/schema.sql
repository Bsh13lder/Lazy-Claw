CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    encryption_salt TEXT NOT NULL,
    encrypted_dek TEXT,
    display_name TEXT,
    personality_file TEXT DEFAULT 'personality/SOUL.md',
    settings TEXT DEFAULT '{}',
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT DEFAULT (datetime('now')),
    password_encrypted_dek TEXT,
    recovery_encrypted_dek TEXT,
    -- Plan Mode: 1 = show the agent's plan and wait for approval before
    -- executing (Claude-Code-style). 0 = auto-execute (trusted / YOLO).
    auto_plan INTEGER NOT NULL DEFAULT 1
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
    -- Primary session flag. Telegram / CLI / TUI / REPL all write into the
    -- user's primary session so history is shared across channels. Exactly
    -- one row per user may be primary (enforced by the partial unique index
    -- below). Web UI "New Chat" creates non-primary branches.
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- NOTE: the partial unique index for `is_primary = 1` is created in
-- `connection.init_db()` AFTER the column migration runs. Putting it
-- here as well crashes `executescript` on legacy DBs where the column
-- doesn't exist yet (because CREATE TABLE IF NOT EXISTS preserves the
-- old shape). Don't move it back without first dropping the migration.

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
    created_at TEXT DEFAULT (datetime('now')),
    last_status TEXT,
    last_error TEXT
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
    completed_at TEXT,
    -- Execution tracking (phase A of self-review gap fix):
    last_error TEXT,                              -- error message from last failed attempt
    attempt_count INTEGER NOT NULL DEFAULT 0,     -- bumps on every fail_task call
    last_attempted_at TEXT,                       -- timestamp of last execution attempt
    trace_session_id TEXT,                        -- conversation trace that created this task
    lazybrain_note_id TEXT,                       -- mirror note id for status sync
    pre_reminders TEXT,                           -- JSON array of pending advance-reminder ISO timestamps
    allocated_budget REAL                         -- per-task slice of the parent project's budget (nullable; plaintext)
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
    cost_usd REAL DEFAULT 0.0,
    tokens_used INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    -- Code Specialist visibility (added 2026-05-16). Populated when
    -- the task ran via claude-code MCP; NULL otherwise. See
    -- lazyclaw/db/connection.py migrations for rationale.
    mcp_prompt TEXT,
    mcp_transcript TEXT,
    workspace_dir TEXT,
    files_touched TEXT,
    short_description TEXT,
    goal_id TEXT
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

-- Pipeline (generic CRM/deals)

CREATE TABLE IF NOT EXISTS pipeline_contacts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    notes TEXT,
    stage TEXT NOT NULL DEFAULT 'new',
    tags TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pipeline_contacts_user
ON pipeline_contacts(user_id, stage);

CREATE TABLE IF NOT EXISTS pipeline_deals (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    contact_id TEXT NOT NULL REFERENCES pipeline_contacts(id),
    title TEXT NOT NULL,
    description TEXT,
    amount REAL DEFAULT 0,
    currency TEXT DEFAULT 'EUR',
    stage TEXT NOT NULL DEFAULT 'inquiry',
    data TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pipeline_deals_user
ON pipeline_deals(user_id, stage);

CREATE INDEX IF NOT EXISTS idx_pipeline_deals_contact
ON pipeline_deals(user_id, contact_id);

-- Project budgets + expenses (Tasks-page money manager). A "project" mirrors
-- the free-text tasks.category: projects.name_key == casefold(category), so a
-- project can pre-exist a task and tasks auto-associate by category match. No
-- task migration. amount is PLAINTEXT (like pipeline_deals.amount) so totals
-- SUM in SQL; name/description/vendor/notes are encrypted per-user DEK.
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,                -- encrypted display name
    name_key TEXT NOT NULL,            -- plaintext casefold name; join key to tasks.category
    budget REAL DEFAULT 0,             -- plaintext base-currency budget
    currency TEXT NOT NULL DEFAULT 'EUR',
    status TEXT NOT NULL DEFAULT 'active',   -- active | archived
    description TEXT,                  -- encrypted
    lazybrain_note_id TEXT,            -- the "<Name> Project" page note
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_projects_user
ON projects(user_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_user_namekey
ON projects(user_id, name_key);

CREATE TABLE IF NOT EXISTS project_expenses (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT,                      -- optional FK to tasks(id), nullable
    amount REAL NOT NULL DEFAULT 0,    -- plaintext, project base currency unless currency overrides
    currency TEXT NOT NULL DEFAULT 'EUR',
    description TEXT,                  -- encrypted
    vendor TEXT,                       -- encrypted
    notes TEXT,                        -- encrypted
    spent_at TEXT,                     -- plaintext ISO date of the spend
    status TEXT NOT NULL DEFAULT 'posted',   -- posted | void
    recurring_expense_id TEXT,         -- NULL for one-off, else originating rule id
    lazybrain_note_id TEXT,            -- per-expense note that wikilinks the project
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_project_expenses_project
ON project_expenses(user_id, project_id, status);

CREATE INDEX IF NOT EXISTS idx_project_expenses_task
ON project_expenses(user_id, task_id);

-- Recurring expense rules. The agent_jobs cron engine materializes a
-- project_expenses row on each due tick (mirror of recurring tasks). The
-- agent_jobs row is the scheduler; this table is the template + dedup anchor.
CREATE TABLE IF NOT EXISTS recurring_expenses (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT,                      -- optional task to attach generated rows to
    amount REAL NOT NULL DEFAULT 0,    -- plaintext
    currency TEXT NOT NULL DEFAULT 'EUR',
    description TEXT,                  -- encrypted template description (e.g. "Hosting")
    vendor TEXT,                       -- encrypted
    cron_expression TEXT NOT NULL,     -- plaintext cron (e.g. '0 0 1 * *' monthly)
    job_id TEXT,                       -- agent_jobs(id) running the schedule
    next_run TEXT,                     -- mirror of agent_jobs.next_run for inspection
    last_charged_at TEXT,              -- dedup guard: ISO of last materialized row
    status TEXT NOT NULL DEFAULT 'active',   -- active | paused
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_recurring_expenses_project
ON recurring_expenses(user_id, project_id, status);

-- Budget top-ups (credit side of the ledger). Each row adds money to a
-- project's budget and records WHERE it came from (`source`). Shown in the
-- project Log alongside expenses. amount plaintext; source encrypted.
CREATE TABLE IF NOT EXISTS budget_entries (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    amount REAL NOT NULL DEFAULT 0,    -- plaintext credit/top-up (signed: negative on edit-down)
    currency TEXT NOT NULL DEFAULT 'EUR',
    source TEXT,                       -- encrypted "where the money came from" / audit reason
    kind TEXT NOT NULL DEFAULT 'credit',  -- credit | edit  (audit type)
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_budget_entries_project
ON budget_entries(user_id, project_id);

-- Performance indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_job_queue_user_status
ON job_queue(user_id, status);

CREATE INDEX IF NOT EXISTS idx_site_memory_user_domain
ON site_memory(user_id, domain);

CREATE INDEX IF NOT EXISTS idx_channel_bindings_user
ON channel_bindings(user_id, channel);

CREATE INDEX IF NOT EXISTS idx_daily_logs_user_date
ON daily_logs(user_id, date DESC);

-- Browser templates (saved-agent recipes for repeatable flows like govt
-- appointments). system_prompt + playbook are encrypted; setup_urls /
-- checkpoints / watch_extractor stay plaintext for query and inspection.
CREATE TABLE IF NOT EXISTS browser_templates (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    icon TEXT,
    system_prompt TEXT,            -- encrypted
    setup_urls TEXT,               -- JSON array of URLs to open before run
    checkpoints TEXT,              -- JSON array of checkpoint names
    playbook TEXT,                 -- encrypted free-form instructions
    page_reader_mode TEXT NOT NULL DEFAULT 'auto',  -- on | off | auto
    watch_url TEXT,                -- URL the slot-poller hits
    watch_extractor TEXT,          -- JS string for cheap polling
    watch_condition TEXT,          -- "TRIGGERED if slots > 0" rule
    watch_job_id TEXT,             -- references agent_jobs(id) when active
    run_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    last_run_at TEXT,
    auto_saved INTEGER NOT NULL DEFAULT 0,
    corrections_pending INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_browser_templates_user
ON browser_templates(user_id, name);

-- LazyBrain — Python-native second brain (Logseq-style PKM shared between
-- user and agent). `title` + `content` encrypted per-user DEK; `tags`,
-- `to_page_name`, timestamps and `trace_session_id` are plaintext because
-- queries need them.
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT,                        -- encrypted (or NULL)
    content TEXT NOT NULL,             -- encrypted markdown body
    tags TEXT,                         -- JSON array, plaintext
    importance INTEGER DEFAULT 5,
    pinned INTEGER DEFAULT 0,
    trace_session_id TEXT,             -- plaintext pointer to replay
    title_key TEXT,                    -- lowercased plaintext title for wikilink resolve
    memory_type TEXT,                  -- plaintext: user|feedback|project|reference|fact|session-log|other (see lazyclaw/lazybrain/memory_types.py)
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- NOTE: idx_notes_user_memory_type is created inside the migrations
-- pass in connection.py AFTER the ALTER TABLE that adds memory_type on
-- pre-2026-05-20 databases. Putting the CREATE INDEX here would crash
-- init_db on existing installs because executescript(schema_sql) runs
-- BEFORE the ALTER TABLE — the table exists but the column doesn't yet,
-- so CREATE INDEX ON notes(user_id, memory_type) fails with
-- ``no such column: memory_type``. Observed 2026-05-20 in Docker
-- after rebuild: container restart-looped until the index was moved.

CREATE INDEX IF NOT EXISTS idx_notes_user_created
ON notes(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notes_user_pinned
ON notes(user_id, pinned, importance DESC);

CREATE INDEX IF NOT EXISTS idx_notes_title_key
ON notes(user_id, title_key);

CREATE TABLE IF NOT EXISTS note_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    from_note_id TEXT NOT NULL,
    to_page_name TEXT NOT NULL,        -- plaintext (lowercased)
    to_note_id TEXT,                   -- nullable — resolved when target exists
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (from_note_id) REFERENCES notes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_note_links_from
ON note_links(from_note_id);

CREATE INDEX IF NOT EXISTS idx_note_links_to
ON note_links(user_id, to_page_name);

-- ────────────────────────────────────────────────────────────────────────
-- LazyBrain: encrypted embeddings (Phase 2.3)
-- One row per note. `vector` is AES-GCM ciphertext over a float32-packed
-- blob (hex-encoded so it fits sqlite's text-style encrypted envelope).
-- `model` + `dim` are plaintext so we can filter incompatible rows without
-- touching the DEK.
CREATE TABLE IF NOT EXISTS note_embeddings (
    note_id     TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      TEXT NOT NULL,  -- encrypted blob (envelope format)
    updated_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_note_embeddings_user
ON note_embeddings(user_id, model, dim);

-- ────────────────────────────────────────────────────────────────────────
-- LazyBrain: canvas boards (Phase 3.1)
-- Free-form spatial workspace. `payload` is AES-GCM ciphertext over a
-- React-Flow JSON dict ({nodes, edges, viewport}). Plaintext name is fine
-- because it's user-facing and used to list boards in the sidebar.
CREATE TABLE IF NOT EXISTS canvas_boards (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_canvas_boards_user
ON canvas_boards(user_id, updated_at);

-- ────────────────────────────────────────────────────────────────────────
-- LazyBrain: graph node positions (Phase 19.1)
-- Remembers where each note sat in the graph view so the layout survives
-- reloads and follows the user across devices. Positions are plaintext
-- — just coordinates in the canvas coord space, no private content.
-- Keyed by (user_id, mode, note_id) because Categories and Neural-link
-- modes are independent layouts.
CREATE TABLE IF NOT EXISTS note_layout_positions (
    user_id    TEXT NOT NULL,
    mode       TEXT NOT NULL,
    note_id    TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    x          REAL NOT NULL,
    y          REAL NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, mode, note_id)
);
CREATE INDEX IF NOT EXISTS idx_note_layout_positions_user_mode
ON note_layout_positions(user_id, mode);

-- ────────────────────────────────────────────────────────────────────────
-- Bounty hunter (claude-bug-bounty fork integration)
--
-- Tracks user-registered bug-bounty programs, the findings the agent
-- prepares for them, and a per-request audit trail. Sensitive fields
-- (scope_assets, finding bodies, target URLs) are encrypted via the
-- existing DEK pattern (`enc:v1:<nonce>:<ct>`). Status / severity /
-- platform stay plaintext because they're queried.
CREATE TABLE IF NOT EXISTS bounty_programs (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    platform         TEXT NOT NULL,        -- intigriti | yeswehack | hackerone | bugcrowd
    scope_assets     TEXT NOT NULL,        -- encrypted JSON: ["*.x.com","api.x.com"]
    excluded_assets  TEXT,                 -- encrypted JSON
    excluded_classes TEXT,                 -- encrypted JSON: ["dos","social_engineering"]
    rate_limit_rps   INTEGER DEFAULT 5,
    enabled          INTEGER DEFAULT 1,
    cookies_jar      TEXT,                 -- encrypted JSON: list of cookie dicts
    cookies_saved_at TEXT,                 -- when cookies were captured
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_bounty_programs_user
ON bounty_programs(user_id, enabled);

CREATE TABLE IF NOT EXISTS bounty_findings (
    id            TEXT PRIMARY KEY,
    program_id    TEXT NOT NULL REFERENCES bounty_programs(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,           -- encrypted
    vuln_class    TEXT,                    -- subdomain_takeover | idor | xss | ...
    severity      TEXT,                    -- info | low | medium | high | critical
    cvss_vector   TEXT,
    cvss_score    REAL,
    poc           TEXT,                    -- encrypted (full reproduction steps)
    target_url    TEXT,                    -- encrypted
    status        TEXT NOT NULL DEFAULT 'proposed',
                  -- proposed | validated | rejected | submitted | paid
    payout_amount REAL,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bounty_findings_program_status
ON bounty_findings(program_id, status, updated_at);

CREATE TABLE IF NOT EXISTS bounty_audit (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id     TEXT NOT NULL REFERENCES bounty_programs(id) ON DELETE CASCADE,
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target         TEXT NOT NULL,          -- encrypted
    tool           TEXT NOT NULL,          -- subfinder | httpx | nuclei | brain | ...
    method         TEXT,                   -- GET | HEAD | OPTIONS | POST | scope_check
    decision       TEXT,                   -- allow | refuse | require_approval
    response_code  INTEGER,
    ts             TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bounty_audit_program_ts
ON bounty_audit(program_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_bounty_audit_user_ts
ON bounty_audit(user_id, ts DESC);

-- Unified person store: one row per real person, multiple handles per row.
-- All PII columns AES-256-GCM. source tells us where the record came from
-- so we can re-sync from macOS Contacts without losing manual edits
-- (manual_overrides keys always win at merge time).
CREATE TABLE IF NOT EXISTS contacts (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(id),
    name_canonical    TEXT NOT NULL,           -- encrypted display name
    aliases           TEXT,                    -- encrypted JSON array of nicknames / transliterations
    handles           TEXT,                    -- encrypted JSON: {phone:[E.164], email:[], instagram, telegram, whatsapp_jid}
    notes             TEXT,                    -- encrypted free-form notes
    manual_overrides  TEXT,                    -- encrypted JSON: keys here win over re-sync
    source            TEXT NOT NULL DEFAULT 'manual',  -- macos_contacts | manual | imported
    external_id       TEXT,                    -- macOS Contacts identifier when source=macos_contacts
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_contacts_user
ON contacts(user_id, source);
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_external
ON contacts(user_id, source, external_id) WHERE external_id IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────
--  Goal Executor (Phase B)
-- ─────────────────────────────────────────────────────────────────────
-- High-level objectives the agent runs autonomously. Plan is drafted
-- via fix_plan.build_fix_plan() then dispatched to the browser
-- specialist; progress lands here so /goal status survives restarts.
CREATE TABLE IF NOT EXISTS goals (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(id),
    title             TEXT NOT NULL,            -- encrypted
    status            TEXT NOT NULL DEFAULT 'drafting',
    plan_json         TEXT,                     -- encrypted JSON {steps, questions, answers, risks, confidence, summary}
    steps_total       INTEGER NOT NULL DEFAULT 0,
    steps_done        INTEGER NOT NULL DEFAULT 0,
    blocked_on        TEXT,
    last_action       TEXT,
    last_progress_at  TEXT,
    account_slug      TEXT,                     -- which browser identity (multi-account)
    task_id           TEXT,                     -- live tracked-task FK (in-mem only)
    code_session_id   TEXT,                     -- persistent worker session for code goals (--resume across turns)
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_goals_user_status ON goals(user_id, status);
CREATE INDEX IF NOT EXISTS idx_goals_user_progress ON goals(user_id, last_progress_at DESC);
