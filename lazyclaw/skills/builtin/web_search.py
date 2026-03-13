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
        from duckduckgo_search import AsyncDDGS

        query = params["query"]
        max_results = params.get("max_results", 5)

        try:
            async with AsyncDDGS() as ddgs:
                results = await ddgs.atext(query, max_results=max_results)

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
