# Grok Read-Only MCP Setup

This setup runs a dedicated, read-only MCP server instance for Grok. It reuses
the existing `mcp_news_server` implementation but has its own process, port,
log, and MySQL identity.

## Security Boundary

The intended request path is:

```text
Grok hosted connector
  -> HTTPS Tailscale Funnel
  -> static Bearer-token authentication
  -> 127.0.0.1:8002/mcp
  -> local MySQL as grokdb
```

Grok does not need local execution, shell access, Tailnet access, or direct
MySQL access. Keep MySQL bound locally and do not expose TCP port 3306.

The launcher always sets `MCP_ENABLE_WRITE_TOOLS=0`. Consequently, the Grok
instance does not register any of the MCP write tools. The `grokdb` MySQL user
must also have only `SELECT` grants so the database independently enforces the
read-only policy.

The read tools include generic schema discovery and `query_table`. MySQL grants
therefore determine which schemas and tables Grok can discover and read. Grant
`grokdb` access only to the specific datasets Grok needs.

## Local Process

The dedicated launcher is:

```text
C:\Users\jdejo\Prompt_Engineering\scripts\start_grok_mcp_news_server.bat
```

Runtime defaults:

- Local MCP URL: `http://127.0.0.1:8002/mcp`
- Transport: `streamable-http`
- MySQL host: `127.0.0.1`
- MySQL user: `grokdb`
- MySQL password source: Windows environment variable `grokdb`
- Write tools: disabled
- Authentication: required static Bearer token
- Public MCP URL: `https://home-pc.tail701e72.ts.net/mcp`
- Log: `logs\grok_mcp_news_server.log`

Create the `grokdb` MySQL account with only the required `SELECT` grants, then
set its password in the Windows user or machine environment variable named
`grokdb`. Do not put the password in this repository.

## Create The Bearer Token

Stop the Grok MCP process if it is currently running. Open PowerShell under the
same Windows account that runs the MCP launcher and run:

```powershell
$bytes = New-Object byte[] 48
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
[Environment]::SetEnvironmentVariable('GROK_MCP_BEARER_TOKEN', $token, 'User')
$token | Set-Clipboard
Write-Host 'Bearer token saved as a user environment variable and copied to the clipboard.'
```

This creates a 64-character random token, stores it outside the repository, and
copies it so it can be entered into Grok. Do not paste the token into source
files, logs, screenshots, or chat messages.

Run the launcher manually again:

```bat
scripts\start_grok_mcp_news_server.bat
```

The startup window should display:

```text
MCP authentication: static Bearer token
```

The launcher fails closed if the token is absent. It also forces
`MCP_ENABLE_WRITE_TOOLS=0`; environment variables cannot turn on Grok write
tools.

## Enable Tailscale Funnel

Tailscale Funnel is available on all Tailscale plans. It gives this machine a
public HTTPS URL under the tailnet's existing `*.ts.net` domain and forwards
only the selected local service. It does not grant Grok shell, file, desktop,
Tailnet, or MySQL access.

On the PC running the Grok MCP:

1. Update Tailscale if it is older than version 1.38.3.
2. Confirm that Tailscale is connected and that this device is named
   `home-pc`. The public URL is derived from this device name.
3. Open **PowerShell as Administrator**.
4. Run:

```powershell
tailscale version
tailscale status
tailscale funnel --bg 8002
```

The first Funnel command may open a Tailscale approval page. Approve Funnel for
the tailnet. Tailscale enables the required HTTPS certificates, MagicDNS, and
Funnel node attribute during this flow.

After approval, the command should report:

```text
Available on the internet:
https://home-pc.tail701e72.ts.net

|-- / proxy http://127.0.0.1:8002
```

Check the persistent configuration:

```powershell
tailscale funnel status
```

The `--bg` setting survives Tailscale and Windows restarts. Funnel exposes only
the MCP web server on port 8002. Do not add Funnel routes for MySQL port 3306,
files, directories, a terminal, or any other local port.

### Tailnet Policy

The approval flow normally adds this policy:

```json
"nodeAttrs": [
  {
    "target": ["autogroup:member"],
    "attr": ["funnel"]
  }
]
```

This allows tailnet members to enable Funnel on devices they own; it does not
automatically publish every device. For a personal single-member tailnet, the
default is reasonable. To narrow who can enable Funnel, open the Tailscale
admin console, select **Access controls**, and replace
`"autogroup:member"` with the exact email address used by the owner of
`home-pc`. Do not remove or overwrite unrelated policy entries.

## Grok Connector

Configure a remote Streamable HTTP MCP connector in Grok with:

```text
URL: https://home-pc.tail701e72.ts.net/mcp
Header name: Authorization
Header value: Bearer <the GROK_MCP_BEARER_TOKEN value>
```

If Grok provides a dedicated Bearer-token field, enter only the token because
the client may add the `Bearer ` prefix itself. If it provides raw custom
headers, enter the complete `Bearer <token>` value.

Do not register this PC for Grok local execution. Do not provide Grok with the
`grokdb` password; only the local MCP process needs it.

After connecting, confirm that Grok can call read tools such as `health`,
`list_symbols`, and `get_symbol_news`, and that no update, insert, or
table-creation tools appear in its MCP tool catalog.

Use this test prompt:

```text
Call health on the Grok MCP, list its available tools, and call list_symbols
with a limit of 5. Confirm that no create, insert, update, delete, or other
write tools are available. Do not attempt any write operation.
```

To remove only the Grok Funnel route:

```powershell
tailscale funnel --bg 8002 off
```

Do not use `tailscale funnel reset` or `tailscale serve reset` on this PC.
Reset clears the node's complete Serve/Funnel configuration and would also
remove the existing tailnet-only OpenClaw route on port 8001.

Stopping the Grok MCP process also makes the endpoint unable to serve database
requests, even if the Funnel configuration remains enabled.
