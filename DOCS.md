# LazyClaw — Function & Class Reference

Complete inventory of all modules, classes, functions, and their signatures.

---

## Crypto (`lazyclaw/crypto/`)

### `encryption.py` — AES-256-GCM encryption, PBKDF2 key derivation

| Function | Signature | Description |
|----------|-----------|-------------|
| `derive_key` | `(password: str, salt: bytes, iterations: int = 100_000) -> bytes` | Derive AES-256 key from password via PBKDF2 |
| `derive_server_key` | `(server_secret: str, user_id: str) -> bytes` | Derive per-user server-side encryption key |
| `encrypt` | `(plaintext: str, key: bytes) -> str` | Encrypt string → `enc:v1:<nonce>:<ciphertext>` |
| `decrypt` | `(token: str, key: bytes) -> str` | Decrypt `enc:v1:` token back to plaintext |
| `is_encrypted` | `(value: str) -> bool` | Check if value has `enc:v1:` prefix |
| `encrypt_field` | `(value: str \| None, key: bytes) -> str \| None` | Encrypt a nullable DB field |
| `decrypt_field` | `(value: str \| None, key: bytes) -> str \| None` | Decrypt a nullable DB field |

**Constants:** `FIXED_SALT = b"lazyclaw-server-key-v1"`

### `vault.py` — Encrypted credential store

| Function | Signature | Description |
|----------|-----------|-------------|
| `set_credential` | `async (config, user_id, key, value) -> None` | Store encrypted credential |
| `get_credential` | `async (config, user_id, key) -> str \| None` | Retrieve and decrypt credential |
| `delete_credential` | `async (config, user_id, key) -> bool` | Delete credential by key |
| `list_credentials` | `async (config, user_id) -> list[str]` | List all credential keys (not values) |

---

## Database (`lazyclaw/db/`)

### `connection.py` — aiosqlite connection management

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_db_path` | `(config: Config) -> Path` | Resolve SQLite database file path |
| `init_db` | `async (config: Config) -> None` | Initialize DB, run schema.sql |
| `db_session` | `async (config: Config) -> AsyncIterator[Connection]` | Async context manager for DB connection (WAL mode) |

### `schema.sql` — Core schema (7 tables)
Tables: `users`, `sessions`, `agent_messages`, `agent_chat_sessions`, `personal_memory`, `site_memory`, `daily_logs`, `skills`, `browser_tasks`, `browser_task_logs`, `channel_bindings`, `channel_configs`, `mcp_connections`, `credential_vault`, `ai_models`, `user_model_assignments`, `agent_jobs`, `connector_tokens`, `job_queue`

---

## LLM (`lazyclaw/llm/`)

### `providers/base.py` — Base types and abstract provider

| Class | Fields / Methods | Description |
|-------|-----------------|-------------|
| `ToolCall` | `id: str, name: str, arguments: dict` | Dataclass for tool invocations |
| `LLMMessage` | `role: str, content: str, tool_call_id: str, tool_calls: list[ToolCall]` | Unified message format |
| `LLMResponse` | `content: str, model: str, usage: dict, tool_calls: list[ToolCall]` | Unified response format |
| `BaseLLMProvider` | ABC | Abstract base for all LLM providers |

**BaseLLMProvider methods:**
| Method | Signature | Description |
|--------|-----------|-------------|
| `chat` | `async (messages, model, **kwargs) -> LLMResponse` | Send chat completion (abstract) |
| `verify_key` | `async () -> bool` | Validate API key (abstract) |

### `providers/openai_provider.py` — OpenAI integration

| Class | Methods | Description |
|-------|---------|-------------|
| `OpenAIProvider(BaseLLMProvider)` | `__init__(api_key)`, `chat(...)`, `verify_key()`, `_serialize_message(m)` | OpenAI chat completions with tool calling |

### `providers/anthropic_provider.py` — Anthropic integration

| Class | Methods | Description |
|-------|---------|-------------|
| `AnthropicProvider(BaseLLMProvider)` | `__init__(api_key)`, `chat(...)`, `verify_key()`, `_convert_tools(...)`, `_serialize_messages(...)` | Anthropic Messages API with tool_use/tool_result |

### `router.py` — Multi-provider routing

| Class | Methods | Description |
|-------|---------|-------------|
| `LLMRouter` | see below | Routes requests to correct provider by model prefix |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(config: Config)` | Initialize with config |
| `_infer_provider_name` | `(model: str) -> str` | Detect provider from model ID prefix |
| `_get_api_key` | `(provider_name: str) -> str \| None` | Get API key from config |
| `_resolve_api_key` | `async (provider_name, user_id) -> str \| None` | Resolve key: vault first, then config fallback |
| `_create_provider` | `(provider_name, api_key) -> BaseLLMProvider` | Instantiate provider class |
| `chat` | `async (messages, model, user_id, **kwargs) -> LLMResponse` | Route chat to correct provider |
| `verify_provider` | `async (provider, api_key) -> bool` | Verify an API key works |

### `model_manager.py` — Model catalog & per-user assignments

| Function | Signature | Description |
|----------|-----------|-------------|
| `seed_default_models` | `async (config) -> int` | Insert default model catalog into DB |
| `list_models` | `async (config) -> list[dict]` | List all available models |
| `get_user_model` | `async (config, user_id, feature) -> str` | Get user's assigned model for a feature |
| `set_user_model` | `async (config, user_id, feature, model_id) -> None` | Assign model to user for feature |
| `get_user_assignments` | `async (config, user_id) -> dict[str, str]` | Get all user model assignments |

**Constants:** `FEATURE_CHAT`, `FEATURE_BROWSER`, `FEATURE_SKILL_WRITER`, `FEATURE_SUMMARY`, `DEFAULT_MODELS`

---

## Runtime (`lazyclaw/runtime/`)

### `agent.py` — Core agentic loop

| Class | Methods | Description |
|-------|---------|-------------|
| `Agent` | `__init__(config, router, registry)`, `process_message(user_id, message, chat_session_id)` | Multi-turn agent with tool calling (max 10 iterations) |

### `tool_executor.py` — Tool dispatch

| Class | Methods | Description |
|-------|---------|-------------|
| `ToolExecutor` | `__init__(registry)`, `execute(tool_call, user_id) -> str` | Dispatches ToolCall to skill registry |

### `personality.py` — SOUL.md & system prompt

| Function | Signature | Description |
|----------|-----------|-------------|
| `_find_project_root` | `() -> Path` | Locate project root directory |
| `load_personality` | `(personality_path: str \| None) -> str` | Load SOUL.md or fallback personality |
| `build_system_prompt` | `(personality, extra_context) -> str` | Assemble full system prompt |

**Constants:** `_FALLBACK_PERSONALITY`

### `context_builder.py` — Memory injection into system prompt

| Function | Signature | Description |
|----------|-----------|-------------|
| `build_context` | `async (config, user_id) -> str` | Fetch personal memories + daily context for prompt |

---

## Skills (`lazyclaw/skills/`)

### `base.py` — Abstract skill base

| Class | Properties / Methods | Description |
|-------|---------------------|-------------|
| `BaseSkill` | ABC | Base class for all skills |

| Member | Type | Description |
|--------|------|-------------|
| `name` | property (abstract) | Skill identifier |
| `description` | property (abstract) | Human-readable description |
| `category` | property | Category grouping (default: "general") |
| `parameters_schema` | property (abstract) | JSON Schema for parameters |
| `execute` | `async (user_id, params) -> str` | Run the skill (abstract) |
| `to_openai_tool` | `() -> dict` | Convert to OpenAI function-calling format |

### `registry.py` — Unified skill registry

| Class | Methods | Description |
|-------|---------|-------------|
| `SkillRegistry` | see below | Central registry for all skill types |

| Method | Signature | Description |
|--------|-----------|-------------|
| `register` | `(skill: BaseSkill) -> None` | Add skill to registry |
| `get` | `(name: str) -> BaseSkill \| None` | Look up skill by name |
| `list_tools` | `() -> list[dict]` | All skills as OpenAI tool definitions |
| `list_by_category` | `() -> dict[str, list[str]]` | Skills grouped by category |
| `register_defaults` | `(config=None) -> None` | Register all built-in skills |

### `instruction.py` — Natural language template skills

| Class | Description |
|-------|-------------|
| `InstructionSkill(BaseSkill)` | Wraps NL instruction as a callable skill. `__init__(skill_name, skill_description, instruction, params_schema)` |

### `sandbox.py` — Sandboxed Python execution

| Class / Function | Signature | Description |
|-----------------|-----------|-------------|
| `SandboxError` | Exception | Raised on validation/execution failures |
| `CodeSkill(BaseSkill)` | `__init__(skill_name, skill_description, code, params_schema)` | Wraps sandboxed Python as a skill |
| `validate_code` | `(source: str) -> list[str]` | AST-validate code for blocked patterns |
| `execute_sandboxed` | `async (source, user_id, params, call_tool, timeout) -> str` | Execute code in restricted environment |

**Constants:** `BLOCKED_NODE_TYPES`, `BLOCKED_FUNCTION_NAMES`, `BLOCKED_ATTRIBUTE_NAMES`, `SAFE_BUILTINS`

### `manager.py` — CRUD for user-created skills (DB-backed)

| Function | Signature | Description |
|----------|-----------|-------------|
| `create_instruction_skill` | `async (config, user_id, name, description, instruction) -> str` | Create instruction skill, return ID |
| `create_code_skill` | `async (config, user_id, name, description, code, parameters_schema) -> str` | Create code skill, return ID |
| `get_skill_by_id` | `async (config, user_id, skill_id) -> dict \| None` | Fetch skill by ID |
| `update_skill` | `async (config, user_id, skill_id, **fields) -> bool` | Update skill fields |
| `delete_user_skill_by_id` | `async (config, user_id, skill_id) -> bool` | Delete skill by ID |
| `list_user_skills` | `async (config, user_id) -> list[dict]` | List all user skills |
| `delete_user_skill` | `async (config, user_id, skill_name) -> bool` | Delete skill by name |
| `load_user_skills` | `async (config, user_id, registry) -> int` | Load user skills into registry, return count |

### `writer.py` — LLM-powered skill generation

| Function | Signature | Description |
|----------|-----------|-------------|
| `_parse_llm_response` | `(content: str) -> dict` | Parse structured LLM output into skill dict |
| `generate_code_skill` | `async (config, user_id, description, name) -> dict` | Generate code skill from natural language description |

**Constants:** `GENERATION_PROMPT`

### Built-in Skills (`lazyclaw/skills/builtin/`)

| File | Class | Category | Description |
|------|-------|----------|-------------|
| `web_search.py` | `WebSearchSkill` | search | DuckDuckGo web search (no API key) |
| `get_time.py` | `GetTimeSkill` | utility | Timezone-aware current time |
| `calculate.py` | `CalculateSkill` | utility | Safe AST-based math calculator |
| `memory_save.py` | `MemorySaveSkill` | memory | Save personal memory fact |
| `memory_recall.py` | `MemoryRecallSkill` | memory | Search/recall memories |
| `vault.py` | `VaultSetSkill` | credentials | Store credential in vault |
| `vault.py` | `VaultListSkill` | credentials | List vault keys |
| `vault.py` | `VaultDeleteSkill` | credentials | Delete vault credential |
| `skill_crud.py` | `CreateSkillSkill` | skills | Create new instruction skill |
| `skill_crud.py` | `ListSkillsSkill` | skills | List user's custom skills |
| `skill_crud.py` | `DeleteSkillSkill` | skills | Delete a custom skill |

**`calculate.py` helpers:** `_safe_eval(expr)`, `_eval_node(node)`, `_OPERATORS`, `_FUNCTIONS`

---

## Memory (`lazyclaw/memory/`)

### `personal.py` — Encrypted personal facts/preferences

| Function | Signature | Description |
|----------|-----------|-------------|
| `save_memory` | `async (config, user_id, content, memory_type="fact", importance=5) -> str` | Save encrypted memory, return ID |
| `get_memories` | `async (config, user_id, limit=20) -> list[dict]` | Fetch memories ordered by importance |
| `delete_memory` | `async (config, user_id, memory_id) -> bool` | Delete a memory |
| `search_memories` | `async (config, user_id, query, limit=10) -> list[dict]` | Keyword search across memories |

### `daily_log.py` — Encrypted daily summaries

| Function | Signature | Description |
|----------|-----------|-------------|
| `save_daily_log` | `async (config, user_id, date, summary, key_events) -> str` | Save daily log entry |
| `get_daily_log` | `async (config, user_id, date) -> dict \| None` | Get log for specific date |
| `list_daily_logs` | `async (config, user_id, limit=30) -> list[dict]` | List recent daily logs |
| `delete_daily_log` | `async (config, user_id, date) -> bool` | Delete daily log |
| `generate_daily_summary` | `async (config, user_id, date) -> str` | LLM-generate summary from day's messages |

---

## Queue (`lazyclaw/queue/`)

### `lane.py` — Per-user FIFO serial execution

| Class | Description |
|-------|-------------|
| `Job` | Dataclass: `user_id: str`, `message: str`, `result_future: asyncio.Future` |
| `LaneQueue` | Per-user serial message processing |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Initialize queue state |
| `set_handler` | `(handler: Callable[[str, str], Awaitable[str]])` | Set message processing callback |
| `enqueue` | `async (user_id, message) -> str` | Queue message, await result |
| `_get_lane` | `(user_id) -> asyncio.Queue[Job]` | Get/create per-user queue |
| `_process_lane` | `async (user_id)` | Worker loop for a user lane |
| `start` | `async ()` | Start queue system |
| `stop` | `async ()` | Graceful shutdown |

---

## Channels (`lazyclaw/channels/`)

### `base.py` — Channel abstractions

| Class | Fields / Methods | Description |
|-------|-----------------|-------------|
| `InboundMessage` | `channel, external_user_id, text, metadata` | Normalized incoming message |
| `OutboundMessage` | `text, metadata` | Outgoing message |
| `ChannelAdapter` | ABC: `start()`, `stop()`, `send_message(external_user_id, message)` | Abstract channel adapter |

### `telegram.py` — Telegram polling adapter

| Class | Methods | Description |
|-------|---------|-------------|
| `TelegramAdapter(ChannelAdapter)` | see below | Telegram bot via python-telegram-bot v21+ |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(token, agent, config, lane_queue)` | Initialize with bot token and dependencies |
| `start` | `async ()` | Start polling |
| `stop` | `async ()` | Stop polling |
| `_handle_message` | `async (update, context)` | Process incoming Telegram message |
| `_handle_start` | `async (update, context)` | Handle /start command |
| `send_message` | `async (external_user_id, message)` | Send message to Telegram user |
| `verify_token` | `static async (token) -> dict \| None` | Validate bot token |

---

## Gateway (`lazyclaw/gateway/`)

### `auth.py` — Session auth & user management

| Class | Description |
|-------|-------------|
| `User` | Frozen dataclass: `id, username, display_name, encryption_salt` |
| `RegisterRequest` | Pydantic: `username, password, display_name` |
| `LoginRequest` | Pydantic: `username, password` |

| Function | Signature | Description |
|----------|-----------|-------------|
| `hash_password` | `(password) -> str` | bcrypt hash |
| `verify_password` | `(password, stored_hash) -> bool` | bcrypt verify |
| `register_user` | `async (config, username, password, display_name) -> User` | Create user with encryption salt |
| `authenticate_user` | `async (config, username, password) -> User \| None` | Verify credentials |
| `create_session` | `async (config, user_id, expires_hours=720) -> str` | Create session token |
| `get_session_user` | `async (config, session_id) -> User \| None` | Resolve session to user |
| `delete_session` | `async (config, session_id) -> None` | Invalidate session |
| `get_current_user` | `async (request: Request) -> User` | FastAPI dependency for auth |

**Routes:** `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`

### `app.py` — FastAPI application

| Item | Description |
|------|-------------|
| `app` | FastAPI instance with lifespan (init DB, seed models) |
| `ChatRequest` | Pydantic: `message: str` |
| `ChatResponse` | Pydantic: `response: str` |
| `set_lane_queue(queue)` | Set global lane queue reference |
| `GET /health` | Health check endpoint |
| `POST /api/agent/chat` | Send message → AI response (auth required) |

### `routes/memory.py` — Memory & daily log endpoints

| Route | Handler | Description |
|-------|---------|-------------|
| `GET /` | `list_personal_memories` | List user's memories |
| `DELETE /{memory_id}` | `delete_personal_memory` | Delete a memory |
| `GET /daily-logs` | `list_daily_logs_route` | List daily logs |
| `GET /daily-logs/{date}` | `get_daily_log_route` | Get specific daily log |
| `POST /daily-logs/{date}/generate` | `generate_daily_log_route` | Generate daily summary |
| `DELETE /daily-logs/{date}` | `delete_daily_log_route` | Delete daily log |

**Helpers:** `_validate_date(date)`, `_DATE_PATTERN`

### `routes/skills.py` — Skill CRUD endpoints

| Route | Handler | Description |
|-------|---------|-------------|
| `GET /` | `list_skills` | List user's skills |
| `POST /` | `create_skill` | Create instruction or code skill |
| `PATCH /{skill_id}` | `update_skill_route` | Update skill fields |
| `DELETE /{skill_id}` | `delete_skill` | Delete a skill |
| `POST /generate` | `generate_skill` | LLM-generate a code skill |

**Models:** `CreateSkillRequest`, `UpdateSkillRequest`, `GenerateSkillRequest`

### `routes/vault.py` — Credential vault endpoints

| Route | Handler | Description |
|-------|---------|-------------|
| `GET /` | `list_vault_keys` | List credential keys |
| `GET /{key}` | `get_vault_credential` | Get credential value |
| `PUT /{key}` | `set_vault_credential` | Set credential |
| `DELETE /{key}` | `delete_vault_credential` | Delete credential |

**Models:** `VaultSetRequest`

---

## Config & CLI (`lazyclaw/`)

### `config.py` — Environment & configuration

| Item | Signature | Description |
|------|-----------|-------------|
| `Config` | dataclass | `server_secret, database_dir, port, default_model, cors_origin, openai_api_key, anthropic_api_key, telegram_bot_token` |
| `get_project_root` | `() -> Path` | Find project root (where .env lives) |
| `load_config` | `() -> Config` | Load config from environment variables |
| `save_env` | `(key, value) -> None` | Write/update key in .env file |

### `cli.py` — Click CLI commands

| Function | Signature | Description |
|----------|-----------|-------------|
| `verify_provider_async` | `async (provider, key) -> bool` | Verify AI provider API key |
| `verify_telegram_async` | `async (token) -> dict \| None` | Verify Telegram bot token |
| `setup_database` | `async (config) -> None` | Initialize database |
| `run_agent` | `async (config) -> None` | Start gateway + Telegram concurrently |
| `main` | Click group | CLI entry point |
| `setup` | Click command | Interactive setup wizard |
| `start` | Click command | Start the agent |
| `_do_start` | `() -> None` | Sync wrapper for async start |

### `__init__.py`
- `__version__ = "0.1.0"`

### `__main__.py` / `main.py`
- `main()` — Module entry point

---

## Browser (`lazyclaw/browser/`)

### `manager.py` — Persistent browser session management

| Class | Methods | Description |
|-------|---------|-------------|
| `PersistentBrowserManager` | see below | Single user's browser session lifecycle |
| `BrowserSessionPool` | see below | Manages sessions across all users |

**PersistentBrowserManager:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_browser` | `async () -> tuple[Any, bool]` | Get/create browser (returns browser, is_new) |
| `is_alive` | `async () -> bool` | Check if browser responsive |
| `cleanup_locks` | `async () -> None` | Remove stale Chrome lock files |
| `kill_orphaned_processes` | `async () -> None` | Kill orphaned chrome processes |
| `close` | `async () -> None` | Close browser + cleanup |
| `touch` | `() -> None` | Update last activity |
| `is_idle` | `(timeout) -> bool` | Check if idle > timeout |

**BrowserSessionPool:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_session` | `async (user_id) -> PersistentBrowserManager` | Get/create session |
| `close_session` | `async (user_id) -> None` | Close user's session |
| `start` | `async () -> None` | Start cleanup loop |
| `stop` | `async () -> None` | Stop and close all sessions |

### `dom_optimizer.py` — DOM analysis utilities

| Method | Signature | Description |
|--------|-----------|-------------|
| `DOMOptimizer.extract_actionable` | `static async (page) -> list[dict]` | Interactive elements (~90% token reduction) |
| `DOMOptimizer.get_page_summary` | `static async (page) -> dict` | Quick page snapshot |
| `DOMOptimizer.detect_changes` | `static (current, previous) -> list[str]` | State diff detection |

### `page_reader.py` — Lightweight page reading + JS extractors

| Method | Signature | Description |
|--------|-----------|-------------|
| `read_page` | `async (url, user_id, custom_extractor, credentials) -> dict` | Extract via JS |
| `read_and_analyze` | `async (url, question, user_id) -> str` | Read + LLM analysis |
| `get_dom_structure` | `async (url, user_id, credentials) -> str` | Simplified DOM tree |
| `generate_extractor` | `async (url, description, user_id) -> dict \| None` | Auto-gen JS extractor |
| `close` | `async () -> None` | Cleanup browser |

**JS Extractors:** `JS_GENERIC`, `JS_SEARCH`, `JS_ARTICLE`, `JS_EMAIL`, `JS_WHATSAPP`

### `site_memory.py` — Encrypted per-domain knowledge

| Function | Signature | Description |
|----------|-----------|-------------|
| `remember` | `async (config, user_id, url, memory_type, title, content) -> str` | Save encrypted memory (UPSERT) |
| `recall` | `async (config, user_id, url) -> dict[str, list[dict]]` | Get memories for domain |
| `recall_all` | `async (config, user_id) -> list[dict]` | All memories for user |
| `forget` | `async (config, user_id, memory_id) -> bool` | Delete specific memory |
| `forget_domain` | `async (config, user_id, domain) -> int` | Delete all for domain |
| `forget_all` | `async (config, user_id) -> int` | Delete all for user |
| `mark_failed` | `async (config, user_id, url, memory_type, title) -> None` | Track failure |
| `format_memories_for_context` | `(memories) -> str` | Format for agent prompt |

### `agent.py` — Browser agent manager

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_task` | `async (user_id, instruction, max_steps) -> str` | Create task |
| `start_task` | `async (task_id) -> None` | Launch as background task |
| `cancel_task` | `async (task_id, user_id) -> None` | Cancel running task |
| `provide_help` | `async (task_id, user_id, response) -> None` | Respond to help request |
| `inject_instruction` | `async (task_id, user_id, instruction) -> None` | Queue instruction |
| `continue_task` | `async (task_id, user_id, instruction) -> None` | Continue task |
| `request_takeover` | `async (task_id, user_id) -> None` | Take manual control |
| `release_takeover` | `async (task_id, user_id) -> None` | Release control |
| `execute_user_action` | `async (task_id, user_id, action) -> dict` | Execute user action |
| `get_task` | `async (task_id, user_id) -> dict \| None` | Get task (decrypted) |
| `list_tasks` | `async (user_id, limit) -> list[dict]` | List tasks |
| `get_task_logs` | `async (task_id, user_id, after_id) -> list[dict]` | Get logs (decrypted) |
| `get_live_screenshot` | `(task_id) -> bytes \| None` | Latest screenshot |

### Browser Skills (`lazyclaw/skills/builtin/browser.py`)

| Class | Category | Description |
|-------|----------|-------------|
| `BrowseWebSkill` | browser | Start browser agent from chat |
| `ReadPageSkill` | browser | Lightweight page read (no agent) |
| `SaveSiteLoginSkill` | browser | Save website credentials to vault for auto-login |

### Browser Routes (`lazyclaw/gateway/routes/browser.py`)

| Route | Description |
|-------|-------------|
| `POST /api/browser/tasks` | Create & start task |
| `GET /api/browser/tasks` | List tasks |
| `GET /api/browser/tasks/{id}` | Get task |
| `GET /api/browser/tasks/{id}/logs` | Step logs |
| `GET /api/browser/tasks/{id}/live` | Live screenshot |
| `POST /api/browser/tasks/{id}/help` | Provide help |
| `POST /api/browser/tasks/{id}/continue` | Continue task |
| `POST /api/browser/tasks/{id}/cancel` | Cancel task |
| `POST /api/browser/tasks/{id}/takeover` | Take control |
| `POST /api/browser/tasks/{id}/release` | Release control |
| `POST /api/browser/tasks/{id}/action` | User action |
| `POST /api/browser/sessions/close` | Close session |
| `GET /api/browser/site-memory` | List site memories |
| `DELETE /api/browser/site-memory/{id}` | Delete memory |
| `DELETE /api/browser/site-memory/domain/{domain}` | Delete domain memories |

---

## Computer (`lazyclaw/computer/`)

### `security.py` — Command/path blocklist validation

| Class | Methods | Description |
|-------|---------|-------------|
| `SecurityManager` | `is_command_allowed(cmd)`, `is_path_allowed(path, write)` | Regex-based command blocklist + path blocklist for read/write |

**Constants:** `BLOCKED_COMMANDS`, `BLOCKED_WRITE_PATHS`, `BLOCKED_READ_PATHS`, `BLOCKED_HOME_PATTERNS`

### `native.py` — Local subprocess execution

| Class | Methods | Description |
|-------|---------|-------------|
| `NativeExecutor` | see below | Execute commands locally via subprocess |

| Method | Signature | Description |
|--------|-----------|-------------|
| `exec_command` | `async (cmd, timeout=30) -> dict` | Run shell command, capture stdout/stderr |
| `read_file` | `async (path) -> dict` | Read file (100KB max, UTF-8/base64) |
| `write_file` | `async (path, content) -> dict` | Write text to file |
| `list_dir` | `async (path=None) -> dict` | List directory (200 entries max) |
| `screenshot` | `async () -> dict` | Capture screen (mss+Pillow, JPEG base64) |

**Constants:** `MAX_FILE_READ`, `MAX_OUTPUT`, `COMMAND_TIMEOUT`, `MAX_DIR_ENTRIES`

### `connector_server.py` — Server-side WebSocket relay

| Class | Methods | Description |
|-------|---------|-------------|
| `ConnectorServer` | see below | Manages WebSocket connections from remote connectors |

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_token` | `async (user_id) -> str` | Generate connector auth token |
| `validate_token` | `async (token) -> str \| None` | Validate token, return user_id |
| `delete_token` | `async (user_id) -> bool` | Revoke token |
| `register` | `(user_id, ws, device_info)` | Register WebSocket connection |
| `unregister` | `(user_id)` | Remove connection + cleanup pending commands |
| `is_connected` | `(user_id) -> bool` | Check connector status |
| `get_device_info` | `(user_id) -> dict \| None` | Get connected device info |
| `send_command` | `async (user_id, command, args) -> str` | Send command, return cmd_id |
| `wait_for_result` | `async (cmd_id, timeout=35) -> dict \| None` | Wait for result |
| `report_result` | `(cmd_id, result)` | Receive result from connector |

### `manager.py` — Unified execution facade

| Class | Methods | Description |
|-------|---------|-------------|
| `ComputerManager` | see below | Routes to local or remote execution |

| Method | Signature | Description |
|--------|-----------|-------------|
| `exec_command` | `async (user_id, cmd, timeout=30) -> dict` | Execute shell command |
| `read_file` | `async (user_id, path) -> dict` | Read file |
| `write_file` | `async (user_id, path, content) -> dict` | Write file |
| `list_dir` | `async (user_id, path=None) -> dict` | List directory |
| `screenshot` | `async (user_id) -> dict` | Capture screenshot |

### Computer Skills (`lazyclaw/skills/builtin/computer.py`)

| Class | Category | Description |
|-------|----------|-------------|
| `RunCommandSkill` | computer | Execute shell command |
| `ReadFileSkill` | computer | Read file contents |
| `WriteFileSkill` | computer | Write content to file |
| `ListDirectorySkill` | computer | List directory entries |
| `TakeScreenshotSkill` | computer | Capture screen screenshot |

### Connector Routes (`lazyclaw/gateway/routes/connector.py`)

| Route | Description |
|-------|-------------|
| `POST /api/connector/token` | Generate connector token (username/password auth) |
| `GET /api/connector/status` | Check connector connection status |
| `DELETE /api/connector/token` | Revoke connector token |
| `WS /ws/connector` | WebSocket endpoint for remote connectors |

### Standalone Connector (`connector/`)

| File | Description |
|------|-------------|
| `connector/security.py` | SecurityManager with interactive approval prompts |
| `connector/connector.py` | WebSocket client with 6 handlers + auto-reconnect |
| `connector/main.py` | CLI entry point with setup wizard |

---

## Statistics

| Metric | Count |
|--------|-------|
| **Classes** | 55+ |
| **Async functions** | 70+ |
| **Sync functions** | 40+ |
| **Properties** | 85+ |
| **Built-in skills** | 19 |
| **API routes** | 40+ |
| **DB tables** | 23 |

---

*Last updated: 2026-03-14*
