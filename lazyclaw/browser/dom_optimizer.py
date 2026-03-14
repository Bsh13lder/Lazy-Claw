"""DOM optimization utilities for browser automation.

Extracted from LazyTasker's DOMOptimizer. Provides lightweight page
analysis without full accessibility tree overhead.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# JS to extract only interactive elements (~90% token reduction)
JS_EXTRACT_ACTIONABLE = """
() => {
    const selectors = 'input, button, select, textarea, a[href], ' +
        '[role="button"], [role="link"], [role="tab"], [role="menuitem"], ' +
        '[onclick], [contenteditable]';
    const elements = Array.from(document.querySelectorAll(selectors));
    return elements
        .filter(el => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none'
                && style.visibility !== 'hidden'
                && rect.width > 0
                && rect.height > 0;
        })
        .map((el, idx) => ({
            idx,
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || null,
            text: (el.textContent || '').trim().slice(0, 80),
            placeholder: el.getAttribute('placeholder') || null,
            name: el.getAttribute('name') || null,
            ariaLabel: el.getAttribute('aria-label') || null,
            href: (el.getAttribute('href') || '').slice(0, 120) || null,
            disabled: el.disabled || false,
        }));
}
"""

# JS to get a quick page summary
JS_PAGE_SUMMARY = """
() => {
    const h1 = document.querySelector('h1');
    const forms = document.querySelectorAll('form').length;
    const inputs = document.querySelectorAll('input').length;
    const buttons = document.querySelectorAll('button, [role="button"]').length;
    const links = document.querySelectorAll('a[href]').length;
    const iframes = document.querySelectorAll('iframe').length;
    const alerts = document.querySelectorAll('[role="alert"], .alert, .error, .warning').length;
    const hasLogin = !!(
        document.querySelector('input[type="password"]') ||
        document.querySelector('form[action*="login"]') ||
        document.querySelector('#login, .login, [name="login"]')
    );
    const hasCaptcha = !!(
        document.querySelector('[class*="captcha"], [id*="captcha"], ' +
            'iframe[src*="recaptcha"], iframe[src*="hcaptcha"]')
    );
    return {
        title: document.title || '',
        url: window.location.href,
        h1: h1 ? h1.textContent.trim().slice(0, 120) : null,
        forms, inputs, buttons, links, iframes, alerts,
        hasLogin, hasCaptcha,
    };
}
"""


class DOMOptimizer:
    """Static utilities for lightweight DOM analysis."""

    @staticmethod
    async def extract_actionable(page: Any) -> list[dict]:
        """Get interactive elements only (~90% token reduction vs full DOM).

        Returns list of dicts with: idx, tag, type, text, placeholder,
        name, ariaLabel, href, disabled.
        """
        try:
            return await page.evaluate(JS_EXTRACT_ACTIONABLE)
        except Exception as exc:
            logger.warning("Failed to extract actionable elements: %s", exc)
            return []

    @staticmethod
    async def get_page_summary(page: Any) -> dict:
        """Quick page snapshot: title, url, form/input counts, login/captcha detection."""
        try:
            return await page.evaluate(JS_PAGE_SUMMARY)
        except Exception as exc:
            logger.warning("Failed to get page summary: %s", exc)
            return {"title": "", "url": "", "error": str(exc)}

    @staticmethod
    def detect_changes(current: dict, previous: dict) -> list[str]:
        """Compare page state snapshots, return list of change descriptions."""
        changes: list[str] = []

        if current.get("url") != previous.get("url"):
            changes.append(f"URL changed: {previous.get('url')} → {current.get('url')}")

        if current.get("title") != previous.get("title"):
            changes.append(f"Title changed: {current.get('title')}")

        if current.get("alerts", 0) > previous.get("alerts", 0):
            changes.append("New alert/error detected on page")

        if current.get("hasCaptcha") and not previous.get("hasCaptcha"):
            changes.append("CAPTCHA appeared")

        if current.get("hasLogin") and not previous.get("hasLogin"):
            changes.append("Login form appeared")

        return changes
