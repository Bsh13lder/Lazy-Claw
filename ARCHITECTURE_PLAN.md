# LazyClaw Architecture Comparison & Improvement Plan

> Deep comparison of OpenClaw, Claude Code, and LazyClaw architectures with prioritized improvement roadmap and ready-to-paste session prompts.

---

## 1. Architecture Comparison (Side-by-Side)

### 1.1 Core Architecture

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **Philosophy** | Self-hosted personal AI agent in messaging apps | Model-driven dev tool — "the product is the model" | Modular agent platform with channel adapters |
| **Core Loop** | ReAct (Reason + Act) event-driven loop | TAOR (Think-Act-Observe-Repeat) while-loop | Tool-execution loop via agent runtime |
| **Language** | TypeScript (430K lines) | TypeScript (React + Ink for TUI, Bun runtime) | TypeScript/Python hybrid |
| **Entry Point** | Gateway receives message → Brain orchestrates | `query()` returns async iterator, streams messages | REST API + WebSocket gateway |
| **License** | MIT | Proprietary (Anthropic) | Proprietary |
| **GitHub Stars** | 335K+ (4 months) | N/A (closed source) | Pre-launch |

### 1.2 Agent Loop & Orchestration

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **Loop Type** | ReAct: load context → LLM → tool call or text → loop | TAOR: think → act (tool) → observe (result) → repeat | Linear: receive → plan → execute tools → respond |
| **Termination** | Final text response (no tool call) | `stop_reason != "tool_use"`, or max_turns/max_budget hit | Task completion or timeout |
| **Planning Phase** | Implicit in ReAct reasoning | Explicit Plan Mode (Shift+Tab×2) — read-only research → structured plan → user approval → execute | Basic task decomposition |
| **Verification** | None built-in | Built-in verify phase: run tests, check output after changes | Manual verification |
| **Reflection** | None built-in | Implicit via TAOR loop + compaction summaries | None |
| **Effort Levels** | Single mode | 4 levels: low/medium/high/max (controls reasoning depth) | Single mode |
| **Error Recovery** | Retry on failure | Checkpoints (snapshot before every edit), Esc to undo | Basic retry logic |

### 1.3 Multi-Agent / Subagent Architecture

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **Multi-Agent** | Agent bindings (channel → agent), different models per agent | Subagent dispatch via Agent tool, each gets isolated context | Single agent per request |
| **Agent Types** | Single type, model varies | 3 built-in: General-Purpose (Sonnet), Explore (Haiku, read-only), Plan (research) | Single type |
| **Parallel Execution** | Agent-to-agent messaging, parallel channels | Concurrent subagents in single message, read-only tools run in parallel | Sequential only |
| **Isolation** | Channel-level isolation | Git worktree isolation — each agent gets full codebase copy on separate branch | None |
| **Coordination** | Mission Control, ClawFlow orchestrators | Main agent coordinates, subagents return results; experimental Agent Teams for self-coordination | Centralized coordinator |
| **Depth Limit** | Unlimited nesting | 1 level (subagents cannot spawn subagents) | 1 level |
| **Context Sharing** | Shared memory files | Subagents get fresh context; Agent Teams share findings | Shared session state |

### 1.4 Tool & Skill System

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **Tool Categories** | Skills (markdown), Plugins (TS/JS), Webhooks | Primitive tools (Bash, Read, Edit, Write, Grep, Glob) + MCP tools | 101+ skills, MCP bridge, browser automation |
| **Skill Format** | SKILL.md markdown files | SKILL.md with frontmatter + instructions | SKILL.md format |
| **Marketplace** | ClawHub: 5,400+ skills, CLI install, VirusTotal scanning | No public marketplace (skills bundled or user-created) | Plugin registry (internal) |
| **MCP Integration** | MCP host, 500+ servers, configured in openclaw.json | MCP client, dynamic tool loading via ToolSearch | MCP bridge for external tools |
| **Tool Discovery** | Registry search, CLI install | ToolSearch tool — lazy-loads tools on demand to save context | Manual configuration |
| **Tool Parallelism** | Sequential | Read-only: concurrent; State-modifying: sequential | Sequential |
| **Permission Model** | Capability-based, human-in-the-loop for sensitive | 3 modes: Default (ask), Auto-accept edits, Plan (read-only); allowed/disallowed tool lists | Role-based |

### 1.5 Context & Memory Management

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **Memory Architecture** | 12-layer with knowledge graphs, semantic search | Multi-level CLAUDE.md + Auto Memory + Session History | Personal memory + daily logs |
| **Persistence** | Markdown files on filesystem, Neo4j graphs (Graphiti), knowledge graphs (Cognee) | CLAUDE.md files (global/project/directory/personal), auto memory in ~/.claude/projects/ | Database-backed personal memory |
| **Context Window** | Depends on model (typically 128K-200K) | 1M tokens (Opus 4.6 / Sonnet 4.6) | Model-dependent |
| **Compaction** | Manual pruning, decay system | Automatic compaction — summarizes older history, keeps recent + key decisions, customizable via CLAUDE.md | Manual truncation |
| **Session Management** | Conversation-based | Continue (most recent), Resume (by ID), Fork (branch session) | Session-based |
| **Memory Layers** | 12 layers: short-term, long-term, episodic, semantic, procedural, knowledge graph, etc. | 4 layers: Global → Project → Directory → Personal CLAUDE.md, plus auto memory | 2 layers: personal + daily logs |
| **Semantic Search** | Built-in multilingual, 7ms GPU lookup | Not built-in (relies on context window size) | Basic keyword matching |

### 1.6 Scheduling & Automation

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **Scheduling** | Dual: Heartbeat (periodic awareness, 30min default) + Cron (isolated background) | Session-based (manual trigger or CI/CD integration) | Job orchestrator with monitors + workers |
| **Background Jobs** | Cron jobs with stagger window, task records | Ctrl+B to background agent, continue working | Worker queue system |
| **Proactive Checks** | Heartbeat batches multiple inbox checks, stays quiet unless needed | None (reactive only) | Monitor-based triggers |

### 1.7 Channel & Deployment

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **Channels** | 50+ (WhatsApp, Telegram, Slack, Discord, Signal, iMessage, Matrix, Teams, etc.) | Terminal only (CLI) + IDE integrations (VS Code, JetBrains) | Telegram, WhatsApp + channel adapter framework |
| **Deployment** | Local (Mac mini), Docker, VPS, NAS, Kubernetes | Local install (npm), runs in user's terminal | Docker, VPS |
| **Gateway** | Single long-lived Node.js service, WebSocket API, JSON Schema validation | N/A (local CLI tool) | REST API + WebSocket gateway |

### 1.8 TUI & Dashboard

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **TUI Components** | Header, chat log, tool cards (Ctrl+O toggle), status line, input area, real-time streaming | React+Ink terminal UI, TodoWrite checklist widget, streaming output, permission prompts | TUI dashboard (custom) |
| **Dashboard** | Web dashboard: live feed, memory browser, cost tracking, TOTP MFA auth | Terminal-only (no web dashboard) | TUI dashboard with monitoring |
| **Theme Support** | Auto light/dark detection, WCAG 2.1 contrast, `OPENCLAW_THEME=light` override | Terminal-native theming | Basic theme |
| **Monitoring** | Real-time in dashboard + TUI status line (connection, agent, session, model, tokens) | Token usage in status bar, cost tracking per session | Basic health checks |

### 1.9 Security

| Dimension | OpenClaw | Claude Code / Agent SDK | LazyClaw (Current) |
|-----------|----------|------------------------|---------------------|
| **Execution Safety** | Broad host access — multiple CVEs, 63% deployments vulnerable | Checkpoints before every edit, permission modes, allowed/disallowed tool lists | Sandboxed tool execution |
| **Supply Chain** | ClawHub: 820+ malicious skills out of 10,700 | No public registry (reduced attack surface) | Curated plugin registry |
| **Isolation** | Docker optional, no default sandbox | Git worktree isolation for parallel agents, no OS-level sandbox | Container-based isolation |
| **Known Issues** | CVE-2026-25253 (WebSocket hijack), CVE-2026-24763 (command injection), CVE-2026-26322 (SSRF), CVE-2026-26329 (path traversal) | Permission system complexity noted as technical debt | TBD (pre-launch) |

---

## 2. Gap Analysis: LazyClaw vs OpenClaw & Claude Code

### 2.1 Critical Gaps (vs Both)

| Gap | OpenClaw Has | Claude Code Has | LazyClaw Status | Severity |
|-----|-------------|-----------------|-----------------|----------|
| **Planning phase** | Implicit in ReAct | Explicit Plan Mode with approval gate | None | HIGH |
| **Verification loop** | None | Built-in verify after changes | None | HIGH |
| **Parallel tool execution** | Channel-parallel | Read-only concurrent, subagent parallel | Sequential only | HIGH |
| **Context compaction** | Decay system + pruning | Automatic summarization compaction | Manual truncation | HIGH |
| **Effort/reasoning levels** | Single | 4 levels (low→max) | Single | MEDIUM |

### 2.2 Gaps vs OpenClaw Specifically

| Gap | OpenClaw | LazyClaw | Impact |
|-----|----------|----------|--------|
| **Channel coverage** | 50+ messaging platforms | 2 (Telegram, WhatsApp) | Market reach |
| **Skill marketplace** | ClawHub: 5,400+ skills, CLI install, security scanning | 101 internal skills, no public registry | Ecosystem growth |
| **Memory depth** | 12 layers + knowledge graphs + semantic search | 2 layers, keyword matching | Personalization quality |
| **Heartbeat system** | Proactive awareness checks every 30min | Reactive only (monitors) | Proactive capabilities |
| **Community ecosystem** | 52 projects, 626K stars, forks in 4 languages | Pre-launch | Adoption velocity |

### 2.3 Gaps vs Claude Code Specifically

| Gap | Claude Code | LazyClaw | Impact |
|-----|------------|----------|--------|
| **Subagent dispatch** | 3 agent types, parallel spawn, isolated context | Single agent type, sequential | Task throughput |
| **Worktree isolation** | Git worktree per agent — full codebase copy | None | Safe parallel work |
| **Hook system** | 18+ lifecycle hooks (PreToolUse, PostToolUse, Stop, etc.) | None | Extensibility |
| **Session fork/resume** | Continue, Resume by ID, Fork (branch sessions) | Basic sessions | Recovery & exploration |
| **TodoWrite tracking** | Real-time task checklist in TUI, persisted across sessions | No equivalent | Visibility & planning |
| **ToolSearch lazy loading** | Load tools on-demand to save context tokens | All tools loaded upfront | Context efficiency |
| **Automatic memory** | Auto-accumulates insights across sessions | Manual memory only | Learning over time |

### 2.4 LazyClaw Advantages (Keep These)

| Advantage | vs OpenClaw | vs Claude Code |
|-----------|-------------|----------------|
| **Browser automation** | OpenClaw has basic web skills | Claude Code has no built-in browser | Native browser-use library integration |
| **Job orchestrator** | OpenClaw has cron, no job queue | Claude Code is session-based only | Full monitor + worker queue |
| **Channel adapters** | Similar architecture | Claude Code is terminal-only | Framework for any channel |
| **MCP bridge** | Similar | Similar | Clean external tool integration |
| **REST API gateway** | OpenClaw uses WebSocket only | No API (CLI only) | API-first for integrations |

---

## 3. Proposed Improvements

### 3.1 Agent Loop Improvements

#### 3.1.1 Three-Phase TAOR Loop (P0 — Launch Blocker)

Replace the current linear loop with a three-phase cycle inspired by Claude Code:

```
PLAN → EXECUTE → VERIFY → (loop or respond)
```

**Phase 1 — Plan**: Gather context, decompose task, identify tools needed, estimate effort. For complex tasks, output a structured plan for user approval before proceeding.

**Phase 2 — Execute**: Run tool calls. Support parallel execution for read-only tools. Checkpoint state before every mutation.

**Phase 3 — Verify**: After execution, validate results. Run tests if available, compare output to expected, check for errors. If verification fails, loop back to Plan with failure context.

#### 3.1.2 Effort Levels (P1 — Week 1)

Add 4 effort levels controlling reasoning depth and token usage:

- **low**: Quick lookups, simple responses. Minimal reasoning.
- **medium**: Standard tasks. Balanced reasoning.
- **high**: Complex tasks. Thorough analysis, multiple verification passes.
- **max**: Deep multi-step problems. Maximum reasoning, self-critique, multiple approaches.

Route automatically based on task complexity, or let users override.

#### 3.1.3 Reflection & Self-Critique (P2 — Month 1)

After each major action, inject a reflection prompt: "Did this achieve the goal? What could go wrong? Should I verify?" This catches errors early without waiting for the verify phase.

---

### 3.2 Multi-Agent Orchestration

#### 3.2.1 Subagent Dispatch System (P0 — Launch Blocker)

Implement Claude Code's dispatch pattern with three agent types:

**General-Purpose Agent**: Full tool access, handles complex multi-step tasks. Uses the primary model.

**Explore Agent**: Read-only, fast model (e.g., Haiku-class). For searching, reading, gathering context. Cannot modify state.

**Specialist Agent**: Configurable per-skill. Browser agent, data agent, communication agent — each with scoped tools and appropriate model.

**Dispatch Rules**:
- 3+ independent subtasks → spawn parallel subagents
- Research/search tasks → Explore agent (cheap, fast)
- Mutations → General-Purpose agent (careful, verified)
- Single-depth only (subagents cannot spawn subagents)

#### 3.2.2 Worktree-Style Isolation (P1 — Week 1)

For parallel agents that modify state, create isolated execution contexts:

- Each agent gets a copy of the relevant state/workspace
- Changes are merged back to main context after completion
- Conflicts detected and flagged for resolution

For LazyClaw's use case (not git-based), this means:
- Snapshot current session state before spawning
- Each agent works on its snapshot
- Results merged by the coordinator

#### 3.2.3 Agent Teams (P2 — Month 1)

For sustained parallel work (not just dispatch-and-collect), implement Agent Teams:

- Workers share a coordination channel
- Can challenge each other's findings
- Self-organize task distribution
- Coordinator monitors progress and resolves conflicts

---

### 3.3 Context Management

#### 3.3.1 Automatic Compaction (P0 — Launch Blocker)

When context approaches the model's limit:

1. Summarize older conversation history (keep last N turns verbatim)
2. Preserve key decisions, tool results, and user preferences
3. Emit a compaction boundary marker
4. Allow customization via memory config ("always preserve X")

**Implementation**: Before each LLM call, check token count. If above 80% threshold, trigger compaction. Use a fast model to summarize older turns.

#### 3.3.2 ToolSearch / Lazy Tool Loading (P1 — Week 1)

With 101+ skills, loading all tool definitions wastes context:

- Load only a summary index at session start (skill name + 1-line description)
- When the agent needs a tool, it calls ToolSearch to load full definition
- Unload tools not used in last N turns
- Saves thousands of tokens per session

#### 3.3.3 Session Fork & Resume (P1 — Week 1)

Adopt Claude Code's session management:

- **Continue**: Pick up the most recent session for a user/channel
- **Resume**: Jump to a specific session by ID
- **Fork**: Branch a session to explore alternatives without losing the original

Critical for long-running tasks and error recovery.

#### 3.3.4 Summarization Instructions in Memory (P2 — Month 1)

Let users/admins define what the compactor must preserve:

```markdown
## Compaction Rules
- Always preserve: user preferences, active task list, tool results from last 3 turns
- Summarize: research findings, intermediate reasoning
- Drop: failed tool attempts, superseded plans
```

---

### 3.4 Tool Routing Optimization

#### 3.4.1 Parallel Tool Execution (P0 — Launch Blocker)

Classify every tool as read-only or state-modifying:

- **Read-only** (run concurrently): search, fetch, read file, query database, check status
- **State-modifying** (run sequentially): write file, send message, execute command, update record

When the agent requests multiple tool calls in one turn, batch read-only calls concurrently.

#### 3.4.2 Model-Based Tool Routing (P1 — Week 1)

Route different tool operations to different models based on cost/speed:

- **Exploration/search**: Fast cheap model (Haiku-class)
- **Planning/reasoning**: Mid-tier model (Sonnet-class)
- **Complex generation**: Premium model (Opus-class)
- **Tool execution**: No model needed (direct execution)

#### 3.4.3 Permission & Safety Framework (P2 — Month 1)

Adopt Claude Code's tiered permission model:

- **Auto-approve**: Read-only tools, safe queries
- **Confirm**: State-modifying tools, external API calls
- **Block**: Dangerous operations (delete, admin actions)
- **Configurable**: Users define allowed/disallowed tool lists per agent

---

### 3.5 Memory Architecture Upgrades

#### 3.5.1 Multi-Layer Memory (P0 — Launch Blocker)

Expand from 2 layers to 5:

| Layer | Scope | Persistence | Example |
|-------|-------|-------------|---------|
| **Session** | Current conversation | Ephemeral | Tool results, intermediate reasoning |
| **User** | Per-user across sessions | Permanent | Preferences, communication style, timezone |
| **Channel** | Per-channel/group | Permanent | Group context, shared decisions, channel rules |
| **Project** | Per-project/workspace | Permanent | Project goals, tech stack, team members |
| **Global** | All agents/users | Permanent | System config, shared knowledge base |

#### 3.5.2 Auto Memory Accumulation (P1 — Week 1)

After each session, automatically extract and persist:

- User preferences discovered
- Successful tool patterns
- Error resolutions
- Domain knowledge learned
- Workflow habits

Store in per-user memory files. Load first 200 lines at session start (like Claude Code's approach).

#### 3.5.3 Semantic Search over Memory (P1 — Week 1)

Replace keyword matching with vector-based semantic search:

- Embed memory entries on write
- Retrieve by semantic similarity at session start
- Prioritize recent + frequently accessed entries
- Use activation/decay scoring (inspired by OpenClaw's system)

#### 3.5.4 Knowledge Graph Integration (P2 — Month 1)

For power users, offer optional knowledge graph storage:

- Extract entities and relationships from conversations
- Build per-user knowledge graphs
- Enable complex queries ("What did I discuss with X about Y last week?")
- Options: Neo4j (like OpenClaw's Graphiti) or lightweight SQLite-based graph

---

### 3.6 TUI & Dashboard Improvements

#### 3.6.1 TodoWrite Task Widget (P0 — Launch Blocker)

Add a real-time task tracking widget to the TUI:

- Agent creates/updates task list as it works
- Shows: completed items, current focus, remaining tasks
- Persists across sessions
- Users see exactly what the agent is doing and what's left

#### 3.6.2 Tool Execution Cards (P1 — Week 1)

Display tool calls as collapsible cards (like OpenClaw's TUI):

- Card header: tool name + status (running/done/failed)
- Collapsed: 1-line summary of args and result
- Expanded (toggle): full arguments, full result, timing
- Color-coded: green (success), red (error), yellow (running)

#### 3.6.3 Cost & Token Tracking (P1 — Week 1)

Show in status bar:

- Tokens used this session (input/output)
- Estimated cost this session
- Context window usage (% full)
- Model currently in use

#### 3.6.4 Web Dashboard (P2 — Month 1)

Build a web dashboard for non-terminal users:

- Live conversation feed
- Memory browser (view/edit memory layers)
- Skill registry browser
- Agent health monitoring
- Session history with fork/resume
- Cost analytics over time

---

### 3.7 Skill Discovery & Marketplace

#### 3.7.1 Public Skill Registry (P1 — Week 1)

Launch a ClawHub-equivalent marketplace:

- Searchable catalog of community skills
- CLI install: `lazyclaw skills install <slug>`
- Categorized: productivity, development, communication, automation, data
- Rating system + usage metrics
- Security scanning on publish (learned from OpenClaw's supply chain issues)

#### 3.7.2 Skill Composition (P2 — Month 1)

Allow skills to chain and compose:

- Skill A's output → Skill B's input (pipelines)
- Conditional routing (if X then Skill A, else Skill B)
- Parallel skill execution for independent steps
- Visual pipeline builder in web dashboard

#### 3.7.3 Skill Versioning & Rollback (P2 — Month 1)

- Semantic versioning for skills
- Pin skill versions per agent/user
- Automatic rollback if new version fails
- Changelog and migration guides

---

## 4. Priority Matrix

### P0 — Launch Blockers (Must ship before public launch)

| # | Improvement | Effort | Impact | Dependencies |
|---|------------|--------|--------|--------------|
| 1 | Three-phase TAOR loop (Plan → Execute → Verify) | L | Critical | None |
| 2 | Subagent dispatch system (3 agent types) | XL | Critical | #1 |
| 3 | Automatic context compaction | M | Critical | None |
| 4 | Parallel tool execution (read-only concurrent) | M | High | None |
| 5 | Multi-layer memory (5 layers) | L | Critical | None |
| 6 | TodoWrite task widget in TUI | S | High | None |

### P1 — Week 1 (Ship within first week post-launch)

| # | Improvement | Effort | Impact | Dependencies |
|---|------------|--------|--------|--------------|
| 7 | Effort levels (low/medium/high/max) | S | Medium | #1 |
| 8 | Worktree-style isolation for parallel agents | L | High | #2 |
| 9 | ToolSearch / lazy tool loading | M | High | None |
| 10 | Session fork & resume | M | High | None |
| 11 | Auto memory accumulation | M | High | #5 |
| 12 | Semantic search over memory | M | High | #5 |
| 13 | Tool execution cards in TUI | S | Medium | None |
| 14 | Cost & token tracking | S | Medium | None |
| 15 | Model-based tool routing | M | Medium | #2 |
| 16 | Public skill registry | L | High | None |

### P2 — Month 1 (Ship within first month)

| # | Improvement | Effort | Impact | Dependencies |
|---|------------|--------|--------|--------------|
| 17 | Reflection & self-critique | S | Medium | #1 |
| 18 | Agent Teams (self-coordinating) | XL | Medium | #2, #8 |
| 19 | Summarization instructions in memory config | S | Medium | #3 |
| 20 | Permission & safety framework | L | High | None |
| 21 | Knowledge graph integration | L | Medium | #5, #12 |
| 22 | Web dashboard | XL | High | #6, #13, #14 |
| 23 | Skill composition & pipelines | L | Medium | #16 |
| 24 | Skill versioning & rollback | M | Medium | #16 |

**Effort Key**: S = 1-2 days, M = 3-5 days, L = 1-2 weeks, XL = 2-4 weeks

---

## 5. Session Prompts (Ready-to-Paste for Claude Code)

### Prompt 1: Three-Phase TAOR Loop (P0)

```
You are implementing a three-phase agent loop for LazyClaw. The current loop is linear
(receive → execute → respond). Replace it with a TAOR cycle: Plan → Execute → Verify.

Architecture requirements:
- Phase 1 (PLAN): Before executing any tools, analyze the user's request. Decompose into
  subtasks. Identify which tools are needed. For complex tasks (3+ steps), generate a
  structured plan and optionally present it for user approval before proceeding.
- Phase 2 (EXECUTE): Run tool calls from the plan. Support checkpointing — snapshot
  state before every mutation so we can rollback. If multiple read-only tools are needed,
  batch them for parallel execution.
- Phase 3 (VERIFY): After execution, validate results. Check for errors, compare output
  to expected results, run any available tests. If verification fails, loop back to PLAN
  with failure context (max 3 retries).

The loop should support an "effort" parameter (low/medium/high/max) that controls how
thorough each phase is. Low effort skips planning and verification for simple lookups.
Max effort does multi-pass verification and self-critique.

Look at the existing agent runtime code and refactor the main loop. Preserve all existing
tool execution logic — wrap it in the new three-phase structure.

Do NOT change tool implementations. Only change the orchestration loop.
```

### Prompt 2: Subagent Dispatch System (P0)

```
You are implementing a subagent dispatch system for LazyClaw, inspired by Claude Code's
architecture. The system needs three agent types that the main agent can spawn:

1. EXPLORE AGENT: Read-only, uses a fast/cheap model. Tools: search, read, fetch, query.
   Cannot modify state. Purpose: gathering context, researching, finding information.
   Should start with fresh context (no history pollution).

2. GENERAL-PURPOSE AGENT: Full tool access, uses the primary model. Purpose: complex
   multi-step tasks that require state changes. Gets isolated execution context.

3. SPECIALIST AGENT: Configurable per-skill. Example: browser agent has only browser
   tools, data agent has only data tools. Scoped tool access for safety and efficiency.

Dispatch rules:
- Main agent analyzes the task and decides whether to handle directly or dispatch
- 3+ independent subtasks → spawn parallel subagents
- Research/search tasks → Explore agent
- State mutations → General-Purpose agent
- Single-depth limit: subagents CANNOT spawn their own subagents
- Each subagent gets isolated context (not the full parent history)
- Results flow back to main agent as structured summaries

Implementation:
- Create an AgentDispatcher class that manages subagent lifecycle
- Create AgentType enum and SubagentConfig interface
- Implement parallel execution with Promise.allSettled
- Add a SubagentResult type that includes: agent_type, task, result, tokens_used, duration
- Wire into the existing agent runtime's execute phase

Start by reading the current agent runtime code to understand the integration points.
```

### Prompt 3: Automatic Context Compaction (P0)

```
You are implementing automatic context compaction for LazyClaw. When the conversation
history approaches the model's context limit, the system must automatically summarize
older history to free space while preserving critical information.

Requirements:
- Monitor token count before each LLM call
- When above 80% of context limit, trigger compaction
- Compaction process:
  1. Keep the last N turns verbatim (configurable, default 5)
  2. Keep all tool results from last 3 turns verbatim
  3. Summarize everything older into a structured summary
  4. Preserve: user preferences, active task list, key decisions, error context
  5. Drop: superseded plans, failed tool attempts, redundant reasoning
- Use a fast model (Haiku-class) for the summarization call
- Emit a "compaction_boundary" event so the TUI can show a marker
- Support custom compaction rules defined in agent memory config

The summary format should be:
```
## Session Summary (compacted at turn N)
### Key Decisions: ...
### Active Tasks: ...
### User Preferences: ...
### Important Context: ...
### Tool Results (preserved): ...
```

Integrate with the existing session management. The compaction should be transparent —
the agent should continue working normally after compaction.
```

### Prompt 4: Parallel Tool Execution (P0)

```
You are implementing parallel tool execution for LazyClaw. Currently all tools run
sequentially. We need concurrent execution for read-only tools while keeping state-
modifying tools sequential.

Step 1: Classify every tool in the system as either:
- READ_ONLY: search, fetch, read, query, status checks, list operations
- STATE_MODIFYING: write, send, execute, update, delete, create

Step 2: Add a `readOnly: boolean` property to the tool interface/schema.

Step 3: Modify the tool execution engine:
- When the agent requests multiple tool calls in one turn:
  - Group by read_only vs state_modifying
  - Execute all read-only tools concurrently (Promise.allSettled)
  - Execute state-modifying tools sequentially in order
  - Read-only batch runs first, then state-modifying sequence
- When a single tool is requested: execute normally

Step 4: Add execution metrics:
- Track: tool_name, start_time, end_time, parallel_group_id
- Log: "Executed 4 tools in parallel (320ms) vs sequential estimate (1280ms)"

Step 5: Update the TUI to show parallel execution:
- Show concurrent tools side-by-side or with a "parallel" indicator
- Show timing savings

Read the existing tool execution code first. Preserve all existing tool behavior —
only change how multiple tools are orchestrated.
```

### Prompt 5: Multi-Layer Memory System (P0)

```
You are implementing a 5-layer memory system for LazyClaw, replacing the current
2-layer (personal + daily logs) system.

The 5 layers:

1. SESSION MEMORY (ephemeral):
   - Current conversation history, tool results, intermediate reasoning
   - Dies when session ends (unless compacted into higher layers)
   - Loaded automatically

2. USER MEMORY (persistent, per-user):
   - Preferences, communication style, timezone, language
   - Successful interaction patterns, frequently used skills
   - Stored as markdown files: /memory/users/{user_id}/MEMORY.md
   - First 200 lines loaded at session start

3. CHANNEL MEMORY (persistent, per-channel):
   - Group context, shared decisions, channel rules, recurring topics
   - Stored as: /memory/channels/{channel_id}/MEMORY.md
   - Loaded when agent joins channel

4. PROJECT MEMORY (persistent, per-project):
   - Project goals, tech stack, team members, conventions
   - Stored as: /memory/projects/{project_id}/MEMORY.md
   - Loaded when project context is active

5. GLOBAL MEMORY (persistent, system-wide):
   - System config, shared knowledge base, admin rules
   - Stored as: /memory/GLOBAL.md
   - Always loaded

Memory operations:
- read_memory(layer, scope_id) → string
- write_memory(layer, scope_id, content) → void
- search_memory(query, layers?) → MemoryResult[]
- auto_extract(session) → extracts learnings into appropriate layers

Loading order: Global → Project → Channel → User → Session
Priority (conflicts): User > Channel > Project > Global

Start by reading the current memory system code, then refactor to support all 5 layers.
Keep backward compatibility with existing personal memory data.
```

### Prompt 6: TodoWrite Task Widget (P0)

```
You are implementing a TodoWrite-style task tracking widget for LazyClaw's TUI,
inspired by Claude Code's approach.

Requirements:

1. TodoManager class:
   - create_todo(content, activeForm) → Todo
   - update_todo(id, status: pending|in_progress|completed) → void
   - get_todos() → Todo[]
   - Only ONE todo should be in_progress at a time

2. Todo interface:
   - id: string
   - content: string (imperative: "Run tests")
   - activeForm: string (continuous: "Running tests")
   - status: "pending" | "in_progress" | "completed"
   - created_at, updated_at timestamps

3. TUI Widget:
   - Renders as a sidebar or header section in the TUI
   - Shows all todos with status indicators: [ ] pending, [→] in_progress, [✓] completed
   - Currently active todo shown prominently with activeForm text
   - Updates in real-time as agent works
   - Persists across session (saved to session state)

4. Agent Integration:
   - Agent calls TodoWrite at the start of complex tasks to create plan
   - Agent updates status as it completes each step
   - Agent marks tasks complete IMMEDIATELY after finishing (no batching)
   - For any task with 3+ steps, TodoWrite is mandatory

5. Wire into the existing TUI framework. Read the TUI code first to understand
   the layout system, then add the todo widget.
```

### Prompt 7: ToolSearch / Lazy Loading (P1)

```
You are implementing lazy tool loading for LazyClaw. With 101+ skills, loading all tool
definitions at session start wastes thousands of context tokens.

New approach:
1. At session start, load only a TOOL INDEX — a compact list of tool names + 1-line
   descriptions (estimate: ~50 tokens per tool vs ~500 for full definition)
2. Create a ToolSearch tool that the agent calls when it needs a specific tool
3. ToolSearch loads the full tool definition into context on demand
4. Track tool usage — auto-load frequently-used tools for repeat users
5. Unload tools not referenced in last 5 turns (optional, for aggressive optimization)

ToolSearch interface:
- Input: { query: string, max_results?: number }
- Output: Full tool definitions for matching tools
- Matching: fuzzy name match + description keyword search

Tool Index format (loaded at session start):
```json
[
  {"name": "web_search", "summary": "Search the web for information"},
  {"name": "send_telegram", "summary": "Send a message via Telegram"},
  ...
]
```

Benefits:
- 101 tools × ~450 token savings = ~45K tokens saved per session
- Agent still knows what tools exist (from index)
- Full definitions loaded only when needed

Read the existing tool registry and skill loading code first.
```

### Prompt 8: Session Fork & Resume (P1)

```
You are implementing advanced session management for LazyClaw with three operations
inspired by Claude Code:

1. CONTINUE: Resume the most recent session for a user/channel pair.
   - Finds latest session by user_id + channel_id
   - Restores full context (conversation history, memory state, active tasks)
   - Agent picks up where it left off

2. RESUME: Jump to a specific session by session_id.
   - Loads exact session state
   - Useful for returning to a specific conversation thread
   - API: POST /sessions/{session_id}/resume

3. FORK: Create a new session branching from an existing one.
   - Copies full history from source session
   - New session gets its own ID
   - Source session remains unchanged
   - Both can be resumed independently
   - Use case: "try a different approach without losing current progress"
   - API: POST /sessions/{session_id}/fork

Session state includes:
- Conversation history (messages)
- Memory state at time of session
- Active task list (TodoWrite state)
- Tool execution history
- Compaction summaries (if any)

Storage: Sessions stored as JSON files in /sessions/{session_id}/
Include: created_at, updated_at, user_id, channel_id, parent_session_id (for forks),
status (active|paused|completed), message_count, token_count.

Read the existing session management code first and extend it.
```

### Prompt 9: Auto Memory Accumulation (P1)

```
You are implementing automatic memory accumulation for LazyClaw. After each session,
the system should automatically extract and persist learnings without manual intervention.

Extraction pipeline (runs at session end):
1. Feed the full session transcript to a fast model with this prompt:
   "Extract the following from this conversation:
   - User preferences discovered (communication style, timezone, tool preferences)
   - Successful patterns (tool sequences that worked, approaches that succeeded)
   - Error resolutions (what failed and how it was fixed)
   - Domain knowledge (facts about user's projects, team, workflows)
   - Workflow habits (preferred order of operations, common requests)"

2. Deduplicate against existing memory (don't store what we already know)

3. Append new entries to the appropriate memory layer:
   - User preferences → User Memory
   - Project facts → Project Memory
   - Channel conventions → Channel Memory

4. Each entry includes:
   - content: the extracted knowledge
   - source_session: session ID it came from
   - confidence: high/medium/low
   - extracted_at: timestamp

5. Memory file format (append-only):
   ```markdown
   ## Auto-extracted: 2026-03-31
   - [HIGH] User prefers concise responses without bullet points
   - [MEDIUM] Project uses PostgreSQL 15 with TimescaleDB extension
   - [HIGH] User's timezone is UTC+3
   ```

6. Loading: First 200 lines of each relevant memory file loaded at session start.
   If file exceeds 200 lines, summarize older entries periodically.

Integrate with the multi-layer memory system from Prompt 5.
```

### Prompt 10: Public Skill Registry (P1)

```
You are implementing a public skill registry for LazyClaw (similar to OpenClaw's
ClawHub but with better security).

Components:

1. REGISTRY API:
   - GET /skills — list/search skills (paginated, filterable by category/rating)
   - GET /skills/{slug} — skill details + SKILL.md content
   - POST /skills — publish new skill (authenticated)
   - PUT /skills/{slug} — update skill
   - GET /skills/{slug}/versions — version history

2. SKILL PACKAGE FORMAT:
   ```
   skill-name/
   ├── SKILL.md          # Skill definition (required)
   ├── metadata.json     # Name, version, author, category, dependencies
   ├── README.md         # Documentation
   └── tests/            # Test cases (optional but encouraged)
   ```

3. CLI COMMANDS:
   - lazyclaw skills search <query>
   - lazyclaw skills install <slug>[@version]
   - lazyclaw skills publish
   - lazyclaw skills update <slug>
   - lazyclaw skills uninstall <slug>

4. SECURITY (learn from OpenClaw's 820+ malicious skills problem):
   - Mandatory automated security scan on publish
   - Skills run in sandboxed context (no host filesystem access by default)
   - Permission declarations in metadata.json (what the skill needs access to)
   - Community reporting system for malicious skills
   - Publisher reputation score (based on history + community ratings)
   - 48-hour quarantine period for new publishers' first skill
   - Automated testing: skill must pass its own test suite before publishing

5. CATEGORIES:
   productivity, development, communication, automation, data, finance,
   social, utilities, integrations, browser, ai-ml

Build the registry API and CLI. Use a simple database (SQLite for MVP, PostgreSQL
for production). Skill files stored on filesystem with registry metadata in DB.
```

---

## 6. Implementation Order (Recommended Sequence)

```
Week -1 (Pre-launch):
├── P0.1: Three-phase TAOR loop
├── P0.3: Automatic context compaction
├── P0.4: Parallel tool execution
├── P0.5: Multi-layer memory (5 layers)
└── P0.6: TodoWrite task widget

Week 0 (Launch):
└── P0.2: Subagent dispatch system (depends on TAOR loop)

Week 1:
├── P1.7:  Effort levels
├── P1.9:  ToolSearch / lazy loading
├── P1.10: Session fork & resume
├── P1.11: Auto memory accumulation
├── P1.13: Tool execution cards in TUI
└── P1.14: Cost & token tracking

Week 2:
├── P1.8:  Worktree-style isolation
├── P1.12: Semantic search over memory
├── P1.15: Model-based tool routing
└── P1.16: Public skill registry (MVP)

Month 1:
├── P2.17: Reflection & self-critique
├── P2.19: Summarization instructions
├── P2.20: Permission & safety framework
├── P2.22: Web dashboard (v1)
└── P2.23: Skill composition & pipelines

Month 2+:
├── P2.18: Agent Teams
├── P2.21: Knowledge graph integration
└── P2.24: Skill versioning & rollback
```

---

## 7. Key Architectural Decisions

### Decision 1: Adopt Claude Code's "Model-Driven" Philosophy

OpenClaw wraps the model in heavy scaffolding (430K lines). Claude Code takes the opposite approach — minimal scaffolding, let the model drive. LazyClaw should lean toward Claude Code's approach since it's optimized for Claude models.

**Recommendation**: Keep the orchestration layer thin. The agent loop, subagent dispatch, and memory system are infrastructure — but tool implementations, skill logic, and decision-making should be delegated to the model.

### Decision 2: Markdown-First Memory (Not Database)

Both OpenClaw and Claude Code use markdown files for memory. This is intentional — LLMs read markdown natively. Database storage adds a translation layer.

**Recommendation**: Store all memory as markdown files. Use a database only for indexing and search. The source of truth is always the markdown.

### Decision 3: Security-First Skill Registry

OpenClaw's supply chain disaster (820+ malicious skills, multiple CVEs) is a cautionary tale. LazyClaw's registry should be secure by default.

**Recommendation**: Mandatory sandboxing, permission declarations, automated scanning, quarantine periods. Accept slower ecosystem growth in exchange for trust.

### Decision 4: Subagents Over Agent Teams (For Now)

Claude Code offers both subagents (dispatch-and-collect) and Agent Teams (self-coordinating). Subagents are simpler, more predictable, and sufficient for most tasks.

**Recommendation**: Ship subagent dispatch as P0. Add Agent Teams as P2 only after subagents are battle-tested.

---

## Appendix: Research Sources

### OpenClaw
- GitHub: github.com/openclaw/openclaw (335K+ stars)
- Docs: docs.openclaw.ai
- ClawHub: openclawskill.ai (5,400+ skills)
- Architecture: ppaolo.substack.com/p/openclaw-system-architecture-overview
- Security: blogs.cisco.com/ai/personal-ai-agents-like-openclaw-are-a-security-nightmare

### Claude Code
- Docs: code.claude.com/docs/en/how-claude-code-works
- Agent SDK: platform.claude.com/docs/en/agent-sdk/overview
- Subagents: code.claude.com/docs/en/sub-agents
- Memory: code.claude.com/docs/en/memory
- Hooks: code.claude.com/docs/en/hooks-guide

### Additional References
- Claude Code Architecture (Reverse Engineered): vrungta.substack.com/p/claude-code-architecture-reverse
- OpenClaw Memory Architecture: github.com/coolmanns/openclaw-memory-architecture
- Claude Code Worktrees Guide: claudefa.st/blog/guide/development/worktree-guide
