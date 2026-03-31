# mcp-jobspy

**Job Search MCP Server** -- Search jobs across Indeed, LinkedIn, Glassdoor, ZipRecruiter, and Google simultaneously. Powered by python-jobspy.

## What It Does

Wraps the python-jobspy library as an MCP tool, letting your AI agent search for jobs across 5 major platforms in a single call:

- **Multi-platform search** -- Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google
- **Smart filtering** -- location, remote-only, posting age, results limit
- **Structured output** -- title, company, salary, URL, description for each result
- **Salary parsing** -- Extracts and normalizes salary ranges when available

## Architecture

```
AI Agent <--stdio--> mcp-jobspy <--HTTP scrape--> Job Platforms
                         |
                   python-jobspy
                   (sync, run in executor)
```

- **Runtime**: Python 3.11+
- **Transport**: stdio (MCP standard)
- **Backend**: python-jobspy (scrapes job platform HTML)
- **Execution**: Sync scraping run in `asyncio.run_in_executor()` to avoid blocking

## Setup

### Prerequisites
- Python 3.11+

### Install
```bash
cd production/mcps/mcp-jobspy
pip install -e .
```

### Register with LazyClaw
```json
{
  "name": "mcp-jobspy",
  "command": "python",
  "args": ["-m", "mcp_jobspy"],
  "transport": "stdio"
}
```

## Available Tools (1)

| Tool | Description |
|------|-------------|
| `search_jobs` | Search jobs with keywords, location, platform, remote filter, age filter |

### Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `search_term` | string | *required* | Keywords (e.g., "python developer") |
| `location` | string | "Remote" | Location filter |
| `site_name` | string[] | ["indeed", "glassdoor"] | Platforms to search |
| `results_wanted` | int | 10 | Max results per platform (capped at 50) |
| `hours_old` | int | 72 | Only jobs posted within N hours |
| `is_remote` | bool | false | Remote-only filter |

## Environment Variables

None required. No API keys needed -- python-jobspy scrapes public job listings.

---

## Analysis

### Viral Potential: MEDIUM

**Reasoning**: Job search is a universal need but not daily. It's most relevant during job hunting seasons. The multi-platform aggregation is genuinely useful -- searching 5 sites at once is a real time-saver. However, the wow-factor is moderate because job search tools already exist (LinkedIn, Indeed). Where this shines is in the AI context: "Tell my AI to find me Python jobs in Madrid paying over $100k" is a compelling demo.

**Key viral moments**:
- AI searches 5 platforms simultaneously and compares results
- AI tracks new job postings daily and alerts you
- Combined with mcp-email: AI finds jobs AND sends applications
- AI analyzes salary ranges across platforms for negotiation leverage

### Known Bugs & Issues

1. **Scraping is fragile** -- python-jobspy scrapes HTML from job sites. Any site redesign breaks it. LinkedIn and Indeed change their HTML frequently.
2. **No pagination** -- Results are capped at 50 per platform per query. No way to get more.
3. **LinkedIn often blocks** -- LinkedIn aggressively blocks scraping. The `linkedin` site_name option frequently returns 0 results or errors.
4. **No caching** -- Every search hits the live sites. Repeated searches for the same query waste bandwidth and increase ban risk.
5. **Description truncated to 500 chars** -- Job descriptions are cut at 500 characters. For detailed filtering, this may not be enough.
6. **Salary parsing fragile** -- The salary extraction tries multiple column names (`min_amount`/`salary_min`, `max_amount`/`salary_max`). If python-jobspy changes its DataFrame schema, salary data disappears silently.
7. **No proxy support** -- Heavy usage from one IP will get blocked by job sites. No built-in proxy rotation.

### Public vs Private Recommendation: PUBLIC

**Recommendation**: Open-source. Low risk, good utility, no proprietary concerns.

**Why public**:
- python-jobspy is already open-source (MIT)
- No authentication or credentials needed
- Simple single-tool design -- easy for people to understand and fork
- Good showcase of "wrap an existing library as MCP" pattern
- Low abuse potential (reading public job listings)

**Before release**:
- Add a note about LinkedIn scraping unreliability
- Add optional proxy support via env var
- Consider adding result caching (10-minute TTL)
