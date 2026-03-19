"""SmartBrowser — LLM-driven browser automation using PageReader + DOM Optimizer.

Replaces browser-use's broken Agent with our own agentic loop that works
on WhatsApp, Instagram, and complex React sites. Uses:
- PageReader's JS extractors to understand pages (WhatsApp, email, search, etc.)
- DOM Optimizer to find clickable elements (indexed list for LLM)
- LLM (gpt-5-mini) to decide actions (cheap, fast)
- Playwright to execute actions (parallel-capable)

Each step: read page → find elements → LLM decides → execute → repeat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from lazyclaw.browser.dom_optimizer import JS_EXTRACT_ACTIONABLE
from lazyclaw.browser.page_reader import EXTRACTORS, JS_GENERIC, _detect_page_type
from lazyclaw.llm.providers.base import LLMMessage

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter

logger = logging.getLogger(__name__)

# LLM prompt for action decisions
_ACTION_PROMPT = """\
You are a browser automation agent. You see the page content and interactive elements.

TASK: {instruction}

CURRENT PAGE ({url}):
{content}

INTERACTIVE ELEMENTS (click by index):
{elements}

STEP {step}/{max_steps}

Return ONE JSON action (no markdown, no explanation):
- Click: {{"type": "click", "index": 3}}
- Type: {{"type": "type", "index": 5, "text": "hello"}}
- Type + Enter: {{"type": "type", "index": 5, "text": "hello", "submit": true}}
- Scroll: {{"type": "scroll", "direction": "down"}}
- Navigate: {{"type": "goto", "url": "https://..."}}
- Task complete: {{"type": "done", "result": "what was accomplished"}}

If the page shows what the task asked for, return done with the data.
If the page needs login (QR code, login form), return done with status LOGIN_REQUIRED."""

# URL pattern
_URL_RE = re.compile(r"https?://[^\s,\"']+")


class SmartBrowser:
    """LLM-driven browser automation. Runs on Playwright, parallel-capable."""

    def __init__(
        self,
        config: Config,
        eco_router: EcoRouter,
        user_id: str,
    ) -> None:
        self._config = config
        self._eco_router = eco_router
        self._user_id = user_id
        self._page = None
        self._browser = None
        self._context = None

    async def run(self, instruction: str, max_steps: int = 10) -> str:
        """Execute a browser task step-by-step. Returns result text."""
        page = await self._get_page()

        # Extract URL from instruction and navigate
        url = self._extract_url(instruction)
        if url:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                # Extra wait for JS-heavy apps like WhatsApp
                await asyncio.sleep(2)
            except Exception as exc:
                logger.warning("Navigation to %s failed: %s", url, exc)

        last_content = ""
        for step in range(max_steps):
            # 1. Read page content
            content = await self._read_page(page)
            if not content.strip():
                content = "(page is loading or empty)"

            # 2. Find interactive elements
            elements = await self._get_elements(page)

            # 3. Ask LLM what to do
            try:
                action = await self._decide_action(
                    instruction, content, elements,
                    step + 1, max_steps, page.url,
                )
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning("LLM action parse failed (step %d): %s", step + 1, exc)
                continue

            logger.info(
                "SmartBrowser step %d: %s", step + 1,
                json.dumps(action)[:100] if isinstance(action, dict) else str(action)[:100],
            )

            # 4. Done?
            if action.get("type") == "done":
                result = action.get("result", content)
                return result

            # 5. Execute action
            try:
                await self._execute_action(page, action)
            except Exception as exc:
                logger.warning("Action failed (step %d): %s", step + 1, exc)

            # 6. Human-like wait
            await asyncio.sleep(random.uniform(0.5, 1.5))
            last_content = content

        return last_content or "Task reached maximum steps."

    async def _get_page(self):
        """Launch Playwright with persistent user profile."""
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        profile_dir = str(
            Path(self._config.database_dir) / "browser_profiles" / self._user_id
        )
        Path(profile_dir).mkdir(parents=True, exist_ok=True)

        # Use system Chrome with persistent profile (cookies shared with CDP)
        self._context = await pw.chromium.launch_persistent_context(
            profile_dir,
            channel="chrome",
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
            ],
            viewport={"width": 1366, "height": 768},
            ignore_https_errors=True,
        )
        self._browser = pw

        # Use existing page or create new one
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        return self._page

    async def _read_page(self, page) -> str:
        """Extract page content using the right JS extractor."""
        url = page.url
        page_type = _detect_page_type(url)

        js = EXTRACTORS.get(page_type, JS_GENERIC)

        try:
            result = await page.evaluate(js)
            if isinstance(result, dict):
                return result.get("text", json.dumps(result))
            return str(result)
        except Exception:
            try:
                return await page.inner_text("body")
            except Exception:
                return ""

    async def _get_elements(self, page) -> str:
        """Find interactive elements using DOM Optimizer."""
        try:
            elements = await page.evaluate(JS_EXTRACT_ACTIONABLE)
            if not elements:
                return "(no interactive elements found)"

            lines = []
            for el in elements[:40]:
                idx = el.get("idx", 0)
                tag = el.get("tag", "?")
                text = (el.get("text") or "")[:50]
                label = (
                    el.get("ariaLabel")
                    or el.get("placeholder")
                    or el.get("name")
                    or ""
                )
                href = el.get("href") or ""
                disabled = " DISABLED" if el.get("disabled") else ""
                parts = [f"[{idx}] <{tag}>"]
                if text:
                    parts.append(text)
                if label:
                    parts.append(f"({label})")
                if href and href != "null":
                    parts.append(f"→ {href[:60]}")
                if disabled:
                    parts.append(disabled)
                lines.append(" ".join(parts))

            return "\n".join(lines)
        except Exception:
            return "(no interactive elements found)"

    async def _decide_action(
        self, instruction: str, content: str, elements: str,
        step: int, max_steps: int, url: str,
    ) -> dict:
        """Ask LLM what to do next."""
        prompt = _ACTION_PROMPT.format(
            instruction=instruction,
            url=url,
            content=content[:3000],
            elements=elements,
            step=step,
            max_steps=max_steps,
        )

        messages = [
            LLMMessage(
                role="system",
                content="You are a browser automation agent. Return ONE valid JSON action. No markdown.",
            ),
            LLMMessage(role="user", content=prompt),
        ]

        response = await self._eco_router.chat(
            messages,
            user_id=self._user_id,
            model=self._config.fast_model,
        )

        text = (response.content or "").strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = "\n".join(
                l for l in text.split("\n")
                if not l.strip().startswith("```")
            ).strip()

        # Find JSON in response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])

        raise ValueError(f"No JSON found in LLM response: {text[:100]}")

    async def _execute_action(self, page, action: dict) -> None:
        """Execute an action on the Playwright page."""
        action_type = action.get("type", "")

        if action_type == "click":
            idx = action.get("index", 0)
            # Use JS to find and click the nth interactive element
            clicked = await page.evaluate(f"""
                (() => {{
                    const selectors = 'input, button, select, textarea, a[href], ' +
                        '[role="button"], [role="link"], [role="tab"], [role="menuitem"], ' +
                        '[onclick], [contenteditable]';
                    const els = Array.from(document.querySelectorAll(selectors))
                        .filter(el => {{
                            const s = window.getComputedStyle(el);
                            const r = el.getBoundingClientRect();
                            return s.display !== 'none' && s.visibility !== 'hidden'
                                && r.width > 0 && r.height > 0;
                        }});
                    const el = els[{idx}];
                    if (el) {{ el.click(); return true; }}
                    return false;
                }})()
            """)
            if not clicked:
                logger.warning("Click failed: element %d not found", idx)

        elif action_type == "type":
            idx = action.get("index", 0)
            text = action.get("text", "")
            submit = action.get("submit", False)

            # Focus the element, clear it, type text
            await page.evaluate(f"""
                (() => {{
                    const selectors = 'input, button, select, textarea, a[href], ' +
                        '[role="button"], [role="link"], [role="tab"], [role="menuitem"], ' +
                        '[onclick], [contenteditable]';
                    const els = Array.from(document.querySelectorAll(selectors))
                        .filter(el => {{
                            const s = window.getComputedStyle(el);
                            const r = el.getBoundingClientRect();
                            return s.display !== 'none' && s.visibility !== 'hidden'
                                && r.width > 0 && r.height > 0;
                        }});
                    const el = els[{idx}];
                    if (el) el.focus();
                }})()
            """)
            await asyncio.sleep(random.uniform(0.1, 0.3))

            # Use keyboard to type (works with contenteditable divs like WhatsApp)
            await page.keyboard.type(text, delay=random.randint(30, 80))

            if submit:
                await asyncio.sleep(random.uniform(0.2, 0.5))
                await page.keyboard.press("Enter")

        elif action_type == "scroll":
            direction = action.get("direction", "down")
            delta = 400 if direction == "down" else -400
            await page.mouse.wheel(0, delta)

        elif action_type == "goto":
            url = action.get("url", "")
            if url:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)

        # Human-like pause
        await asyncio.sleep(random.uniform(0.2, 0.8))

    def _extract_url(self, instruction: str) -> str | None:
        """Extract first URL from instruction text."""
        match = _URL_RE.search(instruction)
        return match.group(0) if match else None

    async def close(self) -> None:
        """Close browser and cleanup."""
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
