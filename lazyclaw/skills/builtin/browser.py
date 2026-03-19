"""Browser skills — browse_web, read_page, and save_site_login for the main agent."""

from __future__ import annotations

import json
import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class BrowseWebSkill(BaseSkill):
    """Start a browser automation task from chat."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "browse_web"

    @property
    def description(self) -> str:
        return (
            "Automate a multi-step browser task (navigate, click, type, read). "
            "Use for NEW sessions: login flows, form filling, posting content. "
            "IMPORTANT: For sites already open in Chrome (WhatsApp, etc.), "
            "use read_tab first — it's instant. Only use browse_web when "
            "read_tab/see_browser can't do the job (e.g. need to click, type, navigate)."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "What to do in the browser (e.g., 'Go to example.com and find the pricing page')",
                },
            },
            "required": ["instruction"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser.smart_browser import SmartBrowser
        from lazyclaw.llm.eco_router import EcoRouter
        from lazyclaw.llm.router import LLMRouter

        if not self._config:
            return "Error: config not available"

        instruction = params.get("instruction", "")
        if not instruction:
            return "Error: instruction is required"

        # Get or create eco_router for SmartBrowser's LLM calls
        router = LLMRouter(self._config)
        eco_router = EcoRouter(self._config, router)

        browser = SmartBrowser(self._config, eco_router, user_id)
        try:
            result = await browser.run(
                instruction=instruction,
                max_steps=10,
            )
            return result or "Task completed."
        except Exception as exc:
            logger.error("browse_web failed: %s", exc)
            return f"Browser task failed: {exc}"
        finally:
            await browser.close()


class ReadPageSkill(BaseSkill):
    """Lightweight page reading — extract content without full browser agent."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "read_page"

    @property
    def description(self) -> str:
        return (
            "Read and extract content from a web page. Much faster and cheaper "
            "than browse_web. Use for reading articles, checking pages, "
            "extracting text. Does NOT interact with the page."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to read",
                },
                "question": {
                    "type": "string",
                    "description": "Optional question to answer about the page content",
                },
            },
            "required": ["url"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser.page_reader import PageReader

        url = params.get("url", "")
        if not url:
            return "Error: url is required"

        question = params.get("question")
        llm_router = None
        if self._config:
            from lazyclaw.llm.router import LLMRouter

            llm_router = LLMRouter(self._config)

        reader = PageReader(config=self._config, llm_router=llm_router)

        try:
            if question:
                return await reader.read_and_analyze(url, question, user_id)

            data = await reader.read_page(url, user_id)
            title = data.get("title", "")
            text = data.get("text", "")
            page_type = data.get("type", "")

            header = f"**{title}** ({page_type})\n\n" if title else ""
            return header + text
        except Exception as exc:
            return f"Error reading page: {exc}"
        finally:
            await reader.close(user_id)


class SaveSiteLoginSkill(BaseSkill):
    """Save website login credentials to the encrypted vault for auto-login."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "save_site_login"

    @property
    def description(self) -> str:
        return (
            "Save login credentials for a website. Stored encrypted in the vault. "
            "Used for automatic login when cookies expire — the browser will "
            "re-login automatically using these credentials. "
            "Example: save_site_login(domain='bank.com', username='me', password='secret')"
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Website domain (e.g., 'bank.com', 'gmail.com')",
                },
                "username": {
                    "type": "string",
                    "description": "Login username or email",
                },
                "password": {
                    "type": "string",
                    "description": "Login password",
                },
            },
            "required": ["domain", "username", "password"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.crypto.vault import set_credential

        if not self._config:
            return "Error: config not available"

        domain = params.get("domain", "").strip().lower()
        username = params.get("username", "")
        password = params.get("password", "")

        if not domain or not username or not password:
            return "Error: domain, username, and password are all required"

        # Remove protocol if user included it
        domain = domain.replace("https://", "").replace("http://", "").rstrip("/")

        creds = json.dumps({"username": username, "password": password})
        await set_credential(self._config, user_id, f"site:{domain}", creds)

        return f"Login credentials saved for {domain}. Auto-login will be used when cookies expire."
