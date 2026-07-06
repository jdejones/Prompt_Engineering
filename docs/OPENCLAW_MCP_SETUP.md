# OpenClaw MCP Setup

This setup runs a second MCP server instance for OpenClaw. It uses the same `mcp_news_server` code as the main server, but it has its own process settings, port, log file, and MySQL user.

## Runtime Shape

- MCP URL: `http://100.78.220.116:8001/mcp`
- Transport: `streamable-http`
- MySQL host: `127.0.0.1`
- MySQL port: `3306`
- MySQL user: `openclawdb`
- MySQL password source: local environment variable `openclawdb`
- Write tools: enabled with `MCP_ENABLE_WRITE_TOOLS=1`
- Write scope: `business_analytics.*` only for the OpenClaw analytics write tools

OpenClaw should reach the MCP server over Tailscale. MySQL should remain local-only and should not be exposed to the tailnet.

## Business Analytics Write Tools

The OpenClaw MCP process exposes scoped tools for `business_analytics`:

- `create_business_analytics_table`
- `insert_business_analytics_rows`
- `update_business_analytics_rows`

These tools do not accept a schema parameter. They always target `business_analytics`, validate table/column identifiers, and use parameterized values for inserts and updates.

The MySQL user `openclawdb` must have `CREATE`, `INSERT`, `UPDATE`, and `SELECT` on `business_analytics.*`.

## Start The Server

Run:

```bat
scripts\start_openclaw_mcp_news_server.bat
```

The launcher writes logs to:

```text
logs\openclaw_mcp_news_server.log
```

For Task Scheduler, use the launcher as the action target:

```text
C:\Users\jdejo\Prompt_Engineering\scripts\start_openclaw_mcp_news_server.bat
```

## OpenClaw Registration

From the OpenClaw machine, register this MCP server:

```bash
openclaw mcp add market-db \
  --url http://home-pc.tail701e72.ts.net:8001/mcp \
  --transport streamable-http \
  --timeout 60
```

Then probe and reload:

```bash
openclaw mcp probe market-db
openclaw mcp reload
```

## Firewall

Allow inbound TCP `8001` only on the Tailscale network path, ideally only from the OpenClaw machine's Tailscale IP.

Do not open MySQL port `3306` to OpenClaw. The MCP server connects to MySQL locally using `127.0.0.1:3306`.
