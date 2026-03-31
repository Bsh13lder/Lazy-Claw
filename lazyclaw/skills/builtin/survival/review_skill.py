"""Review work before submitting to a client — quality gate."""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_CODE_KEYWORDS = frozenset({
    "api", "code", "script", "function", "bug", "fix", "build",
    "develop", "implement", "backend", "frontend", "endpoint",
    "database", "migration", "test", "deploy", "refactor",
    "software", "program", "app", "website", "server",
})


class ReviewDeliverableSkill(BaseSkill):
    """Review work before submitting to a client.

    Code tasks: Claude Code MCP reads files, runs tests, auto-fixes (max 3 rounds).
    Non-code tasks: LLM text review via gpt-5-mini.
    """

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "review_deliverable"

    @property
    def description(self) -> str:
        return (
            "Review work before submitting to a client. "
            "For code: reads files, runs tests, checks requirements. "
            "Auto-fixes issues (max 3 rounds). "
            "Usage: 'review my work' or 'check deliverable before submit'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "job_description": {
                    "type": "string",
                    "description": "The original job requirements/description",
                },
                "deliverable_summary": {
                    "type": "string",
                    "description": "What was built/written (summary or file paths)",
                },
                "auto_fix": {
                    "type": "boolean",
                    "description": "Auto-fix issues using Claude Code (default true)",
                    "default": True,
                },
            },
            "required": ["job_description"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        job_desc = params.get("job_description", "")
        deliverable = params.get("deliverable_summary", "")
        auto_fix = params.get("auto_fix", True)

        is_code = any(k in job_desc.lower() for k in _CODE_KEYWORDS)

        if is_code:
            return await self._review_code(user_id, job_desc, deliverable, auto_fix)
        return await self._review_text(user_id, job_desc, deliverable)

    async def _review_code(
        self, user_id: str, job_desc: str, deliverable: str, auto_fix: bool,
    ) -> str:
        claude_tool = self._find_claude_tool()
        if claude_tool is None:
            return await self._review_text(user_id, job_desc, deliverable)

        desc = job_desc[:500]
        deliv = deliverable[:500] if deliverable else ""

        review_prompt = (
            "You are a strict code reviewer checking freelance deliverables.\n\n"
            f"Job requirements:\n{desc}\n\n"
            f"{'Deliverable info: ' + deliv if deliv else ''}\n\n"
            "Do the following:\n"
            "1. Read ALL files that were created or modified for this job\n"
            "2. Run any existing tests (pytest, npm test, go test, etc.)\n"
            "3. Check: does the code meet EVERY requirement?\n"
            "4. Check: any bugs, edge cases, missing error handling?\n"
            "5. Check: security issues (injection, auth bypass, data leaks)?\n"
            "6. Check: code quality — would a senior dev approve this?\n\n"
            "Score: PASS / NEEDS_WORK / FAIL\n"
            "If not PASS, list EXACT issues with file paths and line numbers.\n"
            "If tests fail, show the failure output."
        )

        review_text = await self._call_claude(claude_tool, user_id, review_prompt)

        if "PASS" in review_text.upper():
            return f"Code Review: PASS\n\n{review_text}\n\nReady to submit to client."

        if not auto_fix:
            return (
                f"Code Review: NEEDS WORK\n\n{review_text}\n\n"
                "Fix the issues manually, then run review again."
            )

        # Auto-fix loop (max 3 rounds)
        for attempt in range(1, 4):
            fix_prompt = (
                f"Fix ALL issues found in this code review:\n\n"
                f"{review_text}\n\nAfter fixing, run all tests to verify."
            )
            await self._call_claude(claude_tool, user_id, fix_prompt)
            review_text = await self._call_claude(claude_tool, user_id, review_prompt)

            if "PASS" in review_text.upper():
                rounds = "round" if attempt == 1 else "rounds"
                return (
                    f"Code Review: PASS (after {attempt} fix {rounds})\n\n"
                    f"{review_text}\n\nReady to submit to client."
                )

        return (
            f"Still has issues after 3 fix rounds:\n\n{review_text}\n\n"
            "Review manually before submitting."
        )

    async def _review_text(
        self, user_id: str, job_desc: str, deliverable: str,
    ) -> str:
        from lazyclaw.llm.eco_router import EcoRouter
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter

        desc = job_desc[:500]
        deliv = deliverable[:500] if deliverable else "(no deliverable summary provided)"

        review_prompt = (
            "You are a strict client reviewing freelance work.\n\n"
            f"Job requirements:\n{desc}\n\n"
            f"Deliverable:\n{deliv}\n\n"
            "Review for:\n"
            "1. Does it meet ALL requirements?\n"
            "2. Completeness: anything missing?\n"
            "3. Quality: professional-grade?\n"
            "4. Would you pay full price for this?\n\n"
            "Score: PASS / NEEDS_WORK / FAIL\n"
            "If not PASS, list exact issues."
        )

        try:
            paid_router = LLMRouter(self._config)
            eco = EcoRouter(self._config, paid_router)
            response = await eco.chat(
                messages=[
                    LLMMessage(role="system", content="You are a strict quality reviewer."),
                    LLMMessage(role="user", content=review_prompt),
                ],
                user_id=user_id,
            )
            text = response.content
        except Exception as exc:
            logger.warning("Text review LLM call failed: %s", exc)
            return "Could not complete review — LLM call failed. Try again."

        if "PASS" in text.upper():
            return f"Review: PASS\n\n{text}\n\nReady to submit."
        return f"Review: NEEDS WORK\n\n{text}\n\nFix issues before submitting."

    def _find_claude_tool(self):
        if self._registry is None:
            return None
        for tool_info in self._registry.list_mcp_tools():
            func = tool_info.get("function", {})
            tname = func.get("name", "").lower()
            if "claude" in tname and "code" in tname:
                tool = self._registry.get(func.get("name", ""))
                if tool is not None:
                    return tool
        return None

    async def _call_claude(self, tool, user_id: str, prompt: str) -> str:
        try:
            result = await tool.execute(user_id, {"prompt": prompt})
            return result if isinstance(result, str) else str(result)
        except Exception as exc:
            logger.warning("Claude Code MCP call failed: %s", exc)
            return f"Claude Code error: {exc}"
