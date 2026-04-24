"""Browser read and open actions — content extraction and navigation.

Extracted from browser_skill.py for maintainability.
"""

from __future__ import annotations

import asyncio
import logging
import random

from lazyclaw.browser.action_errors import (
    RETRY_GIVE_UP,
    RETRY_RE_READ,
    ActionError,
    ActionErrorCode,
)
from lazyclaw.browser.action_verifier import (
    capture_error_text,
    capture_state,
    capture_state_fresh,
)
from lazyclaw.browser.page_reader import run_extractor

from .backends import get_backend, get_cdp_backend, query_to_url, raise_browser_window

logger = logging.getLogger(__name__)


async def action_read(
    user_id: str, params: dict, tab_context, config, snapshot_mgr,
) -> str:
    """Read content from current tab or navigate+read a target."""
    backend = await get_backend(user_id, tab_context)
    target = (params.get("target") or "").strip()

    if target:
        if tab_context:
            nav_url = query_to_url(target)
            if nav_url:
                await backend.goto(nav_url)
                await asyncio.sleep(3)
            else:
                return str(ActionError(
                    code=ActionErrorCode.NOT_FOUND,
                    message=f"Couldn't resolve '{target}' to a URL.",
                    hint="Pass a full URL (https://...) or a known shortcut like 'twitter'.",
                    retry_strategy=RETRY_GIVE_UP,
                ))
        else:
            tab_list = await backend.tabs()
            match = next(
                (t for t in tab_list
                 if target.lower() in t.title.lower()
                 or target.lower() in t.url.lower()),
                None,
            )
            if match:
                await backend.switch_tab(match.id)
            else:
                nav_url = query_to_url(target)
                if nav_url:
                    logger.info("No tab '%s', navigating to %s", target, nav_url)
                    await backend.goto(nav_url)
                    await asyncio.sleep(3)
                else:
                    return str(ActionError(
                        code=ActionErrorCode.NOT_FOUND,
                        message=f"No tab matching '{target}' and couldn't resolve to a URL.",
                        hint="Pass a full URL or use action='open', target='https://...'.",
                        retry_strategy=RETRY_GIVE_UP,
                    ))

    result = await run_extractor(backend)
    title = result.get("title", "")
    url = result.get("url", "")
    text = result.get("text", "")[:2000]
    page_type = result.get("type", "generic")

    summary = f"Tab: {title}\nURL: {url}"
    if page_type == "whatsapp" and result.get("unread_count"):
        summary += f"\nUnread: {result['unread_count']}"
    summary += f"\n\n{text}"

    if url and config:
        try:
            from lazyclaw.browser.site_memory import recall, format_memories_for_context
            memories = await recall(config, user_id, url)
            if memories:
                summary += "\n\n--- Site Knowledge ---\n" + format_memories_for_context(memories)
        except Exception:
            logger.debug("Site memory recall failed (best-effort)", exc_info=True)

    return summary


async def action_open(
    user_id: str, params: dict, tab_context, config,
    snapshot_mgr, verifier, is_background: bool,
) -> str:
    """Open browser and navigate to target."""
    from lazyclaw.browser.page_reader import detect_page_context

    force_visible = params.pop("visible", False)
    target = (params.get("target") or "").strip()
    visible = force_visible or (not target and not is_background)

    if not target:
        await get_backend(user_id, tab_context, visible=visible)
        if visible:
            await raise_browser_window()
            return "Done — browser is open on your screen."
        return "Done — browser ready (headless)."

    nav_url = query_to_url(target)
    if not nav_url:
        return str(ActionError(
            code=ActionErrorCode.NOT_FOUND,
            message=f"Couldn't resolve '{target}' to a URL.",
            hint="Pass a full URL (https://...) or a shortcut like 'twitter'.",
            retry_strategy=RETRY_GIVE_UP,
        ))

    backend = await get_backend(user_id, tab_context, visible=visible)
    if visible:
        await raise_browser_window()

    if not tab_context:
        is_full_url = target.startswith("http://") or target.startswith("https://")
        if not is_full_url:
            try:
                tab_list = await backend.tabs()
                match = next(
                    (t for t in tab_list
                     if target.lower() in t.title.lower()
                     or target.lower() in t.url.lower()
                     or nav_url.split("//")[-1].split("/")[0] in t.url),
                    None,
                )
                if match:
                    await backend.switch_tab(match.id)
                    return await _page_context_summary(
                        backend, snapshot_mgr, f"Switched to: {match.title}", match.url,
                    )
            except Exception as exc:
                logger.debug("Tab switch by title match failed: %s", exc)

    _before_state = None
    try:
        _before_state = await capture_state(backend, snapshot_mgr)
    except Exception as exc:
        logger.debug("Failed to capture pre-navigation state: %s", exc)

    await asyncio.sleep(2)
    try:
        await backend.goto(nav_url)
    except TimeoutError:
        await asyncio.sleep(3)
        await backend.goto(nav_url)

    # Blank page detection
    for _wait in (0.5, 1.0, 2.0, 3.0):
        try:
            _check = await backend.evaluate(
                "(document.body.innerText || '').trim().length > 0 || "
                "document.querySelectorAll('input,button,a,[role=\"button\"]').length > 0"
            )
            if _check:
                break
        except Exception:
            break
        await asyncio.sleep(_wait)
    else:
        try:
            await backend.evaluate("location.reload()")
            await asyncio.sleep(3)
        except Exception as exc:
            logger.debug("Page reload on blank detection failed: %s", exc)

    # Page survey
    try:
        _survey = await backend.evaluate("""(() => {
            const inputs = document.querySelectorAll('input:not([type=hidden]),textarea,select');
            const buttons = document.querySelectorAll('button,[type=submit],[role=button]');
            const links = document.querySelectorAll('a[href]');
            const forms = document.querySelectorAll('form');
            const tables = document.querySelectorAll('table');
            const title = document.title || '';
            const textLen = (document.body.innerText || '').trim().length;
            let pageType = 'CONTENT';
            if (forms.length > 0 && inputs.length >= 2) pageType = 'FORM';
            else if (inputs.length === 2 && title.toLowerCase().includes('login')) pageType = 'LOGIN';
            else if (inputs.length === 2 && document.querySelector('[type=password]')) pageType = 'LOGIN';
            else if (tables.length > 0) pageType = 'TABLE';
            else if (textLen < 50 && inputs.length === 0) pageType = 'BLANK';
            else if (links.length > 20) pageType = 'LIST';
            return `Page: ${pageType} | ${inputs.length} inputs, ${buttons.length} buttons, ${links.length} links, ${forms.length} forms | text: ${textLen} chars`;
        })()""")
        _survey_line = f"[{_survey}]\n" if _survey else ""
    except Exception:
        _survey_line = ""

    result = _survey_line + await _page_context_summary(backend, snapshot_mgr, None, nav_url)

    # Inject site knowledge
    if nav_url and config:
        try:
            from lazyclaw.browser.site_memory import recall, format_memories_for_context
            memories = await recall(config, user_id, nav_url)
            if memories:
                result += "\n--- Site Knowledge ---\n" + format_memories_for_context(memories)
        except Exception:
            logger.debug("Failed to inject site knowledge after navigation", exc_info=True)

    # Post-action verification
    if _before_state is not None:
        try:
            _error_text = await capture_error_text(backend)
            _after_state = await capture_state_fresh(backend, snapshot_mgr)
            _vr = verifier.verify(
                _before_state, _after_state, "open", error_text=_error_text,
            )
            result += f"\n\n{_vr.format('Opened')}"
        except Exception:
            logger.debug("Post-open verification failed", exc_info=True)

    # Record the visit in LazyBrain so the user can see where the agent has
    # been browsing. Fire-and-forget; a PKM failure must not break browsing.
    if nav_url and config:
        try:
            await _record_visit_in_lazybrain(config, user_id, nav_url, backend)
        except Exception:
            logger.debug("lazybrain visit mirror failed", exc_info=True)

    return result


# ── Helpers ────────────────────────────────────────────────────────────


def format_actionable_elements(elements: list[dict], limit: int = 30) -> str:
    """Format extracted actionable elements into a compact text list."""
    hints = []
    for el in elements[:limit]:
        parts = [el.get("tag", "?")]
        if el.get("ariaLabel"):
            parts.append(f'aria-label="{el["ariaLabel"]}"')
        if el.get("text"):
            parts.append(f'"{el["text"][:50]}"')
        if el.get("placeholder"):
            parts.append(f'placeholder="{el["placeholder"]}"')
        if el.get("name"):
            parts.append(f'name="{el["name"]}"')
        if el.get("type"):
            parts.append(f'type={el["type"]}')
        hints.append("  " + " ".join(parts))
    return "\n".join(hints)


async def _page_context_summary(
    backend, snapshot_mgr, heading: str | None = None, url: str | None = None,
) -> str:
    """Take ref-ID snapshot + JS extractor content, return compact summary."""
    from lazyclaw.browser.page_reader import detect_page_context

    ctx = await detect_page_context(backend)
    page_type = ctx.get("page_type", "other")
    landmarks = ctx.get("landmarks", "none detected")
    alerts = ctx.get("alerts", "None")

    page_data = await run_extractor(backend)
    title = page_data.get("title", "") or ctx.get("title", "") or await backend.title()
    page_url = url or page_data.get("url", "") or ctx.get("url", "")
    page_text = page_data.get("text", "")

    header = (
        f"Page: {title}\n"
        f"URL: {page_url}\n"
        f"Type: {page_type}\n"
        f"Key sections: {landmarks}\n"
        f"Alerts: {alerts}"
    )
    parts = [header]
    if heading:
        parts.insert(0, heading)

    if page_text:
        preview = page_text[:1500]
        if len(page_text) > 1500:
            preview += "\n... [truncated]"
        parts.append(f"\n--- Page Content ---\n{preview}")

    try:
        snapshot = await snapshot_mgr.take_snapshot(backend)
        snap_text = snapshot_mgr.format_snapshot(snapshot)
        parts.append(f"\n{snap_text}")
    except Exception:
        logger.debug("Snapshot failed in page_context_summary", exc_info=True)
        try:
            from lazyclaw.browser.dom_optimizer import DOMOptimizer
            elements = await DOMOptimizer.extract_actionable(backend)
            if elements:
                parts.append(
                    "\n--- Actionable Elements ---\n"
                    + format_actionable_elements(elements)
                )
        except Exception as exc:
            logger.debug("DOMOptimizer fallback failed: %s", exc)

    return "\n".join(parts)


async def element_not_found_hint(backend, target: str) -> str:
    """When an element isn't found, return actionable elements for self-correction.

    Prefixes with the structured `[not_found]` error code so the agent can
    branch retry strategy without regexing the prose.
    """
    from lazyclaw.browser.dom_optimizer import DOMOptimizer

    err = ActionError(
        code=ActionErrorCode.NOT_FOUND,
        message=f"Element not found: '{target}'.",
        hint="Use action='snapshot' to see page structure, or try a different selector.",
        retry_strategy=RETRY_RE_READ,
    )

    try:
        elements = await DOMOptimizer.extract_actionable(backend)
        if elements:
            hint_text = format_actionable_elements(elements, limit=25)
            return (
                f"{err}\n"
                f"Here are the interactive elements on the page:\n{hint_text}"
            )
    except Exception as exc:
        logger.debug("DOMOptimizer.extract_actionable failed: %s", exc)
    return str(err)


# Visit tracking ----------------------------------------------------------
#
# Every `browser(action="open")` that actually navigates to a URL is recorded
# as a note in LazyBrain so the user can browse *where* the agent has been
# and *when*. Dedupe within a 30-minute window per domain — consecutive opens
# on the same domain append a bullet to the existing note instead of creating
# a noisy pile of one-line pages.

_VISIT_WINDOW_MIN = 30


async def _record_visit_in_lazybrain(
    config, user_id: str, nav_url: str, backend
) -> None:
    """Append a timestamped visit entry to the per-domain LazyBrain note.

    Fire-and-forget — caller wraps in try/except. Safe to call on every
    successful open.
    """
    from datetime import datetime, timezone, timedelta
    from urllib.parse import urlparse

    parsed = urlparse(nav_url)
    domain = parsed.hostname or nav_url
    if not domain:
        return

    # Best-effort page title (backend.evaluate returns None on failure).
    page_title = ""
    try:
        page_title = await backend.evaluate("document.title") or ""
    except Exception:
        logger.debug("visit title lookup failed", exc_info=True)
    page_title = (page_title or "").strip()[:160]

    now = datetime.now(timezone.utc)
    bullet = f"- {now.strftime('%Y-%m-%d %H:%M UTC')} — [{page_title or nav_url}]({nav_url})"

    from lazyclaw.lazybrain import events as lb_events
    from lazyclaw.lazybrain import store as lb_store

    # Look for an existing visit note for this domain touched in the last
    # _VISIT_WINDOW_MIN minutes. If one exists, append; else create new.
    tag_domain = f"site/{domain}"
    recent = await lb_store.list_notes(config, user_id, tag=tag_domain, limit=3)
    cutoff = now - timedelta(minutes=_VISIT_WINDOW_MIN)
    existing = None
    for note in recent:
        if "visit" not in (note.get("tags") or []):
            continue
        updated_at = note.get("updated_at") or note.get("created_at") or ""
        try:
            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if updated_dt >= cutoff:
            existing = note
            break

    if existing:
        body = (existing.get("content") or "").rstrip() + "\n" + bullet
        updated = await lb_store.update_note(
            config, user_id, existing["id"], content=body,
        )
        if updated:
            lb_events.publish_note_saved(
                user_id, updated["id"], updated.get("title"),
                updated.get("tags"), source="site-memory",
            )
        return

    # No recent note — create a new one. Title summarises the domain.
    title = f"Visits: {domain}"
    body = (
        f"**Browser visits to `{domain}`** — auto-recorded by the agent. "
        f"Each bullet is one landing on a URL.\n\n{bullet}"
    )
    note = await lb_store.save_note(
        config, user_id,
        content=body,
        title=title,
        tags=[
            "visit", "site-memory", "auto", "owner/agent",
            tag_domain,
        ],
        importance=4,
    )
    lb_events.publish_note_saved(
        user_id, note["id"], note.get("title"),
        note.get("tags"), source="site-memory",
    )
