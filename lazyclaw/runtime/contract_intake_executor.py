"""Auto-setup the execution stack for a freshly-answered Upwork contract goal.

Runs when a ``Goal`` with ``account_slug='upwork'`` transitions from
``AWAITING_USER_INFO`` to ``EXECUTING`` (i.e. every checklist question
has been answered). The Goal Executor's ``_dispatch`` method looks up
this module via ``_DEFAULT_DISPATCH_BY_SLUG`` and invokes
:func:`dispatch_contract_intake`.

Composition (web_monitoring path — covers the James Blue contract and
every gig-platform watcher of the same shape):

  1. Worker LLM parses the user's free-text answers into a structured
     ``_ContractSetup`` (URL, host, creds blob, city, accept text,
     notify channel, poll cadence, auto-accept rule). Ollama-down
     fallback marks the goal blocked instead of crashing.
  2. ``vault_set(<slug>_credentials)`` — single encrypted blob; the
     downstream browser-template player parses it at use-time.
  3. ``add_live_host(host)`` — extends the per-user live-browser
     watcher list so the watcher framework polls through the user's
     signed-in Brave (PR 1).
  4. ``register_account(slug, friendly_name, primary_domain)`` —
     isolated Chromium profile dir for the platform.
  5. ``watch_site(...)`` — registers the listings-page watcher via
     the existing skill (zero-LLM polling).
  6. ``create_template(name=f'{slug}_accept', playbook=<answer text>)``
     — stub template. The existing "auto-save on success" path will
     enrich it with concrete selectors after the user demonstrates
     one successful accept.
  7. ``lazybrain.notes.save_note(...)`` — setup-doc note that lists
     the platform, vault key NAMES only (never values), watcher cron,
     template name, accept-action text, deadline hint. Tagged
     ``contract``, ``<slug>``, ``setup-doc``.
  8. ``escalate_to_human(...)`` — "Open {url} and log in, reply DONE
     when you're ready" — blocks up to 1h. The login itself happens
     in the user's live Brave (the registered profile dir).
  9. Mark every Goal step ``done`` and call ``mark_done`` to land in
     terminal DONE state.

Failure handling:
  - Any individual composition step that raises is logged + recorded
    on the matching Goal step as ``failed``, but later steps still
    run. The Goal lands in ``BLOCKED`` so the user can re-trigger via
    ``execute_contract_intake_setup(goal_id=...)``.
  - If the answer parser returns None (Ollama down), the goal goes
    BLOCKED with a clear ``blocked_on`` message; nothing is provisioned.

For non-web_monitoring work types, the executor saves the setup-doc
note + sets the goal DONE without provisioning a watcher. The user
gets a Telegram message telling them what was captured + a hint to
manually run the relevant skills.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from lazyclaw.config import Config
from lazyclaw.runtime.goal_executor import (
    Goal,
    GoalExecutor,
    GoalStatus,
    register_default_dispatch,
)

logger = logging.getLogger(__name__)


# ── Dataclass for parsed setup ──────────────────────────────────────


@dataclass(frozen=True)
class _ContractSetup:
    """Structured view of the user's free-text answers.

    Every field is best-effort. ``platform_host`` is the only one that
    MUST be derivable — without it there's nothing to watch. Missing
    optional fields just mean the corresponding composition step gets
    skipped (logged, not failed).
    """
    platform_url: str | None
    platform_host: str | None
    credentials_text: str
    city: str | None
    accept_action_text: str
    notify_channel: str
    poll_seconds: int
    auto_accept: bool
    agent_location: str  # "local" | "remote" | "unspecified"
    twofa_method: str | None  # "sms" | "app" | "email" | None


# ── Parser prompt ───────────────────────────────────────────────────


_PARSE_PROMPT = """The user has answered onboarding questions for a freelance contract.
Parse their answers into structured fields. Use null for missing data; never invent.

Contract title: {title}
Questions and answers (Q-A pairs):
{qa_block}

Output STRICT JSON, no prose, no fence:
{{
  "platform_url": "https://..." or null,
  "credentials_text": "verbatim copy of the user's credential answer; empty string if none",
  "city": "the city/region filter" or null,
  "accept_action_text": "verbatim copy of the user's accept-action answer; empty string if not described",
  "notify_channel": "telegram" or "pushover" or "sms" or "email" or "imessage" or "unspecified",
  "poll_seconds": integer (default 60),
  "auto_accept": true or false,
  "agent_location": "local" or "remote" or "unspecified",
  "twofa_method": "sms" or "app" or "email" or null
}}"""


# ── Public dispatch entry point ─────────────────────────────────────


async def dispatch_contract_intake(
    goal: Goal,
    *,
    config: Config | None = None,
    registry: Any | None = None,
) -> str:
    """Run the auto-setup composition for an Upwork contract goal.

    Returns a Telegram-ready summary. The Goal Executor's ``_dispatch``
    calls this with no kwargs, so ``config`` and ``registry`` resolve
    via the module-level fallbacks. Direct callers (tests, manual
    skill invocations) can inject specific dependencies.
    """
    config = config or _resolve_default_config()
    registry = registry or _resolve_default_registry()

    logger.debug(
        "[intake] dispatch_contract_intake entry goal=%s user=%s "
        "account_slug=%s status=%s",
        goal.id, goal.user_id, goal.account_slug, goal.status.value,
    )

    if config is None:
        logger.warning(
            "[intake] dispatch rejected: config unavailable goal=%s",
            goal.id,
        )
        return "Cannot execute intake — config unavailable."
    # Argument-shape checks first — these are validation failures that
    # don't need a registry to report. The registry-availability guard
    # comes AFTER so a caller probing this function with a bad slug or
    # bad status still gets the expected, specific error message.
    if goal.account_slug != "upwork":
        logger.debug(
            "[intake] dispatch rejected: account_slug=%s not 'upwork' "
            "goal=%s",
            goal.account_slug, goal.id,
        )
        return (
            f"Goal {goal.id} has account_slug={goal.account_slug!r}; "
            "contract_intake_executor only handles 'upwork'."
        )
    if goal.status != GoalStatus.EXECUTING:
        logger.debug(
            "[intake] dispatch rejected: status=%s not EXECUTING goal=%s",
            goal.status.value, goal.id,
        )
        return (
            f"Goal {goal.id} is in {goal.status.value}; executor expects "
            "EXECUTING. Did the answers complete? Call answer_goal_questions "
            "for any remaining."
        )
    if registry is None:
        # Without a registry we can't resolve watch_site /
        # lazybrain_save_note / escalate_to_human. Mark the goal BLOCKED
        # with a clear reason so the user can re-trigger via
        # ``execute_contract_intake_setup`` after the runtime is fully
        # wired. Returning early avoids the AttributeError surfaced by
        # ``_step_register_watcher`` when it does ``registry.get(...)``.
        logger.warning(
            "[intake] dispatch rejected: registry unavailable goal=%s",
            goal.id,
        )
        await _mark_blocked(
            config, goal,
            "Skill registry unavailable — runtime not fully initialized. "
            "Restart lazyclaw and call execute_contract_intake_setup("
            f"goal_id='{goal.id}') to retry.",
        )
        return (
            f"⚠️ Goal {goal.id} BLOCKED — skill registry unavailable. "
            "Restart lazyclaw, then re-run "
            f"`execute_contract_intake_setup(goal_id='{goal.id}')`."
        )

    work_type = _parse_work_type(goal)
    logger.info(
        "[intake] work_type classified goal=%s work_type=%s",
        goal.id, work_type,
    )
    if work_type != "web_monitoring":
        # Other types just get the setup-doc note + mark DONE.
        return await _handle_non_monitor(goal, config, registry, work_type)

    setup = await _parse_setup_from_answers(config, goal)
    if setup is None or not setup.platform_host:
        logger.warning(
            "[intake] setup parse failed or missing platform_host "
            "goal=%s parsed=%s",
            goal.id, setup is not None,
        )
        await _mark_blocked(
            config, goal,
            "Could not parse platform URL from answers — re-trigger with "
            "execute_contract_intake_setup(goal_id=...) after confirming "
            "the platform-URL answer is a real URL.",
        )
        return (
            f"⚠️ Goal {goal.id} BLOCKED — parser couldn't extract the "
            "platform URL from your answers. Edit the answer (or re-run "
            "answer_goal_questions) and the executor will retry."
        )

    slug = _slug_from_host(setup.platform_host)
    logger.debug(
        "[intake] provisioning starting goal=%s slug=%s",
        goal.id, slug,
    )
    summary_lines: list[str] = [
        f"📦 Contract setup running for {goal.title} (slug=`{slug}`)",
    ]
    failures: list[str] = []

    # 2. Vault credentials
    await _step_vault(
        config, goal.user_id, slug, setup, summary_lines, failures,
    )

    # 3. Live-browser host
    await _step_live_host(
        config, goal.user_id, setup.platform_host, summary_lines, failures,
    )

    # 4. Browser account
    await _step_register_account(
        config, goal.user_id, slug, goal.title,
        setup.platform_host, summary_lines, failures,
    )

    # 5. Watcher (with slug so the Telegram push gets the ✅ Accept button)
    await _step_register_watcher(
        registry, goal.user_id, setup, slug, summary_lines, failures,
    )

    # 6. Browser template
    await _step_save_template(
        config, goal.user_id, slug, setup, summary_lines, failures,
    )

    # 7. LazyBrain setup-doc
    await _step_save_note(
        registry, goal.user_id, goal, slug, setup, summary_lines, failures,
    )

    # 8. Escalate — block on user login
    await _step_escalate_login(
        registry, goal.user_id, slug, setup, summary_lines, failures,
    )

    # 9. Land the goal
    executor = GoalExecutor(config)
    if failures:
        logger.warning(
            "[intake] provisioning finished with %d failure(s) goal=%s "
            "slug=%s — marking BLOCKED",
            len(failures), goal.id, slug,
        )
        await executor.mark_blocked(
            goal.user_id, goal.id,
            reason="; ".join(failures)[:500],
        )
        summary_lines.append(
            f"\n⚠️ {len(failures)} step(s) failed — goal BLOCKED. Re-run "
            f"execute_contract_intake_setup(goal_id='{goal.id}') after fixing."
        )
    else:
        try:
            # Mark all steps done so the goal lands in DONE state.
            for idx, step in enumerate(goal.plan):
                await executor.mark_step(
                    goal.user_id, goal.id, idx,
                    status="done",
                    action="contract_intake_executor",
                )
            await executor.mark_done(goal.user_id, goal.id)
            logger.info(
                "[intake] provisioning succeeded goal=%s slug=%s -> done",
                goal.id, slug,
            )
            summary_lines.append(f"\n✅ Goal {goal.id} DONE — every step provisioned.")
        except Exception as exc:
            logger.exception(
                "[intake] failed to land goal in DONE state goal=%s "
                "slug=%s error_type=%s",
                goal.id, slug, type(exc).__name__,
            )
            summary_lines.append(
                f"\n⚠️ Setup succeeded but couldn't transition goal to DONE: {exc}"
            )

    return "\n".join(summary_lines)


# ── Composition steps ───────────────────────────────────────────────


async def _step_vault(
    config, user_id, slug, setup, summary, failures,
) -> None:
    if not setup.credentials_text.strip():
        logger.debug("[intake] step=vault slug=%s status=skip (no creds)", slug)
        summary.append("  · vault: SKIP (no credentials provided)")
        return
    try:
        from lazyclaw.crypto.vault import set_credential
        key = f"{slug}_credentials"
        await set_credential(config, user_id, key, setup.credentials_text)
        logger.debug("[intake] step=vault slug=%s status=ok", slug)
        summary.append(f"  ✓ vault: stored `{key}` (value redacted)")
    except Exception as exc:
        logger.exception(
            "[intake] step=vault slug=%s status=failed error_type=%s",
            slug, type(exc).__name__,
        )
        failures.append(f"vault: {exc}")
        summary.append(f"  ✗ vault: {exc}")


async def _step_live_host(
    config, user_id, host, summary, failures,
) -> None:
    try:
        from lazyclaw.browser.browser_settings import add_live_host
        hosts = await add_live_host(config, user_id, host)
        logger.debug(
            "[intake] step=live_host host=%s status=ok count=%d",
            host, len(hosts),
        )
        summary.append(
            f"  ✓ live_host: added `{host}` (user list now {len(hosts)})"
        )
    except Exception as exc:
        logger.exception(
            "[intake] step=live_host host=%s status=failed error_type=%s",
            host, type(exc).__name__,
        )
        failures.append(f"live_host: {exc}")
        summary.append(f"  ✗ live_host: {exc}")


async def _step_register_account(
    config, user_id, slug, friendly_name, host, summary, failures,
) -> None:
    try:
        from lazyclaw.browser.browser_settings import register_account
        await register_account(
            config, user_id, slug, friendly_name[:50] or slug, host,
        )
        logger.debug(
            "[intake] step=browser_account slug=%s status=ok", slug,
        )
        summary.append(f"  ✓ browser_account: registered `{slug}` -> {host}")
    except Exception as exc:
        logger.exception(
            "[intake] step=browser_account slug=%s status=failed "
            "error_type=%s",
            slug, type(exc).__name__,
        )
        failures.append(f"browser_account: {exc}")
        summary.append(f"  ✗ browser_account: {exc}")


async def _step_register_watcher(
    registry, user_id, setup, slug, summary, failures,
) -> None:
    if registry is None:
        logger.debug("[intake] step=watcher slug=%s status=skip (no registry)", slug)
        failures.append("watcher: registry unavailable")
        summary.append("  ✗ watcher: registry unavailable")
        return
    watch_skill = registry.get("watch_site")
    if watch_skill is None:
        logger.debug(
            "[intake] step=watcher slug=%s status=skip (watch_site not registered)",
            slug,
        )
        failures.append("watcher: watch_site skill not registered")
        summary.append("  ✗ watcher: watch_site skill not registered")
        return
    try:
        check_min = max(1, setup.poll_seconds // 60)
        result = await watch_skill.execute(user_id, {
            "url": setup.platform_url,
            "what_to_watch": (
                f"new items in {setup.city}" if setup.city
                else "new items in the listings page"
            ),
            "check_interval_minutes": check_min,
            "duration_hours": 24 * 30,  # 30 days; renewable via NL
            # Wires the watcher push to the ✅ Accept button (PR 5).
            "accept_template_slug": slug,
        })
        logger.debug(
            "[intake] step=watcher slug=%s status=ok interval_min=%d",
            slug, check_min,
        )
        summary.append(f"  ✓ watcher: registered ({result[:120]})")
    except Exception as exc:
        # Log by slug, not the raw platform_url (may carry query-string
        # PII) — slug is the normalized, already-non-sensitive host id.
        logger.exception(
            "[intake] step=watcher slug=%s status=failed error_type=%s",
            slug, type(exc).__name__,
        )
        failures.append(f"watcher: {exc}")
        summary.append(f"  ✗ watcher: {exc}")


async def _step_save_template(
    config, user_id, slug, setup, summary, failures,
) -> None:
    if not setup.accept_action_text.strip():
        logger.debug(
            "[intake] step=template slug=%s status=skip (no accept action)",
            slug,
        )
        summary.append("  · template: SKIP (no accept action described)")
        return
    try:
        from lazyclaw.browser import templates as tpl_store
        playbook = (
            f"Accept the matched item on {setup.platform_host}.\n\n"
            f"User-described action:\n{setup.accept_action_text}\n\n"
            "Confirm before any irreversible click — call "
            "request_user_approval('Confirm accept') first time."
        )
        tpl = await tpl_store.create_template(
            config, user_id,
            name=f"{slug}_accept",
            playbook=playbook,
            setup_urls=[setup.platform_url] if setup.platform_url else None,
            checkpoints=["Confirm accept"],
            watch_url=setup.platform_url,
        )
        logger.debug(
            "[intake] step=template slug=%s status=ok template_id=%s",
            slug, str(tpl.get("id", ""))[:8],
        )
        summary.append(
            f"  ✓ template: saved `{slug}_accept` (id {tpl['id'][:8]}) — "
            "will auto-enrich on first successful accept"
        )
    except Exception as exc:
        logger.exception(
            "[intake] step=template slug=%s status=failed error_type=%s",
            slug, type(exc).__name__,
        )
        failures.append(f"template: {exc}")
        summary.append(f"  ✗ template: {exc}")


async def _step_save_note(
    registry, user_id, goal, slug, setup, summary, failures,
) -> None:
    if registry is None:
        logger.debug("[intake] step=setup_doc slug=%s status=skip (no registry)", slug)
        failures.append("setup_doc: registry unavailable")
        summary.append("  ✗ setup_doc: registry unavailable")
        return
    save_note = registry.get("lazybrain_save_note")
    if save_note is None:
        logger.debug(
            "[intake] step=setup_doc slug=%s status=skip (skill not registered)",
            slug,
        )
        failures.append("setup_doc: lazybrain_save_note skill not registered")
        summary.append("  ✗ setup_doc: lazybrain_save_note skill not registered")
        return
    body = _compose_setup_doc(goal, slug, setup)
    try:
        await save_note.execute(user_id, {
            "content": body,
            "title": f"Contract setup: {goal.title}",
            "tags": ["contract", slug, "setup-doc", "owner/agent"],
            "importance": 8,
            "pinned": True,
        })
        logger.debug("[intake] step=setup_doc slug=%s status=ok", slug)
        summary.append("  ✓ setup_doc: saved to LazyBrain (pinned, credentials redacted)")
    except Exception as exc:
        logger.exception(
            "[intake] step=setup_doc slug=%s status=failed error_type=%s",
            slug, type(exc).__name__,
        )
        failures.append(f"setup_doc: {exc}")
        summary.append(f"  ✗ setup_doc: {exc}")


async def _step_escalate_login(
    registry, user_id, slug, setup, summary, failures,
) -> None:
    if registry is None:
        logger.debug("[intake] step=escalate slug=%s status=skip (no registry)", slug)
        summary.append("  · escalate: SKIP (no registry to fire escalate_to_human)")
        return
    escalate = registry.get("escalate_to_human")
    if escalate is None:
        logger.debug(
            "[intake] step=escalate slug=%s status=skip (skill not registered)",
            slug,
        )
        summary.append("  · escalate: SKIP (escalate_to_human not registered)")
        return
    try:
        await escalate.execute(user_id, {
            "context": (
                f"Contract setup ready for {setup.platform_host}. Open "
                f"{setup.platform_url} in your Brave (account profile `{slug}`) "
                "and log in. Reply DONE here when you're logged in so the "
                "watcher can baseline."
            ),
            "suggested_replies": ["DONE", "I need help", "ABORT"],
            "urgency": "business_hours",
            "timeout_seconds": 3600,
        })
        logger.debug("[intake] step=escalate slug=%s status=ok", slug)
        summary.append("  ✓ escalate: login prompt pushed to Telegram")
    except Exception as exc:
        # Escalate failures are NOT setup-blocking — log + note in summary
        # but don't push the goal to BLOCKED. User can still log in
        # manually without the prompt.
        logger.exception(
            "[intake] step=escalate slug=%s status=warn error_type=%s",
            slug, type(exc).__name__,
        )
        summary.append(f"  · escalate: WARN ({exc}) — log in manually")


async def _handle_non_monitor(
    goal, config, registry, work_type,
) -> str:
    """Non-web_monitoring work types: save a doc note + mark DONE."""
    logger.debug(
        "[intake] non-monitor path goal=%s work_type=%s",
        goal.id, work_type,
    )
    summary_lines = [
        f"📝 Contract setup for `{work_type}` work type — note-only path.",
    ]
    body_parts = [
        f"# Contract: {goal.title}",
        f"\nWork type: **{work_type}**",
        f"\nGoal ID: `{goal.id}`",
        "\n## Captured answers",
    ]
    for q, a in goal.answers.items():
        body_parts.append(f"\n### {q}\n{a}")
    body_parts.append(
        "\n---\n\nNo automatic provisioning for this work type yet. "
        "Run the relevant skills manually."
    )

    save_note = registry.get("lazybrain_save_note") if registry else None
    if save_note is not None:
        try:
            await save_note.execute(goal.user_id, {
                "content": "\n".join(body_parts),
                "title": f"Contract setup: {goal.title}",
                "tags": ["contract", work_type, "setup-doc", "owner/agent"],
                "importance": 7,
                "pinned": True,
            })
            logger.debug(
                "[intake] non-monitor setup_doc goal=%s work_type=%s status=ok",
                goal.id, work_type,
            )
            summary_lines.append("  ✓ setup_doc: saved to LazyBrain")
        except Exception as exc:
            logger.exception(
                "[intake] non-monitor setup_doc goal=%s work_type=%s "
                "status=failed error_type=%s",
                goal.id, work_type, type(exc).__name__,
            )
            summary_lines.append(f"  ✗ setup_doc: {exc}")

    # Land the goal regardless
    try:
        executor = GoalExecutor(config)
        for idx, step in enumerate(goal.plan):
            await executor.mark_step(
                goal.user_id, goal.id, idx,
                status="done", action="non-monitor note-only path",
            )
        await executor.mark_done(goal.user_id, goal.id)
        logger.info(
            "[intake] non-monitor goal=%s work_type=%s -> done",
            goal.id, work_type,
        )
        summary_lines.append(f"\n✅ Goal {goal.id} DONE.")
    except Exception as exc:
        logger.exception(
            "[intake] non-monitor failed to land goal goal=%s "
            "work_type=%s error_type=%s",
            goal.id, work_type, type(exc).__name__,
        )
        summary_lines.append(f"\n⚠️ Couldn't land goal: {exc}")

    return "\n".join(summary_lines)


# ── Answer parser ───────────────────────────────────────────────────


async def _parse_setup_from_answers(
    config, goal,
) -> _ContractSetup | None:
    """Worker LLM call → structured _ContractSetup. None on failure."""
    qa_block = "\n".join(
        f"Q: {q}\nA: {a}\n" for q, a in goal.answers.items()
    )
    prompt = _PARSE_PROMPT.format(
        title=goal.title[:200],
        qa_block=qa_block[:4000],
    )
    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter
    except Exception as exc:
        logger.debug(
            "[intake] parse_setup: imports failed goal=%s error_type=%s",
            goal.id, type(exc).__name__,
        )
        return None

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await asyncio.wait_for(
            eco.chat(
                messages=[
                    LLMMessage(role="system", content="You output JSON only."),
                    LLMMessage(role="user", content=prompt),
                ],
                user_id=goal.user_id,
                role=ROLE_WORKER,
            ),
            timeout=6.0,
        )
    except asyncio.TimeoutError:
        logger.debug("[intake] parse_setup: worker LLM timed out goal=%s", goal.id)
        return None
    except Exception as exc:
        logger.debug(
            "[intake] parse_setup: worker LLM call failed goal=%s error_type=%s",
            goal.id, type(exc).__name__,
        )
        return None

    raw = (resp.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Never log the raw LLM response — it's derived from the user's
        # contract answers. Length only.
        logger.debug(
            "[intake] parse_setup: JSON parse failed goal=%s raw_len=%d",
            goal.id, len(raw),
        )
        return None
    if not isinstance(data, dict):
        logger.debug(
            "[intake] parse_setup: parsed JSON not a dict goal=%s type=%s",
            goal.id, type(data).__name__,
        )
        return None
    return _build_setup_from_dict(data)


def _build_setup_from_dict(data: dict) -> _ContractSetup:
    """Coerce + validate the parser's JSON into _ContractSetup."""
    url = _strip_or_none(data.get("platform_url"))
    host = None
    if url:
        host = _normalize_host(urlparse(url).hostname or "")
    creds_text = str(data.get("credentials_text") or "")
    city = _strip_or_none(data.get("city"))
    accept_text = str(data.get("accept_action_text") or "")
    notify = (str(data.get("notify_channel") or "unspecified")).strip().lower()
    if notify not in {"telegram", "pushover", "sms", "email", "imessage", "unspecified"}:
        notify = "unspecified"
    poll_seconds = int(data.get("poll_seconds") or 60)
    if poll_seconds < 5:
        poll_seconds = 5
    if poll_seconds > 3600:
        poll_seconds = 3600
    auto_accept = bool(data.get("auto_accept") or False)
    location = (str(data.get("agent_location") or "unspecified")).strip().lower()
    if location not in {"local", "remote", "unspecified"}:
        location = "unspecified"
    twofa = _strip_or_none(data.get("twofa_method"))
    if twofa is not None and twofa.lower() not in {"sms", "app", "email"}:
        twofa = None
    return _ContractSetup(
        platform_url=url,
        platform_host=host,
        credentials_text=creds_text,
        city=city,
        accept_action_text=accept_text,
        notify_channel=notify,
        poll_seconds=poll_seconds,
        auto_accept=auto_accept,
        agent_location=location,
        twofa_method=twofa,
    )


def _compose_setup_doc(goal, slug, setup) -> str:
    lines = [
        f"# Contract: {goal.title}",
        f"\n**Slug:** `{slug}`",
        f"**Platform:** {setup.platform_url or '(unknown)'}",
        f"**Host:** {setup.platform_host or '(unknown)'}",
        f"**City filter:** {setup.city or '(none)'}",
        f"**Notification channel:** {setup.notify_channel}",
        f"**Poll cadence:** every {setup.poll_seconds}s",
        f"**Auto-accept:** {'yes' if setup.auto_accept else 'no — human approval required'}",
        f"**Agent location:** {setup.agent_location}",
        f"**2FA method:** {setup.twofa_method or '(none specified)'}",
        f"\n**Vault key:** `{slug}_credentials` (value never shown — use `vault_get` if you need to verify)",
        f"**Browser template:** `{slug}_accept`",
        f"\n## Accept action (verbatim from user)\n\n{setup.accept_action_text or '(not described)'}",
        "\n## How this runs",
        f"- Watcher polls `{setup.platform_url}` every {setup.poll_seconds}s through your live Brave (host added to live_hosts).",
        f"- When a new item matches `{setup.city or 'any city'}`, Telegram fires `🔔 ...` with a `✅ Accept` button.",
        "- Tapping accept runs the saved browser template; first run pauses at a `Confirm accept` checkpoint.",
        f"\n## Goal\n\n`{goal.id}` (status when this note was written: {goal.status.value})",
    ]
    return "\n".join(lines)


# ── Tiny helpers ────────────────────────────────────────────────────


def _strip_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _normalize_host(host: str) -> str:
    h = host.lower().strip()
    if h.startswith("www."):
        h = h[4:]
    return h


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug_from_host(host: str) -> str:
    """Convert 'www.task-rabbit.com' → 'taskrabbit_com'.

    Stays within ``profile_resolver.validate_slug``'s [a-z0-9_-]{1,32}
    constraint. Truncates to 32 chars max.
    """
    h = _normalize_host(host)
    slug = _SLUG_RE.sub("_", h).strip("_")
    return (slug or "platform")[:32]


_KNOWN_WORK_TYPES: tuple[str, ...] = (
    "web_monitoring", "data_scraping", "content_creation",
    "code_dev", "customer_support", "other",
)


def _parse_work_type(goal) -> str:
    """Resolve the goal's work-type classification.

    Resolution order:

    1. ``goal.work_type`` — set by ``new_contract_intake`` and threaded
       through ``GoalExecutor.start`` + the plan envelope. This is the
       authoritative source for every row written from 2026-05-16
       onwards.

    2. Legacy fallback: scan ``goal.summary`` for a known token. Older
       goal rows didn't store ``work_type`` as a field; the brain LLM's
       summary occasionally echoed back the hint text ("web_monitoring
       (confidence: high)"). Kept ONLY for backwards compatibility —
       new code paths should always set ``work_type`` upstream.

    3. ``"other"`` if neither source resolves. Triggers the note-only
       path, which is the safer default than auto-provisioning with
       guessed metadata.
    """
    # 1. Structured field — preferred path.
    wt = (getattr(goal, "work_type", None) or "").strip().lower()
    if wt in _KNOWN_WORK_TYPES:
        return wt

    # 2. Legacy prose scan over `goal.summary`. Only fires when the
    # structured field is unset (legacy rows) or unrecognized.
    summary = (goal.summary or "").lower()
    for candidate in _KNOWN_WORK_TYPES:
        if candidate in summary:
            return candidate

    return "other"


async def _mark_blocked(config, goal, reason: str) -> None:
    try:
        executor = GoalExecutor(config)
        await executor.mark_blocked(
            goal.user_id, goal.id, reason=reason,
        )
        logger.debug(
            "[intake] mark_blocked succeeded goal=%s reason_len=%d",
            goal.id, len(reason or ""),
        )
    except Exception:
        logger.exception(
            "[intake] mark_blocked failed goal=%s user=%s",
            goal.id, goal.user_id,
        )


# ── Module-level default config/registry resolution ─────────────────


# These are set by the lazyclaw bootstrap so the registered dispatch
# callback can resolve config + registry without being told them every
# time. ``register_runtime`` is called once at startup; tests can call
# it with fakes.

_DEFAULT_CONFIG: Config | None = None
_DEFAULT_REGISTRY: Any | None = None


def register_runtime(config: Config, registry: Any) -> None:
    """Wire the executor's default config + registry. Idempotent."""
    global _DEFAULT_CONFIG, _DEFAULT_REGISTRY
    _DEFAULT_CONFIG = config
    _DEFAULT_REGISTRY = registry


def _resolve_default_config() -> Config | None:
    return _DEFAULT_CONFIG


def _resolve_default_registry() -> Any | None:
    return _DEFAULT_REGISTRY


# ── Register the dispatch callback at import time ───────────────────


async def _dispatch_entry(goal: Goal) -> None:
    """Thin wrapper called by GoalExecutor._dispatch for upwork goals."""
    result = await dispatch_contract_intake(goal)
    # NOTE: log only the length, never the summary text — it is built
    # from goal.title / setup fields (contract-adjacent user content) and
    # must never land in plaintext production logs.
    logger.info(
        "[intake] dispatch_entry goal=%s result_len=%d",
        goal.id, len(result or ""),
    )


register_default_dispatch("upwork", _dispatch_entry)
