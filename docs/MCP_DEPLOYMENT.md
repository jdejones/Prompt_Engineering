# MySQL News MCP Deployment Guide

This guide walks through the MCP server that was added in this repo and how to deploy it to your Hostinger VPS for ChatGPT agents/apps usage.

## What was built

- Read-only MCP server in `mcp_news_server/` with tools:
  - `health`
  - `list_symbols`
  - `get_symbol_news`
  - `search`
  - `fetch`
- Query safety controls:
  - symbol whitelist from `information_schema.tables`
  - parameterized SQL values for filters/search
  - max row limits and max symbol scan limits
- OAuth resource server support:
  - JWT verification via JWKS (`AUTH_JWKS_URI`)
  - issuer/audience/scope checks
  - MCP auth metadata via `AuthSettings` (includes protected resource metadata route)
- Daily DB copy scripts:
  - Windows export + upload: `scripts/export_news_dump.ps1`
  - VPS import + rotation: `scripts/import_news_dump.sh`
- VPS deployment templates:
  - `deploy/mcp-news.service`
  - `deploy/nginx-mcp-news.conf`

## 1) Local project setup

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `mcp_news_server/.env.example` values into your server environment (or `.env` file on VPS).

## 2) Configure OAuth provider (Auth0-style)

Your MCP server is implemented as a resource server and expects OAuth access tokens minted by your authorization server.

Required environment values:

- `AUTH_ISSUER_URL` (for example `https://your-tenant.us.auth0.com/`)
- `AUTH_JWKS_URI` (your issuer JWKS endpoint)
- `AUTH_AUDIENCE` (set to MCP base URL, same as `MCP_BASE_URL`)
- `AUTH_REQUIRED_SCOPES` (default `news.read`)

Important ChatGPT OAuth requirements:

- Allow redirect URI:
  - `https://chatgpt.com/connector_platform_oauth_redirect`
- For app review flows, also allow:
  - `https://platform.openai.com/apps-manage/oauth`
- Ensure PKCE (`S256`) is enabled.
- Ensure dynamic client registration support is enabled if your provider requires explicit config.
- Ensure the `resource` audience semantics map to your MCP base URL.

## 3) Deploy to Hostinger VPS

Example deployment layout:

- App root: `/opt/mcp-news/app`
- Virtualenv: `/opt/mcp-news/venv`
- Environment file: `/opt/mcp-news/.env`
- Incoming dumps: `/opt/mcp-news/incoming`
- Archived dumps: `/opt/mcp-news/archive`

Recommended commands (run on VPS):

```bash
sudo useradd --system --create-home --shell /bin/bash mcp || true
sudo mkdir -p /opt/mcp-news/app /opt/mcp-news/incoming /opt/mcp-news/archive
sudo chown -R mcp:mcp /opt/mcp-news
```

Copy project code to `/opt/mcp-news/app`, then:

```bash
cd /opt/mcp-news/app
python3 -m venv /opt/mcp-news/venv
/opt/mcp-news/venv/bin/pip install -r requirements.txt
```

Create `/opt/mcp-news/.env` with all required vars from `.env.example`.

Install service:

```bash
sudo cp deploy/mcp-news.service /etc/systemd/system/mcp-news.service
sudo systemctl daemon-reload
sudo systemctl enable --now mcp-news
sudo systemctl status mcp-news
```

## 4) Reverse proxy + TLS

1. Copy `deploy/nginx-mcp-news.conf` to your nginx site config and set `server_name`.
2. Enable the site and reload nginx.
3. Issue a certificate (for example with certbot).

The MCP URL you configure in ChatGPT should be the HTTPS public endpoint for your streamable HTTP transport.
If you keep default FastMCP paths, this is typically `https://mcp.your-domain.com/mcp`.

## 5) Daily database transfer

### Local Windows machine (source DB)

Run export script manually first:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\export_news_dump.ps1 `
  -DbPassword "<your-local-db-password>" `
  -RemoteUser "mcp" `
  -RemoteHost "<your-vps-ip-or-domain>" `
  -RemotePath "/opt/mcp-news/incoming"
```

Then schedule daily in Task Scheduler:

- Trigger: Daily (pick your preferred time)
- Action:
  - Program: `powershell.exe`
  - Arguments:
    - `-ExecutionPolicy Bypass -File "C:\Users\jdejo\Prompt_Engineering\scripts\export_news_dump.ps1" -RemoteUser "mcp" -RemoteHost "<vps-host>" -RemotePath "/opt/mcp-news/incoming"`
- Set `DATABASE_PASSWORD` as a machine/user environment variable or pass `-DbPassword`.

### VPS (target import)

Make script executable:

```bash
chmod +x /opt/mcp-news/app/scripts/import_news_dump.sh
```

Run once manually:

```bash
DB_PASSWORD="<vps-mysql-password>" /opt/mcp-news/app/scripts/import_news_dump.sh
```

Add cron entry (daily):

```bash
crontab -e
```

Example cron line:

```cron
20 2 * * * DB_PASSWORD='<vps-mysql-password>' /opt/mcp-news/app/scripts/import_news_dump.sh >> /var/log/mcp-news-import.log 2>&1
```

## 6) Start MCP server locally (optional test)

```bash
python -m mcp_news_server
```

Server env keys that must exist:

- `MYSQL_PASSWORD`
- `MCP_BASE_URL`
- `AUTH_ISSUER_URL`

## 7) Connect in ChatGPT (agents/apps)

1. Open ChatGPT settings for apps/connectors (developer mode).
2. Add your remote MCP server URL.
3. Complete OAuth linking flow.
4. In a chat using reasoning/agents mode, test tool usage:
   - "List available stock symbol tables."
   - "For AAPL, fetch today's news and summarize likely price impact."
   - "Search for news about guidance cuts from today and fetch top 5 items."

## 8) Validation checklist

- `mcp-news` systemd service is active after reboot.
- HTTPS endpoint is reachable externally.
- OAuth link succeeds from ChatGPT.
- `list_symbols` returns expected stock tables.
- `get_symbol_news(symbol='AAPL', date_from='YYYY-MM-DD')` returns rows.
- `search` + `fetch` returns citations/data consistent with MySQL rows.
- Daily export/import logs show successful completion.

Optional API smoke test from your workstation:

```bash
export OPENAI_API_KEY=...
export MCP_SERVER_URL=https://mcp.your-domain.com/mcp
python scripts/test_mcp_with_openai.py
```

## 9) Troubleshooting

- 401 from ChatGPT:
  - verify `AUTH_ISSUER_URL`, `AUTH_AUDIENCE`, and scope mapping
  - verify JWKS URL is reachable from VPS
- Empty `search` results:
  - confirm text columns exist in symbol tables
  - confirm `date_from` format is `YYYY-MM-DD`
- `fetch` failures:
  - ensure table has a primary key column
  - pass id as `<SYMBOL>:<PRIMARY_KEY_VALUE>`
- Connection errors to MySQL:
  - verify `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`
  - check firewall and local bind address
