# n8n + LazyClaw Integration

## What is n8n?

[n8n](https://n8n.io) is an open-source, self-hostable workflow automation platform with 400+ built-in integrations. It lets you visually wire together apps, APIs, databases, and custom logic using a node-based editor. You own your data, workflows run on your own server, and complex multi-step automations require zero code.

## Why Connect n8n to LazyClaw?

LazyClaw agents can call tools. n8n workflows can be exposed as MCP tools. Connect them and every n8n workflow becomes a LazyClaw skill — your agent can trigger CRM updates, send Slack messages, process emails, or kick off data pipelines using plain English.

## Prerequisites

- **n8n** running (self-hosted via Docker/npm, or n8n Cloud)
- **LazyClaw** running with MCP bridge enabled (`lazyclaw start`)

## Setup

### Step 1: Create the workflow in n8n

1. Open your n8n instance and create a new workflow (or open an existing one).
2. Add an **MCP Server Trigger** node as the entry point.
3. Wire your workflow logic after the trigger.
4. **Activate** the workflow — the MCP endpoint is only live when the workflow is active.

### Step 2: Copy the MCP server URL

In the MCP Server Trigger node settings, copy the **MCP server URL**. It looks like:

```
https://your-n8n-instance.com/mcp/abc123xyz/sse
```

For n8n Cloud the URL is in the trigger node panel. For self-hosted, it uses your configured public domain.

### Step 3: Add the server to LazyClaw

1. Open LazyClaw Web UI → **Settings** → **MCP** → **Add Server**
2. Paste the URL from Step 2
3. Give it a name (e.g., `n8n-crm-workflow`)
4. Click **Connect**

Alternatively, from Telegram:

```
/mcp install https://your-n8n-instance.com/mcp/abc123xyz/sse
```

### Step 4: Use it

LazyClaw registers the workflow's tools from the MCP manifest. They appear as first-class skills:

```
You: update the CRM contact for alex@example.com with today's call notes
Agent: [calls n8n workflow → updates HubSpot → confirms]
Agent: Done. Contact updated with call summary.
```

## Example Use Cases

| Workflow | What it does |
|----------|-------------|
| **Email triage** | Agent says "process inbox" → n8n reads Gmail, categorizes, drafts replies |
| **CRM updates** | Agent fills in contact details → n8n writes to HubSpot/Salesforce |
| **Slack notifications** | Agent completes a task → n8n posts summary to team channel |
| **Data pipeline** | Agent triggers ETL → n8n pulls from API, transforms, loads to DB |
| **Calendar + task sync** | Agent creates a task → n8n syncs to Google Calendar and Notion |

## Notes

- Each n8n workflow appears as a separate tool in LazyClaw. Name your workflows clearly — the name becomes the tool description the LLM sees.
- If you update a workflow's inputs/outputs, remove and re-add the MCP server in LazyClaw to refresh the tool schema.
- For workflows with sensitive credentials, n8n handles auth internally — LazyClaw only calls the MCP endpoint, never sees n8n credentials.

## Further Reading

- [n8n MCP Server documentation](https://docs.n8n.io/advanced-ai/accessing-n8n-mcp-server/)
- LazyClaw MCP configuration: `Settings → MCP` in the Web UI
