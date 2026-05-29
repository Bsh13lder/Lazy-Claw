"""Single source of truth for Claude Code built-in tools that lazyclaw
must always block on the MODE_CLAUDE transports.

Both the SDK provider (`claude_sdk_provider.py`) and the legacy CLI
provider (`claude_cli_provider.py`) import :data:`DISALLOWED_BUILT_INS`
from here so the block-list can NEVER drift between transports again.

Background: `strict_mcp_config=True` (SDK) / `--tools ""` (CLI) only lock
down *external* MCP servers — Claude Code's *native* tool surface is a
separate Anthropic capability that has to be enumerated explicitly.
Tools observed leaking past those guards in live logs:
  - 2026-05-14: ``Skill``, ``run_command``/``RunCommand``, ``SlashCommand``.
  - 2026-05-26 (WhatsApp "I don't have those tools" incident): on a
    no-lazyclaw-tools turn (e.g. a bare "yes") the model fell back to its
    interactive built-in preset and emitted ``AskUserQuestion``; the brain
    then modelled its own toolset as "scheduling, planning, monitoring,
    worktree management" — exactly these built-ins — and confabulated that
    it couldn't reach the (actually-present) MCP tools.

When the model emits one of these, lazyclaw's runtime drops it as a
hallucinated tool — a wasted slot in the batch + a confused brain. The
`disallowed_tools` lever is the real fix (it's always applied), so name
every leak-prone built-in here.
"""

from __future__ import annotations

# Tuple-of-record kept as a list because both providers pass it straight
# into APIs that expect a list (``--disallowedTools *list`` for the CLI,
# ``disallowed_tools=[...]`` for the SDK). Treat as read-only.
DISALLOWED_BUILT_INS: list[str] = [
    "Bash", "Read", "Edit", "Write", "Glob", "Grep",
    "WebSearch", "WebFetch", "Task", "Agent",
    "TodoWrite", "NotebookEdit", "BashOutput", "KillShell",
    "ToolSearch",
    # Added 2026-05-14 after live leak observations:
    "Skill",                 # Claude Code's skill-runner — clashes with lazyclaw's `search_tools`
    "run_command", "RunCommand",  # Claude Code's bash equivalent
    "SlashCommand",          # Claude Code's slash-command invoker
    "Skills",                # Plural variant occasionally emitted
    # Added 2026-05-26 after the WhatsApp "I don't have those tools" incident:
    # on a no-lazyclaw-tools turn (e.g. a bare "yes") the SDK fell back to
    # its interactive built-in preset and emitted `AskUserQuestion`; the
    # brain then modelled its own toolset as "scheduling, planning,
    # monitoring, worktree management" — exactly these built-ins — and
    # confabulated that it couldn't reach the (actually-present) whatsapp
    # MCP tools. `disallowed_tools` is the real lever (it's always applied;
    # `allowed_tools` only governs auto-approval, which bypassPermissions
    # already moots), so name every leak-prone built-in here.
    "AskUserQuestion",       # interactive prompt — lazyclaw owns user Q&A
    "EnterPlanMode", "ExitPlanMode",          # "planning"
    "Monitor",                                # "monitoring"
    "EnterWorktree", "ExitWorktree",          # "worktree management"
    "CronCreate", "CronList", "CronDelete",   # "scheduling" (use schedule_job)
    "ScheduleWakeup", "RemoteTrigger", "PushNotification",
    "ListMcpResourcesTool", "ReadMcpResourceTool", "LSP",
    # Added 2026-05-29 after the "Chek my whats up + chek mail" incident:
    # under MODE_CLAUDE+SDK the brain hallucinated a `Workflow` tool_use
    # block on BOTH the foreground turn and the auto-promoted background
    # worker (log lines 3993/3996 + 4110/4113). Each was dropped as
    # hallucinated, but burned the iteration's first action. `Workflow` and
    # the Task-orchestration family (`TaskCreate`/`TaskList`/…) are Claude
    # Code's multi-agent built-ins; lazyclaw orchestrates via `delegate` /
    # `dispatch_subagents` / `run_background`, so block the whole family.
    "Workflow",
    "TaskCreate", "TaskGet", "TaskList",
    "TaskOutput", "TaskStop", "TaskUpdate",
]
