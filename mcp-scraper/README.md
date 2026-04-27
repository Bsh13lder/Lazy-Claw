# mcp-scraper

LazyClaw's self-hosted scraper MCP server. Vendors `crawl4ai` 0.7.8 + a forked FastMCP wrapper from `walksoda/crawl-mcp`, with all imports rewritten to `mcp_scraper._vendor.crawl4ai` so the upstream pip package is never installed.

## Why it exists

The default `web_search` tool returns snippets only and burns rate limits quickly. The `browser` skill is heavy and gets blocked by login walls. `mcp-scraper` is the middle layer: HTTP-fast for static pages, JS-rendered for SPAs, with built-in entity extraction (emails, phones, social handles).

## Tools (19)

Categories surfaced via FastMCP:

- **Web crawling**: `crawl_url`, `deep_crawl_site`, `crawl_url_with_fallback`
- **Data extraction**: `extract_structured_data`, `extract_with_llm`, `extract_entities`
- **Batch**: `batch_crawl`
- **YouTube**: transcripts, metadata, comments
- **Google Search**: 7 genre operators
- **File processing**: PDF / Office / archive → markdown

The full list materializes after the server starts — see `mcp_scraper/server_tools/__init__.py` (`register_all_tools`).

## How LazyClaw connects

`mcp-scraper` is registered in `lazyclaw/mcp/manager.py` `BUNDLED_MCPS` with `"module": "mcp_scraper"`. The MCP manager spawns it via:

```bash
python -m mcp_scraper
```

Stdio transport. No new Docker service — runs as a subprocess inside the main lazyclaw container.

## Editing

This is YOUR fork. Add a custom composite tool in `mcp_scraper/tools/` and register it in `mcp_scraper/server_tools/__init__.py`:

```python
@mcp.tool()
async def find_business_email(name: str, city: str) -> dict:
    # 1. google → first result URL
    # 2. crawl4ai extract_entities on that URL
    # 3. return {"email": entities.emails[0]} or {"email": None, "tried": [...]}
```

## Licenses

- Wrapper code (this package): MIT — see `LICENSE`
- Vendored `_vendor/crawl4ai/`: Apache-2.0 — see `LICENSE-CRAWL4AI`
- Both notices retained in `NOTICE`

## Upgrade path

To bump vendored crawl4ai:

```bash
git clone --depth=1 --branch v<NEW_TAG> https://github.com/unclecode/crawl4ai /tmp/crawl4ai
rm -rf mcp_scraper/_vendor/crawl4ai
cp -R /tmp/crawl4ai/crawl4ai mcp_scraper/_vendor/crawl4ai
# Rerun the import-rewrite sed pass:
find mcp_scraper/_vendor/crawl4ai -name "*.py" -print0 | xargs -0 sed -i '' \
  -e 's/from crawl4ai\./from mcp_scraper._vendor.crawl4ai./g' \
  -e 's/from crawl4ai import/from mcp_scraper._vendor.crawl4ai import/g'
# Update pyproject.toml deps if upstream changed them
docker compose build lazyclaw && docker compose up -d lazyclaw
```
