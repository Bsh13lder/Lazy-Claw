"""Browser watcher — zero-token page change detection via CDP JavaScript.

Polls open browser tabs using JS extractors. Known sites (WhatsApp, Gmail)
use built-in extractors. Unknown sites get a one-time LLM-generated JS
snippet stored in the job context.

No LLM calls during polling — pure CDP evaluate(). Only triggers the
agent when a change is detected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from lazyclaw.browser.page_reader import (
    JS_EMAIL,
    JS_WHATSAPP,
    _detect_page_type,
)

logger = logging.getLogger(__name__)

# Built-in extractors for known sites (zero LLM cost)
_BUILTIN_EXTRACTORS: dict[str, str] = {
    "whatsapp": JS_WHATSAPP,
    "email": JS_EMAIL,
}

# Generic extractor — hash page text to detect any change
_JS_GENERIC_HASH = """
(() => {
    const sel = ['main', 'article', '[role="main"]', '.content', '#content', 'body'];
    for (const s of sel) {
        const el = document.querySelector(s);
        if (el && el.innerText.trim().length > 50) {
            return el.innerText.trim().substring(0, 3000);
        }
    }
    return document.body?.innerText?.substring(0, 3000) || '';
})()
"""

# Universal sidecar — runs alongside the site-specific extractor.
# Captures global UI signals available on any logged-in upwork-style page
# (top-nav Messages badge + Notifications bell + page path). When the user
# has a same-host tab open but it isn't the exact target path, the
# site-specific extractor returns empty (its selectors don't exist), so
# this sidecar gives us a coarse "something changed" signal even then.
# Falls back gracefully — every field is null when its selector is absent.
#
# document.title is INTENTIONALLY excluded — titles like "WhatsApp (3)" /
# "(5) Inbox · Gmail" flip on every unread badge update and were the main
# source of false-positive watcher fires.
_JS_NAV_SIDECAR = """
(() => {
    const pickBadge = (selectors) => {
        for (const s of selectors) {
            const el = document.querySelector(s);
            if (el) {
                const t = (el.textContent || '').trim();
                if (t) return t;
            }
        }
        return null;
    };
    const messages = pickBadge([
        '[data-test="messages"] [data-test="badge"]',
        '[aria-label*="message" i] .nav-badge',
        '[data-cy="messages"] .nav-badge',
        'a[href*="/messages"] [class*="badge" i]',
        'a[href*="/messages"] sup',
    ]);
    const notifications = pickBadge([
        '[data-test="notifications"] [data-test="badge"]',
        '[aria-label*="notif" i] .nav-badge',
        '[data-cy="notifications"] .nav-badge',
        'button[aria-label*="notif" i] [class*="badge" i]',
        'button[aria-label*="notif" i] sup',
    ]);
    return {
        m: messages,
        n: notifications,
        p: location.pathname,
    };
})()
"""


def _content_hash(text: str) -> str:
    """SHA-256 hash of text for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _is_degraded_result(result) -> bool:
    """True when the site-specific extractor produced NO real content.

    A "degraded" read means the page wasn't actually scraped — the most
    common cause on Cloudflare-protected hosts (upwork.com, linkedin.com)
    or after a layout drift is that the extractor's selectors don't match,
    so ``backend.evaluate()`` returns an empty value. Treating these as a
    real reading poisons the baseline (``last_value``) and makes the
    empty↔populated oscillation re-fire the watcher on every tick.

    Degraded = falsy scalar (None / "" / whitespace-only string) OR an
    empty container ({} / []). A populated dict such as the WhatsApp
    ``{"unread_count": 0, "text": ""}`` is NOT degraded — that's a
    legitimate "nothing unread" reading the downstream comparison already
    handles; only a genuinely empty extraction is degraded.
    """
    if result is None:
        return True
    if isinstance(result, str):
        return not result.strip()
    if isinstance(result, (dict, list, tuple, set)):
        return len(result) == 0
    # Any other non-empty primitive (number, bool True, etc.) is real.
    return not result


def build_watcher_context(
    url: str,
    custom_js: str | None = None,
    check_interval: int = 300,
    expires_at: str | None = None,
    notify_template: str | None = None,
    one_shot: bool = False,
    accept_template_slug: str | None = None,
    on_change_instruction: str | None = None,
) -> str:
    """Build the JSON context blob stored encrypted in agent_jobs.context.

    ``accept_template_slug`` (when set) tells the heartbeat watcher
    push to attach an inline-keyboard ``✅ Accept`` button whose
    callback fires ``run_browser_template(name=f'{slug}_accept')`` —
    that's how contract-intake watchers deliver the
    1-tap-under-3-seconds promise.

    ``on_change_instruction`` is OPT-IN. When unset (the default) the
    watcher only pushes a notification on change — it never drives the
    live Brave, so it can't steal the active tab from a foreground or
    background task. Set it only when the user explicitly wants the agent
    to ACT on changes (e.g. auto-reply). See
    ``heartbeat/watcher_dispatch.decide_on_change_action``.
    """
    page_type = _detect_page_type(url)

    ctx = {
        "url": url,
        "page_type": page_type,
        "custom_js": custom_js,
        "check_interval": check_interval,
        "expires_at": expires_at,
        "notify_template": notify_template,
        "one_shot": one_shot,
        "accept_template_slug": accept_template_slug,
        "on_change_instruction": on_change_instruction,
        "last_value": None,
        "last_check": None,
    }
    return json.dumps(ctx)


async def check_watcher(
    backend,
    context: dict,
    *,
    passive: bool = False,
    user_id: str | None = None,
    job_id: str | None = None,
) -> tuple[bool, str | None, dict]:
    """Run a single watcher check. Returns (changed, notification, updated_context).

    Zero LLM calls — pure JS execution via CDP.

    When ``passive=True`` (the watcher runs against the user's live shared
    Brave) the watcher drives its OWN dedicated parked tab — tracked by
    ``context["anchor_target_id"]`` — and NEVER steals or navigates the user's
    visible tab. The parked tab is created once at the watched URL (a
    background ``Target.createTarget`` in the same signed-in Brave, so it
    shares the session and passes Cloudflare) and re-resolved by target id on
    later polls; it self-heals if the tab was closed. ``user_id``/``job_id``
    let us register the tab in :mod:`lazyclaw.browser.owned_tabs` so the
    foreground excludes it from MRU and the reaper anchors it.

    When ``passive=False`` (a separate headless background browser, not the
    shared Brave) the original host-match / navigate behavior applies — there's
    no foreground tab to collide with on a private instance.
    """
    url = context.get("url", "")
    page_type = context.get("page_type", "auto")
    custom_js = context.get("custom_js")
    last_value = context.get("last_value")
    notify_template = context.get("notify_template")

    # Pick the right extractor
    if custom_js:
        js_code = custom_js
    elif page_type in _BUILTIN_EXTRACTORS:
        js_code = f"({_BUILTIN_EXTRACTORS[page_type]})()"
    else:
        js_code = _JS_GENERIC_HASH

    # Resolve which tab to read.
    created_anchor: str | None = None
    if passive:
        # LIVE shared Brave: use the watcher's OWN parked tab. Selection is by
        # tab id, NEVER by host-match — host-match used to grab whatever tab
        # was on the host (including the user's foreground tab), which is the
        # exact tab-steal we're eliminating.
        anchor_id = context.get("anchor_target_id")
        tabs = await backend.tabs()
        if anchor_id and any(t.id == anchor_id for t in tabs):
            await backend.switch_tab(anchor_id, focus=False)
        elif url:
            # First run, or the parked tab was closed/stale → create our own.
            try:
                created_anchor = await backend.new_tab(url)
                await backend.switch_tab(created_anchor, focus=False)
            except Exception:
                logger.debug(
                    "watcher parked-tab create/switch failed for %s",
                    url, exc_info=True,
                )
                return False, None, context
            if user_id is not None:
                try:
                    from lazyclaw.browser import owned_tabs

                    key = f"watch:{job_id}" if job_id else f"watch:{url}"
                    owned_tabs.set_owned(user_id, key, created_anchor)
                except Exception:
                    logger.debug("owned_tabs register failed", exc_info=True)
            # Give the freshly opened tab a moment to load before extracting.
            import asyncio
            await asyncio.sleep(2)
        else:
            # No anchor and no URL → nothing to poll this tick.
            return False, None, context
    else:
        # Headless background browser (separate instance) — original behavior.
        current_url = await backend.current_url()
        current_host = urlparse(current_url).hostname or ""
        target_host = urlparse(url).hostname or ""

        if target_host and target_host not in current_host:
            tabs = await backend.tabs()
            target_tab = next(
                (t for t in tabs if target_host in (urlparse(t.url).hostname or "")),
                None,
            )
            if target_tab:
                await backend.switch_tab(target_tab.id)
            else:
                # Tab not open — navigate current tab
                await backend.goto(url)
                # Wait for page load
                import asyncio
                await asyncio.sleep(2)

    # WhatsApp sync wait (short, tab is usually loaded)
    if page_type == "whatsapp":
        import asyncio
        for _ in range(5):
            count = await backend.evaluate(
                "(() => document.querySelectorAll('[role=\"row\"]').length)()"
            )
            if count and count > 0:
                break
            await asyncio.sleep(1)

    # Execute JS extractor
    result = await backend.evaluate(js_code)

    # Sidecar — always run a cheap universal nav-badge / page-id check on
    # top of the site-specific extractor. If the picked tab is on the same
    # host but a different path (e.g. /nx/proposals/ instead of the watched
    # /ab/messages/rooms/), the site-specific JS returns empty AND the user
    # never finds out something changed. The sidecar captures the global
    # Messages/Notifications badges plus the path/title so a real change
    # still flips the hash. Add-on only — does NOT replace ``result`` for
    # downstream callers; just augments the comparison value.
    sidecar: dict | None = None
    try:
        raw_sidecar = await backend.evaluate(_JS_NAV_SIDECAR)
        if isinstance(raw_sidecar, dict):
            sidecar = raw_sidecar
    except Exception:
        logger.debug("nav-sidecar evaluate failed", exc_info=True)

    # Degraded-read guard — when the site-specific extractor returned NO
    # real content (Cloudflare challenge / layout drift / wrong-path tab),
    # do NOT let it poison the baseline. Overwriting ``last_value`` with an
    # empty read makes the next (populated) tick look "changed", and the
    # empty↔full flip then re-fires the watcher on every heartbeat — an
    # infinite self-feeding loop. Once we have a prior good baseline we skip
    # this read entirely and return the ORIGINAL context unchanged so
    # ``last_value`` survives and ``last_check`` stays untouched (retry
    # promptly next interval, exactly like the passive no-tab skip above).
    degraded = _is_degraded_result(result)
    if degraded and last_value is not None:
        logger.debug(
            "Watcher degraded read on %s — preserving last good baseline, "
            "skipping (sidecar ignored)", url,
        )
        return False, None, context

    # Normalize result for comparison
    if isinstance(result, dict):
        current_value = json.dumps(result, sort_keys=True)
    else:
        current_value = str(result) if result else ""

    # The sidecar AUGMENTS a real read — it must never independently flip
    # the hash when the primary extractor came back empty. On a degraded
    # read (only reachable here when there is no prior baseline yet — the
    # branch above already returned otherwise) we refuse to fold it in so a
    # nav-badge blip can't masquerade as scraped content.
    if sidecar is not None and not degraded:
        # Fold sidecar into the comparison value so a change in nav badges
        # or page identity also bumps the hash. Sorted-keys keeps the
        # serialization stable across polls.
        current_value = (
            current_value
            + "|nav="
            + json.dumps(sidecar, sort_keys=True)
        )

    # Update context (immutable — new dict)
    new_context = dict(context)
    new_context["last_value"] = current_value
    new_context["last_check"] = datetime.now(timezone.utc).isoformat()
    # Persist a freshly created parked-tab id so the next poll reuses it
    # (saved by the daemon via update_job).
    if created_anchor:
        new_context["anchor_target_id"] = created_anchor

    # First check — just store baseline, no notification
    if last_value is None:
        return False, None, new_context

    # Compare — for WhatsApp, only trigger on unread count change (not timestamp noise)
    changed = False
    if page_type == "whatsapp" and isinstance(result, dict):
        try:
            old = json.loads(last_value)
            old_unread = old.get("unread_count", 0)
            new_unread = result.get("unread_count", 0)
            old_text = old.get("text", "")[:200]
            new_text = result.get("text", "")[:200]
            # Only trigger if unread count changed or top chat message changed
            changed = new_unread != old_unread or new_text != old_text
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse previous watcher value for WhatsApp comparison", exc_info=True)
            changed = current_value != last_value
    else:
        changed = current_value != last_value

    if not changed:
        return False, None, new_context

    # Build notification with DIFF (what's new, not everything)
    notification = _build_notification(
        context, result, notify_template, last_value,
    )

    return True, notification, new_context


def _build_notification(
    context: dict,
    raw_result,
    template: str | None,
    last_value: str | None = None,
) -> str:
    """Build a human-readable notification showing WHAT changed."""
    url = context.get("url", "")
    page_type = context.get("page_type", "auto")

    if template:
        return template

    # WhatsApp — show only chats with new/changed messages
    if page_type == "whatsapp" and isinstance(raw_result, dict):
        new_text = raw_result.get("text", "")
        old_text = ""
        if last_value:
            try:
                old_data = json.loads(last_value)
                old_text = old_data.get("text", "")
            except (json.JSONDecodeError, TypeError):
                logger.warning("Could not parse previous watcher value as JSON; treating as empty")

        # Find new lines (messages that weren't in the previous check)
        old_lines = set(old_text.split("\n"))
        new_lines = new_text.split("\n")
        diff_lines = [ln for ln in new_lines if ln.strip() and ln not in old_lines]

        if diff_lines:
            return "WhatsApp new:\n" + "\n".join(diff_lines[:10])

        unread = raw_result.get("unread_count", 0)
        if unread:
            # Show first chat with unread
            first_chat = new_text.split("\n\n")[0] if new_text else ""
            return f"WhatsApp: {unread} unread\n{first_chat}"

        return f"WhatsApp update:\n{new_text[:300]}"

    if page_type == "email" and isinstance(raw_result, dict):
        count = raw_result.get("email_count", 0)
        text = raw_result.get("text", "")
        # Show just first email
        first = text.split("\n\n")[0] if text else ""
        return f"Email: {count} messages\n{first}"

    # Generic
    host = urlparse(url).hostname or url
    if isinstance(raw_result, dict):
        return f"Change on {host}: {json.dumps(raw_result)[:500]}"
    return f"Change on {host}: {str(raw_result)[:500]}"


def is_watcher_expired(context: dict) -> bool:
    """Check if a watcher has passed its expiration time."""
    expires_at = context.get("expires_at")
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)
        if exp.tzinfo is None:
            from datetime import timezone as tz
            exp = exp.replace(tzinfo=tz.utc)
        return now >= exp
    except (ValueError, TypeError):
        logger.debug("Failed to parse watcher expires_at value", exc_info=True)
        return False


def is_check_due(context: dict) -> bool:
    """Check if enough time has passed since last check.

    Honours ``snoozed_until`` (ISO timestamp) — when the user taps
    ⏰ on a watcher push, the callback writes a future timestamp here
    and we silently skip polls until it elapses. Stale or malformed
    ``snoozed_until`` values are treated as unset, never as "snooze
    forever", so a corrupted ctx can't permanently mute a watcher.
    """
    snoozed_until = context.get("snoozed_until")
    if snoozed_until:
        try:
            snooze = datetime.fromisoformat(snoozed_until)
            if snooze.tzinfo is None:
                from datetime import timezone as tz
                snooze = snooze.replace(tzinfo=tz.utc)
            if datetime.now(timezone.utc) < snooze:
                return False
        except (ValueError, TypeError):
            logger.debug(
                "Failed to parse snoozed_until — treating as unset",
                exc_info=True,
            )

    interval = context.get("check_interval", 300)
    last_check = context.get("last_check")
    if not last_check:
        return True
    try:
        last = datetime.fromisoformat(last_check)
        now = datetime.now(timezone.utc)
        if last.tzinfo is None:
            from datetime import timezone as tz
            last = last.replace(tzinfo=tz.utc)
        return (now - last).total_seconds() >= interval
    except (ValueError, TypeError):
        logger.debug("Failed to parse watcher last_check value, treating as due", exc_info=True)
        return True
