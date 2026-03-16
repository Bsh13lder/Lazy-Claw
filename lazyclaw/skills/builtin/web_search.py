from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class WebSearchSkill(BaseSkill):
    @property
    def category(self) -> str:
        return "research"

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web for information. Returns titles, URLs, and snippets from search results."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        import asyncio
        from ddgs import DDGS

        query = params["query"]
        max_results = params.get("max_results", 5)

        try:
            # DDGS v8+ is sync-only, run in thread to avoid blocking
            def _search():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=max_results))

            results = await asyncio.get_event_loop().run_in_executor(None, _search)

            if not results:
                return f"No results found for: {query}"

            formatted = []
            for i, r in enumerate(results, 1):
                formatted.append(
                    f"{i}. {r['title']}\n   {r['href']}\n   {r['body']}"
                )

            return "\n\n".join(formatted)
        except Exception as e:
            return f"Search failed: {e}"
