"""new_contract_intake — onboard a new Upwork contract via Goal Executor.

Detects a freshly-awarded contract, parses the brief, identifies which
required inputs are missing, and starts a ``Goal`` so the user gets
ONE batched Telegram ask with every clarifying question at once
(instead of drip-asked turn-by-turn — the Goal Executor's explicit
anti-Chrome-Auto-Browse design).

The skill itself is thin: it composes existing primitives. The state
machine and Q&A persistence live in
:mod:`lazyclaw.runtime.goal_executor`; this module's only job is to
bridge ``mcp_upwork_get_contract_details`` → ``GoalExecutor.start``
with a contract-aware hints payload.

Workflow:
  1. Fetch the contract via ``upwork_get_contract_details`` (MCP bridge).
  2. Worker LLM classifies the work type into a hand-coded enum
     (``web_monitoring`` / ``data_scraping`` / ``content_creation`` /
     ``code_dev`` / ``customer_support`` / ``other``). Graceful Ollama-
     down fallback returns ``other`` so the user still gets ONE
     all-purpose question instead of a hard failure.
  3. Worker LLM pre-extracts known facts from the brief (deadline,
     platform_url, city, credentials_present). Anything unknown
     becomes a question.
  4. Look up ``_REQUIRED_INPUTS[work_type]`` checklist.
  5. Build the hints dict + call ``GoalExecutor.start()``. The brain
     LLM in ``build_fix_plan`` sees ``missing_inputs`` in the hints
     and emits one question per unknown field in a single batch.

PR 3 SCOPE: stop at Goal creation. The Goal sits in
``AWAITING_USER_INFO``; the user answers via the existing
``answer_goal_questions(goal_id, answers)`` NL skill. PR 4 wires the
``contract_intake_executor`` dispatch callback for the post-answers
auto-setup phase.

Also extends PR 2's ``upwork_contract_poll`` cron: instead of printing
a stub "intake contract <url>" hint, the poll skill now calls THIS
skill for every newly-seen contract so onboarding is fully autonomous.
"""

from __future__ import annotations

import asyncio
import json
import logging

from lazyclaw.config import Config
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# ── Required-inputs checklist per work type ──────────────────────────
# These are the questions the Goal Executor batch-asks the user when the
# brief doesn't already provide them. ORDER MATTERS — the brain's
# build_fix_plan emits questions in this order, and the user sees them
# in the same order on Telegram.
#
# Adding a new work type: add the enum value to _VALID_TYPES + the
# checklist here. The classifier prompt will pick it up on its next
# call (the prompt lists the enum from this dict's keys, no hardcoded
# duplication).

_REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "web_monitoring": (
        "Which platform is being monitored? Paste the full URL of the listings page.",
        "Login credentials — username/email + password. We'll store encrypted in the vault and never echo them back.",
        "Which city/cities or geographic filter to apply?",
        "What is the EXACT accept action — which button to click, any confirmation modal, any payment authorization?",
        "Notification channel — Telegram, Apple Push (Pushover), SMS, or email? Which devices?",
        "Where should the agent run — your machine (your IP, local lazyclaw install) or our server (different IP)?",
        "Does the platform 2FA on each login? If yes, how do you receive the code (SMS, app, email)?",
        "Poll cadence — how fast must new items be detected (5s, 30s, 2min)?",
        "Auto-accept rules — accept ANY matching item, or always ping you first for human approval?",
    ),
    "data_scraping": (
        "Source platforms — list each one with its URL.",
        "Login credentials for each platform that needs auth (we vault them).",
        "Output schema — list every column the deliverable must have.",
        "Filter rules per platform (equity%, vacancy, city, tax delinquency, etc.).",
        "Cadence — one-shot delivery or recurring? If recurring, how often?",
        "Delivery format — Excel, Google Sheet, Notion table, or email digest?",
        "Dedup rules — what counts as the same record across sources (e.g. owner-name + address)?",
    ),
    "content_creation": (
        "Brand voice — link to existing samples or describe in 3 sentences.",
        "Topics / content calendar — provided as a list or auto-generated from a theme?",
        "Channels — X, LinkedIn, TikTok, YouTube, newsletter? Which subset?",
        "Review gate — auto-publish each piece or human-approve every one?",
        "Cadence — per-channel posting schedule.",
    ),
    "code_dev": (
        "Repo URL — where does the code live? GitHub, GitLab, somewhere else?",
        "Stack + version constraints (e.g. Python 3.11, Node 20, specific framework versions).",
        "Acceptance criteria — what does 'done' look like? Tests, demo, doc?",
        "Where to deliver — pull request, branch, zip, deployment?",
        "Existing code to read first (links to relevant files / docs)?",
    ),
    "customer_support": (
        "Support channel — email inbox, chat platform, ticketing system?",
        "Login credentials for the channel.",
        "Response SLA — how fast must replies go out?",
        "Escalation rules — which message types need human approval before reply?",
        "Knowledge source — FAQ doc, past tickets, product docs?",
    ),
    "other": (
        "Describe the work in your own words — what does success look like, "
        "what must I do, and what credentials/tools/sites do I need access to?",
    ),
}

_VALID_TYPES: frozenset[str] = frozenset(_REQUIRED_INPUTS.keys())


# ── Worker-LLM prompts ───────────────────────────────────────────────


_CLASSIFY_PROMPT = """You classify freelance contracts into one of these work types:

{enum_lines}

Contract title: {title}
Client: {client}
Rate / type: {rate}
Brief (verbatim):
{brief}

Pick the SINGLE best-fitting type. Output STRICT JSON, no prose, no fence:
{{"work_type": "web_monitoring" or "data_scraping" or ..., "confidence": "high"|"medium"|"low", "reason": "short why"}}"""


_EXTRACT_PROMPT = """Extract specific facts from this freelance contract brief.

Contract brief:
{brief}

Output STRICT JSON with these keys (use null for anything not stated in the brief):
{{"platform_url": null or "https://...",
  "platform_name": null or "TaskRabbit",
  "city": null or "Madrid",
  "deadline_hint": null or "1 week" or "by 2026-06-01",
  "credentials_mentioned": true/false,
  "notification_channel": null or "phone push",
  "accept_action_described": true/false}}

Be strict — only set a field if the brief actually states it. Inferences belong in null."""


# ── Helper dataclasses ───────────────────────────────────────────────


from dataclasses import dataclass


@dataclass(frozen=True)
class _Classification:
    work_type: str
    confidence: str
    reason: str


@dataclass(frozen=True)
class _Extraction:
    platform_url: str | None
    platform_name: str | None
    city: str | None
    deadline_hint: str | None
    credentials_mentioned: bool
    notification_channel: str | None
    accept_action_described: bool


_DEFAULT_EXTRACTION = _Extraction(
    platform_url=None,
    platform_name=None,
    city=None,
    deadline_hint=None,
    credentials_mentioned=False,
    notification_channel=None,
    accept_action_described=False,
)


# ── Skill ────────────────────────────────────────────────────────────


class NewContractIntakeSkill(BaseSkill):
    """Onboard a new Upwork contract via Goal Executor batch-ask."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "new_contract_intake"

    @property
    def description(self) -> str:
        return (
            "Onboard a new Upwork contract: fetch the brief, identify which "
            "required inputs are missing (platform URL, login, city filter, "
            "accept action, notification channel, etc.), and start a Goal "
            "so you get ONE batched Telegram ask with every clarifying "
            "question at once. Answer via "
            "`answer_goal_questions(goal_id, {{q: a, ...}})`. Called "
            "automatically by the survival_contract_intake cron for new "
            "contracts; also callable manually: "
            "'intake contract <upwork-contract-url>'."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "contract_url": {
                    "type": "string",
                    "description": (
                        "Full Upwork contract URL (https://www.upwork.com/...). "
                        "If omitted, the skill returns an error explaining "
                        "how to find it."
                    ),
                },
                "skip_classification": {
                    "type": "boolean",
                    "description": (
                        "Skip the worker-LLM work-type classifier and use "
                        "'other' (the catch-all single-question checklist). "
                        "Useful when Ollama is down. Default false."
                    ),
                },
            },
            "required": ["contract_url"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if self._registry is None:
            return "Error: skill registry not available."
        if self._config is None:
            return "Error: config not available."

        contract_url = (params.get("contract_url") or "").strip()
        if not contract_url:
            return (
                "contract_url is required. Pass the full URL — e.g. "
                "intake contract https://www.upwork.com/nx/wm/contracts/12345"
            )

        skip_classify = bool(params.get("skip_classification") or False)

        # 1. Fetch the contract via the MCP bridge.
        details = await self._fetch_contract_details(user_id, contract_url)
        if isinstance(details, str):
            # Error message — propagate verbatim
            return details

        title = (details.get("title") or "").strip() or "(untitled contract)"
        client = (details.get("client_name") or "").strip() or "(unknown client)"
        rate = (details.get("rate") or details.get("type") or "").strip()
        brief = self._compose_brief_text(details)

        if not brief:
            return (
                f"Contract '{title}' from {client} fetched, but the brief "
                "is empty — nothing to onboard. Open the contract on "
                "Upwork and confirm the description is non-empty, then "
                "re-run the intake."
            )

        # 2. Classify work type (graceful fallback to 'other').
        if skip_classify:
            classification = _Classification(
                work_type="other", confidence="none",
                reason="skip_classification=true",
            )
        else:
            classification = await self._classify_work_type(
                user_id, title, client, rate, brief,
            )

        # 3. Extract known facts from the brief.
        extraction = await self._extract_known_facts(user_id, brief)

        # 4. Look up the required-inputs checklist.
        checklist = _REQUIRED_INPUTS.get(
            classification.work_type, _REQUIRED_INPUTS["other"],
        )

        # 5. Build the hints + open the Goal.
        hints = self._build_hints(
            contract_url=contract_url,
            title=title,
            client=client,
            rate=rate,
            brief=brief,
            classification=classification,
            extraction=extraction,
            checklist=checklist,
        )

        goal = await self._start_goal(
            user_id=user_id,
            title=f"Set up Upwork contract: {title}",
            hints=hints,
            work_type=classification.work_type,
        )
        if isinstance(goal, str):
            return goal  # error message

        # Format the user-facing summary. Always include the goal ID so
        # the user knows how to answer back.
        return self._format_intake_summary(
            goal=goal,
            title=title,
            client=client,
            work_type=classification.work_type,
            classification_confidence=classification.confidence,
        )

    # ── Composition steps ──────────────────────────────────────────

    async def _fetch_contract_details(
        self, user_id: str, contract_url: str,
    ):
        """Return contract dict on success, error-string on failure."""
        get_details = self._find_tool("upwork_get_contract_details")
        if get_details is None:
            return (
                "Cannot fetch contract — mcp-upwork tools not registered. "
                "Make sure the upwork MCP is running and you're logged in "
                "to Upwork in your Brave."
            )
        try:
            raw = await get_details.execute(
                user_id, {"contract_url": contract_url},
            )
        except Exception as exc:
            logger.exception("upwork_get_contract_details raised")
            return f"Failed to fetch contract: {exc}"

        # MCP responses are JSON-string OR dict; tolerate both
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return (
                    f"Contract response from mcp-upwork was not parseable JSON. "
                    f"Raw: {raw[:300]}"
                )
        if isinstance(raw, dict) and "result" in raw and isinstance(raw["result"], dict):
            raw = raw["result"]
        if not isinstance(raw, dict):
            return (
                "Contract response from mcp-upwork had unexpected shape "
                f"(type={type(raw).__name__}). Expected dict; got: {str(raw)[:200]}"
            )
        return raw

    def _compose_brief_text(self, details: dict) -> str:
        """Concatenate every text-y field of the contract into one brief.

        mcp-upwork's contract details can live in different field names
        depending on the page layout (``description`` / ``brief`` /
        ``scope`` / ``message_history``). We coalesce so a layout change
        doesn't silently produce an empty brief.
        """
        parts: list[str] = []
        for key in (
            "description", "brief", "scope", "scope_of_work",
            "offer_text", "summary", "details",
        ):
            v = details.get(key)
            if v and isinstance(v, str):
                parts.append(v.strip())
        # Milestones often carry the substantive deliverables list.
        for m in (details.get("milestones") or []):
            if isinstance(m, dict):
                t = (m.get("title") or "").strip()
                if t:
                    parts.append(f"Milestone: {t}")
        return "\n\n".join(p for p in parts if p)

    async def _classify_work_type(
        self,
        user_id: str,
        title: str,
        client: str,
        rate: str,
        brief: str,
    ) -> _Classification:
        """Worker-LLM call; gracefully falls back to 'other' on failure."""
        enum_lines = "\n".join(
            f"- {wt}: {self._type_blurb(wt)}" for wt in sorted(_VALID_TYPES)
        )
        prompt = _CLASSIFY_PROMPT.format(
            enum_lines=enum_lines,
            title=title[:200],
            client=client[:80],
            rate=(rate or "(not stated)")[:80],
            brief=brief[:3000],
        )
        data = await self._worker_json(user_id, prompt, timeout_s=4.0)
        if not data:
            return _Classification(
                work_type="other", confidence="none",
                reason="classifier unavailable",
            )
        wt = str(data.get("work_type") or "").strip().lower()
        if wt not in _VALID_TYPES:
            return _Classification(
                work_type="other",
                confidence="low",
                reason=f"classifier returned unknown type {wt!r}",
            )
        conf = str(data.get("confidence") or "low").strip().lower()
        if conf not in {"high", "medium", "low"}:
            conf = "low"
        return _Classification(
            work_type=wt,
            confidence=conf,
            reason=str(data.get("reason") or "")[:200],
        )

    async def _extract_known_facts(
        self, user_id: str, brief: str,
    ) -> _Extraction:
        """Worker-LLM extraction; falls back to all-None on failure.

        Returning all-Nones is safe — the Goal Executor's brain LLM
        will simply emit ALL checklist questions because nothing in
        the hints will look pre-filled.
        """
        prompt = _EXTRACT_PROMPT.format(brief=brief[:3000])
        data = await self._worker_json(user_id, prompt, timeout_s=4.0)
        if not data:
            return _DEFAULT_EXTRACTION
        return _Extraction(
            platform_url=_str_or_none(data.get("platform_url")),
            platform_name=_str_or_none(data.get("platform_name")),
            city=_str_or_none(data.get("city")),
            deadline_hint=_str_or_none(data.get("deadline_hint")),
            credentials_mentioned=bool(data.get("credentials_mentioned")),
            notification_channel=_str_or_none(data.get("notification_channel")),
            accept_action_described=bool(data.get("accept_action_described")),
        )

    def _build_hints(
        self,
        *,
        contract_url: str,
        title: str,
        client: str,
        rate: str,
        brief: str,
        classification: _Classification,
        extraction: _Extraction,
        checklist: tuple[str, ...],
    ) -> dict[str, str]:
        """Compose the hints payload for GoalExecutor.start().

        Goal Executor's :func:`_build_enriched_message` formats this dict
        as bullet lines for the brain LLM in :func:`build_fix_plan`. The
        brain reads ``missing_inputs`` and emits one question per item
        — that's how we get the batched ask.
        """
        # Identify which checklist items the extraction already covered.
        # Heuristic: simple keyword match on the question text. Imperfect
        # but biased toward emitting MORE questions (safe — over-asking
        # is annoying but harmless; under-asking ships a broken setup).
        already_known: list[str] = []
        if extraction.platform_url:
            already_known.append(
                f"platform URL (from brief): {extraction.platform_url}"
            )
        if extraction.platform_name and not extraction.platform_url:
            already_known.append(
                f"platform name (from brief, URL still needed): {extraction.platform_name}"
            )
        if extraction.city:
            already_known.append(f"city (from brief): {extraction.city}")
        if extraction.deadline_hint:
            already_known.append(
                f"deadline hint (from brief): {extraction.deadline_hint}"
            )
        if extraction.notification_channel:
            already_known.append(
                f"notification channel (from brief): {extraction.notification_channel}"
            )

        # Filter the checklist by removing items the extraction already
        # covers. Each filter is a SUBSTRING test against the question
        # text — intentionally lenient so partial coverage still trims.
        missing = list(checklist)
        if extraction.platform_url:
            missing = [q for q in missing if "platform" not in q.lower()
                       or "URL" not in q]
        if extraction.city:
            missing = [q for q in missing if "city" not in q.lower()]
        if extraction.notification_channel:
            missing = [q for q in missing if "notification channel" not in q.lower()]

        missing_block = "\n".join(
            f"  {i + 1}. {q}" for i, q in enumerate(missing)
        ) or "  (none — brief covered everything; verify before EXECUTING)"

        return {
            "contract_url": contract_url,
            "client_name": client,
            "rate_or_type": rate or "(not stated)",
            "work_type": (
                f"{classification.work_type} (confidence: "
                f"{classification.confidence})"
            ),
            "contract_brief": brief[:1500],
            "already_known_from_brief": (
                "\n  - ".join([""] + already_known) if already_known
                else "(nothing inferable)"
            ),
            "missing_inputs": "\n" + missing_block,
            "intake_directive": (
                "Emit ONE question per item in `missing_inputs` above. "
                "Order them as listed. Do NOT ask about anything in "
                "`already_known_from_brief`. Use `steps` to draft the "
                "post-answers auto-setup sequence (vault creds, register "
                "browser account, register watcher, save accept template, "
                "send Telegram baseline summary)."
            ),
        }

    async def _start_goal(
        self,
        *,
        user_id: str,
        title: str,
        hints: dict[str, str],
        work_type: str | None = None,
    ):
        """Wrap GoalExecutor.start; returns Goal or error string.

        ``work_type`` is stored as a first-class field on the Goal so the
        contract-intake executor can read it directly instead of scanning
        ``goal.summary`` for a token the brain LLM may not echo back.
        """
        try:
            from lazyclaw.runtime.goal_executor import GoalExecutor
        except Exception as exc:
            logger.exception("GoalExecutor import failed")
            return f"Cannot start goal — GoalExecutor unavailable: {exc}"
        executor = GoalExecutor(self._config)
        try:
            goal = await executor.start(
                user_id=user_id,
                title=title,
                hints=hints,
                account_slug="upwork",
                work_type=work_type,
            )
        except Exception as exc:
            logger.exception("GoalExecutor.start failed")
            return f"Could not start intake goal: {exc}"
        return goal

    # ── Worker-LLM helper ───────────────────────────────────────────

    async def _worker_json(
        self, user_id: str, prompt: str, *, timeout_s: float = 4.0,
    ) -> dict | None:
        """Run a worker-LLM call expecting JSON output. None on failure.

        Mirrors :func:`lazyclaw.tasks.smart_intake.suggest_intake` — the
        ROLE_WORKER router targets the user's local Ollama Gemma 4 E2B
        ($0). Ollama down / timeout / parse error all collapse to None
        so the caller can fall back gracefully.
        """
        try:
            from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
            from lazyclaw.llm.providers.base import LLMMessage
            from lazyclaw.llm.router import LLMRouter
        except Exception as exc:  # pragma: no cover — import-time failure only
            logger.debug("worker imports failed: %s", exc)
            return None

        try:
            paid = LLMRouter(self._config)
            eco = EcoRouter(self._config, paid)
            resp = await asyncio.wait_for(
                eco.chat(
                    messages=[
                        LLMMessage(role="system", content="You output JSON only."),
                        LLMMessage(role="user", content=prompt),
                    ],
                    user_id=user_id,
                    role=ROLE_WORKER,
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.debug("worker LLM timed out after %.1fs", timeout_s)
            return None
        except Exception as exc:
            logger.debug("worker LLM failed: %s", exc)
            return None

        raw = (resp.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("worker JSON parse failed: %s", raw[:200])
            return None
        if not isinstance(data, dict):
            return None
        return data

    # ── Misc helpers ────────────────────────────────────────────────

    def _find_tool(self, suffix: str):
        """MCP-bridge tool lookup by suffix. Same as upwork_inbox_check."""
        if self._registry is None:
            return None
        for tool_info in self._registry.list_mcp_tools():
            func = tool_info.get("function", {})
            tname = (func.get("name") or "").lower()
            if tname.endswith(suffix.lower()) or suffix.lower() in tname:
                tool = self._registry.get(func.get("name", ""))
                if tool is not None:
                    return tool
        return self._registry.get(suffix)

    @staticmethod
    def _type_blurb(work_type: str) -> str:
        return {
            "web_monitoring": "watch a site for new items + 1-click accept",
            "data_scraping": "collect rows from one or more sites into a deliverable",
            "content_creation": "write posts / threads / scripts on a schedule",
            "code_dev": "build or modify software with a concrete acceptance bar",
            "customer_support": "respond to incoming customer messages",
            "other": "anything that doesn't fit the above buckets",
        }.get(work_type, "")

    @staticmethod
    def _format_intake_summary(
        *,
        goal,
        title: str,
        client: str,
        work_type: str,
        classification_confidence: str,
    ) -> str:
        status_label = goal.status.value
        n_questions = len(goal.questions_pending)
        lines = [
            f"📋 Contract intake started — {title}",
            f"   Client: {client}",
            f"   Type: {work_type} (confidence: {classification_confidence})",
            f"   Goal ID: `{goal.id}`",
            f"   Status: {status_label}",
        ]
        if n_questions:
            lines.append("")
            lines.append(
                f"❓ {n_questions} question(s) — please reply via "
                f"`answer_goal_questions(goal_id='{goal.id}', answers={{...}})`:"
            )
            for q in goal.questions_pending:
                lines.append(f"   • {q}")
            lines.append("")
            lines.append(
                "Tip: answer in ONE message — the goal moves to EXECUTING "
                "as soon as every question has an answer."
            )
        else:
            lines.append("")
            lines.append(
                "✅ No clarifying questions — goal moved straight to "
                "EXECUTING. Check progress with "
                f"`goal_status(goal_id='{goal.id}')`."
            )
        return "\n".join(lines)


# ── Module-level utils ───────────────────────────────────────────────


def _str_or_none(v) -> str | None:
    """Coerce JSON value to non-empty string or None."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None
