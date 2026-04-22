from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.llm.router import LLMRouter
from lazyclaw.llm.eco_router import EcoRouter, ROLE_BRAIN, ROLE_WORKER
from lazyclaw.llm.providers.base import LLMMessage, ToolCall
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import encrypt, decrypt
from lazyclaw.db.connection import db_session

from lazyclaw.runtime.callbacks import AgentEvent, CancellationToken, NullCallback
from lazyclaw.runtime.events import (
    FAST_DISPATCH, INSTANT_COMMAND,
    HELP_NEEDED, HELP_RESPONSE,
    COMPACTION_BOUNDARY,
    BROWSER_PLAN, BROWSER_ACTION, BROWSER_VERIFY, BROWSER_PROGRESS,
)
from lazyclaw.runtime.team_lead import TeamLead
from lazyclaw.runtime.stuck_detector import detect_stuck
from lazyclaw.browser.action_planner import (
    ActionPlannerState,
    PlanStatus,
    evaluate_action_result,
    make_plan_injection_prompt,
    parse_plan_from_response,
    should_inject_plan,
)
from lazyclaw.runtime.tool_executor import APPROVAL_PREFIX, ToolExecutor
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Chat-only patterns — messages that NEVER need tools.
# Everything else gets tools and the LLM decides what to use.
# Action confirmations — short messages that continue a tool-using conversation.
# "do it", "go", "run it", "yes do it", "go ahead", "start", "proceed", "continue"
_ACTION_CONFIRM_PATTERN = re.compile(
    r"^(do it|do that|go|go ahead|run it|start|proceed|continue|execute|"
    r"yes do it|yeah do it|yep do it|ok do it|sure do it|"
    r"finish it|try it|try again|retry|send it|ship it|"
    r"first one|second one|option 1|option 2|the first|the second)[\s!?.,]*$",
    re.IGNORECASE,
)

_CHAT_ONLY_PATTERN = re.compile(
    r"^(hi|hey|hello|yo|sup|thanks|thank you|thx|ok|okay|sure|"
    r"yes|no|yep|nope|good morning|good night|good evening|"
    r"bye|goodbye|see you|how are you|what's up|whats up|"
    r"nice|cool|great|awesome|lol|haha|hehe|wow|omg|"
    r"good|bad|fine|alright|sounds good|got it|understood|"
    r"perfect|exactly|right|correct|wrong|nah|meh)[\s!?.,]*$",
    re.IGNORECASE,
)

# Heartbeat daemon prefixes task-bound reminders with [TASK_REMINDER:<task_id>]
# (see lazyclaw/tasks/store.py:609).  When a turn starts with this marker we
# bind it to that task so the finally-block safety net can auto-fail the row
# if the agent exits without calling complete_task / fail_task.
_TASK_REMINDER_RE = re.compile(r"^\[TASK_REMINDER:([^\]]+)\]")


# ── Meta-Tool Pattern ─────────────────────────────────────────────────
# Instead of regex-guessing which tools the LLM needs (brittle, 5K tokens),
# send only 3-4 base tools. LLM discovers others via search_tools on demand.
# Schemas for discovered tools are injected dynamically.

# Base tools always sent — everything the brain needs to work
_BASE_TOOL_NAMES = frozenset({
    "search_tools", "web_search", "recall_memories", "save_memory", "delegate",
    "dispatch_subagents",
    "browser",
    "read_file", "write_file", "run_command", "list_directory",
    "connect_mcp_server", "disconnect_mcp_server",
    "watch_messages", "watch_site", "list_watchers", "stop_watcher",
})

# Minimal tools for local models (4B can't handle 4000+ tool tokens)
# Brain only needs delegate (dispatch workers) + search + memory
_LOCAL_TOOL_NAMES = frozenset({
    "delegate", "web_search", "recall_memories", "save_memory", "search_tools",
})

# Browser only when user explicitly asks — prevents unwanted visible browser popups
_BROWSER_KEYWORDS = frozenset({
    "browser", "open", "show me", "show it", "visible", "wallapop",
    "navigate to", "go to", "visit", "open the", "open a",
    "qr", "scan", "log in", "login", "sign in",
})

# Channel keywords → prefer MCP tools over browser
# When message matches, auto-inject matching MCP tools and drop browser
_CHANNEL_KEYWORDS: dict[str, list[str]] = {
    "whatsapp": ["whatsapp", "wa msg", "wa message"],
    "instagram": ["instagram", "ig msg", "ig message", "insta"],
    "email": ["email", "gmail", "mail", "inbox"],
}

# Channel tool suffixes to include for simple status/read queries.
# Full tool set only injected when message indicates action intent.
_CHANNEL_CORE_SUFFIXES = frozenset({
    "_status", "_setup", "_read", "_read_dms", "_read_profile",
    "_read_feed", "_list_chats", "_search", "_send", "_send_dm",
    "_reply_dm",
})

# Keywords that indicate the user wants to DO something (not just check status).
# When absent, only core channel tools are injected to reduce tool count.
# Matched with word boundaries to prevent "followers" matching "follow".
_CHANNEL_ACTION_RE = re.compile(
    r"\b(post|send|reply|follow|unfollow|like|unlike|comment|story|reel|"
    r"carousel|delete|move|mark|mute|unmute|forward|image|photo|label)\b",
    re.IGNORECASE,
)

# Task manager keywords → inject task skills directly
_TASK_KEYWORDS = frozenset({
    # Core task words
    "task", "tasks", "todo", "to-do", "to do",
    # Reminder triggers (including typos)
    "remind", "reminder", "remember", "remeber", "rember", "reminde",
    "remind me", "remember me", "don't forget", "dont forget",
    # Time-based triggers (catches "after 10 minutes", "in 30 minutes")
    " minutes", " minute", " hours", " hour",
    # Task management
    "briefing", "daily briefing", "what do i have", "my tasks",
    "overdue", "upcoming", "someday", "complete task", "done with",
    "add task", "new task", "schedule", "deadline",
    # AI tasks
    "your job", "your task", "your todo", "your todos",
    "do your todos", "do the todo", "work on your", "execute your",
    "do todo list", "work todos", "ai tasks", "agent tasks",
    # Stop/cancel
    "stop tasks", "stop background", "cancel task", "cancel all",
    "stop all", "cancel background", "stop running",
})

# Task skill names to inject when task keywords detected
_TASK_TOOL_NAMES = frozenset({
    "add_task", "list_tasks", "complete_task", "update_task",
    "delete_task", "daily_briefing", "work_todos", "stop_background",
    "set_reminder", "schedule_job", "list_jobs",
})

# Survival/job keywords → inject search_jobs + survival tools directly
_SURVIVAL_KEYWORDS = frozenset({
    "jobs", "jobspy", "freelance", "gig", "gigs",
    "find work", "find job", "search job", "apply job", "apply for",
    "survival mode", "survival status", "skills profile",
    "start gig", "submit deliverable", "invoice client",
})

# Survival skill names to inject when job keywords detected
_SURVIVAL_TOOL_NAMES = frozenset({
    "search_jobs", "apply_job", "survival_mode", "survival_status",
    "set_skills_profile", "review_deliverable",
    "start_gig", "submit_deliverable", "invoice_client",
})

# n8n workflow automation — TIGHT keywords only to avoid false positives
# "watch for" removed — overlaps with watch_messages/watch_site in _BASE_TOOL_NAMES
_N8N_KEYWORDS = frozenset({
    "n8n", "automate", "automation", "every day at", "every morning",
    "when i get", "automatically", "set up a flow",
    "recurring", "auto post", "workflow",
})

_N8N_TOOL_NAMES = frozenset({
    "n8n_status", "n8n_list_workflows", "n8n_create_workflow",
    "n8n_manage_workflow", "n8n_run_workflow", "n8n_list_executions",
    "n8n_get_workflow", "n8n_update_workflow",
    "n8n_list_credentials", "n8n_get_execution",
    "n8n_create_credential", "n8n_delete_credential",
    "n8n_google_sheets_setup", "n8n_google_oauth_setup",
    "n8n_google_services_setup",
    "n8n_test_workflow", "n8n_search_templates",
    "n8n_install_template", "n8n_list_webhooks",
    # One-shot task runner — referenced from n8n_create_workflow's
    # cheat sheet. Was missing from this bundle; without it the brain
    # would narrate `<invoke name="n8n_run_task">…</invoke>` as text
    # instead of calling the tool. (logged 2026-04-22).
    "n8n_run_task",
})

# Suffixes of the czlonkowski/n8n-mcp tools we care about when n8n
# keywords fire. The MCP bridge prefixes every tool with
# `mcp_<server_id>_`, so we match on suffix instead of full name.
# Keeping this tight (6 tools, not the full 39 exposed by n8n-mcp)
# protects the tool budget — agent already runs with ~36 tools on
# n8n turns. These six cover: discover → describe → validate → find
# example, which is the full progressive-disclosure flow.
_N8N_MCP_TOOL_SUFFIXES: tuple[str, ...] = (
    "_search_nodes",
    "_get_node",
    "_validate_node",
    "_validate_workflow",
    "_search_templates",
    "_get_template",
)
_N8N_MCP_SERVER_NAME = "n8n-nodes"

# Channel name → bundled MCP server name (for on-demand connect)
_CHANNEL_TO_MCP: dict[str, str] = {
    "whatsapp": "mcp-whatsapp",
    "instagram": "mcp-instagram",
    "email": "mcp-email",
}

# Max chars for a tool result before truncation (prevents MCP JSON blowup)
_MAX_TOOL_RESULT_CHARS = 4000


def _extract_tool_names_from_search_result(result: str) -> list[str]:
    """Extract tool names from search_tools result text (bold **name**: pattern).

    Tool names may contain hyphens (UUID-prefixed MCP tools like
    mcp_c2d0f293-ccf7-4987-a4dd-7edadc97261f_instagram_read_profile).
    """
    return re.findall(r"\*\*([\w-]+)\*\*:", result)


def _compact_history(
    history: list[LLMMessage], keep_recent: int = 4,
) -> list[LLMMessage]:
    """Keep last N user/assistant exchanges in full, compact older ones.

    Old messages become 1-line summaries. Tool messages are dropped.
    Returns new list (immutable pattern).
    """
    if not history:
        return history

    # Split into user/assistant pairs and tool messages
    # Keep system messages (summaries) as-is
    recent: list[LLMMessage] = []
    old: list[LLMMessage] = []

    # Count user/assistant messages from the end
    user_assistant_count = 0
    for msg in reversed(history):
        if msg.role in ("user", "assistant") and not msg.tool_calls:
            user_assistant_count += 1

    # Walk forward, putting old messages into compact and recent into full
    seen_recent = 0
    cutoff = max(0, user_assistant_count - keep_recent)
    ua_idx = 0

    for msg in history:
        # System messages (summaries) — always keep
        if msg.role == "system":
            recent.append(msg)
            continue

        # Tool messages from old conversations — drop entirely
        if msg.role == "tool":
            if ua_idx < cutoff:
                continue  # drop old tool results
            recent.append(msg)
            continue

        # Assistant with tool_calls from old conversations — compact
        if msg.role == "assistant" and msg.tool_calls:
            if ua_idx < cutoff:
                tools_used = ", ".join(tc.name for tc in msg.tool_calls)
                summary = f"[Used {tools_used}]"
                if msg.content:
                    summary = msg.content[:100] + f" [{tools_used}]"
                old.append(LLMMessage(role="assistant", content=summary))
                continue
            recent.append(msg)
            continue

        # User/assistant messages
        if msg.role in ("user", "assistant"):
            ua_idx += 1
            if ua_idx <= cutoff:
                # Compact old messages
                content = (msg.content or "")[:100]
                if len(msg.content or "") > 100:
                    content += "..."
                old.append(LLMMessage(role=msg.role, content=content))
            else:
                recent.append(msg)
            continue

        recent.append(msg)

    # If there are old messages, combine into a single summary
    if old:
        summary_lines = []
        for msg in old[-6:]:  # Keep last 6 compacted messages max
            prefix = "User" if msg.role == "user" else "Assistant"
            summary_lines.append(f"{prefix}: {msg.content}")
        compact_msg = LLMMessage(
            role="system",
            content="--- Earlier conversation (compacted) ---\n" + "\n".join(summary_lines),
        )
        return [compact_msg] + recent

    return recent


# Error patterns in assistant messages that should be stripped from history.
# These cause the LLM to reference past errors ("looks like there were auth
# errors earlier") instead of responding to the current message.
_HISTORY_ERROR_RE = re.compile(
    r"(Sorry, an error occurred|Error code: [45]\d{2}|"
    r"authentication_error|invalid x-api-key|"
    r"AuthenticationError|rate_limit_error)",
    re.IGNORECASE,
)


def _filter_error_messages(history: list[LLMMessage]) -> list[LLMMessage]:
    """Remove assistant messages that are just error dumps.

    Returns new list (immutable pattern).
    """
    result = []
    skip_next_user = False
    for msg in history:
        if msg.role == "assistant" and msg.content and _HISTORY_ERROR_RE.search(msg.content):
            skip_next_user = False  # Don't skip user messages
            continue  # Drop the error assistant message
        result.append(msg)
    return result


def _prune_old_tool_results(
    messages: list[LLMMessage], keep_last_n: int = 2,
) -> list[LLMMessage]:
    """Replace old tool results with 1-line summaries. Keep last N in full.

    Returns new list (immutable pattern — never mutate input).
    Keeps tool_call_id intact for OpenAI validation.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.role == "tool"]

    if len(tool_indices) <= keep_last_n:
        return messages

    to_compress = set(tool_indices[:-keep_last_n])

    result: list[LLMMessage] = []
    for i, msg in enumerate(messages):
        if i in to_compress:
            content = msg.content or ""
            summary = content[:150].replace("\n", " ").strip()
            if len(content) > 150:
                summary += "..."
            result.append(LLMMessage(
                role=msg.role,
                content=f"[Previous result truncated] {summary}",
                tool_call_id=msg.tool_call_id,
            ))
        else:
            result.append(msg)

    return result


def _cap_tool_result(result: str) -> str:
    """Truncate oversized tool results to save tokens.

    MCP tools can return huge JSON arrays (50+ contacts, full page snapshots).
    Cap at ~4K chars — enough for the LLM to understand, not enough to blow up context.
    """
    if not result or len(result) <= _MAX_TOOL_RESULT_CHARS:
        return result
    # Keep first part + tail hint
    truncated = result[:_MAX_TOOL_RESULT_CHARS]
    remaining = len(result) - _MAX_TOOL_RESULT_CHARS
    return f"{truncated}\n... [truncated {remaining} chars]"


def _strip_tool_messages(history: list[LLMMessage]) -> list[LLMMessage]:
    """Remove tool-related messages from history for tool-free conversations.

    Converts assistant messages with tool_calls to plain text,
    and drops tool role messages entirely. This prevents GPT-5 from
    hallucinating tool calls when it sees old tool patterns in history.
    """
    result = []
    for msg in history:
        if msg.role == "tool":
            continue  # Drop tool result messages
        if msg.role == "assistant" and msg.tool_calls:
            # Convert tool-calling assistant message to plain text
            parts = []
            if msg.content:
                parts.append(msg.content)
            for tc in msg.tool_calls:
                parts.append(f"[Used {tc.name}]")
            result.append(LLMMessage(
                role="assistant",
                content=" ".join(parts) if parts else "[Used tools]",
            ))
        else:
            result.append(msg)
    return result


def _wants_any_tools(message: str) -> bool:
    """Decide if tools should be available for this message.

    Inverted approach: assume tools UNLESS the message is clearly just chat.
    The LLM is smart enough to not call tools on "what's my name?" even
    if tools are available. We only block tools for pure greetings/acks.
    """
    lower = message.lower().strip()
    # Action confirmations — these continue a prior tool-using conversation.
    # MUST check before the short-message filter, because "do it" is <8 chars.
    if _ACTION_CONFIRM_PATTERN.match(lower):
        return True
    # Very short messages (under 5 chars) — greetings like "hi", "ok", "ty"
    if len(lower) < 5:
        return False
    # Known chat-only patterns — no tools needed
    if _CHAT_ONLY_PATTERN.match(lower):
        return False
    # Everything else: give the LLM tools and let it decide
    return True


# ── Fast Dispatch ─────────────────────────────────────────────────────
# Tools that trigger automatic delegation to background specialists.

# Tools that trigger background dispatch. Browser is NOT here —
# browser tasks often need user interaction (login, captcha, 0-element pages).
# They run foreground so stuck detection can fall back to the user.
HEAVY_TOOLS: frozenset[str] = frozenset({
    "run_command", "read_file", "write_file",
})

# MCP tool base names that should trigger fast dispatch when they appear
# in multi-step chains (delete, move, organize = slow IMAP operations).
_HEAVY_MCP_BASES: frozenset[str] = frozenset({
    "email_delete", "email_move", "email_mark", "email_create_label",
})


def _handle_instant_command(
    message: str, team_lead: TeamLead | None, task_runner=None,
    user_id: str | None = None,
) -> str | None:
    """Handle status and cancel commands without LLM. Returns response or None."""
    if team_lead is None:
        return None
    stripped = message.strip()
    if TeamLead.is_status_query(stripped):
        return team_lead.format_status()
    is_cancel, target = TeamLead.is_cancel_command(stripped)
    if is_cancel:
        task_id = team_lead.find_cancel_target(target)
        if task_id:
            if task_runner and user_id:
                from lazyclaw.runtime.aio_helpers import fire_and_forget

                fire_and_forget(
                    task_runner.cancel(task_id, user_id),
                    name=f"cancel-{task_id}",
                )
            team_lead.cancel(task_id)
            return f"Cancelled task matching \"{target}\""
        return f"No running task matching \"{target}\""
    return None


async def _extract_and_store_lesson(
    eco_router, config, user_id: str, message: str, recent: list,
) -> None:
    """Fire-and-forget: extract a lesson from user correction and store it.

    Never raises — all errors caught and logged. Uses gpt-5-mini for cost.
    """
    if eco_router is None:
        return
    try:
        from lazyclaw.runtime.lesson_extractor import extract_lesson
        from lazyclaw.runtime.lesson_store import store_lesson

        lesson = await extract_lesson(
            eco_router, user_id, message, recent,
        )
        if lesson:
            await store_lesson(config, user_id, lesson)
    except Exception as e:
        logger.debug("Lesson extraction background task failed: %s", e)


class _PlanRejected(Exception):
    """Raised when the user rejects a plan at the approval gate."""

    def __init__(self, reason: str | None) -> None:
        self.reason = reason
        super().__init__(reason or "plan rejected")


async def _load_auto_plan_setting(user_id: str) -> bool:
    """Read the user's auto_plan toggle. Default True (plan mode ON)."""
    try:
        from lazyclaw.config import load_config
        from lazyclaw.db.connection import db_session
        config = load_config()
        async with db_session(config) as conn:
            cur = await conn.execute(
                "SELECT auto_plan FROM users WHERE id = ?", (user_id,),
            )
            row = await cur.fetchone()
            if row is None or row[0] is None:
                return True
            # SQLite stores booleans as 0/1.
            return bool(row[0])
    except Exception:
        # Missing column on older DBs — default to ON.
        return True


class Agent:
    def __init__(
        self,
        config: Config,
        router: LLMRouter,
        registry: SkillRegistry | None = None,
        eco_router: EcoRouter | None = None,
        permission_checker=None,
        task_runner=None,
        team_lead: TeamLead | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.eco_router = eco_router or EcoRouter(config, router)
        self.registry = registry
        self.executor = (
            ToolExecutor(
                registry,
                permission_checker=permission_checker,
                timeout=config.tool_timeout,
            )
            if registry
            else None
        )
        self._task_runner = task_runner
        self._team_lead = team_lead

    async def _run_plan_gate(
        self,
        *,
        user_id: str,
        message: str,
        messages_so_far: list,
        cb,
        cancel_token,
    ) -> str:
        """Generate a user-facing plan, show it, block until approval.

        Returns the plan text (already approved). Raises `_PlanRejected`
        if the user rejects or times out.
        """
        from lazyclaw.runtime import plan_checkpoint
        from lazyclaw.runtime.taor import (
            make_user_facing_plan_prompt, parse_plan_steps,
        )
        # ROLE_BRAIN already imported at module top (line 12). Reuse it.

        # Build a cheap plan-only LLM call (no tools). We reuse the same
        # message history for context but append a plan-only instruction.
        tool_names: list[str] = []
        if self.registry is not None:
            try:
                tool_names = [
                    t.get("function", {}).get("name", "")
                    for t in self.registry.list_tools()
                ]
                tool_names = [n for n in tool_names if n][:40]
            except Exception:
                tool_names = []

        plan_instruction = make_user_facing_plan_prompt(message, tool_names)
        # LLMMessage is imported at the top of this module.
        plan_messages = list(messages_so_far) + [
            LLMMessage(role="system", content=plan_instruction),
        ]

        # Clarification-question loop. Capped at 3 rounds to prevent
        # ping-pong if the model keeps asking.
        _THINK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.DOTALL)
        max_rounds = 3
        plan_text: str = ""
        steps: list[str] = []
        for round_idx in range(max_rounds):
            logger.info(
                "Plan gate: requesting plan text from brain (round %d)",
                round_idx,
            )
            plan_resp = await self.eco_router.chat(
                plan_messages, user_id=user_id, role=ROLE_BRAIN,
            )
            plan_text = (plan_resp.content or "").strip()
            if "<think>" in plan_text:
                plan_text = _THINK_RE.sub("", plan_text).strip()
            if not plan_text:
                logger.warning("Plan gate: empty plan — skipping approval")
                return "(empty plan — auto-continuing)"

            if cancel_token.is_cancelled:
                raise _PlanRejected("cancelled before approval")

            # Clarifying-question branch.
            if plan_text.startswith("QUESTION:"):
                question = plan_text[len("QUESTION:"):].strip()
                logger.info(
                    "Plan gate: requesting clarification: %s",
                    question[:120],
                )
                await cb.on_event(AgentEvent(
                    "plan_question", question, {"question": question},
                ))
                answer = await plan_checkpoint.request_clarification(
                    user_id=user_id, question=question,
                )
                if answer is None:
                    raise _PlanRejected("question cancelled or timed out")
                logger.info(
                    "Plan gate: got answer (%d chars)", len(answer),
                )
                plan_messages.append(LLMMessage(
                    role="user",
                    content=f"(answer to your question) {answer}",
                ))
                continue

            # Normal plan branch — break out of the round loop.
            steps = parse_plan_steps(plan_text)
            break
        else:
            # Exhausted rounds without a plan.
            raise _PlanRejected(
                "too many clarification rounds — rephrase the request"
            )

        # Stream the plan to the user via the normal chat channel too,
        # so Telegram / CLI users see it even without a plan card.
        await cb.on_event(AgentEvent(
            "plan_pending", plan_text,
            {"steps": steps, "plan": plan_text},
        ))
        logger.info(
            "Plan gate: showing %d-step plan to user %s", len(steps), user_id,
        )

        decision = await plan_checkpoint.request_plan_approval(
            user_id=user_id,
            plan_text=plan_text,
            steps=steps,
        )

        if not decision.approved:
            raise _PlanRejected(decision.reason)

        await cb.on_event(AgentEvent(
            "plan_approved", "Plan approved",
            {"auto_approve_session": decision.auto_approve_session},
        ))
        logger.info(
            "Plan gate: approved (auto_approve_session=%s)",
            decision.auto_approve_session,
        )
        return plan_text

    async def process_message(
        self,
        user_id: str,
        message: str,
        chat_session_id: str | None = None,
        callback=None,
        channel_context: str | None = None,
    ) -> str:
        cb = callback or NullCallback()
        cancel_token = CancellationToken()
        if hasattr(cb, 'cancel_token'):
            cb.cancel_token = cancel_token

        logger.info("process_message START: user=%s msg=%s", user_id[:8], message[:40])
        self._nudged_tool_use = False  # Reset per-message nudge flag

        # Instant commands — no LLM call needed
        instant = _handle_instant_command(message, self._team_lead, self._task_runner, user_id)
        if instant is not None:
            await cb.on_event(AgentEvent(INSTANT_COMMAND, instant, {}))
            await cb.on_event(AgentEvent("done", "Response ready", {}))
            return instant

        key = await get_user_dek(self.config, user_id)
        _start_time = time.monotonic()
        _all_tools_used: list[str] = []

        # ── Compound task splitting ──────────────────────────────────
        # If message has multiple tasks AND we have a TaskRunner,
        # split and dispatch each to the right lane. TeamLead stays free.
        if (
            self._team_lead
            and self._task_runner
            and not getattr(self, "is_background", False)
            and _wants_any_tools(message)
        ):
            from lazyclaw.runtime.task_splitter import split_tasks, _looks_compound

            if _looks_compound(message):
                logger.info("COMPOUND detected for: %s", message[:60])
                sub_tasks = await split_tasks(
                    self.eco_router, user_id, message,
                )
                logger.info("COMPOUND split result: %d tasks — %s",
                            len(sub_tasks), [(s.name, s.lane) for s in sub_tasks])
                if len(sub_tasks) > 1:
                    has_bg = any(st.lane == "background" for st in sub_tasks)
                    if has_bg and self._task_runner:
                        # Mix of foreground/background — dispatch all to task_runner
                        dispatched: list[str] = []
                        for st in sub_tasks:
                            await self._task_runner.submit(
                                user_id=user_id,
                                instruction=st.instruction,
                                name=st.name,
                                callback=callback,
                            )
                            dispatched.append(f"{st.name} ({st.lane})")

                        status_msg = (
                            f"On it — split into {len(sub_tasks)} tasks:\n"
                            + "\n".join(f"  • {d}" for d in dispatched)
                        )

                        await cb.on_event(AgentEvent(
                            FAST_DISPATCH, status_msg,
                            {"tasks": len(sub_tasks), "names": dispatched},
                        ))
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                        await cb.on_event(AgentEvent("done", "Dispatched", {}))
                        return status_msg
                    # All foreground — run sequentially inline, don't background
                    # (fall through to normal processing with the full original message)

        # Bind turn to a task if message carries the heartbeat reminder prefix.
        # Only the daemon's task-linked reminder path (tasks/store.py:609) emits
        # `[TASK_REMINDER:<task_id>]`; plain `[REMINDER]` has no id and stays
        # unbound.  Populated here, read by the safety net in the finally block.
        _bound_task_id: str | None = None
        _m = _TASK_REMINDER_RE.match(message or "")
        if _m:
            _bound_task_id = _m.group(1).strip() or None

        # Register foreground task with TeamLead (skip for background agents)
        _fg_task_id: str | None = None
        if self._team_lead and not getattr(self, "is_background", False):
            _fg_task_id = str(uuid4())
            self._team_lead.register(
                _fg_task_id, "chat", message[:80], "foreground",
                instruction_full=message,
                cancel_token=cancel_token,
                user_id=user_id,
            )
        _session_tokens = 0
        # Last ECO fallback tag seen this turn (e.g. "overloaded" → Haiku via
        # Claude CLI). Surfaces in the final "done" event so channels can show
        # a "fallback → X" chip instead of silently swapping the model.
        _last_fallback_reason: str | None = None
        _last_model_used: str | None = None

        # Initialize trace recorder
        from lazyclaw.replay.recorder import TraceRecorder
        recorder = TraceRecorder(self.config, user_id)
        logger.debug("process_message TRACE: recording user message...")
        await recorder.record_user_message(message)
        logger.debug("process_message TRACE: recorded OK")

        import asyncio as _aio

        from lazyclaw.memory.compressor import compress_history
        from lazyclaw.skills.manager import load_user_skills
        from lazyclaw.runtime.context_builder import build_context
        from lazyclaw.runtime.personality import load_personality

        # Check if using local model (needed for tool count optimization)
        _is_local_model = False
        try:
            from lazyclaw.llm.eco_settings import get_eco_settings as _get_eco
            from lazyclaw.llm.model_registry import get_model, get_mode_models
            logger.debug("process_message TRACE: loading eco settings...")
            _eco = await _get_eco(self.config, user_id)
            _mode = _eco.get("mode", "hybrid")
            # Check if the resolved worker model is local
            _defaults = get_mode_models(_mode)
            _worker_id = _eco.get(f"{_mode}_worker_model") or _eco.get("worker_model") or _defaults.get("worker", "")
            _brain_id = _eco.get(f"{_mode}_brain_model") or _eco.get("brain_model") or _defaults.get("brain", "")
            _worker_profile = get_model(_worker_id)
            _brain_profile = get_model(_brain_id)
            _is_local_model = bool(
                (_worker_profile and _worker_profile.is_local)
                or (_brain_profile and _brain_profile.is_local)
            )
        except Exception:
            logger.debug("Failed to load eco settings for local model check", exc_info=True)

        # Decide upfront: does this message need tools?
        needs_tools_early = self.registry is not None and _wants_any_tools(message)

        async def _load_history():
            async with db_session(self.config) as db:
                if chat_session_id:
                    rows = await db.execute(
                        "SELECT id, role, content, tool_name, metadata FROM agent_messages "
                        "WHERE user_id = ? AND chat_session_id = ? ORDER BY created_at ASC",
                        (user_id, chat_session_id),
                    )
                else:
                    rows = await db.execute(
                        "SELECT id, role, content, tool_name, metadata FROM agent_messages "
                        "WHERE user_id = ? ORDER BY created_at ASC",
                        (user_id,),
                    )
                return await rows.fetchall()

        logger.debug("process_message TRACE: needs_tools_early=%s", needs_tools_early)
        if needs_tools_early:
            # Full parallel init — load history, skills, and rich context
            # Run sequentially with traces to find which one hangs
            logger.info("process_message TRACE: loading history...")
            history_rows = await _load_history()
            logger.info("process_message TRACE: history loaded, loading skills...")
            await load_user_skills(self.config, user_id, self.registry)
            logger.info("process_message TRACE: skills loaded, building context...")
            try:
                system_prompt = await asyncio.wait_for(
                    build_context(
                        self.config,
                        user_id,
                        registry=self.registry,
                        user_message=message,
                    ),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                logger.warning("build_context timed out (>15s) — using personality only")
                system_prompt = load_personality()
            logger.info("process_message TRACE: context built")
        else:
            # Fast chat path — minimal system prompt, skip skills + MCP + memories
            _is_greeting = _CHAT_ONLY_PATTERN.match(message.strip().lower().rstrip("!?."))
            if _is_greeting and _is_local_model:
                # Tiny prompt for greetings on local models
                system_prompt = "You are LazyClaw, a helpful AI assistant. Be friendly and concise."
            else:
                system_prompt = load_personality()  # Full SOUL.md, cached
            logger.debug("process_message TRACE: loading history...")
            history_rows = await _load_history()
            logger.debug("process_message TRACE: history loaded")

        logger.info("process_message TRACE: compressing history...")
        history = await compress_history(
            self.config, self.eco_router, user_id, chat_session_id,
            raw_messages=history_rows,
        )

        logger.info("process_message TRACE: history compressed (%d messages)", len(history))

        # Register delegate skill — lets the agent dispatch to specialists
        # inline (NanoClaw pattern: no separate team lead LLM call)
        _delegate_registered = False
        _dispatch_registered = False
        if self.registry is not None:
            from lazyclaw.skills.builtin.delegate import DelegateSkill

            delegate_skill = DelegateSkill(
                config=self.config,
                registry=self.registry,
                eco_router=self.eco_router,
                permission_checker=self.executor._checker if self.executor else None,
                callback=cb,
            )
            self.registry.register(delegate_skill)
            _delegate_registered = True

            from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill

            dispatch_skill = DispatchSubagentsSkill(
                config=self.config,
                registry=self.registry,
                eco_router=self.eco_router,
                permission_checker=self.executor._checker if self.executor else None,
            )
            self.registry.register(dispatch_skill)
            _dispatch_registered = True

        # Initialize channel state (used by tool nudge later, must exist for all paths)
        _matched_channels: list[str] = []

        # Meta-tool pattern: send only base tools (search_tools, memory, delegate).
        # LLM discovers other tools on demand via search_tools → schemas injected dynamically.
        # Channel detection: if message mentions whatsapp/instagram/email, prefer MCP tools over browser.
        needs_tools = self.registry is not None and _wants_any_tools(message)
        tools: list = []

        if needs_tools:
            from lazyclaw.mcp.manager import _favorite_server_ids, _active_clients

            # Detect channel keywords → find matching MCP tools.
            # Skip channel injection for planning/strategy questions where
            # "email" / "instagram" are topics, not tool-use targets.
            _msg_lower = message.lower()
            _planning_words = {"how to", "strategy", "plan", "market", "reach",
                               "steps", "approach", "idea", "advice", "suggest"}
            _is_planning = any(pw in _msg_lower for pw in _planning_words)
            _matched_channels: list[str] = []
            if not _is_planning:
                for channel, keywords in _CHANNEL_KEYWORDS.items():
                    if any(kw in _msg_lower for kw in keywords):
                        _matched_channels.append(channel)

            # Re-inject channel tools if the LAST assistant response used them
            # AND the user's message looks like a continuation (reply words, short msg).
            # Without the continuation check, one-shot tool calls (e.g. mute from
            # a notification reply) make channel tools "sticky" for all future messages.
            if not _matched_channels:
                _continuation_signals = {
                    "reply", "tell", "say", "send", "forward", "yes", "no",
                    "ok", "read", "check", "show", "next", "more",
                }
                _words = set(_msg_lower.split())
                _looks_like_continuation = bool(_words & _continuation_signals) and len(_msg_lower) < 40
                if _looks_like_continuation:
                    for msg in history[-2:]:  # Only last 2 messages (immediate context)
                        if msg.role == "assistant" and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tname = tc.name.lower()
                                for channel in _CHANNEL_KEYWORDS:
                                    if channel in tname and channel not in _matched_channels:
                                        _matched_channels.append(channel)
                    if _matched_channels:
                        logger.info("Channel tools re-injected (conversation continuity): %s", _matched_channels)

            # Find MCP tools for matched channels.
            # Simple queries (status, read) get only core tools to reduce
            # tool count (51→~25). Action queries get the full set.
            _channel_tools: list = []
            _wants_action = bool(_CHANNEL_ACTION_RE.search(_msg_lower))
            if _matched_channels:
                for tool_info in self.registry.list_mcp_tools():
                    func = tool_info.get("function", {})
                    tname = func.get("name", "").lower()
                    tdesc = func.get("description", "").lower()
                    for ch in _matched_channels:
                        if ch in tname or ch in tdesc:
                            # Filter: only core suffixes unless action intent
                            if not _wants_action:
                                _base_name = tname.rsplit("_", 1)[-1] if "_" in tname else tname
                                _has_core = any(tname.endswith(s) for s in _CHANNEL_CORE_SUFFIXES)
                                if not _has_core:
                                    break
                            schema = self.registry.get_tool_schema(func.get("name", ""))
                            if schema is not None:
                                _channel_tools.append(schema)
                            break

                # On-demand connect: if channel detected but no MCP tools found,
                # the optional MCP server was never cached. Connect it now.
                if not _channel_tools:
                    logger.info(
                        "No MCP tools found for channels %s — attempting on-demand connect",
                        _matched_channels,
                    )
                    try:
                        from lazyclaw.mcp.manager import (
                            connect_server, get_server_id_by_name,
                        )
                        from lazyclaw.mcp.bridge import (
                            cache_tool_schemas, register_mcp_tools,
                        )

                        for ch in _matched_channels:
                            mcp_name = _CHANNEL_TO_MCP.get(ch)
                            if not mcp_name:
                                continue
                            try:
                                sid = await get_server_id_by_name(
                                    self.config, user_id, mcp_name,
                                )
                                if not sid:
                                    logger.info("No MCP server %s registered for user", mcp_name)
                                    continue
                                logger.info("On-demand connecting %s (id=%s)...", mcp_name, sid[:8])
                                client = await asyncio.wait_for(
                                    connect_server(self.config, user_id, sid),
                                    timeout=15,
                                )
                                tools_list = await client.list_tools()
                                await cache_tool_schemas(self.config, mcp_name, tools_list)
                                count = await register_mcp_tools(
                                    client, self.registry,
                                    config=self.config, user_id=user_id,
                                )
                                logger.info(
                                    "On-demand connected %s: %d tools registered",
                                    mcp_name, count,
                                )
                                # Re-scan for channel tools after registration
                                for tool_info in self.registry.list_mcp_tools():
                                    func = tool_info.get("function", {})
                                    tname = func.get("name", "").lower()
                                    tdesc = func.get("description", "").lower()
                                    if ch in tname or ch in tdesc:
                                        schema = self.registry.get_tool_schema(
                                            func.get("name", ""),
                                        )
                                        if schema is not None:
                                            _channel_tools.append(schema)
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "Timeout connecting %s on-demand (>15s)", mcp_name,
                                )
                            except Exception:
                                # Retry once — MCP SDK has a cancel scope race
                                # that fails intermittently on first connect
                                logger.info(
                                    "First connect for %s failed, retrying...",
                                    mcp_name,
                                )
                                try:
                                    await asyncio.sleep(0.5)
                                    client = await asyncio.wait_for(
                                        connect_server(self.config, user_id, sid),
                                        timeout=15,
                                    )
                                    tools_list = await client.list_tools()
                                    await cache_tool_schemas(self.config, mcp_name, tools_list)
                                    count = await register_mcp_tools(
                                        client, self.registry,
                                        config=self.config, user_id=user_id,
                                    )
                                    logger.info(
                                        "On-demand connected %s on retry: %d tools",
                                        mcp_name, count,
                                    )
                                    for tool_info in self.registry.list_mcp_tools():
                                        func = tool_info.get("function", {})
                                        tname = func.get("name", "").lower()
                                        tdesc = func.get("description", "").lower()
                                        if ch in tname or ch in tdesc:
                                            schema = self.registry.get_tool_schema(
                                                func.get("name", ""),
                                            )
                                            if schema is not None:
                                                _channel_tools.append(schema)
                                except Exception:
                                    logger.warning(
                                        "Failed on-demand connect for %s (after retry)",
                                        mcp_name, exc_info=True,
                                    )
                    except Exception:
                        logger.warning(
                            "On-demand MCP connect failed (import or setup)",
                            exc_info=True,
                        )

            # Context carry-forward: if recent history used specific tools,
            # re-inject them so follow-up messages ("apply for it", "click that")
            # have access to the same tools without needing keywords again.
            _history_tool_names: set[str] = set()
            for msg in history[-8:]:  # Scan last 8 messages
                if msg.role == "assistant" and msg.tool_calls:
                    for tc in msg.tool_calls:
                        _history_tool_names.add(tc.name)

            # Task manager keyword detection → inject task tools
            _task_tools_extra: list = []
            _wants_tasks = any(kw in _msg_lower for kw in _TASK_KEYWORDS)
            if not _wants_tasks and _history_tool_names & _TASK_TOOL_NAMES:
                _wants_tasks = True
                logger.info("Task tools re-injected from recent history context")
            if _wants_tasks:
                for tname in _TASK_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(tname)
                    if schema is not None:
                        _task_tools_extra.append(schema)
                if _task_tools_extra:
                    logger.info(
                        "Task keywords detected — %d task tools injected",
                        len(_task_tools_extra),
                    )

            # Survival/job keyword detection → inject survival tools
            _survival_tools: list = []
            _wants_survival = any(kw in _msg_lower for kw in _SURVIVAL_KEYWORDS)
            # Also trigger if recent history used survival tools
            if not _wants_survival and _history_tool_names & _SURVIVAL_TOOL_NAMES:
                _wants_survival = True
                logger.info("Survival tools re-injected from recent history context")
            if _wants_survival:
                for sname in _SURVIVAL_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(sname)
                    if schema is not None:
                        _survival_tools.append(schema)
                if _survival_tools:
                    logger.info(
                        "Job/survival keywords detected — %d survival tools injected",
                        len(_survival_tools),
                    )

            # n8n automation keyword detection → inject n8n tools
            _n8n_tools: list = []
            _wants_n8n = any(kw in _msg_lower for kw in _N8N_KEYWORDS)
            if not _wants_n8n and _history_tool_names & _N8N_TOOL_NAMES:
                _wants_n8n = True
                logger.info("n8n tools re-injected from recent history context")
            if _wants_n8n:
                for nname in _N8N_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(nname)
                    if schema is not None:
                        _n8n_tools.append(schema)
                # Also inject n8n-nodes MCP tools (search_nodes / get_node
                # / validate_workflow / ...) so the brain can discover
                # node schemas for ANY of the ~1,505 node types instead
                # of guessing from memory. Matched by tool-name suffix
                # because the MCP bridge prefixes with `mcp_<uuid>_`.
                _n8n_mcp_attached = 0
                for tool_info in self.registry.list_mcp_tools():
                    func = tool_info.get("function", {})
                    tname = func.get("name", "")
                    tdesc = func.get("description", "").lower()
                    if not any(tname.endswith(s) for s in _N8N_MCP_TOOL_SUFFIXES):
                        continue
                    # Tighten: only from the n8n-nodes MCP server (not
                    # from e.g. another MCP that happens to expose a
                    # `get_node` tool).
                    if _N8N_MCP_SERVER_NAME not in tdesc:
                        continue
                    schema = self.registry.get_tool_schema(tname)
                    if schema is not None:
                        _n8n_tools.append(schema)
                        _n8n_mcp_attached += 1
                # On-demand connect the n8n-nodes MCP server if nothing
                # was found but the user asked for n8n work.
                if _n8n_mcp_attached == 0:
                    try:
                        from lazyclaw.mcp.manager import (
                            connect_server, get_server_id_by_name,
                        )
                        from lazyclaw.mcp.bridge import (
                            cache_tool_schemas, register_mcp_tools,
                        )
                        sid = await get_server_id_by_name(
                            self.config, user_id, _N8N_MCP_SERVER_NAME,
                        )
                        if sid:
                            logger.info(
                                "On-demand connecting %s (id=%s)…",
                                _N8N_MCP_SERVER_NAME, sid[:8],
                            )
                            client = await asyncio.wait_for(
                                connect_server(self.config, user_id, sid),
                                timeout=20,
                            )
                            tools_list = await client.list_tools()
                            await cache_tool_schemas(
                                self.config, _N8N_MCP_SERVER_NAME, tools_list,
                            )
                            await register_mcp_tools(
                                client, self.registry,
                                config=self.config, user_id=user_id,
                            )
                            # Re-scan after registration.
                            for tool_info in self.registry.list_mcp_tools():
                                func = tool_info.get("function", {})
                                tname = func.get("name", "")
                                tdesc = func.get("description", "").lower()
                                if not any(
                                    tname.endswith(s)
                                    for s in _N8N_MCP_TOOL_SUFFIXES
                                ):
                                    continue
                                if _N8N_MCP_SERVER_NAME not in tdesc:
                                    continue
                                schema = self.registry.get_tool_schema(tname)
                                if schema is not None:
                                    _n8n_tools.append(schema)
                                    _n8n_mcp_attached += 1
                    except Exception:
                        # Don't fail the whole n8n turn if the catalog
                        # MCP can't start — the native n8n_* skills still
                        # work, brain just loses the describe-before-emit
                        # primitive.
                        logger.debug(
                            "n8n-nodes MCP on-demand connect failed",
                            exc_info=True,
                        )
                if _n8n_tools:
                    logger.info(
                        "n8n/automation keywords detected — %d n8n tools injected (of which %d MCP node-catalog)",
                        len(_n8n_tools), _n8n_mcp_attached,
                    )
                # n8n wins over raw channel MCP tools — suppress channel injection
                # to avoid ambiguous tool sets (e.g. "automate email notifications"
                # matching both email MCP and n8n).
                if _matched_channels:
                    logger.info(
                        "n8n detected with channel keywords %s — suppressing channel injection",
                        _matched_channels,
                    )
                    _matched_channels = []
                    _channel_tools = []

            # Build base tools + conditionally add browser.
            # Smart tool selection: same for all models. LLM discovers extras via search_tools().
            # When channel MCP tools dominate, trim base set to reduce noise.
            _base_names = set(_BASE_TOOL_NAMES)
            if _channel_tools and not _wants_tasks and not _wants_survival:
                # Drop heavy tools irrelevant for messaging queries
                _base_names -= {
                    "run_command", "write_file", "read_file", "list_directory",
                    "watch_site", "connect_mcp_server", "disconnect_mcp_server",
                }
                logger.info("Channel-focused: trimmed base tools to %d", len(_base_names))

            # Browser only when user explicitly asks (keyword match).
            # No re-injection from history — if the user has moved on, the LLM
            # should not reach for the browser on its own.
            _wants_browser = any(kw in _msg_lower for kw in _BROWSER_KEYWORDS)
            _wants_visible = any(kw in _msg_lower for kw in (
                "visible", "show me", "show it", "let me see", "make visible",
            ))
            # Browser suppressed ONLY when channel MCP tools handle the request
            # (WhatsApp/Instagram/Email have dedicated MCP tools — no browser needed).
            # Task and survival tools should NEVER suppress browser — user can
            # browse the web AND have task/job tools available simultaneously.
            if _wants_browser and not _channel_tools:
                _base_names.add("browser")
                logger.info("Browser keyword detected — browser tool included")
            elif _wants_browser and _channel_tools:
                logger.info("Channel detected: %s → %d MCP tools, browser suppressed (MCP-first)", _matched_channels, len(_channel_tools))
            elif _channel_tools:
                logger.info("Channel detected: %s → %d MCP tools, no browser", _matched_channels, len(_channel_tools))

            tools = [
                schema for name in _base_names
                if (schema := self.registry.get_tool_schema(name)) is not None
            ]

            tools.extend(_channel_tools)
            # Add task manager tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for tt in _task_tools_extra:
                if tt.get("function", {}).get("name") not in _existing_names:
                    tools.append(tt)

            # Add survival tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for st in _survival_tools:
                if st.get("function", {}).get("name") not in _existing_names:
                    tools.append(st)

            # Add n8n tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for nt in _n8n_tools:
                if nt.get("function", {}).get("name") not in _existing_names:
                    tools.append(nt)

            # Include favorite MCP tools ONLY when channel keywords matched
            # (not on every message — the meta-tool pattern uses search_tools for discovery)
            _fav_prefixes = tuple(
                f"mcp_{sid}_" for sid in _favorite_server_ids
                if sid in _active_clients
            )
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            if _fav_prefixes and _matched_channels:
                for tool_info in self.registry.list_mcp_tools():
                    func = tool_info.get("function", {})
                    tname = func.get("name", "")
                    if tname.startswith(_fav_prefixes) and tname not in _existing_names:
                        schema = self.registry.get_tool_schema(tname)
                        if schema is not None:
                            tools.append(schema)
            logger.info("%s mode: %d tools for: %s", "LOCAL" if _is_local_model else "META", len(tools), message[:50])
            if tools:
                logger.info("Tool names sent: %s", [t.get("function", {}).get("name") for t in tools])
            else:
                logger.warning("ZERO tools resolved from base_names=%s — registry may be empty", _base_names)
        else:
            logger.info("No tools — fast chat path for: %s", message[:50])

        # Build context — keep recent messages, compact old ones.
        # Filter error messages so LLM doesn't reference past failures.
        _clean_history = _filter_error_messages(history)
        if needs_tools:
            chat_history = _compact_history(_clean_history, keep_recent=4)
        else:
            chat_history = _strip_tool_messages(_clean_history)
            if len(chat_history) > 6:
                chat_history = chat_history[-6:]

        # Message order optimized for prompt caching:
        # 1. System (STATIC — cached), 2. Channel (semi-static),
        # 3. History (dynamic), 4. User message (dynamic)
        system_messages = [LLMMessage(role="system", content=system_prompt)]
        if channel_context:
            system_messages.append(LLMMessage(role="system", content=channel_context))
        messages: list[LLMMessage] = (
            system_messages
            + chat_history
            + [LLMMessage(role="user", content=message)]
        )

        # If user wants visible browser, prepend instruction to user message
        # so LLM calls show action before navigating
        if needs_tools and _wants_visible:
            _vis_prefix = (
                "[IMPORTANT: Browser runs HEADLESS (invisible). "
                "You MUST call browser(action='show') FIRST before any other browser action. "
                "Then navigate.]\n\n"
            )
            # Modify the last user message in-place
            _last_msg = messages[-1]
            if _last_msg.role == "user":
                messages[-1] = LLMMessage(
                    role="user", content=_vis_prefix + _last_msg.content,
                )

        logger.info("Context: %d messages (%d history + system + user), tools=%d",
                     len(messages), len(chat_history), len(tools))

        # ── Learn from corrections (fire-and-forget) ─────────────
        # If the user is correcting the previous response, extract a
        # compact lesson and save it to memory for future sessions.
        if len(history) >= 2:
            from lazyclaw.runtime.lesson_extractor import is_correction

            prev_assistant = next(
                (m for m in reversed(history) if m.role == "assistant"),
                None,
            )
            if prev_assistant and is_correction(message):
                _aio.create_task(
                    _extract_and_store_lesson(
                        self.eco_router, self.config, user_id,
                        message, history[-4:],
                    )
                )

        # ── TAOR: Three-Phase loop setup (Plan → Execute → Verify) ───────
        # Pure functions imported here to keep top-level imports clean.
        from lazyclaw.runtime.taor import (
            detect_effort, make_plan_prompt, verify_response, EffortLevel,
            make_user_facing_plan_prompt, parse_plan_steps,
            has_plan_bypass_phrase,
        )
        _effort = detect_effort(message, has_tools=needs_tools)
        # Number of verify+correction passes after the execute phase.
        # LOW → 0 (skip entirely), MEDIUM → 1, HIGH → 2, MAX → 3.
        _taor_verify_passes = {
            EffortLevel.LOW: 0,
            EffortLevel.MEDIUM: 1,
            EffortLevel.HIGH: 2,
            EffortLevel.MAX: 3,
        }[_effort]
        _taor_retry_context: str | None = None  # Set on verify failure for replan
        logger.info("TAOR: effort=%s verify_passes=%d", _effort, _taor_verify_passes)

        # ── Plan-Mode approval gate (Claude-Code-style) ────────────────────
        # For MEDIUM+ tasks, show the user the plan and block on approval
        # before any tool calls. Three bypasses:
        #   (a) per-user auto_plan setting disabled → skip entirely
        #   (b) per-turn phrase ("just do it", "go ahead", etc.) → skip
        #   (c) session-level trust via plan_checkpoint → auto-approve
        _plan_mode_used: bool = False
        _plan_text_approved: str | None = None
        _auto_plan_enabled = await _load_auto_plan_setting(user_id)
        _bypassed_by_phrase = has_plan_bypass_phrase(message)
        if (
            _effort != EffortLevel.LOW
            and needs_tools
            and _auto_plan_enabled
            and not _bypassed_by_phrase
        ):
            try:
                _plan_text_approved = await self._run_plan_gate(
                    user_id=user_id,
                    message=message,
                    messages_so_far=messages,
                    cb=cb,
                    cancel_token=cancel_token,
                )
                _plan_mode_used = True
            except _PlanRejected as pr:
                logger.info("Plan rejected for user %s: %s", user_id, pr.reason)
                await cb.on_event(AgentEvent(
                    "done", "Plan rejected", {"reason": pr.reason or ""}
                ))
                return (
                    f"Plan rejected: {pr.reason or 'no reason given'}. "
                    "Tell me how you'd like to proceed."
                )

        # Agentic loop — brain decides when to stop, safety cap prevents runaway
        max_iterations = self.config.max_tool_iterations
        _nudge_at = int(max_iterations * 0.8)  # Nudge LLM at 80% of cap
        _nudged = False
        all_new_messages: list[LLMMessage] = [LLMMessage(role="user", content=message)]
        _tool_call_history: list[str] = []  # Track tool names for loop detection
        _tool_results: list[str] = []  # Track results for error detection
        _escalated = False  # True after auto-escalation to brain_model
        _escalation_iter = 0  # Iteration when escalation happened
        _tool_call_cache: dict[str, str] = {}  # (name, args_hash) → result
        _denied_approvals: set[str] = set()  # "skill_name:args_hash" — user-denied this turn
        # Graduated escalation level: 0=normal, 1=soft nudge, 2=brain escalation
        # Level 3 (give up) triggers when stuck is detected at level 2.
        _escalation_level: int = 0
        # Counts stuck signals that fired AFTER the L2 brain escalation. The
        # 1st gets the existing HELP_NEEDED dialog (user can rescue);
        # the 2nd hard-stops the loop with a terminal message instead of
        # popping a second dialog. Without this, a stubborn brain (e.g.
        # MiniMax M2.7) can flail through 10+ extra iterations after L2 has
        # already conceded — see hirossa.com postmortem 2026-04-19.
        _post_l2_stuck_count: int = 0
        # Browser action planner — ephemeral per-conversation, not persisted
        _plan_state = ActionPlannerState(
            plan_injected=not should_inject_plan(message, []),
        )
        # Research strategy — tracks requirements + sources for research tasks
        from lazyclaw.browser.research_strategy import (
            ResearchStrategy, ResearchStatus,
            is_research_task, make_requirements_prompt,
            parse_requirements_from_response, note_source,
            make_progress_message as _make_research_progress,
        )
        _research_state: ResearchStrategy | None = (
            ResearchStrategy(
                query=message,
                info_requirements=(),
                sources_checked=(),
                gaps=(),
            ) if is_research_task(message) else None
        )
        _research_prompt_injected = False

        response = None
        iteration = 0
        try:
            for iteration in range(max_iterations):
                if cancel_token.is_cancelled:
                    if self._team_lead and _fg_task_id:
                        self._team_lead.cancel(_fg_task_id)
                    if _delegate_registered and self.registry:
                        self.registry.unregister("delegate")
                    await cb.on_event(AgentEvent("done", "Cancelled", {}))
                    return "Operation cancelled."

                # ── Drain side-channel notes from the user (WebSocket) ────
                # If the user typed something while we were working, inject
                # it as a system message so the agent can pivot.
                try:
                    _popper = getattr(cb, "pop_side_notes", None)
                    if callable(_popper):
                        _notes = _popper()
                        for _note in _notes:
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    f"[User side-note mid-task]: {_note}\n"
                                    "Acknowledge briefly and incorporate into "
                                    "your current work if relevant."
                                ),
                            ))
                            await cb.on_event(AgentEvent(
                                "side_note_ack",
                                f"Got it: {_note[:60]}",
                                {"note": _note},
                            ))
                except Exception:
                    pass

                # TAOR phase: entering "think" — LLM planning / reasoning.
                try:
                    await cb.on_event(AgentEvent(
                        "phase", "think",
                        {"phase": "think", "iteration": iteration},
                    ))
                    if self._team_lead and _fg_task_id:
                        self._team_lead.update_phase(_fg_task_id, "think")
                except Exception:
                    pass

                # Prune old tool results to save tokens
                # Early iterations: keep 2 full results for context
                # Later iterations: keep only 1 (agent already has the gist)
                if iteration > 0:
                    keep_n = 1 if iteration >= 3 else 2
                    messages = _prune_old_tool_results(messages, keep_last_n=keep_n)

                # ── Token-based context compaction ────────────────────
                # Check estimated token count before every LLM call.
                # If above 80% of the model's context limit, summarise
                # older turns with a fast model to free headroom.
                if needs_tools:
                    from lazyclaw.memory.context_compactor import (
                        estimate_tokens,
                        get_context_limit,
                        compact_messages as _compact_messages,
                        COMPACTION_THRESHOLD,
                    )
                    _model_name = (
                        self.eco_router.last_routing.model
                        if self.eco_router and self.eco_router.last_routing
                        else None
                    )
                    _ctx_limit = get_context_limit(_model_name)
                    _est_tokens = estimate_tokens(messages, tools)
                    if _est_tokens > int(_ctx_limit * COMPACTION_THRESHOLD):
                        _compact_result = await _compact_messages(
                            self.eco_router, self.config, user_id,
                            messages,
                            tools=tools,
                            current_turn=iteration,
                        )
                        if _compact_result.did_compact:
                            messages = list(_compact_result.messages)
                            await cb.on_event(AgentEvent(
                                COMPACTION_BOUNDARY,
                                (
                                    f"Context compacted at iteration {iteration}: "
                                    f"{_compact_result.before_tokens:,} → "
                                    f"{_compact_result.after_tokens:,} tokens "
                                    f"({_compact_result.turns_compacted} older turns summarised)"
                                ),
                                {
                                    "iteration": iteration,
                                    "before_tokens": _compact_result.before_tokens,
                                    "after_tokens": _compact_result.after_tokens,
                                    "context_limit": _ctx_limit,
                                    "turns_compacted": _compact_result.turns_compacted,
                                },
                            ))

                # ── Browser action planner injection ──────────────────
                # At iteration 0, if this looks like a browser task, ask
                # the brain LLM to output a plan JSON before acting.
                # No extra LLM call — plan + first action in one response.
                if iteration == 0 and not _plan_state.plan_injected and tools:
                    _plan_prompt = make_plan_injection_prompt(message)
                    messages.append(LLMMessage(role="system", content=_plan_prompt))
                    from dataclasses import replace as _dc_replace
                    _plan_state = _dc_replace(_plan_state, plan_injected=True)
                    logger.info("Browser action planner: injected planning prompt")

                # ── Research strategy requirements injection ──────────────
                # At iteration 0, if this is a research task, ask the LLM to
                # list what information it needs before starting to browse.
                if (
                    iteration == 0
                    and not _research_prompt_injected
                    and _research_state is not None
                    and tools
                ):
                    _research_prompt = make_requirements_prompt(_research_state.query)
                    messages.append(LLMMessage(role="system", content=_research_prompt))
                    _research_prompt_injected = True
                    logger.info("Research strategy: injected requirements prompt")

                # ── TAOR Phase 1: PLAN ────────────────────────────────────
                # For non-LOW effort tasks, inject a Claude-optimized XML
                # planning prompt so the model decomposes the task and
                # identifies required tools before taking action.
                # Leverages Claude's strong XML structured reasoning.
                # On verify-retry, _taor_retry_context carries failure context
                # so the model adjusts its approach.
                if (
                    iteration == 0
                    and _effort != EffortLevel.LOW
                    and tools
                    and needs_tools
                ):
                    # Plan-mode override: if the user already approved a
                    # user-facing plan above, feed THAT into the loop
                    # instead of asking the model to re-plan. Keeps the
                    # approved intent intact and saves an LLM call.
                    if _plan_mode_used and _plan_text_approved:
                        messages.append(LLMMessage(
                            role="system",
                            content=(
                                "The user has REVIEWED AND APPROVED this plan. "
                                "Execute it step by step — do NOT re-plan, do "
                                "NOT ask for confirmation again.\n\n"
                                f"{_plan_text_approved}"
                            ),
                        ))
                        logger.info(
                            "TAOR Plan: using user-approved plan (no re-plan)",
                        )
                    else:
                        _taor_plan = make_plan_prompt(
                            message, _effort, _taor_retry_context,
                        )
                        messages.append(LLMMessage(
                            role="system", content=_taor_plan,
                        ))
                        logger.info(
                            "TAOR Plan phase injected (effort=%s, retry=%s)",
                            _effort, _taor_retry_context is not None,
                        )

                kwargs: dict = {}
                if tools:
                    kwargs["tools"] = tools
                logger.info("AGENTIC LOOP iter=%d: tools=%d, messages=%d",
                            iteration, len(tools), len(messages))

                # Role routing: brain for strategy + final answers,
                # worker for mid-chain tool orchestration (cheaper/local).
                #
                # iteration 0: brain picks strategy + first tools
                # iteration 1+: worker handles tool chains (just orchestrating)
                # escalated: brain takes back over for quality
                # last iteration hint: if previous response had tool calls and
                #   this iteration might be the final answer, use brain.
                iter_model = None  # Let eco_router decide based on mode + role
                if iteration == 0:
                    _iter_role = ROLE_BRAIN      # First call: brain picks strategy + tools
                elif _escalated:
                    _iter_role = ROLE_BRAIN      # After escalation: brain takes over
                else:
                    _iter_role = ROLE_WORKER     # Mid-chain: worker orchestrates tools

                model_name = "brain"
                # Show actual routing model if available
                if self.eco_router and self.eco_router.last_routing:
                    model_name = self.eco_router.last_routing.display_name
                logger.info("Iteration %d: calling %s", iteration + 1, model_name)
                await cb.on_event(AgentEvent(
                    "llm_call",
                    f"Thinking ({model_name})...",
                    {"iteration": iteration + 1, "model": model_name},
                ))
                await recorder.record_llm_call(
                    model=None, message_count=len(messages), has_tools=bool(tools),
                )

                from lazyclaw.llm.providers.base import LLMResponse as _LLMResp

                streamed_content = ""
                response = None

                # When tools are available, use non-streaming chat() to reliably
                # capture tool calls. MLX streaming drops tool_calls in many cases.
                # For pure chat (no tools), stream for real-time UX.
                if tools:
                    try:
                        response = await self.eco_router.chat(
                            messages, user_id=user_id, model=iter_model,
                            role=_iter_role, **kwargs
                        )
                        if response.content:
                            # Strip <plan>/<taor_plan> tags and <think>... thinking blocks
                            _display_content = response.content
                            if "<plan>" in _display_content:
                                _display_content = re.sub(
                                    r"<plan>.*?</plan>\s*", "", _display_content, flags=re.DOTALL
                                ).strip()
                            if "<taor_plan>" in _display_content:
                                _display_content = re.sub(
                                    r"<taor_plan>.*?</taor_plan>\s*", "", _display_content, flags=re.DOTALL
                                ).strip()
                            # Strip <think>... thinking tags (MiniMax reasoning output)
                            if "<think>" in _display_content:
                                _display_content = re.sub(
                                    r"<think>.*?</think>\s*", "", _display_content, flags=re.DOTALL
                                ).strip()
                            if _display_content:
                                await cb.on_event(AgentEvent(
                                    "token", _display_content, {"model": response.model},
                                ))
                    except Exception as exc:
                        logger.error("Chat failed: %s", exc, exc_info=True)
                        response = _LLMResp(
                            content=f"Sorry, an error occurred: {exc}",
                            model="unknown",
                            tool_calls=[],
                        )
                else:
                    # No tools — stream for real-time output
                    # Buffer <think>...</think> and <taor_plan>...</taor_plan> — don't show to user
                    _in_think_block = False
                    _think_buffer = ""
                    _in_taor_block = False
                    _taor_buffer = ""
                    _taor_close_tag = "</taor_plan>"
                    try:
                        async for chunk in self.eco_router.stream_chat(
                            messages, user_id=user_id, model=iter_model,
                            role=_iter_role, **kwargs
                        ):
                            if chunk.delta:
                                streamed_content += chunk.delta

                                # Buffer thinking, only emit real content.
                                # ALSO relay thinking chunks to the UI as
                                # `thinking_delta` events so the chat can
                                # show a collapsible reasoning panel.
                                text = chunk.delta
                                if "<think>" in streamed_content and not _in_think_block:
                                    _in_think_block = True
                                    _think_buffer = ""
                                if _in_think_block:
                                    _think_buffer += text
                                    if "</think>" in _think_buffer:
                                        before, after_raw = _think_buffer.split(
                                            "</think>", 1,
                                        )
                                        # Emit the closing reasoning chunk
                                        # (minus the </think> tag itself).
                                        _reasoning_chunk = before.replace(
                                            "<think>", "",
                                        )
                                        if _reasoning_chunk:
                                            await cb.on_event(AgentEvent(
                                                "thinking_delta",
                                                _reasoning_chunk, {},
                                            ))
                                        await cb.on_event(AgentEvent(
                                            "thinking_done", "", {},
                                        ))
                                        after = after_raw.strip()
                                        _in_think_block = False
                                        _think_buffer = ""
                                        if after:
                                            text = after
                                        else:
                                            continue
                                    else:
                                        # Streaming reasoning mid-block —
                                        # surface the delta, strip any
                                        # leading <think> opener.
                                        _r_chunk = text.replace("<think>", "")
                                        if _r_chunk:
                                            await cb.on_event(AgentEvent(
                                                "thinking_delta", _r_chunk, {},
                                            ))
                                        continue

                                # Buffer <taor_plan> and <plan> blocks
                                for _tag in ("<taor_plan>", "<plan>"):
                                    _close = _tag.replace("<", "</")
                                    if _tag in streamed_content and not _in_taor_block:
                                        _in_taor_block = True
                                        _taor_buffer = ""
                                        _taor_close_tag = _close
                                        break
                                if _in_taor_block:
                                    _taor_buffer += text
                                    if _taor_close_tag in _taor_buffer:
                                        after = _taor_buffer.split(_taor_close_tag, 1)[1].strip()
                                        _in_taor_block = False
                                        _taor_buffer = ""
                                        if after:
                                            text = after
                                        else:
                                            continue
                                    else:
                                        continue

                                await cb.on_event(AgentEvent(
                                    "token", text, {"model": chunk.model},
                                ))

                            if chunk.done:
                                response = _LLMResp(
                                    content=streamed_content,
                                    model=chunk.model,
                                    usage=chunk.usage,
                                    tool_calls=chunk.tool_calls,
                                )
                    except Exception as exc:
                        logger.error("Streaming failed: %s", exc, exc_info=True)
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                        response = _LLMResp(
                            content=f"Sorry, an error occurred: {exc}",
                            model="unknown",
                            tool_calls=[],
                        )

                if response is None:
                    response = _LLMResp(content=streamed_content or "No response received.", model="unknown")

                logger.info(
                    "LLM response: model=%s, content_len=%d, tool_calls=%d",
                    response.model,
                    len(response.content or ""),
                    len(response.tool_calls or []),
                )

                # Track router fallback + final model so the "done" event
                # can tell channels when Sonnet silently became Haiku.
                _resp_fallback = getattr(response, "fallback_reason", None)
                if _resp_fallback:
                    _last_fallback_reason = _resp_fallback
                if response.model and response.model != "unknown":
                    _last_model_used = response.model

                await recorder.record_llm_response(
                    content=response.content or "",
                    model=response.model,
                    has_tool_calls=bool(response.tool_calls),
                )

                # Report token usage
                usage = response.usage or {}
                total_tokens = usage.get("total_tokens", 0)
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                eco_mode = usage.get("eco_mode")  # "eco", "hybrid_free", or None (paid)
                _session_tokens += total_tokens
                await cb.on_event(AgentEvent(
                    "tokens",
                    f"{total_tokens} tokens ({prompt_tokens} in, {completion_tokens} out)",
                    {"total": total_tokens, "prompt": prompt_tokens,
                     "completion": completion_tokens, "model": response.model,
                     "eco_mode": eco_mode,
                     "fallback_reason": _resp_fallback},
                ))

                # ── Parse browsing plan from LLM response ─────────────
                # If the planner injected a prompt, try to extract the JSON
                # plan block from the response content (no extra LLM call).
                if (
                    response.content
                    and _plan_state.plan is None
                    and response.tool_calls
                    and any(tc.name == "browser" for tc in response.tool_calls)
                ):
                    _parsed_plan = parse_plan_from_response(response.content)
                    if _parsed_plan is not None:
                        from dataclasses import replace as _dc_replace
                        _plan_state = _dc_replace(_plan_state, plan=_parsed_plan)
                        await cb.on_event(AgentEvent(
                            BROWSER_PLAN,
                            f"Plan: {_parsed_plan.goal}",
                            {
                                "goal": _parsed_plan.goal,
                                "steps": [s.description for s in _parsed_plan.steps],
                                "current_step": _parsed_plan.current_step,
                            },
                        ))

                # ── Parse research requirements from first response ────────
                # If research mode is active and requirements not yet set,
                # extract the JSON array the LLM should have output.
                if (
                    _research_state is not None
                    and not _research_state.info_requirements
                    and response.content
                    and iteration == 0
                ):
                    _reqs = parse_requirements_from_response(response.content)
                    if _reqs:
                        from dataclasses import replace as _dc_replace
                        _research_state = _dc_replace(
                            _research_state,
                            info_requirements=tuple(_reqs),
                            gaps=tuple(_reqs),
                        )

                # Debug: log tool state
                logger.info(
                    "TOOL STATE: tools_sent=%d, tool_calls_received=%d, names=%s",
                    len(tools),
                    len(response.tool_calls or []),
                    [tc.name for tc in (response.tool_calls or [])],
                )

                # If no tools were provided but LLM returned tool_calls
                # (hallucination from history patterns), ignore them
                if not tools and response.tool_calls:
                    logger.warning(
                        "LLM returned %d tool_calls but no tools were provided — ignoring",
                        len(response.tool_calls),
                    )
                    response = _LLMResp(
                        content=response.content or "Hello! How can I help you?",
                        model=response.model,
                        usage=response.usage,
                        tool_calls=[],
                    )

                # Filter out hallucinated tool calls for tools not in the current list.
                # If a tool is not in the sent set but exists in the full registry,
                # dynamically inject it (the LLM remembered it from history).
                if tools and response.tool_calls:
                    _valid_names = {
                        t.get("function", {}).get("name") for t in tools
                    }
                    # Before dropping, try to inject missing tools from registry
                    _injected_count = 0
                    for tc in response.tool_calls:
                        if tc.name not in _valid_names and self.registry:
                            _schema = self.registry.get_tool_schema(tc.name)
                            if _schema is not None:
                                tools.append(_schema)
                                _valid_names.add(tc.name)
                                _injected_count += 1
                    if _injected_count:
                        logger.info(
                            "Late-injected %d tools from registry (LLM remembered from history)",
                            _injected_count,
                        )

                    _valid_calls = [
                        tc for tc in response.tool_calls if tc.name in _valid_names
                    ]
                    _dropped = len(response.tool_calls) - len(_valid_calls)
                    if _dropped > 0:
                        _dropped_names = [tc.name for tc in response.tool_calls if tc.name not in _valid_names]
                        logger.warning(
                            "Dropped %d hallucinated tool calls (not in current tools): %s",
                            _dropped, _dropped_names,
                        )
                        if _valid_calls:
                            response = _LLMResp(
                                content=response.content,
                                model=response.model,
                                usage=response.usage,
                                tool_calls=_valid_calls,
                            )
                        else:
                            # ALL tool calls were hallucinated — inject correction and retry
                            _avail = sorted(_valid_names - {"search_tools", "delegate"})[:10]
                            _correction = (
                                f"[SYSTEM: The tool '{_dropped_names[0]}' is not available in your current toolset. "
                                f"Use search_tools('{_dropped_names[0].split('_')[0]}') to discover available tools — "
                                f"it may exist under a different name. "
                                f"Your current tools: {', '.join(_avail)}. "
                                f"Try search_tools FIRST, then use what you find, or respond with text if nothing fits.]"
                            )
                            messages.append(LLMMessage(role="assistant", content=response.content or ""))
                            messages.append(LLMMessage(role="user", content=_correction))
                            logger.warning("Injecting correction for hallucinated tools, retrying LLM")
                            continue  # Retry the agentic loop iteration

                if not response.tool_calls:
                    _final_content = response.content or streamed_content or ""

                    # Nudge: if iteration 0 returned text-only but channel
                    # tools were available, the LLM likely repeated old data
                    # from history instead of calling the tool. Force retry once.
                    if (
                        iteration == 0
                        and _matched_channels
                        and tools
                        and not getattr(self, "_nudged_tool_use", False)
                    ):
                        self._nudged_tool_use = True
                        _channel_tool_names = [
                            t.get("function", {}).get("name", "")
                            for t in tools
                            if any(ch in t.get("function", {}).get("name", "").lower()
                                   for ch in _matched_channels)
                        ][:3]
                        _nudge = (
                            "[SYSTEM: You answered from memory without calling any tool. "
                            "Your data may be stale. Use one of these tools to get LIVE data: "
                            f"{', '.join(_channel_tool_names)}. "
                            "Call the tool NOW — do not repeat old numbers.]"
                        )
                        messages.append(LLMMessage(role="assistant", content=_final_content))
                        messages.append(LLMMessage(role="user", content=_nudge))
                        logger.warning("Nudging tool use: LLM skipped tools for channel query")
                        continue

                    # Strip internal tags that should never reach the user
                    if "<think>" in _final_content:
                        _final_content = re.sub(
                            r"<think>.*?</think>\s*", "", _final_content, flags=re.DOTALL
                        ).strip()
                    if "<taor_plan>" in _final_content:
                        _final_content = re.sub(
                            r"<taor_plan>.*?</taor_plan>\s*", "", _final_content, flags=re.DOTALL
                        ).strip()
                    if "<plan>" in _final_content:
                        _final_content = re.sub(
                            r"<plan>.*?</plan>\s*", "", _final_content, flags=re.DOTALL
                        ).strip()

                    # Empty response from worker/brain model — escalate to fallback.
                    # Covers: MiniMax error 2013 (empty), Haiku empty response bug,
                    # worker can't produce final answer, etc.
                    # Use the fallback model (different provider) so we don't
                    # loop back to the same broken brain.
                    if (
                        not _final_content.strip()
                        and tools
                        and not _escalated
                    ):
                        try:
                            _fallback_name = await self.eco_router.get_fallback_model(user_id)
                        except Exception:
                            _fallback_name = None
                        logger.warning(
                            "Empty LLM response from %s (usage=%s, tools=%d) — escalating to fallback %s",
                            response.model, response.usage, len(tools), _fallback_name,
                        )
                        _escalated = True
                        _escalation_iter = iteration
                        # Pin to fallback model AND use a non-brain/worker role
                        # so eco_router.chat() takes the explicit-model bypass
                        # (_route_paid) instead of re-picking the broken brain.
                        iter_model = _fallback_name
                        _iter_role = "escalation" if _fallback_name else ROLE_BRAIN
                        streamed_content = ""
                        continue  # Retry with a different provider

                    # Final text response (already streamed to user)
                    await cb.on_event(AgentEvent("stream_done", "", {}))
                    all_new_messages.append(
                        LLMMessage(role="assistant", content=_final_content)
                    )
                    break

                # ── Fast dispatch ─────────────────────────────────────
                # On first heavy tool call (any iteration), push to TaskRunner
                # and return immediately so the lane queue stays free.
                # Only dispatch tools that are actually in the current tools list.
                _current_tool_names = {
                    t.get("function", {}).get("name") for t in tools
                } if tools else set()
                def _is_heavy(tc_name: str) -> bool:
                    """Check if a tool call is heavy (should fast-dispatch)."""
                    if tc_name in HEAVY_TOOLS:
                        return True
                    # MCP tools: mcp_{uuid-with-hyphens}_{base_name}
                    # e.g. mcp_aa828e97-7923-4189-b6e4-1f2ace89b115_email_delete
                    if tc_name.startswith("mcp_"):
                        for base in _HEAVY_MCP_BASES:
                            if tc_name.endswith("_" + base):
                                return True
                    return False

                # Don't fast-dispatch if task tools were injected — task
                # operations (add_task, daily_briefing) should stay foreground.
                _has_task_tools = bool(_current_tool_names & _TASK_TOOL_NAMES)
                if (
                    iteration <= 2  # Allow dispatch on first few iterations
                    and self._task_runner is not None
                    and not getattr(self, "is_background", False)  # Don't re-dispatch background tasks
                    and not _has_task_tools  # Task messages stay foreground
                    and any(
                        _is_heavy(tc.name) and tc.name in _current_tool_names
                        for tc in response.tool_calls
                    )
                ):
                    from lazyclaw.runtime.agent_settings import get_agent_settings

                    _agent_settings = await get_agent_settings(self.config, user_id)
                    _active_count = self._team_lead.active_count if self._team_lead else 0
                    if (
                        _agent_settings.get("auto_delegate", True)
                        and _active_count
                            < _agent_settings.get("max_concurrent_specialists", 3)
                    ):
                        _specialist_name = response.tool_calls[0].name

                        _task_id = await self._task_runner.submit(
                            user_id=user_id,
                            instruction=message,
                            name=None,  # auto-generates readable name from instruction
                            timeout=_agent_settings.get("specialist_timeout_s", 120),
                            callback=callback,
                        )

                        # Foreground task was re-routed to background
                        if self._team_lead and _fg_task_id:
                            self._team_lead.cancel(_fg_task_id)
                            _fg_task_id = None

                        await cb.on_event(AgentEvent(
                            FAST_DISPATCH,
                            f"Dispatched to background ({_specialist_name})",
                            {"task_id": _task_id, "specialist": _specialist_name},
                        ))
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                        await cb.on_event(AgentEvent("done", "Dispatched", {}))

                        if _delegate_registered:
                            self.registry.unregister("delegate")
                        return "On it — working in background."

                # End streaming before tool lines appear (prevents text/tool garbling)
                if streamed_content:
                    await cb.on_event(AgentEvent("stream_done", "", {}))
                    streamed_content = ""

                # TAOR phase: entering "act" — tool execution.
                try:
                    await cb.on_event(AgentEvent(
                        "phase", "act",
                        {"phase": "act", "iteration": iteration,
                         "tools": [tc.name for tc in (response.tool_calls or [])]},
                    ))
                    if self._team_lead and _fg_task_id:
                        self._team_lead.update_phase(_fg_task_id, "act")
                except Exception:
                    pass

                # Assistant message with tool calls (may have partial text content)
                assistant_msg = LLMMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
                messages.append(assistant_msg)
                all_new_messages.append(assistant_msg)

                # If delegate is among tool calls, execute ONLY delegate.
                # Delegate runs a full specialist loop — other parallel tool
                # calls are redundant and waste tokens (double-agent problem).
                _tool_calls_to_run = response.tool_calls
                _skipped_calls: list[ToolCall] = []
                if len(response.tool_calls) > 1:
                    _delegate_calls = [
                        tc for tc in response.tool_calls if tc.name == "delegate"
                    ]
                    if _delegate_calls:
                        _skipped_calls = [
                            tc for tc in response.tool_calls if tc.name != "delegate"
                        ]
                        _tool_calls_to_run = _delegate_calls
                        logger.info(
                            "Delegate detected — skipping %d parallel tool calls",
                            len(_skipped_calls),
                        )

                # Inject stub results for skipped calls (LLM expects tool_call_id responses)
                for sc in _skipped_calls:
                    messages.append(LLMMessage(
                        role="tool",
                        content="Skipped — delegate is handling this task.",
                        tool_call_id=sc.id,
                    ))

                # ── Parallel pre-execution for read-only tools ─────────────
                # Identify read-only tools that are not already in the cache.
                # Run them concurrently via execute_batch(), then use the
                # pre-computed results in the sequential loop below.
                # State-modifying tools always run sequentially in the loop.
                _pre_executed: dict[str, tuple[str, int]] = {}  # tc.id → (result, duration_ms)
                _ro_to_batch: list[ToolCall] = []
                for _btc in _tool_calls_to_run:
                    _bkey = f"{_btc.name}:{json.dumps(_btc.arguments, sort_keys=True)}"
                    if _bkey not in _tool_call_cache:
                        _skill = self.registry.get(_btc.name) if self.registry else None
                        if _skill and getattr(_skill, "read_only", False):
                            _ro_to_batch.append(_btc)
                if len(_ro_to_batch) > 1:
                    _batch_outcomes = await self.executor.execute_batch(
                        _ro_to_batch, user_id, callback=cb,
                    )
                    for _btc, _bres, _bdur, _bgroup in _batch_outcomes:
                        _pre_executed[_btc.id] = (_bres, _bdur)

                # Execute each tool call
                for tc in _tool_calls_to_run:
                    _display = self.registry.get_display_name(tc.name) if self.registry else tc.name
                    _all_tools_used.append(_display)
                    await cb.on_event(AgentEvent(
                        "tool_call", _display,
                        {"tool": tc.name, "display_name": _display, "args": tc.arguments},
                    ))
                    # Emit browser-specific action event for transparency
                    if tc.name == "browser":
                        _br_action = tc.arguments.get("action", "")
                        _br_target = (
                            tc.arguments.get("url", "")
                            or tc.arguments.get("ref", "")
                            or tc.arguments.get("query", "")
                            or tc.arguments.get("text", "")
                        )
                        _br_step = (
                            (_plan_state.plan.current_step + 1)
                            if _plan_state.plan else 0
                        )
                        _br_total = (
                            len(_plan_state.plan.steps)
                            if _plan_state.plan else 0
                        )
                        await cb.on_event(AgentEvent(
                            BROWSER_ACTION,
                            f"Browser: {_br_action}",
                            {
                                "action": _br_action,
                                "target": _br_target[:100],
                                "step_number": _br_step,
                                "total_steps": _br_total,
                            },
                        ))
                    await recorder.record_tool_call(tc.name, tc.arguments)
                    # Inject background flag so browser uses headless
                    if getattr(self, "is_background", False) and tc.name == "browser":
                        tc = ToolCall(
                            id=tc.id, name=tc.name,
                            arguments={**tc.arguments, "_background": True},
                        )

                    # Duplicate call cache — skip re-executing identical tool calls
                    _cache_key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                    if _cache_key in _tool_call_cache:
                        result = _tool_call_cache[_cache_key]
                        logger.info("Tool %s cache hit (skipped re-execution)", tc.name)
                    elif tc.id in _pre_executed:
                        # Already executed in parallel batch above
                        result, _dur_ms = _pre_executed[tc.id]
                        logger.debug("Tool %s used parallel pre-execution result (%dms)", tc.name, _dur_ms)
                    else:
                        result = await self.executor.execute(tc, user_id, callback=cb)

                    # Cap oversized results before injecting into context
                    # Skip capping + caching for approval responses (JSON args must stay intact)
                    if isinstance(result, str) and not result.startswith(APPROVAL_PREFIX):
                        result = _cap_tool_result(result)
                        _tool_call_cache[_cache_key] = result

                    await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))

                    # Handle approval-required responses BEFORE emitting tool_result
                    # to avoid duplicate result events (one for placeholder, one for real)
                    if isinstance(result, str) and result.startswith(APPROVAL_PREFIX):
                        try:
                            parts = result[len(APPROVAL_PREFIX):]
                            colon_idx = parts.index(":")
                            skill_name = parts[:colon_idx]
                            args_json = parts[colon_idx + 1:]
                            parsed_args = json.loads(args_json) if args_json != "{}" else {}
                        except (ValueError, json.JSONDecodeError) as parse_err:
                            logger.error("Malformed approval response: %s (%s)", result, parse_err)
                            result = f"Tool error: malformed approval response"
                            tool_msg = LLMMessage(
                                role="tool",
                                content=result,
                                tool_call_id=tc.id,
                            )
                            messages.append(tool_msg)
                            all_new_messages.append(tool_msg)
                            continue

                        # Check if user already denied this exact tool+args this turn
                        _denial_key = f"{skill_name}:{json.dumps(parsed_args, sort_keys=True)}"
                        if _denial_key in _denied_approvals:
                            logger.info("Tool %s already denied this turn, skipping re-prompt", skill_name)
                            result = (
                                f"The user already denied '{skill_name}' with these arguments. "
                                f"Do not retry this action. Try a different approach or ask the user."
                            )
                            tool_msg = LLMMessage(
                                role="tool",
                                content=result,
                                tool_call_id=tc.id,
                            )
                            messages.append(tool_msg)
                            all_new_messages.append(tool_msg)
                            continue

                        # Try inline approval via callback (CLI y/n prompt)
                        # Pass display name so the UI shows friendly names
                        if hasattr(cb, '_pending_display_name'):
                            cb._pending_display_name = _display
                        approved = await cb.on_approval_request(skill_name, parsed_args)

                        if approved:
                            await cb.on_event(AgentEvent(
                                "approval", f"{_display} approved",
                                {"skill": skill_name, "display_name": _display, "approved": True},
                            ))
                            result = await self.executor.execute_allowed(tc, user_id, callback=cb)
                            await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))
                            await cb.on_event(AgentEvent(
                                "tool_result", _display,
                                {"tool": tc.name, "display_name": _display},
                            ))
                        else:
                            _denied_approvals.add(_denial_key)
                            await cb.on_event(AgentEvent(
                                "approval", f"{_display} denied",
                                {"skill": skill_name, "display_name": _display, "approved": False},
                            ))
                            result = (
                                f"The user denied the action '{skill_name}'. "
                                f"Do not retry this action. Explain what you wanted to do "
                                f"and ask if the user wants to try a different approach."
                            )
                    else:
                        # Normal tool (no approval needed) — emit result event
                        await cb.on_event(AgentEvent(
                            "tool_result", _display,
                            {"tool": tc.name, "display_name": _display},
                        ))

                    # Track step with TeamLead
                    if self._team_lead and _fg_task_id:
                        self._team_lead.update_step(_fg_task_id, _display)

                    # ── Non-browser result verification ──────────────────
                    # Stamp tool results with `→ FAILED: <reason>` when the
                    # output looks like an error (HTTP 4xx/5xx, "Error:" /
                    # "Exception:" / traceback, empty result from tools that
                    # shouldn't be empty). Browser already self-stamps via
                    # action_verifier — classify() is a no-op if the marker
                    # is already present. The marker flows both into the
                    # LLM context (so it stops paraphrasing errors as
                    # success) and into stuck_detector.detect_no_progress.
                    _result_str = result if isinstance(result, str) else str(result)
                    from lazyclaw.runtime import result_verifier as _rv
                    _rv_status, _rv_reason = _rv.classify(tc.name, _result_str)
                    if _rv_status == "failed" and _rv_reason:
                        _result_str = _rv.stamp_failed(_result_str, _rv_reason)
                        result = _result_str  # keep downstream paths in sync

                    tool_msg = LLMMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id,
                    )
                    messages.append(tool_msg)
                    all_new_messages.append(tool_msg)
                    _tool_call_history.append(tc.name)
                    _tool_results.append(_result_str)

                    # ── Hard stop: OAuth credential not authorized ────
                    # n8n_management surfaces this with a STOP_OAUTH_CREDENTIAL
                    # marker when a workflow call fails because the user
                    # hasn't finished OAuth consent. Break the loop
                    # immediately and hand the consent URL back.
                    if "STOP_OAUTH_CREDENTIAL" in _result_str:
                        logger.warning(
                            "OAuth credential not authorized — "
                            "hard-stopping agent loop for user %s", user_id,
                        )
                        await cb.on_event(AgentEvent(
                            "done", "OAuth credential not authorized",
                            {"reason": "oauth_pending"},
                        ))
                        # Strip the internal marker before returning to user.
                        _user_msg = _result_str.replace(
                            "Error: STOP_OAUTH_CREDENTIAL: ", "",
                        )
                        return _user_msg

                    # ── Browser action planner: evaluate result ────────
                    # Track plan progress after every browser call.
                    # REPLAN → inject fallback hint.
                    # ESCALATE → let the stuck detector handle it (don't inject).
                    if tc.name == "browser" and _plan_state.plan is not None:
                        _result_str = result if isinstance(result, str) else str(result)
                        _plan_decision = evaluate_action_result(
                            _plan_state,
                            tc.arguments.get("action", ""),
                            _result_str,
                        )
                        from dataclasses import replace as _dc_replace
                        _plan_state = _dc_replace(
                            _plan_state,
                            plan=_plan_decision.new_state.plan,
                            browser_call_count=_plan_decision.new_state.browser_call_count,
                            consecutive_failures=_plan_decision.new_state.consecutive_failures,
                        )
                        if (
                            _plan_decision.status in (PlanStatus.CONTINUE, PlanStatus.REPLAN)
                            and _plan_decision.system_message
                        ):
                            messages.append(LLMMessage(
                                role="system",
                                content=_plan_decision.system_message,
                            ))

                    # ── BROWSER_VERIFY: parse and emit verification result ─
                    if tc.name == "browser":
                        _res_for_verify = result if isinstance(result, str) else str(result)
                        _verify_succeeded: bool | None = None
                        _verify_evidence = ""
                        if "→ SUCCESS:" in _res_for_verify:
                            _verify_succeeded = True
                            _idx = _res_for_verify.find("→ SUCCESS:")
                            _verify_evidence = _res_for_verify[_idx + 10:].split("\n")[0].strip()[:120]
                        elif "→ FAILED:" in _res_for_verify:
                            _verify_succeeded = False
                            _idx = _res_for_verify.find("→ FAILED:")
                            _verify_evidence = _res_for_verify[_idx + 9:].split("\n")[0].strip()[:120]
                        if _verify_succeeded is not None:
                            await cb.on_event(AgentEvent(
                                BROWSER_VERIFY,
                                _verify_evidence,
                                {
                                    "action": tc.arguments.get("action", ""),
                                    "succeeded": _verify_succeeded,
                                    "evidence": _verify_evidence,
                                },
                            ))

                    # ── Research strategy: update per-source ──────────────
                    # After each browser or web_search result, record the source
                    # and inject a progress hint so the LLM knows when to stop.
                    if tc.name in ("browser", "web_search") and _research_state is not None:
                        _res_str = result if isinstance(result, str) else str(result)
                        _src_url = (
                            tc.arguments.get("url", "")
                            or tc.arguments.get("query", "")
                        )
                        from dataclasses import replace as _dc_replace
                        _research_state = note_source(
                            _research_state,
                            url=_src_url,
                            title="",
                            tool_result=_res_str,
                        )
                        _progress_msg = _make_research_progress(_research_state)
                        if _progress_msg:
                            messages.append(LLMMessage(
                                role="system",
                                content=_progress_msg,
                            ))
                        logger.debug(
                            "Research state: sources=%d status=%s gaps=%d",
                            _research_state.sources_count,
                            _research_state.status.value,
                            len(_research_state.gaps),
                        )
                        await cb.on_event(AgentEvent(
                            BROWSER_PROGRESS,
                            f"Research: {_research_state.requirements_met_count}/{len(_research_state.info_requirements)} found",
                            {
                                "sources_checked": _research_state.sources_count,
                                "requirements_met": _research_state.requirements_met_count,
                                "total_requirements": len(_research_state.info_requirements),
                                "gaps": list(_research_state.gaps[:3]),
                                "status": _research_state.status.value,
                            },
                        ))

                    # Dynamic schema injection: after search_tools returns,
                    # inject discovered tool schemas so LLM can call them next iteration
                    if tc.name == "search_tools" and self.registry:
                        discovered = _extract_tool_names_from_search_result(
                            result if isinstance(result, str) else str(result)
                        )
                        for dname in discovered:
                            schema = self.registry.get_tool_schema(dname)
                            if schema and schema not in tools:
                                tools.append(schema)
                        if discovered:
                            logger.info(
                                "Injected %d tool schemas: %s",
                                len(discovered), ", ".join(discovered),
                            )

                # ── Terminal tools: force text response next iteration ──
                # After these tools, the job is done. Inject a stop signal so
                # the LLM responds with a brief confirmation, not more tools.
                _TERMINAL_TOOLS = frozenset({
                    # Task management
                    "add_task", "complete_task", "delete_task", "update_task",
                    "daily_briefing", "list_tasks", "work_todos",
                    # Messaging platforms (one-shot actions)
                    "whatsapp_send", "whatsapp_mute", "whatsapp_list_muted",
                    "whatsapp_send_image",
                    "instagram_send", "instagram_like", "instagram_comment",
                    "instagram_follow", "instagram_unfollow",
                    "email_send",
                })
                if response and response.tool_calls:
                    _terminal_used = any(
                        tc.name in _TERMINAL_TOOLS for tc in response.tool_calls
                    )
                    if _terminal_used:
                        messages.append(LLMMessage(
                            role="system",
                            content=(
                                "RESPOND NOW with a SHORT message (1-3 sentences max). "
                                "Do NOT call any more tools. Do NOT explain how things work. "
                                "Do NOT write code or technical details. Just show the result."
                            ),
                        ))

                # ── Running-long nudge ──
                # At 80% of safety cap, tell the LLM to wrap up or ask user
                if iteration >= _nudge_at and not _nudged:
                    _nudged = True
                    _nudge_msg = LLMMessage(
                        role="system",
                        content=(
                            "You've been working for a while. You have a few steps left. "
                            "Either: (1) finish what you're doing and summarize results, "
                            "or (2) if you need more work, explain what's left and ask "
                            "the user if they want you to continue."
                        ),
                    )
                    messages.append(_nudge_msg)
                    logger.info(
                        "Iteration %d/%d: injected running-long nudge",
                        iteration + 1, max_iterations,
                    )

                # ── Stuck detection (replaces old inline loop detection) ──
                # Only run if tools were actually called this iteration
                if response and response.tool_calls:
                    # TAOR phase: entering "observe" — interpreting tool results.
                    try:
                        await cb.on_event(AgentEvent(
                            "phase", "observe",
                            {"phase": "observe", "iteration": iteration,
                             "results_count": len(response.tool_calls)},
                        ))
                        if self._team_lead and _fg_task_id:
                            self._team_lead.update_phase(_fg_task_id, "observe")
                    except Exception:
                        pass

                    _last_result = _tool_results[-1] if _tool_results else None
                    _stuck_signal = detect_stuck(
                        _tool_call_history, _tool_results, _last_result,
                    )
                    if _stuck_signal is not None:
                        logger.warning(
                            "Stuck detected: %s (%s)", _stuck_signal.reason, _stuck_signal.context,
                        )

                        # ── Graduated recovery (3 levels) ─────────────────────
                        # Level 1 (soft): read + try different approach, no model switch
                        # Level 2 (medium): switch to brain, demand strategy change
                        # Level 3 (hard): give up / ask user for help
                        if _escalation_level == 0:
                            _escalation_level = 1
                            logger.info(
                                "Stuck level 1 (soft nudge): %s", _stuck_signal.reason
                            )
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    f"⚠ Not making progress: {_stuck_signal.context}\n\n"
                                    f"Before your next action:\n"
                                    f"1. Use action='read' to understand what's currently on the page.\n"
                                    f"2. Use action='snapshot' to see available elements.\n"
                                    f"Then try a DIFFERENT approach — not the same action again."
                                ),
                            ))
                            _tool_call_history.clear()
                            _tool_results.clear()
                            continue

                        elif _escalation_level == 1:
                            _escalation_level = 2
                            _escalated = True  # triggers brain model in routing
                            _escalation_iter = iteration
                            _post_l2_stuck_count = 0  # reset window counter
                            logger.info(
                                "Stuck level 2 (strategy change, brain escalation): %s",
                                _stuck_signal.reason,
                            )
                            await cb.on_event(AgentEvent(
                                "llm_call",
                                "Escalating to brain model...",
                                {"escalation": True},
                            ))
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    f"⚠ STRATEGY CHANGE REQUIRED: {_stuck_signal.context}\n\n"
                                    f"The previous approach failed twice. You MUST choose ONE:\n"
                                    f"1. Use web_search to RESEARCH how to accomplish this — find the right URL, steps, or workaround\n"
                                    f"2. Try a COMPLETELY DIFFERENT approach (different URL, different button, different strategy)\n"
                                    f"3. If the page requires login or human interaction, say so explicitly\n"
                                    f"4. If the task is truly impossible right now, STOP and explain what went wrong (text only)\n\n"
                                    f"Describe what you've tried, why it failed, and what you'll try differently."
                                ),
                            ))
                            _tool_call_history.clear()
                            _tool_results.clear()
                            iter_model = None  # Let eco_router decide
                            _iter_role = ROLE_BRAIN
                            continue

                        # Level 3: brain also stuck — give up.
                        # Background agents: break immediately (can't ask user).
                        # Foreground agents: 1st post-L2 stuck → ask user for
                        # help; 2nd → hard-stop with a terminal message
                        # (don't keep popping help dialogs at the user).
                        _post_l2_stuck_count += 1
                        _is_bg = getattr(self, "is_background", False)
                        _hard_stop = _is_bg or _post_l2_stuck_count >= 2
                        if _hard_stop:
                            if _is_bg:
                                logger.warning("Background agent stuck after escalation — giving up")
                            else:
                                logger.warning(
                                    "Stuck level 3 hard-stop: %d post-L2 stuck signals "
                                    "(reason=%s, tool=%s) — terminating loop without "
                                    "another HELP_NEEDED dialog",
                                    _post_l2_stuck_count,
                                    _stuck_signal.reason,
                                    _stuck_signal.tool_name,
                                )
                            _failed_tool = _stuck_signal.tool_name or "the same step"
                            all_new_messages.append(LLMMessage(
                                role="assistant",
                                content=(
                                    f"I tried '{_failed_tool}' repeatedly and it kept "
                                    f"failing — {_stuck_signal.context}. I'm stopping "
                                    f"so I don't burn more time on a broken path. "
                                    f"Tell me what you'd like me to do differently."
                                ),
                            ))
                            break

                        await cb.on_event(AgentEvent(
                            HELP_NEEDED, _stuck_signal.context,
                            {"reason": _stuck_signal.reason, "tool": _stuck_signal.tool_name,
                             "needs_browser": _stuck_signal.needs_browser},
                        ))

                        # Ask user for help — timeout after 5 minutes
                        try:
                            _help_response = await asyncio.wait_for(
                                cb.on_help_request(
                                    _stuck_signal.context, _stuck_signal.needs_browser,
                                ),
                                timeout=300.0,
                            )
                        except asyncio.TimeoutError:
                            logger.info("Help request timed out after 5 min, auto-skipping")
                            _help_response = "skip"

                        if _help_response == "skip":
                            all_new_messages.append(LLMMessage(
                                role="assistant",
                                content=f"I got stuck: {_stuck_signal.context}. Let me try a different approach.",
                            ))
                            break

                        # Browser handoff: user said "ready" → ensure visible → wait for "done"
                        if _stuck_signal.needs_browser and _help_response in (
                            "ready", "show me", "show", "ok", "yes",
                        ):
                            if not getattr(self, "is_background", False):
                                from lazyclaw.skills.builtin.browser_actions.backends import (
                                    get_visible_cdp_backend as _get_visible_cdp_backend,
                                    raise_browser_window as _raise_browser_window,
                                    stop_remote_session as _stop_remote_session,
                                )
                                await _get_visible_cdp_backend(user_id)

                                # Server mode: send noVNC URL via Telegram
                                from lazyclaw.browser.remote_takeover import (
                                    get_active_session, is_server_mode,
                                )
                                _remote = get_active_session(user_id) if is_server_mode() else None

                                if _remote:
                                    await cb.on_event(AgentEvent(
                                        HELP_RESPONSE,
                                        f"Browser ready for remote control: {_remote.url}",
                                        {"novnc_url": _remote.url,
                                         "stuck_context": _stuck_signal.context},
                                    ))
                                    try:
                                        _done_resp = await asyncio.wait_for(
                                            cb.on_help_request(
                                                "Say 'done' when you're finished.",
                                                False,
                                            ),
                                            timeout=600.0,  # 10 min for browser handoff
                                        )
                                    except asyncio.TimeoutError:
                                        logger.info("Browser handoff timed out after 10 min")
                                        _done_resp = "skip"
                                    # Cleanup: stop noVNC, relaunch headless
                                    await _stop_remote_session(user_id)
                                else:
                                    await _raise_browser_window()
                                    # Give macOS time to bring window to foreground
                                    await asyncio.sleep(1.0)
                                    await cb.on_event(AgentEvent(
                                        HELP_RESPONSE,
                                        "Browser is visible. Take over and say 'done' when finished.",
                                        {},
                                    ))
                                    try:
                                        _done_resp = await asyncio.wait_for(
                                            cb.on_help_request(
                                                "Browser is open on your screen. Say 'done' when you're finished.",
                                                False,
                                            ),
                                            timeout=600.0,  # 10 min for browser handoff
                                        )
                                    except asyncio.TimeoutError:
                                        logger.info("Browser handoff timed out after 10 min")
                                        _done_resp = "skip"

                                if _done_resp == "skip":
                                    all_new_messages.append(LLMMessage(
                                        role="assistant",
                                        content=f"I got stuck: {_stuck_signal.context}. User chose to skip.",
                                    ))
                                    break

                        # Notify observability that help was received
                        await cb.on_event(AgentEvent(
                            HELP_RESPONSE,
                            f"User help received: {_help_response}",
                            {"response": _help_response},
                        ))

                        # Take snapshot after user intervention
                        try:
                            from lazyclaw.skills.builtin.browser_actions.backends import get_cdp_backend as _get_cdp_backend
                            _snap_backend = await _get_cdp_backend(user_id)
                            _snap_url = await _snap_backend.current_url()
                            _snap_title = await _snap_backend.title()
                            _snapshot = f"After user help: now on {_snap_title} ({_snap_url})"
                        except Exception:
                            logger.debug("Failed to get browser snapshot after user help", exc_info=True)
                            _snapshot = f"User intervention complete. Response: {_help_response}"

                        # Inject snapshot as assistant message (NOT tool —
                        # tool results were already appended, so a second tool
                        # msg with the same id would orphan and crash OpenAI).
                        _help_msg = LLMMessage(
                            role="assistant", content=_snapshot,
                        )
                        messages.append(_help_msg)
                        all_new_messages.append(_help_msg)
                        _tool_call_history.clear()
                        _tool_results.clear()
                        continue

            else:
                # Safety cap reached — shouldn't happen often with nudge
                if self._team_lead and _fg_task_id:
                    self._team_lead.fail(_fg_task_id, "Safety cap reached")
                    _fg_task_id = None
                logger.warning(
                    "Safety cap reached (%d iterations). Last tool: %s",
                    max_iterations,
                    _tool_call_history[-1] if _tool_call_history else "none",
                )
                all_new_messages.append(
                    LLMMessage(
                        role="assistant",
                        content=(response.content if response and response.content else "")
                        or (
                            "I hit the safety limit. Here's what I've done so far. "
                            "Say 'continue' if you want me to keep going."
                        ),
                    )
                )
        except asyncio.CancelledError:
            if self._team_lead and _fg_task_id:
                self._team_lead.cancel(_fg_task_id)
            await cb.on_event(AgentEvent("done", "Cancelled", {}))
            if _delegate_registered and self.registry:
                self.registry.unregister("delegate")
            if _dispatch_registered and self.registry:
                self.registry.unregister("dispatch_subagents")
            return "Operation cancelled."

        # ── TAOR Phase 3: VERIFY ──────────────────────────────────────
        # After the execute phase, verify the final response with lightweight
        # heuristic checks (no LLM call). On failure, inject failure context
        # and make one correction LLM call, then re-verify. Repeats up to
        # _taor_verify_passes times. LOW effort skips this entirely.
        #
        # The correction call uses ROLE_BRAIN (Claude) and explicitly instructs
        # text-only response — no tool calls. This keeps it cheap and fast.
        if _taor_verify_passes > 0 and needs_tools and all_new_messages:
            from lazyclaw.llm.providers.base import LLMResponse as _LLMRespV
            # TAOR phase: entering "reflect" — verification pass.
            try:
                await cb.on_event(AgentEvent(
                    "phase", "reflect",
                    {"phase": "reflect", "passes": _taor_verify_passes},
                ))
                if self._team_lead and _fg_task_id:
                    self._team_lead.update_phase(_fg_task_id, "reflect")
            except Exception:
                pass
            for _taor_v in range(_taor_verify_passes):
                _taor_last = all_new_messages[-1]
                _taor_final = _taor_last.content or ""
                _taor_ok, _taor_reason = verify_response(
                    message, _taor_final, _tool_results, _effort,
                )
                if _taor_ok:
                    logger.info("TAOR verify passed on attempt %d", _taor_v + 1)
                    break
                # Last attempt — log and proceed with best-effort response.
                if _taor_v == _taor_verify_passes - 1:
                    logger.info(
                        "TAOR verify: %d passes exhausted, using last response",
                        _taor_verify_passes,
                    )
                    break
                logger.info(
                    "TAOR verify failed (pass %d/%d): %s",
                    _taor_v + 1, _taor_verify_passes, _taor_reason,
                )
                _taor_retry_context = _taor_reason
                await cb.on_event(AgentEvent(
                    "taor_verify_retry",
                    f"Self-checking response (attempt {_taor_v + 2})",
                    {"reason": _taor_reason, "attempt": _taor_v + 1},
                ))
                # Inject correction directive — text-only, no tools.
                _correction_directive = (
                    "[SELF-VERIFICATION FAILED: "
                    f"{_taor_reason} "
                    "Provide a corrected, complete response that addresses "
                    "this issue. Respond with text only — do NOT call any tools.]"
                )
                messages.append(LLMMessage(role="assistant", content=_taor_final))
                messages.append(LLMMessage(role="user", content=_correction_directive))
                try:
                    _corr_resp = await self.eco_router.chat(
                        messages, user_id=user_id, role=ROLE_BRAIN,
                    )
                    if _corr_resp.content and _corr_resp.content.strip():
                        _corrected = _corr_resp.content
                        all_new_messages.append(
                            LLMMessage(role="assistant", content=_corrected)
                        )
                        await cb.on_event(AgentEvent(
                            "token", _corrected,
                            {"model": _corr_resp.model, "taor_correction": True},
                        ))
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                except Exception as _ve:
                    logger.warning("TAOR verify correction call failed: %s", _ve)
                    break

        # ── Post-loop: persist + cleanup (guarded by finally) ─────────
        content = ""
        try:
            # Resolve chat session
            if not chat_session_id:
                async with db_session(self.config) as db:
                    row = await db.execute(
                        "SELECT id FROM agent_chat_sessions "
                        "WHERE user_id = ? AND archived_at IS NULL "
                        "ORDER BY created_at DESC LIMIT 1",
                        (user_id,),
                    )
                    existing = await row.fetchone()
                    if existing:
                        chat_session_id = existing[0]
                    else:
                        chat_session_id = str(uuid4())
                        await db.execute(
                            "INSERT INTO agent_chat_sessions (id, user_id) VALUES (?, ?)",
                            (chat_session_id, user_id),
                        )
                        await db.commit()

            # Store ALL messages (user, assistant, tool calls, tool results) encrypted
            rows = []
            for msg in all_new_messages:
                msg_id = str(uuid4())
                encrypted_content = encrypt(msg.content, key)
                tool_name = None
                metadata = None

                if msg.tool_calls:
                    metadata = json.dumps(
                        [
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                            for tc in msg.tool_calls
                        ]
                    )
                if msg.tool_call_id:
                    tool_name = msg.tool_call_id

                rows.append((msg_id, user_id, chat_session_id, msg.role, encrypted_content, tool_name, metadata))

            async with db_session(self.config) as db:
                # Ensure chat session row exists (upsert)
                if chat_session_id:
                    await db.execute(
                        "INSERT OR IGNORE INTO agent_chat_sessions (id, user_id) VALUES (?, ?)",
                        (chat_session_id, user_id),
                    )
                await db.executemany(
                    "INSERT INTO agent_messages (id, user_id, chat_session_id, role, content, tool_name, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                # Update message count + auto-set title from first user message
                if chat_session_id:
                    await db.execute(
                        "UPDATE agent_chat_sessions SET message_count = "
                        "(SELECT COUNT(*) FROM agent_messages WHERE chat_session_id = ?) "
                        "WHERE id = ?",
                        (chat_session_id, chat_session_id),
                    )
                    # Set title from first user message if still untitled
                    user_msgs = [m for m in all_new_messages if m.role == "user"]
                    if user_msgs:
                        first_text = user_msgs[0].content[:50]
                        await db.execute(
                            "UPDATE agent_chat_sessions SET title = ? "
                            "WHERE id = ? AND (title IS NULL OR title = '')",
                            (first_text, chat_session_id),
                        )
                await db.commit()

            # Record and return the final assistant message content
            final = all_new_messages[-1]
            content = final.content or ""
            if not content.strip():
                content = "I wasn't able to generate a response. Please try again."
            await recorder.record_final_response(content)

            # Fire work summary for direct mode (with model attribution)
            from lazyclaw.runtime.summary import build_work_summary
            from lazyclaw.llm.pricing import calculate_cost

            _models_used = []
            _total_cost = 0.0
            _routing = self.eco_router.last_routing if self.eco_router else None
            if _routing:
                _models_used.append((_routing.display_name, _routing.icon, _routing.is_local))
                _total_cost = calculate_cost(
                    _routing.model,
                    _session_tokens // 2,  # approximate split
                    _session_tokens - _session_tokens // 2,
                )

            _direct_summary = build_work_summary(
                start_time=_start_time,
                llm_calls=iteration + 1,
                tools_used=_all_tools_used,
                specialists=[],
                total_tokens=_session_tokens,
                user_message=message,
                response=content,
                models_used=_models_used,
                total_cost=_total_cost,
            )
            await cb.on_event(AgentEvent(
                "work_summary", "Task complete",
                {"summary": _direct_summary},
            ))

        finally:
            # Safety net: if this turn was bound to a task (via the
            # [TASK_REMINDER:<id>] prefix) and the agent ended without calling
            # complete_task or fail_task, auto-fail the row so LazyBrain and
            # the tasks table stop lying about outcome.  Only auto-fails —
            # never auto-completes (preserves graph honesty).
            if _bound_task_id and not (
                {"complete_task", "fail_task"} & set(_all_tools_used)
            ):
                try:
                    from lazyclaw.tasks.store import fail_task as _fail_task
                    await _fail_task(
                        self.config,
                        user_id,
                        _bound_task_id,
                        error="agent exited without marking",
                    )
                    logger.info(
                        "TAOR safety net: auto-failed bound task %s "
                        "(user=%s) — agent turn ended without "
                        "complete_task/fail_task",
                        _bound_task_id, user_id,
                    )
                except Exception:
                    logger.debug(
                        "auto-fail of bound task %s failed",
                        _bound_task_id, exc_info=True,
                    )

            # ALWAYS mark foreground task done/failed in TeamLead
            if self._team_lead and _fg_task_id:
                if content:
                    self._team_lead.complete(
                        _fg_task_id, content[:100], result_full=content,
                    )
                else:
                    self._team_lead.fail(_fg_task_id, "Post-loop error")

            await cb.on_event(AgentEvent(
                "done", "Response ready",
                {
                    "model_used": _last_model_used,
                    "fallback_reason": _last_fallback_reason,
                },
            ))

            # Clean up per-message skills to avoid stale callback references
            if _delegate_registered and self.registry:
                self.registry.unregister("delegate")
            if _dispatch_registered and self.registry:
                self.registry.unregister("dispatch_subagents")

        # LazyBrain post-processing (fire-and-forget).  Both calls swallow
        # their own errors — we never block the reply on PKM bookkeeping.
        if content:
            try:
                from lazyclaw.runtime.wikilink_injector import inject as _lb_inject
                content = await _lb_inject(self.config, user_id, content)
            except Exception:
                logger.debug("wikilink_injector failed", exc_info=True)

            try:
                from lazyclaw.lazybrain.auto_capture import (
                    capture_text_with_llm as _lb_capture_llm,
                )
                from lazyclaw.llm.eco_router import EcoRouter as _EcoRouter

                _eco = _EcoRouter(self.config, self.router)
                await _lb_capture_llm(
                    self.config,
                    user_id,
                    message,
                    _eco,
                    source="chat",
                )
            except Exception:
                logger.debug("lazybrain auto_capture failed", exc_info=True)

        return content
