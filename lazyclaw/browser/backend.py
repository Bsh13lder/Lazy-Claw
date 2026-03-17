"""Browser backend abstraction — enables Playwright and CDP to coexist.

BrowserBackend ABC defines the interface for page interaction.
Playwright stays for automated tasks. CDP added for real browser control.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TabInfo:
    """Immutable info about a browser tab."""

    id: str
    title: str
    url: str
    active: bool = False


class BrowserBackend(ABC):
    """Abstract browser backend. Implemented by Playwright and CDP."""

    @abstractmethod
    async def goto(self, url: str) -> None:
        """Navigate to a URL."""

    @abstractmethod
    async def current_url(self) -> str:
        """Get the current page URL."""

    @abstractmethod
    async def title(self) -> str:
        """Get the current page title."""

    @abstractmethod
    async def content(self) -> str:
        """Get the full page HTML."""

    @abstractmethod
    async def evaluate(self, js: str) -> Any:
        """Execute JavaScript and return the result."""

    @abstractmethod
    async def screenshot(self, full_page: bool = False) -> bytes:
        """Take a screenshot. Returns PNG bytes."""

    @abstractmethod
    async def click(self, selector: str) -> None:
        """Click an element matching the CSS selector."""

    @abstractmethod
    async def type_text(self, selector: str, text: str) -> None:
        """Type text into an element matching the CSS selector."""

    @abstractmethod
    async def scroll(self, direction: str = "down", amount: int = 300) -> None:
        """Scroll the page. direction: 'up' or 'down'."""

    @abstractmethod
    async def wait_for_selector(
        self, selector: str, timeout_ms: int = 5000,
    ) -> bool:
        """Wait for a selector to appear. Returns True if found."""

    @abstractmethod
    async def tabs(self) -> list[TabInfo]:
        """List all open browser tabs."""

    @abstractmethod
    async def switch_tab(self, tab_id: str) -> None:
        """Switch to a specific tab by ID."""

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if the backend is alive and connected."""

    @abstractmethod
    async def close(self) -> None:
        """Close the backend connection (not the browser itself for CDP)."""

    @property
    @abstractmethod
    def backend_type(self) -> str:
        """Return 'playwright' or 'cdp'."""
