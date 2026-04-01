# Activepieces + LazyClaw Integration

## What is Activepieces?

[Activepieces](https://www.activepieces.com) is an open-source (MIT), self-hostable workflow automation platform with 450+ built-in pieces (integrations). It lets you visually wire together apps, APIs, databases, and custom logic using a flow editor — no code required. You own your data, flows run on your own server, and it ships its own MCP server so every flow is natively callable as a tool.

## Why Connect Activepieces to LazyClaw?

Activepieces exposes all 450+ pieces as MCP tools. Connect to LazyClaw and your agent can trigger CRM updates, send emails, process webhooks, run data pipelines, or post to social media using plain English — with zero extra code.

Activepieces runs alongside LazyClaw in Docker on the same host. No cloud dependency. MIT licensed.

## Prerequisites

- **Docker** installed (`docker --version`)
- **LazyClaw** running with MCP bridge enabled (`lazyclaw start`)

## Step 1: Start Activepieces

From the LazyClaw project root:

```bash
docker compose -f docker-compose.activepieces.yml up -d
```

Activepieces will be available at **http://localhost:8080** in about 30 seconds.

Open the URL, create an admin account, and you're in.

> **Note:** The `docker-compose.activepieces.yml` in this repo has pre-generated `AP_ENCRYPTION_KEY` and `AP_JWT_SECRET`. For a production deployment, regenerate them:
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(16))"
> ```
> Run twice — once for each key — and replace the values in the compose file.

## Step 2: Create a Flow

1. In the Activepieces UI, click **New Flow**.
2. Choose a trigger (e.g., **MCP Server Trigger** for agent-callable flows, or **Webhook** for HTTP-triggered ones).
3. Add pieces after the trigger (Gmail, Slack, HubSpot, Notion, etc.).
4. **Publish** the flow — the MCP endpoint is only live when the flow is published.

## Step 3: Get the MCP URL

In the flow editor, click the **MCP Server Trigger** node. Copy the **MCP server URL**:

```
http://localhost:8080/api/v1/mcp/YOUR-FLOW-ID/sse
```

## Step 4: Add to LazyClaw

**Via Web UI:**
1. Open LazyClaw Web UI → **Settings** → **MCP** → **Add Server**
2. Paste the MCP URL from Step 3
3. Name it (e.g., `ap-email-pipeline`)
4. Click **Connect**

**Via Telegram:**
```
/mcp install http://localhost:8080/api/v1/mcp/YOUR-FLOW-ID/sse
```

LazyClaw registers the flow's tools from the MCP manifest. They appear as first-class skills immediately.

## Step 5: Use It

```
You: send the weekly report to the team Slack channel
Agent: [calls Activepieces flow → posts to #engineering → confirms]
Agent: Done. Posted to #engineering with this week's summary.
```

## Example Use Cases

| Flow | What it does |
|------|-------------|
| **Email triage** | Agent says "process inbox" → Activepieces reads Gmail, categorizes, drafts replies |
| **CRM updates** | Agent captures call notes → Activepieces writes to HubSpot/Salesforce |
| **Slack notifications** | Agent completes a task → Activepieces posts summary to team channel |
| **Data pipeline** | Agent triggers ETL → Activepieces pulls from API, transforms, loads to DB |
| **Social posting** | Agent drafts content → Activepieces posts to Twitter/LinkedIn/Instagram |
| **Calendar + task sync** | Agent creates a task → Activepieces syncs to Google Calendar and Notion |

## Managing Activepieces

```bash
# Stop
docker compose -f docker-compose.activepieces.yml down

# View logs
docker compose -f docker-compose.activepieces.yml logs -f activepieces

# Restart
docker compose -f docker-compose.activepieces.yml restart activepieces

# Update to latest
docker compose -f docker-compose.activepieces.yml pull && docker compose -f docker-compose.activepieces.yml up -d
```

Data is persisted in Docker volumes (`ap_postgres_data`, `ap_redis_data`) and survives container restarts.

## Notes

- Each published flow appears as a separate tool in LazyClaw. Name your flows clearly — the name becomes the tool description the LLM sees.
- If you update a flow's inputs/outputs, remove and re-add the MCP server in LazyClaw to refresh the tool schema.
- Activepieces handles all piece credentials internally. LazyClaw only calls the MCP endpoint and never sees upstream API keys.
- Default port is **8080**. Change it in `docker-compose.activepieces.yml` if that port is taken.

## Further Reading

- [Activepieces MCP documentation](https://www.activepieces.com/docs/mcp)
- [Activepieces pieces library](https://www.activepieces.com/pieces)
- LazyClaw MCP configuration: `Settings → MCP` in the Web UI
